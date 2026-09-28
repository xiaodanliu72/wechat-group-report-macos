import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from wechat_local import keychain_store as store
from wechat_local.core import window

class KeychainTests(unittest.TestCase):
    def test_reject_wrong_account_and_bad_payload(self):
        root=Path('/synthetic/alice')
        record={'schema':1,'account_root':str(root),'keys':{'a'*32:'b'*64}}
        self.assertEqual(store.validate(root,record),record['keys'])
        with self.assertRaises(RuntimeError):store.validate('/synthetic/bob',record)
        record['keys']={'a'*32:'invalid'}
        with self.assertRaises(RuntimeError):store.validate(root,record)

    def test_missing_key_does_not_start_or_copy_client(self):
        a,b=window()
        with patch.object(store,'load',side_effect=store.KeyNotFound('missing')),patch('wechat_local.preparation.debug_copy') as launch,patch('wechat_local.reader.snapshot') as snap:
            with self.assertRaises(store.KeyNotFound):store.export('/synthetic','群',None,a,b,'unused')
            launch.assert_not_called();snap.assert_not_called()

    def test_wrong_key_fails_closed_and_clears_memory(self):
        from wechat_local.vendor.sqlcipher_probe import DB
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'db_storage'
            for rel in ('contact/contact.db','message/message_0.db'):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
                with DB(p,'1'*64,fixture_write=True) as db:db.query('CREATE TABLE fixture(n INTEGER)')
            keys={}
            for p in root.rglob('*.db'):
                with p.open('rb') as f:keys[f.read(16).hex()]='2'*64
            a,b=window()
            with patch.object(store,'load',return_value=keys),patch('wechat_local.preparation.debug_copy') as launch:
                with self.assertRaisesRegex(RuntimeError,'不会自动重新登录'):store.export(root,'群',None,a,b,Path(tmp)/'out')
                launch.assert_not_called()
            self.assertEqual(keys,{})
            self.assertFalse((Path(tmp)/'out').exists())
