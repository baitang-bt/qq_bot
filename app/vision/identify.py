"""Download QQ attachments and describe them with a vision model."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import httpx
from PIL import Image

from app.config import Settings
from app.qq.events import Attachment
from app.vision.cache import ImageCache
from app.vision.keys import keys_from_attachment, md5_key

_log = logging.getLogger(__name__)

_VISION_PROMPT = (
    "用一两句中文说明这张图或表情包在表达什么。"
    "如果有文字就读出来。不要猜测隐私信息。"
)


class ImageIdentifier:
    """Resolve sticker/image descriptions with cache-first vision calls."""

    def __init__(self, settings: Settings, cache: ImageCache) -> None:
        self._settings = settings
        self._cache = cache

    def peek_cached(self, attachments: tuple[Attachment, ...]) -> list[str] | None:
        """Return descriptions if every image already has a pre-download cache hit."""
        notes: list[str] = []
        for attachment in attachments:
            keys = keys_from_attachment(attachment)
            hit = self._cache.get(keys)
            if not hit:
                return None
            notes.append(hit)
        return notes if notes else []

    async def describe_all(self, attachments: tuple[Attachment, ...]) -> list[str]:
        """Describe each image, using cache whenever a key matches."""
        notes: list[str] = []
        for attachment in attachments:
            notes.append(await self._describe_one(attachment))
        return notes

    async def _describe_one(self, attachment: Attachment) -> str:
        """Cache lookup, then download + vision, then store all keys."""
        keys = keys_from_attachment(attachment)
        hit = self._cache.get(keys)
        if hit:
            _log.info("image_cache hit keys=%s", keys)
            return hit
        data, mime = await self._download(attachment)
        if not data:
            return "没看清这张图"
        keys = list(dict.fromkeys([*keys, md5_key(data)]))
        hit = self._cache.get(keys)
        if hit:
            _log.info("image_cache md5 hit keys=%s", keys)
            self._cache.put(keys, hit, source="md5-hit")
            return hit
        _log.info("vision call keys=%s bytes=%s", keys, len(data))
        description = await self._vision(data, mime)
        self._cache.put(keys, description, source="vision")
        return description

    async def _download(self, attachment: Attachment) -> tuple[bytes, str]:
        """Fetch attachment bytes and normalize GIF/webp to a JPEG first frame."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                response = await client.get(
                    attachment.url,
                    headers={"User-Agent": "QQBot"},
                )
            response.raise_for_status()
            data = response.content
        except Exception:
            _log.exception("attachment download failed")
            return b"", "image/jpeg"
        mime = (attachment.content_type or response.headers.get("content-type") or "image/jpeg")
        mime = mime.split(";")[0].strip().lower()
        if mime in {"image/gif", "image/webp"} or attachment.filename.lower().endswith(
            (".gif", ".webp")
        ):
            data, mime = _first_frame_jpeg(data)
        if mime not in {"image/jpeg", "image/png", "image/jpg"}:
            data, mime = _first_frame_jpeg(data)
        return data, mime

    async def _vision(self, data: bytes, mime: str) -> str:
        """Call the OpenAI-compatible vision chat API with a data URL."""
        if not self._settings.llm_api_key:
            return "收到一张图（未配置视觉模型）"
        b64 = base64.b64encode(data).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        url = f"{self._settings.llm_base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._settings.vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "max_tokens": 200,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.vision_timeout_seconds
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            text = str(response.json()["choices"][0]["message"]["content"]).strip()
            return text or "图里没什么能说的"
        except Exception:
            _log.exception("vision call failed")
            return "图没看清"


def _first_frame_jpeg(data: bytes) -> tuple[bytes, str]:
    """Convert an animated or exotic image to a single JPEG frame."""
    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue(), "image/jpeg"
    except Exception:
        _log.exception("image convert failed")
        return data, "image/jpeg"
