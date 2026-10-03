"""Reply policy gates: kinds, keywords, and per-session rate limits."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.qq.events import (
    C2C_EVENT,
    GROUP_AT_EVENT,
    GROUP_MESSAGE_EVENT,
    Attachment,
    IncomingMessage,
)
from app.reply_policy import ReplyGate, is_beijing_unmentioned_peak, load_reply_settings


def _c2c(content: str = "你好", attachments: tuple[Attachment, ...] = ()) -> IncomingMessage:
    """Build a private-chat message for policy tests."""
    return IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e1",
        msg_id="m1",
        content=content,
        user_openid="user-a",
        group_openid=None,
        attachments=attachments,
    )


def test_load_toml(tmp_path: Path) -> None:
    """Toml values override defaults."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "enabled = true\nc2c = false\nmin_interval_seconds = 30\nrequire_keywords = [\"ping\"]\n",
        encoding="utf-8",
    )
    settings = load_reply_settings(path)
    assert settings.c2c is False
    assert settings.min_interval_seconds == 30
    assert settings.require_keywords == ("ping",)


def test_skip_group_when_group_off(tmp_path: Path) -> None:
    """Group messages are dropped when group=false."""
    path = tmp_path / "reply_policy.toml"
    path.write_text("group = false\n", encoding="utf-8")
    gate = ReplyGate(path)
    group_msg = IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e2",
        msg_id="m2",
        content="你好",
        user_openid="user-a",
        group_openid="group-b",
        attachments=(),
    )
    assert gate.decide(group_msg, now=1.0) == "group_off"
    assert gate.decide(_c2c(), now=1.0) is None


def test_voice_uses_asr_text() -> None:
    """Voice attachments with asr_refer_text count as user text."""
    voice = Attachment(
        url="https://example.com/a.silk",
        filename="voice.silk",
        content_type="voice",
        size=10,
        asr_refer_text="明天天气怎么样",
    )
    message = _c2c(content="", attachments=(voice,))
    assert message.user_text == "明天天气怎么样"
    gate = ReplyGate(Path("/no/such.toml"))
    assert gate.decide(message, now=1.0) is None


def test_voice_without_asr_skipped() -> None:
    """Voice with no official ASR text is not answered."""
    voice = Attachment(
        url="https://example.com/a.silk",
        filename="voice.silk",
        content_type="voice",
        size=10,
    )
    message = _c2c(content="", attachments=(voice,))
    gate = ReplyGate(Path("/no/such.toml"))
    assert gate.decide(message, now=1.0) == "voice_no_asr"


def test_pure_image_allowed_when_on_image(tmp_path: Path) -> None:
    """Image-only messages pass the gate when on_image=true."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "on_image = true\nmax_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    image = Attachment(
        url="https://example.com/a.png",
        filename="a.png",
        content_type="image/png",
        size=12,
    )
    message = _c2c(content="", attachments=(image,))
    assert gate.decide(message, now=1.0) is None


def test_pure_image_blocked_when_on_image_false(tmp_path: Path) -> None:
    """Image-only messages are skipped when on_image=false."""
    path = tmp_path / "reply_policy.toml"
    path.write_text("on_image = false\n", encoding="utf-8")
    gate = ReplyGate(path)
    image = Attachment(
        url="https://example.com/a.png",
        filename="a.png",
        content_type="image/png",
        size=12,
    )
    message = _c2c(content="", attachments=(image,))
    assert gate.decide(message, now=1.0) == "kind_off"


def test_min_interval(tmp_path: Path) -> None:
    """A second message inside the cooldown is skipped."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "min_interval_seconds = 10\nmax_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_c2c(), now=100.0) is None
    assert gate.decide(_c2c(), now=105.0) == "min_interval"
    assert gate.decide(_c2c(), now=111.0) is None


def _group_plain(content: str) -> IncomingMessage:
    """Build an un-@ group message."""
    return IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e3",
        msg_id="m3",
        content=content,
        user_openid="user-a",
        group_openid="group-b",
        attachments=(),
    )


