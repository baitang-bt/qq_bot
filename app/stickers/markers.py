"""Parse model replies into ordered text / sticker segments."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

_log = logging.getLogger(__name__)

# Canonical marker, plus the memory-form models often copy from chat history.
_MARKER = re.compile(
    r"(?:"
    r"\[\[\s*sticker\s*:\s*([A-Za-z0-9_\-]+)\s*\]\]"
    r"|"
    r"\[\s*发送表情\s*:\s*([A-Za-z0-9_\-]+)\s*\]"
    r")",
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


def drop_echoed_stickers(
    segments: list[ReplySegment],
    inbound_ids: set[str] | frozenset[str] | tuple[str, ...],
) -> list[ReplySegment]:
    """Remove outbound stickers that copy this turn's inbound sticker ids."""
    blocked = {item.strip().lower() for item in inbound_ids if item and item.strip()}
    if not blocked:
        return segments
    kept: list[ReplySegment] = []
    for seg in segments:
        if isinstance(seg, StickerSeg) and seg.sticker_id.lower() in blocked:
            _log.info("drop echoed inbound sticker id=%s", seg.sticker_id)
            continue
        kept.append(seg)
    return _merge_adjacent_text(kept)


def memory_text_for_segments(segments: list[ReplySegment]) -> str:
    """Serialize segments for short-term memory using canonical [[sticker:id]]."""
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, TextSeg):
            if seg.text.strip():
                parts.append(seg.text.strip())
        else:
            # Keep the same marker the model is taught, so history does not teach
            # a different "[发送表情:…]" form that later leaks as plain text.
            parts.append(f"[[sticker:{seg.sticker_id}]]")
    return "\n".join(parts).strip()


def _normalize_reply(text: str) -> str:
    """Map fullwidth brackets to ASCII and strip hidden format chars so markers match."""
    mapped = (text or "").translate(_BRACKET_MAP)
    cleaned = "".join(ch for ch in mapped if unicodedata.category(ch) != "Cf")
    return cleaned.strip()


def _marker_sticker_id(match: re.Match[str]) -> str:
    """Return the id from either capture group of _MARKER."""
    return (match.group(1) or match.group(2) or "").strip().lower()


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
        sticker_id = _marker_sticker_id(match)
        if not sticker_id:
            pos = match.end()
            continue
        if allowed is not None and sticker_id not in allowed:
            _log.warning("unknown sticker marker id=%s dropped", sticker_id)
        else:
            if match.group(2):
                _log.info(
                    "accepted legacy memory sticker marker id=%s",
                    sticker_id,
                )
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
