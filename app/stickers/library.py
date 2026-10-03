"""Persist learned stickers to disk and upsert stickers.toml."""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.stickers.format import encode_for_library

_log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")
_ALLOWED = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})
_DESC_MAX = 80


@dataclass(frozen=True)
class LearnedSticker:
    """One sticker row written into the local library index."""

    id: str
    file: str
    tags: tuple[str, ...]
    md5: str
    description: str
    source: str = "learned"


def truncate_description(text: str, *, limit: int = _DESC_MAX) -> str:
    """Clamp a sticker description so prompt_block and toml stay compact."""
    body = " ".join((text or "").split())
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 1)].rstrip() + "…"


def normalize_sticker_id(raw: str, content_md5: str) -> str:
    """Return a safe sticker id, or a md5-derived fallback when invalid."""
    candidate = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if _ID_RE.match(candidate):
        return candidate
    return f"s_{content_md5[:10]}"


def content_md5(data: bytes) -> str:
    """Hex MD5 of raw image bytes (before optional re-encode)."""
    return hashlib.md5(data).hexdigest()


def encode_sticker_image(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """Persist inbound sticker bytes unchanged; suffix follows the file magic."""
    return encode_for_library(data, mime)


class StickerLibrary:
    """Write learned stickers under stickers_dir and keep stickers.toml in sync."""

    def __init__(
        self,
        stickers_dir: Path,
        index_path: Path,
        *,
        max_learned: int = 200,
    ) -> None:
        self._dir = stickers_dir
        self._index = index_path
        self._max_learned = max(0, max_learned)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._md5_index: dict[str, str] | None = None

    def invalidate(self) -> None:
        """Drop the in-memory md5 map so the next lookup reloads from toml/files."""
        self._md5_index = None

    def learned_count(self) -> int:
        """Count rows marked source=learned in the index (missing file still counts)."""
        rows = self._load_rows()
        return sum(1 for row in rows if str(row.get("source") or "") == "learned")

    def has_md5(self, digest: str) -> bool:
        """True when this content hash is already registered or present on disk."""
        digest = (digest or "").strip().lower()
        if not digest:
            return False
        mapping = self._ensure_md5_index()
        return digest in mapping

    def path_for_md5(self, digest: str) -> Path | None:
        """Return the file path for a known content md5, if any."""
        mapping = self._ensure_md5_index()
        filename = mapping.get((digest or "").strip().lower())
        if not filename:
            return None
        path = (self._dir / filename).resolve()
        return path if path.is_file() else None

    def description_for_md5(self, digest: str) -> str:
        """Return the stored description for a content md5, or empty."""
        digest = (digest or "").strip().lower()
        if not digest:
            return ""
        for row in self._load_rows():
            if str(row.get("md5") or "").strip().lower() != digest:
                continue
            return truncate_description(str(row.get("description") or ""))
        return ""

    def save_sticker(
        self,
        data: bytes,
        mime: str,
        *,
        sticker_id: str,
        description: str,
        tags: list[str] | tuple[str, ...] = (),
    ) -> LearnedSticker | None:
        """Write image bytes and upsert the toml index; None when skipped by caps/dup."""
        if not data:
            return None
        desc = truncate_description(description)
        if not desc:
            _log.warning("sticker library skip empty description id=%s", sticker_id)
            return None
        digest = content_md5(data)
        if self.has_md5(digest):
            _log.info("sticker library skip duplicate md5=%s", digest[:10])
            return None
        if self._max_learned and self.learned_count() >= self._max_learned:
            _log.warning(
                "sticker library full max=%s; skip save id=%s",
                self._max_learned,
                sticker_id,
            )
            return None

        safe_id = normalize_sticker_id(sticker_id, digest)
        encoded, suffix, _ = encode_sticker_image(data, mime)
        filename = f"{safe_id}{suffix}"
        dest = self._dir / filename
        if dest.is_file() and content_md5(dest.read_bytes()) != digest:
            filename = f"{safe_id}_{digest[:10]}{suffix}"
            dest = self._dir / filename
            safe_id = f"{safe_id}_{digest[:10]}"
            if not _ID_RE.match(safe_id):
                safe_id = f"s_{digest[:10]}"
                filename = f"{safe_id}{suffix}"
                dest = self._dir / filename

        dest.write_bytes(encoded)
        clean_tags = tuple(
            str(tag).strip() for tag in tags if str(tag).strip()
        )[:8]
        entry = LearnedSticker(
            id=safe_id,
            file=filename,
            tags=clean_tags,
            md5=digest,
            description=desc,
            source="learned",
        )
        self.upsert_index(entry)
        self.invalidate()
        _log.info(
            "sticker learned id=%s file=%s md5=%s tags=%s desc=%r",
            entry.id,
            entry.file,
            digest[:10],
            list(entry.tags),
            entry.description[:40],
        )
        return entry

    def upsert_index(self, entry: LearnedSticker) -> None:
        """Insert or replace a sticker row in stickers.toml by id."""
        rows = self._load_rows()
        replaced = False
        new_row = {
            "id": entry.id,
            "file": entry.file,
            "tags": list(entry.tags),
            "md5": entry.md5,
            "description": entry.description,
            "source": entry.source,
        }
        for index, row in enumerate(rows):
            if str(row.get("id") or "").strip().lower() == entry.id:
                rows[index] = new_row
                replaced = True
                break
        if not replaced:
            rows.append(new_row)
        self._write_rows(rows)
        # Bump mtime for catalog hot-reload even when content length unchanged.
        self._index.touch()

    def _ensure_md5_index(self) -> dict[str, str]:
        """Build md5 -> filename from toml rows and existing files."""
        if self._md5_index is not None:
            return self._md5_index
        mapping: dict[str, str] = {}
        for row in self._load_rows():
            digest = str(row.get("md5") or "").strip().lower()
            filename = str(row.get("file") or "").strip()
            if digest and filename:
                mapping[digest] = filename
        for path in self._dir.iterdir() if self._dir.is_dir() else []:
            if not path.is_file() or path.suffix.lower() not in _ALLOWED:
                continue
            try:
                digest = content_md5(path.read_bytes())
            except OSError:
                continue
            mapping.setdefault(digest, path.name)
        self._md5_index = mapping
        return mapping

    def _load_rows(self) -> list[dict]:
        """Load [[sticker]] tables from the index file."""
        if not self._index.is_file():
            return []
        try:
            data = tomllib.loads(self._index.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            _log.exception("failed to read stickers index %s", self._index)
            return []
        rows = data.get("sticker") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def _write_rows(self, rows: list[dict]) -> None:
        """Rewrite stickers.toml from row dicts (preserves learned + manual entries)."""
        lines = [
            "# Local outbound stickers. Files live under data/stickers/ by default.",
            "# Model replies may embed [[sticker:id]] to send one.",
            "# Entries with source = \"learned\" were auto-collected from inbound stickers.",
            "",
        ]
        for row in rows:
            sticker_id = str(row.get("id") or "").strip()
            filename = str(row.get("file") or "").strip()
            if not sticker_id or not filename:
                continue
            lines.append("[[sticker]]")
            lines.append(f'id = "{_toml_escape(sticker_id)}"')
            lines.append(f'file = "{_toml_escape(filename)}"')
            tags = row.get("tags") or []
            if isinstance(tags, list) and tags:
                rendered = ", ".join(
                    f'"{_toml_escape(str(tag).strip())}"'
                    for tag in tags
                    if str(tag).strip()
                )
                lines.append(f"tags = [{rendered}]")
            digest = str(row.get("md5") or "").strip()
            if digest:
                lines.append(f'md5 = "{_toml_escape(digest)}"')
            description = truncate_description(str(row.get("description") or ""))
            if description:
                lines.append(f'description = "{_toml_escape(description)}"')
            source = str(row.get("source") or "").strip()
            if source:
                lines.append(f'source = "{_toml_escape(source)}"')
            lines.append("")
        self._index.parent.mkdir(parents=True, exist_ok=True)
        self._index.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _toml_escape(value: str) -> str:
    """Escape a string for basic TOML double-quoted scalars."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
