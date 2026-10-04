"""Parse C2C and group-at events into a single inbound message type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.qq import message_cache
from app.qq.quote import resolve_quoted

C2C_EVENT = "C2C_MESSAGE_CREATE"
GROUP_AT_EVENT = "GROUP_AT_MESSAGE_CREATE"
GROUP_MESSAGE_EVENT = "GROUP_MESSAGE_CREATE"
_CHAT_EVENTS = frozenset({C2C_EVENT, GROUP_AT_EVENT, GROUP_MESSAGE_EVENT})


@dataclass(frozen=True)
class Attachment:
    """One media attachment on an inbound QQ message."""

    url: str
    filename: str
    content_type: str
    size: int
    voice_wav_url: str = ""
    asr_refer_text: str = ""


@dataclass(frozen=True)
class IncomingMessage:
    """Normalized inbound chat event used by the bot orchestrator."""

    event_type: str
    event_id: str
    msg_id: str
    content: str
    user_openid: str
    group_openid: str | None
    username: str = ""
    member_role: str = ""
    quote_id: str = ""
    ref_msg_idx: str = ""
    quoted_text: str = ""
    quotes_bot: bool = False
    message_type: int = 0
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    quoted_attachments: tuple[Attachment, ...] = field(default_factory=tuple)

    @property
    def is_group(self) -> bool:
        """True when this event came from a group (with or without @)."""
        return self.group_openid is not None

    @property
    def mentioned(self) -> bool:
        """True for DMs and group messages that @ the bot (or contain an @ tag)."""
        if not self.is_group:
            return True
        if self.event_type == GROUP_AT_EVENT:
            return True
        return "<@" in self.content

    @property
    def session_id(self) -> str:
        """Stable short-memory key: one thread per user, or per user-in-group."""
        if self.group_openid:
            return f"group:{self.group_openid}:{self.user_openid}"
        return f"c2c:{self.user_openid}"

    @property
    def image_attachments(self) -> tuple[Attachment, ...]:
        """Attachments that look like still images or sticker GIFs."""
        return tuple(item for item in self.attachments if _is_image_attachment(item))

    @property
    def quoted_image_attachments(self) -> tuple[Attachment, ...]:
        """Images on the quoted message (type 103), used to learn/send stickers."""
        return tuple(
            item for item in self.quoted_attachments if _is_image_attachment(item)
        )

    @property
    def voice_attachments(self) -> tuple[Attachment, ...]:
        """Attachments that are voice clips (content_type=voice)."""
        voices: list[Attachment] = []
        for item in self.attachments:
            ctype = item.content_type.lower()
            if ctype == "voice" or ctype.startswith("audio/") or item.asr_refer_text:
                voices.append(item)
        return tuple(voices)

    @property
    def asr_text(self) -> str:
        """Official ASR text from voice attachments, if the platform provided it."""
        parts = [
            item.asr_refer_text.strip()
            for item in self.voice_attachments
            if item.asr_refer_text.strip()
        ]
        return " ".join(parts).strip()

    @property
    def spoken_text(self) -> str:
        """Typed content plus ASR, without the quote wrapper used for the model."""
        typed = self.content.strip()
        asr = self.asr_text
        if typed and asr:
            return f"{typed}\n{asr}"
        return typed or asr

    @property
    def user_text(self) -> str:
        """Text the model should treat as the user's utterance (typed, ASR, or quote)."""
        body = self.spoken_text
        quote = self.quoted_text.strip()
        if not quote:
            return body
        if body:
            return f"[引用]\n{quote}\n\n[消息]\n{body}"
        return f"[引用]\n{quote}"


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


