"""Load and save per-user impression JSON (openid-keyed; QQ number if they say it)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_QQ_IN_TEXT = re.compile(r"(?:QQ|qq)[号号码是:：=\s]*([1-9]\d{4,10})")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_-]+")


def extract_qq_from_text(text: str) -> str:
    """Pull a QQ number if the user wrote it in the message (platform does not send QQ)."""
    match = _QQ_IN_TEXT.search(text or "")
    return match.group(1) if match else ""


def _safe_filename(user_openid: str) -> str:
    """Turn an openid into a single path component."""
    name = _SAFE_ID.sub("_", user_openid.strip()) or "unknown"
    return name[:80]


class ImpressionStore:
    """One JSON file per user under data/impressions/."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, user_openid: str) -> Path:
        """Return the JSON path for this user."""
        return self._root / f"{_safe_filename(user_openid)}.json"

    def load(self, user_openid: str) -> dict[str, Any]:
        """Read the impression file, or a blank record if it does not exist."""
        path = self.path_for(user_openid)
        if not path.is_file():
            return _blank(user_openid)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _blank(user_openid)
        if not isinstance(data, dict):
            return _blank(user_openid)
        return _normalize(user_openid, data)

    def save(self, record: dict[str, Any]) -> Path:
        """Write the impression JSON atomically enough for a single bot process."""
        user_openid = str(record.get("user_openid") or "")
        path = self.path_for(user_openid)
        payload = _normalize(user_openid, record)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def touch(
        self,
        user_openid: str,
        username: str = "",
        spoken_qq: str = "",
    ) -> dict[str, Any]:
        """Create or refresh identity fields without wiping impression."""
        record = self.load(user_openid)
        if username:
            record["username"] = username
        if spoken_qq:
            record["qq"] = spoken_qq
        self.save(record)
        return self.load(user_openid)

    def list_records(self) -> list[dict[str, Any]]:
        """Load every impression JSON on disk."""
        records: list[dict[str, Any]] = []
        for path in sorted(self._root.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                records.append(_normalize(str(data.get("user_openid") or path.stem), data))
        return records

    def find_by_username(self, username: str) -> list[dict[str, Any]]:
        """Return impression records whose username matches (case-insensitive)."""
        needle = username.strip().casefold()
        if not needle:
            return []
        hits = []
        for record in self.list_records():
            if str(record.get("username") or "").strip().casefold() == needle:
                hits.append(record)
        return hits


def _blank(user_openid: str) -> dict[str, Any]:
    """Empty impression used until the model writes one."""
    return {
        "user_openid": user_openid,
        "username": "",
        "qq": "",
        "impression": "",
        "updated_at": "",
    }


def _normalize(user_openid: str, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only known fields with string values."""
    blank = _blank(user_openid)
    for key in ("user_openid", "username", "qq", "impression", "updated_at"):
        if key in data and data[key] is not None:
            blank[key] = str(data[key])
    blank["user_openid"] = user_openid or blank["user_openid"]
    return blank
