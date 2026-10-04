"""Load stickers.toml and resolve sticker ids to local PNG/JPG/GIF paths."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.stickers.library import content_md5, normalize_sticker_id, truncate_description

_log = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})


@dataclass(frozen=True)
class StickerEntry:
    """One outbound sticker listed in stickers.toml or found in the stickers folder."""

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
        self._folder_stamp: tuple[tuple[str, float], ...] = ()
        self._by_id: dict[str, StickerEntry] = {}
        self._by_md5: dict[str, str] = {}

    def invalidate(self) -> None:
        """Force reload on the next lookup (after library upsert or folder drop)."""
        self._mtime = -1.0
        self._folder_stamp = ()

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

    def id_for_md5(self, digest: str) -> str:
        """Return the catalog sticker id for a content md5, or empty."""
        self._reload_if_changed()
        return self._by_md5.get((digest or "").strip().lower(), "")

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
            dropped = "·文件夹" if entry.source == "folder" else ""
            mark = learned or dropped
            desc = (entry.description or "").strip()
            tags = "、".join(entry.tags) if entry.tags else ""
            if desc:
                suffix = f"（{tags}{mark}）" if tags or mark else ""
                lines.append(
                    f"- {sticker_id} — {desc}{suffix} → [[sticker:{sticker_id}]]"
                )
            elif tags:
                lines.append(
                    f"- {sticker_id}（{tags}{mark}）→ [[sticker:{sticker_id}]]"
                )
            else:
                lines.append(f"- {sticker_id}{mark} → [[sticker:{sticker_id}]]")
        if not lines:
            return ""
        return (
            "【可用本地表情】这是 bot 程序能力（与当前人设无关）。"
            "入站/引用表情由程序评估是否入库；入库后出现在下面列表里，"
            "只在后续对话里你自己的语气需要时才选，不要把对方刚发的那张原样打回去"
            "（除非对方明确说发出来/发这个）。"
            "从列表按画面选 [[sticker:id]]（每轮最多 2 个）；禁止编造未列出的 id，不要把标记解释给用户看。\n"
            + "\n".join(lines)
        )

    def _reload_if_changed(self) -> None:
        """Reload when stickers.toml or files in the stickers folder change."""
        index_mtime = (
            self._index_path.stat().st_mtime if self._index_path.is_file() else -1.0
        )
        folder_stamp = _folder_stamp(self._stickers_dir)
        if index_mtime == self._mtime and folder_stamp == self._folder_stamp:
            return
        by_id, by_md5 = self._load_index_rows()
        _merge_folder_files(by_id, by_md5, self._stickers_dir)
        self._by_id = by_id
        self._by_md5 = by_md5
        self._mtime = index_mtime
        self._folder_stamp = folder_stamp
        _log.info(
            "loaded stickers index=%s dir=%s count=%s",
            self._index_path,
            self._stickers_dir,
            len(by_id),
        )

    def _load_index_rows(self) -> tuple[dict[str, StickerEntry], dict[str, str]]:
        """Parse stickers.toml rows that still have files on disk."""
        by_id: dict[str, StickerEntry] = {}
        by_md5: dict[str, str] = {}
        if not self._index_path.is_file():
            return by_id, by_md5
        try:
            data = tomllib.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            _log.exception("failed to load stickers index %s", self._index_path)
            return by_id, by_md5
        rows = data.get("sticker") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = []
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
            if not digest:
                try:
                    digest = content_md5(path.read_bytes())
                except OSError:
                    digest = ""
            description = truncate_description(str(row.get("description") or ""))
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
        return by_id, by_md5


def _folder_stamp(stickers_dir: Path) -> tuple[tuple[str, float], ...]:
    """Snapshot of image filenames and mtimes so dropped files trigger a reload."""
    if not stickers_dir.is_dir():
        return ()
    items: list[tuple[str, float]] = []
    for path in stickers_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        try:
            items.append((path.name, path.stat().st_mtime))
        except OSError:
            continue
    return tuple(sorted(items))


def _merge_folder_files(
    by_id: dict[str, StickerEntry],
    by_md5: dict[str, str],
    stickers_dir: Path,
) -> None:
    """Add PNG/JPG/GIF files that are not already listed in stickers.toml."""
    if not stickers_dir.is_dir():
        return
    listed = {entry.path.resolve() for entry in by_id.values()}
    for path in sorted(stickers_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _ALLOWED_SUFFIXES:
            continue
        resolved = path.resolve()
        if resolved in listed:
            continue
        try:
            digest = content_md5(path.read_bytes())
        except OSError:
            _log.warning("skip sticker file unreadable %s", path)
            continue
        if digest in by_md5:
            continue
        sticker_id = normalize_sticker_id(path.stem, digest)
        if sticker_id in by_id:
            sticker_id = f"s_{digest[:10]}"
        stem = path.stem.replace("_", " ").strip() or sticker_id
        by_id[sticker_id] = StickerEntry(
            id=sticker_id,
            path=resolved,
            tags=(),
            md5=digest,
            description=truncate_description(stem),
            source="folder",
        )
        by_md5[digest] = sticker_id
        listed.add(resolved)
        _log.info("sticker folder scan id=%s file=%s", sticker_id, path.name)
