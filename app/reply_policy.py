"""Hot-reloadable reply gates: who/what to answer, and how often."""

from __future__ import annotations

import logging
import re
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.qq.events import IncomingMessage

_log = logging.getLogger(__name__)
_BEIJING = ZoneInfo("Asia/Shanghai")

SPEAK_AUTO = "auto"
SPEAK_ALL = "all"
SPEAK_MENTION_QUOTE = "mention_quote"
SPEAK_MODES = (SPEAK_AUTO, SPEAK_ALL, SPEAK_MENTION_QUOTE)
SPEAK_MODE_LABELS = {
    SPEAK_AUTO: "自动",
    SPEAK_ALL: "全部尝试回复",
    SPEAK_MENTION_QUOTE: "仅@和引用回复",
}
_SPEAK_ASSIGN = re.compile(r'(?m)^(\s*speak_mode\s*=\s*)(["\']).*?\2')


@dataclass(frozen=True)
class ReplySettings:
    """Reply switches and rate limits. Edit reply_policy.toml; reload is automatic."""

    enabled: bool = True
    c2c: bool = True
    group: bool = True
    on_text: bool = True
    on_image: bool = True
    on_voice: bool = True
    min_interval_seconds: float = 12.0
    max_per_session_per_minute: int = 3
    require_keywords: tuple[str, ...] = ()
    skip_keywords: tuple[str, ...] = ()
    group_unmentioned: bool = True
    unmentioned_cooldown_seconds: float = 5.0
    unmentioned_need_hook: bool = True
    bot_names: tuple[str, ...] = ()
    unmentioned_thread_minutes: float = 30.0
    unmentioned_off_peak_only: bool = True
    speak_mode: str = SPEAK_AUTO


def default_settings() -> ReplySettings:
    """Conservative defaults so the bot is not noisy before a toml exists."""
    return ReplySettings()


def load_reply_settings(path: Path) -> ReplySettings:
    """Parse reply_policy.toml; missing file or keys fall back to defaults."""
    base = default_settings()
    if not path.is_file():
        return base
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return ReplySettings(
        enabled=bool(data.get("enabled", base.enabled)),
        c2c=bool(data.get("c2c", base.c2c)),
        group=bool(data.get("group", base.group)),
        on_text=bool(data.get("on_text", base.on_text)),
        on_image=bool(data.get("on_image", base.on_image)),
        on_voice=bool(data.get("on_voice", base.on_voice)),
        min_interval_seconds=float(
            data.get("min_interval_seconds", base.min_interval_seconds)
        ),
        max_per_session_per_minute=int(
            data.get("max_per_session_per_minute", base.max_per_session_per_minute)
        ),
        require_keywords=_string_tuple(data.get("require_keywords")),
        skip_keywords=_string_tuple(data.get("skip_keywords")),
        group_unmentioned=bool(data.get("group_unmentioned", base.group_unmentioned)),
        unmentioned_cooldown_seconds=float(
            data.get(
                "unmentioned_cooldown_seconds",
                base.unmentioned_cooldown_seconds,
            )
        ),
        unmentioned_need_hook=bool(
            data.get("unmentioned_need_hook", base.unmentioned_need_hook)
        ),
        bot_names=_string_tuple(data.get("bot_names")),
        unmentioned_thread_minutes=float(
            data.get("unmentioned_thread_minutes", base.unmentioned_thread_minutes)
        ),
        unmentioned_off_peak_only=bool(
            data.get("unmentioned_off_peak_only", base.unmentioned_off_peak_only)
        ),
        speak_mode=normalize_speak_mode(data.get("speak_mode", base.speak_mode)),
    )


def normalize_speak_mode(value: object) -> str:
    """Map a toml/UI value to auto / all / mention_quote."""
    key = str(value or "").strip().lower()
    if key in SPEAK_MODES:
        return key
    return SPEAK_AUTO


def next_speak_mode(current: str) -> str:
    """Return the next mode in the console cycle: auto → all → mention_quote."""
    key = normalize_speak_mode(current)
    index = SPEAK_MODES.index(key)
    return SPEAK_MODES[(index + 1) % len(SPEAK_MODES)]


def speak_mode_label(mode: str) -> str:
    """Chinese label for the console speak-mode button."""
    return SPEAK_MODE_LABELS[normalize_speak_mode(mode)]