def test_beijing_peak_hours_window() -> None:
    """Mon-Fri 09:00-12:00 and 14:00-18:00 Beijing are peak; lunch and weekends are not."""
    tz = ZoneInfo("Asia/Shanghai")
    monday_9 = datetime(2026, 8, 31, 9, 0, tzinfo=tz).timestamp()
    monday_10 = datetime(2026, 8, 31, 10, 0, tzinfo=tz).timestamp()
    monday_12 = datetime(2026, 8, 31, 12, 0, tzinfo=tz).timestamp()
    monday_lunch = datetime(2026, 8, 31, 13, 0, tzinfo=tz).timestamp()
    monday_14 = datetime(2026, 8, 31, 14, 0, tzinfo=tz).timestamp()
    monday_15 = datetime(2026, 8, 31, 15, 0, tzinfo=tz).timestamp()
    monday_18 = datetime(2026, 8, 31, 18, 0, tzinfo=tz).timestamp()
    saturday_10 = datetime(2026, 8, 29, 10, 0, tzinfo=tz).timestamp()
    assert is_beijing_unmentioned_peak(monday_9) is True
    assert is_beijing_unmentioned_peak(monday_10) is True
    assert is_beijing_unmentioned_peak(monday_12) is False
    assert is_beijing_unmentioned_peak(monday_lunch) is False
    assert is_beijing_unmentioned_peak(monday_14) is True
    assert is_beijing_unmentioned_peak(monday_15) is True
    assert is_beijing_unmentioned_peak(monday_18) is False
    assert is_beijing_unmentioned_peak(saturday_10) is False


def test_unmentioned_blocked_during_beijing_peak(tmp_path: Path) -> None:
    """Un-@ named messages are skipped during weekday peak hours when off_peak_only is on."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_off_peak_only = true\n"
        "unmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    peak = datetime(2026, 8, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    off_peak = datetime(2026, 8, 31, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    assert gate.decide(_group_plain("0x01 这怎么弄？"), now=peak) == "unmentioned_peak_hours"
    assert gate.decide(_group_plain("0x01 这怎么弄？"), now=off_peak) is None


def test_unmentioned_skips_bare_question(tmp_path: Path) -> None:
    """Questions without bot name or prior thread are ignored."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("这怎么弄？"), now=1.0) == "unmentioned_no_hook"


def test_unmentioned_skips_chatter(tmp_path: Path) -> None:
    """Idle group chat without bot name or thread is ignored."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("我先下班了"), now=1.0) == "unmentioned_no_hook"


def test_unmentioned_allows_bot_name(tmp_path: Path) -> None:
    """Naming the bot in text allows an un-@ reply."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("0x01 在吗"), now=1.0) is None


def test_unmentioned_thread_continuation(tmp_path: Path) -> None:
    """After bot replied, follow-up un-@ lines in the same thread are allowed."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "unmentioned_thread_minutes = 30\nmax_per_session_per_minute = 10\n"
        "unmentioned_cooldown_seconds = 0\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    msg = _group_plain("好的那继续")
    gate.note_bot_reply(msg.session_id, now=100.0)
    assert gate.decide(_group_plain("测试一下非@回复"), now=120.0) is None
    assert gate.decide(_group_plain("哑巴了"), now=121.0) is None
    assert gate.decide(_group_plain("我先下班了"), now=122.0) == "unmentioned_no_hook"


def test_unmentioned_named_has_cooldown(tmp_path: Path) -> None:
    """Un-@ messages naming the bot can be answered, then the same group waits ~5s."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_cooldown_seconds = 5\n"
        "unmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("0x01 这怎么弄？"), now=100.0) is None
    gate.note_unmentioned_reply("group-b", now=100.0)
    assert gate.decide(_group_plain("0x01 还有谁会？"), now=102.0) == "unmentioned_cooldown"
    assert gate.decide(_group_plain("0x01 那要怎么做？"), now=106.0) is None


