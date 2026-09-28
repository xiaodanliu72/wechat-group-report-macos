"""macOS containment for a disposable client; no original account access in child."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

BUNDLE='com.tencent.xinWeChat'

def profile(home=None, allow_network=False):
    home=Path(home or Path.home()).resolve()
    original=home/'Library/Containers'/BUNDLE
    group=home/'Library/Group Containers/5A4RE8SF68.com.tencent.xinWeChat'
    # Deny all writes to real home (including resolved symlinks), all reads of the
    # original WeChat containers, and network access. No permissive fallback.
    return '(version 1)(allow default)'+('' if allow_network else '(deny network*)')+''.join(
        '(deny '+op+' (subpath '+json.dumps(str(path))+'))'
        for op,path in [('file-write*',home),('file-read*',original),('file-read*',group)])

def run_contained(argv,isolated_home,*,extra_env=None,timeout=30):
    isolated_home=Path(isolated_home).resolve()
    if isolated_home.is_relative_to(Path.home().resolve()):
        raise ValueError('隔离目录必须在真实用户目录之外，以便禁止整个真实用户目录写入')
    env=dict(os.environ,CFFIXED_USER_HOME=str(isolated_home))
    if extra_env:env.update(extra_env)
    return subprocess.run(['/usr/bin/sandbox-exec','-p',profile(),*argv],env=env,
                          capture_output=True,text=True,timeout=timeout)

def self_test():
    """Synthetic controls: child allowed inside capsule, denied outside and TCP."""
    with tempfile.TemporaryDirectory(prefix='wechat-containment-') as tmp:
        capsule=Path(tmp)
        # The denied sentinel is synthetic and never touches an actual WeChat file.
        with tempfile.TemporaryDirectory(prefix='wechat-sentinel-',dir=Path.home()/'Library/Caches') as sentinel:
            marker=Path(sentinel)/'marker.txt';marker.write_text('unchanged')
            script='''import json,socket,sys
from pathlib import Path
p=Path(sys.argv[1]);q=Path(sys.argv[2]);p.write_text('isolated')
try:q.write_text('must not write');denied=False
except PermissionError:denied=True
s=socket.socket()
try:s.connect(('127.0.0.1',9));network_denied=False
except PermissionError:network_denied=True
except OSError:network_denied=False
print(json.dumps({'isolated_write':p.read_text()=='isolated','real_home_write_denied':denied,'network_denied':network_denied}))
'''
            r=run_contained(['/usr/bin/python3','-c',script,str(capsule/'allowed'),str(marker)],capsule)
            if r.returncode:raise RuntimeError('隔离自检进程失败')
            result=json.loads(r.stdout)
            if marker.read_text()!='unchanged' or not all(result.values()):raise RuntimeError('隔离自检未全部通过，不允许启动真实数据流程')
            return result

if __name__=='__main__':print(json.dumps(self_test(),ensure_ascii=False))
