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

# Notarize + staple so the DMG opens without a Gatekeeper warning, using the
# App Store Connect API key. Skipped automatically if creds are unavailable.
ASC_CFG="$HOME/.config/appstore-connect/default.json"
if [[ -f "$ASC_CFG" ]] && security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID Application"; then
  KEY_ID="$(python3 -c "import json;print(json.load(open('$ASC_CFG'))['key_id'])")"
  ISSUER="$(python3 -c "import json;print(json.load(open('$ASC_CFG'))['issuer_id'])")"
  KEYFILE=""
  for c in "$HOME/AuthKey_${KEY_ID}.p8" "$(python3 -c "import json;print(json.load(open('$ASC_CFG')).get('key_filepath',''))")"; do
    [[ -n "$c" && -f "$c" ]] && KEYFILE="$c" && break
  done
  if [[ -n "$KEYFILE" ]]; then
    echo "Notarizing $DMG_PATH …" >&2
    xcrun notarytool submit "$DMG_PATH" --key "$KEYFILE" --key-id "$KEY_ID" --issuer "$ISSUER" --wait >&2
    xcrun stapler staple "$DMG_PATH" >&2
  else
    echo "Notary key (.p8) not found; skipping notarization." >&2
  fi
else
  echo "No Developer ID identity / ASC config; skipping notarization." >&2
fi

echo "$DMG_PATH"
