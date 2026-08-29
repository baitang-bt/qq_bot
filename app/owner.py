"""Decide whether the sender may run owner-only slash commands."""

from __future__ import annotations

import json
from pathlib import Path

from app.command_admins import CommandAdminStore
from app.impression.store import ImpressionStore
from app.qq.events import IncomingMessage


class OwnerGate:
    """Authorize against .env QQ_ID and data/command_admins.json."""

    def __init__(self, owner_id: str, data_dir: Path) -> None:
        self._owner_id = owner_id.strip()
        self._path = data_dir / "owner.json"
        self._admins = CommandAdminStore(data_dir / "command_admins.json")

    def allows(self, message: IncomingMessage, impressions: ImpressionStore) -> bool:
        """True if this sender matches QQ_ID, a configured admin, or a bound openid."""
        if self._allows_legacy_owner(message, impressions):
            return True
        openid = message.user_openid.strip()
        if openid and self._admins.allows_openid(openid):
            return True
        record = impressions.load(message.user_openid)
        qq = str(record.get("qq") or "").strip()
        if qq and self._admins.find_by_qq(qq):
            self._admins.bind_openid(qq, openid)
            return True
        return False

    def bind_claimed_qq(self, message: IncomingMessage, claimed_qq: str) -> str:
        """Bind this openid when claimed QQ matches QQ_ID or an admin row."""
        claimed = claimed_qq.strip()
        if not claimed:
            return "用法：/bind QQ号。请私聊发送。"
        if self._owner_id and claimed == self._owner_id:
            self._bind_legacy(message.user_openid)
            return "已绑定主人。之后群里也可以用 /impression。"
        if self._admins.find_by_qq(claimed):
            self._admins.bind_openid(claimed, message.user_openid)
            return "已绑定指令权限。之后群里 @ 机器人即可使用 /impression 等指令。"
        if self._owner_id:
            return "这个号不在管理员列表里，也没有与 .env 的 QQ_ID 一致，无法绑定。"
        if not self._admins.list_admins():
            return "还没配置管理员 QQ，无法绑定。"
        return "这个 QQ 不在管理员列表里，无法绑定。"

    def _allows_legacy_owner(self, message: IncomingMessage, impressions: ImpressionStore) -> bool:
        """Check the original single-owner QQ_ID path and owner.json bind."""
        if not self._owner_id:
            return False
        bound = self._bound_openid_legacy()
        if bound and message.user_openid == bound:
            return True
        if message.user_openid == self._owner_id:
            self._bind_legacy(message.user_openid)
            return True
        if message.username.strip() == self._owner_id:
            self._bind_legacy(message.user_openid)
            return True
        record = impressions.load(message.user_openid)
        if str(record.get("qq") or "") == self._owner_id:
            self._bind_legacy(message.user_openid)
            return True
        return False

    def _bound_openid_legacy(self) -> str:
        """Read the previously saved owner openid from owner.json."""
        if not self._path.is_file():
            return ""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(data, dict):
            return ""
        return str(data.get("user_openid") or "").strip()

    def _bind_legacy(self, user_openid: str) -> None:
        """Remember this openid as the legacy QQ_ID owner."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"user_openid": user_openid}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
