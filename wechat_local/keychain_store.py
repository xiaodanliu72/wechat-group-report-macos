"""Only this project's explicitly authorized keys, in the local login Keychain."""
import hashlib
import json
import os
import re
import subprocess as sp
from pathlib import Path

SOURCE=Path(__file__).with_name('keychain_helper.swift')
RUNTIME=Path.home()/'Library/Application Support/wechat-local-reader'

class KeyNotFound(RuntimeError):pass

def helper():
    RUNTIME.mkdir(parents=True,exist_ok=True,mode=0o700)
    executable=RUNTIME/'keychain-helper'
    digest=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    receipt=RUNTIME/'helper-source.sha256'
    if not executable.is_file() or not receipt.is_file() or receipt.read_text()!=digest:
        result=sp.run(['/usr/bin/xcrun','swiftc','-O',str(SOURCE),'-o',str(executable)],capture_output=True,text=True)
        if result.returncode:raise RuntimeError('钥匙串辅助程序编译失败；未读取密钥')
        sp.run(['/usr/bin/codesign','--force','--sign','-',str(executable)],capture_output=True,check=True)
        executable.chmod(0o700);receipt.write_text(digest);receipt.chmod(0o600)
    return executable

def identity(root):return hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()

def invoke(operation,root,payload=None):
    result=sp.run([str(helper()),operation,identity(root)],input=payload,capture_output=True,timeout=90)
    if result.returncode==4:raise KeyNotFound('本账号没有已保存的密钥；已停止，不会自动启动微信或重新登录')
    if result.returncode:raise RuntimeError('钥匙串操作失败：'+result.stderr.decode('utf8','replace').strip())
    return result.stdout

def validate(root,record):
    if not isinstance(record,dict) or record.get('schema')!=1 or record.get('account_root')!=str(Path(root).resolve()):
        raise RuntimeError('钥匙串记录的账号或版本不匹配')
    keys=record.get('keys')
    if not isinstance(keys,dict) or not keys or len(keys)>500:
        raise RuntimeError('钥匙串密钥集合无效')
    if any(not isinstance(s,str) or not re.fullmatch('[0-9a-f]{32}',s) or not isinstance(k,str) or not re.fullmatch('[0-9a-f]{64}',k) for s,k in keys.items()):
        raise RuntimeError('钥匙串密钥格式无效')
    return keys

def save(root,keys):
    record={'schema':1,'account_root':str(Path(root).resolve()),'keys':keys}
    validate(root,record)
    payload=json.dumps(record).encode()
    try:invoke('put',root,payload)
    finally:payload=None

def load(root):
    payload=invoke('get',root)
    try:return validate(root,json.loads(payload))
    except (ValueError,TypeError):raise RuntimeError('钥匙串数据无法解析') from None
    finally:payload=None

def status(root):return json.loads(invoke('status',root))
def delete(root):invoke('delete',root)

def verify_keys(root,keys):
    from .vendor.capture_ephemeral import verified
    from .vendor.sqlcipher_probe import DB
    paths=[Path(root)/'contact/contact.db']+sorted((Path(root)/'message').glob('message_[0-9]*.db'))
    checked=[]
    for p in paths:
        with p.open('rb') as f:page=f.read(4096)
        key=keys.get(page[:16].hex())
        if not key or not verified(bytes.fromhex(key),page):
            raise RuntimeError('密钥缺失或数据库校验失败：'+str(p.relative_to(root))+'；已停止，不会自动重新登录')
        with DB(p,key) as db:
            if db.query('PRAGMA integrity_check')!=[{'integrity_check':'ok'}]:
                raise RuntimeError('数据库完整性未通过：'+p.name)
        checked.append(str(p.relative_to(root)))
    return checked

def export(root,name,gid,start,end,out):
    # Deliberately has no dependency on application preparation or bootstrap.
    from .reader import snapshot,read
    from .core import export_files
    keys=load(root)
    try:
        with snapshot(root) as frozen:
            checked=verify_keys(frozen,keys)
            data=read(frozen,keys,name,gid,start,end)
            data['metadata'].update(account_directory=Path(root).parent.name,
                acquisition='本机专用钥匙串复用；验证后只读加密副本；未启动客户端或重新登录',verified_key_databases=checked)
            export_files(out,data,account=Path(root).parent)
            return data
    finally:keys.clear()
