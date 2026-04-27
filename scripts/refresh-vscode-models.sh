#!/usr/bin/env bash
# Refresh the VS Code Hermes AI Agent model picker:
#   1. Regenerate ~/.hermes/models_dev_cache.json from ~/.hermes/config.yaml
#      (with live model probes against reachable custom_providers).
#   2. Re-apply the extension.js patch (idempotent; needed after every
#      auto-update of joaompfp.hermes-ai-agent).
#
# Designed to be run by a LaunchAgent at login + hourly. Always exits 0
# unless something catastrophic happens, so launchd does not respawn it
# tightly on transient failures.
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

PY=$(command -v python3 || true)
if [[ -z "$PY" ]]; then
    echo "[refresh-vscode-models] python3 not on PATH; skipping" >&2
    exit 0
fi

# Prefer the hermes-agent venv if it exists (PyYAML guaranteed there).
for candidate in \
    "$SCRIPT_DIR/../.venv/bin/python" \
    "$SCRIPT_DIR/../venv/bin/python" \
    "$HOME/.hermes/hermes-agent/venv/bin/python"
do
    if [[ -x "$candidate" ]]; then
        PY="$candidate"
        break
    fi
done

echo "[refresh-vscode-models] $(date '+%F %T') using $PY"
"$PY" "$SCRIPT_DIR/regenerate-vscode-models-cache.py" || \
    echo "[refresh-vscode-models] cache regen failed (non-fatal)" >&2

bash "$SCRIPT_DIR/patch-vscode-hermes-extension.sh" || \
    echo "[refresh-vscode-models] extension patch failed (non-fatal)" >&2

exit 0
