#!/usr/bin/env python3
"""Cheap changed-source checks; application tests are separate manifest tasks."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path.cwd()
base=os.environ.get('BASE_SHA')
args=['git','diff','--name-only','--diff-filter=ACMR','-z',base+'...HEAD'] if base else ['git','ls-files','-z']
p=subprocess.run(args,stdout=subprocess.PIPE,check=True)
paths=[Path(os.fsdecode(x)) for x in p.stdout.split(b'\0') if x]
failed=[]
for path in paths:
    if path.is_symlink() or not path.is_file():continue
    try:
        if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path))
        elif path.suffix=='.json':json.loads(path.read_text())
        elif path.suffix in ['.sh','.bash']:
            subprocess.run(['bash','-n',str(path)],check=True)
        elif path.suffix=='.php':subprocess.run(['php','-l',str(path)],check=True,stdout=subprocess.DEVNULL)
    except (SyntaxError,ValueError,OSError,subprocess.CalledProcessError) as exc:
        failed.append(str(path)+': '+str(exc))
print('Changed-source syntax: '+str(len(paths))+' files considered; '+str(len(failed))+' errors')
for error in failed:print(error)
sys.exit(bool(failed))
