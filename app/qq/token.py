"""Cache QQ OpenAPI access tokens (QQBot scheme)."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings

_log = logging.getLogger(__name__)


class TokenManager:
    """Fetch and refresh App access tokens before they expire."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token = ""
        self._expires_at = 0.0

    async def get_token(self) -> str:
        """Return a valid access_token, refreshing when within 60s of expiry."""
        if self._token and time.time() < self._expires_at:
            return self._token
        if not self._settings.qq_app_id or not self._settings.qq_app_secret:
            raise RuntimeError("QQ_APP_ID and QQ_APP_SECRET are required")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                self._settings.qq_token_url,
                json={
                    "appId": self._settings.qq_app_id,
                    "clientSecret": self._settings.qq_app_secret,
                },
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        token = str(data.get("access_token") or "")
        expires_in = int(data.get("expires_in") or 7200)
        if not token:
            raise RuntimeError(f"token response missing access_token: {data}")
        self._token = token
        self._expires_at = time.time() + max(expires_in - 60, 30)
        _log.info("refreshed QQ access token, expires_in=%s", expires_in)
        return self._token

    async def auth_headers(self) -> dict[str, str]:
        """Build Authorization headers for OpenAPI calls."""
        token = await self.get_token()
        return {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self._settings.qq_app_id,
        }
