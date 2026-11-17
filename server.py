from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import argparse, getpass, json, os, secrets, sys, zipfile
from pathlib import Path
from urllib.parse import urlsplit
from excel_io import excel, read_excel, preview_import
from catalog import Store, validate, diff, Conflict
from shared_rules import RuleStore, validate as validate_rules
from rules_excel import rules_excel, preview_rules_import
ROOT=Path(__file__).resolve().parent

def make_server(store,host='127.0.0.1',port=8790,prefix='/vehicle-config/',origins=(),readonly=False,proxy_secret=None,rules=None):
    token=secrets.token_urlsafe(32)
    rules=rules or RuleStore(store.root,None)
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,obj,kind='application/json; charset=utf-8'):
            raw=obj if isinstance(obj,bytes) else json.dumps(obj,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
            for k,v in {'Cache-Control':'no-store','X-Frame-Options':'SAMEORIGIN','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"}.items():self.send_header(k,v)
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
                if path=='/api/state':return self.send(200,{**store.get(),'csrf':token,'readonly':readonly})
                if path=='/api/rules/state':return self.send(200,rules.get())
                if path.startswith('/api/rules/history/'):return self.send(200,rules.history(int(path.rsplit('/',1)[1])))
                if path=='/api/rules/template':return self.send(200,rules_excel(rules.get()['draft']['data'],template=True),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path.startswith('/api/history/'):return self.send(200,store.history(int(path.rsplit('/',1)[1])))
                if path=='/api/template':return self.send(200,excel(store.get()['draft']['data'],template=True),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path=='/api/export':return self.send(200,excel(store.get()['draft']['data']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                files={'/':'index.html','/rules.html':'rules.html','/app.js':'app.js','/rules.js':'rules.js','/style.css':'style.css'}
                if path not in files:return self.send(404,{'error':'未找到'})
                ext={'/':'text/html; charset=utf-8','/rules.html':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/rules.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}
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
                if size<=0 or size>8_000_000:raise ValueError('请求大小须在 1–8MB 内')
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
                    if action=='reload':return self.send(200,rules.reload(body['revision'],body['source_hash'],actor))
                    if action=='import':
                        state=rules.get()['draft']
                        if body.get('revision')!=state['revision']:raise Conflict('规则草稿已被其他人修改，请重新载入后导入')
                        return self.send(200,{**preview_rules_import(state['data'],body.get('files')),'revision':state['revision']})
                    return self.send(404,{'error':'未找到'})
                if path=='/api/import-batch':
                    if not isinstance(body,dict):raise ValueError('导入请求格式无效')
                    state=store.get()['draft']
                    if body.get('revision')!=state['revision']:raise Conflict('草稿已被其他人修改，请重新载入后导入')
                    return self.send(200,{**preview_import(state['data'],body.get('files')),'revision':state['revision']})
                if path=='/api/export':return self.send(200,excel(body['data']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                if path=='/api/preview':return self.send(200,{'errors':validate(body['data'],True),'changes':diff(store.get()['published']['data'],body['data'])})
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

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8790);p.add_argument('--public-prefix',default='/vehicle-config/');p.add_argument('--data-dir');p.add_argument('--shared-config');p.add_argument('--public-origin',action='append',default=[]);p.add_argument('--read-only',action='store_true');p.add_argument('--require-proxy-identity',action='store_true');a=p.parse_args()
    if a.host not in ['127.0.0.1','::1']:p.error('仅允许回环监听，通过部门门户代理访问')
    if not a.public_prefix.startswith('/') or not a.public_prefix.endswith('/') or '..' in a.public_prefix:p.error('公共前缀格式无效')
    workspace=next((x for x in ROOT.parents if x.name=='Project'),None)
    data_root=(ROOT / os.environ['PLANHUB_DATA_ROOT']).resolve() if os.environ.get('PLANHUB_DATA_ROOT') else ROOT.parent/'工作台数据'
    directory=a.data_dir or str(data_root/'database'/'vehicle-config')
    legacy=(workspace/'共享数据'/'车型配置') if workspace else None
    if not a.data_dir and not Path(directory).exists() and legacy and (legacy/'catalog.sqlite3').exists():p.error('旧车型配置数据库尚未迁移，不会创建空库。')
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
    source=a.shared_config or os.environ.get('VEHICLE_CONFIG_SHARED_CONFIG') or (workspace/'订单分析'/'scripts'/'config'/'shared_config.py' if workspace else None)
    store=Store(root);rules=RuleStore(root,source);server=make_server(store,a.host,a.port,a.public_prefix,a.public_origin,a.read_only,secret,rules)
    print(f'车型配置管理 http://{a.host}:{a.port}{a.public_prefix} | {root}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close();store.db.close();lock.close()
if __name__=='__main__':main()
