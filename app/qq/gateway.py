"""Outbound QQ Gateway WebSocket: no public URL required."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets

from app.bot import ChatBot
from app.config import Settings
from app.qq.dedupe import MessageDedupe
from app.qq.events import parse_incoming
from app.qq.token import TokenManager

_log = logging.getLogger(__name__)

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# C2C + group @ events
INTENT_GROUP_AND_C2C = 1 << 25


class QQGateway:
    """Keep a websocket session to QQ so events arrive without inbound webhook."""

    def __init__(
        self,
        settings: Settings,
        tokens: TokenManager,
        bot: ChatBot,
        dedupe: MessageDedupe,
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._bot = bot
        self._dedupe = dedupe
        self._stop = asyncio.Event()
        self._seq: int | None = None
        self._session_id = ""

    async def run_forever(self) -> None:
        """Connect, identify, and reconnect until stop() is called."""
        while not self._stop.is_set():
            try:
                await self._one_session()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("gateway session ended")
            if self._stop.is_set():
                break
            await asyncio.sleep(3)

    def stop(self) -> None:
        """Ask the reconnect loop to exit."""
        self._stop.set()

    async def _one_session(self) -> None:
        """Run hello → identify → heartbeat until the socket closes."""
        url = await self._gateway_url()
        _log.info("connecting gateway")
        async with websockets.connect(url, max_size=2**22, ping_interval=None) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("op") != OP_HELLO:
                raise RuntimeError(f"expected hello, got {hello.get('op')}")
            interval_ms = int((hello.get("d") or {}).get("heartbeat_interval") or 45000)
            await self._identify(ws)
            hb = asyncio.create_task(self._heartbeat(ws, interval_ms / 1000))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    await self._on_packet(ws, raw)
                _log.warning("gateway socket closed by peer")
            finally:
                hb.cancel()
                _log.info("gateway session finished")

    async def _gateway_url(self) -> str:
        """GET /gateway and return the websocket URL."""
        headers = await self._tokens.auth_headers()
        _log.info("fetching gateway url")
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await asyncio.wait_for(
                client.get(
                    f"{self._settings.qq_api_base}/gateway",
                    headers=headers,
                ),
                timeout=12.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        url = str(data.get("url") or "")
        if not url:
            raise RuntimeError("gateway url missing")
        _log.info("gateway url ok")
        return url

    async def _identify(self, ws: Any) -> None:
        """Send OpCode 2 Identify with a fresh access token."""
        token = await self._tokens.get_token()
        payload = {
            "op": OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": INTENT_GROUP_AND_C2C,
                "shard": [0, 1],
                "properties": {"$os": "darwin", "$browser": "qq-chat-bot", "$device": "mac"},
            },
        }
        await ws.send(json.dumps(payload))

    async def _send_heartbeat(self, ws: Any) -> None:
        """Push one OpCode 1 heartbeat with the last seen sequence."""
        await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._seq}))

    async def _heartbeat(self, ws: Any, interval: float) -> None:
        """Wait for READY, then heartbeat on the Hello interval."""
        await asyncio.sleep(1)
        try:
            await self._send_heartbeat(ws)
        except Exception:
            return
        while not self._stop.is_set():
            await asyncio.sleep(max(interval * 0.8, 5))
            try:
                await self._send_heartbeat(ws)
            except Exception:
                return

    async def _on_packet(self, ws: Any, raw: str | bytes) -> None:
        """Handle one gateway payload."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return
        seq = payload.get("s")
        if isinstance(seq, int):
            self._seq = seq
        op = payload.get("op")
        _log.info("gateway packet op=%s t=%s", op, payload.get("t"))
        if op == OP_DISPATCH:
            await self._dispatch(payload)
        elif op == OP_RECONNECT:
            await ws.close()
        elif op == OP_INVALID:
            self._session_id = ""
            _log.warning("gateway invalid session")
            await ws.close()
        elif op == OP_HEARTBEAT:
            await self._send_heartbeat(ws)
        elif op == OP_HEARTBEAT_ACK:
            _log.debug("gateway heartbeat ack")

    async def _dispatch(self, payload: dict[str, Any]) -> None:
        """Turn a Dispatch event into a ChatBot.handle call."""
        event = str(payload.get("t") or "")
        data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
        if event == "READY":
            self._session_id = str(data.get("session_id") or "")
            _log.info("gateway ready session=%s", self._session_id[:8])
            return
        if event == "RESUMED":
            _log.info("gateway resumed")
            return
        message = parse_incoming(payload)
        if message is None:
            _log.info("gateway ignore t=%s", event or "-")
            return
        if self._dedupe.already_handled(message.msg_id):
            _log.info("gateway duplicate msg_id=%s", message.msg_id)
            return
        _log.info(
            "gateway chat t=%s msg_id=%s group=%s",
            event,
            message.msg_id,
            bool(message.group_openid),
        )
        asyncio.create_task(self._run_bot(message))

    async def _run_bot(self, message: Any) -> None:
        """Run the bot without blocking the gateway receive loop."""
        try:
            await self._bot.handle(message)
        except Exception:
            _log.exception("bot handle failed msg_id=%s", getattr(message, "msg_id", ""))
