#!/usr/bin/env python3
"""Shared, dependency-aware preflight. No deployment, credential or merge authority."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

VERSION = '1.0.0'

def git(*args, cwd=None):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false', *args], cwd=cwd, text=True).strip()

def load_manifest(root):
    data = json.loads((root / '.preflight/manifest.json').read_text())
    if data.get('version') != 1 or not isinstance(data.get('tasks'), list) or not data['tasks']:
        raise ValueError('manifest must have version 1 and a nonempty tasks list')
    tasks = data['tasks']; ids = [t['id'] for t in tasks]
    if len(set(ids)) != len(ids): raise ValueError('duplicate task id')
    for t in tasks:
        if not isinstance(t.get('command'), list) or not t['command'] or not all(isinstance(x,str) for x in t['command']):
            raise ValueError('command must be a nonempty argv list: '+t['id'])
        if any(x not in ids for x in t.get('needs', [])): raise ValueError('unknown dependency: '+t['id'])
        if not 0 < t.get('timeout', 1200) <= 3600: raise ValueError('invalid timeout: '+t['id'])
        dest=(root / t.get('cwd','.')).resolve()
        if dest != root.resolve() and root.resolve() not in dest.parents: raise ValueError('task cwd escapes snapshot')
    pending={t['id']:set(t.get('needs',[])) for t in tasks}; done=set()
    while pending:
        ready={key for key,deps in pending.items() if deps <= done}
        if not ready: raise ValueError('dependency cycle')
        done |= ready
        for key in ready: del pending[key]
    return data

def execute(task, root, env):
    p=None
    try:
        p=subprocess.Popen(task['command'],cwd=root/task.get('cwd','.'),env=env,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        output,_=p.communicate(timeout=task.get('timeout',1200))
        return {'id':task['id'],'status':'PASS' if p.returncode==0 else 'FAIL','code':p.returncode,'output':output[-12000:]}
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGKILL);output,_=p.communicate()
        return {'id':task['id'],'status':'FAIL','code':124,'output':'Timed out\n'+output[-12000:]}
    except OSError as exc:
        return {'id':task['id'],'status':'FAIL','code':127,'output':str(exc)}

def run(root, base=None, jobs=2):
    data=load_manifest(root)
    # Do not pass the developer's tokens, .env files or global package-manager config to checks.
    keep=['PATH','SYSTEMROOT','TMPDIR','TEMP','TMP','LANG','LC_ALL','USER','LOGNAME']
    env={k:os.environ[k] for k in keep if k in os.environ}
    with tempfile.TemporaryDirectory(prefix='preflight-home-') as home:
        env.update(HOME=home,CI='true',GIT_CONFIG_GLOBAL=os.devnull,GIT_CONFIG_NOSYSTEM='1',
                   npm_config_userconfig=os.devnull,npm_config_cache=str(Path.home()/'.npm'))
        env.update(data.get('environment',{}))
        if base:env.update(BASE_SHA=base,TURBO_SCM_BASE=base,EVENT_NAME='pull_request')
        results={};pending=list(data['tasks'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            active={}
            while pending or active:
                for task in pending[:]:
                    needs=task.get('needs',[])
                    if not all(n in results for n in needs):continue
                    if any(results[n]['status']!='PASS' for n in needs):
                        results[task['id']]={'id':task['id'],'status':'SKIP','output':'Dependency failed'}
                        print('SKIP '+task['id']+' (dependency failed)',flush=True);pending.remove(task)
                    elif len(active)<jobs:
                        print('RUN '+task['id'],flush=True)
                        active[pool.submit(execute,task,root,env)]=task;pending.remove(task)
                if not active:continue
                completed,_=concurrent.futures.wait(active,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in completed:
                    task=active.pop(future);result=future.result();results[task['id']]=result
                    print(result['status']+' '+task['id'],flush=True)
                    if result['status']=='FAIL':print(result['output'],flush=True)
        for check in data.get('cloud_checks',[]): print('CLOUD REQUIRED: '+check)
        print('Preflight: '+', '.join(f'{s}={sum(r["status"]==s for r in results.values())}' for s in ['PASS','FAIL','SKIP']))
        unchanged=subprocess.run(['git','diff','--quiet','HEAD','--'],cwd=root).returncode==0
        if not unchanged:print('FAIL source-integrity: validation changed tracked source (or checkout was already dirty)')
        return unchanged and all(r['status']=='PASS' for r in results.values())

def snapshot_check(repo, revision, base, jobs):
    # --no-local avoids mutable hardlinks/alternates. Clone config and hooks are never inherited.
    with tempfile.TemporaryDirectory(prefix='git-preflight-') as temp:
        dest=Path(temp)/'source'
        git('-c','protocol.file.allow=always','clone','--quiet','--no-local','--no-checkout',str(repo),str(dest))
        git('-c','protocol.file.allow=always','fetch','--quiet','--no-tags',str(repo),revision,*([base] if base else []),cwd=dest)
        git('checkout','--quiet','--detach',revision,cwd=dest)
        actual=git('rev-parse','HEAD',cwd=dest)
        print('Checking committed source '+actual,flush=True)
        return run(dest,base,jobs)

def push(repo, args):
    # Git supplies every ref update. Validate every distinct pushed commit, not merely HEAD.
    revisions=[]
    for line in sys.stdin:
        fields=line.split()
        if len(fields)!=4:raise ValueError('invalid pre-push ref input')
        local_ref,sha,remote_ref,old=fields
        if set(sha)=={'0'}:continue
        commit=git('rev-parse',sha+'^{commit}',cwd=repo)
        if commit not in revisions:revisions.append(commit)
    if not revisions:return True
    manifest=load_manifest(repo)
    branch=manifest.get('base','main')
    # Read remote base and fetch it without changing the author's branches or working files.
    remote=args.remote or 'origin'
    git('check-ref-format','refs/heads/'+branch,cwd=repo)
    git('fetch','--quiet','--no-tags','--',remote,'refs/heads/'+branch,cwd=repo)
    base=git('rev-parse','FETCH_HEAD^{commit}',cwd=repo)
    print('Verified target base '+base,flush=True)
    success=True
    for revision in revisions:
        try:success=snapshot_check(repo,revision,base,args.jobs) and success
        except (ValueError,OSError,subprocess.CalledProcessError) as exc:
            print('FAIL snapshot: '+str(exc),file=sys.stderr);success=False
    return success

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--push',action='store_true');parser.add_argument('--remote');parser.add_argument('--base')
    parser.add_argument('--jobs',type=int,choices=range(1,5),default=2)
    args=parser.parse_args()
    try:
        repo=Path(git('rev-parse','--show-toplevel'))
        passed=push(repo,args) if args.push else run(repo,args.base,args.jobs)
        return 0 if passed else 1
    except (ValueError,KeyError,OSError,subprocess.CalledProcessError) as exc:
        print('Preflight blocked: '+str(exc),file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
