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

    def filled_records(self) -> list[dict[str, Any]]:
        """Return impression files that actually have body text."""
        filled: list[dict[str, Any]] = []
        for record in self.list_records():
            if str(record.get("impression") or "").strip():
                filled.append(record)
        return filled

    def directory_label(self, record: dict[str, Any]) -> str:
        """Format one impression row as a roster line (nickname, optional QQ, or short id)."""
        username = str(record.get("username") or "").strip()
        qq = str(record.get("qq") or "").strip()
        user_openid = str(record.get("user_openid") or "").strip()
        if username and qq:
            return f"{username}（QQ {qq}）"
        if username:
            return username
        short = user_openid[:8] if user_openid else "?"
        return f"（无昵称）{short}"

    def directory_block(self, speaker_name: str = "") -> str:
        """Compact roster of files that have impression text."""
        filled = self.filled_records()
        lines = [
            f"【已知印象】共 {len(filled)} 份"
            "（按昵称对应；问有几份、是谁时按本名单如实说，"
            "不要用聊天记录里的旧说法顶替。"
            "其他人的正文在下面【印象·昵称】段，评价时用那些内容。）"
        ]
        for record in filled:
            lines.append(f"- {self.directory_label(record)}")
        speaker = (speaker_name or "").strip()
        if speaker:
            lines.append(f"本轮说话的是：{speaker}")
        return "\n".join(lines)

    def others_block(self, exclude_openid: str = "") -> str:
        """Full impression bodies for roster members other than the current speaker."""
        skip = (exclude_openid or "").strip()
        chunks: list[str] = []
        for record in self.filled_records():
            oid = str(record.get("user_openid") or "").strip()
            if skip and oid == skip:
                continue
            body = str(record.get("impression") or "").strip()
            if not body:
                continue
            label = self.directory_label(record)
            chunks.append(f"【印象·{label}】\n{body}")
        if not chunks:
            return ""
        header = (
            "【其他人的印象正文】评价、对比、提起某人时用对应档；"
            "不要串到当前说话人身上，也不要把全文逐字念给群友。"
        )
        return header + "\n\n" + "\n\n".join(chunks)

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
