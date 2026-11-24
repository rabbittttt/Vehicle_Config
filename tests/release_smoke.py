import hashlib, json, subprocess, sys, tempfile, time, socket, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));from test_catalog import fixture
with tempfile.TemporaryDirectory() as tmp:
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    payload=Path(sys.argv[1]).resolve()/'payload';base=f'http://127.0.0.1:{port}'
    cmd=[sys.executable,'-B',str(payload/'server.py'),'--port',str(port),'--data-dir',tmp]
    manifest=json.loads((payload.parent/'app-release.json').read_text('utf-8'))
    expected={f['path']:f for f in manifest['files']}
    assert {p.relative_to(payload).as_posix() for p in payload.rglob('*') if p.is_file()}==expected.keys()
    for name,entry in expected.items():
        content=(payload/name).read_bytes()
        assert len(content)==entry['bytes'] and hashlib.sha256(content).hexdigest()==entry['sha256'],name
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
        if manifest.get('gitCommit'):
            info=json.load(urllib.request.urlopen(base+'/api/runtime'))
            assert info['release']['gitCommit']==manifest['gitCommit']
            assert info['release']['version']==manifest['version']
            assert Path(info['data_dir'])==Path(tmp).resolve()
        state=json.load(urllib.request.urlopen(base+'/api/state'))
        req=urllib.request.Request(base+'/api/publish',data=json.dumps({'revision':state['draft']['revision'],'data':fixture()}).encode(),headers={'X-CSRF-Token':state['csrf']})
        assert json.load(urllib.request.urlopen(req))['published']['revision']==1
        # Use a separate listening port so a bind failure cannot pass the storage-lock check.
        second_cmd=cmd.copy()
        second_cmd[second_cmd.index('--port')+1]='0'
        other=subprocess.Popen(second_cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            _,error=other.communicate(timeout=60)
            assert other.returncode and b'msvcrt.locking' in error, error
        finally:
            if other.poll() is None:stop(other)
    finally:stop(proc)
    proc=start()
    try:assert json.load(urllib.request.urlopen(base+'/api/state'))['published']['data']==fixture()
    finally:stop(proc)
print('Immutable release startup, publishing, restart persistence and single-writer lock passed.')
print('Tested version:',manifest['version'],'| build commit:',manifest.get('gitCommit','unknown'),'| deployed version: not checked')
