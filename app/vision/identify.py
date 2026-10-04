"""Download images and resolve sticker descriptions via cache, library, or triage."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeAlias
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import Settings
from app.llm.client import StickerTriage
from app.qq.events import Attachment
from app.stickers.format import frame_for_model, sniffed_mime
from app.stickers.library import content_md5, normalize_sticker_id
from app.vision.cache import ImageCache
from app.vision.keys import is_sticker, keys_from_attachment, md5_key

if TYPE_CHECKING:
    from app.stickers.catalog import StickerCatalog
    from app.stickers.library import StickerLibrary

_log = logging.getLogger(__name__)

DescribeFn: TypeAlias = Callable[[bytes, str], Awaitable[str]]
TriageFn: TypeAlias = Callable[[bytes, str], Awaitable[StickerTriage]]
LearnedFn: TypeAlias = Callable[[], None]

# Failure strings that must never stay in image_cache.
_BAD_CACHE_DESCRIPTIONS = frozenset(
    {
        "图没看清",
        "没看清这张图",
        "收到一张图（未配置视觉模型）",
        "图里没什么能说的",
    }
)
_MISS_NOTE = "图没看清"


class ImageIdentifier:
    """Fetch photo bytes for multimodal chat; resolve sticker notes via cache/describe."""

    def __init__(
        self,
        settings: Settings,
        cache: ImageCache | None = None,
        describe: DescribeFn | None = None,
        *,
        triage: TriageFn | None = None,
        library: StickerLibrary | None = None,
        catalog: StickerCatalog | None = None,
        auto_learn: bool | None = None,
        on_learned: LearnedFn | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self._describe = describe
        self._triage = triage
        self._library = library
        self._catalog = catalog
        self._auto_learn = (
            settings.sticker_auto_learn if auto_learn is None else auto_learn
        )
        self._on_learned = on_learned
        if self._cache is not None:
            removed = self._cache.purge_descriptions(_BAD_CACHE_DESCRIPTIONS)
            if removed:
                _log.info("purged %s stale vision-cache rows", removed)

    def set_describe(self, describe: DescribeFn) -> None:
        """Attach the multimodal describe callback after LLMClient is constructed."""
        self._describe = describe

    def set_triage(self, triage: TriageFn) -> None:
        """Attach the sticker describe+triage callback after LLMClient is constructed."""
        self._triage = triage

    @staticmethod
    def partition(
        attachments: tuple[Attachment, ...],
    ) -> tuple[tuple[Attachment, ...], tuple[Attachment, ...]]:
        """Split attachments into (stickers, photos)."""
        stickers: list[Attachment] = []
        photos: list[Attachment] = []
        for item in attachments:
            if is_sticker(item):
                stickers.append(item)
            else:
                photos.append(item)
        return tuple(stickers), tuple(photos)

    async def fetch_images(
        self,
        attachments: tuple[Attachment, ...],
    ) -> list[tuple[bytes, str]]:
        """Download each image attachment; skip failures and return (bytes, mime) pairs."""
        out: list[tuple[bytes, str]] = []
        for attachment in attachments:
            data, mime = await self._download(attachment)
            if not data:
                _log.warning(
                    "image download empty filename=%s url=%s",
                    attachment.filename,
                    (attachment.url or "")[:120],
                )
                continue
            frame, frame_mime = frame_for_model(data)
            out.append((frame, frame_mime))
        return out

    async def resolve_sticker_notes(
        self,
        attachments: tuple[Attachment, ...],
        *,
        force_save: bool = False,
    ) -> list[str]:
        """Return sticker descriptions: cache/library hit skips vision; miss triages."""
        notes: list[str] = []
        for attachment in attachments:
            note = await self._resolve_one_sticker(attachment, force_save=force_save)
            if note:
                notes.append(note)
        return notes

    async def _resolve_one_sticker(
        self,
        attachment: Attachment,
        *,
        force_save: bool = False,
    ) -> str:
        """Resolve one sticker: library/catalog → image_cache → triage → optional learn."""
        keys = keys_from_attachment(attachment)
        if not force_save and self._cache is not None and keys:
            cached = self._cache.get(keys)
            if cached:
                _log.info("sticker cache hit keys=%s", keys[:2])
                return cached

        data, mime = await self._download(attachment)
        if not data:
            _log.warning(
                "sticker download empty filename=%s url=%s",
                attachment.filename,
                (attachment.url or "")[:120],
            )
            return _MISS_NOTE

        digest = content_md5(data)
        local_desc = self._local_description(digest)
        if local_desc:
            all_keys = self._merge_keys(keys, data)
            if self._cache is not None:
                self._cache.put(all_keys, local_desc, source="library")
            _log.info(
                "sticker library/catalog hit md5=%s chars=%s",
                digest[:10],
                len(local_desc),
            )
            return self._note_with_marker(digest, local_desc)

        all_keys = self._merge_keys(keys, data)
        cached = ""
        if self._cache is not None:
            cached = (self._cache.get(all_keys) or "").strip()
        already_saved = self._already_saved(digest)
        if cached and not (force_save and not already_saved):
            _log.info("sticker cache hit after download keys=%s", all_keys[:2])
            self._cache.put(all_keys, cached, source="backfill")
            return self._note_with_marker(digest, cached)

        if cached and force_save:
            triage = StickerTriage(
                description=cached,
                save=True,
                reason="user_requested",
            )
            desc = cached
        else:
            triage = await self._run_triage(*frame_for_model(data))
            desc = (triage.description or "").strip()
            if not desc or desc in _BAD_CACHE_DESCRIPTIONS:
                return desc or _MISS_NOTE

        if self._cache is not None and all_keys:
            self._cache.put(all_keys, desc, source="vision")
            _log.info("sticker cache put keys=%s chars=%s", all_keys[:2], len(desc))

        if (
            self._auto_learn
            and (triage.save or force_save)
            and self._library is not None
            and not self._library.has_md5(digest)
        ):
            self._try_learn(data, mime, digest, triage, desc)

        return self._note_with_marker(digest, desc)

    def _note_with_marker(self, digest: str, desc: str) -> str:
        """Append a sendable [[sticker:id]] when this image is already in the library."""
        sticker_id = ""
        if self._catalog is not None:
            sticker_id = self._catalog.id_for_md5(digest)
        if not sticker_id and self._library is not None:
            sticker_id = self._library.id_for_md5(digest)
        if sticker_id:
            return f"{desc} → 可发 [[sticker:{sticker_id}]]"
        return desc

    def _already_saved(self, digest: str) -> bool:
        """True when catalog or library already has this content hash."""
        if self._catalog is not None and self._catalog.has_md5(digest):
            return True
        if self._library is not None and self._library.has_md5(digest):
            return True
        return False

    def _local_description(self, digest: str) -> str:
        """Return a description from catalog or library for this content md5."""
        if self._catalog is not None:
            desc = self._catalog.description_for_md5(digest)
            if desc:
                return desc
        if self._library is not None:
            desc = self._library.description_for_md5(digest)
            if desc:
                return desc
        return ""

    def _merge_keys(self, keys: list[str], data: bytes) -> list[str]:
        """Combine pre-download keys with the content md5 key."""
        all_keys = list(keys)
        content_key = md5_key(data)
        if content_key not in all_keys:
            all_keys.append(content_key)
        return all_keys

    async def _run_triage(self, data: bytes, mime: str) -> StickerTriage:
        """Run describe+triage, falling back to describe-only when triage is unset."""
        if self._triage is not None:
            _log.info("sticker cache miss; multimodal triage")
            try:
                return await self._triage(data, mime)
            except Exception:
                _log.exception("sticker triage failed")
                return StickerTriage(description="", save=False, reason="triage_error")
        if self._describe is None:
            _log.warning("sticker describe callback missing")
            return StickerTriage(description="", save=False, reason="no_callback")
        _log.info("sticker cache miss; multimodal describe keys")
        try:
            desc = (await self._describe(data, mime)).strip()
        except Exception:
            _log.exception("sticker describe failed")
            return StickerTriage(description="", save=False, reason="describe_error")
        return StickerTriage(description=desc, save=False, reason="describe_only")

    def _try_learn(
        self,
        data: bytes,
        mime: str,
        digest: str,
        triage: StickerTriage,
        description: str,
    ) -> None:
        """Persist a worthwhile sticker to the local library and dual-write cache keys."""
        assert self._library is not None
        sticker_id = normalize_sticker_id(triage.sticker_id, digest)
        entry = self._library.save_sticker(
            data,
            mime,
            sticker_id=sticker_id,
            description=description,
            tags=triage.tags,
        )
        if entry is None:
            return
        extra_keys = [f"md5:{entry.md5}", f"sticker:{entry.id}"]
        if self._cache is not None:
            self._cache.put(extra_keys, entry.description, source="learned")
        if self._catalog is not None:
            self._catalog.invalidate()
        if self._on_learned is not None:
            try:
                self._on_learned()
            except Exception:
                _log.exception("sticker on_learned callback failed")
        _log.info(
            "sticker auto-learned id=%s md5=%s reason=%s",
            entry.id,
            entry.md5[:10],
            triage.reason or "-",
        )

    async def _download(self, attachment: Attachment) -> tuple[bytes, str]:
        """Fetch original attachment bytes; mime is sniffed from magic, not flattened."""
        try:
            url = prefer_original_media_url(attachment.url)
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    url,
                    headers={"User-Agent": "QQBot"},
                )
            response.raise_for_status()
            data = response.content
        except Exception:
            _log.exception("attachment download failed")
            return b"", "image/jpeg"
        mime = sniffed_mime(data)
        declared = (
            attachment.content_type or response.headers.get("content-type") or ""
        )
        declared = declared.split(";")[0].strip().lower()
        name = (attachment.filename or "").lower()
        if (
            name.endswith((".gif", ".webp")) or declared in {"image/gif", "image/webp"}
        ) and mime not in {"image/gif", "image/webp"}:
            _log.warning(
                "sticker download still after gif/webp hint filename=%s sniffed=%s declared=%s bytes=%s",
                attachment.filename,
                mime,
                declared or "-",
                len(data),
            )
        _log.info(
            "attachment fetched filename=%s sniffed=%s declared=%s bytes=%s",
            attachment.filename,
            mime,
            declared or "-",
            len(data),
        )
        return data, mime


def prefer_original_media_url(url: str) -> str:
    """Rewrite QQ download spec= to 0 so we fetch the original file, not a still thumb."""
    if not url:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key.lower() == "spec" for key, _ in pairs):
        return url
    rewritten = [
        ("spec", "0") if key.lower() == "spec" else (key, value)
        for key, value in pairs
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(rewritten), parts.fragment)
    )
