"""Hot-reloadable reply gates: who/what to answer, and how often."""

from __future__ import annotations

import logging
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.qq.events import IncomingMessage

_log = logging.getLogger(__name__)


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
        if not message.mentioned:
            reason = _unmentioned_skip(message, policy)
            if reason:
                return reason
            group_id = message.group_openid or ""
            last = self._unat_at.get(group_id, 0.0)
            if stamp - last < policy.unmentioned_cooldown_seconds:
                return "unmentioned_cooldown"
            cap = self._minute_cap(f"unat:{group_id}", policy, stamp)
            if cap:
                return cap
            self._unat_at[group_id] = stamp
            self._record_hit(f"unat:{group_id}", stamp)
            return None
        reason = self._rate_limit(message.session_id, policy, stamp)
        if reason:
            return reason
        self._record_hit(message.session_id, stamp)
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


_UNAT_HOOKS = (
    "?",
    "？",
    "吗",
    "么",
    "呢",
    "怎么",
    "为什么",
    "为啥",
    "啥",
    "帮我",
    "请问",
    "有人",
    "谁",
    "求",
)


def _unmentioned_skip(message: IncomingMessage, policy: ReplySettings) -> str | None:
    """Skip un-@ group lines that are not worth an LLM call."""
    if not policy.group_unmentioned:
        return "unmentioned_off"
    if not policy.unmentioned_need_hook:
        return None
    text = message.user_text.strip()
    if not text:
        return "unmentioned_no_hook"
    lowered = text.lower()
    if any(name.lower() in lowered for name in policy.bot_names if name.strip()):
        return None
    if any(hook in text for hook in _UNAT_HOOKS):
        return None
    return "unmentioned_no_hook"
