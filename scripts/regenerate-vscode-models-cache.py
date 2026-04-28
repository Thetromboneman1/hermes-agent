#!/usr/bin/env python3
"""Regenerate ~/.hermes/models_dev_cache.json with `groups` for the local
joaompfp.hermes-ai-agent VS Code extension picker.

Sources (in priority order):
  1. ~/.hermes/config.yaml — model.default + custom_providers + fallback_providers
  2. Live model lists from each reachable custom_providers base_url (/v1/models),
     with a 2s timeout (skipped silently on failure).

The output JSON has shape::

    {"groups": [{"label": ..., "prefix": "custom",
                 "items": [{"id": ..., "label": ...}]}, ...]}

This is consumed by the patched extension.js (see
`scripts/patch-vscode-hermes-extension.sh`). Any group present here is
prepended to the picker, above the hardcoded Anthropic / OpenAI Codex
catalogs.

Designed to be safe to run unattended (LaunchAgent, hourly cron, etc.):
non-zero exits only on truly catastrophic failure (unreadable config),
never on network errors.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - bootstrap fallback
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    sys.exit(2)

HOME = Path(os.environ.get("HOME") or Path.home())
CONFIG_PATH = HOME / ".hermes" / "config.yaml"
CACHE_PATH = HOME / ".hermes" / "models_dev_cache.json"

# Pretty labels for known local Docker model IDs.
KNOWN_LABELS = {
    "ai/qwen3-coder-next": "Qwen3 Coder Next  ·  Docker llama.cpp",
    "ai/qwen3:8B-Q4_K_M": "Qwen3 8B  ·  Q4_K_M  ·  Docker llama.cpp",
    "ai/qwen3-embedding": "Qwen3 Embedding  ·  Docker llama.cpp",
}

# Provider names (custom_providers[].name) whose endpoint is the Docker
# model-runner. We always advertise every entry from KNOWN_LABELS for these
# providers, because Docker's /v1/models only returns models that are
# currently loaded — not everything available in `docker model ls`.
DOCKER_RUNNER_PROVIDERS = {"local-docker"}

# Friendly group names per provider key.
GROUP_LABELS = {
    "local-docker": "Local Docker LLM",
    "omniroute-local": "OmniRoute (local)",
    "omniroute-docker": "OmniRoute (Docker host)",
    "ollama-cloud": "Ollama Cloud",
}

GROUP_PRIORITY = {
    "local-docker": 0,
    "omniroute-local": 1,
    "omniroute-docker": 2,
    "ollama-cloud": 3,
}


def _fetch_models(base_url: str, timeout: float = 2.0) -> list[str]:
    if not base_url:
        return []
    url = urljoin(base_url.rstrip("/") + "/", "models")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - local URLs only
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in (data or {}).get("data") or []:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str):
            continue
        if model_id in seen:
            continue
        seen.add(model_id)
        out.append(model_id)
    return out


def _load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        try:
            return yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            sys.stderr.write(f"Failed to parse {CONFIG_PATH}: {exc}\n")
            sys.exit(2)


def _label_for(model_id: str) -> str:
    return KNOWN_LABELS.get(model_id, model_id)


def _build_groups(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    model_cfg = cfg.get("model") or {}
    default_model = (
        model_cfg.get("default") if isinstance(model_cfg, dict) else None
    ) or ""
    default_base_url = (
        model_cfg.get("base_url") if isinstance(model_cfg, dict) else None
    ) or ""

    groups: list[dict[str, Any]] = []

    # Per custom provider: live-probe; fall back to per-entry model;
    # final fall back to model.default if base_url matches.
    for entry in cfg.get("custom_providers") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        base_url = str(entry.get("base_url") or "").strip()
        if not name or not base_url:
            continue
        # Skip pure-cloud providers — Ollama Cloud direct is handled via
        # explicit per-entry model since it requires an API key.
        live = _fetch_models(base_url) if base_url.startswith("http://") else []
        items: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        def _add(mid: str) -> None:
            mid = (mid or "").strip()
            if not mid or mid in seen_ids:
                return
            seen_ids.add(mid)
            items.append({"id": mid, "label": _label_for(mid)})

        # For Docker model-runner endpoints, always seed with every known
        # local model ID so the picker doesn't shrink when models unload.
        if name in DOCKER_RUNNER_PROVIDERS:
            for mid in KNOWN_LABELS:
                _add(mid)

        # Live-probed IDs come next.
        for mid in live:
            # Drop the no-slash duplicates OmniRoute emits
            # ("ollamacloud/foo" alongside "ollama-cloud/foo").
            if "/" in mid:
                _add(mid)
            elif name in DOCKER_RUNNER_PROVIDERS:
                _add(mid)
        for mid in live:
            # Second pass: include hyphenated form preferentially, then any leftovers.
            _add(mid)

        if not items:
            entry_model = entry.get("model")
            if isinstance(entry_model, str) and entry_model.strip():
                _add(entry_model.strip())
            elif default_model and base_url == default_base_url:
                _add(default_model)

        # Drop the duplicate no-hyphen variants if a hyphenated equivalent is present.
        deduped: list[dict[str, str]] = []
        present = {it["id"] for it in items}
        for it in items:
            mid = it["id"]
            if "/" in mid:
                head, _, tail = mid.partition("/")
                # "ollamacloud/x" is a dup of "ollama-cloud/x" — keep only the hyphenated one.
                if head and "-" not in head:
                    hyphenated = head[:-len("cloud")] + "-cloud/" + tail if head.endswith("cloud") else None
                    if hyphenated and hyphenated in present:
                        continue
            deduped.append(it)
        items = deduped
        seen_ids = {it["id"] for it in items}
        if not items:
            continue
        groups.append(
            {
                "label": GROUP_LABELS.get(name, name),
                "prefix": "custom",
                "items": items,
                "_provider_name": name,
            }
        )

    # Ensure model.default appears at least once (top of first group).
    if default_model:
        already_present = any(
            any(item.get("id") == default_model for item in g.get("items") or [])
            for g in groups
        )
        if not already_present:
            groups.insert(
                0,
                {
                    "label": "Default",
                    "prefix": "custom",
                    "items": [{"id": default_model, "label": _label_for(default_model)}],
                },
            )

    # Stable ordering: keep Local Docker group on top, then OmniRoute groups,
    # then everything else.
    groups.sort(
        key=lambda g: (
            GROUP_PRIORITY.get(str(g.get("_provider_name") or ""), 999),
            str(g.get("label") or "").lower(),
        )
    )
    for g in groups:
        g.pop("_provider_name", None)
    return groups


def main() -> int:
    cfg = _load_config()
    groups = _build_groups(cfg)
    payload = {"groups": groups}
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    total = sum(len(g.get("items") or []) for g in groups)
    print(f"Wrote {CACHE_PATH} — {len(groups)} group(s), {total} model(s)")
    for g in groups:
        ids = ", ".join(item["id"] for item in g["items"])
        print(f"  · {g['label']}: {ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
