#!/usr/bin/env python3
"""Use the verified project runtime from any working directory."""
import argparse
import json
import os
import sys
from pathlib import Path

def find_project(explicit=None):
    if explicit is not None:
        return explicit.expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / 'wechat_local/skill_workflow.py').is_file():
            return parent
    config = Path(__file__).resolve().parents[1] / 'runtime.local.json'
    if config.is_file():
        value = json.loads(config.read_text())
        return Path(value['project']).expanduser().resolve()
    raise ValueError('未找到项目；使用 --project 指定目录，或运行项目的 scripts/install_skill.py。')

def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--project', type=Path)
    args, remaining = parser.parse_known_args()
    try:
        project = find_project(args.project)
    except (ValueError, KeyError, OSError) as error:
        sys.exit(str(error))
    python = project / '.venv/bin/python'
    if not python.is_file() or not (project / 'wechat_local/skill_workflow.py').is_file():
        sys.exit('未找到已部署的微信读取项目或虚拟环境；使用 --project 指定完整项目目录。')
    # Resolve user-supplied relative paths before changing the runtime directory.
    os.environ['WECHAT_REPORT_CALLER_CWD'] = str(Path.cwd())
    os.chdir(project)
    os.execv(str(python), [str(python), '-m', 'wechat_local.skill_workflow', *remaining])

if __name__ == '__main__':
    main()
