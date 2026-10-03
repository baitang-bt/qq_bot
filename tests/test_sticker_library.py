"""Sticker library save/dedupe and identify auto-learn path."""

from __future__ import annotations

import asyncio
import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.config import Settings
from app.llm.client import StickerTriage
from app.qq.events import Attachment
from app.stickers.catalog import StickerCatalog
from app.stickers.library import StickerLibrary, content_md5, normalize_sticker_id
from app.vision.cache import ImageCache
from app.vision.identify import ImageIdentifier


def _png_bytes(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    """Encode a tiny PNG into memory for library tests."""
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _settings(tmp_path: Path, *, auto_learn: bool = True) -> Settings:
    """Minimal settings for library / identify learn tests."""
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
        sticker_auto_learn=auto_learn,
        sticker_learn_max=200,
    )


def test_normalize_sticker_id_fallback() -> None:
    """Illegal ids become s_ + md5 prefix."""
    digest = "abcdef0123456789"
    assert normalize_sticker_id("Bad ID!!!", digest) == f"s_{digest[:10]}"
    assert normalize_sticker_id("facepalm_cat", digest) == "facepalm_cat"


def test_save_sticker_writes_description_and_dedupes(tmp_path: Path) -> None:
    """First save writes file+toml description; same md5 is skipped."""
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index, max_learned=10)
    data = _png_bytes()
    entry = library.save_sticker(
        data,
        "image/png",
        sticker_id="facepalm_cat",
        description="一只猫捂脸，无奈表情",
        tags=["捂脸", "无奈"],
    )
    assert entry is not None
    assert entry.description == "一只猫捂脸，无奈表情"
    assert (stickers / entry.file).is_file()
    text = index.read_text(encoding="utf-8")
    assert 'description = "一只猫捂脸，无奈表情"' in text
    assert f'md5 = "{content_md5(data)}"' in text

    again = library.save_sticker(
        data,
        "image/png",
        sticker_id="other_id",
        description="重复不应写入",
        tags=["x"],
    )
    assert again is None
    assert library.learned_count() == 1


def test_save_sticker_rejects_empty_description(tmp_path: Path) -> None:
    """Empty description never lands on disk."""
    library = StickerLibrary(tmp_path / "stickers", tmp_path / "stickers.toml")
    assert (
        library.save_sticker(
            _png_bytes(),
            "image/png",
            sticker_id="empty_desc",
            description="   ",
        )
        is None
    )


def test_library_description_hit_skips_triage(tmp_path: Path) -> None:
    """Known local md5 returns toml description without calling triage."""
    data = _png_bytes((1, 2, 3))
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index)
    library.save_sticker(
        data,
        "image/png",
        sticker_id="facepalm_cat",
        description="本地描述缓存",
        tags=["捂脸"],
    )
    catalog = StickerCatalog(index, stickers)
    triage = AsyncMock(
        return_value=StickerTriage(description="不应调用", save=False)
    )
    cache = ImageCache(tmp_path / "bot.sqlite3")
    vision = ImageIdentifier(
        _settings(tmp_path),
        cache,
        triage=triage,
        library=library,
        catalog=catalog,
        auto_learn=True,
    )
    sticker = Attachment(
        url="https://x.example/download?fileid=FID",
        filename="a.gif",
        content_type="image/gif",
        size=1,
    )

    async def fake_download(_attachment):
        return data, "image/png"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["本地描述缓存"]
    triage.assert_not_awaited()
    assert cache.get([f"md5:{hashlib.md5(data).hexdigest()}"]) == "本地描述缓存"


def test_triage_save_false_does_not_write_file(tmp_path: Path) -> None:
    """save=false still caches description but does not upsert the library."""
    data = _png_bytes((9, 9, 9))
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index)
    catalog = StickerCatalog(index, stickers)
    triage = AsyncMock(
        return_value=StickerTriage(
            description="一次性梗图",
            save=False,
            sticker_id="skip_me",
            reason="once",
        )
    )
    cache = ImageCache(tmp_path / "bot.sqlite3")
    vision = ImageIdentifier(
        _settings(tmp_path),
        cache,
        triage=triage,
        library=library,
        catalog=catalog,
        auto_learn=True,
    )
    sticker = Attachment(
        url="https://x.example/download?fileid=FID2",
        filename="b.gif",
        content_type="image/gif",
        size=1,
    )

    async def fake_download(_attachment):
        return data, "image/png"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["一次性梗图"]
    assert library.learned_count() == 0
    assert list(stickers.glob("*.png")) == []
    assert cache.get(["fileid:FID2"]) == "一次性梗图"


