"""Quote payload for passive text replies and outbound REFIDX caching."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.qq import message_cache
from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, IncomingMessage
from app.qq.reply import (
    ReplyClient,
    build_text_payload,
    parse_send_response_ids,
    remember_outbound_send,
    should_quote_inbound,
    user_turns_since_last_bot,
)
from app.config import Settings

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _settings(tmp_path: Path) -> Settings:
    """Minimal settings for ReplyClient unit tests."""
    return Settings(
        qq_app_id="",
        qq_app_secret="",
        qq_api_base="https://api.example.com",
        qq_token_url="https://example.com/token",
        llm_base_url="https://example.com/v1",
        llm_api_key="",
        llm_model="deepseek-flash",
        vision_model="deepseek-flash",
        llm_timeout_seconds=25.0,
        vision_timeout_seconds=20.0,
        memory_max_turns=12,
        data_dir=tmp_path,
        reply_policy_path=tmp_path / "reply_policy.toml",
        bot_prompt_path=tmp_path / "bot_prompt.json",
        personas_dir=tmp_path / "personas",
        personas_index_path=tmp_path / "personas.toml",
        qq_id="",
        host="127.0.0.1",
        port=8080,
        coalesce_burst_seconds=30.0,
        coalesce_debounce_seconds=3.0,
        coalesce_single_debounce_seconds=3.0,
        stickers_dir=tmp_path / "stickers",
        stickers_index_path=tmp_path / "stickers.toml",
        sticker_auto_learn=False,
        sticker_learn_max=200,
    )


def test_text_payload_quotes_inbound_message() -> None:
    """send_text bodies include message_reference pointing at the target msg."""
    message = IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e",
        msg_id="msg-1",
        content="你好",
        user_openid="u1",
        group_openid="g1",
        quote_id="REFIDX_abc",
    )
    payload = build_text_payload(message, "收到", 1)
    assert payload["msg_id"] == "msg-1"
    assert payload["message_reference"]["message_id"] == "REFIDX_abc"


def test_text_payload_falls_back_to_msg_id() -> None:
    """If the event has no msg_idx, quote the event msg_id."""
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="msg-2",
        content="hi",
        user_openid="u1",
        group_openid=None,
    )
    payload = build_text_payload(message, "嗯", 1)
    assert payload["message_reference"]["message_id"] == "msg-2"


def test_text_payload_skips_quote_when_disabled() -> None:
    """Follow-up bubbles in a split reply do not quote again."""
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="msg-3",
        content="hi",
        user_openid="u1",
        group_openid=None,
        quote_id="REFIDX_x",
    )
    payload = build_text_payload(message, "第二句", 2, quote=False)
    assert "message_reference" not in payload


def test_should_quote_after_long_gap() -> None:
    """More than three user lines since the last bot reply triggers a quote."""
    history = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "user", "content": "d"},
        {"role": "user", "content": "e"},
    ]
    assert user_turns_since_last_bot(history) == 4
    assert should_quote_inbound(history) is True


def test_should_not_quote_in_active_thread() -> None:
    """Zero to three user lines since the last bot reply skip the quote block."""
    fresh = []
    assert should_quote_inbound(fresh) is False
    history = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在"},
    ]
    assert user_turns_since_last_bot(history) == 1
    assert should_quote_inbound(history) is False
    busy = [
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "1"},
        {"role": "user", "content": "2"},
    ]
    assert user_turns_since_last_bot(busy) == 3
    assert should_quote_inbound(busy) is False
    too_many = [
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "1"},
        {"role": "user", "content": "2"},
        {"role": "user", "content": "3"},
    ]
    assert user_turns_since_last_bot(too_many) == 4
    assert should_quote_inbound(too_many) is True


def test_parse_send_response_ids_from_fixture() -> None:
    """Official send response exposes ext_info.ref_idx for quote cache keys."""
    raw = json.loads((_FIXTURES / "qq_send_with_ref_idx.json").read_text(encoding="utf-8"))
    response = httpx.Response(200, json=raw)
    ref_idx, out_id = parse_send_response_ids(response)
    assert ref_idx == "REFIDX_bot_outbound_xx=="
    assert out_id.startswith("ROBOT1.0_")


def test_remember_outbound_prefers_ref_idx() -> None:
    """Bot quote matching uses REFIDX, not the ROBOT1.0 message id."""
    message_cache.clear_for_tests()
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m",
        content="hi",
        user_openid="u1",
        group_openid=None,
    )
    remember_outbound_send(
        message,
        text="哦就是那种原汁原味",
        ref_idx="REFIDX_bot_outbound_xx==",
        out_id="ROBOT1.0_abc",
    )
    assert message_cache.is_bot_msg_idx("REFIDX_bot_outbound_xx==") is True
    assert message_cache.is_bot_msg_idx("ROBOT1.0_abc") is False
    assert message_cache.lookup("REFIDX_bot_outbound_xx==") == "哦就是那种原汁原味"
    assert message_cache.lookup("ROBOT1.0_abc") == "哦就是那种原汁原味"
    message_cache.clear_for_tests()


def test_remember_outbound_id_only_does_not_mark_bot_idx() -> None:
    """Missing ref_idx caches text under id but does not mark bot REFIDX set."""
    message_cache.clear_for_tests()
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m",
        content="hi",
        user_openid="u1",
        group_openid=None,
    )
    remember_outbound_send(
        message,
        text="仅有 id",
        ref_idx="",
        out_id="ROBOT1.0_only",
    )
    assert message_cache.is_bot_msg_idx("ROBOT1.0_only") is False
    assert message_cache.lookup("ROBOT1.0_only") == "仅有 id"
    message_cache.clear_for_tests()


def test_send_text_remembers_ref_idx_from_response(tmp_path: Path) -> None:
    """ReplyClient._post stores ext_info.ref_idx after a successful text send."""
    from unittest.mock import patch

    message_cache.clear_for_tests()
    tokens = MagicMock()
    tokens.auth_headers = AsyncMock(return_value={"Authorization": "Bearer t"})
    client = ReplyClient(_settings(tmp_path), tokens)
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m1",
        content="hi",
        user_openid="user-1",
        group_openid=None,
    )
    raw = json.loads((_FIXTURES / "qq_send_with_ref_idx.json").read_text(encoding="utf-8"))

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):  # noqa: A002
            return httpx.Response(200, json=raw)

    async def run() -> bool:
        with patch("app.qq.reply.httpx.AsyncClient", FakeClient):
            return await client.send_text(message, "原汁原味版本", quote=False)

    assert asyncio.run(run()) is True
    assert message_cache.is_bot_msg_idx("REFIDX_bot_outbound_xx==") is True
    assert message_cache.lookup("REFIDX_bot_outbound_xx==") == "原汁原味版本"
    message_cache.clear_for_tests()
