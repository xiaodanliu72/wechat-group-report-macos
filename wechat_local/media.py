"""Resolve only selected messages' local images; no client launch or downloads.

V2 crypto/cache parameters are adapted separately under Apache-2.0.
Media lookup, pixel validation and report integration are project code (MIT).
"""
import base64
import datetime as dt
import hashlib
import html
import io
import math
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo
from PIL import Image
from .wechat_image_keys import load_image_parameters
from .wechat_image_codec import ImageDecodeError, decrypt_v2, V2_MAGIC

TZ = ZoneInfo("Asia/Shanghai")
MAX_MEDIA_BYTES = 32 * 1024 * 1024

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()



def _protobuf_fields(data: bytes) -> list[tuple[int, int, bytes | int]]:
    """Decode bounded wire fields; never search arbitrary bytes for filenames."""
    if len(data) > 65536:
        raise ValueError("packed_image_metadata_size_limit")
    fields = []
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError("truncated_packed_image_metadata")
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                if shift == 63 and byte > 1:
                    raise ValueError("invalid_packed_image_metadata")
                return value
        raise ValueError("invalid_packed_image_metadata")

    while offset < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not 0 < number < (1 << 29):
            raise ValueError("invalid_packed_image_metadata")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else 8 if wire == 1 else 4
            if length > len(data) - offset:
                raise ValueError("truncated_packed_image_metadata")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError("unsupported_packed_image_metadata")
        fields.append((number, wire, value))
    return fields



def _packed_image_hash(data: bytes) -> str | None:
    """Message image descriptor field 3 / fileHash field 4, exactly.

    The observed local schema has optional dimensions in descriptor fields 1/2.
    CDN keys, XML md5 values and hashes elsewhere are not filename evidence.
    Conflicting descriptors or malformed protobuf produce no binding.
    """
    try:
        hashes = []
        for number, wire, value in _protobuf_fields(data):
            if number != 3 or wire != 2:
                continue
            for child_number, child_wire, child_value in _protobuf_fields(value):
                if child_number == 4 and child_wire == 2:
                    if not re.fullmatch(rb"[0-9a-fA-F]{32}", child_value):
                        return None
                    hashes.append(child_value.decode("ascii").lower())
        return hashes[0] if hashes and len(set(hashes)) == 1 else None
    except ValueError:
        return None



def _sanitize_media_xml(text: str) -> str:
    """Do not export media keys or credential-bearing CDN resource locators."""
    if not isinstance(text, str) or "<" not in text:
        return text
    from defusedxml import ElementTree as ET
    prefix, _, xml = text.partition("<")
    try:
        root = ET.fromstring("<" + xml)
        changed = False
        for node in root.iter():
            for attribute in list(node.attrib):
                name = attribute.rsplit("}", 1)[-1].lower()
                if name.startswith("cdn") or name in {"aeskey", "aes_key", "encryptkey", "encryptionkey"}:
                    del node.attrib[attribute]
                    changed = True
            for child in list(node):
                name = child.tag.rsplit("}", 1)[-1].lower() if isinstance(child.tag, str) else ""
                if name.startswith("cdn") or name in {"aeskey", "aes_key", "encryptkey", "encryptionkey"}:
                    node.remove(child)
                    changed = True
        return prefix + ET.tostring(root, encoding="unicode") if changed else text
    except Exception:
        # Malformed media XML is not a reason to preserve recognizable secrets.
        if re.search(r"(?i)(?:aeskey|aes_key|encryptkey|encryptionkey|cdn[\w:.-]*)\s*(?:=|>)", text):
            return prefix + "[媒体 XML 无法解析，已省略敏感字段]"
        return text



