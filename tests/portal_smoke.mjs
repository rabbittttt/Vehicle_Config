import {loadAppIntegrations} from '../../计划工作台/scripts/app-integrations.mjs';
import {startAppServices} from '../../计划工作台/scripts/app-services.mjs';
import {createServer} from '../../计划工作台/scripts/dev-server.mjs';
import {mkdtemp,writeFile} from 'node:fs/promises';
import {createServer as createPortProbe} from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),portal=path.resolve(root,'../计划工作台');
const app=(await loadAppIntegrations(portal))['vehicle-config'];
const directory=await mkdtemp(path.join(root,'artifacts/portal-smoke-'));
const script=path.join(directory,'shared_config.py');
await writeFile(script, 'GEN_DISPLAY_NAME_MAP = {"old": "standard"}\n', 'utf8');
// Exercise the registered launcher without reusing the running workbench service.
const probe=createPortProbe();
await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const port=String(probe.address().port);
await new Promise(resolve=>probe.close(resolve));
app.target='http://127.0.0.1:'+port;
app.start.args.push('--port',port,'--data-dir',directory,'--shared-config',script);
const apps={'vehicle-config':app},stop=await startAppServices(apps,{root,logRoot:path.join(directory,'logs')});
if(stop.failures.length)throw Error(stop.failures.join('\n'));
const server=createServer({root:portal,applications:apps});
try{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const result=await promisify(execFile)(app.start.command,['-B',path.join(root,'tests/portal_browser.py'),`http://127.0.0.1:${server.address().port}`],{cwd:root,env:{...process.env,PYTHONIOENCODING:'utf-8'}});console.log(result.stdout);}finally{await new Promise(resolve=>server.close(resolve));stop();}
