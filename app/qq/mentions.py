"""Parse QQ <@openid> tags and rewrite them to @昵称 for the model."""

from __future__ import annotations

import re
from typing import Any, Callable

_AT_TAG = re.compile(r"<@!?([A-Za-z0-9_-]+)>")


def extract_mention_ids(text: str) -> tuple[str, ...]:
    """Return openids found in `<@id>` / `<@!id>` markers, in appearance order."""
    return tuple(_AT_TAG.findall(text or ""))


def display_name(openid: str, impression_name: str = "", roster_name: str = "") -> str:
    """Pick a screen name: impression username, then roster, then 未知(short id)."""
    name = (impression_name or "").strip() or (roster_name or "").strip()
    if name:
        return name
    short = (openid or "").strip()[:8] or "?"
    return f"未知({short})"


def rewrite_mentions(text: str, resolve: Callable[[str], str]) -> str:
    """Replace `<@id>` markers with `@昵称` using `resolve(openid)`."""

    def _repl(match: re.Match[str]) -> str:
        return "@" + resolve(match.group(1))

    return _AT_TAG.sub(_repl, text or "")


def mention_pairs_from_payload(data: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Collect (openid, username) from optional mentions / msg_elements fields."""
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        oid = str(
            item.get("id")
            or item.get("member_openid")
            or item.get("user_openid")
            or item.get("openid")
            or item.get("user_id")
            or ""
        ).strip()
        name = str(
            item.get("username")
            or item.get("nick")
            or item.get("nickname")
            or item.get("name")
            or ""
        ).strip()
        if not oid or oid in seen:
            return
        seen.add(oid)
        pairs.append((oid, name))

    for key in ("mentions", "mention_users"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                add(item)
    for key in ("msg_elements", "elements"):
        raw = data.get(key)
        if not isinstance(raw, list):
            continue
        for element in raw:
            if not isinstance(element, dict):
                continue
            add(element)
            for nested_key in ("mention", "at", "user"):
                nested = element.get(nested_key)
                if isinstance(nested, dict):
                    add(nested)
    return tuple(pairs)
