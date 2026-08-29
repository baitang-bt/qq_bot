"""Merge consecutive inbound bursts before one bot reply."""

import asyncio

from app.qq.coalesce import MessageCoalescer, should_flush_immediately
from app.qq.events import GROUP_AT_EVENT, GROUP_MESSAGE_EVENT, IncomingMessage, merge_inbound_messages


def _msg(
    content: str,
    *,
    msg_id: str = "m1",
    mentioned: bool = False,
    session: str = "group:g1:u1",
) -> IncomingMessage:
    """Build a minimal group message for coalesce tests."""
    event = GROUP_AT_EVENT if mentioned else GROUP_MESSAGE_EVENT
    group, user = "g1", "u1"
    if session.startswith("group:"):
        parts = session.split(":")
        if len(parts) >= 3:
            group, user = parts[1], parts[2]
    return IncomingMessage(
        event_type=event,
        event_id="e",
        msg_id=msg_id,
        content=content,
        user_openid=user,
        group_openid=group,
    )


def test_merge_inbound_messages_joins_text() -> None:
    """Multiple lines become one user_text block separated by newlines."""
    merged = merge_inbound_messages([_msg("你好", msg_id="m1"), _msg("在吗", msg_id="m2")])
    assert merged.msg_id == "m2"
    assert merged.user_text == "你好\n在吗"


def test_merge_marks_mentioned_if_any_line_was_at() -> None:
    """A burst that started with @ stays eligible for @ reply policy."""
    merged = merge_inbound_messages(
        [_msg("@0x01 你好", msg_id="m1", mentioned=True), _msg("继续", msg_id="m2")]
    )
    assert merged.mentioned is True
    assert merged.event_type == GROUP_AT_EVENT


def test_coalescer_waits_for_burst() -> None:
    """Back-to-back lines within the burst window are handled once."""
    handled: list[IncomingMessage] = []

    async def handler(message: IncomingMessage) -> None:
        handled.append(message)

    async def run() -> None:
        coalescer = MessageCoalescer(handler, burst_window=30, debounce=0.15)
        await coalescer.submit(_msg("第一句", msg_id="m1"))
        await coalescer.submit(_msg("第二句", msg_id="m2"))
        await asyncio.sleep(0.25)

    asyncio.run(run())
    assert len(handled) == 1
    assert handled[0].user_text == "第一句\n第二句"


def test_should_flush_immediately_for_image_and_text() -> None:
    """One bubble with caption+image bypasses coalesce debounce."""
    from app.qq.events import Attachment

    assert should_flush_immediately(_msg("这张图咋样")) is False
    assert should_flush_immediately(_msg("@0x01 在吗", mentioned=True)) is False
    message = IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e",
        msg_id="m1",
        content="这张图咋样",
        user_openid="u1",
        group_openid="g1",
        attachments=(
            Attachment(
                url="https://example.com/a.png",
                filename="a.png",
                content_type="image/png",
                size=1,
            ),
        ),
    )
    assert should_flush_immediately(message) is True


def test_coalescer_flushes_image_text_immediately() -> None:
    """Image+text does not wait for the debounce timer."""
    handled: list[IncomingMessage] = []

    async def handler(message: IncomingMessage) -> None:
        handled.append(message)

    async def run() -> None:
        from app.qq.events import Attachment

        coalescer = MessageCoalescer(handler, burst_window=30, debounce=2.0)
        message = IncomingMessage(
            event_type=GROUP_MESSAGE_EVENT,
            event_id="e",
            msg_id="m1",
            content="这张图咋样",
            user_openid="u1",
            group_openid="g1",
            attachments=(
                Attachment(
                    url="https://example.com/a.png",
                    filename="a.png",
                    content_type="image/png",
                    size=1,
                ),
            ),
        )
        await coalescer.submit(message)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert len(handled) == 1
    assert handled[0].user_text == "这张图咋样"


def test_coalescer_single_line_uses_shorter_debounce() -> None:
    """One line alone should flush before the multi-line debounce."""
    handled: list[IncomingMessage] = []

    async def handler(message: IncomingMessage) -> None:
        handled.append(message)

    async def run() -> None:
        coalescer = MessageCoalescer(
            handler,
            burst_window=30,
            debounce=1.0,
            single_debounce=0.12,
        )
        await coalescer.submit(_msg("单条", msg_id="m1"))
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert len(handled) == 1
    assert handled[0].user_text == "单条"


def test_coalescer_handler_does_not_hold_session_lock() -> None:
    """While handle() runs, submit() for the same session must not block."""
    handled: list[IncomingMessage] = []
    gate = asyncio.Event()

    async def handler(message: IncomingMessage) -> None:
        handled.append(message)
        if message.user_text == "第一句":
            gate.set()
            await asyncio.sleep(0.15)

    async def run() -> None:
        coalescer = MessageCoalescer(handler, burst_window=30, single_debounce=0.05)
        first = asyncio.create_task(coalescer.submit(_msg("第一句", msg_id="m1")))
        await gate.wait()
        await coalescer.submit(_msg("第二句", msg_id="m2"))
        await first
        await asyncio.sleep(0.6)

    asyncio.run(run())
    assert len(handled) == 2
    assert handled[0].user_text == "第一句"
    assert handled[1].user_text == "第二句"


def test_coalescer_flushes_command_immediately() -> None:
    """Slash commands bypass the debounce and flush pending chat first."""
    handled: list[IncomingMessage] = []

    async def handler(message: IncomingMessage) -> None:
        handled.append(message)

    async def run() -> None:
        coalescer = MessageCoalescer(handler, burst_window=30, debounce=1.0)
        await coalescer.submit(_msg("攒着", msg_id="m1"))
        await coalescer.submit(_msg("/bind 10001", msg_id="m2", mentioned=True))
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert len(handled) == 2
    assert handled[0].user_text == "攒着"
    assert handled[1].content.startswith("/bind")


def test_coalescer_does_not_cancel_handle_mid_delivery() -> None:
    """New lines during handle() must not cancel the in-flight LLM/reply turn."""
    handled: list[str] = []
    gate = asyncio.Event()

    async def handler(message: IncomingMessage) -> None:
        handled.append(message.user_text)
        if message.user_text == "第一句":
            gate.set()
            await asyncio.sleep(0.15)

    async def run() -> None:
        coalescer = MessageCoalescer(handler, burst_window=30, single_debounce=0.05)
        await coalescer.submit(_msg("第一句", msg_id="m1"))
        await asyncio.sleep(0.06)
        await gate.wait()
        await coalescer.submit(_msg("第二句", msg_id="m2"))
        await asyncio.sleep(0.6)

    asyncio.run(run())
    assert handled == ["第一句", "第二句"]
