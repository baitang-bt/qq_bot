"""Admin dashboard HTTP API."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.admin.server import create_app


def test_admin_page_and_status(tmp_path: Path, monkeypatch) -> None:
    """Dashboard and status endpoints respond on localhost admin app."""
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    client = TestClient(create_app())
    page = client.get("/admin")
    assert page.status_code == 200
    assert "QQ 机器人管理" in page.text
    icon = client.get("/admin/icon.png")
    assert icon.status_code in (200, 404)
    status = client.get("/admin/api/status")
    assert status.status_code == 200
    data = status.json()
    assert "running" in data
    assert data["llm_configured"] is True


def test_admin_prompt_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Prompt can be read and written through the admin API."""
    prompt_path = tmp_path / "bot_prompt.json"
    prompt_path.write_text(
        json.dumps({"persona": "test", "anti_injection": ["a"], "stay_on_prompt": ["b"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    monkeypatch.setenv("BOT_PROMPT_PATH", str(prompt_path))
    client = TestClient(create_app())
    got = client.get("/admin/api/prompt").json()
    assert got["persona"] == "test"
    client.put(
        "/admin/api/prompt",
        json={"persona": "new", "anti_injection": ["x"], "stay_on_prompt": []},
    )
    saved = json.loads(prompt_path.read_text(encoding="utf-8"))
    assert saved["persona"] == "new"
