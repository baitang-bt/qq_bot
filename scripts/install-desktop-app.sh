#!/usr/bin/env bash
# 在桌面安装「QQ机器人管理.app」（AppleScript 小程序 → 真 Mach-O，Gatekeeper 比 shell 启动器稳）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="QQ机器人管理"
DESKTOP="${DESKTOP:-$HOME/Desktop}"
APP_DIR="$DESKTOP/${APP_NAME}.app"
CMD_FILE="$DESKTOP/${APP_NAME}.command"
ICON_SRC="$ROOT/assets/app-icon.png"
APPLESCRIPT_SRC="$ROOT/scripts/desktop-app.applescript"

if [[ ! -f "$ICON_SRC" ]]; then
  echo "缺少图标: $ICON_SRC"
  exit 1
fi

if [[ ! -f "$APPLESCRIPT_SRC" ]]; then
  echo "缺少 AppleScript: $APPLESCRIPT_SRC"
  exit 1
fi

if ! command -v osacompile >/dev/null 2>&1; then
  echo "需要 osacompile（macOS 自带，请确认未删除 /usr/bin/osacompile）"
  exit 1
fi

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "缺少 .venv，请先在项目目录安装依赖。"
  exit 1
fi

if ! "$ROOT/.venv/bin/python" -c "import PyQt6" 2>/dev/null; then
  echo "缺少 PyQt6，请执行: .venv/bin/pip install -r requirements.txt"
  exit 1
fi

build_icns() {
  local src="$1"
  local out="$2"
  local iconset
  iconset="$(mktemp -d)/AppIcon.iconset"
  mkdir -p "$iconset"
  local sizes=(16 32 64 128 256 512)
  for size in "${sizes[@]}"; do
    sips -z "$size" "$size" "$src" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$src" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$iconset" -o "$out"
}

plist_set() {
  local plist="$1"
  local key="$2"
  local value="$3"
  if /usr/libexec/PlistBuddy -c "Print :$key" "$plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c "Set :$key $value" "$plist"
  else
    /usr/libexec/PlistBuddy -c "Add :$key string $value" "$plist"
  fi
}

sign_app_bundle() {
  local app="$1"
  local exe="$app/Contents/MacOS/applet"
  if ! command -v codesign >/dev/null 2>&1; then
    return 0
  fi
  codesign --force --sign - --timestamp=none "$exe"
  codesign --force --sign - --timestamp=none "$app"
}

register_app() {
  local app="$1"
  local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  if [[ -x "$lsregister" ]]; then
    "$lsregister" -f "$app" >/dev/null 2>&1 || true
  fi
}

rm -rf "$APP_DIR"
osacompile -o "$APP_DIR" "$APPLESCRIPT_SRC"
printf '%s\n' "$ROOT" >"$APP_DIR/Contents/Resources/project-root.txt"
build_icns "$ICON_SRC" "$APP_DIR/Contents/Resources/AppIcon.icns"

PLIST="$APP_DIR/Contents/Info.plist"
plist_set "$PLIST" CFBundleIconFile AppIcon
plist_set "$PLIST" CFBundleIdentifier com.potatoblock.qq-chat-bot-admin
plist_set "$PLIST" CFBundleName "$APP_NAME"
plist_set "$PLIST" CFBundleDisplayName "$APP_NAME"
plist_set "$PLIST" CFBundleShortVersionString 1.5
plist_set "$PLIST" CFBundleVersion 6
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSHighResolutionCapable true" "$PLIST"

xattr -cr "$APP_DIR" 2>/dev/null || true
sign_app_bundle "$APP_DIR"
register_app "$APP_DIR"
touch "$APP_DIR"

cat >"$CMD_FILE" <<CMD
#!/usr/bin/env bash
cd "$ROOT"
exec "$ROOT/scripts/admin-launcher.sh"
CMD
chmod +x "$CMD_FILE"
xattr -cr "$CMD_FILE" 2>/dev/null || true

echo "已安装:"
echo "  App: $APP_DIR  （AppleScript 小程序，双击即可）"
echo "  备用: $CMD_FILE"
echo ""
echo "若 App 仍无法打开，可继续用 .command；或重新执行本脚本。"
