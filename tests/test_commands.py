"""Owner slash commands."""

from pathlib import Path

from app.commands import CommandRouter
from app.impression.store import ImpressionStore
from app.owner import OwnerGate
from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, GROUP_MESSAGE_EVENT, IncomingMessage


def _c2c(content: str, openid: str = "owner-1", username: str = "白糖") -> IncomingMessage:
    """Build a private-chat message."""
    return IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m",
        content=content,
        user_openid=openid,
        group_openid=None,
        username=username,
    )


def _group(
    content: str,
    openid: str = "owner-1",
    username: str = "白糖",
    role: str = "owner",
) -> IncomingMessage:
    """Build a group-at message."""
    return IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e",
        msg_id="m",
        content=content,
        user_openid=openid,
        group_openid="group-1",
        username=username,
        member_role=role,
    )


def test_impression_after_bind(tmp_path: Path) -> None:
    """/impression works after /bind with the configured QQ_ID."""
    store = ImpressionStore(tmp_path / "impressions")
    rec = store.touch("u-other", username="小明")
    rec["impression"] = "说话很快，喜欢问天气。"
    store.save(rec)
    router = CommandRouter(OwnerGate("10001", tmp_path), store)
    assert "已绑定" in (router.try_handle(_c2c("/bind 10001")) or "")
    text = router.try_handle(_c2c("/impression 小明"))
    assert text is not None
    assert "说话很快" in text


def test_group_owner_role_is_not_enough(tmp_path: Path) -> None:
    """Being 群主 does not authorize commands; QQ bind is required."""
    store = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("10001", tmp_path), store)
    text = router.try_handle(_group("/impression 白糖", role="owner"))
    assert text is not None
    assert "管理员" in text


def test_bind_wrong_qq_rejected(tmp_path: Path) -> None:
    """/bind with a number that is not QQ_ID does not bind."""
    store = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("10001", tmp_path), store)
    text = router.try_handle(_c2c("/bind 99999"))
    assert text is not None
    assert "不一致" in text or "不在管理员列表" in text
    denied = router.try_handle(_c2c("/impression 小明"))
    assert denied is not None
    assert "管理员" in denied


def test_group_command_after_c2c_bind(tmp_path: Path) -> None:
    """After C2C /bind, the same openid may run /impression in a group."""
    store = ImpressionStore(tmp_path / "impressions")
    rec = store.touch("u-other", username="小明")
    rec["impression"] = "记过。"
    store.save(rec)
    router = CommandRouter(OwnerGate("10001", tmp_path), store)
    router.try_handle(_c2c("/bind 10001"))
    text = router.try_handle(_group("/impression 小明"))
    assert text is not None
    assert "记过" in text


def test_unknown_slash_is_still_command(tmp_path: Path) -> None:
    """A leading slash is treated as a command, not a normal chat turn."""
    store = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("owner-1", tmp_path), store)
    text = router.try_handle(_c2c("/foo"))
    assert text is not None
    assert "impression" in text


def test_group_slash_without_at_is_ignored(tmp_path: Path) -> None:
    """Un-@ group lines starting with / are not treated as commands."""
    store = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("10001", tmp_path), store)
    router.try_handle(_c2c("/bind 10001"))
    plain = IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e",
        msg_id="m",
        content="/impression 小明",
        user_openid="owner-1",
        group_openid="group-1",
        username="白糖",
    )
    assert plain.mentioned is False
    assert router.try_handle(plain) is None


def test_non_command_returns_none(tmp_path: Path) -> None:
    """Normal chat is not intercepted."""
    store = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("x", tmp_path), store)
    assert router.try_handle(_c2c("你好")) is None
