"""LLM client must work across isolated asyncio.run worker loops."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings
from app.llm.client import LLMClient


def _client(tmp_path: Path) -> LLMClient:
    """Build a client with a dummy API key for unit tests."""
    settings = Settings(
        qq_app_id="",
        qq_app_secret="",
        qq_api_base="https://example.com",
        qq_token_url="https://example.com/token",
        llm_base_url="https://example.com/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        vision_model="test-model",
        llm_timeout_seconds=25.0,
        vision_timeout_seconds=20.0,
        memory_max_turns=12,
        data_dir=tmp_path,
        reply_policy_path=tmp_path / "reply_policy.toml",
        bot_prompt_path=tmp_path / "bot_prompt.json",
        qq_id="",
        host="127.0.0.1",
        port=8080,
        coalesce_burst_seconds=30.0,
        coalesce_debounce_seconds=3.0,
        coalesce_single_debounce_seconds=3.0,
    )
    (tmp_path / "bot_prompt.json").write_text(
        '{"persona":"test","anti_injection":[],"stay_on_prompt":[]}',
        encoding="utf-8",
    )
    return LLMClient(settings)


def test_llm_client_survives_multiple_fresh_event_loops(tmp_path: Path) -> None:
    """Thread-pool workers each call asyncio.run; locks must not bind to one loop."""
    client = _client(tmp_path)
    payload = {"choices": [{"message": {"content": "ok"}}]}

    async def once() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=payload)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            return await client.complete([], "ping", [])

    assert asyncio.run(once()) == "ok"
    assert asyncio.run(once()) == "ok"
