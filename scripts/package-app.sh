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
echo "$APP_PATH"
