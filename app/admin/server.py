"""Standalone local admin server (127.0.0.1 only)."""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from app.admin.router import create_router


def create_app() -> FastAPI:
    """Build the admin-only FastAPI app."""
    app = FastAPI(title="qq-chat-bot-admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_router())
    return app


def main() -> None:
    """Run the admin dashboard on localhost."""
    port = int(os.getenv("ADMIN_PORT", "8765"))
    uvicorn.run(
        "app.admin.server:create_app",
        factory=True,
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
