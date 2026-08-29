"""Split model output into multiple chat bubbles when the model chooses."""

from __future__ import annotations

_SPLIT_MARKER = "\n---\n"
_MAX_BUBBLES = 4
_MIN_BUBBLE_CHARS = 1


def split_reply_bubbles(text: str, max_bubbles: int = _MAX_BUBBLES) -> list[str]:
    """Return one or more non-empty bubbles from a model reply."""
    raw = (text or "").strip()
    if not raw:
        return []
    cap = max(1, max_bubbles)
    if _SPLIT_MARKER not in raw:
        return [raw]
    parts = [part.strip() for part in raw.split(_SPLIT_MARKER) if part.strip()]
    if len(parts) <= 1:
        return [raw]
    trimmed = [part for part in parts[:cap] if len(part) >= _MIN_BUBBLE_CHARS]
    return trimmed or [raw]
