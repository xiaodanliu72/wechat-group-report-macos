import copy
import unittest
from wechat_local.core import package,window,validate_messages
class ValidationTests(unittest.TestCase):
 def setUp(self):
  a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
  self.data=package([dict(local_id=i+1,server_id=i+1,database='message_0.db',create_time=int(a.timestamp())+i,body='虚构',local_type=1,sender_id='u1',sender='虚构') for i in range(2)],'测试','test@chatroom',a,b,True)
 def test_reject_corrupted_statistics(self):
  for field,value in [('message_count',99),('speaker_count',3),('unknown_sender_messages',1),('type_counts',{'语音':2}),('message_count',True)]:
   with self.subTest(field=field):
    d=copy.deepcopy(self.data);d['metadata'][field]=value
    with self.assertRaises(ValueError):validate_messages(d)
 def test_reject_duplicate_and_boolean_ids(self):
  for bad in [True,self.data['messages'][1]['id']]:
   d=copy.deepcopy(self.data);d['messages'][0]['id']=bad
   with self.assertRaises(ValueError):validate_messages(d)
 def test_reject_exclusive_end_and_mismatch(self):
  for field,value in [('time',self.data['metadata']['end']),('timestamp',0)]:
   d=copy.deepcopy(self.data);d['messages'][0][field]=value
   with self.assertRaises(ValueError):validate_messages(d)
 def test_order_and_valid_empty(self):
  d=copy.deepcopy(self.data);d['messages'].reverse()
  with self.assertRaises(ValueError):validate_messages(d)
  self.assertTrue(validate_messages(self.data))
  a,b=window();self.assertTrue(validate_messages(package([],'空群','empty@chatroom',a,b,True)))
