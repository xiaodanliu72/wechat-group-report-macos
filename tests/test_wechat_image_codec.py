import base64
import ctypes
import struct
import sys
import traceback
import unittest
from unittest.mock import patch
import zlib

from wechat_local import wechat_image_codec as codec


KEY = b"fixture-key-1234"
XOR = 0x59


def _encrypt_aes(data, key=KEY):
    """Fixture construction only, with an independent system-crypto call."""
    library = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
    crypt = library.CCCrypt
    crypt.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                      ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    crypt.restype = ctypes.c_int32
    output = ctypes.create_string_buffer(len(data))
    moved = ctypes.c_size_t()
    status = crypt(0, 0, 2, ctypes.create_string_buffer(key), len(key), None,
                   ctypes.create_string_buffer(data), len(data), output,
                   len(data), ctypes.byref(moved))
    if status != 0 or moved.value != len(data):
        raise RuntimeError("fixture_encryption_failed")
    return output.raw[:moved.value]


def _chunk(name, data):
    return (struct.pack(">I", len(data)) + name + data
            + struct.pack(">I", zlib.crc32(name + data)))


def _png():
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + _chunk(b"tEXt", b"Comment\0" + b"fixture" * 300)
            + _chunk(b"IDAT", zlib.compress(b"\0\x11\x22\x33"))
            + _chunk(b"IEND", b""))


def _v2(plaintext, aes_size=16, xor_size=9, *, key=KEY, xor_key=XOR):
    if not 0 <= aes_size <= len(plaintext) - xor_size:
        raise ValueError("invalid_fixture_split")
    prefix = plaintext[:aes_size]
    padding = 16 - aes_size % 16
    encrypted = _encrypt_aes(prefix + bytes([padding]) * padding, key)
    raw_end = len(plaintext) - xor_size
    return (codec.V2_MAGIC + struct.pack("<II", aes_size, xor_size) + b"\x01"
            + encrypted + plaintext[aes_size:raw_end]
            + bytes(value ^ xor_key for value in plaintext[raw_end:]))


