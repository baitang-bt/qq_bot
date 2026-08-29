"""Quote payload for passive text replies."""

from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, IncomingMessage
from app.qq.reply import build_text_payload, should_quote_inbound, user_turns_since_last_bot


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
