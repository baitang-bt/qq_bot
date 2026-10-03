"""LLM client: event-loop safety and multimodal payload shape."""

import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings
from app.llm.client import LLMClient, _parse_sticker_triage


def _client(tmp_path: Path) -> LLMClient:
    """Build a client with a dummy API key for unit tests."""
    settings = Settings(
        qq_app_id="",
        qq_app_secret="",
        qq_api_base="https://example.com",
        qq_token_url="https://example.com/token",
        llm_base_url="https://example.com/v1",
        llm_api_key="test-key",
        llm_model="deepseek-flash",
        vision_model="deepseek-flash",
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
        stickers_dir=tmp_path / "stickers",
        stickers_index_path=tmp_path / "stickers.toml",
        sticker_auto_learn=True,
        sticker_learn_max=200,
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
            return await client.complete([], "ping")

    assert asyncio.run(once()) == "ok"
    assert asyncio.run(once()) == "ok"


def test_complete_text_only_keeps_string_content(tmp_path: Path) -> None:
    """Without images, user content stays a plain string and thinking is disabled."""
    client = _client(tmp_path)
    captured: dict = {}

    async def run() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": "hi"}}]}
        )

        async def fake_post(*_args, **kwargs):
            captured["json"] = kwargs["json"]
            return mock_response

        with patch("httpx.AsyncClient.post", new=fake_post):
            return await client.complete([], "ping")

    assert asyncio.run(run()) == "hi"
    body = captured["json"]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][-1]["content"] == "ping"


def test_complete_with_images_builds_multimodal_payload(tmp_path: Path) -> None:
    """With images, user content is a text + image_url parts list using a data URL."""
    client = _client(tmp_path)
    captured: dict = {}
    png = b"\x89PNG\r\n\x1a\nfake"
    b64 = base64.b64encode(png).decode("ascii")

    async def run() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": "一只猫"}}]}
        )

        async def fake_post(*_args, **kwargs):
            captured["json"] = kwargs["json"]
            return mock_response

        with patch("httpx.AsyncClient.post", new=fake_post):
            return await client.complete(
                [],
                "这是什么",
                images=[(png, "image/png")],
            )

    assert asyncio.run(run()) == "一只猫"
    content = captured["json"]["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "这是什么"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{b64}"
    assert content[1]["image_url"]["detail"] == "auto"
    assert captured["json"]["thinking"] == {"type": "disabled"}


def test_complete_image_only_uses_default_text(tmp_path: Path) -> None:
    """Pure-image turns still send a text part so the multimodal request is valid."""
    client = _client(tmp_path)
    captured: dict = {}

    async def run() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": "图"}}]}
        )

        async def fake_post(*_args, **kwargs):
            captured["json"] = kwargs["json"]
            return mock_response

        with patch("httpx.AsyncClient.post", new=fake_post):
            return await client.complete([], "", images=[(b"abc", "image/jpeg")])

    assert asyncio.run(run()) == "图"
    assert captured["json"]["messages"][-1]["content"][0]["text"] == "（用户发来一张图）"


def test_complete_with_notes_appends_sticker_text(tmp_path: Path) -> None:
    """Sticker notes become [表情包] lines in plain string user content (no images)."""
    client = _client(tmp_path)
    captured: dict = {}

    async def run() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": "哈哈哈"}}]}
        )

        async def fake_post(*_args, **kwargs):
            captured["json"] = kwargs["json"]
            return mock_response

        with patch("httpx.AsyncClient.post", new=fake_post):
            return await client.complete([], "", notes=["一只猫在捂脸"])

    assert asyncio.run(run()) == "哈哈哈"
    assert captured["json"]["messages"][-1]["content"] == "[表情包] 一只猫在捂脸"


def test_describe_image_sends_multimodal_sticker_payload(tmp_path: Path) -> None:
    """describe_image uses triage multimodal with thinking disabled."""
    client = _client(tmp_path)
    captured: dict = {}
    jpeg = b"fakejpeg"
    triage_json = (
        '{"description":"捂脸猫","save":false,"id":"facepalm_cat",'
        '"tags":["捂脸"],"reason":"ok"}'
    )

    async def run() -> str:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={"choices": [{"message": {"content": triage_json}}]}
        )

        async def fake_post(*_args, **kwargs):
            captured["json"] = kwargs["json"]
            return mock_response

        with patch("httpx.AsyncClient.post", new=fake_post):
            return await client.describe_image(jpeg, "image/jpeg")

    assert asyncio.run(run()) == "捂脸猫"
    body = captured["json"]
    assert body["model"] == "deepseek-flash"
    assert body["thinking"] == {"type": "disabled"}
    content = body["messages"][-1]["content"]
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_parse_sticker_triage_save_true() -> None:
    """Valid triage JSON sets save, id, tags, and description."""
    result = _parse_sticker_triage(
        '{"description":"一只猫捂脸","save":true,"id":"facepalm_cat",'
        '"tags":["捂脸","无奈"],"reason":"通用"}'
    )
    assert result.save is True
    assert result.description == "一只猫捂脸"
    assert result.sticker_id == "facepalm_cat"
    assert result.tags == ("捂脸", "无奈")


def test_parse_sticker_triage_invalid_keeps_text() -> None:
    """Non-JSON prose becomes description only; save stays false."""
    result = _parse_sticker_triage("一只猫在捂脸，无奈")
    assert result.save is False
    assert result.description == "一只猫在捂脸，无奈"
    assert result.reason == "parse_error"