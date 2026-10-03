"""Shared admin operations for HTTP API and native desktop UI."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.admin import bot_control
from app.config import load_settings
from app.impression.store import ImpressionStore
from app.personas.catalog import PersonaCatalog
from app.reply_policy import (
    load_reply_settings,
    next_speak_mode,
    speak_mode_label,
    upsert_speak_mode_text,
)


@dataclass(frozen=True)
class BotSnapshot:
    """Runtime status shown in the admin UI."""

    running: bool
    healthy: bool
    pid: int | None
    llm_model: str
    qq_configured: bool
    llm_configured: bool
    project: str


@dataclass(frozen=True)
class AdminUiState:
    """Persisted desktop admin UI preferences."""

    persona_anti_injection_expanded: bool = True
    persona_stay_on_prompt_expanded: bool = True


@dataclass(frozen=True)
class PromptData:
    """Active persona pack as the three reserved txt files (legacy admin API)."""

    persona: str
    anti_injection: list[str]
    stay_on_prompt: list[str]
    path: str


def _persona_catalog() -> PersonaCatalog:
    """Build a catalog from current settings (reloads toml and folders)."""
    settings = load_settings()
    return PersonaCatalog(
        settings.personas_index_path,
        settings.personas_dir,
        json_migrate_path=settings.bot_prompt_path,
    )


def personas_dir() -> Path:
    """Return the local persona packs folder."""
    path = load_settings().personas_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_persona_packs() -> list[dict[str, object]]:
    """List persona packs with active flag and txt filenames."""
    catalog = _persona_catalog()
    active = catalog.active_id()
    rows: list[dict[str, object]] = []
    for entry in catalog.list_packs():
        rows.append(
            {
                "id": entry.id,
                "title": entry.title,
                "active": entry.id == active,
                "files": catalog.list_files(entry.id),
                "path": str(entry.path),
            }
        )
    return rows


def persona_active_id() -> str:
    """Return the enabled persona pack id."""
    return _persona_catalog().active_id()


def set_persona_active(persona_id: str) -> str:
    """Enable one persona pack for the next chat turn."""
    return _persona_catalog().set_active(persona_id)


def list_persona_files(persona_id: str) -> list[str]:
    """Txt filenames in a pack, reserved files first."""
    return _persona_catalog().list_files(persona_id)


def read_persona_file(persona_id: str, filename: str) -> str:
    """Read one txt from a pack."""
    return _persona_catalog().read_file(persona_id, filename)


def save_persona_file(persona_id: str, filename: str, text: str) -> Path:
    """Write one txt in a pack."""
    return _persona_catalog().save_file(persona_id, filename, text)


def add_persona_file(persona_id: str, filename: str) -> Path:
    """Create an empty txt in a pack."""
    return _persona_catalog().add_file(persona_id, filename)


def new_persona_pack(persona_id: str, title: str = "") -> dict[str, object]:
    """Create a pack and return its list-row dict."""
    entry = _persona_catalog().new_pack(persona_id, title)
    return {
        "id": entry.id,
        "title": entry.title,
        "active": entry.id == persona_active_id(),
        "files": list_persona_files(entry.id),
        "path": str(entry.path),
    }


def delete_persona_pack(persona_id: str) -> None:
    """Delete a non-active pack that is not the last remaining one."""
    _persona_catalog().delete_pack(persona_id)


def read_prompt() -> PromptData:
    """Load reserved txts of the active pack for the legacy HTTP editor."""
    catalog = _persona_catalog()
    pack_id = catalog.active_id()
    folder = catalog.dir_for(pack_id) if pack_id else None
    if not pack_id or folder is None:
        return PromptData("", [], [], str(load_settings().personas_dir))
    return PromptData(
        persona=catalog.read_file(pack_id, "persona.txt"),
        anti_injection=_txt_lines(catalog.read_file(pack_id, "anti_injection.txt")),
        stay_on_prompt=_txt_lines(catalog.read_file(pack_id, "stay_on_prompt.txt")),
        path=str(folder),
    )


def save_prompt(persona: str, anti_injection: list[str], stay_on_prompt: list[str]) -> Path:
    """Write reserved txts of the active pack (legacy HTTP editor)."""
    catalog = _persona_catalog()
    pack_id = catalog.active_id()
    if not pack_id:
        catalog.new_pack("0x01", "默认")
        pack_id = "0x01"
    catalog.save_file(pack_id, "persona.txt", persona.strip() + "\n")
    catalog.save_file(
        pack_id,
        "anti_injection.txt",
        "\n".join(line.strip() for line in anti_injection if line.strip()) + "\n",
    )
    catalog.save_file(
        pack_id,
        "stay_on_prompt.txt",
        "\n".join(line.strip() for line in stay_on_prompt if line.strip()) + "\n",
    )
    folder = catalog.dir_for(pack_id)
    return folder if folder is not None else catalog.save_file(pack_id, "persona.txt", persona)


def import_prompt_file(path: Path) -> Path:
    """Import a bot_prompt.json object into the active pack's reserved txts."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON：{exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("人设 JSON 根节点必须是对象")
    return save_prompt(
        str(raw.get("persona") or ""),
        _string_list(raw.get("anti_injection")),
        _string_list(raw.get("stay_on_prompt")),
    )