def upsert_speak_mode_text(text: str, mode: str) -> str:
    """Replace or append speak_mode in reply_policy.toml without dropping comments."""
    mode = normalize_speak_mode(mode)
    raw = text or ""
    if _SPEAK_ASSIGN.search(raw):
        return _SPEAK_ASSIGN.sub(rf'\1"{mode}"', raw, count=1)
    line = f'speak_mode = "{mode}"'
    if not raw.strip():
        return line + "\n"
    return raw.rstrip() + (
        "\n\n# 群发言模式：auto=自动 / all=全部尝试回复 / mention_quote=仅@和引用\n"
        f"{line}\n"
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    """Normalize a toml array of strings."""
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


class ReplyGate:
    """Decide whether to reply; re-reads toml when the file mtime changes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime = -1.0
        self._settings = default_settings()
        self._hits: dict[str, list[float]] = {}
        self._unat_at: dict[str, float] = {}
        self._last_bot_reply: dict[str, float] = {}

    def note_bot_reply(self, session_id: str, now: float | None = None) -> None:
        """Remember that the bot replied in this session (for un-@ thread follow-ups)."""
        self._touch_thread(session_id, now)

    def note_unmentioned_reply(self, group_id: str, now: float | None = None) -> None:
        """Record a delivered un-@ group reply for cooldown and per-minute caps."""
        stamp = time.time() if now is None else now
        gid = group_id.strip()
        if not gid:
            return
        self._unat_at[gid] = stamp
        self._record_hit(f"unat:{gid}", stamp)

    def note_user_engagement(self, session_id: str, now: float | None = None) -> None:
        """Remember @ or name-call so un-@ follow-ups work before the bot finishes replying."""
        self._touch_thread(session_id, now)

    def note_inbound_engagement(self, message: IncomingMessage, now: float | None = None) -> None:
        """Open the un-@ thread window when the user @'s or names the bot."""
        if not message.is_group:
            return
        policy = self.current()
        text = message.user_text.strip()
        if message.mentioned or message.quotes_bot or _mentions_bot_name(text, policy.bot_names):
            self.note_user_engagement(message.session_id, now)

    def _touch_thread(self, session_id: str, now: float | None) -> None:
        """Refresh the active conversation window for a user-in-group session."""
        stamp = time.time() if now is None else now
        if session_id.strip():
            self._last_bot_reply[session_id] = stamp

    def current(self) -> ReplySettings:
        """Return the latest policy, reloading from disk when needed."""
        self._reload_if_changed()
        return self._settings

    def decide(self, message: IncomingMessage, now: float | None = None) -> str | None:
        """Return a skip reason, or None if the bot should reply."""
        policy = self.current()
        if not policy.enabled:
            return "disabled"
        if message.is_group and not policy.group:
            return "group_off"
        if not message.is_group and not policy.c2c:
            return "c2c_off"
        has_voice = bool(message.voice_attachments)
        has_image = bool(message.image_attachments)
        typed = message.content.strip()
        asr = message.asr_text if policy.on_voice else ""
        user_text = message.user_text if policy.on_voice else typed
        if has_voice and not policy.on_voice:
            has_voice = False
        if has_image and not policy.on_image:
            has_image = False
        want_text = bool(typed or asr) and policy.on_text
        if has_voice and policy.on_voice and not asr and not typed:
            return "voice_no_asr"
        if not want_text and not has_image and not (has_voice and asr):
            return "kind_off"
        if policy.require_keywords and not _contains_any(user_text, policy.require_keywords):
            return "require_keywords"
        if policy.skip_keywords and _contains_any(user_text, policy.skip_keywords):
            return "skip_keywords"
        stamp = time.time() if now is None else now
        if message.mentioned or (message.is_group and message.quotes_bot):
            reason = self._rate_limit(message.session_id, policy, stamp)
            if reason:
                return reason
            self._record_hit(message.session_id, stamp)
            return None
        if not message.mentioned:
            mode = normalize_speak_mode(policy.speak_mode)
            if mode == SPEAK_MENTION_QUOTE:
                return "unmentioned_off"
            if mode != SPEAK_ALL:
                if policy.unmentioned_off_peak_only and is_beijing_unmentioned_peak(stamp):
                    return "unmentioned_peak_hours"
                reason = _unmentioned_skip(message, policy, self, stamp)
                if reason:
                    return reason
            group_id = message.group_openid or ""
            last = self._unat_at.get(group_id, 0.0)
            if last > 0 and stamp - last < policy.unmentioned_cooldown_seconds:
                return "unmentioned_cooldown"
            cap = self._minute_cap(f"unat:{group_id}", policy, stamp)
            if cap:
                return cap
            return None

    def _reload_if_changed(self) -> None:
        """Reload toml when it appears or its mtime changes."""
        if not self._path.is_file():
            self._settings = default_settings()
            self._mtime = -1.0
            return
        mtime = self._path.stat().st_mtime
        if mtime == self._mtime:
            return
        try:
            self._settings = load_reply_settings(self._path)
            self._mtime = mtime
            _log.info("loaded reply policy from %s", self._path)
        except Exception:
            _log.exception("failed to load %s; keeping previous policy", self._path)

    def _rate_limit(self, session_id: str, policy: ReplySettings, now: float) -> str | None:
        """Enforce per-session cooldown and per-minute cap for @ / DM replies."""
        hits = [t for t in self._hits.get(session_id, []) if now - t < 60.0]
        self._hits[session_id] = hits
        if hits and now - hits[-1] < policy.min_interval_seconds:
            return "min_interval"
        if len(hits) >= max(1, policy.max_per_session_per_minute):
            return "max_per_minute"
        return None

    def _minute_cap(self, key: str, policy: ReplySettings, now: float) -> str | None:
        """Cap un-@ replies per group per minute so a busy chat cannot burn tokens."""
        hits = [t for t in self._hits.get(key, []) if now - t < 60.0]
        self._hits[key] = hits
        if len(hits) >= max(1, policy.max_per_session_per_minute):
            return "max_per_minute"
        return None

    def _record_hit(self, session_id: str, now: float) -> None:
        """Record that we are going to reply in this session."""
        hits = self._hits.setdefault(session_id, [])
        hits.append(now)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """True if any keyword appears in text (case-insensitive)."""
    lowered = text.lower()
    return any(key.lower() in lowered for key in keywords)


def _mentions_bot_name(text: str, names: tuple[str, ...]) -> bool:
    """True when user text contains a configured bot name."""
    lowered = text.lower()
    return any(name.lower() in lowered for name in names if name.strip())


def _has_recent_thread(gate: ReplyGate, session_id: str, now: float, ttl_seconds: float) -> bool:
    """True when the bot replied in this session within the thread window."""
    last = gate._last_bot_reply.get(session_id, 0.0)
    return last > 0 and now - last <= ttl_seconds


def _looks_like_group_broadcast(text: str) -> bool:
    """True when the line is clearly addressed to the whole group, not the bot."""
    stripped = text.strip()
    if not stripped:
        return False
    if any(word in stripped for word in ("大家", "各位", "全员", "@all", "@所有人")):
        return True
    lowered = stripped.casefold()
    for phrase in ("我先下班", "我走了", "我先撤", "大家晚安", "各位注意"):
        if phrase.casefold() in lowered:
            return True
    return False


def _unmentioned_skip(
    message: IncomingMessage,
    policy: ReplySettings,
    gate: ReplyGate,
    now: float,
) -> str | None:
    """Skip un-@ group lines unless bot is named or the user is in an active thread."""
    if not policy.group_unmentioned:
        return "unmentioned_off"
    if not policy.unmentioned_need_hook:
        return None
    text = message.user_text.strip()
    if not text:
        return "unmentioned_no_hook"
    if _mentions_bot_name(text, policy.bot_names):
        return None
    ttl = max(1.0, policy.unmentioned_thread_minutes) * 60.0
    if _has_recent_thread(gate, message.session_id, now, ttl):
        if _looks_like_group_broadcast(text):
            return "unmentioned_no_hook"
        return None
    return "unmentioned_no_hook"


def is_beijing_unmentioned_peak(stamp: float) -> bool:
    """True on Mon-Fri 09:00-12:00 and 14:00-18:00 Beijing time (no un-@ replies)."""
    dt = datetime.fromtimestamp(stamp, tz=_BEIJING)
    if dt.weekday() >= 5:
        return False
    minute_of_day = dt.hour * 60 + dt.minute
    morning = 9 * 60 <= minute_of_day < 12 * 60
    afternoon = 14 * 60 <= minute_of_day < 18 * 60
    return morning or afternoon
