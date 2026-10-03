"""Owner slash commands. Commands are answered as plain text, not via the chat model."""

from __future__ import annotations

import logging
import re

from app.impression.store import ImpressionStore
from app.owner import OwnerGate
from app.qq.events import IncomingMessage

_log = logging.getLogger(__name__)
_AT_PREFIX = re.compile(r"^(?:<@!?\w+>\s*)+")
_IMPRESSION = re.compile(
    r"^/(?:impression|impression)(?:\s+|　+)(.+)$",
    re.IGNORECASE,
)
_BIND = re.compile(r"^/bind(?:\s+|　+)(.*)$", re.IGNORECASE)
_STICKER = re.compile(r"^/(?:贴纸|sticker)(?:\s+|　+)(.+)$", re.IGNORECASE)
_DENIED = (
    "这条指令只认管理员 QQ。"
    "官方群消息里没有 QQ 号，请先私聊发送：/bind 你的QQ号"
)


class CommandRouter:
    """Parse and run slash commands for the bot owner."""

    def __init__(self, owner: OwnerGate, impressions: ImpressionStore) -> None:
        self._owner = owner
        self._impressions = impressions

    def try_sticker_send(self, message: IncomingMessage) -> str | None:
        """Return a sticker id for /贴纸|/sticker, or None if not that command."""
        if message.is_group and not message.mentioned:
            return None
        text = _slash_text(message)
        match = _STICKER.match(text)
        if not match:
            return None
        if not self._owner.allows(message, self._impressions):
            return None
        sticker_id = match.group(1).strip().lower()
        return sticker_id or None

    def try_handle(self, message: IncomingMessage) -> str | None:
        """Return a command reply, or None if this is not a directed slash command."""
        if message.is_group and not message.mentioned:
            return None
        text = _slash_text(message)
        if not text.startswith("/"):
            return None
        if _STICKER.match(text):
            # Owner-denied sticker attempts still need a text reply.
            if not self._owner.allows(message, self._impressions):
                return _DENIED
            return "用法：/贴纸 facepalm（需 stickers.toml 里已配置）"
        bind = _BIND.match(text)
        if bind:
            return self._owner.bind_claimed_qq(message, bind.group(1).strip())
        if not self._owner.allows(message, self._impressions):
            return _DENIED
        match = _IMPRESSION.match(text)
        if match:
            return self._impression(match.group(1).strip())
        return "还不认识这条指令。现在可以用：/bind QQ号，/impression 昵称，/贴纸 id"

    def _impression(self, username: str) -> str:
        """Dump the stored impression prompt for a username."""
        if not username:
            return "用法：/impression 昵称"
        hits = self._impressions.find_by_username(username)
        if not hits:
            return f"没有找到昵称「{username}」的印象。对方至少要跟 bot 说过话才会建档。"
        lines: list[str] = []
        for record in hits:
            body = str(record.get("impression") or "").strip() or "（还没有印象正文）"
            qq = str(record.get("qq") or "").strip()
            extra = f"，qq={qq}" if qq else ""
            lines.append(f"{record.get('username') or username}{extra} 的印象：\n{body}")
        return "\n\n".join(lines)


def looks_like_command(message: IncomingMessage) -> bool:
    """True when the inbound line is a directed owner slash command."""
    if message.is_group and not message.mentioned:
        return False
    return _slash_text(message).startswith("/")


def _slash_text(message: IncomingMessage) -> str:
    """Strip leading @ tags so @bot /impression still counts as a command."""
    typed = message.content.strip()
    asr = message.asr_text
    body = typed or asr
    if typed and asr:
        body = f"{typed}\n{asr}"
    return _AT_PREFIX.sub("", body.strip()).strip()
