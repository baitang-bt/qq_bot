"""Reply policy gates: kinds, keywords, and per-session rate limits."""

from pathlib import Path

from app.qq.events import (
    C2C_EVENT,
    GROUP_AT_EVENT,
    GROUP_MESSAGE_EVENT,
    Attachment,
    IncomingMessage,
)
from app.reply_policy import ReplyGate, load_reply_settings


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


def test_unmentioned_skips_chatter(tmp_path: Path) -> None:
    """Idle group chat without a question or bot name is ignored."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_need_hook = true\nbot_names = [\"0x01\"]\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("我先下班了"), now=1.0) == "unmentioned_no_hook"


def test_unmentioned_question_has_cooldown(tmp_path: Path) -> None:
    """Un-@ questions can be answered, then the same group waits ~5s."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "group_unmentioned = true\nunmentioned_cooldown_seconds = 5\n"
        "unmentioned_need_hook = true\nmax_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("这怎么弄？"), now=100.0) is None
    assert gate.decide(_group_plain("还有谁会？"), now=102.0) == "unmentioned_cooldown"
    assert gate.decide(_group_plain("那要怎么做？"), now=106.0) is None


def test_at_mention_skips_unmentioned_cooldown(tmp_path: Path) -> None:
    """@ the bot still replies even if an un-@ reply just happened."""
    path = tmp_path / "reply_policy.toml"
    path.write_text(
        "unmentioned_cooldown_seconds = 5\nmin_interval_seconds = 0\n"
        "max_per_session_per_minute = 10\n",
        encoding="utf-8",
    )
    gate = ReplyGate(path)
    assert gate.decide(_group_plain("有人在吗？"), now=200.0) is None
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
