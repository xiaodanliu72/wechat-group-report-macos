import argparse,json,os,sys,datetime
from pathlib import Path
from .core import window,export_files,render

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='本地微信导出与 Codex 总结交付工具')
    sub=p.add_subparsers(dest='cmd',required=True)
    e=sub.add_parser('export',help='读取本人账号；stdin 接收临时 salt→key JSON，不保存密钥')
    e.add_argument('--db-root',required=True);e.add_argument('--group');e.add_argument('--group-id');e.add_argument('--hours',type=int,choices=[24,48,72],default=24);e.add_argument('--start');e.add_argument('--end');e.add_argument('--out',required=True)
    k=sub.add_parser('export-keychain',help='复用本机专用钥匙串密钥；不启动微信，不自动重新登录')
    k.add_argument('--db-root',type=Path);k.add_argument('--group');k.add_argument('--group-id');k.add_argument('--hours',type=int,choices=[24,48,72],default=24);k.add_argument('--start');k.add_argument('--end');k.add_argument('--out',required=True)
    s=sub.add_parser('keychain',help='仅管理本项目当前账号的钥匙串项')
    s.add_argument('action',choices=['status','forget']);s.add_argument('--db-root',type=Path)
    r=sub.add_parser('render',help='核验由当前 Codex 会话编写的总结，并生成离线网页/PNG')
    r.add_argument('directory');r.add_argument('--report',required=True)
    doctor=sub.add_parser('doctor');doctor.add_argument('--app',type=Path)
    a=p.parse_args()
    if a.cmd=='doctor':
        import platform,plistlib,shutil
        from .preparation import find_app
        app=find_app(a.app)/'Contents/Info.plist'
        info=plistlib.loads(app.read_bytes())
        root=Path.home()/'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
        try:accounts=[x.name for x in root.iterdir() if (x/'db_storage').is_dir()];access=True
        except OSError:accounts=[];access=False
        print(json.dumps({'platform':platform.platform(),'wechat':info.get('CFBundleShortVersionString'),'data_access':access,'account_directories':accounts,'wxgf_decoder_available':bool(shutil.which('ffmpeg')),'note':'doctor 仅检查本机环境；不证明取钥、读取或总结成功'},ensure_ascii=False,indent=2));return 0 if access else 2
    if a.cmd=='render':render(a.directory,json.loads(Path(a.report).read_text()));return 0
    if a.cmd in ('export-keychain','keychain'):
        from .preparation import select_account
        from . import keychain_store
        root,_=select_account(Path.home()/'Library/Containers/com.tencent.xinWeChat/Data',a.db_root)
        if a.cmd=='keychain':
            if a.action=='forget':keychain_store.delete(root);print('{"project_keychain_item_deleted":true}')
            else:print(json.dumps(keychain_store.status(root)))
            return 0
        if Path(a.out).exists():raise ValueError('输出目录已存在')
        name=a.group or input('请输入完整群名：').strip()
        if not name:raise ValueError('群名不能为空')
        start,end=window(a.hours,a.start,a.end)
        data=keychain_store.export(root,name,a.group_id,start,end,a.out)
        print(json.dumps({'ok':True,'message_count':data['metadata']['message_count'],'output':a.out,'client_launch_performed':False,'summary_pending':True},ensure_ascii=False))
        return 0
    name=a.group or input('请输入完整群名：').strip()
    if not name:raise ValueError('群名不能为空')
    start,end=window(a.hours,a.start,a.end)
    from .reader import read
    keys=json.load(sys.stdin)
    try:data=read(a.db_root,keys,name,a.group_id,start,end)
    finally:keys.clear()
    export_files(a.out,data,account=Path(a.db_root).parent)
    print(json.dumps({'ok':True,'message_count':data['metadata']['message_count'],'output':a.out,'next':'由 Codex 读完全部 messages.json，编写带引用 report.json，再执行 render'},ensure_ascii=False))
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError,RuntimeError,OSError) as ex:
        print(json.dumps({'ok':False,'error':str(ex)},ensure_ascii=False),file=sys.stderr);raise SystemExit(2)
