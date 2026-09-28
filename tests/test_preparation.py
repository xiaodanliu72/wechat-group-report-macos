import tempfile
import plistlib
import unittest
from unittest.mock import patch
from pathlib import Path
from wechat_local.preparation import select_account,find_app
from wechat_local.core import normalize,window


class PreparationTests(unittest.TestCase):
    def test_explicit_app_must_have_verified_bundle_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Path(tmp)/'WeChat.app';contents=app/'Contents';contents.mkdir(parents=True)
            info=contents/'Info.plist'
            info.write_bytes(plistlib.dumps({'CFBundleIdentifier':'com.tencent.xinWeChat'}))
            self.assertEqual(find_app(app),app.resolve())
            info.write_bytes(plistlib.dumps({'CFBundleIdentifier':'other.app'}))
            with self.assertRaises(RuntimeError):find_app(app)

    def test_initialization_rejects_unsupported_architecture_before_account_access(self):
        from wechat_local.isolated_bootstrap import main
        with patch('sys.argv',['isolated_bootstrap','--out','unused']),patch('wechat_local.isolated_bootstrap.platform.machine',return_value='x86_64'),patch('wechat_local.isolated_bootstrap.select_account') as accounts:
            with self.assertRaisesRegex(RuntimeError,'Apple Silicon'):main()
            accounts.assert_not_called()

    def test_network_login_requires_explicit_disruption_acceptance(self):
        from wechat_local.isolated_bootstrap import main
        with patch('sys.argv',['isolated_bootstrap','--allow-login-network','--out','unused']),patch('wechat_local.isolated_bootstrap.select_account') as accounts,patch('wechat_local.isolated_bootstrap.debug_copy') as copy:
            with self.assertRaisesRegex(RuntimeError,'顶掉原微信'):main()
            accounts.assert_not_called();copy.assert_not_called()

    def test_account_selection_never_mixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=Path(tmp).resolve();base=data/'Documents/xwechat_files'
            root=base/'alice_1234/db_storage';root.mkdir(parents=True)
            (base/'all_users/login/alice').mkdir(parents=True)
            self.assertEqual(select_account(data),(root,'alice'))
            (base/'bob_5678/db_storage').mkdir(parents=True)
            (base/'all_users/login/bob').mkdir(parents=True)
            with self.assertRaises(RuntimeError):select_account(data)
            self.assertEqual(select_account(data,root),(root,'alice'))
            with self.assertRaises(RuntimeError):select_account(data,root,'bob')

    def test_same_second_uses_numeric_local_sequence(self):
        a,b=window(start='2026-09-27T12:00:00+08:00',end='2026-09-28T12:00:00+08:00')
        rows=[dict(local_id=i,server_id=100-i,database='message_0.db',create_time=int(a.timestamp()),body='虚构',local_type=1,sender_id='u1') for i in (10,2,1)]
        msgs,_=normalize(rows,'g@chatroom',a,b)
        self.assertEqual([m['local_id'] for m in msgs],['1','2','10'])
