"""Prepare a disposable debug copy; never sign or modify the installed client."""
import argparse
import contextlib
import json
import plistlib
import subprocess as sp
import tempfile
from pathlib import Path

def find_app(explicit=None):
    candidates=[Path(explicit).expanduser()] if explicit else [base/name for base in (Path('/Applications'),Path.home()/'Applications') for name in ('微信.app','WeChat.app')]
    matches=[]
    for app in candidates:
        info=app/'Contents/Info.plist'
        if not info.is_file():continue
        if plistlib.loads(info.read_bytes()).get('CFBundleIdentifier')=='com.tencent.xinWeChat':
            matches.append(app.resolve())
    matches=list(dict.fromkeys(matches))
    if len(matches)!=1:raise RuntimeError('微信应用未唯一匹配；请用 --app 指定本人安装的微信 .app 路径')
    return matches[0]


@contextlib.contextmanager
def debug_copy(original):
    original=Path(original).resolve(strict=True)
    info=plistlib.loads((original/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier')!='com.tencent.xinWeChat':
        raise RuntimeError('应用不是已核实的微信客户端')
    sp.run(['codesign','--verify','--deep','--strict',str(original)],check=True,capture_output=True)
    signature=sp.check_output(['codesign','-dvv',str(original)],stderr=sp.STDOUT)
    with tempfile.TemporaryDirectory(prefix='wechat-debug-copy-') as tmp:
        shadow=Path(tmp)/'WeChat-local.app'
        try:
            # Keep away from Documents/FileProvider and omit metadata that blocks signing.
            sp.run(['/usr/bin/ditto','--noextattr','--noqtn',str(original),str(shadow)],check=True,capture_output=True)
            sp.run(['codesign','--force','--deep','--sign','-',str(shadow)],check=True,capture_output=True)
            sp.run(['codesign','--verify','--deep','--strict',str(shadow)],check=True,capture_output=True)
            yield shadow
        finally:
            if sp.check_output(['codesign','-dvv',str(original)],stderr=sp.STDOUT)!=signature:
                raise RuntimeError('原版签名发生变化')


def select_account(data, db_root=None, login_account=None):
    data=Path(data).resolve(strict=True)
    base=data/'Documents/xwechat_files'
    candidates=sorted(p/'db_storage' for p in base.iterdir() if (p/'db_storage').is_dir())
    if db_root is None:
        if len(candidates)!=1:
            raise RuntimeError('账号目录不唯一，请用 --db-root 指定本次账号：'+', '.join(str(p) for p in candidates))
        root=candidates[0]
    else:root=Path(db_root).resolve(strict=True)
    if root not in candidates:raise RuntimeError('仅支持当前用户原微信目录中的单一账号')
    logins=data/'Documents/xwechat_files/all_users/login'
    names=sorted(p.name for p in logins.iterdir() if p.is_dir() and root.parent.name.startswith(p.name+'_'))
    if login_account is None:
        if len(names)!=1:raise RuntimeError('无法唯一关联登录配置，请用 --login-account 指定并人工核对')
        login_account=names[0]
    if login_account not in names:raise RuntimeError('登录配置与所选账号目录不匹配')
    return root,login_account


if __name__=='__main__':
    p=argparse.ArgumentParser(description='只验证临时应用复制、签名及清理，不启动微信或读取聊天')
    p.add_argument('--app',type=Path)
    args=p.parse_args()
    with debug_copy(find_app(args.app)) as app:
        copy_path=app
        print(json.dumps({'copy_signature_verified':True,'client_started':False}))
    print(json.dumps({'temporary_app_cleaned':not copy_path.exists()}))