def _txt_lines(text: str) -> list[str]:
    """Split a one-rule-per-line txt into non-empty strings."""
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def bot_snapshot() -> BotSnapshot:
    """Return bot process and config summary for the dashboard."""
    settings = load_settings()
    status = bot_control.bot_status()
    return BotSnapshot(
        running=bool(status.get("running")),
        healthy=bool(status.get("healthy")),
        pid=status.get("pid") if isinstance(status.get("pid"), int) else None,
        llm_model=settings.llm_model,
        qq_configured=bool(settings.qq_app_id and settings.qq_app_secret),
        llm_configured=bool(settings.llm_api_key),
        project=str(bot_control.project_root()),
    )


def start_bot() -> BotSnapshot:
    """Start the bot via start-local.sh."""
    bot_control.start_bot()
    return bot_snapshot()


def stop_bot() -> BotSnapshot:
    """Stop the bot via stop-local.sh."""
    bot_control.stop_bot()
    return bot_snapshot()


def read_reply_policy() -> tuple[str, Path]:
    """Load reply_policy.toml text."""
    path = load_settings().reply_policy_path
    if not path.is_file():
        return "", path
    return path.read_text(encoding="utf-8"), path


def save_reply_policy(text: str) -> Path:
    """Write reply_policy.toml."""
    path = load_settings().reply_policy_path
    path.write_text(text, encoding="utf-8")
    return path


def read_speak_mode() -> str:
    """Return the current group speak mode from reply_policy.toml."""
    path = load_settings().reply_policy_path
    return load_reply_settings(path).speak_mode


def cycle_speak_mode() -> str:
    """Advance speak_mode in reply_policy.toml; next inbound message reloads it."""
    path = load_settings().reply_policy_path
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    current = load_reply_settings(path).speak_mode if path.is_file() else "auto"
    nxt = next_speak_mode(current)
    path.write_text(upsert_speak_mode_text(text, nxt), encoding="utf-8")
    return nxt


def speak_mode_button_text(mode: str | None = None) -> str:
    """Label for the run-tab speak-mode cycle button."""
    key = mode if mode is not None else read_speak_mode()
    return f"发言模式：{speak_mode_label(key)}"


def read_admin_ui() -> AdminUiState:
    """Load persisted desktop UI preferences."""
    path = _admin_ui_path()
    if not path.is_file():
        return AdminUiState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AdminUiState()
    if not isinstance(raw, dict):
        return AdminUiState()
    return AdminUiState(
        persona_anti_injection_expanded=_bool(raw.get("persona_anti_injection_expanded"), True),
        persona_stay_on_prompt_expanded=_bool(raw.get("persona_stay_on_prompt_expanded"), True),
    )


def save_admin_ui(state: AdminUiState) -> Path:
    """Write desktop UI preferences for the next launch."""
    path = _admin_ui_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "persona_anti_injection_expanded": state.persona_anti_injection_expanded,
        "persona_stay_on_prompt_expanded": state.persona_stay_on_prompt_expanded,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def list_impressions() -> list[dict[str, Any]]:
    """Return all impression records."""
    return _impression_store().list_records()


