"""Load bot settings from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    """Read a trimmed environment variable with a default."""
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback."""
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable; blank values use the default."""
    raw = _env(name)
    if not raw:
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable (1/true/yes/on)."""
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the QQ bot process."""

    qq_app_id: str
    qq_app_secret: str
    qq_api_base: str
    qq_token_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    vision_model: str
    llm_timeout_seconds: float
    vision_timeout_seconds: float
    memory_max_turns: int
    data_dir: Path
    reply_policy_path: Path
    bot_prompt_path: Path
    personas_dir: Path
    personas_index_path: Path
    qq_id: str
    host: str
    port: int
    coalesce_burst_seconds: float
    coalesce_debounce_seconds: float
    coalesce_single_debounce_seconds: float
    stickers_dir: Path
    stickers_index_path: Path
    sticker_auto_learn: bool
    sticker_learn_max: int


def load_settings() -> Settings:
    """Build Settings from process environment (and optional .env)."""
    data_dir = _ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    stickers_dir = Path(_env("STICKERS_DIR") or str(data_dir / "stickers"))
    stickers_dir.mkdir(parents=True, exist_ok=True)
    personas_dir = Path(_env("PERSONAS_DIR") or str(data_dir / "personas"))
    personas_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        qq_app_id=_env("QQ_APP_ID"),
        qq_app_secret=_env("QQ_APP_SECRET"),
        qq_api_base=_env("QQ_API_BASE", "https://api.sgroup.qq.com").rstrip("/"),
        qq_token_url=_env(
            "QQ_TOKEN_URL", "https://bots.qq.com/app/getAppAccessToken"
        ),
        llm_base_url=_env("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        llm_api_key=_env("LLM_API_KEY"),
        llm_model=_env("LLM_MODEL", "deepseek-flash"),
        vision_model=_env("VISION_MODEL") or _env("LLM_MODEL", "deepseek-flash"),
        llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 25.0),
        vision_timeout_seconds=_env_float("VISION_TIMEOUT_SECONDS", 20.0),
        memory_max_turns=_env_int("MEMORY_MAX_TURNS", 12),
        data_dir=data_dir,
        reply_policy_path=Path(_env("REPLY_POLICY_PATH") or str(_ROOT / "reply_policy.toml")),
        bot_prompt_path=Path(_env("BOT_PROMPT_PATH") or str(_ROOT / "bot_prompt.json")),
        personas_dir=personas_dir,
        personas_index_path=Path(
            _env("PERSONAS_INDEX_PATH") or str(_ROOT / "personas.toml")
        ),
        qq_id=_env("QQ_ID"),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8080),
        coalesce_burst_seconds=_env_float("COALESCE_BURST_SECONDS", 30.0),
        coalesce_debounce_seconds=_env_float("COALESCE_DEBOUNCE_SECONDS", 3.0),
        coalesce_single_debounce_seconds=_env_float(
            "COALESCE_SINGLE_DEBOUNCE_SECONDS", 3.0
        ),
        stickers_dir=stickers_dir,
        stickers_index_path=Path(
            _env("STICKERS_INDEX_PATH") or str(_ROOT / "stickers.toml")
        ),
        sticker_auto_learn=_env_bool("STICKER_AUTO_LEARN", True),
        sticker_learn_max=_env_int("STICKER_LEARN_MAX", 200),
    )
