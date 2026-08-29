"""Send passive C2C / group replies through QQ OpenAPI v2."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import httpx

from app.config import Settings
from app.qq import message_cache
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

    async def send_text(
        self,
        message: IncomingMessage,
        text: str,
        *,
        quote: bool = True,
    ) -> bool:
        """Send a passive text reply; only the first bubble should quote inbound."""
        body = (text or "").strip()
        if not body:
            return False
        seq = self.next_seq(message.msg_id)
        return await self._post(message, build_text_payload(message, body, seq, quote=quote))

    async def send_bubbles(
        self,
        message: IncomingMessage,
        bubbles: list[str],
        *,
        quote_first: bool = True,
    ) -> bool:
        """Send multiple short bubbles with human-like pauses between them."""
        cleaned = [part.strip() for part in bubbles if part and part.strip()]
        if not cleaned:
            return False
        delivered = False
        for index, bubble in enumerate(cleaned):
            if index > 0:
                delay = min(3.0, max(0.6, len(bubble) * 0.06))
                if not message.is_group:
                    try:
                        await self.send_c2c_typing(
                            message,
                            seconds=min(60, int(delay) + 2),
                        )
                    except Exception:
                        _log.warning("typing between bubbles failed", exc_info=True)
                await asyncio.sleep(delay)
            if await self.send_text(
                message,
                bubble,
                quote=(index == 0 and quote_first),
            ):
                delivered = True
            else:
                return delivered
        return delivered

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

    async def _post(self, message: IncomingMessage, payload: dict) -> bool:
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
            return False
        text = str(payload.get("content") or "").strip()
        if text:
            out_id = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    out_id = str(body.get("id") or body.get("msg_id") or "")
            except ValueError:
                out_id = ""
            message_cache.remember_bot(
                out_id,
                text,
                group_openid=message.group_openid,
                user_openid=message.user_openid,
            )
        return True


def user_turns_since_last_bot(history: list[dict[str, str]]) -> int:
    """Count user lines since the last assistant turn, including the inbound being answered."""
    count = 1
    for turn in reversed(history):
        if turn["role"] == "assistant":
            break
        if turn["role"] == "user":
            count += 1
    return count


def should_quote_inbound(history: list[dict[str, str]], *, max_unquoted_gap: int = 3) -> bool:
    """Return True when the reply should quote the inbound QQ message (gap > max_unquoted_gap)."""
    return user_turns_since_last_bot(history) > max_unquoted_gap


def build_text_payload(
    message: IncomingMessage,
    text: str,
    seq: int,
    *,
    quote: bool = True,
) -> dict:
    """Build a text send body; optionally quote the target inbound message."""
    payload = {
        "content": text[:1500],
        "msg_type": 0,
        "msg_id": message.msg_id,
        "msg_seq": seq,
    }
    if not quote:
        return payload
    quote_id = (message.quote_id or message.msg_id).strip()
    if quote_id:
        payload["message_reference"] = {
            "message_id": quote_id,
            "ignore_get_message_error": True,
        }
    return payload
