"""Send passive C2C / group replies through QQ OpenAPI v2."""

from __future__ import annotations

import logging
from collections import defaultdict

import httpx

from app.config import Settings
from app.qq.events import IncomingMessage
from app.qq.token import TokenManager

_log = logging.getLogger(__name__)


class ReplyClient:
    """Post text (and C2C input-status) replies bound to an inbound msg_id."""

    def __init__(self, settings: Settings, tokens: TokenManager) -> None:
        self._settings = settings
        self._tokens = tokens
        self._seq: dict[str, int] = defaultdict(int)

    def next_seq(self, msg_id: str) -> int:
        """Increment the per-msg_id reply sequence used by QQ de-duplication."""
        self._seq[msg_id] += 1
        return self._seq[msg_id]

    async def send_text(self, message: IncomingMessage, text: str) -> None:
        """Send a passive text reply that quotes the inbound message."""
        body = (text or "").strip()
        if not body:
            return
        seq = self.next_seq(message.msg_id)
        await self._post(message, build_text_payload(message, body, seq))

    async def send_c2c_typing(self, message: IncomingMessage, seconds: int = 20) -> None:
        """Show a C2C 'typing' indicator. Group chats have no equivalent."""
        if message.is_group:
            return
        seq = self.next_seq(message.msg_id)
        payload = {
            "msg_type": 6,
            "msg_id": message.msg_id,
            "msg_seq": seq,
            "input_notify": {"input_type": 1, "input_second": max(1, min(seconds, 60))},
        }
        await self._post(message, payload)

    async def _post(self, message: IncomingMessage, payload: dict) -> None:
        """POST a message payload to the C2C or group messages endpoint."""
        if message.is_group:
            path = f"/v2/groups/{message.group_openid}/messages"
        else:
            path = f"/v2/users/{message.user_openid}/messages"
        url = f"{self._settings.qq_api_base}{path}"
        headers = await self._tokens.auth_headers()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            _log.warning(
                "reply failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            response.raise_for_status()


def build_text_payload(message: IncomingMessage, text: str, seq: int) -> dict:
    """Build a text send body that quotes the target inbound message."""
    payload = {
        "content": text[:1500],
        "msg_type": 0,
        "msg_id": message.msg_id,
        "msg_seq": seq,
    }
    quote_id = (message.quote_id or message.msg_id).strip()
    if quote_id:
        payload["message_reference"] = {
            "message_id": quote_id,
            "ignore_get_message_error": True,
        }
    return payload
