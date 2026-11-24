"""Run repository acceptance checks with the selected Python interpreter."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def run(*args):
    print('>',subprocess.list2cmdline(list(args)),flush=True)
    subprocess.run(args,cwd=ROOT,check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--release',type=Path,help='Also check an immutable release directory')
    args=parser.parse_args()
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT):
        commit+=' (working tree changes)'
    run(sys.executable,'-B','-m','unittest','discover','-s','tests','-q')
    run(sys.executable,'-B','tests/browser_imported_year.py')
    if args.full:
        for name in ['browser_check','browser_bulk_check','browser_rules_check','browser_header_check']:
            run(sys.executable,'-B','tests/'+name+'.py')
        run('node','tests/portal_smoke.mjs')
    if args.release:
        run(sys.executable,'-B','tests/release_smoke.py',str(args.release.resolve()))
    print('Acceptance passed at',commit,'| release:',args.release or 'not tested','| deployment: not requested')


if __name__=='__main__':main()