def load_impression(user_openid: str) -> dict[str, Any]:
    """Load one impression by openid."""
    return _impression_store().load(user_openid)


def save_impression(
    user_openid: str,
    username: str,
    qq: str,
    impression: str,
) -> Path:
    """Create or update one user's impression file."""
    store = _impression_store()
    record = store.load(user_openid)
    record["username"] = username.strip()
    record["qq"] = qq.strip()
    record["impression"] = impression.strip()
    return store.save(record)


def import_impression_files(paths: list[Path]) -> tuple[int, int, list[str]]:
    """Import impression JSON files; return (imported, skipped, error lines)."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: 无法读取 JSON（{exc}）")
            continue
        rows = _records_from_import_payload(raw)
        if not rows:
            errors.append(f"{path.name}: 没有可导入的记录（需含 user_openid）")
            continue
        for index, row in enumerate(rows, start=1):
            openid = str(row.get("user_openid") or "").strip()
            if not openid:
                skipped += 1
                errors.append(f"{path.name}#{index}: 缺少 user_openid，已跳过")
                continue
            save_impression(
                openid,
                str(row.get("username") or ""),
                str(row.get("qq") or ""),
                str(row.get("impression") or ""),
            )
            imported += 1
    return imported, skipped, errors


def _records_from_import_payload(raw: object) -> list[dict[str, object]]:
    """Normalize one JSON object or array into impression dict rows."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        if "user_openid" in raw:
            return [raw]
        nested = raw.get("impressions")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def list_command_admins() -> list[dict[str, str]]:
    """Return slash-command admins with openid hints from impressions when unbound."""
    from app.command_admins import CommandAdminStore

    admins = CommandAdminStore(_command_admins_path()).list_admins()
    qq_openid: dict[str, str] = {}
    for record in list_impressions():
        qq = str(record.get("qq") or "").strip()
        openid = str(record.get("user_openid") or "").strip()
        if qq and openid:
            qq_openid[qq] = openid
    enriched: list[dict[str, str]] = []
    for row in admins:
        item = dict(row)
        if not item.get("user_openid"):
            hint = qq_openid.get(item.get("qq", ""), "")
            if hint:
                item["openid_hint"] = hint
        enriched.append(item)
    return enriched


def save_command_admins(admins: list[dict[str, object]]) -> Path:
    """Write data/command_admins.json."""
    from app.command_admins import CommandAdminStore

    return CommandAdminStore(_command_admins_path()).save_admins(admins)


def _command_admins_path() -> Path:
    """Path to the slash-command admin list."""
    return load_settings().data_dir / "command_admins.json"


_MONITOR_MARKERS = (
    "app.qq.gateway",
    "app.bot",
    "gateway chat",
    "gateway event",
    "gateway ignore",
    "gateway ready",
    "gateway 已连接",
    "group full-message",
    "inbound t=",
    "正在回复",
    "正在发送",
    "排队等待",
    "llm 排队等待",
    "llm request timeout",
    "skip reply",
    "replied t=",
    "bot handle failed",
)


def tail_logs(lines: int = 100) -> str:
    """Return filtered gateway/bot monitor lines from uvicorn.log."""
    log_path = uvicorn_log_path()
    if not log_path.is_file():
        return "（还没有日志）"
    cap = max(10, min(lines, 400))
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    picked = [line for line in all_lines if _is_monitor_line(line)]
    if not picked:
        return "（暂无消息监控日志；发群消息后应出现 gateway chat / inbound）"
    return "\n".join(picked[-cap:])


def tail_log_lines(lines: int = 100) -> list[str]:
    """Return filtered monitor lines from uvicorn.log (oldest-first within window)."""
    log_path = uvicorn_log_path()
    if not log_path.is_file():
        return []
    cap = max(10, min(lines, 400))
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    picked = [line for line in all_lines if _is_monitor_line(line)]
    if not picked:
        return []
    return picked[-cap:]


