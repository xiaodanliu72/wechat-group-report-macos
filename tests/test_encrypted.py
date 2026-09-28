import hashlib,os,tempfile,unittest
from pathlib import Path
from wechat_local.vendor.sqlcipher_probe import DB
from wechat_local.reader import read,literal
from wechat_local.core import window
class EncryptedTests(unittest.TestCase):
 def test_encrypted_shards_wal_and_exact_group(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);keys={};writers=[];gid='fixture@chatroom';table='Msg_'+hashlib.md5(gid.encode()).hexdigest();a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
   try:
    for rel in ('contact/contact.db','message/message_0.db','message/message_1.db'):
     path=root/rel;path.parent.mkdir(exist_ok=True);key=os.urandom(32).hex();db=DB(path,key,fixture_write=True);writers.append(db)
     db.query('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;')
     if rel.startswith('contact'):
      db.query('CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT);'+"INSERT INTO contact VALUES('fixture@chatroom','精确测试群',''),('u1','测试人','');")
     else:
      db.query(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'u1\');')
     db.query('PRAGMA wal_checkpoint(TRUNCATE);')
     with path.open('rb') as f:keys[f.read(16).hex()]=key
    before=hashlib.sha256((root/'message/message_0.db').read_bytes()).hexdigest()
    writers[1].query(f'INSERT INTO "{table}" VALUES(1,111,1,{int(a.timestamp())},1,{literal("u1:\n建议明天复核")});')
    writers[1].query(f'INSERT INTO "{table}" VALUES(2,222,1,{int(b.timestamp())},1,{literal("不应包含终点")});')
    # Duplicate same server message in another shard is removed, zero server ID stays distinct.
    writers[2].query(f'INSERT INTO "{table}" VALUES(3,111,1,{int(a.timestamp())},1,{literal("u1:\n建议明天复核")});')
    self.assertEqual(before,hashlib.sha256((root/'message/message_0.db').read_bytes()).hexdigest())
    data=read(root,keys,'精确测试群',None,a,b)
    self.assertEqual(data['metadata']['message_count'],1);self.assertEqual(data['metadata']['duplicates_removed'],1)
    self.assertEqual(data['messages'][0]['sender'],'测试人');self.assertEqual(data['messages'][0]['text'],'建议明天复核')
    self.assertEqual(before,hashlib.sha256((root/'message/message_0.db').read_bytes()).hexdigest())
    with self.assertRaises(RuntimeError):read(root,keys,'精确测试',None,a,b)
    writers[0].query("INSERT INTO contact VALUES('other@chatroom','精确测试群','');")
    with self.assertRaises(RuntimeError):read(root,keys,'精确测试群',None,a,b)
    self.assertEqual(read(root,keys,'精确测试群',gid,a,b)['metadata']['message_count'],1)
   finally:
    for db in writers:db.close()
if __name__=='__main__':unittest.main()

class LargeWindowTests(unittest.TestCase):
 def test_all_pages_milliseconds_and_uncommitted_wal(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);keys={};writers=[];gid='large@chatroom';table='Msg_'+hashlib.md5(gid.encode()).hexdigest();a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
   try:
    for rel in ('contact/contact.db','message/message_0.db'):
     path=root/rel;path.parent.mkdir(exist_ok=True);key=os.urandom(32).hex();db=DB(path,key,fixture_write=True);writers.append(db)
     db.query('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;')
     if rel.startswith('contact'):
      db.query("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT); INSERT INTO contact VALUES('large@chatroom','分页测试',''),('u1','甲','');")
     else:
      db.query(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'u1\');')
     db.query('PRAGMA wal_checkpoint(TRUNCATE);')
     with path.open('rb') as f:keys[f.read(16).hex()]=key
    writer=writers[1];writer.query('BEGIN;')
    for i in range(1203):
     ts=int(a.timestamp()*1000)+i
     writer.query(f'INSERT INTO "{table}" VALUES({i+1},{i+1},1,{ts},1,{literal("虚构分页消息")});')
    writer.query('COMMIT;')
    writer.query('PRAGMA cache_size=1; BEGIN;')
    for i in range(2000,2040):
     writer.query(f'INSERT INTO "{table}" VALUES({i},{i},1,{int(a.timestamp()*1000)+i},1,{literal("未提交"*1000)});')
    data=read(root,keys,'分页测试',None,a,b)
    self.assertEqual(len(data['messages']),1203)
    self.assertEqual(data['metadata']['shards'][0]['sql_window_count'],1203)
    self.assertEqual(data['messages'][-1]['timestamp'],a.timestamp()+1.202)
    writer.query('ROLLBACK;')
   finally:
    for db in writers:db.close()
