import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
script=PROJECT/'scripts/install_skill.py'
if not script.exists():script=PROJECT/'release_templates/scripts/install_skill.py'
spec=importlib.util.spec_from_file_location('fixture_installer',script)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class SkillInstallTests(unittest.TestCase):
    def test_installed_pointer_and_explicit_project_from_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();project=root/'虚构项目';dest=root/'installed'
            (project/'wechat_local').mkdir(parents=True)
            (project/'wechat_local/skill_workflow.py').write_text('import json,os;print(json.dumps({"cwd":os.getcwd(),"caller":os.environ["WECHAT_REPORT_CALLER_CWD"]}))')
            python=project/'.venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
            wrapper=PROJECT/'scripts/run.py'
            if not wrapper.exists():wrapper=PROJECT/'skills/wechat-group-report/scripts/run.py'
            for name in installer.FILES:
                path=project/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(wrapper.read_text() if name=='scripts/run.py' else 'synthetic')
            installer.install(project,dest)
            result=subprocess.run([sys.executable,str(dest/'scripts/run.py'),'export'],cwd=root,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout),{'cwd':str(project),'caller':str(root)})
            pointer=dest/'runtime.local.json';self.assertEqual(pointer.stat().st_mode&0o777,0o600)
            pointer.write_text(json.dumps({'project':'/synthetic/nonexistent'}))
            result=subprocess.run([sys.executable,str(dest/'scripts/run.py'),'--project',str(project),'export'],cwd=root,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout)['cwd'],str(project))
            with self.assertRaisesRegex(ValueError,'未覆盖'):installer.install(project,dest)
