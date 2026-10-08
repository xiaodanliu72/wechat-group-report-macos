import hashlib
from pathlib import Path
import tempfile
import unittest

from wechat_local.wechat_image_keys import load_image_parameters


class ImageParameterTests(unittest.TestCase):
    def setup_cache(self, temporary, code=42):
        data = Path(temporary) / "Data"
        suffix = hashlib.md5(str(code).encode()).hexdigest()[:4]
        account = data / "Documents/xwechat_files" / ("fixture_wxid_" + suffix)
        account.mkdir(parents=True)
        cache = data / "Documents/app_data/net/kvcomm"
        cache.mkdir(parents=True)
        (cache / f"key_{code}_fixture.statistic").write_bytes(b"not read")
        return account, cache

    def test_current_account_filename_parameters_without_reading_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            account, _ = self.setup_cache(temporary)
            result = load_image_parameters(account)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0].aes_key, hashlib.md5(b"42fixture_wxid").hexdigest()[:16].encode())
            self.assertEqual(result[0].xor_key, 42)
            self.assertNotIn(result[0].aes_key.decode(), repr(result[0]))
            self.assertNotIn("42", repr(result[0]))

    def test_other_account_and_malformed_filenames_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            account, cache = self.setup_cache(temporary)
            for name in ("key_43_other.statistic", "key_4294967296_large.statistic", "key_secret.statistic"):
                (cache / name).write_bytes(b"ignored")
            self.assertEqual(len(load_image_parameters(account)), 2)
            wrong = account.with_name("fixture_wxid_0000")
            wrong.mkdir()
            self.assertEqual(load_image_parameters(wrong), ())

    def test_symlinked_parameter_file_is_not_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            account, cache = self.setup_cache(temporary)
            path = next(cache.iterdir())
            path.unlink()
            other = Path(temporary) / "foreign.statistic"
            other.write_bytes(b"foreign")
            path.symlink_to(other)
            self.assertEqual(load_image_parameters(account), ())

    def test_symlinked_cache_directory_is_not_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            account, cache = self.setup_cache(temporary)
            actual = cache.with_name("foreign")
            cache.rename(actual)
            cache.symlink_to(actual, target_is_directory=True)
            self.assertEqual(load_image_parameters(account), ())

    def test_missing_cache_and_unsupported_account_stop_without_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(load_image_parameters(Path(temporary) / "unknown"), ())
            self.assertEqual(load_image_parameters(Path(temporary) / "fixture_wxid_a1b2"), ())


if __name__ == "__main__":
    unittest.main()
