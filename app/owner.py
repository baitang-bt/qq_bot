"""Decide whether the sender may run owner-only slash commands."""

from __future__ import annotations

import json
from pathlib import Path

from app.impression.store import ImpressionStore
from app.qq.events import IncomingMessage


class OwnerGate:
    """Authorize against .env QQ_ID. Group events have no QQ number; bind via /bind."""

    def __init__(self, owner_id: str, data_dir: Path) -> None:
        self._owner_id = owner_id.strip()
        self._path = data_dir / "owner.json"

    def allows(self, message: IncomingMessage, impressions: ImpressionStore) -> bool:
        """True if this sender matches QQ_ID or the openid saved by /bind."""
        if not self._owner_id:
            return False
        bound = self._bound_openid()
        if bound and message.user_openid == bound:
            return True
        if message.user_openid == self._owner_id:
            self._bind(message.user_openid)
            return True
        if message.username.strip() == self._owner_id:
            self._bind(message.user_openid)
            return True
        record = impressions.load(message.user_openid)
        if str(record.get("qq") or "") == self._owner_id:
            self._bind(message.user_openid)
            return True
        return False

    def bind_claimed_qq(self, message: IncomingMessage, claimed_qq: str) -> str:
        """Bind this openid if claimed_qq equals QQ_ID. Does not echo the number."""
        claimed = claimed_qq.strip()
        if not self._owner_id:
            return "还没配置 QQ_ID，无法绑定。"
        if not claimed:
            return "用法：/bind QQ号（须与 .env 里的 QQ_ID 相同）。请私聊发送。"
        if claimed != self._owner_id:
            return "这个号和配置的 QQ_ID 不一致，没有绑定。"
        self._bind(message.user_openid)
        return "已绑定主人。之后群里也可以用 /impression。"

    def _bound_openid(self) -> str:
        """Read the previously saved owner openid, if any."""
        if not self._path.is_file():
            return ""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        if not isinstance(data, dict):
            return ""
        return str(data.get("user_openid") or "").strip()

    def _bind(self, user_openid: str) -> None:
        """Remember this openid as the owner for later command checks."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"user_openid": user_openid}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
