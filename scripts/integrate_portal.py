import json, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];portal=ROOT.parent/'计划工作台'
def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
registration={'id':'vehicle-config','name':'车型配置','type':'proxy','group':'tools','icon':'grid','order':45,'status':'ready','port':8790,'stripPrefix':True,'healthPath':'/healthz','healthKeys':['ok','app','public_prefix'],'healthExpected':{'ok':True,'app':'vehicle-config','public_prefix':'{basePath}'},'start':{'cwd':'../配置管理','command':'../.venv/Scripts/python.exe','args':['-B','server.py','--host','{host}','--port','{port}','--public-prefix','{basePath}']}}
registry=read(portal/'portal/config/apps.json')
if any(a['id']=='vehicle-config' for g in registry['groups'] for a in g['apps']):registration['replace']=True
config=ROOT/'artifacts'/'registration.json';config.parent.mkdir(exist_ok=True);write(config,registration)
subprocess.run(['node',str(portal/'scripts/register-app.mjs'),'--config',str(config),'--dry-run'],cwd=portal,check=True)
subprocess.run(['node',str(portal/'scripts/register-app.mjs'),'--config',str(config)],cwd=portal,check=True)
local=read(portal/'config.local/apps.json');integration=read(portal/'integration/apps.json');integration['applications']['vehicle-config']=local['applications'].pop('vehicle-config');write(portal/'integration/apps.json',integration);write(portal/'config.local/apps.json',local)
publish=read(portal/'deploy/publish.example.json');publish['applications']['vehicle-config']={'kind':'release','sourceRoot':'../配置管理','build':{'command':'../.venv/Scripts/python.exe','args':['-B','scripts/build_release.py','--version','{version}','--destination','{output}']}};write(portal/'deploy/publish.example.json',publish)
deployment=read(portal/'deploy/deployment.example.json');deployment['applications']['vehicle-config']={'kind':'service','command':'runtime/vehicle-config/Scripts/python.exe','arguments':['-B','{payload}/server.py','--host','127.0.0.1','--port','{port}','--data-dir','{data}','--public-prefix','{basePath}','--public-origin','{publicOrigin}','--require-proxy-identity'],'environment':{'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1','PYTHONDONTWRITEBYTECODE':'1'},'secretEnvironmentFiles':{'VEHICLE_CONFIG_PROXY_SECRET':'config/vehicle-config-proxy.secret'},'dataDirectory':'{shared}/车型配置','port':8790,'taskName':'PlanHub-vehicle-config','backendHealthPath':'/healthz','healthExpected':{'ok':True,'app':'vehicle-config','public_prefix':'{basePath}'},'proxyMode':'existing','stripPrefix':True,'maxRequestBytes':8000000};write(portal/'deploy/deployment.example.json',deployment)
p=portal/'README.md';s=p.read_text(encoding='utf-8');line='| 车型配置 | 动态 | `../配置管理/server.py` | `/vehicle-config/` |';
if line not in s:s=s.replace('| 数据速查 | 动态 | `../数据速查` | `/quicklook/` |','| 数据速查 | 动态 | `../数据速查` | `/quicklook/` |\n'+line);p.write_text(s,encoding='utf-8')
p=portal/'docs/child-app-developer-guide.md';s=p.read_text(encoding='utf-8');line='- **车型配置**：动态服务 `/vehicle-config/`，开发源码 `../配置管理`，健康接口 `/healthz`；`scripts/build_release.py` 生成独立通用包。`--port`、`--public-prefix` 与 `--data-dir` 配置运行位置；共享持久目录为 `共享数据/车型配置`。草稿/发布分离，业务报表尚未接入。生产必须启用 `--require-proxy-identity`，外置 `VEHICLE_CONFIG_PROXY_SECRET`，由 Windows 认证代理覆盖 `X-Config-User` 与 `X-Config-Proxy-Key`；模板 `proxyMode=existing`，先在目标机配置可信身份桥，不能仅凭健康检查判定上线。开发模式记录本机操作人。详见原项目 README。'
if line not in s:s=s.replace('## 当前版本核对（2026-11-12）','## 当前版本核对（2026-11-12）\n\n'+line);p.write_text(s,encoding='utf-8')
print('Portal integration updated; no deployment performed.')
