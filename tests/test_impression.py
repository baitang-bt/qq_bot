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


def test_directory_block_empty_and_filled(tmp_path) -> None:
    """Roster lists only files with impression text; nicknames and short ids both work."""
    store = ImpressionStore(tmp_path / "impressions")
    assert "共 0 份" in store.directory_block()
    store.touch("empty-user", username="路过")
    store.touch("named", username="小明", spoken_qq="123456")
    named = store.load("named")
    named["impression"] = "说话很快。"
    store.save(named)
    store.touch("7858e28fabcdef")
    anon = store.load("7858e28fabcdef")
    anon["impression"] = "很少说话。"
    store.save(anon)
    block = store.directory_block(speaker_name="白糖")
    assert "共 2 份" in block
    assert "路过" not in block
    assert "小明（QQ 123456）" in block
    assert "（无昵称）7858e28f" in block
    assert "本轮说话的是：白糖" in block
    assert "按本名单如实说" in block


def test_others_block_includes_bodies_except_speaker(tmp_path) -> None:
    """Other members' impression text is injected; the current speaker is omitted."""
    store = ImpressionStore(tmp_path / "impressions")
    store.touch("oid-a", username="白糖")
    a = store.load("oid-a")
    a["impression"] = "白糖口吻短。"
    store.save(a)
    store.touch("oid-b", username="PiGeoN")
    b = store.load("oid-b")
    b["impression"] = "鸽子爱摸鱼。"
    store.save(b)
    block = store.others_block(exclude_openid="oid-a")
    assert "鸽子爱摸鱼" in block
    assert "【印象·PiGeoN】" in block
    assert "白糖口吻短" not in block
