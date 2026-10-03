"""API .env settings: masking and in-place updates."""

from pathlib import Path

from app.admin import env_settings


def test_mask_secret_redacts_middle() -> None:
    """Long secrets keep a short prefix and last 4 chars only."""
    assert env_settings.mask_secret("sk-abcdefghijklmnop") == "sk-…mnop"
    assert env_settings.mask_secret("short") == "****"
    assert env_settings.mask_secret("") == ""


def test_read_api_settings_never_returns_raw_secret(tmp_path: Path) -> None:
    """UI view blanks secret fields and only exposes a mask."""
    path = tmp_path / ".env"
    path.write_text(
        "LLM_BASE_URL=https://api.example.com/v1\n"
        "LLM_API_KEY=sk-real-secret-value-1234\n"
        "LLM_MODEL=demo\n"
        "QQ_APP_SECRET=qqsecretABCDEFG\n",
        encoding="utf-8",
    )
    view = env_settings.read_api_settings(path)
    assert view.values["LLM_BASE_URL"] == "https://api.example.com/v1"
    assert view.values["LLM_MODEL"] == "demo"
    assert view.values["LLM_API_KEY"] == ""
    assert view.values["QQ_APP_SECRET"] == ""
    assert view.secret_set["LLM_API_KEY"] is True
    assert view.secret_masks["LLM_API_KEY"].endswith("1234")
    assert "real-secret" not in view.secret_masks["LLM_API_KEY"]
    assert "sk-real-secret-value-1234" not in repr(view)


def test_save_keeps_secret_when_blank(tmp_path: Path) -> None:
    """Blank secret inputs must not wipe existing .env values."""
    path = tmp_path / ".env"
    path.write_text(
        "# keep me\n"
        "LLM_BASE_URL=https://old.example/v1\n"
        "LLM_API_KEY=keep-this-key-9999\n"
        "LLM_MODEL=old-model\n",
        encoding="utf-8",
    )
    env_settings.save_api_settings(
        {
            "LLM_BASE_URL": "https://new.example/v1",
            "LLM_MODEL": "new-model",
            "LLM_API_KEY": "",
            "QQ_APP_ID": "app1",
        },
        path=path,
    )
    text = path.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "LLM_BASE_URL=https://new.example/v1" in text
    assert "LLM_MODEL=new-model" in text
    assert "LLM_API_KEY=keep-this-key-9999" in text
    assert "QQ_APP_ID=app1" in text


def test_save_rejects_mask_placeholder(tmp_path: Path) -> None:
    """Pasting the masked display string must not overwrite the real key."""
    path = tmp_path / ".env"
    real = "sk-abcdefghijklmnopqrstuvwxyz"
    path.write_text(f"LLM_API_KEY={real}\n", encoding="utf-8")
    mask = env_settings.mask_secret(real)
    env_settings.save_api_settings({"LLM_API_KEY": mask}, path=path)
    assert env_settings.parse_env_file(path.read_text(encoding="utf-8"))["LLM_API_KEY"] == real


def test_save_updates_secret_when_typed(tmp_path: Path) -> None:
    """A newly typed secret replaces the previous value."""
    path = tmp_path / ".env"
    path.write_text("LLM_API_KEY=old-key-value-0000\n", encoding="utf-8")
    env_settings.save_api_settings({"LLM_API_KEY": "new-key-value-1111"}, path=path)
    data = env_settings.parse_env_file(path.read_text(encoding="utf-8"))
    assert data["LLM_API_KEY"] == "new-key-value-1111"
