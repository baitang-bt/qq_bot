"""Sticker / image cache key extraction."""

from app.qq.events import Attachment
from app.vision.cache import ImageCache
from app.vision.keys import extract_fileid, extract_hex_filename, keys_from_attachment


def test_fileid_from_query() -> None:
    """fileid query param is the primary pre-download cache key."""
    url = "https://gchat.qpic.cn/download?appid=1&fileid=AbCd123&rkey=secret"
    assert extract_fileid(url) == "AbCd123"


def test_hex_filename() -> None:
    """MD5-looking filenames are usable sticker identities."""
    assert extract_hex_filename("0123456789abcdef0123456789abcdef.gif") == (
        "0123456789abcdef0123456789abcdef"
    )
    assert extract_hex_filename("photo.jpg") == ""


def test_keys_from_attachment_skips_rkey() -> None:
    """The full signed URL must not become a cache key."""
    attachment = Attachment(
        url="https://x.example/download?fileid=FID&rkey=DONTUSE",
        filename="0123456789abcdef0123456789abcdef.png",
        content_type="image/png",
        size=1,
    )
    keys = keys_from_attachment(attachment)
    assert keys == [
        "fileid:FID",
        "hex:0123456789abcdef0123456789abcdef",
    ]


def test_image_cache_roundtrip(tmp_path) -> None:
    """Same description is returned for any stored key."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    cache.put(["fileid:FID", "md5:abc"], "一只猫的表情包", source="test")
    assert cache.get(["fileid:FID"]) == "一只猫的表情包"
    assert cache.get(["md5:abc"]) == "一只猫的表情包"
    assert cache.get(["fileid:missing"]) is None
