import datetime as dt
import tempfile
import unittest
from pathlib import Path
from wechat_local.core import *
from wechat_local.reader import snapshot
class Tests(unittest.TestCase):
 def setUp(self):self.a,self.b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
 def row(self,n,t=None,text='测试',typ=1):return dict(local_id=n,server_id=n,database='message_0.db',create_time=t or int(self.a.timestamp()),body=text,local_type=typ,sender_id='u1',sender='小明')
 def test_boundary_dedup(self):
  r=self.row(1); data=package([self.row(0,int(self.a.timestamp())-1),r,r.copy(),self.row(2,int(self.b.timestamp()))],'群','g@chatroom',self.a,self.b)
  self.assertEqual(data['metadata']['message_count'],1);self.assertEqual(data['metadata']['duplicates_removed'],1)
 def test_conflict(self):
  with self.assertRaises(ValueError):normalize([self.row(1),self.row(1,text='不同')],'g',self.a,self.b)
 def test_xml_media(self):
  r=parse_body('<msg><appmsg><title>文章</title><url>https://example.com</url><refermsg><svrid>9</svrid><content>建议</content></refermsg></appmsg></msg>',49,'u1')
  self.assertEqual(r['details']['quote']['svrid'],'9');self.assertIn('未解析',parse_body('',3,None)['text'])
 def test_real_zstd_api(self):
  from compression import zstd
  body=zstd.compress('u1:\n压缩的中文消息'.encode())
  self.assertEqual(parse_body(body,1,'u1')['text'],'压缩的中文消息')
  with self.assertRaises(ValueError):parse_body(body[:-2],1,'u1')
 def test_timestamp(self):self.assertEqual(timestamp(int(self.a.timestamp())*1000),self.a.timestamp())
 def test_render_refs_escape(self):
  d=package([self.row(1,text='<script>alert(1)</script>')],'群 <测试>','g',self.a,self.b,True)
  mid=d['messages'][0]['id'];r={'overview':{'text':'虚构测试概览','sources':[mid]},'reviewed_message_ids':[mid],**{k:[] for k in SECTIONS}}
  with tempfile.TemporaryDirectory() as tmp:
   out=Path(tmp)/'run';export_files(out,d);render(out,r)
   self.assertTrue((out/'report.png').exists());self.assertIn('&lt;script&gt;', (out/'index.html').read_text());self.assertNotIn('<script>',(out/'index.html').read_text())
   r['overview']['sources']=['fake']
   with self.assertRaises(ValueError):validate_report(d,r)
 def test_snapshot_cleanup(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t)
   for rel in ('contact/contact.db','message/message_0.db'):
    p=root/rel;p.parent.mkdir(exist_ok=True);p.write_bytes(b'encrypted fixture')
   with self.assertRaises(RuntimeError):
    with snapshot(root) as s:
     location=s;raise RuntimeError('simulated failure')
   self.assertFalse(location.exists())
if __name__=='__main__':unittest.main()
