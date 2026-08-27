"""HTTP hardening for a bot that is only meant to be reached via ngrok."""

from __future__ import annotations

import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

# Public surface: webhook only. /health is for this Mac, not the internet.
_PUBLIC_POST = "/qq/webhook"
_LOCAL_GET = "/health"
_MAX_BODY = 512 * 1024
_WINDOW = 60.0
_MAX_HITS = 90


class HttpGuard(BaseHTTPMiddleware):
    """Limit exposed routes, body size, and burst rate on the webhook."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)
        self._hits: deque[float] = deque()

    async def dispatch(self, request: Request, call_next):
        """Reject unexpected paths, oversized bodies, and webhook floods."""
        path = request.url.path
        if path in {"/docs", "/redoc", "/openapi.json"}:
            return Response(status_code=404)
        if path == _LOCAL_GET and request.method == "GET":
            return await call_next(request)
        if path == _PUBLIC_POST and request.method == "GET":
            return await call_next(request)
        if path != _PUBLIC_POST or request.method != "POST":
            return Response(status_code=404)
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > _MAX_BODY:
                    return JSONResponse({"error": "payload too large"}, status_code=413)
            except ValueError:
                return Response(status_code=400)
        if not self._allow():
            return JSONResponse({"error": "rate limited"}, status_code=429)
        return await call_next(request)

    def _allow(self) -> bool:
        """True if the webhook is under the global per-minute cap."""
        now = time.monotonic()
        while self._hits and now - self._hits[0] > _WINDOW:
            self._hits.popleft()
        if len(self._hits) >= _MAX_HITS:
            return False
        self._hits.append(now)
        return True