def test_triage_save_true_learns_and_dual_writes(tmp_path: Path) -> None:
    """save=true writes file, toml description, and sticker:/md5: cache keys."""
    data = _png_bytes((4, 5, 6))
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index)
    catalog = StickerCatalog(index, stickers)
    refreshed: list[str] = []
    triage = AsyncMock(
        return_value=StickerTriage(
            description="通用捂脸猫",
            save=True,
            sticker_id="facepalm_cat",
            tags=("捂脸",),
            reason="通用反应",
        )
    )
    cache = ImageCache(tmp_path / "bot.sqlite3")
    vision = ImageIdentifier(
        _settings(tmp_path),
        cache,
        triage=triage,
        library=library,
        catalog=catalog,
        auto_learn=True,
        on_learned=lambda: refreshed.append("ok"),
    )
    sticker = Attachment(
        url="https://x.example/download?fileid=FID3",
        filename="c.gif",
        content_type="image/gif",
        size=1,
    )

    async def fake_download(_attachment):
        return data, "image/png"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["通用捂脸猫"]
    assert library.learned_count() == 1
    digest = content_md5(data)
    assert cache.get([f"md5:{digest}"]) == "通用捂脸猫"
    assert cache.get(["sticker:facepalm_cat"]) == "通用捂脸猫"
    catalog.invalidate()
    assert catalog.description_for_md5(digest) == "通用捂脸猫"
    assert "通用捂脸猫" in catalog.prompt_block()
    assert refreshed == ["ok"]


def _animated_gif() -> bytes:
    """Two-frame GIF used as a fake QQ attachment body."""
    buf = BytesIO()
    first = Image.new("RGB", (8, 8), (200, 0, 0))
    second = Image.new("RGB", (8, 8), (0, 200, 0))
    first.save(
        buf,
        format="GIF",
        save_all=True,
        append_images=[second],
        duration=50,
        loop=0,
    )
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    """Tiny JPEG for still-save tests."""
    buf = BytesIO()
    Image.new("RGB", (8, 8), (11, 22, 33)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class _GifHttpClient:
    """httpx.AsyncClient stand-in that returns a GIF regardless of declared type."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __call__(self, *args, **kwargs):
        """Match AsyncClient(...) construction."""
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        """Return the canned GIF with an image/gif content-type header."""

        class _Resp:
            content = self._body
            headers = {"content-type": "image/gif"}

            def raise_for_status(self) -> None:
                return None

        return _Resp()


def test_download_does_not_flatten_gif(tmp_path: Path) -> None:
    """Declared image/gif stays raw GIF8 bytes; fetch_images still sends a PNG frame."""
    gif = _animated_gif()
    vision = ImageIdentifier(_settings(tmp_path), None)
    sticker = Attachment(
        url="https://x.example/download?fileid=GIFRAW",
        filename="dance.gif",
        content_type="image/gif",
        size=len(gif),
    )
    client = _GifHttpClient(gif)
    with patch("app.vision.identify.httpx.AsyncClient", client):
        data, mime = asyncio.run(vision._download(sticker))
        frames = asyncio.run(vision.fetch_images((sticker,)))
    assert mime == "image/gif"
    assert data.startswith(b"GIF8")
    assert data == gif
    assert frames[0][1] == "image/png"
    assert frames[0][0].startswith(b"\x89PNG")


def test_learn_animated_gif_writes_gif_file(tmp_path: Path) -> None:
    """Auto-learn persists animation as .gif; triage only sees a still PNG."""
    gif = _animated_gif()
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index)
    catalog = StickerCatalog(index, stickers)
    triage = AsyncMock(
        return_value=StickerTriage(
            description="跳舞小人",
            save=True,
            sticker_id="dance_loop",
            tags=("动图",),
            reason="通用反应",
        )
    )
    cache = ImageCache(tmp_path / "bot.sqlite3")
    vision = ImageIdentifier(
        _settings(tmp_path),
        cache,
        triage=triage,
        library=library,
        catalog=catalog,
        auto_learn=True,
    )
    sticker = Attachment(
        url="https://x.example/download?fileid=DANCE",
        filename="dance.gif",
        content_type="image/gif",
        size=len(gif),
    )

    async def fake_download(_attachment):
        return gif, "image/gif"

    with patch.object(vision, "_download", side_effect=fake_download):
        notes = asyncio.run(vision.resolve_sticker_notes((sticker,)))

    assert notes == ["跳舞小人"]
    payload, mime = triage.await_args.args
    assert mime == "image/png"
    assert payload.startswith(b"\x89PNG")
    dest = stickers / "dance_loop.gif"
    assert dest.is_file()
    assert dest.read_bytes().startswith(b"GIF8")
    assert dest.read_bytes() == gif


def test_learn_jpeg_keeps_jpeg(tmp_path: Path) -> None:
    """True JPEG bytes stay JPEG; we do not transcode to PNG."""
    jpeg = _jpeg_bytes()
    stickers = tmp_path / "stickers"
    index = tmp_path / "stickers.toml"
    library = StickerLibrary(stickers, index)
    entry = library.save_sticker(
        jpeg,
        "image/jpeg",
        sticker_id="still_face",
        description="静态脸",
    )
    assert entry is not None
    assert entry.file.endswith(".jpg")
    path = stickers / entry.file
    assert path.read_bytes() == jpeg
