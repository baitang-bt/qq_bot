"""OpenAI-compatible chat completions client (text + optional multimodal images)."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.personas.catalog import PersonaCatalog
from app.prompt_book import PromptBook

_log = logging.getLogger(__name__)

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class StickerTriage:
    """Combined sticker description plus whether to keep a local copy."""

    description: str
    save: bool
    sticker_id: str = ""
    tags: tuple[str, ...] = ()
    reason: str = ""


class LLMClient:
    """Call an OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        settings: Settings,
        prompts: PromptBook | None = None,
        stickers_prompt: str = "",
    ) -> None:
        self._settings = settings
        if prompts is not None:
            self._prompts = prompts
        else:
            catalog = PersonaCatalog(
                settings.personas_index_path,
                settings.personas_dir,
                json_migrate_path=settings.bot_prompt_path,
            )
            self._prompts = PromptBook(catalog)
        self._stickers_prompt = stickers_prompt
        self._slot = threading.Lock()

    def set_stickers_prompt(self, block: str) -> None:
        """Attach the catalog-generated sticker marker instructions for chat turns."""
        self._stickers_prompt = block or ""

    def is_configured(self) -> bool:
        """True when an API key is present so we can call a model."""
        return bool(self._settings.llm_api_key)

    def _http_timeout(self) -> httpx.Timeout:
        """Split connect/read limits so a hung socket cannot block forever."""
        total = max(5.0, self._settings.llm_timeout_seconds)
        connect = min(10.0, total)
        return httpx.Timeout(total, connect=connect, read=total, write=connect, pool=connect)

    async def _call_api(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        *,
        label: str,
    ) -> dict[str, Any]:
        """Serialize LLM HTTP calls across worker threads with a threading lock."""
        deadline = self._settings.llm_timeout_seconds + 10.0
        wait_start = time.monotonic()
        await asyncio.to_thread(self._slot.acquire)
        try:
            waited = time.monotonic() - wait_start
            if waited >= 0.3:
                _log.info("llm 排队等待 slot %.1fs label=%s", waited, label)
            api_start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._http_timeout()) as client:
                    response = await asyncio.wait_for(
                        client.post(url, headers=headers, json=payload),
                        timeout=deadline,
                    )
                    response.raise_for_status()
                    data = response.json()
            except asyncio.TimeoutError:
                _log.error(
                    "llm request timeout label=%s elapsed=%.1fs",
                    label,
                    time.monotonic() - api_start,
                )
                raise
            if not isinstance(data, dict):
                raise RuntimeError("LLM response is not a JSON object")
            elapsed = time.monotonic() - api_start
            _log.info("llm request finished label=%s elapsed=%.1fs", label, elapsed)
            return data
        finally:
            self._slot.release()

    async def describe_image(self, data: bytes, mime: str) -> str:
        """Multimodal one-shot: short Chinese sticker description for the cache."""
        triage = await self.describe_and_triage_sticker(data, mime)
        return triage.description

    async def describe_and_triage_sticker(
        self, data: bytes, mime: str
    ) -> StickerTriage:
        """Describe a sticker and decide whether it is worth saving locally."""
        if not self.is_configured() or not data:
            return StickerTriage(description="", save=False, reason="unconfigured")
        safe_mime = (mime or "image/jpeg").split(";")[0].strip().lower()
        if safe_mime == "image/jpg":
            safe_mime = "image/jpeg"
        b64 = base64.b64encode(data).decode("ascii")
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你在为 QQ 机器人处理表情包。只输出一个 JSON 对象，不要其它文字。\n"
                        "字段：description(string,一两句中文描述画面与情绪)、"
                        "save(boolean,是否值得本地收藏复用)、"
                        "id(string,小写英文蛇形 id,2-32)、"
                        "tags(string 数组,中文短标签)、"
                        "reason(string,简短理由)。\n"
                        "值得存：通用反应表情（捂脸/点赞/无奈/嘲笑等）、画面清晰可复用。\n"
                        "不存：文字截图、色情暴力、真人私照/隐私、一次性梗、空白糊图。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "评估这张表情包并输出 JSON。"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{safe_mime};base64,{b64}",
                                "detail": "auto",
                            },
                        },
                    ],
                },
            ],
            "temperature": 0.2,
            "max_tokens": 220,
            "thinking": {"type": "disabled"},
        }
        _log.info("llm request start model=%s label=sticker_triage", self._settings.llm_model)
        try:
            data_json = await self._call_api(
                url, headers, payload, label="sticker_triage"
            )
            raw = str(data_json["choices"][0]["message"]["content"]).strip()
        except Exception:
            _log.exception("sticker triage failed")
            return StickerTriage(description="", save=False, reason="api_error")
        return _parse_sticker_triage(raw)

    async def complete(
        self,
        history: list[dict[str, str]],
        user_text: str,
        images: list[tuple[bytes, str]] | None = None,
        notes: list[str] | None = None,
        impression: str = "",
    ) -> str:
        """Generate a reply; photos may be multimodal, stickers arrive as text notes."""
        image_list = list(images or ())
        note_list = [item.strip() for item in (notes or ()) if item and item.strip()]
        note_block = "\n".join(f"[表情包] {item}" for item in note_list)
        text_body = user_text.strip()
        if note_block:
            text_body = f"{text_body}\n{note_block}".strip() if text_body else note_block
        if not text_body and image_list:
            text_body = "（用户发来一张图）"
        if not self.is_configured():
            return _echo_fallback(text_body, image_list, note_list)
        if not text_body and not image_list:
            return "嗯。"
        user_content: str | list[dict[str, Any]]
        if image_list:
            parts: list[dict[str, Any]] = [{"type": "text", "text": text_body}]
            for data, mime in image_list:
                safe_mime = (mime or "image/jpeg").split(";")[0].strip().lower()
                if safe_mime == "image/jpg":
                    safe_mime = "image/jpeg"
                b64 = base64.b64encode(data).decode("ascii")
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{safe_mime};base64,{b64}",
                            "detail": "auto",
                        },
                    }
                )
            user_content = parts
        else:
            user_content = text_body
        system = self._prompts.system_text(
            impression,
            stickers_block=self._stickers_prompt,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 400,
            "thinking": {"type": "disabled"},
        }
        try:
            _log.info(
                "llm request start model=%s messages=%s images=%s notes=%s",
                self._settings.llm_model,
                len(messages),
                len(image_list),
                len(note_list),
            )
            data = await self._call_api(url, headers, payload, label="chat")
            text = str(data["choices"][0]["message"]["content"]).strip()
            _log.info("llm request done chars=%s", len(text))
            return text or "嗯。"
        except Exception:
            _log.exception("llm complete failed")
            return "这会儿没接上模型，稍后再试。"

    async def complete_plain(self, system: str, user_text: str, max_tokens: int = 400) -> str:
        """One-shot completion without chat history (used to rewrite impressions)."""
        if not self.is_configured():
            return ""
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        data = await self._call_api(url, headers, payload, label="impression")
        return str(data["choices"][0]["message"]["content"]).strip()


