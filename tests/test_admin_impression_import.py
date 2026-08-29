"""Admin store helpers for impression import."""

import json
from dataclasses import replace
from pathlib import Path

from app.admin import store
from app.config import load_settings


def test_import_single_record_json(tmp_path: Path, monkeypatch) -> None:
    """One JSON object with user_openid is imported."""
    data_dir = tmp_path / "data"
    impressions_dir = data_dir / "impressions"
    impressions_dir.mkdir(parents=True)
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )

    payload = {
        "user_openid": "openid-import-1",
        "username": "导入用户",
        "qq": "123456",
        "impression": "测试印象正文。",
    }
    path = tmp_path / "one.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    imported, skipped, errors = store.import_impression_files([path])
    assert imported == 1
    assert skipped == 0
    assert not errors
    record = store.load_impression("openid-import-1")
    assert record["username"] == "导入用户"
    assert "测试印象" in record["impression"]


def test_import_array_json(tmp_path: Path, monkeypatch) -> None:
    """A JSON array imports multiple records."""
    data_dir = tmp_path / "data"
    (data_dir / "impressions").mkdir(parents=True)
    monkeypatch.setattr(
        store,
        "load_settings",
        lambda: replace(load_settings(), data_dir=data_dir),
    )

    payload = [
        {"user_openid": "a1", "username": "A", "impression": "one"},
        {"user_openid": "a2", "username": "B", "impression": "two"},
        {"username": "missing-openid"},
    ]
    path = tmp_path / "many.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    imported, skipped, errors = store.import_impression_files([path])
    assert imported == 2
    assert skipped == 1
    assert store.load_impression("a1")["username"] == "A"
