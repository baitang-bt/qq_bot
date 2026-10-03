"""Send passive C2C / group replies through QQ OpenAPI v2."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import httpx

from app.config import Settings
from app.qq import message_cache
from app.qq.events import IncomingMessage
from app.qq.media import MediaUploader
from app.qq.token import TokenManager
from app.stickers.markers import ReplySegment, StickerSeg, TextSeg

_log = logging.getLogger(__name__)


class ReplyClient:
    """Post text / rich-media replies bound to an inbound msg_id."""

    def __init__(
        self,
        settings: Settings,
        tokens: TokenManager,
        media: MediaUploader | None = None,
    ) -> None:
        self._settings = settings
        self._tokens = tokens
        self._media = media or MediaUploader(settings, tokens)
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

    async def send_image(
        self,
        message: IncomingMessage,
        path: Path,
        *,
        quote: bool = False,
    ) -> bool:
        """Upload a local PNG/JPG and send it as msg_type=7 rich media."""
        try:
            file_info = await self._media.upload_image(message, path)
        except Exception:
            _log.exception("send_image upload failed path=%s", path)
            return False
        seq = self.next_seq(message.msg_id)
        payload: dict = {
            "msg_type": 7,
            "msg_id": message.msg_id,
            "msg_seq": seq,
            "media": {"file_info": file_info},
        }
        if quote:
            quote_id = (message.quote_id or message.msg_id).strip()
            if quote_id:
                payload["message_reference"] = {
                    "message_id": quote_id,
                    "ignore_get_message_error": True,
                }
        ok = await self._post(
            message,
            payload,
            remember_text=f"[发送图片:{path.name}]",
        )
        if ok:
            _log.info("sent image path=%s", path.name)
        return ok

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

    async def send_segments(
        self,
        message: IncomingMessage,
        segments: list[ReplySegment],
        *,
        resolve_sticker: Callable[[str], Path | None],
        quote_first: bool = True,
    ) -> bool:
        """Send ordered text / sticker segments; resolve_sticker(id) -> Path | None."""
        if not segments:
            return False
        delivered = False
        first = True
        for index, seg in enumerate(segments):
            if index > 0:
                await asyncio.sleep(0.5)
            if isinstance(seg, TextSeg):
                text = seg.text.strip()
                if not text:
                    continue
                if index > 0 and not message.is_group:
                    try:
                        await self.send_c2c_typing(
                            message,
                            seconds=min(60, max(2, int(len(text) * 0.06) + 1)),
                        )
                    except Exception:
                        _log.warning("typing before text segment failed", exc_info=True)
                ok = await self.send_text(
                    message,
                    text,
                    quote=(first and quote_first),
                )
                if ok:
                    delivered = True
                    first = False
                else:
                    return delivered
            elif isinstance(seg, StickerSeg):
                path = resolve_sticker(seg.sticker_id)
                if path is None:
                    _log.warning("sticker resolve failed id=%s", seg.sticker_id)
                    continue
                ok = await self.send_image(
                    message,
                    path,
                    quote=(first and quote_first),
                )
                if ok:
                    delivered = True
                    first = False
                else:
                    _log.warning("sticker send failed id=%s", seg.sticker_id)
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

    async def _post(
        self,
        message: IncomingMessage,
        payload: dict,
        *,
        remember_text: str = "",
    ) -> bool:
        """POST a message payload to the C2C or group messages endpoint."""
        if message.is_group:
            path = f"/v2/groups/{message.group_openid}/messages"
        else:
            path = f"/v2/users/{message.user_openid}/messages"
        url = f"{self._settings.qq_api_base}{path}"
        headers = await self._tokens.auth_headers()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            _log.warning(
                "reply failed status=%s body=%s",
                response.status_code,
                response.text[:400],
            )
            return False
        # Typing indicators are not chat messages — skip quote cache.
        if int(payload.get("msg_type") or 0) == 6:
            return True
        text = (remember_text or str(payload.get("content") or "")).strip()
        ref_idx, out_id = parse_send_response_ids(response)
        remember_outbound_send(
            message,
            text=text,
            ref_idx=ref_idx,
            out_id=out_id,
        )
        return True


def parse_send_response_ids(response: httpx.Response) -> tuple[str, str]:
    """Read ext_info.ref_idx and message id from a QQ send response."""
    try:
        body = response.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    ext = body.get("ext_info")
    ref_idx = ""
    if isinstance(ext, dict):
        ref_idx = str(ext.get("ref_idx") or "").strip()
    out_id = str(body.get("id") or body.get("msg_id") or "").strip()
    return ref_idx, out_id


def remember_outbound_send(
    message: IncomingMessage,
    *,
    text: str,
    ref_idx: str,
    out_id: str,
) -> None:
    """Cache outbound bot text under REFIDX (required for inbound quote lookup)."""
    body = (text or "").strip()
    key = (ref_idx or "").strip()
    alt = (out_id or "").strip()
    if key:
        message_cache.remember_bot(
            key,
            body or "[消息]",
            group_openid=message.group_openid,
            user_openid=message.user_openid,
            alt_id=alt,
        )
        _log.info("remember_bot ref_idx=%s chars=%s", key[:28], len(body))
        return
    if alt and body:
        # Do not mark ROBOT1.0 ids as bot REFIDX — inbound uses ref_msg_idx.
        message_cache.remember(alt, body)
        _log.warning(
            "send missing ext_info.ref_idx; cached id only id=%s",
            alt[:28],
        )
        return
    if not key:
        _log.warning("send missing ext_info.ref_idx and usable id")


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