def _media_candidates(account: Path, gid: str, message: dict) -> list[dict]:
    """Exact, declared variants only; never choose by directory order or mtime."""
    group_hash = hashlib.md5(gid.encode()).hexdigest()
    stamp = float(message["timestamp"])
    month = dt.datetime.fromtimestamp(stamp, TZ).strftime("%Y-%m")
    cache = account / "cache" / month / "Message" / group_hash
    lid = str(message["local_id"])
    if not re.fullmatch(r"[0-9]+", lid):
        return []
    # Nonintegral timestamps cannot be silently rounded into another message.
    token = str(int(stamp)) if stamp.is_integer() else str(stamp)
    candidates = []

    def add(path, binding, variant):
        if path.is_file() and not path.is_symlink():
            try:
                path.resolve().relative_to(account.resolve())
            except ValueError:
                return
            candidates.append({"source_path": path, "binding": binding, "variant": variant})

    for variant in ("big", "mid", "small", "thumb"):
        add(cache / "ImageTemp" / f"{lid}_{token}_{variant}_temp_convert", "local_id_timestamp", variant)
    for extension in ("jpg", "jpeg", "png"):
        add(cache / "Thumb" / f"{lid}_{token}_thumb.{extension}", "local_id_timestamp", "thumbnail")
    seen_hashes = set()
    for field, binding in (("image_file_hash", "message_packed_file_hash"), ("image_md5", "image_xml_md5")):
        image_hash = message.get("details", {}).get(field)
        if not image_hash or not re.fullmatch(r"[0-9a-f]{32}", image_hash) or image_hash in seen_hashes:
            continue
        seen_hashes.add(image_hash)
        attachment = account / "msg" / "attach" / group_hash / month / "Img"
        for suffix, variant in ((".dat", "original"), ("_h.dat", "high")):
            add(attachment / (image_hash + suffix), binding, variant)
        add(cache / "Bubble" / f"{image_hash}_b.dat", binding, "bubble")
        add(attachment / (image_hash + "_t.dat"), binding, "thumbnail")
    priority = {"original": 0, "high": 1, "big": 2, "mid": 3,
                "small": 4, "bubble": 5, "thumb": 6, "thumbnail": 6}
    return sorted(candidates, key=lambda candidate: priority.get(candidate["variant"], 7))



def _wxgf_units(data: bytes) -> list[bytes]:
    if not data.startswith(b"wxgf") or len(data) > MAX_MEDIA_BYTES:
        return []
    starts = []
    i = 4
    while i < len(data) - 3:
        prefix = 4 if data[i:i + 4] == b"\x00\x00\x00\x01" else 3 if data[i:i + 3] == b"\x00\x00\x01" else 0
        if prefix:
            starts.append((i, prefix))
            i += prefix
        else:
            i += 1
    result = []
    for index, (start, prefix) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(data)
        unit = data[start + prefix:end]
        if len(unit) >= 2 and not unit[0] & 0x80:
            result.append(unit)
    return result



def _wxgf_candidates(data: bytes) -> list[bytes]:
    units = _wxgf_units(data)
    starts = [i for i, unit in enumerate(units) if (unit[0] >> 1) & 63 == 32]
    candidates = []
    for number, start in enumerate(starts):
        group = units[start:starts[number + 1] if number + 1 < len(starts) else len(units)]
        if any((unit[0] >> 1) & 63 in (1, 19, 20) for unit in group):
            candidates.append(b"".join(b"\x00\x00\x00\x01" + unit for unit in group))
    if units:
        candidates.append(b"".join(b"\x00\x00\x00\x01" + unit for unit in units))
    # A malformed container must not be passed through as arbitrary ffmpeg input.
    return list(dict.fromkeys(candidates))[:8]



def _parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if header is None:
        raise ValueError("invalid_decoded_frame")
    width, height = int(header[1]), int(header[2])
    pixels = data[header.end():]
    if not (0 < width <= 20000 and 0 < height <= 20000 and width * height <= 30_000_000
            and len(pixels) == width * height * 3):
        raise ValueError("invalid_decoded_frame")
    return width, height, pixels



def _blank_frame(pixels: bytes) -> bool:
    stride = max(1, len(pixels) // 3 // 4096)
    samples = [pixels[index:index + 3] for index in range(0, len(pixels), stride * 3)]
    deviation = []
    for channel in range(3):
        values = [sample[channel] for sample in samples]
        mean = sum(values) / len(values)
        deviation.append(math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)))
    extreme = max(sum(all(value >= 250 for value in sample) for sample in samples),
                  sum(all(value <= 5 for value in sample) for sample in samples)) / len(samples)
    return sum(deviation) / 3 < 2 and extreme > .985



def _rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    rows = b"".join(b"\x00" + pixels[row * width * 3:(row + 1) * width * 3] for row in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))



