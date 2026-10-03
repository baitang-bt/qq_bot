"""Load stickers.toml and resolve sticker ids to local PNG/JPG paths."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})


@dataclass(frozen=True)
class StickerEntry:
    """One outbound sticker listed in stickers.toml."""

    id: str
    path: Path
    tags: tuple[str, ...]
    md5: str = ""
    description: str = ""
    source: str = ""


class StickerCatalog:
    """Hot-reloadable map from sticker id to a local image file."""

    def __init__(self, index_path: Path, stickers_dir: Path) -> None:
        self._index_path = index_path
        self._stickers_dir = stickers_dir
        self._mtime = -1.0
        self._by_id: dict[str, StickerEntry] = {}
        self._by_md5: dict[str, str] = {}

    def invalidate(self) -> None:
        """Force reload on the next lookup (after library upsert)."""
        self._mtime = -1.0

    def path_for(self, sticker_id: str) -> Path | None:
        """Return the absolute image path for an id, or None if unknown/missing."""
        self._reload_if_changed()
        key = (sticker_id or "").strip().lower()
        entry = self._by_id.get(key)
        if entry is None:
            return None
        if not entry.path.is_file():
            _log.warning("sticker file missing id=%s path=%s", entry.id, entry.path)
            return None
        return entry.path

    def has_md5(self, digest: str) -> bool:
        """True when a sticker with this content hash is already in the catalog."""
        self._reload_if_changed()
        return (digest or "").strip().lower() in self._by_md5

    def description_for_md5(self, digest: str) -> str:
        """Return the cached description for a content md5, or empty."""
        self._reload_if_changed()
        sticker_id = self._by_md5.get((digest or "").strip().lower(), "")
        if not sticker_id:
            return ""
        return self.description_for_id(sticker_id)

    def description_for_id(self, sticker_id: str) -> str:
        """Return the cached description for a sticker id, or empty."""
        self._reload_if_changed()
        entry = self._by_id.get((sticker_id or "").strip().lower())
        if entry is None:
            return ""
        return (entry.description or "").strip()

    def known_ids(self) -> list[str]:
        """Return sorted sticker ids that currently resolve to files on disk."""
        self._reload_if_changed()
        return sorted(
            entry.id for entry in self._by_id.values() if entry.path.is_file()
        )

    def prompt_block(self) -> str:
        """Build a short system-prompt section listing available sticker markers."""
        self._reload_if_changed()
        lines: list[str] = []
        for sticker_id in self.known_ids():
            entry = self._by_id[sticker_id]
            learned = "·学来的" if entry.source == "learned" else ""
            desc = (entry.description or "").strip()
            tags = "、".join(entry.tags) if entry.tags else ""
            if desc:
                suffix = f"（{tags}{learned}）" if tags or learned else ""
                lines.append(
                    f"- {sticker_id} — {desc}{suffix} → [[sticker:{sticker_id}]]"
                )
            elif tags:
                lines.append(
                    f"- {sticker_id}（{tags}{learned}）→ [[sticker:{sticker_id}]]"
                )
            else:
                lines.append(f"- {sticker_id}{learned} → [[sticker:{sticker_id}]]")
        if not lines:
            return ""
        return (
            "【可用本地表情（偶尔用，每轮最多 2 个；按描述选 id；含学来的条目；禁止编造未列出的 id）】\n"
            + "\n".join(lines)
        )

    def _reload_if_changed(self) -> None:
        """Reload the toml index when it appears or its mtime changes."""
        if not self._index_path.is_file():
            self._by_id = {}
            self._by_md5 = {}
            self._mtime = -1.0
            return
        mtime = self._index_path.stat().st_mtime
        if mtime == self._mtime:
            return
        try:
            data = tomllib.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            _log.exception("failed to load stickers index %s", self._index_path)
            return
        rows = data.get("sticker") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = []
        by_id: dict[str, StickerEntry] = {}
        by_md5: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            sticker_id = str(row.get("id") or "").strip().lower()
            filename = str(row.get("file") or "").strip()
            if not sticker_id or not filename:
                continue
            path = (self._stickers_dir / filename).resolve()
            if path.suffix.lower() not in _ALLOWED_SUFFIXES:
                _log.warning(
                    "skip sticker id=%s unsupported suffix %s",
                    sticker_id,
                    path.suffix,
                )
                continue
            if not path.is_file():
                _log.warning("skip sticker id=%s missing file %s", sticker_id, path)
                continue
            tags_raw = row.get("tags") or []
            tags = tuple(
                str(item).strip()
                for item in tags_raw
                if isinstance(tags_raw, list) and str(item).strip()
            )
            digest = str(row.get("md5") or "").strip().lower()
            description = " ".join(str(row.get("description") or "").split())
            if len(description) > 80:
                description = description[:79].rstrip() + "…"
            source = str(row.get("source") or "").strip()
            by_id[sticker_id] = StickerEntry(
                id=sticker_id,
                path=path,
                tags=tags,
                md5=digest,
                description=description,
                source=source,
            )
            if digest:
                by_md5[digest] = sticker_id
        self._by_id = by_id
        self._by_md5 = by_md5
        self._mtime = mtime
        _log.info(
            "loaded stickers index=%s count=%s",
            self._index_path,
            len(by_id),
        )