def _parse_attachments(raw: Any) -> tuple[Attachment, ...]:
    """Read attachments / attachments arrays from an event body."""
    if not isinstance(raw, list):
        return ()
    items: list[Attachment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        asr = str(entry.get("asr_refer_text") or "")
        wav = str(entry.get("voice_wav_url") or "")
        content_type = _attachment_content_type(entry)
        if not url and not wav and not asr:
            continue
        items.append(
            Attachment(
                url=url or wav,
                filename=str(entry.get("filename") or ""),
                content_type=content_type,
                size=int(entry.get("size") or 0),
                voice_wav_url=wav,
                asr_refer_text=asr,
            )
        )
    return tuple(items)


def _member_role(author: dict[str, Any]) -> str:
    """Read group role from author.member_role, author.role, or author.roles."""
    raw = str(author.get("member_role") or author.get("role") or "").strip().lower()
    if raw:
        return raw
    roles = author.get("roles")
    if isinstance(roles, list):
        lowered = [str(item).strip().lower() for item in roles]
        if "owner" in lowered:
            return "owner"
        if "admin" in lowered:
            return "admin"
    return ""


def _parse_scene_ext(scene: dict[str, Any]) -> dict[str, str]:
    """Read message_scene.ext key=value pairs (msg_idx, ref_msg_idx, …)."""
    result: dict[str, str] = {}
    ext = scene.get("ext")
    if not isinstance(ext, list):
        return result
    for item in ext:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value
    return result


def _quote_id(data: dict[str, Any], msg_id: str) -> str:
    """Pick the id used for quote replies: scene msg_idx, else the event msg_id."""
    scene = _as_dict(data.get("message_scene"))
    ext = _parse_scene_ext(scene)
    msg_idx = ext.get("msg_idx", "").strip()
    if msg_idx:
        return msg_idx
    return msg_id


def _merge_attachments(*groups: tuple[Attachment, ...]) -> tuple[Attachment, ...]:
    """Concatenate attachment tuples while dropping obvious duplicates."""
    seen: set[str] = set()
    merged: list[Attachment] = []
    for group in groups:
        for item in group:
            key = item.url or item.filename or item.asr_refer_text
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
    return tuple(merged)


def _collect_msg_elements_body(raw: Any) -> tuple[str, tuple[Attachment, ...]]:
    """Extract inline text and attachments from parallel/compound msg_elements."""
    if not isinstance(raw, list):
        return "", ()
    texts: list[str] = []
    attachments: list[Attachment] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if content:
            texts.append(content)
        attachments.extend(_parse_attachments(entry.get("attachments")))
        nested_text, nested_attachments = _collect_msg_elements_body(
            entry.get("msg_elements")
        )
        if nested_text:
            texts.append(nested_text)
        attachments.extend(nested_attachments)
    return "\n".join(texts).strip(), tuple(attachments)


def _is_image_attachment(item: Attachment) -> bool:
    """True when an attachment looks like a still image or sticker file."""
    ctype = item.content_type.lower()
    name = item.filename.lower()
    return ctype.startswith("image/") or name.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp")
    )


def _merge_content_with_elements(content: str, element_text: str) -> str:
    """Combine top-level content with inline msg_elements text without duplication."""
    base = content.strip()
    extra = element_text.strip()
    if not extra:
        return base
    if not base:
        return extra
    if extra in base or base in extra:
        return base if len(base) >= len(extra) else extra
    return f"{base}\n{extra}"


def _remember_for_quotes(msg_idx: str, content: str, asr_text: str) -> None:
    """Cache this message's own spoken body so later ref_msg_idx lookups can succeed."""
    body = content.strip() or asr_text.strip()
    if body:
        message_cache.remember(msg_idx, body)


