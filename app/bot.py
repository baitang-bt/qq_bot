"""Orchestrate inbound QQ messages: memory, vision, LLM, reply."""

from __future__ import annotations

import logging

from app.commands import CommandRouter
from app.impression.store import ImpressionStore, extract_qq_from_text
from app.impression.writer import ImpressionWriter
from app.llm.client import LLMClient
from app.memory.store import MemoryStore
from app.qq.events import IncomingMessage
from app.qq.reply import ReplyClient
from app.reply_policy import ReplyGate
from app.vision.identify import ImageIdentifier

_log = logging.getLogger(__name__)


class ChatBot:
    """Handle one inbound message end-to-end."""

    def __init__(
        self,
        replies: ReplyClient,
        llm: LLMClient,
        memory: MemoryStore,
        vision: ImageIdentifier,
        gate: ReplyGate,
        impressions: ImpressionStore,
        impression_writer: ImpressionWriter,
        commands: CommandRouter,
    ) -> None:
        self._replies = replies
        self._llm = llm
        self._memory = memory
        self._vision = vision
        self._gate = gate
        self._impressions = impressions
        self._impression_writer = impression_writer
        self._commands = commands

    async def handle(self, message: IncomingMessage) -> None:
        """Identify images if needed, then reply with a chat completion."""
        _log.info(
            "handle session=%s group=%s text_len=%s images=%s",
            message.session_id,
            message.is_group,
            len(message.user_text),
            len(message.image_attachments),
        )
        if not message.is_group:
            try:
                await self._replies.send_c2c_typing(message, seconds=20)
            except Exception:
                _log.warning("c2c typing failed", exc_info=True)
        command_reply = self._commands.try_handle(message)
        if command_reply is not None:
            await self._replies.send_text(message, command_reply)
            return
        skip = self._gate.decide(message)
        if skip:
            _log.info("skip reply session=%s reason=%s", message.session_id, skip)
            return
        user_text = message.user_text
        images = message.image_attachments
        notes: list[str] = []
        if images:
            cached = self._vision.peek_cached(images)
            if cached is None:
                try:
                    if message.is_group:
                        await self._replies.send_text(message, "在看图")
                    else:
                        await self._replies.send_c2c_typing(message, seconds=20)
                except Exception:
                    _log.warning("pre-vision status reply failed", exc_info=True)
                notes = await self._vision.describe_all(images)
            else:
                notes = cached
        if not user_text and not notes:
            return
        spoken_qq = extract_qq_from_text(user_text)
        profile = self._impressions.touch(
            message.user_openid,
            username=message.username,
            spoken_qq=spoken_qq,
        )
        history = self._memory.history(message.session_id)
        reply = await self._llm.complete(
            history,
            user_text,
            notes,
            impression=str(profile.get("impression") or ""),
        )
        user_record = user_text
        if message.asr_text:
            user_record = f"[语音] {user_text}"
        if notes:
            user_record = (user_record + "\n" if user_record else "") + "\n".join(
                f"[图] {note}" for note in notes
            )
        self._memory.append(message.session_id, "user", user_record)
        self._memory.append(message.session_id, "assistant", reply)
        await self._replies.send_text(message, reply)
        await self._impression_writer.after_turn(
            user_openid=message.user_openid,
            username=message.username,
            user_text=user_record,
            assistant_text=reply,
            spoken_qq=spoken_qq,
        )
