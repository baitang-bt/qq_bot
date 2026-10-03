"""Parse model replies into ordered text / sticker segments."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# After NFKC, allow extra spaces inside the marker.
_MARKER = re.compile(
    r"\[\[\s*sticker\s*:\s*([A-Za-z0-9_\-]+)\s*\]\]",
    re.IGNORECASE,
)
_SPLIT_MARKER = "\n---\n"
_BRACKET_MAP = str.maketrans("［］【】", "[][]")


@dataclass(frozen=True)
class TextSeg:
    """A plain-text bubble fragment to send as msg_type=0."""

    text: str


@dataclass(frozen=True)
class StickerSeg:
    """A local sticker id to upload and send as msg_type=7."""

    sticker_id: str


ReplySegment = TextSeg | StickerSeg


def parse_reply_segments(
    text: str,
    *,
    known_ids: set[str] | frozenset[str] | None = None,
) -> list[ReplySegment]:
    """Split a model reply into text and sticker segments; drop unknown sticker ids."""
    raw = _normalize_reply(text)
    if not raw:
        return []
    allowed = {item.lower() for item in known_ids} if known_ids is not None else None
    segments: list[ReplySegment] = []
    bubbles = [part.strip() for part in raw.split(_SPLIT_MARKER) if part.strip()]
    if not bubbles:
        bubbles = [raw]
    for bubble in bubbles:
        segments.extend(_parse_one_bubble(bubble, allowed))
    return _merge_adjacent_text(segments)


def memory_text_for_segments(segments: list[ReplySegment]) -> str:
    """Serialize segments for short-term memory (stickers as text markers)."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSeg):
            if seg.text.strip():
                parts.append(seg.text.strip())
        else:
            parts.append(f"[发送表情:{seg.sticker_id}]")
    return "\n".join(parts).strip()


def _normalize_reply(text: str) -> str:
    """Map fullwidth brackets to ASCII and strip hidden format chars so markers match."""
    mapped = (text or "").translate(_BRACKET_MAP)
    cleaned = "".join(ch for ch in mapped if unicodedata.category(ch) != "Cf")
    return cleaned.strip()


def _parse_one_bubble(
    bubble: str,
    allowed: set[str] | None,
) -> list[ReplySegment]:
    """Parse sticker markers inside one text bubble; never keep the marker as user-visible text."""
    out: list[ReplySegment] = []
    pos = 0
    for match in _MARKER.finditer(bubble):
        before = bubble[pos : match.start()]
        if before.strip():
            out.append(TextSeg(text=before.strip()))
        sticker_id = match.group(1).strip().lower()
        if allowed is not None and sticker_id not in allowed:
            _log.warning("unknown sticker marker id=%s dropped", sticker_id)
        else:
            out.append(StickerSeg(sticker_id=sticker_id))
        pos = match.end()
    tail = bubble[pos:]
    if tail.strip():
        out.append(TextSeg(text=tail.strip()))
    return out


def _merge_adjacent_text(segments: list[ReplySegment]) -> list[ReplySegment]:
    """Collapse consecutive text segments separated only by dropped markers."""
    merged: list[ReplySegment] = []
    for seg in segments:
        if (
            isinstance(seg, TextSeg)
            and merged
            and isinstance(merged[-1], TextSeg)
        ):
            merged[-1] = TextSeg(text=f"{merged[-1].text}\n{seg.text}".strip())
        else:
            merged.append(seg)
    return merged
