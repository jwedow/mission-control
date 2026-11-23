#!/usr/bin/env python3
"""Install a repo-local dispatcher without overwriting any existing hooks."""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

MARKER='aulron-preflight-v1'

def git(*args):return subprocess.check_output(['git',*args],text=True).strip()

def install():
    root=Path(git('rev-parse','--show-toplevel'))
    common=Path(git('rev-parse','--git-common-dir'))
    if not common.is_absolute():common=(root/common).resolve()
    dest=common/'aulron-preflight-hooks';state=dest/'state.json'
    scoped=subprocess.run(['git','config','--show-scope','--get','core.hooksPath'],capture_output=True,text=True).stdout
    if scoped.startswith(('worktree', 'command')):raise ValueError('worktree/command hooksPath must be reconciled before installation')
    configured=subprocess.run(['git','config','--path','--get','core.hooksPath'],capture_output=True,text=True).stdout.strip()
    previous=Path(configured) if configured else common/'hooks'
    if not previous.is_absolute():previous=(root/previous).resolve()
    if previous==dest:
        data=json.loads(state.read_text())
        if data.get('marker')!=MARKER:raise ValueError('unrecognized hook installation')
        previous=Path(data['previous'])
    elif dest.exists():raise ValueError('hook installation exists but another hooksPath is active; refusing to overwrite')
    if previous==dest:raise ValueError('recursive hook configuration')
    dest.mkdir(exist_ok=True)
    # Execute originals by their original path, preserving scripts that use dirname $0.
    if previous.exists():
        for path in previous.iterdir():
            if path.name=='pre-push' or not path.is_file():continue
            target=dest/path.name
            if target.is_symlink():target.unlink()
            target.write_text('#!/bin/sh\nexec '+shlex.quote(str(path.resolve()))+' \"$@\"\n')
            target.chmod(path.stat().st_mode & 0o777)
    shutil.copyfile(Path(__file__).with_name('preflight.py'),dest/'preflight.py')
    data={'marker':MARKER,'previous':str(previous)}
    state.write_text(json.dumps(data)+'\n')
    wrapper='''#!/usr/bin/env python3
# aulron-preflight-v1
import json, os, pathlib, subprocess, sys
here=pathlib.Path(__file__).resolve().parent
state=json.loads((here/'state.json').read_text())
body=sys.stdin.buffer.read()
previous=pathlib.Path(state['previous'])/'pre-push'
if previous.is_file() and os.access(previous,os.X_OK):
    result=subprocess.run([str(previous),*sys.argv[1:]],input=body)
    if result.returncode:sys.exit(result.returncode)
args=[sys.executable,str(here/'preflight.py'),'--push']
if len(sys.argv)>1:args+=['--remote',sys.argv[1]]
sys.exit(subprocess.run(args,input=body).returncode)
'''
    (dest/'pre-push').write_text(wrapper);(dest/'pre-push').chmod(0o755)
    git('config','--local','core.hooksPath',str(dest))
    if git('config','--get','core.hooksPath')!=str(dest):raise ValueError('higher-priority hooksPath overrides installation')
    print('Preflight installed; existing hooks preserved at '+str(previous))

if __name__=='__main__':
    try:install()
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        print('Installation blocked: '+str(exc),file=sys.stderr);sys.exit(1)
