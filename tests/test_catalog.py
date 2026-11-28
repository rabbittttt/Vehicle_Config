import copy, json, tempfile, unittest, threading, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import quote
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
        self.assertEqual(json.loads((Path(self.tmp.name)/'catalog'/'current.json').read_text('utf8'))['revision'],1)
        self.assertEqual(self.store.history(2)['data'],d)

    def test_flat_catalog_upgrade_preserves_draft_publication_and_history(self):
        root = Path(self.tmp.name)
        directory = root / 'catalog'
        published = fixture()
        self.store.save(published, 0, '发布人', True)
        draft = copy.deepcopy(published)
        draft['versions'][0]['name'] = '改名后的草稿'
        self.store.save(draft, 1, '编辑人')
        expected = self.store.get()
        self.store.db.close()
        # Recreate the legacy layout from a populated catalog.
        for asset in directory.iterdir():
            asset.rename(root / asset.name)
        directory.rmdir()
        unrelated = root / 'rules'
        unrelated.mkdir()
        sentinel = unrelated / 'preserved'
        sentinel.write_bytes(b'unchanged')

        self.store = Store(root)
        self.assertEqual(self.store.get(), expected)
        for path, state in [('current.json', 'published'), ('drafts/current.json', 'draft')]:
            self.assertEqual(json.loads((directory / path).read_text('utf8')), expected[state])
        for event_id, data in [(1, published), (2, draft)]:
            event = json.loads((directory / 'history' / f'{event_id}.json').read_text('utf8'))
            self.assertEqual(event['data'], data)
        self.assertEqual({path.name for path in root.iterdir()}, {'catalog', 'rules'})
        self.assertEqual(sentinel.read_bytes(), b'unchanged')

    def test_interrupted_layout_upgrade_resumes_on_next_start(self):
        root = Path(self.tmp.name)
        directory = root / 'catalog'
        self.store.save(fixture(), 0, '测试', True)
        expected = self.store.get()
        self.store.db.close()
        (directory / 'drafts').rename(root / 'drafts')
        marker = directory / '.migrating-from-flat'
        marker.touch()
        self.store = Store(root)
        self.assertEqual(self.store.get(), expected)
        self.assertFalse(marker.exists())
        self.assertFalse((root / 'drafts').exists())
        self.assertEqual(json.loads((directory / 'drafts/current.json').read_text('utf8')), expected['draft'])

    def test_conflicting_catalog_locations_preserve_both(self):
        root = Path(self.tmp.name)
        nested = root / 'catalog' / 'catalog.sqlite3'
        self.store.db.close()
        original = nested.read_bytes()
        legacy = root / 'catalog.sqlite3'
        legacy.write_bytes(b'other database')
        with self.assertRaises(ValueError):
            Store(root)
        self.assertEqual(nested.read_bytes(), original)
        self.assertEqual(legacy.read_bytes(), b'other database')
    def test_no_delete(self):
        self.store.save(fixture(),0,'user')
        with self.assertRaises(ValueError):self.store.save(empty(),1,'user')
    def test_publish_preview_matches_commit_checks(self):
        data=fixture()
        self.store.save(data,0,'tester')
        with self.assertRaises(Conflict):self.store.preview(data,0)
        self.assertTrue(self.store.preview(data,1)['changes'])
        for invalid in [empty(),{},dict(data,models=[{'series':'missing id'}])]:
            preview=self.store.preview(invalid,1)
            self.assertTrue(preview['errors'])
            self.assertFalse(preview['changes'])
            with self.assertRaises(ValueError) as exc:self.store.save(invalid,1,'tester',True)
            self.assertEqual(str(exc.exception),'\n'.join(preview['errors']))
        self.assertEqual(self.store.get()['published']['revision'],0)
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

    def test_default_directory_accepts_previously_centralized_catalog(self):
        project = Path(self.tmp.name) / 'Project'
        legacy = project / '共享数据' / '车型配置' / 'catalog.sqlite3'
        legacy.parent.mkdir(parents=True)
        self.store.save(fixture(), 0, '测试', True)
        self.store.db.close()
        legacy.write_bytes((Path(self.tmp.name) / 'catalog/catalog.sqlite3').read_bytes())
        for layout in ('flat', 'nested'):
            with self.subTest(layout=layout):
                data_root = project / layout
                target = data_root / 'database/vehicle-config'
                database = target / ('catalog.sqlite3' if layout == 'flat' else 'catalog/catalog.sqlite3')
                database.parent.mkdir(parents=True)
                database.write_bytes(legacy.read_bytes())
                with patch.object(server, 'ROOT', project / 'app'), patch.dict('os.environ', PLANHUB_DATA_ROOT=str(data_root)), patch('sys.argv', ['server']), patch.object(server, 'RuleStore'), patch.object(server, 'make_server') as start:
                    server.main()
                start.return_value.serve_forever.assert_called_once()
                self.assertEqual(json.loads((target / 'catalog/current.json').read_text('utf8'))['data'], fixture())

    def test_history_pagination_and_projection_repair(self):
        draft=fixture()
        with self.store.db:
            self.store.db.executemany('INSERT INTO events(kind,actor,time,data,changes) VALUES(?,?,?,?,?)',
                                      [('草稿','actor-'+str(i),'',json.dumps(draft),'[]') for i in range(45)])
        self.store.project()
        self.assertEqual(len(self.store.get()['history']),40)
        self.assertEqual([r['id'] for r in self.store.history_page(2)['history']],[5,4,3,2,1])
        self.assertEqual(self.store.history_page(1,'actor-0')['history'][0]['id'],1)
        missing=Path(self.tmp.name)/'catalog'/'history'/'1.json'
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
            with urllib.request.urlopen(base+'/vehicle-config/api/runtime') as response:
                runtime = json.load(response)
            root = Path(self.tmp.name).resolve()
            for key, relative in [('catalog','catalog/catalog.sqlite3'), ('rules_db','rules/rules.sqlite3'), ('campaign_db','campaigns/campaigns.sqlite3')]:
                self.assertEqual(Path(runtime[key]), root / relative)
                self.assertTrue((root / relative).is_file())
            for origin,token in [('http://evil.test',state['csrf']),('http://localhost:8080','wrong')]:
                req=urllib.request.Request(base+'/api/save',data=json.dumps({'data':fixture(),'revision':0}).encode(),headers={'Origin':origin,'X-CSRF-Token':token})
                with self.assertRaises(urllib.error.HTTPError) as ctx:urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code,403)
            def save_local(owner):
                return urllib.request.urlopen(urllib.request.Request(base+'/api/save',
                    data=json.dumps({'data':fixture(),'revision':0,'owner':owner}).encode(),
                    headers={'Origin':'http://localhost:8080','X-CSRF-Token':state['csrf']}))
            for owner in (None, '', 'a'*101, '操\n作'):
                with self.assertRaises(urllib.error.HTTPError) as failed:
                    save_local(owner)
                self.assertEqual(failed.exception.code, 400)
                failed.exception.close()
            with save_local('本地编辑') as response:
                self.assertEqual(json.load(response)['draft']['revision'],1)
            self.assertEqual(self.store.history_page()['history'][0]['actor'], '本地编辑')
            self.assertEqual(urllib.request.urlopen(base+'/vehicle-config/app.js').status,200)
        finally:server.shutdown();server.server_close();thread.join()
    def test_proxy_identity(self):
        server=make_server(self.store,port=0,proxy_secret='test-secret');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
        try:
            self.assertEqual(urllib.request.urlopen(base+'/healthz').status,200)
            identity = {'account':'测试员工', 'roleCode':'0', 'roleName':'计划员'}
            def encoded(fields):
                return quote(json.dumps(fields, ensure_ascii=False))
            def get(path, headers):
                return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers))
            transport = {'X-Config-Proxy-Key':'test-secret','X-Config-User':'DOMAIN/user'}
            headers = {**transport, 'Sop-Frame-Identity':encoded(identity)}
            with get('/api/session', transport) as response:
                pending = json.load(response)
            self.assertEqual((pending['mode'], pending['user'], pending['canWrite']), ('gateway', None, False))
            request = urllib.request.Request(base+'/api/campaign/import', data=b'{}',
                                             headers={**transport, 'X-CSRF-Token':pending['csrf']})
            with self.assertRaises(urllib.error.HTTPError) as blocked:
                urllib.request.urlopen(request)
            with blocked.exception as response:
                self.assertEqual(response.code, 403)
                self.assertIn('请从计划工作台重新打开', json.load(response)['error'])
            with get('/api/campaign/state', headers) as response:
                self.assertEqual(json.load(response)['revision'], 0)
            with self.assertRaises(urllib.error.HTTPError) as denied:
                get('/api/change-history', transport)
            with denied.exception as response:
                self.assertEqual(response.code, 403)
            with get('/api/change-history', headers) as response:
                self.assertEqual(json.load(response)['total'], 0)
            with get('/vehicle-config/', transport) as response:
                self.assertEqual(response.status, 200)
            invalid_identity = [{}, {**identity, 'account':' '}, {**identity, 'roleCode':0},
                                {**identity, 'account':'a'*101}, {**identity, 'roleName':'bad\nvalue'}]
            rejected = [{'Sop-Frame-Identity':encoded(identity)}, transport,
                        {**transport, 'Sop-Frame-Identity':'%FF'}]
            rejected.extend({**transport, 'Sop-Frame-Identity':encoded(fields)} for fields in invalid_identity)
            for invalid in rejected:
                with self.assertRaises(urllib.error.HTTPError) as failed:
                    get('/api/state', invalid)
                self.assertEqual(failed.exception.code, 403)
                failed.exception.close()
            with get('/api/session', headers) as response:
                session = json.load(response)
            self.assertTrue(session['canWrite'])
            self.assertEqual((session['user'], session['roleCode'], session['roleName']), ('测试员工','0','计划员'))
            req = urllib.request.Request(base+'/api/save',
                data=json.dumps({'data':fixture(),'revision':0,'owner':'旧账号'}).encode(),
                headers={**headers,'X-CSRF-Token':session['csrf']})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(json.load(response)['draft']['revision'], 1)
            self.assertEqual(self.store.history_page()['history'][0]['actor'], '测试员工')
        finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
