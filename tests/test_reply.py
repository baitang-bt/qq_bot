"""Quote payload for passive text replies."""

from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, IncomingMessage
from app.qq.reply import build_text_payload


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
