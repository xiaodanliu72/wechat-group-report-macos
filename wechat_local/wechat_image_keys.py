# SPDX-License-Identifier: Apache-2.0
# Adapted in Python from erbanku/weixin-cli; see NOTICE.md.
"""Derive this Mac account's image parameters from existing cache filenames.

The macOS format was checked against the primary implementation at
https://github.com/erbanku/weixin-cli/blob/08af894594b4afd468e23e17dbd783f15403f13b/src/attachment/image_key/macos.rs
Only the current WeChat container's kvcomm directory is listed. No cache body,
process memory, Keychain write, client launch, network or persisted key is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re


@dataclass(frozen=True)
class ImageParameters:
    aes_key: bytes = field(repr=False)
    xor_key: int = field(repr=False)


def _cache_directories(account: Path) -> list[Path]:
    if account.parent.name != "xwechat_files":
        return []
    documents = account.parent.parent
    container_data = documents.parent
    return [documents / "app_data/net/kvcomm", documents / "xwechat/net/kvcomm",
            container_data / "Library/Application Support/com.tencent.xinWeChat/xwechat/net/kvcomm",
            container_data / "Library/Application Support/com.tencent.xinWeChat/net/kvcomm"]


def load_image_parameters(account: Path) -> tuple[ImageParameters, ...]:
    """Return a small, account-bound candidate set kept only in process memory.

    The four-hex account-directory suffix is matched to the cached numeric ID;
    a successful full-image decode is still required before any evidence is used.
    Missing, ambiguous, unreadable or symlinked caches never trigger a fallback
    scan or guessed default key.
    """
    account = Path(account)
    matched = re.fullmatch(r"([A-Za-z0-9_]+)_([0-9a-fA-F]{4})", account.name)
    if not matched or account.is_symlink():
        return ()
    normalized, suffix = matched[1], matched[2].lower()
    codes = set()
    for directory in _cache_directories(account):
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            directory.resolve().relative_to(account.parent.parent.parent.resolve())
            for path in directory.iterdir():
                if path.is_symlink() or not path.is_file():
                    continue
                name = re.fullmatch(r"key_(\d{1,10})_[^/]+\.statistic", path.name)
                if not name:
                    continue
                code = int(name[1])
                if not 0 < code <= 0xffffffff:
                    continue
                if hashlib.md5(str(code).encode()).hexdigest()[:4] != suffix:
                    continue
                codes.add(code)
                if len(codes) > 16:
                    return ()
        except (OSError, ValueError):
            continue
    result, seen = [], set()
    for code in sorted(codes):
        for wxid in (normalized, account.name):
            key = hashlib.md5((str(code) + wxid).encode()).hexdigest()[:16].encode("ascii")
            if key not in seen:
                seen.add(key)
                result.append(ImageParameters(key, code & 0xff))
    return tuple(result)