@unittest.skipUnless(sys.platform == "darwin", "CommonCrypto runtime is macOS")
class WechatImageCodecTests(unittest.TestCase):
    def assert_code(self, expected, *args, **kwargs):
        with self.assertRaises(codec.ImageDecodeError) as raised:
            codec.decrypt_v2(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)
        self.assertEqual(str(raised.exception), expected)
        return raised.exception

    def test_system_decryption_matches_independent_nist_aes128_ecb_vector(self):
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        ciphertext = bytes.fromhex("3ad77bb40d7a3660a89ecaf32466ef97")
        expected = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        self.assertEqual(codec._aes_ecb_decrypt(ciphertext, key), expected)

    def test_aes_prefix_sizes_cross_block_boundaries_without_losing_raw_or_xor(self):
        plaintext = _png()
        for size in (0, 1, 7, 15, 16, 17, 31, 32, 1024):
            with self.subTest(aes_size=size):
                encrypted = _v2(plaintext, size, 257)
                self.assertEqual(codec.decrypt_v2(encrypted, KEY, XOR), plaintext)

    def test_aligned_aes_prefix_uses_additional_full_padding_block(self):
        plaintext = _png()
        encrypted = _v2(plaintext, 1024, 0)
        self.assertEqual(len(encrypted), 15 + len(plaintext) + 16)
        self.assertEqual(codec.decrypt_v2(encrypted, KEY, XOR), plaintext)

    def test_no_raw_middle_and_no_xor_tail_are_supported(self):
        plaintext = _png()
        for prefix, tail in ((7, len(plaintext) - 7), (len(plaintext), 0)):
            with self.subTest(prefix=prefix, tail=tail):
                self.assertEqual(codec.decrypt_v2(_v2(plaintext, prefix, tail), KEY, XOR), plaintext)

    def test_wxgf_and_standard_container_bytes_are_returned_without_transcoding(self):
        gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        wxgf = b"wxgf" + b"\x00" * 12 + b"\x00\x00\x00\x01\x40\x01" + b"fixture-nalu"
        jpeg = b"\xff\xd8\xff\xe0" + b"fixture-container" + b"\xff\xd9"
        webp_body = b"VP8L" + struct.pack("<I", 4) + b"\x2f\x00\x00\x00"
        webp = b"RIFF" + struct.pack("<I", len(webp_body) + 4) + b"WEBP" + webp_body
        for plaintext in (_png(), gif, wxgf, jpeg, webp):
            with self.subTest(header=plaintext[:4]):
                self.assertEqual(codec.decrypt_v2(_v2(plaintext, 3, 2), KEY, XOR), plaintext)

    def test_invalid_key_types_or_sizes_are_never_coerced(self):
        encrypted = _v2(_png())
        for key in (None, "fixture-key-1234", b"", b"a" * 15, b"a" * 24, b"a" * 32, bytearray(KEY)):
            with self.subTest(key_type=type(key).__name__):
                self.assert_code("invalid_image_key", encrypted, key, XOR)

    def test_xor_key_must_be_explicit_single_byte_integer(self):
        encrypted = _v2(_png())
        for value in (None, True, False, -1, 256, 1.5, "89"):
            with self.subTest(value_type=type(value).__name__):
                self.assert_code("invalid_image_xor_key", encrypted, KEY, value)

    def test_bounded_input_and_invalid_limits_fail_before_crypto(self):
        encrypted = _v2(_png())
        with patch.object(codec, "_aes_ecb_decrypt") as decrypt:
            self.assert_code("image_too_large", encrypted, KEY, XOR, max_bytes=len(encrypted) - 1)
            for limit in (0, -1, True, 1.5, codec.MAX_IMAGE_BYTES + 1):
                self.assert_code("invalid_image_limit", encrypted, KEY, XOR, max_bytes=limit)
            self.assert_code("invalid_image_data", bytearray(encrypted), KEY, XOR)
            decrypt.assert_not_called()

    def test_truncated_or_wrong_magic_header_fails_before_crypto(self):
        with patch.object(codec, "_aes_ecb_decrypt") as decrypt:
            for data in (b"", codec.V2_MAGIC, codec.V2_MAGIC + b"\x00" * 8,
                         b"badbad" + b"\x00" * 9):
                self.assert_code("invalid_v2_header", data, KEY, XOR)
            decrypt.assert_not_called()

    def test_declared_lengths_cannot_overflow_truncate_or_overlap(self):
        body = b"\x00" * 16
        with patch.object(codec, "_aes_ecb_decrypt") as decrypt:
            for prefix, tail in ((0xffffffff, 0), (0, 0xffffffff), (0, 1), (16, 0)):
                data = codec.V2_MAGIC + struct.pack("<II", prefix, tail) + b"\x01" + body
                self.assert_code("invalid_v2_lengths", data, KEY, XOR)
            decrypt.assert_not_called()

    def test_padding_length_bytes_and_declared_plaintext_size_are_strict(self):
        encrypted = _v2(_png(), 15, 0)
        invalid = (b"a" * 15 + b"\x00", b"a" * 15 + b"\x11",
                   b"a" * 14 + b"\x00\x02", b"a" * 14 + b"\x02\x02")
        for padded in invalid:
            with self.subTest(last=padded[-1]), patch.object(codec, "_aes_ecb_decrypt", return_value=padded):
                self.assert_code("invalid_v2_padding", encrypted, KEY, XOR)

    def test_backend_partial_output_is_rejected(self):
        with patch.object(codec, "_aes_ecb_decrypt", return_value=b"short"):
            self.assert_code("image_crypto_failed", _v2(_png()), KEY, XOR)

    def test_incorrect_aes_key_does_not_return_partial_plaintext(self):
        encrypted = _v2(_png(), 1024, 257)
        with self.assertRaises(codec.ImageDecodeError) as raised:
            codec.decrypt_v2(encrypted, b"different-key!!!", XOR)
        self.assertIn(raised.exception.code, {"invalid_v2_padding", "invalid_image_header"})

    def test_wrong_xor_is_rejected_by_this_images_fixed_trailer_without_guessing(self):
        encrypted = _v2(_png(), 1024, 257)
        self.assert_code("invalid_image_header", encrypted, KEY, XOR ^ 1)

    def test_valid_padding_does_not_accept_unknown_or_truncated_image_headers(self):
        for plaintext in (b"unrecognized-image-data", b"wxgf", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"):
            with self.subTest(header=plaintext[:4]):
                self.assert_code("invalid_image_header", _v2(plaintext, 1, 0), KEY, XOR)

    def test_backend_errors_carry_no_key_data_or_original_diagnostic_context(self):
        secret_diagnostic = "synthetic-key-value-must-never-escape"
        encrypted = _v2(_png())
        with patch.object(codec, "_common_crypto", side_effect=RuntimeError(secret_diagnostic)):
            try:
                codec.decrypt_v2(encrypted, KEY, XOR)
            except codec.ImageDecodeError as error:
                self.assertEqual(error.code, "image_crypto_failed")
                self.assertIsNone(error.__context__)
                rendered = "".join(traceback.format_exception(error))
            else:
                self.fail("backend error was not controlled")
        self.assertNotIn(secret_diagnostic, rendered)
        self.assertNotIn(KEY.decode(), rendered)

    def test_missing_system_crypto_is_controlled_without_loader_context(self):
        codec._common_crypto.cache_clear()
        try:
            with patch.object(codec.ctypes, "CDLL", side_effect=OSError("synthetic-loader-secret")):
                error = self.assert_code("image_crypto_unavailable", codec.V2_MAGIC
                                         + struct.pack("<II", 0, 0) + b"\x01" + b"x" * 16, KEY, XOR)
                self.assertIsNone(error.__context__)
                self.assertNotIn("synthetic-loader-secret", repr(error))
        finally:
            codec._common_crypto.cache_clear()

    def test_unknown_error_codes_cannot_echo_input(self):
        error = codec.ImageDecodeError("synthetic-secret-value")
        self.assertEqual(error.code, "image_decode_failed")
        self.assertEqual(repr(error), "ImageDecodeError('image_decode_failed')")


if __name__ == "__main__":
    unittest.main()
