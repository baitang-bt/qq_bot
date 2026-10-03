"""Chunked local-file upload to QQ OpenAPI v2 (rich media)."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.qq.events import IncomingMessage
from app.qq.token import TokenManager

_log = logging.getLogger(__name__)

_MD5_10M_BYTES = 10_002_432
_FILE_TYPE_IMAGE = 1


def file_checksums(data: bytes) -> tuple[str, str, str]:
    """Return (md5, sha1, md5_10m) hex digests for upload_prepare."""
    md5 = hashlib.md5(data).hexdigest()
    sha1 = hashlib.sha1(data).hexdigest()
    md5_10m = hashlib.md5(data[:_MD5_10M_BYTES]).hexdigest()
    return md5, sha1, md5_10m


class MediaUploader:
    """Upload a local PNG/JPG via prepare → PUT parts → part_finish → files merge."""

    def __init__(self, settings: Settings, tokens: TokenManager) -> None:
        self._settings = settings
        self._tokens = tokens
        self._cache: dict[str, tuple[str, float]] = {}

    async def upload_image(self, message: IncomingMessage, path: Path) -> str:
        """Upload a local image for this chat scene; return file_info for msg_type=7."""
        data = path.read_bytes()
        if not data:
            raise RuntimeError(f"empty sticker file: {path}")
        md5, sha1, md5_10m = file_checksums(data)
        scene = _scene_key(message)
        cache_key = f"{scene}:{md5}"
        cached = self._cache.get(cache_key)
        now = time.time()
        if cached and cached[1] > now:
            _log.info("media file_info cache hit scene=%s md5=%s", scene, md5[:8])
            return cached[0]

        headers = await self._tokens.auth_headers()
        base = self._settings.qq_api_base
        target = _target_id(message)
        prefix = "groups" if message.is_group else "users"

        prepare_url = f"{base}/v2/{prefix}/{target}/upload_prepare"
        prepare_body = {
            "file_type": _FILE_TYPE_IMAGE,
            "file_size": str(len(data)),
            "file_name": path.name,
            "md5": md5,
            "sha1": sha1,
            "md5_10m": md5_10m,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            prepare_resp = await client.post(
                prepare_url, headers=headers, json=prepare_body
            )
            if prepare_resp.status_code >= 400:
                raise RuntimeError(
                    f"upload_prepare failed status={prepare_resp.status_code} "
                    f"body={prepare_resp.text[:300]}"
                )
            prepare = prepare_resp.json()
            if not isinstance(prepare, dict):
                raise RuntimeError("upload_prepare response is not an object")
            upload_id = str(prepare.get("upload_id") or "")
            if not upload_id:
                raise RuntimeError("upload_prepare missing upload_id")
            parts = prepare.get("parts") or []
            if not isinstance(parts, list) or not parts:
                # Some deployments may omit parts for tiny files; treat whole file as one.
                block = int(str(prepare.get("block_size") or len(data)))
                parts = [
                    {
                        "index": 0,
                        "presigned_url": "",
                        "block_size": str(block),
                    }
                ]
            offset = 0
            for part in parts:
                if not isinstance(part, dict):
                    continue
                index = int(part.get("index") or 0)
                block_size = int(str(part.get("block_size") or "0") or 0)
                if block_size <= 0:
                    block_size = max(1, len(data) - offset)
                chunk = data[offset : offset + block_size]
                offset += len(chunk)
                presigned = str(part.get("presigned_url") or "")
                if not presigned:
                    raise RuntimeError(f"upload part {index} missing presigned_url")
                put_resp = await client.put(
                    presigned,
                    content=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                )
                if put_resp.status_code >= 400:
                    raise RuntimeError(
                        f"part PUT failed index={index} status={put_resp.status_code}"
                    )
                part_md5 = hashlib.md5(chunk).hexdigest()
                finish_url = f"{base}/v2/{prefix}/{target}/upload_part_finish"
                finish_body = {
                    "upload_id": upload_id,
                    "part_index": index,
                    "block_size": str(len(chunk)),
                    "md5": part_md5,
                }
                finish_resp = await client.post(
                    finish_url, headers=headers, json=finish_body
                )
                if finish_resp.status_code >= 400:
                    raise RuntimeError(
                        f"upload_part_finish failed index={index} "
                        f"status={finish_resp.status_code} body={finish_resp.text[:200]}"
                    )

            files_url = f"{base}/v2/{prefix}/{target}/files"
            files_body = {
                "file_type": _FILE_TYPE_IMAGE,
                "upload_id": upload_id,
                "srv_send_msg": False,
                "file_name": path.name,
            }
            files_resp = await client.post(files_url, headers=headers, json=files_body)
            if files_resp.status_code >= 400:
                raise RuntimeError(
                    f"files merge failed status={files_resp.status_code} "
                    f"body={files_resp.text[:300]}"
                )
            payload = files_resp.json()
            if not isinstance(payload, dict):
                raise RuntimeError("files response is not an object")
            file_info = str(payload.get("file_info") or "")
            if not file_info:
                raise RuntimeError("files response missing file_info")
            ttl = int(payload.get("ttl") or 0)
            expire_at = now + (ttl if ttl > 0 else 3600) - 30
            self._cache[cache_key] = (file_info, expire_at)
            _log.info(
                "media uploaded scene=%s file=%s md5=%s ttl=%s",
                scene,
                path.name,
                md5[:8],
                ttl,
            )
            return file_info


def _scene_key(message: IncomingMessage) -> str:
    """Cache key prefix: group and c2c uploads are not interchangeable."""
    if message.is_group:
        return f"group:{message.group_openid}"
    return f"c2c:{message.user_openid}"


def _target_id(message: IncomingMessage) -> str:
    """OpenID used in upload URL path segments."""
    if message.is_group:
        if not message.group_openid:
            raise RuntimeError("group message missing group_openid")
        return message.group_openid
    return message.user_openid