def parse_incoming(payload: dict[str, Any]) -> IncomingMessage | None:
    """Turn a webhook JSON body into IncomingMessage, or None if not a chat event."""
    opcode = payload.get("op")
    event_type = str(payload.get("t") or "").upper()
    if opcode not in (0, None) and event_type not in _CHAT_EVENTS:
        if opcode != 0:
            return None
    if event_type not in _CHAT_EVENTS:
        return None
    data = _as_dict(payload.get("d"))
    author = _as_dict(data.get("author"))
    if author.get("bot") is True:
        return None
    user_openid = str(
        author.get("user_openid")
        or author.get("member_openid")
        or author.get("id")
        or ""
    )
    msg_id = str(data.get("id") or "")
    if not user_openid or not msg_id:
        return None
    attachments = _parse_attachments(data.get("attachments"))
    message_type = int(data.get("message_type") or 0)
    element_text = ""
    element_attachments: tuple[Attachment, ...] = ()
    quoted_attachments: tuple[Attachment, ...] = ()
    if message_type in {101, 102}:
        element_text, element_attachments = _collect_msg_elements_body(
            data.get("msg_elements")
        )
        attachments = _merge_attachments(attachments, element_attachments)
    elif message_type == 103:
        _, quoted_attachments = _collect_msg_elements_body(data.get("msg_elements"))
    else:
        element_text, element_attachments = _collect_msg_elements_body(
            data.get("msg_elements")
        )
        if element_attachments:
            attachments = _merge_attachments(attachments, element_attachments)
    group_openid = str(data.get("group_openid") or "") or None
    if event_type in {GROUP_AT_EVENT, GROUP_MESSAGE_EVENT} and not group_openid:
        return None
    if event_type == C2C_EVENT:
        group_openid = None
    scene = _as_dict(data.get("message_scene"))
    scene_ext = _parse_scene_ext(scene)
    msg_idx = scene_ext.get("msg_idx", "").strip() or msg_id
    content = _merge_content_with_elements(
        str(data.get("content") or "").strip(),
        element_text,
    )
    quoted = resolve_quoted(
        data,
        scene_ext,
        message_type,
        group_openid=group_openid,
        user_openid=user_openid,
    )
    asr_parts = [
        item.asr_refer_text.strip()
        for item in attachments
        if item.asr_refer_text.strip()
    ]
    asr_text = " ".join(asr_parts).strip()
    _remember_for_quotes(msg_idx, content, asr_text)
    return IncomingMessage(
        event_type=event_type,
        event_id=str(payload.get("id") or ""),
        msg_id=msg_id,
        content=content,
        user_openid=user_openid,
        username=str(author.get("username") or ""),
        group_openid=group_openid,
        member_role=_member_role(author),
        quote_id=_quote_id(data, msg_id),
        ref_msg_idx=quoted.ref_msg_idx,
        quoted_text=quoted.text,
        quotes_bot=quoted.quotes_bot,
        message_type=message_type,
        attachments=attachments,
        quoted_attachments=quoted_attachments,
    )


def merge_inbound_messages(messages: list[IncomingMessage]) -> IncomingMessage:
    """Combine a burst of consecutive user lines; passive reply uses the latest msg_id."""
    if not messages:
        raise ValueError("merge_inbound_messages requires at least one message")
    if len(messages) == 1:
        return messages[0]
    last = messages[-1]
    texts: list[str] = []
    for item in messages:
        text = item.spoken_text.strip()
        if text and (not texts or texts[-1] != text):
            texts.append(text)
    quoted_text = ""
    ref_msg_idx = last.ref_msg_idx
    for item in reversed(messages):
        if item.quoted_text.strip():
            quoted_text = item.quoted_text.strip()
            ref_msg_idx = item.ref_msg_idx
            break
    attachments: list[Attachment] = []
    seen: set[str] = set()
    for item in messages:
        for attachment in item.attachments:
            key = attachment.url or attachment.filename or attachment.asr_refer_text
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            attachments.append(attachment)
    quoted_attachments: list[Attachment] = []
    quoted_seen: set[str] = set()
    for item in messages:
        for attachment in item.quoted_attachments:
            key = attachment.url or attachment.filename or attachment.asr_refer_text
            if key and key in quoted_seen:
                continue
            if key:
                quoted_seen.add(key)
            quoted_attachments.append(attachment)
    mentioned = any(item.mentioned for item in messages)
    quotes_bot = any(item.quotes_bot for item in messages)
    event_type = last.event_type
    if mentioned and last.is_group and event_type == GROUP_MESSAGE_EVENT:
        event_type = GROUP_AT_EVENT
    return IncomingMessage(
        event_type=event_type,
        event_id=last.event_id,
        msg_id=last.msg_id,
        content="\n".join(texts) if texts else last.content,
        user_openid=last.user_openid,
        group_openid=last.group_openid,
        username=last.username,
        member_role=last.member_role,
        quote_id=last.quote_id,
        ref_msg_idx=ref_msg_idx,
        quoted_text=quoted_text,
        quotes_bot=quotes_bot,
        message_type=last.message_type,
        attachments=tuple(attachments),
        quoted_attachments=tuple(quoted_attachments),
    )
