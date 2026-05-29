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

_sanitize_candidate_python_env() {
    local candidate="$1"
    local candidate_dir lib_dir

    candidate_dir=$(cd -- "$(dirname -- "$candidate")" && pwd)
    lib_dir="$candidate_dir/../lib"
    [[ -d "$lib_dir" ]] || return 0

    # macOS occasionally marks .pth files hidden; clear the flag so Python site
    # processing remains stable for editable installs.
    if command -v chflags >/dev/null 2>&1; then
        find "$lib_dir" -name "*.pth" -print0 2>/dev/null \
            | xargs -0 -I{} /usr/bin/chflags nohidden {} 2>/dev/null || true
    fi

    # Self-heal stale editable install breadcrumbs left by older hermes-agent
    # versions (0.10.0) that reference a missing finder module.
    while IFS= read -r -d '' pth; do
        if grep -q "__editable___hermes_agent_0_10_0_finder" "$pth" 2>/dev/null; then
            rm -f "$pth"
        fi
    done < <(find "$lib_dir" -name "editable-hermes_agent.pth" -print0 2>/dev/null)
}

_python_candidate_healthy() {
    local candidate="$1"
    "$candidate" -c "import yaml" >/dev/null 2>&1
}

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
        _sanitize_candidate_python_env "$candidate"
        if _python_candidate_healthy "$candidate"; then
            PY="$candidate"
            break
        fi
    fi
done

echo "[refresh-vscode-models] $(date '+%F %T') using $PY"
"$PY" "$SCRIPT_DIR/regenerate-vscode-models-cache.py" || \
    echo "[refresh-vscode-models] cache regen failed (non-fatal)" >&2

bash "$SCRIPT_DIR/patch-vscode-hermes-extension.sh" || \
    echo "[refresh-vscode-models] extension patch failed (non-fatal)" >&2

exit 0
