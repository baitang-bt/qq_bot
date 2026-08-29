"""Slash-command admin list and authorization."""

from pathlib import Path

from app.command_admins import CommandAdminStore
from app.commands import CommandRouter
from app.impression.store import ImpressionStore
from app.owner import OwnerGate
from app.qq.events import C2C_EVENT, GROUP_AT_EVENT, IncomingMessage


def _c2c(content: str, openid: str = "u-admin", username: str = "管理员") -> IncomingMessage:
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


def test_command_admin_store_dedupes_by_qq(tmp_path: Path) -> None:
    """Saving admins keeps one row per QQ."""
    store = CommandAdminStore(tmp_path / "command_admins.json")
    path = store.save_admins(
        [
            {"qq": "10001", "username": "A", "user_openid": ""},
            {"qq": "10001", "username": "B", "user_openid": "x"},
            {"qq": "bad", "username": "skip"},
        ]
    )
    rows = store.list_admins()
    assert len(rows) == 1
    assert rows[0]["qq"] == "10001"
    assert path.is_file()


def test_configured_admin_can_bind_and_run_commands(tmp_path: Path) -> None:
    """An admin listed in command_admins.json may /bind and /impression."""
    CommandAdminStore(tmp_path / "command_admins.json").save_admins(
        [{"qq": "20002", "username": "小明", "user_openid": ""}]
    )
    impressions = ImpressionStore(tmp_path / "impressions")
    rec = impressions.touch("u-other", username="目标")
    rec["impression"] = "喜欢猫。"
    impressions.save(rec)
    router = CommandRouter(OwnerGate("", tmp_path), impressions)
    bind = router.try_handle(_c2c("/bind 20002", openid="u-admin"))
    assert bind is not None
    assert "已绑定" in bind
    text = router.try_handle(_c2c("/impression 目标", openid="u-admin"))
    assert text is not None
    assert "喜欢猫" in text


def test_non_admin_bind_rejected(tmp_path: Path) -> None:
    """/bind fails when QQ is not in the admin list."""
    CommandAdminStore(tmp_path / "command_admins.json").save_admins(
        [{"qq": "20002", "username": "小明", "user_openid": ""}]
    )
    impressions = ImpressionStore(tmp_path / "impressions")
    router = CommandRouter(OwnerGate("", tmp_path), impressions)
    text = router.try_handle(_c2c("/bind 99999"))
    assert text is not None
    assert "不在管理员列表" in text
