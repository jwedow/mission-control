import os, pathlib, subprocess, sys, tempfile, unittest
SOURCE=pathlib.Path(__file__).resolve().parents[1]
class SyntaxTest(unittest.TestCase):
 def run_check(self,name,body,exclude=False):
  with tempfile.TemporaryDirectory() as tmp:
   p=pathlib.Path(tmp); subprocess.run(['git','init','-q',tmp],check=True)
   (p/name).write_text(body);
   if exclude:
    (p/'.preflight').mkdir();(p/'.preflight/manifest.json').write_text('{"syntax_exclude":["negative.sh"]}')
   subprocess.run(['git','add',name],cwd=p,check=True)
   env=dict(os.environ);env.pop('BASE_SHA',None)
   return subprocess.run([sys.executable,str(SOURCE/'checks.py')],cwd=p,env=env,capture_output=True,text=True)
 def test_supported_jsonc_configs(self):
  for name in ['turbo.json','tsconfig.test.json','jsconfig.json']:
   with self.subTest(name=name):
    result=self.run_check(name,'{ /* config */ "url":"https://example.com/a//b", // note\n"items":[1,],}')
    self.assertEqual(result.returncode,0,result.stdout)
 def test_invalid_jsonc_is_rejected(self):
  self.assertNotEqual(self.run_check('tsconfig.json','{ /* note */ "x": nope }').returncode,0)
 def test_package_json_stays_strict(self):
  self.assertNotEqual(self.run_check('package.json','{ /* not allowed */ "name":"a" }').returncode,0)

 def test_explicit_negative_fixture_exclusion(self):
  self.assertNotEqual(self.run_check('negative.sh','if broken').returncode,0)
  self.assertEqual(self.run_check('negative.sh','if broken',exclude=True).returncode,0)
