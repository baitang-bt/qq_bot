"""Impression roster, mention rewrite, and skip-path nickname recording."""

from pathlib import Path

from app.bot import ChatBot
from app.personas.catalog import PersonaCatalog
from app.prompt_book import PromptBook
from app.qq.events import GROUP_MESSAGE_EVENT, IncomingMessage, parse_incoming
from app.qq.mentions import display_name, rewrite_mentions
from app.qq.roster import MemberRoster
from unittest.mock import AsyncMock, MagicMock
import asyncio


def test_rewrite_known_and_unknown() -> None:
    """`<@!openid>` becomes @昵称; unknown openids become @未知(前8位)."""
    names = {"oid-sugar": "白糖"}

    def resolve(oid: str) -> str:
        return display_name(oid, names.get(oid, ""), "")

    text = rewrite_mentions("你好 <@!oid-sugar> 和 <@deadbeef012345>", resolve)
    assert "@白糖" in text
    assert "<@" not in text
    assert "@未知(deadbeef)" in text


def test_roster_skip_still_notes(tmp_path: Path) -> None:
    """Inbound authors are stored even when reply policy skips the turn."""
    roster = MemberRoster(tmp_path / "member_names.json")
    impressions = MagicMock()
    impressions.load = MagicMock(return_value={})
    impressions.touch = MagicMock()
    impressions.directory_block = MagicMock()
    impressions.others_block = MagicMock(return_value="")

    replies = MagicMock()
    replies.send_c2c_typing = AsyncMock()
    llm = MagicMock()
    llm.complete = AsyncMock()
    memory = MagicMock()
    vision = MagicMock()
    gate = MagicMock()
    gate.decide = MagicMock(return_value="unmentioned")
    gate.note_inbound_engagement = MagicMock()
    commands = MagicMock()
    commands.try_handle = MagicMock(return_value=None)
    commands.try_sticker_send = MagicMock(return_value=None)
    writer = MagicMock()

    bot = ChatBot(
        replies=replies,
        llm=llm,
        memory=memory,
        vision=vision,
        gate=gate,
        impressions=impressions,
        impression_writer=writer,
        commands=commands,
        roster=roster,
    )
    message = IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e1",
        msg_id="m1",
        content="路过",
        user_openid="oid-skip",
        group_openid="g1",
        username="小明",
    )
    asyncio.run(bot._handle_turn(message))
    assert roster.lookup("oid-skip") == "小明"
    llm.complete.assert_not_called()
    again = MemberRoster(tmp_path / "member_names.json")
    assert again.lookup("oid-skip") == "小明"


def test_empty_username_does_not_overwrite(tmp_path: Path) -> None:
    """A later inbound without a nickname keeps the stored name."""
    roster = MemberRoster(tmp_path / "member_names.json")
    roster.note("oid-a", "白糖")
    roster.note("oid-a", "")
    assert roster.lookup("oid-a") == "白糖"


def test_parse_payload_mentions() -> None:
    """Optional mentions[] usernames are attached for the roster."""
    payload = {
        "op": 0,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "msg-1",
            "content": "看这个 <@!oid-b>",
            "group_openid": "g1",
            "author": {"member_openid": "oid-a", "username": "白糖"},
            "mentions": [{"id": "oid-b", "username": "小明"}],
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert ("oid-b", "小明") in message.mention_names
    assert message.mentioned is True
    assert "<@" in message.content


def test_prompt_book_directory_before_impression(tmp_path: Path) -> None:
    """Known-impression roster is injected above the current speaker's body."""
    pack = tmp_path / "personas" / "demo"
    pack.mkdir(parents=True)
    (pack / "persona.txt").write_text("你是测试机器人。", encoding="utf-8")
    catalog = PersonaCatalog(tmp_path / "personas.toml", tmp_path / "personas")
    catalog.set_active("demo")
    book = PromptBook(catalog)
    text = book.system_text(
        impression="喜欢短句",
        directory="【已知印象】共 2 份\n- 白糖\n本轮说话的是：白糖",
        others="【印象·PiGeoN】\n鸽子爱摸鱼。",
    )
    assert text.index("共 2 份") < text.index("印象·PiGeoN")
    assert text.index("印象·PiGeoN") < text.index("对该用户的印象")
    assert "喜欢短句" in text