def clear_monitor_logs() -> Path:
    """Truncate uvicorn.log so the admin monitor view starts empty."""
    log_path = uvicorn_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    return log_path


def uvicorn_log_path() -> Path:
    """Return the bot process log file path."""
    return load_settings().data_dir / "uvicorn.log"


def stickers_cache_dir() -> Path:
    """Return the local sticker library folder (learned + manual PNG/JPG)."""
    path = load_settings().stickers_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def reveal_in_file_manager(path: Path) -> Path:
    """Open a folder in Finder (macOS). Creates the directory if missing."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["open", str(path)], check=True)
    return path


def _is_monitor_line(line: str) -> bool:
    """True for bot/gateway lines; skip /health access noise."""
    if "GET /health" in line:
        return False
    return any(marker in line for marker in _MONITOR_MARKERS)


def gateway_stats() -> dict[str, object]:
    """Read live gateway counters from /health."""
    try:
        response = httpx.get("http://127.0.0.1:8080/health", timeout=1.5)
        if response.status_code != 200:
            return {}
        payload = response.json()
        gateway = payload.get("gateway")
        return gateway if isinstance(gateway, dict) else {}
    except httpx.HTTPError:
        return {}


def gateway_status_line() -> str:
    """One-line gateway monitor summary for the admin run tab."""
    stats = gateway_stats()
    if not stats:
        return ""
    chat_total = int(stats.get("chat_total") or 0)
    group_at = int(stats.get("group_at") or 0)
    group_plain = int(stats.get("group_plain") or 0)
    preview = str(stats.get("last_chat_preview") or "")
    if chat_total == 0:
        idle = stats.get("idle_seconds")
        if isinstance(idle, int) and idle >= 60:
            return f"消息监控：已连接 {idle}s，尚未收到任何群/私聊（未@需手机 QQ 群开「获取群内全部消息」）"
        return "消息监控：等待首条群/私聊…"
    tail = f" · 最近「{preview}」" if preview else ""
    return (
        f"消息监控：共 {chat_total} 条（@ {group_at} · 未@ {group_plain}）{tail}"
    )


def group_message_hint() -> str:
    """Warn when the gateway sees @ messages but not full group messages."""
    log_path = uvicorn_log_path()
    if not log_path.is_file():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "t=GROUP_MESSAGE_CREATE" in text:
        return ""
    if "gateway ready" not in text:
        return ""
    if "t=GROUP_AT_MESSAGE_CREATE" in text:
        return (
            "网关只收到 @ 消息、未收到全量群消息。"
            "请在手机 QQ → 群设置 → 机器人 →「获取群内全部消息」"
            "（WebSocket 模式不用改开放平台；需群主/管理员操作）。"
        )
    return (
        "尚未收到任何群消息。"
        "确认机器人已进群；未@ 需在手机 QQ 群机器人设置里开「获取群内全部消息」。"
    )


def format_error(exc: BaseException) -> str:
    """Turn subprocess or IO errors into a short user-facing message."""
    if isinstance(exc, subprocess.CalledProcessError):
        detail = ""
        if isinstance(exc.stderr, str) and exc.stderr.strip():
            detail = exc.stderr.strip()
        elif isinstance(exc.stdout, str) and exc.stdout.strip():
            detail = exc.stdout.strip()
        if detail:
            return f"脚本执行失败（退出码 {exc.returncode}）\n{detail[-800:]}"
        return f"脚本执行失败（退出码 {exc.returncode}）"
    text = str(exc) or exc.__class__.__name__
    return text[-1200:]


def _string_list(value: object) -> list[str]:
    """Coerce JSON array values to trimmed strings."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bool(value: object, default: bool) -> bool:
    """Coerce JSON booleans with a fallback."""
    if isinstance(value, bool):
        return value
    return default


def _admin_ui_path() -> Path:
    """Path to persisted desktop admin UI preferences."""
    return load_settings().data_dir / "admin_ui.json"


def _impression_store() -> ImpressionStore:
    """Open the on-disk impression store."""
    return ImpressionStore(load_settings().data_dir / "impressions")
