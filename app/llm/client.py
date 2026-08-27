"""OpenAI-compatible chat completions client."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.prompt_book import PromptBook

_log = logging.getLogger(__name__)


class LLMClient:
    """Call an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, settings: Settings, prompts: PromptBook | None = None) -> None:
        self._settings = settings
        self._prompts = prompts or PromptBook(settings.bot_prompt_path)

    def is_configured(self) -> bool:
        """True when an API key is present so we can call a model."""
        return bool(self._settings.llm_api_key)

    async def complete(
        self,
        history: list[dict[str, str]],
        user_text: str,
        image_notes: list[str],
        impression: str = "",
    ) -> str:
        """Generate a reply from short memory plus the current user turn."""
        if not self.is_configured():
            return _echo_fallback(user_text, image_notes)
        parts = [user_text.strip()] if user_text.strip() else []
        for index, note in enumerate(image_notes, start=1):
            parts.append(f"[图片识别{index}] {note}")
        user_content = "\n".join(parts).strip() or "（用户发来一张图）"
        system = self._prompts.system_text(impression)
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_content})
        url = f"{self._settings.llm_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._settings.llm_model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 400,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.llm_timeout_seconds
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            text = str(data["choices"][0]["message"]["content"]).strip()
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
        payload = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(timeout=self._settings.llm_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return str(data["choices"][0]["message"]["content"]).strip()


def _echo_fallback(user_text: str, image_notes: list[str]) -> str:
    """Plain echo used when LLM_API_KEY is not set (webhook smoke test)."""
    if image_notes:
        return "看到图了：" + "；".join(image_notes)[:500]
    if user_text.strip():
        return user_text.strip()
    return "收到。"
