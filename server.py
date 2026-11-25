from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import argparse, getpass, json, os, secrets, sys, zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from excel_io import excel, read_excel, preview_import
from catalog import Store, Conflict, PUBLISH_REQUIRED, IDENTITY_FIELDS, identity, validate as validate_catalog
from shared_rules import RuleStore, validate as validate_rules
from rules_excel import rules_excel, preview_rules_import
ROOT=Path(__file__).resolve().parent

def import_preview(body, draft, preview, validate, conflict_message):
    if body.get('revision') != draft['revision']:
        raise Conflict(conflict_message)
    base = draft['data']
    if 'editor' in body:
        base = body['editor']
        errors = validate(base)
        if errors:
            raise ValueError(errors[0])
    return {**preview(base, body.get('files')), 'revision': draft['revision']}

def make_server(store,host='127.0.0.1',port=8790,prefix='/vehicle-config/',origins=(),readonly=False,proxy_secret=None,rules=None,path_sources=None):
    token=secrets.token_urlsafe(32)
    rules=rules or RuleStore(store.root,None)
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,obj,kind='application/json; charset=utf-8'):
            raw=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
            for k,v in {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'"}.items():self.send_header(k,v)
            if kind.startswith('application/vnd'):self.send_header('Content-Disposition','attachment; filename="vehicle-config.xlsx"')
            self.end_headers();self.wfile.write(raw)
        def route(self):
            path=urlsplit(self.path).path
            if path.startswith(prefix):path='/'+path[len(prefix):]
            return path
        def check_host(self):
            host=urlsplit('http://'+self.headers.get('Host','')).hostname
            allowed={'localhost','127.0.0.1','::1'}|{urlsplit(origin).hostname for origin in origins}
            if host not in allowed:raise PermissionError('Host 不受信任')
        def actor(self):
            if proxy_secret:
                if not secrets.compare_digest(self.headers.get('X-Config-Proxy-Key',''),proxy_secret):raise PermissionError('缺少可信代理身份')
                actor=self.headers.get('X-Config-User','').strip()
                if not actor or len(actor)>200:raise PermissionError('缺少操作人身份')
                return actor
            return '本机:'+getpass.getuser()
        def do_GET(self):
            try:
                path=self.route()
                self.check_host()
                if path=='/healthz':return self.send(200,{'ok':True,'app':'vehicle-config','public_prefix':prefix})
                self.actor()
                if path=='/api/state':return self.send(200,{**store.get(),'csrf':token,'readonly':readonly,'required':PUBLISH_REQUIRED,'identity_fields':IDENTITY_FIELDS})
                if path=='/api/runtime':
                    return self.send(200,runtime_info(store.root,rules.source_path,path_sources or {}))
                if path=='/api/rules/state':return self.send(200,rules.get())
                if path in ('/api/history','/api/rules/history'):
                    params=parse_qs(urlsplit(self.path).query)
                    page=int(params.get('page',['1'])[0])
                    owner=rules if path=='/api/rules/history' else store
                    return self.send(200,owner.history_page(page,params.get('q',[''])[0]))
                if path.startswith('/api/rules/history/'):return self.send(200,rules.history(int(path.rsplit('/',1)[1])))
                if path=='/api/rules/template':return self.send(200,rules_excel(rules.get()['draft']['data'],template=True),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path.startswith('/api/history/'):return self.send(200,store.history(int(path.rsplit('/',1)[1])))
                if path=='/api/template':return self.send(200,excel(store.get()['draft']['data'],template=True),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path=='/api/export':return self.send(200,excel(store.get()['draft']['data']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path=='/rules.html':
                    self.send_response(302)
                    self.send_header('Location','./')
                    self.send_header('Content-Length','0')
                    self.end_headers()
                    return
                files={'/':'index.html','/app.js':'app.js','/rules.js':'rules.js','/style.css':'style.css'}
                if path not in files:return self.send(404,{'error':'未找到'})
                ext={'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/rules.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}
                self.send(200,(ROOT/'web'/files[path]).read_bytes(),ext[path])
            except PermissionError as e:self.send(403,{'error':str(e)})
            except (ValueError,KeyError) as e:self.send(400,{'error':str(e)})
            except Exception as e:
                print(type(e).__name__,str(e),file=sys.stderr);self.send(500,{'error':'读取失败，请检查服务日志'})
        def do_POST(self):
            try:
                self.check_host()
                actor=self.actor()
                origin=self.headers.get('Origin')
                allowed=set(origins)|{'http://'+self.headers.get('Host','')}
                if origin and origin not in allowed:raise PermissionError('来源不受信任')
                if not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),token):raise PermissionError('请求校验失败，请刷新页面')
                path=self.route()
                if readonly and path not in ['/api/export','/api/rules/export']:raise PermissionError('服务维护中，暂不可写入')
                size=int(self.headers.get('Content-Length',0))
                # A preview can include both a full editor snapshot and encoded workbooks.
                limit_mb = 16 if path in ('/api/import-batch', '/api/rules/import') else 8
                if size <= 0 or size > limit_mb * 1_000_000:
                    raise ValueError(f'请求正文不能为空或超过 {limit_mb} MB')
                raw=self.rfile.read(size);path=self.route()
                if path=='/api/import':
                    state=store.get()['draft']
                    return self.send(200,{**preview_import(state['data'],[{'name':'导入文件.xlsx','raw':raw}]),'revision':state['revision']})
                body=json.loads(raw)
                if path.startswith('/api/rules/'):
                    if not isinstance(body,dict):raise ValueError('规则请求格式无效')
                    action=path.rsplit('/',1)[1]
                    if action=='validate':return self.send(200,{'errors':validate_rules(body['data'])})
                    if action=='export':return self.send(200,rules_excel(body['data']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    if action=='save':return self.send(200,rules.save(body['data'],body['revision'],actor))
                    if action=='preview':return self.send(200,rules.preview(body['data'],body['revision']))
                    if action=='publish':return self.send(200,rules.publish(body['data'],body['revision'],body['source_hash'],actor))
                    if action=='reload':return self.send(200,rules.reload(body['revision'],body['source_hash'],actor,body.get('pending_token')))
                    if action=='import':
                        state=rules.get()['draft']
                        return self.send(200,import_preview(body,state,preview_rules_import,validate_rules,'规则草稿已被其他人修改，请重新载入后导入'))
                    return self.send(404,{'error':'未找到'})
                if path=='/api/check-name':
                    rows=body['others']
                    if not isinstance(rows,list):raise ValueError('别名列表格式无效')
                    def alias_key(row):
                        if not isinstance(row,dict) or any(not isinstance(row.get(f),str) for f in IDENTITY_FIELDS['aliases']):
                            raise ValueError('来源与别名须为文本')
                        return identity('aliases',row)
                    candidate=alias_key(body['row'])
                    duplicate=any(alias_key(r)==candidate for r in rows)
                    return self.send(200,{'duplicate':duplicate})
                if path=='/api/import-batch':
                    if not isinstance(body,dict):raise ValueError('导入请求格式无效')
                    state=store.get()['draft']
                    return self.send(200,import_preview(body,state,preview_import,validate_catalog,'草稿已被其他人修改，请重新载入后导入'))
                if path=='/api/export':return self.send(200,excel(body['data']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path=='/api/preview':return self.send(200,store.preview(body['data'],body.get('revision')))
                if path in ['/api/save','/api/publish']:return self.send(200,store.save(body['data'],body['revision'],actor,path.endswith('publish')))
                self.send(404,{'error':'未找到'})
            except Conflict as e:self.send(409,{'error':str(e)})
            except PermissionError as e:self.send(403,{'error':str(e)})
            except (ValueError,KeyError,TypeError,zipfile.BadZipFile) as e:self.send(400,{'error':str(e)})
            except Exception as e:
                print(type(e).__name__,str(e),file=sys.stderr);self.send(500,{'error':'保存或读取失败，请重新载入核对状态并检查服务日志'})
    class Server(ThreadingHTTPServer):
        def server_close(self):
            super().server_close()
            rules.db.close()
    return Server((host,port),Handler)

def runtime_info(directory,source,sources):
    root=Path(directory).resolve()
    script=Path(source).resolve() if source else None
    manifest=ROOT.parent/'app-release.json'
    release={}
    if ROOT.name=='payload' and manifest.is_file():
        try:
            candidate=json.loads(manifest.read_text('utf-8-sig'))
            if isinstance(candidate,dict) and candidate.get('appId')=='vehicle-config':
                release={k:candidate.get(k) for k in ('version','gitCommit','sourceDirty','sourceRevision')}
        except (ValueError,OSError):pass
    return {'release':release,'data_dir':str(root),'catalog':str(root/'catalog.sqlite3'),
            'rules_db':str(root/'rules'/'rules.sqlite3'),'shared_config':str(script) if script else '',
            'script_exists':bool(script and script.is_file()),'sources':sources,'excel':'浏览器下载目录'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8790);p.add_argument('--public-prefix',default='/vehicle-config/');p.add_argument('--data-dir');p.add_argument('--shared-config');p.add_argument('--public-origin',action='append',default=[]);p.add_argument('--read-only',action='store_true');p.add_argument('--require-proxy-identity',action='store_true');p.add_argument('--show-paths',action='store_true');a=p.parse_args()
    if a.host not in ['127.0.0.1','::1']:p.error('仅允许回环监听，通过部门门户代理访问')
    if not a.public_prefix.startswith('/') or not a.public_prefix.endswith('/') or '..' in a.public_prefix:p.error('公共前缀格式无效')
    workspace=next((x for x in ROOT.parents if x.name=='Project'),None)
    data_root=(ROOT / os.environ['PLANHUB_DATA_ROOT']).resolve() if os.environ.get('PLANHUB_DATA_ROOT') else ROOT.parent/'工作台数据'
    directory=a.data_dir or str(data_root/'database'/'vehicle-config')
    source=a.shared_config or os.environ.get('VEHICLE_CONFIG_SHARED_CONFIG') or (workspace/'订单分析'/'scripts'/'config'/'shared_config.py' if workspace else None)
    sources={'data_dir':'--data-dir' if a.data_dir else 'PLANHUB_DATA_ROOT' if os.environ.get('PLANHUB_DATA_ROOT') else '工作区默认',
             'shared_config':'--shared-config' if a.shared_config else 'VEHICLE_CONFIG_SHARED_CONFIG' if os.environ.get('VEHICLE_CONFIG_SHARED_CONFIG') else '工作区默认' if workspace else '未配置'}
    if a.show_paths:
        print(json.dumps(runtime_info(directory,source,sources),ensure_ascii=False,indent=2))
        return
    legacy=(workspace/'共享数据'/'车型配置') if workspace else None
    if legacy and Path(directory).resolve()==(data_root/'database'/'vehicle-config').resolve():
        for db_file in ('catalog.sqlite3','rules/rules.sqlite3'):
            target=Path(directory)/db_file
            if (legacy/db_file).is_file() and (not target.is_file() or target.stat().st_size==0):
                p.error(f'旧车型配置数据库 {db_file} 尚未迁移，请停服迁移后启动。')
    secret=os.environ.get('VEHICLE_CONFIG_PROXY_SECRET')
    if a.require_proxy_identity and not secret:p.error('缺少 VEHICLE_CONFIG_PROXY_SECRET')
    # Lifetime OS lock prevents two versions writing the same persistent catalog.
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=True);lock=open(root/'service.lock','a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    store=Store(root);rules=RuleStore(root,source);server=make_server(store,a.host,a.port,a.public_prefix,a.public_origin,a.read_only,secret,rules,sources)
    print(f'车型配置管理 http://{a.host}:{a.port}{a.public_prefix} | {root}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();store.db.close();lock.close()
if __name__=='__main__':main()
