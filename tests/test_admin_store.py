"""Admin store read/write helpers."""

import json
from dataclasses import replace
from pathlib import Path

from app.admin import store
from app.config import load_settings


def test_read_save_prompt_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Prompt JSON can be written and read back."""
    prompt_path = tmp_path / "bot_prompt.json"
    monkeypatch.setenv("BOT_PROMPT_PATH", str(prompt_path))
    store.save_prompt("我是 bot", ["规则1"], ["习惯1"])
    data = store.read_prompt()
    assert data.persona == "我是 bot"
    assert data.anti_injection == ["规则1"]


def test_import_prompt_file(tmp_path: Path, monkeypatch) -> None:
    """bot_prompt.json can be imported from another file."""
    prompt_path = tmp_path / "bot_prompt.json"
    source = tmp_path / "import.json"
    source.write_text(
        json.dumps(
            {
                "persona": "导入的人设",
                "anti_injection": ["防注入1"],
                "stay_on_prompt": ["保持1", "保持2"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BOT_PROMPT_PATH", str(prompt_path))
    saved = store.import_prompt_file(source)
    assert saved == prompt_path
    data = store.read_prompt()
    assert data.persona == "导入的人设"
    assert data.anti_injection == ["防注入1"]
    assert data.stay_on_prompt == ["保持1", "保持2"]


def test_read_save_policy(tmp_path: Path, monkeypatch) -> None:
    """Reply policy toml round-trips."""
    policy_path = tmp_path / "reply_policy.toml"
    monkeypatch.setenv("REPLY_POLICY_PATH", str(policy_path))
    store.save_reply_policy("enabled = true\n")
    text, path = store.read_reply_policy()
    assert text == "enabled = true\n"
    assert path == policy_path


def test_tail_logs_filters_health_noise(tmp_path: Path, monkeypatch) -> None:
    """Monitor log view hides /health access lines."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log = data_dir / "uvicorn.log"
    log.write_text(
        "INFO: GET /health HTTP/1.1 200 OK\n"
        "2026 INFO app.qq.gateway gateway chat t=GROUP_MESSAGE_CREATE mentioned=False preview='你好'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )
    text = store.tail_logs()
    assert "GET /health" not in text
    assert "gateway chat" in text


def test_group_message_hint_detects_missing_full_messages(
    tmp_path: Path, monkeypatch
) -> None:
    """Admin hint warns when only @ events appear in the log."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log = data_dir / "uvicorn.log"
    log.write_text(
        "gateway ready session=abcd1234\n"
        "gateway packet op=0 t=GROUP_AT_MESSAGE_CREATE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )
    hint = store.group_message_hint()
    assert "获取群内全部消息" in hint


def test_clear_monitor_logs(tmp_path: Path, monkeypatch) -> None:
    """Admin can truncate uvicorn.log to clear the monitor view."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log = data_dir / "uvicorn.log"
    log.write_text("2026 INFO app.bot inbound t=GROUP\n", encoding="utf-8")
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )
    cleared = store.clear_monitor_logs()
    assert cleared == log
    assert log.read_text(encoding="utf-8") == ""
    assert "暂无消息监控日志" in store.tail_logs()


def test_admin_ui_state_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Desktop fold state for persona sections persists in data/admin_ui.json."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )
    assert store.read_admin_ui() == store.AdminUiState()
    saved = store.save_admin_ui(
        store.AdminUiState(
            persona_anti_injection_expanded=False,
            persona_stay_on_prompt_expanded=True,
        )
    )
    assert saved == data_dir / "admin_ui.json"
    loaded = store.read_admin_ui()
    assert loaded.persona_anti_injection_expanded is False
    assert loaded.persona_stay_on_prompt_expanded is True
