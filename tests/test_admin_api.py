"""Admin dashboard HTTP API."""

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
    """Active persona reserved txts can be read and written through the admin API."""
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    monkeypatch.setenv("BOT_PROMPT_PATH", str(tmp_path / "bot_prompt.json"))
    monkeypatch.setenv("PERSONAS_DIR", str(tmp_path / "personas"))
    monkeypatch.setenv("PERSONAS_INDEX_PATH", str(tmp_path / "personas.toml"))
    client = TestClient(create_app())
    listed = client.get("/admin/api/personas")
    assert listed.status_code == 200
    client.put(
        "/admin/api/prompt",
        json={"persona": "new", "anti_injection": ["x"], "stay_on_prompt": []},
    )
    got = client.get("/admin/api/prompt").json()
    assert got["persona"].strip() == "new"
    assert got["anti_injection"] == ["x"]
