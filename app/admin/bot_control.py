"""Start/stop the bot process via existing shell scripts and pid files."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parent.parent.parent
_PID_FILE = _ROOT / "data" / "uvicorn.pid"
_START = _ROOT / "scripts" / "start-local.sh"
_STOP = _ROOT / "scripts" / "stop-local.sh"
_HEALTH_URL = "http://127.0.0.1:8080/health"


def project_root() -> Path:
    """Return the qq-chat-bot repository root."""
    return _ROOT


def _read_pid() -> int | None:
    """Read uvicorn pid from disk, or None if missing/invalid."""
    if not _PID_FILE.is_file():
        return None
    raw = _PID_FILE.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _pid_alive(pid: int) -> bool:
    """True when the process id still exists."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def bot_status() -> dict[str, object]:
    """Report whether the bot process is up and responding to /health."""
    pid = _read_pid()
    running = pid is not None and _pid_alive(pid)
    healthy = False
    if running:
        try:
            response = httpx.get(_HEALTH_URL, timeout=1.5)
            healthy = response.status_code == 200
        except httpx.HTTPError:
            healthy = False
    return {
        "running": running,
        "pid": pid,
        "healthy": healthy,
        "health_url": _HEALTH_URL,
    }


def start_bot() -> dict[str, object]:
    """Run scripts/start-local.sh and return the new status."""
    if not _START.is_file():
        raise FileNotFoundError(str(_START))
    subprocess.run(["/bin/bash", str(_START)], cwd=_ROOT, check=True)
    return bot_status()


def stop_bot() -> dict[str, object]:
    """Run scripts/stop-local.sh and return the new status."""
    if not _STOP.is_file():
        raise FileNotFoundError(str(_STOP))
    subprocess.run(["/bin/bash", str(_STOP)], cwd=_ROOT, check=True)
    return bot_status()
