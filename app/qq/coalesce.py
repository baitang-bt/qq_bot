"""Merge consecutive inbound lines from the same user before one bot reply."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from app.commands import looks_like_command
from app.qq.events import IncomingMessage, merge_inbound_messages

_log = logging.getLogger(__name__)

Handler = Callable[[IncomingMessage], Awaitable[None]]


def should_flush_immediately(message: IncomingMessage) -> bool:
    """Image+text in one bubble bypasses debounce; chat lines always wait for the window."""
    return bool(message.image_attachments) and bool(message.user_text.strip())


class MessageCoalescer:
    """Wait briefly for back-to-back user lines, then handle them as one turn."""

    def __init__(
        self,
        handler: Handler,
        *,
        burst_window: float = 30.0,
        debounce: float = 1.0,
        single_debounce: float = 0.6,
    ) -> None:
        self._handler = handler
        self._burst_window = max(1.0, burst_window)
        self._debounce = max(0.2, debounce)
        self._single_debounce = max(0.15, min(single_debounce, self._debounce))
        self._buffers: dict[str, list[IncomingMessage]] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._last_arrival: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._deliver_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._delivering: set[str] = set()
        self._reschedule_after_deliver: set[str] = set()
        self._followup_debounce = 0.5

    async def submit(self, message: IncomingMessage) -> None:
        """Queue one inbound line; flush when the user pauses or a command arrives."""
        session = message.session_id
        batches: list[list[IncomingMessage]] = []
        command: IncomingMessage | None = None

        async with self._locks[session]:
            if looks_like_command(message):
                batch = self._take_batch_unlocked(session)
                if batch:
                    batches.append(batch)
                command = message
            else:
                now = time.time()
                gap = now - self._last_arrival.get(session, 0.0)
                if self._buffers.get(session) and gap > self._burst_window:
                    batch = self._take_batch_unlocked(session)
                    if batch:
                        batches.append(batch)
                self._buffers.setdefault(session, []).append(message)
                self._last_arrival[session] = now
                if should_flush_immediately(message):
                    batch = self._take_batch_unlocked(session)
                    if batch:
                        batches.append(batch)
                else:
                    self._schedule_unlocked(session)

        for batch in batches:
            await self._deliver(batch)
        if command is not None:
            await self._handler(command)

    async def _debounced_flush(self, session: str) -> None:
        """Sleep for debounce, then flush the buffered burst for this session."""
        delay = self._pending_delay(session)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with self._locks[session]:
            batch = self._take_batch_unlocked(session)
        try:
            await self._deliver(batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("coalesce deliver failed session=%s", session)

    def _pending_delay(self, session: str) -> float:
        """Wait this long after the last line before merging and replying."""
        return self._single_debounce

    def _schedule_unlocked(self, session: str) -> None:
        """Reset the debounce timer after another line lands in the buffer."""
        if session in self._delivering:
            self._reschedule_after_deliver.add(session)
            return
        task = self._timers.pop(session, None)
        if task is not None and not task.done():
            task.cancel()
        self._timers[session] = asyncio.create_task(self._debounced_flush(session))

    def _take_batch_unlocked(self, session: str) -> list[IncomingMessage]:
        """Pop the pending burst and cancel its debounce timer."""
        task = self._timers.pop(session, None)
        if task is not None and not task.done():
            task.cancel()
        return self._buffers.pop(session, [])

    async def _deliver(self, batch: list[IncomingMessage]) -> None:
        """Merge and hand off to the bot handler; one in-flight turn per session."""
        if not batch:
            return
        merged = merge_inbound_messages(batch)
        session = merged.session_id
        _log.info(
            "coalesce flush session=%s lines=%s preview=%r",
            session,
            len(batch),
            merged.user_text[:120],
        )
        self._delivering.add(session)
        try:
            async with self._deliver_locks[session]:
                await self._handler(merged)
        finally:
            self._delivering.discard(session)
            if session in self._reschedule_after_deliver:
                self._reschedule_after_deliver.discard(session)
                if self._buffers.get(session):
                    self._schedule_followup_unlocked(session)

    def _schedule_followup_unlocked(self, session: str) -> None:
        """After one reply finishes, merge any buffered lines with a short pause."""
        if session in self._delivering:
            self._reschedule_after_deliver.add(session)
            return
        task = self._timers.pop(session, None)
        if task is not None and not task.done():
            task.cancel()
        self._timers[session] = asyncio.create_task(self._debounced_flush_followup(session))

    async def _debounced_flush_followup(self, session: str) -> None:
        """Brief merge window for messages that arrived during the previous reply."""
        try:
            await asyncio.sleep(self._followup_debounce)
        except asyncio.CancelledError:
            return
        async with self._locks[session]:
            batch = self._take_batch_unlocked(session)
        try:
            await self._deliver(batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("coalesce deliver failed session=%s", session)
