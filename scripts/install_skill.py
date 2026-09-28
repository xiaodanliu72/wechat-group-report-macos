#!/usr/bin/env python3
"""Install only the skill instructions and a local pointer to this checkout."""
import argparse
import json
import os
import shutil
from pathlib import Path

FILES=('SKILL.md','agents/openai.yaml','references/report-schema.md','scripts/run.py')

def install(project,destination):
    project=Path(project).resolve();destination=Path(destination).expanduser().absolute()
    if not (project/'wechat_local/skill_workflow.py').is_file():
        raise ValueError('请从完整项目运行安装脚本')
    if destination.exists() or destination.is_symlink():
        raise ValueError('目标 Skill 已存在，未覆盖；请使用 --destination 指定另一目录，或人工处理旧版本')
    if any(not (project/name).is_file() for name in FILES):raise ValueError('Skill 文件不完整')
    destination.mkdir(parents=True,mode=0o700)
    try:
        for name in FILES:
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(project/name,target)
        pointer=destination/'runtime.local.json'
        pointer.write_text(json.dumps({'project':str(project)},ensure_ascii=False,indent=2))
        pointer.chmod(0o600)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,default=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'skills/wechat-group-report')
    args=parser.parse_args();os.umask(0o077)
    try:
        dest=install(Path(__file__).resolve().parents[1],args.destination)
        print(json.dumps({'ok':True,'skill':str(dest),'runtime_auto_discovery':'请在新会话确认；也可直接指定 SKILL.md 路径'},ensure_ascii=False))
    except (ValueError,OSError) as error:
        parser.exit(2,str(error)+'\n')

if __name__=='__main__':main()
