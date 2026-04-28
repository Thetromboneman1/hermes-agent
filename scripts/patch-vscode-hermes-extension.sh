#!/usr/bin/env bash
# Patch joaompfp.hermes-ai-agent VS Code extension so its model picker shows
# extra groups defined in ~/.hermes/models_dev_cache.json under a top-level
# "groups": [{ label, prefix, items: [{id,label}] }] array.
#
# WHY: v3.0.0 hardcodes the picker to render ONLY "Anthropic" + "OpenAI Codex"
# groups (built from baked-in arrays in extension.js). It ignores the ACP
# `available_models` payload entirely. This patch teaches it to also unshift
# any groups present in the cache file, so local providers (Docker llama.cpp,
# OmniRoute, Ollama Cloud, etc.) appear in the dropdown.
#
# Re-run after every extension auto-update.

set -euo pipefail

EXT_DIR=$(/bin/ls -d "$HOME/.vscode/extensions/joaompfp.hermes-ai-agent-"* 2>/dev/null | tail -n1 || true)
if [[ -z "$EXT_DIR" ]]; then
  echo "joaompfp.hermes-ai-agent extension not found under ~/.vscode/extensions" >&2
  exit 1
fi
TARGET="$EXT_DIR/dist/extension.js"
[[ -f "$TARGET" ]] || { echo "missing $TARGET" >&2; exit 1; }

# Idempotent: skip if already patched with v2 marker.
if grep -q "__hg_prepend_order_v2" "$TARGET"; then
  echo "Already patched: $TARGET"
  exit 0
fi

cp -n "$TARGET" "$TARGET.bak.original" || true

python3 - "$TARGET" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
src = p.read_text()
needle = 'return[m("Anthropic","anthropic",c,t),m("OpenAI Codex","openai-codex",p,n)]'
replacement = (
  'const __hg=[m("Anthropic","anthropic",c,t),m("OpenAI Codex","openai-codex",p,n)];'
  'try{if(Array.isArray(e?.groups)){'
  'const __hg_prepend_order_v2=[];'
  'for(const __g of e.groups){'
  'if(!__g||!Array.isArray(__g.items))continue;'
  '__hg_prepend_order_v2.push({group:String(__g.label||__g.prefix||"Local"),items:__g.items.map(__it=>('
  '{id:String(__it.id),label:String(__it.label||__it.name||__it.id),'
  'command:String(__it.command||((__g.prefix||"custom")+":"+__it.id))}))})}'
  'for(let __i=__hg_prepend_order_v2.length-1;__i>=0;__i--){__hg.unshift(__hg_prepend_order_v2[__i])}'
  '}}catch(__err){}'
  'return __hg'
)

if needle in src:
  out = src.replace(needle, replacement, 1)
elif "__hg.unshift" in src:
  # Upgrade previously patched v1 payload in place.
  start = src.find('const __hg=[m("Anthropic","anthropic",c,t),m("OpenAI Codex","openai-codex",p,n)];')
  end = src.find('return __hg', start)
  if start == -1 or end == -1:
    sys.exit("existing patch shape not recognized; extension layout changed")
  end += len('return __hg')
  out = src[:start] + replacement + src[end:]
else:
  sys.exit("anchor not found; extension layout changed")

p.write_text(out)
print("patched", p)
PY

echo "Done. Reload VS Code window (Cmd+Shift+P > 'Developer: Reload Window')."
