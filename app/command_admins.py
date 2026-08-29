"""Persist users allowed to run owner slash commands (/bind, /impression, …)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_QQ = re.compile(r"^[1-9]\d{4,10}$")


def normalize_admin(record: dict[str, Any]) -> dict[str, str]:
    """Keep only known admin fields with trimmed string values."""
    return {
        "qq": str(record.get("qq") or "").strip(),
        "username": str(record.get("username") or "").strip(),
        "user_openid": str(record.get("user_openid") or "").strip(),
    }


class CommandAdminStore:
    """JSON list of command admins keyed by QQ number."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def list_admins(self) -> list[dict[str, str]]:
        """Return all configured command admins."""
        data = self._read()
        admins = data.get("admins")
        if not isinstance(admins, list):
            return []
        out: list[dict[str, str]] = []
        for item in admins:
            if not isinstance(item, dict):
                continue
            row = normalize_admin(item)
            if row["qq"] and _QQ.match(row["qq"]):
                out.append(row)
        return out

    def save_admins(self, admins: list[dict[str, Any]]) -> Path:
        """Replace the admin list; dedupe by QQ and skip invalid rows."""
        cleaned: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in admins:
            row = normalize_admin(item if isinstance(item, dict) else {})
            if not row["qq"] or not _QQ.match(row["qq"]) or row["qq"] in seen:
                continue
            seen.add(row["qq"])
            cleaned.append(row)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"admins": cleaned}
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return self._path

    def find_by_qq(self, qq: str) -> dict[str, str] | None:
        """Look up one admin row by QQ number."""
        needle = qq.strip()
        if not needle:
            return None
        for row in self.list_admins():
            if row["qq"] == needle:
                return row
        return None

    def allows_openid(self, user_openid: str) -> bool:
        """True when this openid was bound for any configured admin."""
        openid = user_openid.strip()
        if not openid:
            return False
        return any(row["user_openid"] == openid for row in self.list_admins())

    def bind_openid(self, qq: str, user_openid: str) -> bool:
        """Attach an openid to the admin row with this QQ; return False if QQ missing."""
        needle = qq.strip()
        openid = user_openid.strip()
        if not needle or not openid:
            return False
        rows = self.list_admins()
        found = False
        for row in rows:
            if row["qq"] == needle:
                row["user_openid"] = openid
                found = True
        if not found:
            return False
        self.save_admins(rows)
        return True

    def _read(self) -> dict[str, Any]:
        """Load the JSON file or an empty object."""
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}
