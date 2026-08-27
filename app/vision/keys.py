"""Build stable cache keys for QQ image / sticker attachments."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlparse

from app.qq.events import Attachment

_HEX32 = re.compile(r"^([0-9a-fA-F]{32})(?:\.[A-Za-z0-9]+)?$")


def extract_fileid(url: str) -> str:
    """Pull fileid from a QQ attachment download URL query string."""
    if not url:
        return ""
    query = parse_qs(urlparse(url).query)
    for key in ("fileid", "file_id", "fileId"):
        values = query.get(key) or []
        if values and values[0]:
            return values[0]
    return ""


def extract_hex_filename(filename: str) -> str:
    """Return a 32-char hex stem when filename looks like an MD5-named sticker."""
    match = _HEX32.match((filename or "").strip())
    return match.group(1).lower() if match else ""


def keys_from_attachment(attachment: Attachment) -> list[str]:
    """Keys available before download: fileid and hex filename."""
    keys: list[str] = []
    fileid = extract_fileid(attachment.url)
    if fileid:
        keys.append(f"fileid:{fileid}")
    hex_name = extract_hex_filename(attachment.filename)
    if hex_name:
        keys.append(f"hex:{hex_name}")
    return keys


def md5_key(data: bytes) -> str:
    """Content-addressed key used after the first download."""
    return f"md5:{hashlib.md5(data).hexdigest()}"


md5_key = md5_key
extract_fileid = extract_fileid
extract_hex_filename = extract_hex_filename
keys_from_attachment = keys_from_attachment
