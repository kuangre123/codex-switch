#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/Applications"
APP_PATH="$APP_DIR/Codex Switch.app"
BUILD_DIR="$(mktemp -d)"
BUILD_APP="$BUILD_DIR/Codex Switch.app"
XCODE_DEVELOPER_DIR="${DEVELOPER_DIR:-}"

# A full Xcode (not just Command Line Tools) is required to build SwiftUI code,
# because CLT lacks the SwiftUI macro plugins. Search common install locations.
if [[ -z "$XCODE_DEVELOPER_DIR" ]]; then
  for candidate in \
    "$HOME/Desktop/Xcode-beta.app" \
    "$HOME/Downloads/Xcode-beta.app" \
    "/Applications/Xcode.app" \
    "/Applications/Xcode-beta.app" \
    "$HOME/Applications/Xcode.app"; do
    if [[ -d "$candidate/Contents/Developer" ]]; then
      XCODE_DEVELOPER_DIR="$candidate/Contents/Developer"
      break
    fi
  done
fi

# Fall back to the active developer dir if it is a full Xcode.
if [[ -z "$XCODE_DEVELOPER_DIR" ]]; then
  active="$(xcode-select -p 2>/dev/null || true)"
  if [[ "$active" == *"/Xcode"*".app/"* ]]; then
    XCODE_DEVELOPER_DIR="$active"
  fi
fi

if [[ -n "$XCODE_DEVELOPER_DIR" ]]; then
  SWIFTC="$XCODE_DEVELOPER_DIR/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc"
  SDK_PATH="$XCODE_DEVELOPER_DIR/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk"
else
  SWIFTC="$(command -v swiftc || true)"
  SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
fi

if [[ -z "$SWIFTC" || ! -x "$SWIFTC" ]]; then
  echo "swiftc is required. This packager runs on macOS." >&2
  exit 1
fi

trap 'rm -rf "$BUILD_DIR"' EXIT
mkdir -p "$BUILD_APP/Contents/MacOS" "$BUILD_APP/Contents/Resources" "$APP_DIR"
cp "$ROOT_DIR/app/Info.plist" "$BUILD_APP/Contents/Info.plist"
cp "$ROOT_DIR/assets/app-icon.icns" "$BUILD_APP/Contents/Resources/app-icon.icns"
cp "$ROOT_DIR/src/codex_switch/cli.py" "$BUILD_APP/Contents/Resources/codex-switch"
chmod 755 "$BUILD_APP/Contents/Resources/codex-switch"
CLANG_MODULE_CACHE_PATH="$BUILD_DIR/clang-cache" \
SWIFT_MODULECACHE_PATH="$BUILD_DIR/swift-cache" \
"$SWIFTC" \
  -parse-as-library \
  -O \
  -target "$(uname -m)-apple-macos13.0" \
  -sdk "$SDK_PATH" \
  -framework SwiftUI \
  -framework AppKit \
  "$ROOT_DIR/app/CodexSwitchApp.swift" \
  -o "$BUILD_APP/Contents/MacOS/Codex Switch"
codesign --force --deep --sign - "$BUILD_APP" >/dev/null
rm -rf "$APP_PATH"
ditto "$BUILD_APP" "$APP_PATH"
echo "$APP_PATH"
