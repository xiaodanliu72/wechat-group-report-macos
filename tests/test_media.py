import base64
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from wechat_local import media
from wechat_local.core import package, window, export_files, render, validate_messages
from wechat_local.master import render as render_master
from wechat_local.reader import read, literal
from wechat_local.vendor.sqlcipher_probe import DB
from tests.test_wechat_image_codec import _v2


class MediaTests(unittest.TestCase):
    def fixture(self):
        a,b=window(start='2026-10-07T00:00:00+08:00',end='2026-10-08T00:00:00+08:00')
        row={'local_id':1,'server_id':123,'local_type':3,'create_time':int(a.timestamp()),
             'sender_id':'u1','sender':'虚构发言人','database':'message_0.db',
             'body':'u1:\n<msg><img md5="'+'b'*32+'" aeskey="synthetic-secret" cdnmidimgurl="synthetic-token"/></msg>',
             'packed':(b'\x1a\x22\x22\x20'+b'a'*32).hex()}
        return package([row],'虚构图片群','fixture@chatroom',a,b,fixture=True)

    def png(self):
        out=io.BytesIO();Image.new('RGB',(32,24),(25,60,100)).save(out,format='PNG');return out.getvalue()

    def account(self, root):
        code=42;suffix=hashlib.md5(str(code).encode()).hexdigest()[:4]
        account=root/'Data/Documents/xwechat_files'/('fixture_wxid_'+suffix)
        account.mkdir(parents=True)
        cache=account.parent.parent/'app_data/net/kvcomm';cache.mkdir(parents=True)
        (cache/'key_42_test.statistic').touch()
        return account

    def encrypted_path(self, account, data):
        group_hash=hashlib.md5(data['metadata']['group_id'].encode()).hexdigest()
        p=account/'msg/attach'/group_hash/'2026-10/Img'/('a'*32+'.dat')
        p.parent.mkdir(parents=True,exist_ok=True);return p

    def test_only_declared_packed_field_and_safe_xml_metadata(self):
        data=self.fixture();details=data['messages'][0]['details']
        self.assertEqual(details,{'image_md5':'b'*32,'image_file_hash':'a'*32})
        self.assertNotIn('synthetic-secret',json.dumps(data))
        self.assertIsNone(media._packed_image_hash(b'\x12\x20'+b'a'*32))
        self.assertIsNone(media._packed_image_hash(b'\x1a\x22\x22\x20'+b'a'*31))
        self.assertIsNone(media._packed_image_hash(b'\x1a\x44\x22\x20'+b'a'*32+b'\x22\x20'+b'b'*32))

    def test_quote_credentials_removed_in_valid_and_malformed_xml(self):
        for text in ('<msg><img aeskey="fixture-secret" cdnthumburl="token" md5="abc"/></msg>',
                     '<msg><aeskey>fixture-secret</aeskey><img',
                     '<msg><img aeskey="fixture-secret" cdnthumburl="token">'):
            safe=media._sanitize_media_xml(text)
            self.assertNotIn('fixture-secret',safe);self.assertNotIn('token',safe)

    def test_original_priority_account_group_month_and_symlink_boundaries(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);account=self.account(root);data=self.fixture();m=data['messages'][0]
            original=self.encrypted_path(account,data);original.write_bytes(b'fixture')
            thumb=original.with_name('a'*32+'_t.dat');thumb.write_bytes(b'fixture')
            self.assertEqual(media._media_candidates(account,data['metadata']['group_id'],m)[0]['variant'],'original')
            self.assertEqual(media._media_candidates(account,'wrong@chatroom',m),[])
            later=copy.deepcopy(m);later['timestamp']+=86400*31
            self.assertEqual(media._media_candidates(account,data['metadata']['group_id'],later),[])
            original.unlink();thumb.unlink();original.symlink_to(root/'outside.dat');(root/'outside.dat').write_bytes(b'x')
            self.assertEqual(media._media_candidates(account,data['metadata']['group_id'],m),[])

    def test_missing_or_invalid_parameters_never_produce_ready(self):
        self.assertEqual(media._decode_image(media.V2_MAGIC+b'bad')['reason'],'encrypted_image_key_unavailable')
        from wechat_local.wechat_image_keys import ImageParameters
        result=media._decode_image(_v2(self.png()),(ImageParameters(b'wrong-key-value!',42),))
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('bytes',result)

    def test_standard_png_fully_decoded_and_white_images_remain_valid(self):
        data=self.png();decoded=media._decode_image(data)
        self.assertEqual((decoded['status'],decoded['width'],decoded['height']),('ready',32,24))
        self.assertEqual(media._decode_image(data[:40])['status'],'unavailable')
        out=io.BytesIO();Image.new('RGB',(5,5),'white').save(out,format='PNG')
        self.assertEqual(media._decode_image(out.getvalue())['status'],'ready')

    def test_wxgf_missing_ffmpeg_ambiguous_frames_and_blank_rejection(self):
        with patch.object(media.shutil,'which',return_value=None):
            self.assertEqual(media._decode_image(b'wxgfdata')['reason'],'existing_ffmpeg_unavailable')
        with patch.object(media.shutil,'which',return_value='fixture-ffmpeg'), patch.object(media,'_wxgf_candidates',return_value=[b'one',b'two']), patch.object(media,'_ffmpeg_frame',side_effect=[(1,1,b'\x30\x40\x50'),(1,1,b'\x40\x50\x60')]):
            self.assertEqual(media._decode_image(b'wxgfdata')['reason'],'ambiguous_decoded_frames')
        with patch.object(media.shutil,'which',return_value='fixture-ffmpeg'), patch.object(media,'_wxgf_candidates',return_value=[b'one']), patch.object(media,'_ffmpeg_frame',return_value=(1,1,b'\xff'*3)):
            self.assertEqual(media._decode_image(b'wxgfdata')['reason'],'blank_decoded_frame')

    def test_export_encrypted_image_embeds_offline_html_with_hash_verification(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);account=self.account(root);data=self.fixture()
            parameters=media.load_image_parameters(account)[0];plain=self.png()
            self.encrypted_path(account,data).write_bytes(_v2(plain,17,9,key=parameters.aes_key,xor_key=parameters.xor_key))
            folder=root/'out';export_files(folder,data,account)
            self.assertEqual(data['metadata']['images']['ready'],1)
            record=data['messages'][0]['details']['media'];output=folder/record['path']
            self.assertEqual(output.read_bytes(),plain);self.assertEqual(output.stat().st_mode & 0o777,0o600)
            self.assertNotIn(parameters.aes_key.decode(),(folder/'messages.json').read_text())
            self.assertNotIn('source_path',json.dumps(record))
            mid=data['messages'][0]['id'];report={'reviewed_message_ids':[mid],'overview':{'text':'虚构蓝色图片用于验证。','sources':[mid]},**{k:[] for k in ('topics','todos','confirmed','unresolved','other')}}
            render(folder,report);render_master(folder,only='html')
            for name in ('index.html','report-master.html'):
                html=(folder/name).read_text()
                self.assertIn(base64.b64encode(plain).decode(),html)
                self.assertNotIn('<details open',html)
            output.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'已改变'):media.image_source_html(folder,data['messages'][0])
            record['path']='../../outside.jpg'
            with self.assertRaisesRegex(ValueError,'路径'):media.image_source_html(folder,data['messages'][0])

    def test_missing_cache_remains_explicit_and_not_empty_success(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);account=self.account(root);data=self.fixture();export_files(root/'out',data,account)
            self.assertEqual(data['metadata']['images']['unavailable'],1)
            self.assertEqual(data['messages'][0]['details']['media']['reason'],'exact_media_cache_missing')

    def test_image_counts_and_message_binding_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);data=self.fixture();export_files(root/'out',data,self.account(root))
            data['metadata']['images']['ready']=1
            with self.assertRaisesRegex(ValueError,'图片统计'):validate_messages(data)
            data['metadata']['images']['ready']=0
            data['messages'][0]['details']['media']['message_id']='wrong'
            with self.assertRaisesRegex(ValueError,'来源绑定'):validate_messages(data)

    def test_selected_image_metadata_is_read_from_encrypted_wal_inside_window(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);keys={};writers=[];gid='fixture@chatroom';table='Msg_'+hashlib.md5(gid.encode()).hexdigest()
            a,b=window(start='2026-10-07T00:00:00+08:00',end='2026-10-08T00:00:00+08:00')
            try:
                for rel in ('contact/contact.db','message/message_0.db'):
                    p=root/rel;p.parent.mkdir();key=os.urandom(32).hex();db=DB(p,key,fixture_write=True);writers.append(db)
                    db.query('PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;')
                    if rel.startswith('contact'):
                        db.query("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT); INSERT INTO contact VALUES('fixture@chatroom','虚构图片群',''),('u1','甲','');")
                    else:
                        db.query(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content BLOB,packed_info_data BLOB); CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES(\'u1\');')
                    db.query('PRAGMA wal_checkpoint(TRUNCATE);')
                    with p.open('rb') as f:keys[f.read(16).hex()]=key
                for i,stamp in enumerate((a.timestamp()-1,a.timestamp(),b.timestamp()),1):
                    packed=(b'\x1a\x22\x22\x20'+b'a'*32).hex()
                    writers[1].query(f'INSERT INTO "{table}" VALUES({i},{i},3,{int(stamp)*1000},1,{literal("<msg><img/></msg>")},x\'{packed}\');')
                data=read(root,keys,'虚构图片群',gid,a,b)
                self.assertEqual(len(data['messages']),1)
                self.assertEqual(data['messages'][0]['details']['image_file_hash'],'a'*32)
                self.assertTrue(data['metadata']['shards'][0]['wal_present'])
            finally:
                for writer in writers:writer.close()
                keys.clear()
