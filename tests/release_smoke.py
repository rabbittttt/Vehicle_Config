import json, subprocess, sys, tempfile, time, socket, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));from test_catalog import fixture
with tempfile.TemporaryDirectory() as tmp:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    payload=Path(sys.argv[1]).resolve()/'payload';base=f'http://127.0.0.1:{port}'
    cmd=[sys.executable,'-B',str(payload/'server.py'),'--port',str(port),'--data-dir',tmp]
    def stop(proc):
        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        proc.wait(timeout=5)
    def start():
        proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:urllib.request.urlopen(base+'/healthz',timeout=.2).close();return proc
            except Exception:
                if proc.poll() is not None:raise RuntimeError('release startup failed')
                time.sleep(.1)
        stop(proc);raise RuntimeError('startup timeout')
    proc=start()
    try:
        state=json.load(urllib.request.urlopen(base+'/api/state'))
        req=urllib.request.Request(base+'/api/publish',data=json.dumps({'revision':state['draft']['revision'],'data':fixture()}).encode(),headers={'X-CSRF-Token':state['csrf']})
        assert json.load(urllib.request.urlopen(req))['published']['revision']==1
        other=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5);assert other.returncode!=0,'second writer accepted'
    finally:stop(proc)
    proc=start()
    try:assert json.load(urllib.request.urlopen(base+'/api/state'))['published']['data']==fixture()
    finally:stop(proc)
print('Immutable release startup, publishing, restart persistence and single-writer lock passed.')
