"""Local admin API for bot control, persona, policy, and impressions."""

from __future__ import annotations

import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.admin import bot_control, store
from pathlib import Path

_STATIC = Path(__file__).resolve().parent / "static"


class PromptBody(BaseModel):
    """Editable fields for the legacy prompt JSON shape."""

    persona: str = ""
    anti_injection: list[str] = Field(default_factory=list)
    stay_on_prompt: list[str] = Field(default_factory=list)


class PersonaFileBody(BaseModel):
    """One txt file inside a persona pack."""

    text: str = ""


class PersonaActiveBody(BaseModel):
    """Enable one persona pack."""

    id: str


class PersonaNewBody(BaseModel):
    """Create a persona pack."""

    id: str
    title: str = ""


class PersonaNewFileBody(BaseModel):
    """Create an empty txt in a pack."""

    name: str


class ReplyPolicyBody(BaseModel):
    """Raw reply_policy.toml text."""

    text: str


class ImpressionBody(BaseModel):
    """One user impression record."""

    username: str = ""
    qq: str = ""
    impression: str = ""


class CommandAdminBody(BaseModel):
    """One slash-command admin row."""

    qq: str
    username: str = ""
    user_openid: str = ""


class CommandAdminsBody(BaseModel):
    """Full admin list replacement."""

    admins: list[CommandAdminBody] = Field(default_factory=list)


def create_router() -> APIRouter:
    """Build the /admin API router (optional HTTP fallback)."""
    router = APIRouter()

    @router.get("/admin")
    def admin_page() -> FileResponse:
        """Serve the legacy web dashboard."""
        path = _STATIC / "index.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="admin ui missing")
        return FileResponse(path, media_type="text/html; charset=utf-8")

    @router.get("/admin/icon.png")
    def admin_icon() -> FileResponse:
        """Serve the app icon."""
        path = bot_control.project_root() / "assets" / "app-icon.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="icon missing")
        return FileResponse(path, media_type="image/png")

    @router.get("/admin/api/status")
    def status() -> dict[str, object]:
        """Bot process and health snapshot."""
        snap = store.bot_snapshot()
        return {
            "running": snap.running,
            "healthy": snap.healthy,
            "pid": snap.pid,
            "project": snap.project,
            "llm_model": snap.llm_model,
            "qq_configured": snap.qq_configured,
            "llm_configured": snap.llm_configured,
            "health_url": "http://127.0.0.1:8080/health",
        }

    @router.post("/admin/api/bot/start")
    def bot_start() -> dict[str, object]:
        """Start uvicorn via start-local.sh."""
        try:
            snap = store.start_bot()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"running": snap.running, "healthy": snap.healthy, "pid": snap.pid}

    @router.post("/admin/api/bot/stop")
    def bot_stop() -> dict[str, object]:
        """Stop uvicorn via stop-local.sh."""
        try:
            snap = store.stop_bot()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"running": snap.running, "healthy": snap.healthy, "pid": snap.pid}

    @router.get("/admin/api/personas")
    def get_personas() -> dict[str, object]:
        """List persona packs and the active id."""
        try:
            packs = store.list_persona_packs()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"active": store.persona_active_id(), "packs": packs}

    @router.put("/admin/api/personas/active")
    def put_persona_active(body: PersonaActiveBody) -> dict[str, str]:
        """Enable one persona pack."""
        try:
            active = store.set_persona_active(body.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": "saved", "active": active}

    @router.post("/admin/api/personas")
    def post_persona(body: PersonaNewBody) -> dict[str, object]:
        """Create a persona pack."""
        try:
            row = store.new_persona_pack(body.id, body.title)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": "created", "pack": row}

    @router.delete("/admin/api/personas/{persona_id}")
    def delete_persona(persona_id: str) -> dict[str, str]:
        """Delete a non-active persona pack."""
        try:
            store.delete_persona_pack(persona_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": "deleted"}

    @router.get("/admin/api/personas/{persona_id}/files/{filename}")
    def get_persona_file(persona_id: str, filename: str) -> dict[str, str]:
        """Read one txt from a pack."""
        return {
            "id": persona_id,
            "name": filename,
            "text": store.read_persona_file(persona_id, filename),
        }

    @router.put("/admin/api/personas/{persona_id}/files/{filename}")
    def put_persona_file(
        persona_id: str, filename: str, body: PersonaFileBody
    ) -> dict[str, str]:
        """Write one txt in a pack."""
        try:
            path = store.save_persona_file(persona_id, filename, body.text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": "saved", "path": str(path)}

    @router.post("/admin/api/personas/{persona_id}/files")
    def post_persona_file(persona_id: str, body: PersonaNewFileBody) -> dict[str, str]:
        """Create an empty txt in a pack."""
        try:
            path = store.add_persona_file(persona_id, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": "created", "path": str(path)}

    @router.get("/admin/api/prompt")
    def get_prompt() -> dict[str, object]:
        """Read reserved txts of the active persona pack."""
        try:
            data = store.read_prompt()
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "persona": data.persona,
            "anti_injection": data.anti_injection,
            "stay_on_prompt": data.stay_on_prompt,
            "path": data.path,
        }

    @router.put("/admin/api/prompt")
    def put_prompt(body: PromptBody) -> dict[str, str]:
        """Write reserved txts of the active persona pack."""
        path = store.save_prompt(body.persona, body.anti_injection, body.stay_on_prompt)
        return {"ok": "saved", "path": str(path)}

    @router.get("/admin/api/reply-policy")
    def get_reply_policy() -> dict[str, str]:
        """Read reply_policy.toml as text."""
        text, path = store.read_reply_policy()
        return {"text": text, "path": str(path)}

    @router.put("/admin/api/reply-policy")
    def put_reply_policy(body: ReplyPolicyBody) -> dict[str, str]:
        """Write reply_policy.toml."""
        path = store.save_reply_policy(body.text)
        return {"ok": "saved", "path": str(path)}

    @router.get("/admin/api/impressions")
    def list_impressions() -> list[dict[str, Any]]:
        """List all stored user impressions."""
        return store.list_impressions()

    @router.get("/admin/api/impressions/{user_openid}")
    def get_impression(user_openid: str) -> dict[str, Any]:
        """Load one impression by openid."""
        return store.load_impression(user_openid)

    @router.put("/admin/api/impressions/{user_openid}")
    def put_impression(user_openid: str, body: ImpressionBody) -> dict[str, str]:
        """Update username, qq, and impression text for one user."""
        path = store.save_impression(user_openid, body.username, body.qq, body.impression)
        return {"ok": "saved", "path": str(path)}

    @router.get("/admin/api/command-admins")
    def list_command_admins() -> list[dict[str, str]]:
        """List users allowed to run slash commands."""
        return store.list_command_admins()

    @router.put("/admin/api/command-admins")
    def put_command_admins(body: CommandAdminsBody) -> dict[str, str]:
        """Replace the slash-command admin list."""
        rows = [item.model_dump() for item in body.admins]
        path = store.save_command_admins(rows)
        return {"ok": "saved", "path": str(path)}

    @router.delete("/admin/api/logs")
    def clear_logs() -> dict[str, str]:
        """Truncate uvicorn.log."""
        path = store.clear_monitor_logs()
        return {"ok": "cleared", "path": str(path)}

    @router.get("/admin/api/logs")
    def tail_logs(lines: int = 80) -> dict[str, str]:
        """Return the last lines of uvicorn.log."""
        return {"text": store.tail_logs(lines), "path": str(store.uvicorn_log_path())}

    return router