def _echo_fallback(
    user_text: str,
    images: list[tuple[bytes, str]],
    notes: list[str] | None = None,
) -> str:
    """Plain echo used when LLM_API_KEY is not set (webhook smoke test)."""
    if images:
        return f"收到 {len(images)} 张图" + (f"：{user_text.strip()}" if user_text.strip() else "")
    if notes:
        return f"收到表情包：{notes[0]}"
    if user_text.strip():
        return user_text.strip()
    return "收到。"


def _parse_sticker_triage(raw: str) -> StickerTriage:
    """Parse model JSON into StickerTriage; on failure keep plain text as description only."""
    text = (raw or "").strip()
    if not text:
        return StickerTriage(description="", save=False, reason="empty")
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT.search(text)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        # Model returned prose instead of JSON: keep as description, do not save.
        return StickerTriage(description=text[:200], save=False, reason="parse_error")
    description = str(payload.get("description") or "").strip()
    if not description:
        description = text[:200]
    save = bool(payload.get("save"))
    sticker_id = str(payload.get("id") or "").strip().lower()
    tags_raw = payload.get("tags") or []
    tags: tuple[str, ...] = ()
    if isinstance(tags_raw, list):
        tags = tuple(str(item).strip() for item in tags_raw if str(item).strip())[:8]
    reason = str(payload.get("reason") or "").strip()
    return StickerTriage(
        description=description,
        save=save,
        sticker_id=sticker_id,
        tags=tags,
        reason=reason,
    )
