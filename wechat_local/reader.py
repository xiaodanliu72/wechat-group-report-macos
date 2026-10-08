"""SQLCipher 4 reader: encrypted private snapshots; ephemeral keys from stdin only."""
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from .vendor.sqlcipher_probe import DB
from .core import package

def literal(s): return "CAST(x'"+s.encode().hex()+"' AS TEXT)"
def fingerprint(p):
    if not p.exists(): return None
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return (p.stat().st_size,h.hexdigest())

@contextmanager
def snapshot(root):
    root=Path(root)
    paths=[root/'contact/contact.db']+sorted((root/'message').glob('message_[0-9]*.db'))
    if len(paths)<2: raise RuntimeError('未找到联系人库和消息分片；先检查目录访问权限')
    # Hash all selected DB/WAL before and after the entire copy. Reject changing files.
    with tempfile.TemporaryDirectory(prefix='wechat-local-') as tmp:
        dest=Path(tmp);os.chmod(dest,0o700)
        for attempt in range(3):
            files=[Path(str(p)+suffix) for p in paths for suffix in ('','-wal')]
            before={p:fingerprint(p) for p in files}
            for p in files:
                target=dest/p.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True)
                if before[p] is not None: shutil.copyfile(p,target);os.chmod(target,0o600)
                elif target.exists():target.unlink()
            after={p:fingerprint(p) for p in files}
            if before==after and all(fingerprint(dest/p.relative_to(root))==v for p,v in before.items()):
                yield dest;return
        raise RuntimeError('数据库持续变化，无法获得一致副本；请稍后重试')

def read(root,keys,name,gid,start,end):
    def open_db(p):
        with p.open('rb') as f: salt=f.read(16).hex()
        key=keys.get(salt)
        if not isinstance(key,str) or not re.fullmatch('[0-9a-fA-F]{64}',key): raise RuntimeError('缺少匹配数据库盐值的临时密钥')
        return DB(p,key)
    with snapshot(root) as snap:
        with open_db(snap/'contact/contact.db') as db:
            candidates=db.query('SELECT username,nick_name,remark FROM contact WHERE nick_name='+literal(name))
            candidates=[r for r in candidates if r['username'].endswith('@chatroom')]
            if gid:candidates=[r for r in candidates if r['username']==gid]
            if len(candidates)!=1: raise RuntimeError('群名未唯一匹配，请核对 group_id：'+json.dumps(candidates,ensure_ascii=False))
            group=candidates[0];gid=group['username'];names={}
            # Only selected-group participant IDs are resolved after reading their messages.
        table='Msg_'+hashlib.md5(gid.encode()).hexdigest(); rows=[];shards=[]
        for p in sorted((snap/'message').glob('message_[0-9]*.db')):
            with open_db(p) as db:
                if db.query('PRAGMA integrity_check;') != [{'integrity_check':'ok'}]: raise RuntimeError('数据库完整性校验失败')
                if not db.query("SELECT name FROM sqlite_master WHERE type='table' AND name="+literal(table)):continue
                columns={x['name'] for x in db.query(f'PRAGMA table_info("{table}")')}
                required={'local_id','server_id','local_type','create_time','real_sender_id','message_content'}
                if not required<=columns: raise RuntimeError('本机消息结构与适配器不兼容')
                packed=',hex(CASE WHEN (local_type & 4294967295)=3 THEN packed_info_data END) packed' if 'packed_info_data' in columns else ''
                db.query('BEGIN;'); offset=0;count=0
                stamp='(CASE WHEN create_time>=946684800000 AND create_time<4102444800000 THEN create_time/1000.0 ELSE create_time END)'
                bounds=db.query(f'SELECT min(create_time) lo,max(create_time) hi FROM "{table}"')[0]
                from .core import timestamp
                for value in bounds.values():
                    if value is not None: timestamp(value)
                where=f'{stamp}>={start.timestamp()} AND {stamp}<{end.timestamp()}'
                expected=int(db.query(f'SELECT count(*) n FROM "{table}" WHERE {where}')[0]['n'])
                while True:
                    batch=db.query(f'SELECT local_id,server_id,local_type,create_time,real_sender_id,hex(message_content) body{packed} FROM "{table}" WHERE {where} ORDER BY {stamp},local_id LIMIT 500 OFFSET {offset}')
                    if not batch:break
                    for r in batch:
                        sender=db.query('SELECT user_name FROM Name2Id WHERE rowid='+str(int(r['real_sender_id'])))
                        r.update(sender_id=sender[0]['user_name'] if len(sender)==1 else None,database=p.name,body=bytes.fromhex(r['body']))
                        rows.append(r)
                    count+=len(batch);offset+=len(batch)
                if count != expected: raise RuntimeError('分页读取数量与同一快照 SQL 统计不一致')
                db.query('COMMIT;');shards.append({'database':p.name,'selected_rows':count,'sql_window_count':expected,'wal_present':Path(str(p)+'-wal').exists(),'earliest_local_timestamp':timestamp(bounds['lo']) if bounds['lo'] else None,'latest_local_timestamp':timestamp(bounds['hi']) if bounds['hi'] else None})
        if not shards:raise RuntimeError('该群没有本地消息表，不能将其解释为时间窗口内零消息')
        with open_db(snap/'contact/contact.db') as db:
            for sender in {r['sender_id'] for r in rows if r['sender_id']}:
                matches=db.query('SELECT nick_name,remark FROM contact WHERE username='+literal(sender))
                if len(matches)==1:names[sender]=matches[0]['remark'] or matches[0]['nick_name'] or sender
        for r in rows:r['sender']=names.get(r['sender_id'],r['sender_id'])
        return package(rows,name,gid,start,end,extra={'shards':shards,'snapshot':'稳定哈希校验的独立加密 DB + WAL 副本；SQLCipher 处理已提交 WAL','sender_mapping':'联系人备注/昵称；未实现群专属昵称','account_directory':Path(root).parent.name})
