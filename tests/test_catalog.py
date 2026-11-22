import copy, json, tempfile, unittest, threading, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch
import server
from catalog import Store, empty, validate, merge, Conflict
from server import make_server, excel, read_excel

def fixture():
    d=empty();d['models']=[dict(id='m1',brand='测试',series='车型',generation='一代',subgeneration='',year='2026',energy='纯电',tier='',status='规划')];d['versions']=[dict(id='v1',model_id='m1',name='标准版',power='',battery='',status='规划')];d['options']=[dict(id='o1',model_id='m1',category='外观',name='白色',status='规划')];return d
class CatalogTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)
    def tearDown(self):self.store.db.close();self.tmp.cleanup()
    def test_publish_restart_history_conflict(self):
        d=fixture();self.store.save(d,0,'测试用户');self.assertEqual(self.store.get()['published']['revision'],0)
        with self.assertRaises(Conflict):self.store.save(d,0,'另一用户')
        self.store.save(d,1,'测试用户',True);self.store.db.close();self.store=Store(self.tmp.name)
        self.assertEqual(self.store.get()['published']['data'],d);self.assertEqual(len(self.store.get()['history']),2)
        self.assertEqual(json.loads((Path(self.tmp.name)/'current.json').read_text('utf8'))['revision'],1)
        self.assertEqual(self.store.history(2)['data'],d)
    def test_no_delete(self):
        self.store.save(fixture(),0,'user')
        with self.assertRaises(ValueError):self.store.save(empty(),1,'user')
    def test_default_directory_checks_each_legacy_database_before_startup(self):
        project=Path(self.tmp.name)/'Project'
        legacy=project/'共享数据'/'车型配置'
        root=project/'workbench'
        target=root/'database'/'vehicle-config'
        target.mkdir(parents=True)
        for relative in ('catalog.sqlite3','rules/rules.sqlite3'):
            with self.subTest(relative=relative):
                old=legacy/relative
                old.parent.mkdir(parents=True,exist_ok=True)
                old.write_bytes(b'legacy')
                with patch.object(server,'ROOT',project/'app'), patch.dict('os.environ',PLANHUB_DATA_ROOT=str(root)), patch('sys.argv',['server']), patch.object(server,'Store',side_effect=AssertionError('created a database')):
                    with self.assertRaises(SystemExit) as error:server.main()
                self.assertEqual(error.exception.code,2)
                self.assertFalse((target/'service.lock').exists())
                self.assertEqual(old.read_bytes(),b'legacy')
                old.unlink()

    def test_history_pagination_and_projection_repair(self):
        draft=fixture()
        with self.store.db:
            self.store.db.executemany('INSERT INTO events(kind,actor,time,data,changes) VALUES(?,?,?,?,?)',
                                      [('草稿','actor-'+str(i),'',json.dumps(draft),'[]') for i in range(45)])
        self.store.project()
        self.assertEqual(len(self.store.get()['history']),40)
        self.assertEqual([r['id'] for r in self.store.history_page(2)['history']],[5,4,3,2,1])
        self.assertEqual(self.store.history_page(1,'actor-0')['history'][0]['id'],1)
        missing=Path(self.tmp.name)/'history'/'1.json'
        missing.unlink()
        self.store.db.close()
        self.store=Store(self.tmp.name)
        self.assertEqual(json.loads(missing.read_text('utf8'))['data'],draft)
        self.store.save(draft,0,'tester',True)
        with self.assertRaisesRegex(ValueError,'没有需要发布'):self.store.save(draft,1,'tester',True)
        self.assertEqual(self.store.history_page()['total'],46)

    def test_matrix_and_names(self):
        d=fixture();d['matrix']=[dict(id='x',version_id='v1',option_id='o1',state='未知')];self.assertTrue(validate(d,True));d['matrix'][0]['state']='未配置';self.assertEqual(validate(d,True),[])
        d['aliases']=[dict(id='a',source='订单',name='别名',target_type='models',target_id='missing')];self.assertTrue(validate(d,True))
    def test_groups(self):
        d=fixture();d['groups']=[dict(id='g1',name='组一',scope='订单',members='m1',start_date='2026-01-01',end_date='2026-06-30'),dict(id='g2',name='组二',scope='订单',members='m1',start_date='2026-07-01',end_date='')];self.assertEqual(validate(d,True),[])
        d['groups'][1]['start_date']='2026-06-30';self.assertTrue(validate(d,True));d['groups'][0]['members']='g2';self.assertTrue(validate(d,True))
    def test_excel_merge(self):
        d=fixture();result=merge(d,read_excel(excel(d)));self.assertEqual(result,d)
        result=merge(d,{'models':[{'id':'m1','brand':'','year':'2027'}]});self.assertEqual(result['models'][0]['brand'],'测试');self.assertEqual(result['models'][0]['year'],'2027');self.assertEqual(result['versions'],d['versions'])
        with self.assertRaises(ValueError):merge(d,{'models':[{'id':'m1'},{'id':'m1'}]})
    def test_http_security_and_prefix(self):
        server=make_server(self.store,port=0,origins=['http://localhost:8080']);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            state=json.load(urllib.request.urlopen(base+'/vehicle-config/api/state'));self.assertIn('csrf',state)
            for origin,token in [('http://evil.test',state['csrf']),('http://localhost:8080','wrong')]:
                req=urllib.request.Request(base+'/api/save',data=json.dumps({'data':fixture(),'revision':0}).encode(),headers={'Origin':origin,'X-CSRF-Token':token})
                with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code,403)
            req=urllib.request.Request(base+'/api/save',data=json.dumps({'data':fixture(),'revision':0}).encode(),headers={'Origin':'http://localhost:8080','X-CSRF-Token':state['csrf']});self.assertEqual(json.load(urllib.request.urlopen(req))['draft']['revision'],1)
            self.assertEqual(urllib.request.urlopen(base+'/vehicle-config/app.js').status,200)
        finally:server.shutdown();server.server_close();thread.join()
    def test_proxy_identity(self):
        server=make_server(self.store,port=0,proxy_secret='test-secret');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            self.assertEqual(urllib.request.urlopen(base+'/healthz').status,200)
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/state')
            req=urllib.request.Request(base+'/api/state',headers={'X-Config-Proxy-Key':'test-secret','X-Config-User':'DOMAIN/user'});self.assertEqual(urllib.request.urlopen(req).status,200)
        finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
