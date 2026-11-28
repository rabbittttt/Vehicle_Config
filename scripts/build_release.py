import argparse, hashlib, json, re, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def build(destination,version):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',version) or version.endswith('.'):raise ValueError('版本号无效')
    names=['server.py','excel_io.py','xlsx_input.py','rules_excel.py','shared_rules.py','catalog.py',
           'campaign_store.py','campaign_merge.py','stage_workbook.py','daily_import.py','requirements.txt','README.md','start.ps1','启动车型配置.cmd',
           'web/index.html','web/frame-account.js','web/rules.js','web/campaign.js','web/app.js','web/style.css']
    snapshots={name:(ROOT/name).read_bytes() for name in names}
    files=[{'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in sorted(snapshots.items())]
    manifest={'schemaVersion':1,'appId':'vehicle-config','version':version,'type':'service','basePath':'/vehicle-config/','entryPoint':'server.py','sourceRevision':'payload-sha256:'+hashlib.sha256(json.dumps(files).encode()).hexdigest(),'dataMode':'external','healthPath':'/healthz','files':files}
    if (ROOT/'.git').exists():
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        dirty=subprocess.check_output(['git','status','--porcelain','--',*names,'scripts/build_release.py'],cwd=ROOT)
        manifest.update(gitCommit=commit,sourceDirty=bool(dirty))
    destination.mkdir(parents=True,exist_ok=False)
    for name,raw in snapshots.items():
        target=destination/'payload'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    (destination/'app-release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    manifest = build(args.destination, args.version)
    print(f"{args.destination.resolve()} ({manifest['version']}, {len(manifest['files'])} payload files)")
