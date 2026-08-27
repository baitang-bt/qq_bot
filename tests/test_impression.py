"""Per-user impression JSON store and QQ-number extraction from text."""

from app.impression.store import ImpressionStore, extract_qq_from_text
from app.qq.events import C2C_EVENT, parse_incoming


def test_extract_qq_from_text() -> None:
    """QQ numbers written in chat are captured; the API does not send QQ."""
    assert extract_qq_from_text("我QQ号是123456789") == "123456789"
    assert extract_qq_from_text("你好") == ""


def test_store_roundtrip(tmp_path) -> None:
    """touch + save persist impression for later replies."""
    store = ImpressionStore(tmp_path / "impressions")
    store.touch("openid-a", username="小明", spoken_qq="123456")
    record = store.load("openid-a")
    record["impression"] = "喜欢短句，说话直接。"
    store.save(record)
    again = store.load("openid-a")
    assert again["username"] == "小明"
    assert again["qq"] == "123456"
    assert "短句" in again["impression"]


def test_parse_username() -> None:
    """Author username is kept on IncomingMessage."""
    payload = {
        "op": 0,
        "t": C2C_EVENT,
        "d": {
            "id": "msg-1",
            "content": "hi",
            "author": {"user_openid": "user-aaa", "username": "小明"},
        },
    }
    message = parse_incoming(payload)
    assert message is not None
    assert message.username == "小明"
