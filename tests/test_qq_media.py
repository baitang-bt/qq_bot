"""QQ media checksums and chunked upload mock flow."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

from app.config import Settings
from app.qq.events import C2C_EVENT, IncomingMessage
from app.qq.media import MediaUploader, file_checksums
from app.qq.reply import ReplyClient


def _settings(tmp_path: Path) -> Settings:
    """Minimal settings for media upload unit tests."""
    return Settings(
        qq_app_id="",
        qq_app_secret="",
        qq_api_base="https://api.example.com",
        qq_token_url="https://example.com/token",
        llm_base_url="https://example.com/v1",
        llm_api_key="",
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


def test_file_checksums_stable() -> None:
    """md5 / sha1 / md5_10m match hashlib for a fixed fixture."""
    data = b"abc" * 1000
    md5, sha1, md5_10m = file_checksums(data)
    assert md5 == hashlib.md5(data).hexdigest()
    assert sha1 == hashlib.sha1(data).hexdigest()
    assert md5_10m == hashlib.md5(data[:10_002_432]).hexdigest()


def test_upload_image_chunked_flow(tmp_path: Path) -> None:
    """prepare → PUT → part_finish → files merge returns file_info."""
    path = tmp_path / "a.png"
    Image.new("RGB", (4, 4), (9, 9, 9)).save(path)
    data = path.read_bytes()
    md5, sha1, md5_10m = file_checksums(data)

    tokens = MagicMock()
    tokens.auth_headers = AsyncMock(return_value={"Authorization": "Bearer t"})
    uploader = MediaUploader(_settings(tmp_path), tokens)
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m1",
        content="x",
        user_openid="user-1",
        group_openid=None,
    )

    calls: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        method = request.method
        url = str(request.url)
        calls.append((method, url))
        if url.endswith("/upload_prepare"):
            body = await request.aread()
            assert md5.encode() in body
            assert sha1.encode() in body
            assert md5_10m.encode() in body
            return httpx.Response(
                200,
                json={
                    "upload_id": "up1",
                    "block_size": str(len(data)),
                    "parts": [
                        {
                            "index": 0,
                            "presigned_url": "https://cos.example.com/p0",
                            "block_size": str(len(data)),
                        }
                    ],
                },
            )
        if url.startswith("https://cos.example.com/"):
            assert method == "PUT"
            return httpx.Response(200, content=b"ok")
        if url.endswith("/upload_part_finish"):
            return httpx.Response(200, json={})
        if url.endswith("/files"):
            return httpx.Response(
                200,
                json={"file_info": "FILEINFO", "ttl": 600},
            )
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)

    async def run() -> str:
        original = httpx.AsyncClient

        def factory(*_args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["transport"] = transport
            return original(*_args, **kwargs)

        with patch("app.qq.media.httpx.AsyncClient", side_effect=factory):
            return await uploader.upload_image(message, path)

    file_info = asyncio.run(run())
    assert file_info == "FILEINFO"
    assert any(u.endswith("/upload_prepare") for _, u in calls)
    assert any(u.startswith("https://cos.example.com/") for _, u in calls)
    assert any(u.endswith("/upload_part_finish") for _, u in calls)
    assert any(u.endswith("/files") for _, u in calls)


def test_send_image_posts_msg_type_7(tmp_path: Path) -> None:
    """ReplyClient.send_image uploads then posts msg_type=7 with file_info."""
    path = tmp_path / "a.png"
    Image.new("RGB", (4, 4), (1, 1, 1)).save(path)
    tokens = MagicMock()
    tokens.auth_headers = AsyncMock(return_value={"Authorization": "Bearer t"})
    media = MagicMock()
    media.upload_image = AsyncMock(return_value="FILEINFO")
    client = ReplyClient(_settings(tmp_path), tokens, media=media)
    message = IncomingMessage(
        event_type=C2C_EVENT,
        event_id="e",
        msg_id="m1",
        content="x",
        user_openid="user-1",
        group_openid=None,
    )
    captured: dict = {}

    async def fake_post(_msg, payload):
        captured["payload"] = payload
        return True

    client._post = fake_post  # type: ignore[method-assign]
    assert asyncio.run(client.send_image(message, path, quote=False)) is True
    media.upload_image.assert_awaited_once()
    assert captured["payload"]["msg_type"] == 7
    assert captured["payload"]["media"]["file_info"] == "FILEINFO"
