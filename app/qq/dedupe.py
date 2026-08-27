"""In-memory de-duplication for webhook retries of the same msg_id."""

from __future__ import annotations

import time


class MessageDedupe:
    """Remember recent msg_ids so QQ retries are not processed twice."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def already_handled(self, msg_id: str) -> bool:
        """Return True if msg_id was seen recently; otherwise record it."""
        now = time.time()
        self._purge(now)
        if not msg_id:
            return False
        if msg_id in self._seen:
            return True
        self._seen[msg_id] = now
        return False

    def _purge(self, now: float) -> None:
        """Drop expired ids so the map cannot grow without bound."""
        expired = [key for key, seen_at in self._seen.items() if now - seen_at > self._ttl]
        for key in expired:
            del self._seen[key]
