"""Gateway monitor counters."""

from app.qq.gateway_stats import GatewayStats


def test_mark_ready_resets_counters() -> None:
    """READY clears chat totals for a fresh websocket session."""
    stats = GatewayStats()
    stats.note_chat("GROUP_AT_MESSAGE_CREATE", "你好", True)
    stats.mark_ready("session-abc")
    snap = stats.snapshot()
    assert snap["chat_total"] == 0
    assert snap["session_id"] == "session-"


def test_note_chat_tracks_plain_group_messages() -> None:
    """GROUP_MESSAGE_CREATE increments the un-@ counter."""
    stats = GatewayStats()
    stats.mark_ready("session-1")
    stats.note_chat("GROUP_MESSAGE_CREATE", "你好", False)
    snap = stats.snapshot()
    assert snap["chat_total"] == 1
    assert snap["group_plain"] == 1
    assert snap["group_at"] == 0
    assert snap["last_chat_preview"] == "你好"


def test_note_chat_counts_mention_in_group_message_create() -> None:
    """GROUP_MESSAGE_CREATE with mentioned=True counts as @ traffic."""
    stats = GatewayStats()
    stats.mark_ready("session-1")
    stats.note_chat("GROUP_MESSAGE_CREATE", "<@bot> 你好", True)
    snap = stats.snapshot()
    assert snap["group_at"] == 1
    assert snap["group_plain"] == 0
