#!/usr/bin/env bash
# Install the LaunchAgent that keeps the VS Code Hermes AI Agent model
# picker in sync with ~/.hermes/config.yaml.
#
# Why a LaunchAgent (not a per-repo cron):
#   - VS Code auto-updates the joaompfp.hermes-ai-agent extension and
#     wipes our `extension.js` patch each time. The agent re-applies it.
#   - macOS LaunchAgents in ~/Documents have hit "Operation not permitted"
#     under TCC; we copy the runtime script into
#     ~/Library/Application Support/Hermes/scripts/ to dodge that.
#
# Idempotent: rerun anytime to refresh the installed copy.
set -euo pipefail

REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME_DIR="$HOME/Library/Application Support/Hermes/scripts"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
LABEL="local.hermes.vscode-models-refresh"
PLIST_SRC="$REPO_DIR/packaging/launchagents/$LABEL.plist"
PLIST_DST="$LAUNCH_AGENT_DIR/$LABEL.plist"

mkdir -p "$RUNTIME_DIR" "$LAUNCH_AGENT_DIR"

# Copy runtime scripts to the TCC-friendly location.
cp "$REPO_DIR/scripts/refresh-vscode-models.sh"           "$RUNTIME_DIR/"
cp "$REPO_DIR/scripts/regenerate-vscode-models-cache.py"  "$RUNTIME_DIR/"
cp "$REPO_DIR/scripts/patch-vscode-hermes-extension.sh"   "$RUNTIME_DIR/"
chmod +x "$RUNTIME_DIR"/*.sh "$RUNTIME_DIR"/*.py

# Strip macOS quarantine attribute so launchd can exec without prompting.
xattr -d com.apple.quarantine "$RUNTIME_DIR"/* 2>/dev/null || true

# Rewrite plist ProgramArguments path (in case $HOME differs from /Users/corn).
python3 - "$PLIST_SRC" "$PLIST_DST" "$RUNTIME_DIR/refresh-vscode-models.sh" "$HOME" <<'PY'
import plistlib, sys
src, dst, runtime_script, home = sys.argv[1:5]
with open(src, "rb") as fh:
    data = plistlib.load(fh)
data["ProgramArguments"] = ["/bin/bash", runtime_script]
env = data.setdefault("EnvironmentVariables", {})
env["HOME"] = home
with open(dst, "wb") as fh:
    plistlib.dump(data, fh)
print(f"installed plist -> {dst}")
PY

# Reload the agent.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo
echo "Installed LaunchAgent: $LABEL"
echo "  runtime scripts: $RUNTIME_DIR"
echo "  plist:           $PLIST_DST"
echo "  log:             /tmp/hermes-vscode-models-refresh.log"
echo
echo "Verify with:"
echo "  launchctl print gui/\$(id -u)/$LABEL | grep -E 'state|exit'"
