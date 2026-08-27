"""Parse C2C and group-at events into a single inbound message type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)

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
        images: list[Attachment] = []
        for item in self.attachments:
            ctype = item.content_type.lower()
            name = item.filename.lower()
            if ctype.startswith("image/") or name.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                images.append(item)
        return tuple(images)

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
        parts = [item.asr_refer_text.strip() for item in self.voice_attachments if item.asr_refer_text.strip()]
        return " ".join(parts).strip()

    @property
    def user_text(self) -> str:
        """Text the model should treat as the user's utterance (typed or ASR)."""
        typed = self.content.strip()
        asr = self.asr_text
        if typed and asr:
            return f"{typed}\n{asr}"
        return typed or asr


def _as_dict(value: Any) -> dict[str, Any]:
    """Coerce nested payload fragments to a dict."""
    return value if isinstance(value, dict) else {}


def _attachment_content_type(entry: dict[str, Any]) -> str:
    """Read MIME type from content_type or content-type keys."""
    for key in ("content_type", "content-type", "content_type"):
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


def _quote_id(data: dict[str, Any], msg_id: str) -> str:
    """Pick the id used for quote replies: scene msg_idx, else the event msg_id."""
    scene = _as_dict(data.get("message_scene"))
    ext = scene.get("ext")
    if isinstance(ext, list):
        for item in ext:
            if not isinstance(item, str) or not item.startswith("msg_idx="):
                continue
            value = item.split("=", 1)[1].strip()
            if value:
                return value
    return msg_id


def parse_incoming(payload: dict[str, Any]) -> IncomingMessage | None:
    """Turn a webhook JSON body into IncomingMessage, or None if not a chat event."""
    opcode = payload.get("op")
    event_type = str(payload.get("t") or "")
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
        or author.get("user_openid")
        or author.get("member_openid")
        or author.get("member_openid")
        or author.get("id")
        or ""
    )
    msg_id = str(data.get("id") or "")
    if not user_openid or not msg_id:
        return None
    attachments = _parse_attachments(data.get("attachments")) or _parse_attachments(
        data.get("attachments")
    )
    group_openid = str(data.get("group_openid") or "") or None
    if event_type in {GROUP_AT_EVENT, GROUP_MESSAGE_EVENT} and not group_openid:
        return None
    if event_type == C2C_EVENT:
        group_openid = None
    return IncomingMessage(
        event_type=event_type,
        event_id=str(payload.get("id") or ""),
        msg_id=msg_id,
        content=str(data.get("content") or "").strip(),
        user_openid=user_openid,
        username=str(author.get("username") or ""),
        group_openid=group_openid,
        member_role=_member_role(author),
        quote_id=_quote_id(data, msg_id),
        attachments=attachments,
    )