def _ffmpeg_frame(data: bytes, container: str, executable: str) -> tuple[int, int, bytes]:
    result = subprocess.run([executable, "-hide_banner", "-loglevel", "error", "-nostdin",
                             "-protocol_whitelist", "pipe", "-f", container, "-i", "pipe:0",
                             "-frames:v", "1", "-c:v", "ppm", "-f", "image2pipe", "pipe:1"],
                            input=data, capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError("image_decode_failed")
    return _parse_ppm(result.stdout)



def _decode_image(data: bytes, image_parameters=None) -> dict:
    if len(data) > MAX_MEDIA_BYTES:
        return {"status": "unavailable", "reason": "media_size_limit"}
    if data.startswith(V2_MAGIC):
        if not image_parameters:
            return {"status": "unavailable", "reason": "encrypted_image_key_unavailable", "container": "wechat_v2"}
        recovered, failures = {}, []
        for parameters in image_parameters:
            try:
                plain = decrypt_v2(data, parameters.aes_key, parameters.xor_key, max_bytes=MAX_MEDIA_BYTES)
            except ImageDecodeError as error:
                failures.append(error.code)
                continue
            decoded = _decode_image(plain)
            if decoded["status"] == "ready":
                recovered[decoded["pixel_sha256"]] = decoded
            else:
                failures.append(decoded["reason"])
        if len(recovered) > 1:
            return {"status": "unavailable", "reason": "ambiguous_decrypted_images", "container": "wechat_v2"}
        if not recovered:
            return {"status": "unavailable", "reason": failures[-1] if failures else "encrypted_image_decode_failed",
                    "container": "wechat_v2"}
        decoded = next(iter(recovered.values()))
        decoded.update(source_container="wechat_v2", decryption="macos_kvcomm_aes_ecb_xor")
        return decoded
    container = "wxgf" if data.startswith(b"wxgf") else "jpeg" if data.startswith(b"\xff\xd8\xff") else "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else None
    if container is None:
        return {"status": "unavailable", "reason": "unsupported_media_format"}
    if container in ("jpeg", "png"):
        try:
            with Image.open(io.BytesIO(data)) as check:
                if check.width > 20000 or check.height > 20000 or check.width * check.height > 30_000_000:
                    return {"status": "unavailable", "reason": "media_pixel_limit"}
                check.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                width, height = image.size
                pixels = image.convert("RGB").tobytes()
            return {"status": "ready", "reason": "", "container": container,
                    "width": width, "height": height, "pixel_sha256": _sha(struct.pack(">II", width, height) + pixels),
                    "content_sha256": _sha(data), "bytes": data,
                    "extension": ".png" if container == "png" else ".jpg"}
        except (OSError, ValueError, Image.DecompressionBombError):
            return {"status": "unavailable", "reason": "image_decode_failed"}
    executable = shutil.which("ffmpeg")
    if not executable:
        return {"status": "unavailable", "reason": "existing_ffmpeg_unavailable", "container": container}
    candidates = _wxgf_candidates(data) if container == "wxgf" else [data]
    decoded = {}
    errors = []
    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
    for candidate in candidates:
        if dt.datetime.now(dt.timezone.utc) >= deadline:
            errors.append("image_decode_timeout")
            break
        try:
            width, height, pixels = _ffmpeg_frame(candidate, "hevc" if container == "wxgf" else "image2pipe", executable)
            if _blank_frame(pixels):
                errors.append("blank_decoded_frame")
                continue
            pixel_hash = _sha(struct.pack(">II", width, height) + pixels)
            decoded[pixel_hash] = (width, height, pixels)
        except subprocess.TimeoutExpired:
            errors.append("image_decode_timeout")
        except (OSError, ValueError):
            errors.append("image_decode_failed")
    if len(decoded) > 1:
        return {"status": "unavailable", "reason": "ambiguous_decoded_frames", "container": container}
    if not decoded:
        return {"status": "unavailable", "reason": errors[-1] if errors else "wxgf_stream_unavailable", "container": container}
    pixel_hash, (width, height, pixels) = next(iter(decoded.items()))
    # Keep standard images unchanged after validating that they fully decode.
    image = _rgb_png(width, height, pixels) if container == "wxgf" else data
    return {"status": "ready", "reason": "", "container": container, "width": width, "height": height,
            "pixel_sha256": pixel_hash, "content_sha256": _sha(image), "bytes": image,
            "extension": ".png" if container in ("wxgf", "png") else ".jpg"}



def _media_record(account: Path, gid: str, message: dict, output: Path, image_parameters=None) -> dict:
    record = {"message_id": message["id"], "group_id": gid, "server_id": message["server_id"], "local_id": message["local_id"],
              "sender_id": message.get("sender_id"), "time": message["time"], "path": None,
              "status": "unavailable", "reason": "exact_media_cache_missing"}
    md5 = message.get("details", {}).get("image_md5")
    if md5:
        record["image_md5"] = md5
    file_hash = message.get("details", {}).get("image_file_hash")
    if file_hash:
        record["image_file_hash"] = file_hash
    if not re.fullmatch(r"[0-9a-f]{24}", message["id"]):
        raise ValueError("invalid_image_message_id")
    candidates = _media_candidates(account, gid, message)
    attempts = []
    for candidate in candidates:
        path = candidate["source_path"]
        try:
            if path.stat().st_size > MAX_MEDIA_BYTES:
                decoded = {"status": "unavailable", "reason": "media_size_limit"}
            else:
                data = path.read_bytes()
                decoded = _decode_image(data, image_parameters)
        except OSError:
            decoded = {"status": "unavailable", "reason": "media_cache_read_failed"}
        if decoded["status"] == "ready":
            destination = output / (message["id"] + decoded.pop("extension"))
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(decoded.pop("bytes"))
            destination.chmod(0o600)
            record.update(decoded, path="media/" + destination.name,
                          binding=candidate["binding"], variant=candidate["variant"])
            return record
        attempts.append({"binding": candidate["binding"], "variant": candidate["variant"],
                         "reason": decoded["reason"],
                         "container": decoded.get("container")})
    if attempts:
        record.update(reason=attempts[-1]["reason"], attempts=attempts)
    return record



def extract_images(account, data, folder):
    """Populate source-bound image statuses before messages and review are saved."""
    from .core import dump, validate_messages
    validate_messages(data)
    folder = Path(folder)
    parameters = load_image_parameters(Path(account))
    records = []
    try:
        for message in data["messages"]:
            if message["type"] != "图片":
                continue
            record = _media_record(Path(account), data["metadata"]["group_id"], message,
                                   folder / "media", parameters)
            message["details"]["media"] = record
            if record["status"] == "ready":
                message["text"] = "[图片：已导出可读文件，内容需智能体实际查看]"
            records.append(record)
    finally:
        parameters = ()
    counts = {"total": len(records), "ready": sum(r["status"] == "ready" for r in records)}
    counts["unavailable"] = counts["total"] - counts["ready"]
    data["metadata"]["images"] = {**counts, "manifest": "media/manifest.json",
        "note": "ready 仅表示像素可读取，未自动识别图片内容；未下载或同步的图片可能不可用。"}
    (folder / "media").mkdir(exist_ok=True, mode=0o700)
    dump(folder / "media/manifest.json", {**counts, "messages": records})
    (folder / "media/manifest.json").chmod(0o600)


def image_source_html(folder, message):
    """Embed a validated local image, so the HTML remains a single offline file."""
    media = message.get("details", {}).get("media")
    if not media:
        return ""
    if media.get("status") != "ready":
        return '<p>图片未读取：' + html.escape(str(media.get("reason", "unknown"))) + '</p>'
    relative = media.get("path", "")
    expected = "media/" + str(message["id"])
    if relative not in (expected + ".png", expected + ".jpg"):
        raise ValueError("图片来源路径与消息 ID 不匹配")
    root = Path(folder).resolve()
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root / "media"):
        raise ValueError("图片来源路径超出当前报告")
    if path.stat().st_size > MAX_MEDIA_BYTES:
        raise ValueError("图片来源超过大小限制")
    content = path.read_bytes()
    if _sha(content) != media.get("content_sha256"):
        raise ValueError("图片来源文件已改变，请重新导出")
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return ('<figure><img loading="lazy" style="max-width:100%;height:auto" alt="消息来源图片" src="data:'
            + mime + ';base64,' + base64.b64encode(content).decode("ascii")
            + '"><figcaption>本地图片 · ' + html.escape(str(media.get("variant", "")))
            + ' · 内容以实际查看为准</figcaption></figure>')
