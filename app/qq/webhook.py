"""FastAPI webhook endpoint for QQ callback validation and events."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.bot import ChatBot
from app.config import Settings
from app.qq.crypto import sign_validation, verify_event_signature
from app.qq.dedupe import MessageDedupe
from app.qq.events import parse_incoming

_log = logging.getLogger(__name__)

OPCODE_DISPATCH = 0
OPCODE_HTTP_CALLBACK_ACK = 12
OPCODE_VALIDATION = 13


def payload_op_hint(body: bytes) -> str:
    """Read opcode for logs without dumping the payload."""
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "?"
    if not isinstance(data, dict):
        return "?"
    return str(data.get("op", ""))


def create_webhook_router(
    settings: Settings, bot: ChatBot, dedupe: MessageDedupe
) -> APIRouter:
    """Build the `/qq/webhook` router bound to this process's bot."""
    router = APIRouter()

    @router.get("/qq/webhook")
    async def qq_webhook_probe() -> Response:
        """Answer URL probes; some consoles GET the callback before opcode-13."""
        return Response(content='{"ok":true}', media_type="application/json")

    @router.post("/qq/webhook")
    async def qq_webhook(request: Request, background: BackgroundTasks) -> Response:
        """Verify QQ callbacks, answer opcode-13, and dispatch chat events."""
        body = await request.body()
        _log.info(
            "webhook %s ua=%s op=%s bytes=%s",
            request.method,
            (request.headers.get("user-agent") or "")[:80],
            payload_op_hint(body),
            len(body),
        )
        try:
            payload: dict[str, Any] = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return Response(status_code=400, content="invalid json")
        opcode = payload.get("op")
        if opcode == OPCODE_VALIDATION:
            return _validation_response(settings.qq_app_secret, payload)
        if not _signature_ok(settings.qq_app_secret, request, body):
            return Response(status_code=401, content="invalid signature")
        if opcode not in (OPCODE_DISPATCH, None):
            return _ack()
        message = parse_incoming(payload)
        if message is None:
            return _ack()
        if dedupe.already_handled(message.msg_id):
            return _ack()
        background.add_task(_run_bot, bot, message)
        return _ack()

    return router


def _validation_response(secret: str, payload: dict[str, Any]) -> Response:
    """Answer opcode-13 URL verification with a signed plain_token."""
    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    plain_token = str(data.get("plain_token") or data.get("plainToken") or "")
    event_ts = str(data.get("event_ts") or data.get("eventTs") or "")
    signature = sign_validation(secret, event_ts, plain_token)
    body = json.dumps({"plain_token": plain_token, "signature": signature})
    return Response(content=body, media_type="application/json")


def _signature_ok(secret: str, request: Request, body: bytes) -> bool:
    """Verify Ed25519 headers when present; reject if they are missing."""
    signature = (
        request.headers.get("X-Signature-Ed25519")
        or request.headers.get("X-Signature-Ed25519")
        or request.headers.get("X-Signature-Ed25519")
        or ""
    )
    timestamp = (
        request.headers.get("X-Signature-Timestamp")
        or request.headers.get("X-Signature-Timestamp")
        or request.headers.get("X-Signature-Timestamp")
        or ""
    )
    if not secret:
        return False
    if not signature or not timestamp:
        _log.warning("webhook missing signature headers")
        return False
    if not _timestamp_fresh(timestamp):
        _log.warning("webhook timestamp stale")
        return False
    return verify_event_signature(secret, timestamp, body, signature)


def _timestamp_fresh(timestamp: str, now: float | None = None) -> bool:
    """Reject signatures whose timestamp is more than 5 minutes off."""
    try:
        raw = int(timestamp)
    except ValueError:
        return False
    if raw > 10**12:
        raw = raw / 1000.0
    stamp = time.time() if now is None else now
    return abs(stamp - raw) <= 300


def _ack() -> Response:
    """HTTP callback ACK (opcode 12)."""
    return Response(
        content=json.dumps({"op": OPCODE_HTTP_CALLBACK_ACK}),
        media_type="application/json",
    )


async def _run_bot(bot: ChatBot, message: Any) -> None:
    """Background task wrapper so webhook ACK is not delayed by the model."""
    try:
        await bot.enqueue(message)
    except Exception:
        _log.exception("bot handle failed msg_id=%s", getattr(message, "msg_id", ""))
