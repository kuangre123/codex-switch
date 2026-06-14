#!/usr/bin/env bash
set -euo pipefail

rm -f "$HOME/.local/bin/codex-switch"
rm -rf "$HOME/.local/share/codex-switch"
rm -rf "$HOME/Applications/Codex Switch.app"

echo "Removed Codex Switch. User Codex settings under ~/.codex were not deleted."
