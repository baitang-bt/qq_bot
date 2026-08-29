#!/usr/bin/env bash
# 启动原生桌面管理窗口（PyQt6，不打开浏览器）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/admin-launcher.sh"
