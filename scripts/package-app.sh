#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$HOME/Applications"
APP_PATH="$APP_DIR/Codex Switch.app"
BUILD_DIR="$(mktemp -d)"
BUILD_APP="$BUILD_DIR/Codex Switch.app"
XCODE_DEVELOPER_DIR="${DEVELOPER_DIR:-}"

if [[ -z "$XCODE_DEVELOPER_DIR" && -d "$HOME/Downloads/Xcode-beta.app/Contents/Developer" ]]; then
  XCODE_DEVELOPER_DIR="$HOME/Downloads/Xcode-beta.app/Contents/Developer"
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
