import {loadAppIntegrations} from '../../计划工作台/scripts/app-integrations.mjs';
import {startAppServices} from '../../计划工作台/scripts/app-services.mjs';
import {createServer} from '../../计划工作台/scripts/dev-server.mjs';
import {mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),portal=path.resolve(root,'../计划工作台');
const app=(await loadAppIntegrations(portal))['vehicle-config'];
const directory=await mkdtemp(path.join(root,'artifacts/portal-smoke-'));
app.start.args.push('--data-dir',directory);
const apps={'vehicle-config':app},stop=await startAppServices(apps,{root});
if(stop.failures.length)throw Error(stop.failures.join('\n'));
const server=createServer({root:portal,applications:apps});
try{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const result=await promisify(execFile)(app.start.command,['-B',path.join(root,'tests/portal_browser.py'),`http://127.0.0.1:${server.address().port}`],{cwd:root,env:{...process.env,PYTHONIOENCODING:'utf-8'}});console.log(result.stdout);}finally{await new Promise(resolve=>server.close(resolve));stop();}