def test_unmentioned_cooldown_starts_after_reply(tmp_path: Path) -> None:
    """Passing decide() alone must not start the un-@ group cooldown."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_cooldown_seconds = 5\n"
        "unmentioned_need_hook = false\nmax_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("第一条"), now=100.0) is None
    assert gate.decide(_group_plain("第二条"), now=102.0) is None
    gate.note_unmentioned_reply("group-b", now=100.0)
    assert gate.decide(_group_plain("第三条"), now=102.0) == "unmentioned_cooldown"


def test_at_mention_skips_unmentioned_cooldown(tmp_path: Path) -> None:
    """@ the bot still replies even if an un-@ reply just happened."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "unmentioned_cooldown_seconds = 5\nmin_interval_seconds = 0\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("0x01 有人在吗？"), now=200.0) is None
    gate.note_unmentioned_reply("group-b", now=200.0)
    mentioned = IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e4",
        msg_id="m4",
        content="你好",
        user_openid="user-a",
        group_openid="group-b",
        attachments=(),
    )
    assert gate.decide(mentioned, now=201.0) is None


def test_quote_bot_allowed_during_beijing_peak(tmp_path: Path) -> None:
    """Quoting the bot bypasses weekday peak-hour block for plain un-@ messages."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_off_peak_only = true\n"
        "unmentioned_need_hook = true\nbot_names = [\"0x01\"]\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    peak = datetime(2026, 8, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    quoted = IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e5",
        msg_id="m5",
        content="怎么这个像豆包",
        user_openid="user-a",
        group_openid="group-b",
        attachments=(),
        quotes_bot=True,
    )
    assert gate.decide(quoted, now=peak) is None
    assert gate.decide(_group_plain("随便聊聊"), now=peak) == "unmentioned_peak_hours"


def test_require_and_skip_keywords(tmp_path: Path) -> None:
    """Keyword allow/deny lists filter user text."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "require_keywords = [\"机器人\"]\nskip_keywords = [\"闭嘴\"]\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_c2c("随便聊聊"), now=1.0) == "require_keywords"
    assert gate.decide(_c2c("机器人闭嘴"), now=2.0) == "skip_keywords"
    assert gate.decide(_c2c("机器人你好"), now=20.0) is None


def test_speak_mode_all_replies_unmentioned_during_peak(tmp_path: Path) -> None:
    """all ignores hook and weekday peak so the bot tries every group line."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        'speak_mode = "all"\ngroup_unmentioned = true\n'
        "unmentioned_off_peak_only = true\nunmentioned_need_hook = true\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    peak = datetime(2026, 8, 31, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
    assert gate.decide(_group_plain("随便聊聊"), now=peak) is None


def test_speak_mode_mention_quote_only(tmp_path: Path) -> None:
    """mention_quote skips plain group chat but still answers @ and quote-of-bot."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        'speak_mode = "mention_quote"\ngroup_unmentioned = true\n'
        "min_interval_seconds = 0\nmax_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("你好"), now=1.0) == "unmentioned_off"
    quoted = IncomingMessage(
        event_type=GROUP_MESSAGE_EVENT,
        event_id="e4",
        msg_id="m4",
        content="接着说",
        user_openid="user-a",
        group_openid="group-b",
        quotes_bot=True,
    )
    assert gate.decide(quoted, now=1.0) is None
    mentioned = IncomingMessage(
        event_type=GROUP_AT_EVENT,
        event_id="e5",
        msg_id="m5",
        content="在吗",
        user_openid="user-a",
        group_openid="group-b",
    )
    assert gate.decide(mentioned, now=20.0) is None


def test_upsert_speak_mode_replaces_existing_line() -> None:
    """Console cycle rewrites speak_mode without dropping other toml comments."""
    from app.reply_policy import next_speak_mode, upsert_speak_mode_text

    text = '# keep me\nspeak_mode = "auto"\nenabled = true\n'
    updated = upsert_speak_mode_text(text, next_speak_mode("auto"))
    assert 'speak_mode = "all"' in updated
    assert "# keep me" in updated
    assert "enabled = true" in updated
