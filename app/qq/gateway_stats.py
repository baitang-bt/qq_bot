"""In-memory counters for QQ gateway event monitoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class GatewayStats:
    """Rolling gateway metrics exposed on /health and admin UI."""

    session_id: str = ""
    ready_at: float | None = None
    chat_total: int = 0
    group_at: int = 0
    group_plain: int = 0
    c2c: int = 0
    ignored_events: int = 0
    full_message_enabled: int = 0
    last_event_type: str = ""
    last_chat_preview: str = ""
    last_chat_at: float | None = None

    def mark_ready(self, session_id: str) -> None:
        """Reset counters when the websocket session becomes READY."""
        self.session_id = session_id
        self.ready_at = time.time()
        self.chat_total = 0
        self.group_at = 0
        self.group_plain = 0
        self.c2c = 0
        self.ignored_events = 0
        self.full_message_enabled = 0
        self.last_event_type = "READY"
        self.last_chat_preview = ""
        self.last_chat_at = None

    def note_event(self, event_type: str) -> None:
        """Record the latest dispatch event name."""
        self.last_event_type = event_type

    def note_chat(self, event_type: str, preview: str, mentioned: bool) -> None:
        """Count an inbound chat line and keep a short preview for diagnostics."""
        self.chat_total += 1
        self.last_chat_at = time.time()
        self.last_event_type = event_type
        self.last_chat_preview = preview[:80]
        if event_type == "C2C_MESSAGE_CREATE":
            self.c2c += 1
        elif event_type in {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
            if mentioned:
                self.group_at += 1
            else:
                self.group_plain += 1

    def note_full_message_enabled(self) -> None:
        """Increment when a group turns on full-message delivery."""
        self.full_message_enabled += 1

    def note_ignored(self) -> None:
        """Count dispatch events we did not turn into chat handling."""
        self.ignored_events += 1

    def snapshot(self) -> dict[str, object]:
        """JSON-safe dict for /health and the admin dashboard."""
        idle_seconds = None
        if self.ready_at is not None:
            idle_seconds = int(time.time() - self.ready_at)
        return {
            "session_id": self.session_id[:8] if self.session_id else "",
            "chat_total": self.chat_total,
            "group_at": self.group_at,
            "group_plain": self.group_plain,
            "c2c": self.c2c,
            "full_message_enabled": self.full_message_enabled,
            "ignored_events": self.ignored_events,
            "last_event_type": self.last_event_type,
            "last_chat_preview": self.last_chat_preview,
            "last_chat_at": self.last_chat_at,
            "idle_seconds": idle_seconds,
            "needs_full_message_setup": self.chat_total == 0 and self.group_at == 0,
        }


# One process-wide monitor shared by gateway and /health.
MONITOR = GatewayStats()
