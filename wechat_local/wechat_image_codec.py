# SPDX-License-Identifier: Apache-2.0
# Adapted in Python from erbanku/weixin-cli; see NOTICE.md.
"""Bounded, in-memory decoding of WeChat V2 image bytes.

The three-segment layout was checked against https://github.com/erbanku/weixin-cli/blob/08af894594b4afd468e23e17dbd783f15403f13b/src/attachment/decoder/v2.rs.
Only supplied key material is used. This module does not discover or persist
keys, read files, launch processes, or perform image-to-pixel conversion.
The caller must fully decode the returned image (including wxgf) before use.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import struct
import sys


V2_MAGIC = b"\x07\x08\x56\x32\x08\x07"
MAX_IMAGE_BYTES = 32 * 1024 * 1024
_HEADER_SIZE = 15
_CODES = frozenset({
    "invalid_image_data", "invalid_image_limit", "image_too_large",
    "invalid_image_key", "invalid_image_xor_key", "invalid_v2_header",
    "invalid_v2_lengths", "invalid_v2_padding", "invalid_image_header",
    "image_crypto_unavailable", "image_crypto_failed", "image_decode_failed",
})


class ImageDecodeError(ValueError):
    """A diagnostic code only; input data and key values are never included."""

    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in _CODES else "image_decode_failed"
        super().__init__(self.code)


@lru_cache(maxsize=1)
def _common_crypto():
    if sys.platform != "darwin":
        raise ImageDecodeError("image_crypto_unavailable")
    crypt = None
    try:
        library = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
        crypt = library.CCCrypt
        crypt.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                          ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                          ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        crypt.restype = ctypes.c_int32
    except (OSError, AttributeError):
        crypt = None
    if crypt is None:
        raise ImageDecodeError("image_crypto_unavailable")
    # Keep the loaded system library alive alongside the callable.
    return library, crypt


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    failed = False
    try:
        _, crypt = _common_crypto()
        key_buffer = ctypes.create_string_buffer(key)
        input_buffer = ctypes.create_string_buffer(ciphertext)
        output_buffer = ctypes.create_string_buffer(len(ciphertext))
        moved = ctypes.c_size_t()
        # kCCDecrypt=1, kCCAlgorithmAES=0, kCCOptionECBMode=2. Padding is
        # deliberately disabled here and strictly checked by decrypt_v2.
        status = crypt(1, 0, 2, key_buffer, len(key), None,
                       input_buffer, len(ciphertext), output_buffer,
                       len(ciphertext), ctypes.byref(moved))
    except ImageDecodeError:
        raise
    except Exception:
        failed = True
    # Raise outside the backend exception handler so even __context__ cannot
    # retain a backend diagnostic containing argument data.
    if failed:
        raise ImageDecodeError("image_crypto_failed")
    if status != 0 or moved.value != len(ciphertext):
        raise ImageDecodeError("image_crypto_failed")
    return output_buffer.raw[:moved.value]


def _check_image_container(data: bytes) -> None:
    """Check known headers and available fixed trailers; not pixel validity."""
    if data.startswith(b"wxgf") and len(data) > 4:
        return
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9") and len(data) >= 5:
        return
    if (data.startswith(b"\x89PNG\r\n\x1a\n")
            and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82") and len(data) >= 20):
        return
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 14 and data.endswith(b";"):
        return
    if (len(data) >= 20 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
            and struct.unpack_from("<I", data, 4)[0] == len(data) - 8):
        return
    raise ImageDecodeError("invalid_image_header")


def decrypt_v2(data: bytes, aes_key: bytes, xor_key: int, *,
               max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Return a V2 image's plaintext bytes, or raise ImageDecodeError(code).

    AES size denotes the unpadded prefix. Its ciphertext always includes one
    PKCS7 padding block when the size is already a multiple of sixteen. The
    remaining bytes are an unchanged middle and an explicitly keyed XOR tail.
    No key is inferred from other images, and no guessed fallback is used.
    max_bytes can reduce the hard 32 MiB input/output limit.
    """
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise ImageDecodeError("invalid_image_limit")
    if type(data) is not bytes:
        raise ImageDecodeError("invalid_image_data")
    if len(data) > max_bytes:
        raise ImageDecodeError("image_too_large")
    if type(aes_key) is not bytes or len(aes_key) != 16:
        raise ImageDecodeError("invalid_image_key")
    if type(xor_key) is not int or not 0 <= xor_key <= 255:
        raise ImageDecodeError("invalid_image_xor_key")
    if len(data) < _HEADER_SIZE or data[:6] != V2_MAGIC:
        raise ImageDecodeError("invalid_v2_header")

    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    aligned_size = aes_size + (16 - aes_size % 16)
    aes_end = _HEADER_SIZE + aligned_size
    raw_end = len(data) - xor_size
    # The one-byte header field is reserved by the reference format. It has
    # no effect on segment boundaries and is never treated as key material.
    if aes_end > len(data) or raw_end < aes_end or aes_size > max_bytes:
        raise ImageDecodeError("invalid_v2_lengths")
    padded = _aes_ecb_decrypt(data[_HEADER_SIZE:aes_end], aes_key)
    if len(padded) != aligned_size:
        raise ImageDecodeError("image_crypto_failed")
    padding = padded[-1]
    if (not 1 <= padding <= 16 or padded[-padding:] != bytes([padding]) * padding
            or len(padded) - padding != aes_size):
        raise ImageDecodeError("invalid_v2_padding")

    plaintext = (padded[:-padding] + data[aes_end:raw_end]
                 + bytes(value ^ xor_key for value in data[raw_end:]))
    if len(plaintext) > max_bytes:
        raise ImageDecodeError("image_too_large")
    _check_image_container(plaintext)
    return plaintext
