"""Webhook opcode-13 validation and signature-gated dispatch."""

import json
import time

from fastapi.testclient import TestClient

from app.qq.crypto import private_key_from_secret, sign_validation


def test_health(monkeypatch) -> None:
    """Health endpoint does not require QQ credentials."""
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_opcode_13_validation(monkeypatch) -> None:
    """Callback URL verification returns a signature over event_ts+plain_token."""
    secret = "DG5g3B4j9X2KOErG"
    monkeypatch.setenv("QQ_APP_ID", "11111111")
    monkeypatch.setenv("QQ_APP_SECRET", secret)
    from app.main import create_app

    client = TestClient(create_app())
    payload = {
        "op": 13,
        "d": {"plain_token": "Arq0D5A61EgUu4OxUvOp", "event_ts": "1725442341"},
    }
    response = client.post("/qq/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["plain_token"] == "Arq0D5A61EgUu4OxUvOp"
    expected = sign_validation(secret, "1725442341", "Arq0D5A61EgUu4OxUvOp")
    assert data["signature"] == expected


def test_dispatch_rejects_bad_signature(monkeypatch) -> None:
    """Chat events without a valid Ed25519 header are rejected."""
    secret = "b" * 32
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", secret)
    from app.main import create_app

    client = TestClient(create_app())
    payload = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {"id": "m1", "content": "hi", "author": {"user_openid": "u1"}},
    }
    response = client.post("/qq/webhook", json=payload)
    assert response.status_code == 401


def test_dispatch_ack_with_valid_signature(monkeypatch) -> None:
    """A signed C2C event is ACKed with opcode 12."""
    secret = "c" * 32
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", secret)
    monkeypatch.setenv("LLM_API_KEY", "")

    async def _noop(_self, _message) -> None:
        return None

    monkeypatch.setattr("app.bot.ChatBot.handle", _noop)
    from app.main import create_app

    body_obj = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {"id": "m2", "content": "hi", "author": {"user_openid": "u1"}},
    }
    raw = json.dumps(payload_obj := body_obj, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = private_key_from_secret(secret).sign(timestamp.encode() + raw).hex()
    client = TestClient(create_app())
    response = client.post(
        "/qq/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert response.status_code == 200
    assert response.json()["op"] == 12
    assert payload_obj["d"]["id"] == "m2"


def test_docs_disabled(monkeypatch) -> None:
    """OpenAPI docs are not exposed on the public process."""
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_unknown_path_not_found(monkeypatch) -> None:
    """Paths other than webhook/health are not served."""
    monkeypatch.setenv("QQ_APP_ID", "app")
    monkeypatch.setenv("QQ_APP_SECRET", "secret-secret-secret-secret-1234")
    from app.main import create_app

    client = TestClient(create_app())
    assert client.get("/").status_code == 404
    assert client.post("/admin").status_code == 404
    assert client.get("/qq/webhook").status_code == 200
