"""Resolve inbound quoted-message text and quotes_bot for type-103 / ref_msg_idx."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.qq import message_cache

_log = logging.getLogger(__name__)

_IMAGE_PLACEHOLDER = "[引用图片]"


@dataclass(frozen=True)
class QuotedRef:
    """Resolved quote context for one inbound event."""

    text: str
    ref_msg_idx: str
    quotes_bot: bool
    source: str  # elements | cache | none


def resolve_quoted(
    data: dict[str, Any],
    scene_ext: dict[str, str],
    message_type: int,
    *,
    group_openid: str | None,
    user_openid: str,
) -> QuotedRef:
    """Resolve quoted body from msg_elements, then local msg_idx cache."""
    ref_idx = (scene_ext.get("ref_msg_idx") or "").strip()
    if message_type != 103 and not ref_idx:
        return QuotedRef(text="", ref_msg_idx="", quotes_bot=False, source="none")

    element_text, has_media = _extract_quote_elements(data.get("msg_elements"))
    if element_text:
        text = element_text
        source = "elements"
    elif has_media:
        text = _IMAGE_PLACEHOLDER
        source = "elements"
    elif ref_idx:
        cached = message_cache.lookup(ref_idx)
        if cached:
            text = cached
            source = "cache"
        else:
            text = ""
            source = "none"
    else:
        text = ""
        source = "none"

    quotes_bot = _detect_quotes_bot(
        data,
        ref_idx=ref_idx,
        message_type=message_type,
        quoted_text=text,
        group_openid=group_openid,
        user_openid=user_openid,
    )
    preview = text.replace("\n", " ")[:40]
    if source == "none" and (message_type == 103 or ref_idx):
        _log.info(
            "quote unresolved ref=%s type=%s quotes_bot=%s",
            (ref_idx or "-")[:28],
            message_type,
            quotes_bot,
        )
    else:
        _log.info(
            "quote source=%s ref=%s quotes_bot=%s preview=%r",
            source,
            (ref_idx or "-")[:28],
            quotes_bot,
            preview,
        )
    return QuotedRef(
        text=text,
        ref_msg_idx=ref_idx,
        quotes_bot=quotes_bot,
        source=source,
    )


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce nested payload fragments to a dict."""
    return value if isinstance(value, dict) else {}


def _attachment_content_type(entry: dict[str, Any]) -> str:
    """Read MIME type from content_type or content-type keys."""
    for key in ("content_type", "content-type"):
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def _has_image_attachment(raw: Any) -> bool:
    """True when attachments look like an image or sticker."""
    if not isinstance(raw, list):
        return False
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = _attachment_content_type(entry).lower()
        name = str(entry.get("filename") or "").lower()
        if ctype.startswith("image/") or name.endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp")
        ):
            return True
        # Stickers often only carry a download URL without a useful MIME.
        if entry.get("url") and not str(entry.get("asr_refer_text") or "").strip():
            return True
    return False


def _extract_quote_elements(raw: Any) -> tuple[str, bool]:
    """Flatten msg_elements into quote text; remember nested msg_idx bodies."""
    if not isinstance(raw, list):
        return "", False
    parts: list[str] = []
    has_media = False
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        element_idx = str(entry.get("msg_idx") or "").strip()
        if content:
            parts.append(content)
            if element_idx:
                message_cache.remember(element_idx, content)
        nested_text, nested_media = _extract_quote_elements(entry.get("msg_elements"))
        if nested_text:
            parts.append(nested_text)
        has_media = has_media or nested_media
        attachments = entry.get("attachments")
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                asr = str(attachment.get("asr_refer_text") or "").strip()
                if asr:
                    parts.append(asr)
            if _has_image_attachment(attachments):
                has_media = True
    return "\n".join(parts).strip(), has_media


def _msg_elements_quote_bot_author(raw: Any) -> bool:
    """True when nested msg_elements mark the quoted author as the bot."""
    if not isinstance(raw, list):
        return False
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        author = _as_dict(entry.get("author"))
        if author.get("bot") is True:
            return True
        if _msg_elements_quote_bot_author(entry.get("msg_elements")):
            return True
    return False


def _detect_quotes_bot(
    data: dict[str, Any],
    *,
    ref_idx: str,
    message_type: int,
    quoted_text: str,
    group_openid: str | None,
    user_openid: str,
) -> bool:
    """True when the user quoted a recent bot message in this chat."""
    if ref_idx and message_cache.is_bot_msg_idx(ref_idx):
        return True
    if message_type == 103 or ref_idx:
        if _msg_elements_quote_bot_author(data.get("msg_elements")):
            return True
    if quoted_text.strip() and quoted_text.strip() != _IMAGE_PLACEHOLDER:
        return message_cache.quoted_matches_bot(
            quoted_text,
            group_openid=group_openid,
            user_openid=user_openid,
        )
    return False
