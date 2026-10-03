"""Orchestrate inbound QQ messages: memory, vision, LLM, reply."""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from app.commands import CommandRouter
from app.config import Settings
from app.qq.coalesce import MessageCoalescer
from app.impression.store import ImpressionStore, extract_qq_from_text
from app.impression.writer import ImpressionWriter
from app.llm.client import LLMClient
from app.memory.store import MemoryStore
from app.qq.events import IncomingMessage
from app.qq.reply import ReplyClient, should_quote_inbound
from app.reply_policy import ReplyGate
from app.stickers.catalog import StickerCatalog
from app.stickers.markers import memory_text_for_segments, parse_reply_segments
from app.vision.identify import ImageIdentifier

_log = logging.getLogger(__name__)
_HANDLE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qq-bot-handle")


def _run_handle_isolated(bot: "ChatBot", message: IncomingMessage) -> None:
    """Run one handle turn on a fresh event loop so gateway websocket I/O cannot stall LLM."""
    asyncio.run(bot._handle_async(message))


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
        settings: Settings | None = None,
        stickers: StickerCatalog | None = None,
    ) -> None:
        self._replies = replies
        self._llm = llm
        self._memory = memory
        self._vision = vision
        self._gate = gate
        self._impressions = impressions
        self._impression_writer = impression_writer
        self._commands = commands
        self._stickers = stickers
        burst = 30.0 if settings is None else settings.coalesce_burst_seconds
        debounce = 3.0 if settings is None else settings.coalesce_debounce_seconds
        single = 3.0 if settings is None else settings.coalesce_single_debounce_seconds
        self._coalescer = MessageCoalescer(
            self.handle,
            burst_window=burst,
            debounce=debounce,
            single_debounce=single,
        )

    async def enqueue(self, message: IncomingMessage) -> None:
        """Queue an inbound line; bursts within ~30s are merged before handle()."""
        await self._coalescer.submit(message)

    async def handle(self, message: IncomingMessage) -> None:
        """Run handle on a worker thread so LLM HTTP does not block the gateway loop."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            _HANDLE_EXECUTOR,
            partial(_run_handle_isolated, self, message),
        )

    async def _handle_async(self, message: IncomingMessage) -> None:
        """Run one turn inside the worker event loop (coalesce serializes per session)."""
        await self._handle_turn(message)

    async def _handle_turn(self, message: IncomingMessage) -> None:
        """Resolve sticker notes / download photos, then reply with chat completion."""
        _log.info(
            "inbound t=%s mentioned=%s quotes_bot=%s session=%s user=%s text=%r images=%s",
            message.event_type,
            message.mentioned,
            message.quotes_bot,
            message.session_id,
            message.username or message.user_openid[:8],
            message.user_text[:80],
            len(message.image_attachments),
        )
        if not message.is_group:
            try:
                await self._replies.send_c2c_typing(message, seconds=20)
            except Exception:
                _log.warning("c2c typing failed", exc_info=True)
        sticker_cmd = self._commands.try_sticker_send(message)
        if sticker_cmd is not None:
            history = self._memory.history(message.session_id)
            quote = should_quote_inbound(history)
            path = self._stickers.path_for(sticker_cmd) if self._stickers else None
            if path is None:
                await self._replies.send_text(
                    message,
                    f"没有本地表情「{sticker_cmd}」。检查 stickers.toml 与 data/stickers/。",
                    quote=quote,
                )
            else:
                ok = await self._replies.send_image(message, path, quote=quote)
                if not ok:
                    await self._replies.send_text(
                        message,
                        f"表情「{sticker_cmd}」发送失败，看日志。",
                        quote=False,
                    )
            self._gate.note_bot_reply(message.session_id, time.time())
            return
        command_reply = self._commands.try_handle(message)
        if command_reply is not None:
            _log.info(
                "正在回复 command user=%s preview=%r",
                message.username or message.user_openid[:8],
                command_reply[:80],
            )
            history = self._memory.history(message.session_id)
            await self._replies.send_text(
                message,
                command_reply,
                quote=should_quote_inbound(history),
            )
            self._gate.note_bot_reply(message.session_id, time.time())
            return
        self._gate.note_inbound_engagement(message)
        skip = self._gate.decide(message)
        if skip:
            _log.info(
                "skip reply t=%s mentioned=%s reason=%s text=%r",
                message.event_type,
                message.mentioned,
                skip,
                message.user_text[:80],
            )
            return
        user_text = message.user_text
        image_attachments = message.image_attachments
        history = self._memory.history(message.session_id)
        quote_inbound = should_quote_inbound(history)
        sticker_notes: list[str] = []
        image_bytes: list[tuple[bytes, str]] = []
        if image_attachments:
            stickers, photos = self._vision.partition(image_attachments)
            _log.info(
                "media start session=%s stickers=%s photos=%s (no QQ status bubble)",
                message.session_id,
                len(stickers),
                len(photos),
            )
            if not message.is_group and (stickers or photos):
                try:
                    await self._replies.send_c2c_typing(message, seconds=20)
                except Exception:
                    _log.warning("c2c typing before media failed", exc_info=True)
            if stickers:
                sticker_notes = await self._vision.resolve_sticker_notes(stickers)
            if photos:
                image_bytes = await self._vision.fetch_images(photos)
                _log.info(
                    "photo multimodal downloaded session=%s ok=%s/%s",
                    message.session_id,
                    len(image_bytes),
                    len(photos),
                )
        if not user_text and not image_bytes and not sticker_notes:
            return
        turn_started = time.monotonic()
        _log.info(
            "正在回复 mentioned=%s user=%s preview=%r photos=%s stickers=%s",
            message.mentioned,
            message.username or message.user_openid[:8],
            message.user_text[:80],
            len(image_bytes),
            len(sticker_notes),
        )
        spoken_qq = extract_qq_from_text(user_text)
        profile = self._impressions.touch(
            message.user_openid,
            username=message.username,
            spoken_qq=spoken_qq,
        )
        if self._stickers is not None:
            self._llm.set_stickers_prompt(self._stickers.prompt_block())
        reply = await self._llm.complete(
            history,
            user_text,
            images=image_bytes,
            notes=sticker_notes,
            impression=str(profile.get("impression") or ""),
        )
        if not reply.strip():
            _log.warning("empty llm reply session=%s", message.session_id)
            return
        user_record = user_text
        if message.asr_text:
            user_record = f"[语音] {user_text}"
        if sticker_notes:
            note_block = "\n".join(f"[表情包] {note}" for note in sticker_notes)
            user_record = f"{user_record}\n{note_block}".strip() if user_record else note_block
        if image_bytes:
            marker = f"[图x{len(image_bytes)}]"
            user_record = f"{user_record}\n{marker}".strip() if user_record else marker
        known = set(self._stickers.known_ids()) if self._stickers else set()
        segments = parse_reply_segments(reply, known_ids=known)
        assistant_record = memory_text_for_segments(segments) or reply
        self._memory.append(message.session_id, "user", user_record)
        self._memory.append(message.session_id, "assistant", assistant_record)
        elapsed = time.monotonic() - turn_started
        _log.info(
            "正在发送 mentioned=%s segments=%s elapsed=%.1fs preview=%r",
            message.mentioned,
            len(segments),
            elapsed,
            reply[:80],
        )
        delivered = await self._replies.send_segments(
            message,
            segments,
            resolve_sticker=lambda sid: (
                self._stickers.path_for(sid) if self._stickers else None
            ),
            quote_first=quote_inbound,
        )
        if not delivered:
            _log.warning(
                "reply not delivered t=%s msg_id=%s session=%s",
                message.event_type,
                message.msg_id,
                message.session_id,
            )
            return
        _log.info(
            "replied t=%s mentioned=%s segments=%s preview=%r",
            message.event_type,
            message.mentioned,
            len(segments),
            reply[:80],
        )
        reply = assistant_record
        now = time.time()
        self._gate.note_bot_reply(message.session_id, now)
        if message.is_group and not message.mentioned and not message.quotes_bot and message.group_openid:
            self._gate.note_unmentioned_reply(message.group_openid, now)
        await self._run_after_turn(
            user_openid=message.user_openid,
            username=message.username,
            user_text=user_record,
            assistant_text=reply,
            spoken_qq=spoken_qq,
        )

    async def _run_after_turn(
        self,
        *,
        user_openid: str,
        username: str,
        user_text: str,
        assistant_text: str,
        spoken_qq: str,
    ) -> None:
        """Refresh impression JSON without blocking the next inbound message."""
        try:
            await self._impression_writer.after_turn(
                user_openid=user_openid,
                username=username,
                user_text=user_text,
                assistant_text=assistant_text,
                spoken_qq=spoken_qq,
            )
        except Exception:
            _log.exception("impression after_turn failed openid=%s", user_openid)
