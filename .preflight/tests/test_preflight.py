import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]

class GitPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.remote = self.root / 'remote.git'
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
        self.run_cmd(['git','init','--bare',str(self.remote)],self.root)
        self.run_cmd(['git','init','-b','main',str(self.repo)],self.root)
        self.git('remote','add','origin',str(self.remote))
        self.write('content.txt','good')
        self.commit()
        self.git('push','-u','origin','main')
        self.git('--git-dir='+str(self.remote),'symbolic-ref','HEAD','refs/heads/main')
        target=self.repo/'.preflight'
        target.mkdir()
        for name in ['preflight.py','install.py']:
            self.assertTrue((SOURCE/name).exists(), 'Missing preflight implementation: '+name)
            shutil.copyfile(SOURCE/name,target/name)
        self.manifest([self.task('content',"from pathlib import Path; assert Path('content.txt').read_text() == 'good'")])
        self.commit()

    def run_cmd(self,args,cwd=None,input=None,check=True):
        p=subprocess.run(args,cwd=cwd or self.repo,env=self.env,input=input,text=True,capture_output=True)
        if check and p.returncode: self.fail(f'{args}: {p.stdout}\n{p.stderr}')
        return p
    def git(self,*args,**kw):return self.run_cmd(['git',*args],**kw)
    def write(self,path,text):
        p=self.repo/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
    def commit(self):self.git('add','.');self.git('commit','-qm','fixture')
    def task(self,id,code,**extra):return dict(id=id,command=[sys.executable,'-c',code],**extra)
    def manifest(self,tasks):self.write('.preflight/manifest.json',json.dumps({'version':1,'base':'main','tasks':tasks,'cloud_checks':[]}))
    def install(self):return self.run_cmd([sys.executable,'.preflight/install.py'])
    def push(self,*refs):return self.git('push','origin',*(refs or ('HEAD:refs/heads/check',)),check=False)

    def test_pass_allows_real_push(self):
        self.install();self.assertEqual(self.push().returncode,0)
    def test_committed_failure_blocks_despite_dirty_fix(self):
        self.write('content.txt','bad');self.commit();self.write('content.txt','good');self.install()
        p=self.push();self.assertNotEqual(p.returncode,0);self.assertIn('FAIL content',p.stdout+p.stderr)
        self.assertEqual((self.repo/'content.txt').read_text(),'good')
    def test_collects_independent_failures_and_skips_dependents(self):
        self.manifest([self.task('one','raise SystemExit(1)'),self.task('two','raise SystemExit(2)'),self.task('child','print("unexpected")',needs=['one'])]);self.commit();self.install()
        p=self.push();output=p.stdout+p.stderr
        self.assertNotEqual(p.returncode,0)
        for expected in ['FAIL one','FAIL two','SKIP child']:self.assertIn(expected,output)
    def test_existing_hooks_preserved_with_arguments_and_stdin(self):
        old=self.repo/'custom hooks';old.mkdir()
        script=old/'pre-push';script.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > "'+str(self.root/'args')+'"\ncat > "'+str(self.root/'stdin')+'"\n');script.chmod(0o755)
        other=old/'pre-commit';other.write_text('#!/bin/sh\nexit 0\n');other.chmod(0o755)
        self.git('config','core.hooksPath',str(old));self.install();self.install()
        self.assertEqual(self.push().returncode,0)
        self.assertEqual((self.root/'args').read_text().strip(),'origin')
        self.assertIn('refs/heads/check',(self.root/'stdin').read_text())
        self.assertEqual(script.read_text().count('cat'),1)
        hooks=Path(self.git('config','core.hooksPath').stdout.strip())
        self.assertTrue((hooks/'pre-commit').exists())
    def test_other_hook_keeps_original_script_directory(self):
        old=self.repo/'custom';old.mkdir()
        (self.repo/'outside-helper').write_text('exit 0\n')
        hook=old/'pre-commit';hook.write_text('#!/bin/sh\n. "$(dirname "$0")/../outside-helper"\n');hook.chmod(0o755)
        self.git('config','core.hooksPath',str(old));self.install()
        self.write('more.txt','change');self.commit()
    def test_fetched_base_available_in_snapshot(self):
        other=self.root/'other'
        self.run_cmd(['git','clone',str(self.remote),str(other)],self.root)
        (other/'remote-only.txt').write_text('new base')
        self.run_cmd(['git','add','.'],other);self.run_cmd(['git','commit','-qm','advance'],other)
        self.run_cmd(['git','push'],other)
        self.manifest([self.task('base','import os,subprocess; subprocess.run(["git","cat-file","-e",os.environ["BASE_SHA"]],check=True)')]);self.commit();self.install()
        self.assertEqual(self.push().returncode,0)
    def test_tilde_hook_path_keeps_original_blocking_hook(self):
        home=self.root/'home';hooks=home/'hooks';hooks.mkdir(parents=True)
        self.env['HOME']=str(home)
        hook=hooks/'pre-commit';hook.write_text('#!/bin/sh\nexit 42\n');hook.chmod(0o755)
        self.git('config','core.hooksPath','~/hooks')
        before=self.git('commit','--allow-empty','-m','blocked',check=False)
        self.assertNotEqual(before.returncode,0)
        self.install()
        after=self.git('commit','--allow-empty','-m','still blocked',check=False)
        self.assertNotEqual(after.returncode,0)
    def test_failing_existing_hook_still_blocks(self):
        old=self.repo/'.git/hooks/pre-push';old.write_text('#!/bin/sh\nexit 42\n');old.chmod(0o755)
        self.install();self.assertNotEqual(self.push().returncode,0)
    def test_non_head_and_multiple_refs_validate_every_commit(self):
        self.git('branch','good');self.write('content.txt','bad');self.commit();self.git('branch','bad');self.git('checkout','good');self.install()
        self.assertNotEqual(self.push('good:refs/heads/good','bad:refs/heads/bad').returncode,0)
        self.assertEqual(self.git('ls-remote','origin','refs/heads/good').stdout,'')
    def test_deletion_does_not_run_checks(self):
        self.install();self.assertEqual(self.push().returncode,0)
        self.manifest([self.task('failure','raise SystemExit(1)')]);self.commit()
        self.assertEqual(self.push(':refs/heads/check').returncode,0)
    def test_changed_commit_cannot_reuse_success(self):
        self.install();self.assertEqual(self.push().returncode,0)
        self.write('content.txt','bad');self.commit();self.assertNotEqual(self.push().returncode,0)
    def test_missing_manifest_blocks(self):
        self.install();(self.repo/'.preflight/manifest.json').unlink();self.git('add','-u');self.git('commit','-qm','remove')
        self.assertNotEqual(self.push().returncode,0)
    def test_unknown_dependencies_fail_before_commands(self):
        self.manifest([self.task('broken','print("unexpected")',needs=['missing'])]);self.commit();self.install()
        p=self.push();self.assertNotEqual(p.returncode,0);self.assertIn('unknown dependency',p.stdout+p.stderr)
    def test_checks_cannot_silently_rewrite_committed_source(self):
        self.manifest([self.task('rewrite',"from pathlib import Path; Path('content.txt').write_text('changed')")]);self.commit();self.install()
        self.assertNotEqual(self.push().returncode,0)
        self.assertEqual((self.repo/'content.txt').read_text(),'good')
    def test_exported_credentials_are_not_passed_to_checks(self):
        self.env['PREFLIGHT_TEST_SECRET']='do-not-forward'
        self.manifest([self.task('environment','import os; assert "PREFLIGHT_TEST_SECRET" not in os.environ')]);self.commit();self.install()
        self.assertEqual(self.push().returncode,0)
    def test_basic_user_context_survives_environment_filter(self):
        self.env['USER']='preflight-fixture'
        self.manifest([self.task('user','import os; assert os.environ.get("USER")=="preflight-fixture"')]);self.commit();self.install()
        self.assertEqual(self.push().returncode,0)
    def test_timeout_is_failure(self):
        self.manifest([self.task('slow','import time; time.sleep(5)',timeout=1)]);self.commit();self.install()
        p=self.push();self.assertNotEqual(p.returncode,0);self.assertIn('FAIL slow',p.stdout+p.stderr)



    def test_symlinked_hook_keeps_invocation_name(self):
        hook=self.repo/'.git/hooks/pre-commit'
        target=self.root/'dispatcher'
        target.write_text('#!/bin/sh\n[ "$(basename "$0")" != pre-commit ]\n');target.chmod(0o755)
        hook.symlink_to(target)
        self.assertNotEqual(self.git('commit','--allow-empty','-m','before',check=False).returncode,0)
        self.run_cmd([sys.executable,'.preflight/install.py'])
        self.assertNotEqual(self.git('commit','--allow-empty','-m','after',check=False).returncode,0)

    def test_linked_worktree_push_preserves_branch(self):
        wt=self.root/'linked'
        self.git('worktree','add','-b','feature',str(wt))
        self.run_cmd([sys.executable,'.preflight/install.py'],cwd=wt)
        result=self.run_cmd(['git','push','origin','feature'],cwd=wt,check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.run_cmd(['git','symbolic-ref','--short','HEAD'],cwd=wt).stdout.strip(),'feature')

    def test_nonhead_push_from_checkout_without_manifest(self):
        self.run_cmd([sys.executable,'.preflight/install.py'])
        self.git('branch','ready')
        self.git('checkout','--detach','HEAD^')
        result=self.git('push','origin','ready',check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_source_fetch_preserves_command_url_configuration(self):
        self.run_cmd([sys.executable,'.preflight/install.py'])
        self.git('remote','set-url','origin','fixture://remote')
        result=self.git('-c','url.'+str(self.remote)+'.insteadOf=fixture://remote','push','origin','HEAD:feature',check=False)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_install_from_subdirectory_uses_repo_git_directory(self):
        self.run_cmd([sys.executable, 'install.py'], self.repo/'.preflight')
        self.assertEqual(Path(self.git('config','core.hooksPath').stdout.strip()), (self.repo/'.git/aulron-preflight-hooks').resolve())
        self.assertEqual(self.push().returncode,0)

    def test_removed_original_hook_no_longer_blocks_commit(self):
        hook=self.repo/'.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 42\n');hook.chmod(0o755)
        self.install();hook.unlink()
        self.git('commit','--allow-empty','-m','removed hook')
        self.install()
        self.git('commit','--allow-empty','-m','reinstalled')

    def test_upgrade_skips_removed_hook_and_preserves_unrelated_dispatcher_file(self):
        import shlex
        original=self.repo/'.git/hooks/pre-commit'
        original.write_text('#!/bin/sh\nexit 42\n');original.chmod(0o755)
        self.install()
        hooks=Path(self.git('config','core.hooksPath').stdout.strip())
        (hooks/'pre-commit').write_text('#!/bin/sh\nexec '+shlex.quote(str(original.resolve()))+' "$@"\n')
        (hooks/'personal.txt').write_text('keep me')
        original.unlink();self.install()
        self.git('commit','--allow-empty','-m','upgraded removed hook')
        self.assertEqual((hooks/'personal.txt').read_text(),'keep me')

    def test_disabled_original_hook_no_longer_blocks_commit(self):
        hook=self.repo/'.git/hooks/pre-commit'
        hook.write_text('#!/bin/sh\nexit 42\n');hook.chmod(0o755)
        self.install();hook.chmod(0o644)
        self.git('commit','--allow-empty','-m','disabled hook')

    def test_relative_hooks_refused_without_weakening_linked_worktree(self):
        linked=self.root/'linked'
        self.git('worktree','add','-b','linked',str(linked))
        for root,code in [(self.repo,0),(linked,42)]:
            hooks=root/'.hooks';hooks.mkdir()
            hook=hooks/'pre-commit';hook.write_text('#!/bin/sh\nexit '+str(code)+'\n');hook.chmod(0o755)
        self.git('config','core.hooksPath','.hooks')
        result=self.run_cmd([sys.executable,'.preflight/install.py'],check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('relative hooksPath',result.stdout+result.stderr)
        self.assertEqual(self.git('config','core.hooksPath').stdout.strip(),'.hooks')
        self.assertNotEqual(self.run_cmd(['git','commit','--allow-empty','-m','blocked'],linked,check=False).returncode,0)

    def profile(self, tasks, selected):
        self.manifest(tasks)
        p=self.repo/'.preflight/manifest.json'
        data=json.loads(p.read_text());data['push_tasks']=selected
        p.write_text(json.dumps(data));self.commit();self.install()

    def test_push_uses_fast_tasks_but_ci_keeps_application_failure(self):
        self.profile([self.task('fast','pass'),self.task('app','raise SystemExit(7)')],['fast'])
        self.assertEqual(self.push().returncode,0)
        ci=self.run_cmd([sys.executable,'.preflight/preflight.py'],check=False)
        self.assertNotEqual(ci.returncode,0)
        self.assertIn('FAIL app',ci.stdout)

    def test_fast_failure_still_blocks_push(self):
        self.profile([self.task('fast','raise SystemExit(1)'),self.task('app','pass')],['fast'])
        self.assertNotEqual(self.push().returncode,0)

    def test_invalid_push_profile_blocks(self):
        for selected in [[],['missing'],['fast','fast'],'fast']:
            with self.subTest(selected=selected):
                self.profile([self.task('fast','pass')],selected)
                self.assertNotEqual(self.push().returncode,0)

    def test_omitted_dependency_blocks_instead_of_hanging(self):
        self.profile([self.task('setup','pass'),self.task('fast','pass',needs=['setup'])],['fast'])
        self.assertNotEqual(self.push().returncode,0)

    def test_valid_dependency_closed_profile(self):
        self.profile([self.task('setup','pass'),self.task('fast','pass',needs=['setup']),self.task('app','raise SystemExit(7)')],['setup','fast'])
        self.assertEqual(self.push().returncode,0)
