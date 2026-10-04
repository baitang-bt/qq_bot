"""Persist inbound group nicknames (openid → username) for @ rewriting."""

from __future__ import annotations

import json
import threading
from pathlib import Path


class MemberRoster:
    """Local nickname table filled from inbound authors (including skipped turns)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._names = self._load()

    def note(self, openid: str, username: str) -> None:
        """Remember a nickname. Empty names never overwrite a stored one."""
        oid = (openid or "").strip()
        name = (username or "").strip()
        if not oid or not name:
            return
        with self._lock:
            if self._names.get(oid) == name:
                return
            self._names[oid] = name
            self._save_unlocked()

    def lookup(self, openid: str) -> str:
        """Return the stored nickname, or empty if this openid has never spoken."""
        oid = (openid or "").strip()
        if not oid:
            return ""
        with self._lock:
            return self._names.get(oid, "")

    def _load(self) -> dict[str, str]:
        """Read member_names.json; missing or corrupt files start empty."""
        if not self._path.is_file():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        names: dict[str, str] = {}
        for key, value in raw.items():
            oid = str(key).strip()
            name = str(value).strip()
            if oid and name:
                names[oid] = name
        return names

    def _save_unlocked(self) -> None:
        """Write the nickname map; caller must hold the lock."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._names, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
