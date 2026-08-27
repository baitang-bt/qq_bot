"""ASGI entry: FastAPI app wiring QQ webhook, LLM, memory, and vision."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot import ChatBot
from app.commands import CommandRouter
from app.config import load_settings
from app.owner import OwnerGate
from app.http_guard import HttpGuard
from app.impression.store import ImpressionStore
from app.impression.writer import ImpressionWriter
from app.llm.client import LLMClient
from app.memory.store import MemoryStore
from app.qq.dedupe import MessageDedupe
from app.qq.gateway import QQGateway
from app.qq.reply import ReplyClient
from app.qq.token import TokenManager
from app.qq.webhook import create_webhook_router
from app.reply_policy import ReplyGate
from app.vision.cache import ImageCache
from app.vision.identify import ImageIdentifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app() -> FastAPI:
    """Construct the FastAPI application with all bot dependencies."""
    settings = load_settings()
    tokens = TokenManager(settings)
    replies = ReplyClient(settings, tokens)
    llm = LLMClient(settings)
    db_path = settings.data_dir / "bot.sqlite3"
    memory = MemoryStore(db_path, max_turns=settings.memory_max_turns)
    vision = ImageIdentifier(settings, ImageCache(db_path))
    gate = ReplyGate(settings.reply_policy_path)
    impressions = ImpressionStore(settings.data_dir / "impressions")
    writer = ImpressionWriter(llm, impressions)
    commands = CommandRouter(OwnerGate(settings.qq_id, settings.data_dir), impressions)
    bot = ChatBot(
        replies=replies,
        llm=llm,
        memory=memory,
        vision=vision,
        gate=gate,
        impressions=impressions,
        impression_writer=writer,
        commands=commands,
    )
    dedupe = MessageDedupe()
    gateway = QQGateway(settings, tokens, bot, dedupe)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start the outbound QQ gateway for the process lifetime."""
        task = asyncio.create_task(gateway.run_forever())
        yield
        gateway.stop()
        task.cancel()

    app = FastAPI(
        title="qq-chat-bot",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(HttpGuard)
    app.include_router(create_webhook_router(settings, bot, dedupe))

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe for tunnels and process managers."""
        return {"status": "ok"}

    return app


app = create_app()
