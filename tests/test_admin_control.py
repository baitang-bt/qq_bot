"""Admin bot control helpers."""

from app.admin.bot_control import bot_status, project_root


def test_bot_status_when_stopped(monkeypatch) -> None:
    """No pid file means the bot is stopped."""
    import app.admin.bot_control as bc

    monkeypatch.setattr(bc, "_PID_FILE", bc.project_root() / "data" / "uvicorn.pid.no-such")
    monkeypatch.setattr(bc, "_read_pid", lambda: None)
    assert bot_status()["running"] is False


def test_project_root_points_at_repo() -> None:
    """Project root contains app/main.py."""
    root = project_root()
    assert (root / "app" / "main.py").is_file()
