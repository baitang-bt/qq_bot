"""Sticker / image cache key extraction and sticker resolution."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.qq.events import Attachment
from app.vision.cache import ImageCache
from app.vision.identify import ImageIdentifier, prefer_original_media_url
from app.vision.keys import (
    extract_fileid,
    extract_hex_filename,
    is_sticker,
    keys_from_attachment,
)


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


def test_image_cache_skips_empty_put(tmp_path) -> None:
    """Empty / failure-like blank descriptions must not be written."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    cache.put(["fileid:X"], "   ", source="test")
    assert cache.get(["fileid:X"]) is None


def test_image_cache_purge_bad_descriptions(tmp_path) -> None:
    """Legacy failure strings are removed so multimodal turns are not poisoned."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    cache.put(["fileid:bad"], "图没看清", source="legacy")
    cache.put(["fileid:ok"], "一只猫", source="legacy")
    removed = cache.purge_descriptions(["图没看清", "没看清这张图"])
    assert removed == 1
    assert cache.get(["fileid:bad"]) is None
    assert cache.get(["fileid:ok"]) == "一只猫"


def test_is_sticker_gif_and_hex() -> None:
    """gif/webp or hex filenames count as stickers; plain photos do not."""
    gif = Attachment(
        url="https://x.example/a",
        filename="a.gif",
        content_type="image/gif",
        size=1,
    )
    hex_png = Attachment(
        url="https://x.example/download?fileid=FID",
        filename="0123456789abcdef0123456789abcdef.png",
        content_type="image/png",
        size=1,
    )
    photo = Attachment(
        url="https://x.example/download?fileid=FID2",
        filename="photo.jpg",
        content_type="image/jpeg",
        size=1,
    )
    assert is_sticker(gif) is True
    assert is_sticker(hex_png) is True
    assert is_sticker(photo) is False


def _settings(tmp_path) -> Settings:
    """Minimal settings for ImageIdentifier unit tests."""
    return Settings(
        qq_app_id="",
        qq_app_secret="",
        qq_api_base="https://example.com",
        qq_token_url="https://example.com/token",
        llm_base_url="https://example.com/v1",
        llm_api_key="test-key",
        llm_model="deepseek-flash",
        vision_model="deepseek-flash",
        llm_timeout_seconds=25.0,
        vision_timeout_seconds=20.0,
        memory_max_turns=12,
        data_dir=tmp_path,
        reply_policy_path=tmp_path / "reply_policy.toml",
        bot_prompt_path=tmp_path / "bot_prompt.json",
        personas_dir=tmp_path / "personas",
        personas_index_path=tmp_path / "personas.toml",
        qq_id="",
        host="127.0.0.1",
        port=8080,
        coalesce_burst_seconds=30.0,
        coalesce_debounce_seconds=3.0,
        coalesce_single_debounce_seconds=3.0,
        stickers_dir=tmp_path / "stickers",
        stickers_index_path=tmp_path / "stickers.toml",
        sticker_auto_learn=False,
        sticker_learn_max=200,
    )


def test_resolve_sticker_notes_cache_hit_skips_describe(tmp_path) -> None:
    """Cached stickers never call the multimodal describe callback."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    cache.put(["fileid:FID"], "捂脸猫", source="test")
    describe = AsyncMock(return_value="should-not-run")
    vision = ImageIdentifier(_settings(tmp_path), cache, describe=describe)
    sticker = Attachment(
        url="https://x.example/download?fileid=FID",
        filename="a.gif",
        content_type="image/gif",
        size=1,
    )
    notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))
    assert notes == ["捂脸猫"]
    describe.assert_not_awaited()


def test_resolve_sticker_notes_miss_describes_and_puts(tmp_path) -> None:
    """Cache miss downloads, multimodal-describes, then stores the description."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    describe = AsyncMock(return_value="一只猫在捂脸")
    vision = ImageIdentifier(_settings(tmp_path), cache, describe=describe)
    sticker = Attachment(
        url="https://x.example/download?fileid=FID",
        filename="a.gif",
        content_type="image/gif",
        size=1,
    )

    async def fake_download(_attachment):
        return b"gifbytes", "image/jpeg"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["一只猫在捂脸"]
    describe.assert_awaited_once()
    assert cache.get(["fileid:FID"]) == "一只猫在捂脸"


def test_resolve_sticker_notes_failure_not_cached(tmp_path) -> None:
    """Failed describe text is returned for the turn but not written to cache."""
    cache = ImageCache(tmp_path / "bot.sqlite3")
    describe = AsyncMock(return_value="图没看清")
    vision = ImageIdentifier(_settings(tmp_path), cache, describe=describe)
    sticker = Attachment(
        url="https://x.example/download?fileid=FID",
        filename="a.gif",
        content_type="image/gif",
        size=1,
    )

    async def fake_download(_attachment):
        return b"gifbytes", "image/jpeg"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["图没看清"]
    assert cache.get(["fileid:FID"]) is None


def test_prefer_original_media_url_sets_spec_zero() -> None:
    """QQ thumb spec is rewritten to spec=0 so the original file is fetched."""
    url = "https://multimedia.nt.qq.com.cn/download?appid=1&fileid=Ab&spec=1&rkey=x"
    out = prefer_original_media_url(url)
    assert "spec=0" in out
    assert "spec=1" not in out
    assert prefer_original_media_url("https://example.com/a.gif") == "https://example.com/a.gif"
