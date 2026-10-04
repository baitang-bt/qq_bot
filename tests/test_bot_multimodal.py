"""Bot media paths: photo multimodal, sticker notes, outbound [[sticker:id]]."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from app.bot import ChatBot
from app.qq.events import GROUP_AT_EVENT, Attachment, IncomingMessage
from app.stickers.catalog import StickerCatalog


def _group_message(
    *,
    text: str = "这是什么",
    attachments: tuple[Attachment, ...] = (),
) -> IncomingMessage:
    """Build a group @ message with optional attachments."""
    return IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e1",
        msg_id="m1",
        content=text,
        user_openid="user-a",
        group_openid="group-b",
        attachments=attachments,
    )


def _bot(
    *,
    vision: MagicMock,
    llm: MagicMock | None = None,
    stickers: StickerCatalog | None = None,
) -> ChatBot:
    """Wire a ChatBot with mocked collaborators for handle-turn tests."""
    replies = MagicMock()
    replies.send_text = AsyncMock()
    replies.send_c2c_typing = AsyncMock()
    replies.send_bubbles = AsyncMock(return_value=True)
    replies.send_segments = AsyncMock(return_value=True)
    replies.send_image = AsyncMock(return_value=True)

    llm = llm or MagicMock()
    if not isinstance(llm.complete, AsyncMock):
        llm.complete = AsyncMock(return_value="看起来是一只猫")
    llm.set_stickers_prompt = MagicMock()

    memory = MagicMock()
    memory.history = MagicMock(return_value=[])
    memory.append = MagicMock()

    gate = MagicMock()
    gate.decide = MagicMock(return_value=None)
    gate.note_inbound_engagement = MagicMock()
    gate.note_bot_reply = MagicMock()
    gate.note_unmentioned_reply = MagicMock()

    impressions = MagicMock()
    impressions.touch = MagicMock(return_value={"impression": ""})

    impression_writer = MagicMock()
    impression_writer.maybe_rewrite = AsyncMock()
    impression_writer.after_turn = AsyncMock()

    commands = MagicMock()
    commands.try_handle = MagicMock(return_value=None)
    commands.try_sticker_send = MagicMock(return_value=None)

    bot = ChatBot(
        replies=replies,
        llm=llm,
        memory=memory,
        vision=vision,
        gate=gate,
        impressions=impressions,
        impression_writer=impression_writer,
        commands=commands,
        stickers=stickers,
    )
    bot._test_replies = replies  # type: ignore[attr-defined]
    bot._test_memory = memory  # type: ignore[attr-defined]
    return bot


def test_handle_turn_photo_multimodal_skips_watching_bubble(caplog, tmp_path: Path) -> None:
    """Photo attachments use fetch_images + multimodal complete; never「在看图」."""
    photo = Attachment(
        url="https://example.com/a.png",
        filename="a.png",
        content_type="image/png",
        size=12,
    )
    vision = MagicMock()
    vision.partition = MagicMock(return_value=((), (photo,)))
    vision.fetch_images = AsyncMock(return_value=[(b"pngbytes", "image/png")])
    vision.resolve_sticker_notes = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.complete = AsyncMock(return_value="看起来是一只猫")
    bot = _bot(vision=vision, llm=llm)
    message = _group_message(attachments=(photo,))

    with caplog.at_level(logging.INFO, logger="app.bot"):
        asyncio.run(bot._handle_turn(message))

    vision.fetch_images.assert_awaited_once()
    kwargs = llm.complete.await_args.kwargs
    assert kwargs["images"] == [(b"pngbytes", "image/png")]
    bot._test_replies.send_segments.assert_awaited_once()  # type: ignore[attr-defined]
    for call in bot._test_replies.send_text.await_args_list:  # type: ignore[attr-defined]
        assert "在看图" not in call.args


def test_handle_turn_sticker_uses_notes_not_images(caplog, tmp_path: Path) -> None:
    """Inbound sticker attachments resolve notes and chat without multimodal images."""
    sticker = Attachment(
        url="https://example.com/download?fileid=FID",
        filename="0123456789abcdef0123456789abcdef.gif",
        content_type="image/gif",
        size=12,
    )
    vision = MagicMock()
    vision.partition = MagicMock(return_value=((sticker,), ()))
    vision.fetch_images = AsyncMock(return_value=[])
    vision.resolve_sticker_notes = AsyncMock(return_value=["一只猫在捂脸"])

    llm = MagicMock()
    llm.complete = AsyncMock(return_value="哈哈哈这表情")
    bot = _bot(vision=vision, llm=llm)
    message = _group_message(text="", attachments=(sticker,))

    with caplog.at_level(logging.INFO, logger="app.bot"):
        asyncio.run(bot._handle_turn(message))

    vision.resolve_sticker_notes.assert_awaited_once()
    call_kwargs = vision.resolve_sticker_notes.await_args.kwargs
    assert call_kwargs.get("force_save") is False
    kwargs = llm.complete.await_args.kwargs
    assert kwargs["images"] == []
    assert kwargs["notes"] == ["一只猫在捂脸"]
    bot._test_memory.append.assert_any_call(  # type: ignore[attr-defined]
        message.session_id, "user", "[表情包] 一只猫在捂脸"
    )


def test_handle_turn_outbound_sticker_marker(tmp_path: Path) -> None:
    """Model [[sticker:id]] is parsed into send_segments with a resolvable path."""
    stickers_dir = tmp_path / "stickers"
    stickers_dir.mkdir()
    png = stickers_dir / "facepalm.png"
    png.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    index = tmp_path / "stickers.toml"
    index.write_text(
        '[[sticker]]\nid = "facepalm"\nfile = "facepalm.png"\n',
        encoding="utf-8",
    )
    catalog = StickerCatalog(index, stickers_dir)

    vision = MagicMock()
    vision.partition = MagicMock(return_value=((), ()))
    vision.fetch_images = AsyncMock(return_value=[])
    vision.resolve_sticker_notes = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.complete = AsyncMock(return_value="无奈 [[sticker:facepalm]]")
    bot = _bot(vision=vision, llm=llm, stickers=catalog)
    message = _group_message(text="无奈啊")

    asyncio.run(bot._handle_turn(message))

    bot._test_replies.send_segments.assert_awaited_once()  # type: ignore[attr-defined]
    args = bot._test_replies.send_segments.await_args  # type: ignore[attr-defined]
    segments = args.args[1]
    assert any(getattr(seg, "sticker_id", None) == "facepalm" for seg in segments)
    bot._test_memory.append.assert_any_call(  # type: ignore[attr-defined]
        message.session_id, "assistant", "无奈\n[发送表情:facepalm]"
    )


def test_handle_turn_quoted_sticker_asks_to_save(tmp_path: Path) -> None:
    """Quoted images are triaged as stickers and force-saved when the user asks."""
    quoted = Attachment(
        url="https://example.com/download?fileid=FISH",
        filename="fish.gif",
        content_type="image/gif",
        size=12,
    )
    vision = MagicMock()
    vision.partition = MagicMock(
        side_effect=[((), ()), ((quoted,), ())],
    )
    vision.fetch_images = AsyncMock(return_value=[])
    vision.resolve_sticker_notes = AsyncMock(
        return_value=["耳机金鱼 → 可发 [[sticker:goldfish_phones]]"]
    )
    llm = MagicMock()
    llm.complete = AsyncMock(return_value="好 [[sticker:goldfish_phones]]")
    bot = _bot(vision=vision, llm=llm)
    message = IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e1",
        msg_id="m1",
        content="@0x01 能存一下然后把这个表情发出来吗",
        user_openid="user-a",
        group_openid="group-b",
        quoted_text="[引用图片]",
        quoted_attachments=(quoted,),
    )
    asyncio.run(bot._handle_turn(message))
    vision.resolve_sticker_notes.assert_awaited_once()
    args, kwargs = vision.resolve_sticker_notes.await_args
    assert args[0] == (quoted,)
    assert kwargs["force_save"] is True
