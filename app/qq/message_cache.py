"""Recent msg_idx → text lookup when a quote event omits msg_elements."""

from __future__ import annotations

from collections import OrderedDict, deque

_MAX_ENTRIES = 2000
_MAX_BOT_TEXTS = 40

_cache: OrderedDict[str, str] = OrderedDict()
_bot_msg_idx: set[str] = set()
_bot_texts: dict[str, deque[str]] = {}


def remember(msg_idx: str, text: str) -> None:
    """Store one seen message body keyed by platform msg_idx (or message id)."""
    key = (msg_idx or "").strip()
    body = (text or "").strip()
    if not key or not body:
        return
    _cache[key] = body
    _cache.move_to_end(key)
    while len(_cache) > _MAX_ENTRIES:
        evicted = next(iter(_cache))
        _cache.pop(evicted, None)
        _bot_msg_idx.discard(evicted)


def remember_bot(
    msg_idx: str,
    text: str,
    *,
    group_openid: str | None,
    user_openid: str,
    alt_id: str = "",
) -> None:
    """Mark an outbound bot message.

    ``msg_idx`` should be ``ext_info.ref_idx`` (REFIDX_*). Only that key enters
    ``_bot_msg_idx``. ``alt_id`` (response ``id``) is stored for text lookup only.
    """
    key = (msg_idx or "").strip()
    body = (text or "").strip()
    alt = (alt_id or "").strip()
    if key:
        remember(key, body)
        _bot_msg_idx.add(key)
    if alt and alt != key and body:
        remember(alt, body)
    if body:
        scope = _bot_scope(group_openid, user_openid)
        bucket = _bot_texts.setdefault(scope, deque(maxlen=_MAX_BOT_TEXTS))
        if not bucket or bucket[-1] != body:
            bucket.append(body)


def is_bot_msg_idx(msg_idx: str) -> bool:
    """True when this ref_msg_idx was sent by the bot (REFIDX from ext_info.ref_idx)."""
    key = (msg_idx or "").strip()
    return bool(key and key in _bot_msg_idx)


def quoted_matches_bot(
    quoted_text: str,
    *,
    group_openid: str | None,
    user_openid: str,
) -> bool:
    """True when quoted body matches a recent bot reply in the same chat scope."""
    needle = _normalize_quote(quoted_text)
    if len(needle) < 4:
        return False
    scope = _bot_scope(group_openid, user_openid)
    for body in _bot_texts.get(scope, ()):
        hay = _normalize_quote(body)
        if not hay:
            continue
        if needle in hay or hay in needle:
            return True
    return False


def lookup(msg_idx: str) -> str:
    """Return cached text for ref_msg_idx, or empty if unknown."""
    key = (msg_idx or "").strip()
    if not key:
        return ""
    return _cache.get(key, "")


def clear_for_tests() -> None:
    """Drop all entries (tests only)."""
    _cache.clear()
    _bot_msg_idx.clear()
    _bot_texts.clear()


def _bot_scope(group_openid: str | None, user_openid: str) -> str:
    """Scope bot reply memory to one group or one private chat."""
    if group_openid:
        return f"group:{group_openid.strip()}"
    return f"c2c:{user_openid.strip()}"


def _normalize_quote(text: str) -> str:
    """Collapse whitespace for fuzzy quote matching."""
    return " ".join((text or "").split())
