"""Mac acquisition with a disposable application, separate data capsule and OS containment."""
import argparse,json,os,platform,shutil,subprocess as sp,tempfile,threading,signal
from pathlib import Path
from .core import window,export_files
from .reader import snapshot,fingerprint,read
from .isolation import profile,self_test,BUNDLE
from .preparation import debug_copy,select_account,find_app

def copy_stable(source,target):
    if not source.is_dir():return
    entries=list(source.rglob('*'))
    if source.name=='Preferences':
        entries=[p for p in entries if p.name==BUNDLE+'.plist']
    if any(p.is_symlink() for p in entries):raise RuntimeError('账号配置含符号链接，停止复制')
    files=[p for p in entries if p.is_file() and not p.name.endswith('-shm')]
    if sum(p.stat().st_size for p in files)>16*1024*1024:raise RuntimeError('启动配置超过限定范围，需检查')
    for _ in range(3):
        before={p:fingerprint(p) for p in files}
        for p in files:
            q=target/p.relative_to(source);q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
        if before=={p:fingerprint(p) for p in files} and all(fingerprint(target/p.relative_to(source))==v for p,v in before.items()):return
    raise RuntimeError('启动配置持续变化，不能建立稳定副本')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db-root',type=Path);ap.add_argument('--group');ap.add_argument('--group-id');ap.add_argument('--login-account');ap.add_argument('--app',type=Path)
    ap.add_argument('--allow-login-network',action='store_true');ap.add_argument('--out',required=True);ap.add_argument('--hours',type=int,choices=[24,48,72],default=24);ap.add_argument('--start');ap.add_argument('--end');ap.add_argument('--timeout',type=int,default=120)
    ap.add_argument('--accept-session-disruption',action='store_true',help='明确接受临时客户端登录可能顶掉原微信；不是无打扰读取')
    ap.add_argument('--save-keychain',action='store_true',help='将已验证的必要库密钥保存到本机专用钥匙串项，供日常复用')
    a=ap.parse_args();os.umask(0o077)
    if a.allow_login_network and not a.accept_session_disruption:
        raise RuntimeError('已拦截：临时客户端登录已被用户报告会顶掉原微信。尚无本机验证的无打扰取钥方案；仅明确接受掉线时才加 --accept-session-disruption。未启动微信、未读取账号。')
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise RuntimeError('首次初始化仅实现原生 Apple Silicon macOS；不支持 Intel、Rosetta 或其他系统')
    original=find_app(a.app)
    def stop(signum,frame):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    original_data=Path.home()/'Library/Containers'/BUNDLE/'Data'
    root,a.login_account=select_account(original_data,a.db_root,a.login_account);account=root.parent.name
    if a.save_keychain:
        from . import keychain_store
        try:keychain_store.status(root)
        except keychain_store.KeyNotFound:pass
        else:raise RuntimeError('本账号已有钥匙串密钥；请用 export-keychain 验证读取，不重复初始化')
    a.group=a.group or input('请输入完整群名：').strip()
    if not a.group:raise RuntimeError('群名不能为空')
    if Path(a.out).exists():raise RuntimeError('输出目录已存在')
    print(json.dumps({'containment_self_test':self_test()}),flush=True)
    start,end=window(a.hours,a.start,a.end)
    signature=sp.check_output(['codesign','-dvv',str(original)],stderr=sp.STDOUT)
    keys={};proc=None
    with debug_copy(original) as app,snapshot(root) as frozen,tempfile.TemporaryDirectory(prefix='wechat-data-capsule-') as tmp:
        capsule=Path(tmp);data=capsule/'Library/Containers'/BUNDLE/'Data'
        for rel in ['Documents/app_data/config','Documents/app_data/login','Documents/app_data/'+a.login_account,'Documents/xwechat_files/all_users/config','Documents/xwechat_files/all_users/login/'+a.login_account,'Documents/xwechat_files/'+account+'/config','Library/Preferences']:
            copy_stable(original_data/rel,data/rel)
        target=data/'Documents/xwechat_files'/account/'db_storage'
        shutil.copytree(frozen,target)
        rfd,wfd=os.pipe();errors=[]
        def receive():
            try:
                with os.fdopen(rfd) as stream:keys.update(json.load(stream))
            except Exception:errors.append('没有收到完整临时密钥')
        thread=threading.Thread(target=receive,daemon=True);thread.start()
        env=dict(os.environ,PYTHONPATH=sp.check_output(['lldb','-P'],text=True).strip())
        args=['/usr/bin/sandbox-exec','-p',profile(allow_network=a.allow_login_network),'/usr/bin/python3',str(Path(__file__).parent/'vendor/capture_ephemeral.py'),'--exe',str(app/'Contents/MacOS/WeChat'),'--db-root',str(frozen),'--isolated-home',str(capsule),'--key-fd',str(wfd),'--timeout',str(a.timeout)]
        print(json.dumps({'stage':'isolated_launch','start':start.isoformat(),'end':end.isoformat(),'network':'normal_login_allowed' if a.allow_login_network else 'denied','original_container_read_write':'denied','capsule':str(capsule)},ensure_ascii=False),flush=True)
        try:
            try:proc=sp.Popen(args,env=env,pass_fds=(wfd,))
            finally:os.close(wfd)
            rc=proc.wait(timeout=a.timeout+45);thread.join(timeout=5)
            if rc or errors or thread.is_alive():raise RuntimeError('隔离取钥尚未成功；未导出或总结真实记录')
            result=read(frozen,keys,a.group,a.group_id,start,end)
            if a.save_keychain:
                keychain_store.verify_keys(frozen,keys)
                keychain_store.save(root,keys)
                reread=keychain_store.load(root)
                try:
                    if reread!=keys or keychain_store.status(root)['synchronizable']:
                        raise RuntimeError('钥匙串保存后回读或非同步属性验证失败')
                finally:reread.clear()
                print('{"keychain_saved_and_verified":true,"synchronizable":false}',flush=True)
            result['metadata']['account_directory']=account
            result['metadata']['acquisition']='隔离客户端；原账号目录被沙箱禁止读写；密钥通过冻结快照首页校验；匿名管道传递'
            export_files(a.out,result)
            print(json.dumps({'ok':True,'output':a.out,'message_count':result['metadata']['message_count'],'summary_pending':True},ensure_ascii=False),flush=True)
        finally:
            if proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT);proc.wait(timeout=15)
            keys.clear()
            if sp.check_output(['codesign','-dvv',str(original)],stderr=sp.STDOUT)!=signature:raise RuntimeError('原版签名发生变化')
    print('{"temporary_capsule_cleaned":true,"temporary_app_cleaned":true}',flush=True)

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError,sp.SubprocessError) as e:
        print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),flush=True);raise SystemExit(2)
