#!/usr/bin/env python3
"""Cheap changed-source checks; application tests are separate manifest tasks."""
import ast
import json
import os
import re
from pathlib import Path
import subprocess
import sys

root=Path.cwd()
base=os.environ.get('BASE_SHA')
args=['git','diff','--name-only','--diff-filter=ACMR','-z',base+'...HEAD'] if base else ['git','ls-files','-z']
p=subprocess.run(args,stdout=subprocess.PIPE,check=True)
paths=[Path(os.fsdecode(x)) for x in p.stdout.split(b'\0') if x]
manifest=root/'.preflight/manifest.json'
excluded=set(json.loads(manifest.read_text()).get('syntax_exclude',[])) if manifest.exists() else set()
failed=[]
for path in paths:
    if path.as_posix() in excluded:
        print('EXCLUDE negative syntax fixture: '+str(path));continue
    if path.is_symlink() or not path.is_file():continue
    try:
        if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path))
        elif path.suffix=='.json':
            source=path.read_text()
            if path.name=='turbo.json' or path.name.startswith(('tsconfig.','jsconfig.')) or '.vscode' in path.parts:
                # Preserve quoted strings before recognizing comments or trailing commas.
                string=r'"(?:\\.|[^"\\])*"'
                source=re.sub('('+string+r')|//[^\n]*|/\*[\s\S]*?\*/',lambda m:m.group(1) or ' ',source)
                source=re.sub('('+string+r')|,\s*(?=[}\]])',lambda m:m.group(1) or '',source)
            json.loads(source)
        elif path.suffix in ['.sh','.bash']:
            subprocess.run(['bash','-n',str(path)],check=True)
        elif path.suffix=='.php':subprocess.run(['php','-l',str(path)],check=True,stdout=subprocess.DEVNULL)
    except (SyntaxError,ValueError,OSError,subprocess.CalledProcessError) as exc:
        failed.append(str(path)+': '+str(exc))
print('Changed-source syntax: '+str(len(paths))+' files considered; '+str(len(failed))+' errors')
for error in failed:print(error)
sys.exit(bool(failed))
