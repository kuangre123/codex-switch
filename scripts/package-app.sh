#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/Applications"
APP_PATH="$APP_DIR/Codex Switch.app"

if ! command -v osacompile >/dev/null 2>&1; then
  echo "osacompile is required. This packager runs on macOS." >&2
  exit 1
fi

mkdir -p "$APP_DIR"
osacompile -o "$APP_PATH" "$ROOT_DIR/scripts/Codex Switch.applescript"
cp "$ROOT_DIR/assets/app-icon.icns" "$APP_PATH/Contents/Resources/applet.icns"
RESOURCE_DIR="$APP_PATH/Contents/Resources/codex-switch"
rm -rf "$RESOURCE_DIR"
mkdir -p "$RESOURCE_DIR"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude 'dist' \
  "$ROOT_DIR/" "$RESOURCE_DIR/"
echo "$APP_PATH"
