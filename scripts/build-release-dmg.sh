#!/usr/bin/env bash
set -euo pipefail

# Build a distributable .dmg containing the app plus an /Applications shortcut,
# so users install by dragging into Applications (a Finder move that clears the
# quarantine/translocation that otherwise runs the app from a temporary path).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
APP_PATH="$HOME/Applications/Codex Switch.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$ROOT_DIR/app/Info.plist" 2>/dev/null || echo "dev")"
DMG_PATH="$DIST_DIR/Codex-Switch-v${VERSION}.dmg"

# Rebuild the app so the DMG always ships the current code.
"$ROOT_DIR/scripts/package-app.sh" >/dev/null

mkdir -p "$DIST_DIR"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
ditto "$APP_PATH" "$STAGING/Codex Switch.app"
ln -s /Applications "$STAGING/Applications"

rm -f "$DMG_PATH"
hdiutil create \
  -volname "Codex Switch" \
  -srcfolder "$STAGING" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG_PATH" >/dev/null

echo "$DMG_PATH"
