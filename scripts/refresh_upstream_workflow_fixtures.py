#!/usr/bin/env python3
"""Refresh explicitly archived upstream workflow contracts, never deployed workflows."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

FIXTURES = Path("tests/ci/fixtures/upstream-workflows")
SOURCE_REPOSITORY = "https://github.com/NousResearch/hermes-agent"


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True,
    ).stdout


def refresh(root: Path, ref: str) -> None:
    directory = root / FIXTURES
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("source_repository") != SOURCE_REPOSITORY:
        raise ValueError("Unexpected workflow fixture source repository")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Workflow fixture manifest must enumerate its files")
    revision = git(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    replacements = {}
    for name, entry in files.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", name):
            raise ValueError(f"Invalid workflow fixture filename: {name!r}")
        source = f".github/workflows/{name}"
        if not isinstance(entry, dict) or entry.get("source_path") != source:
            raise ValueError(f"Invalid upstream workflow source: {name!r}")
        if (directory / name).is_symlink():
            raise ValueError(f"Workflow fixture must not be a symlink: {name!r}")
        # Read every source before changing any fixture: a renamed/removed
        # upstream workflow requires review, not a partial or stale snapshot.
        contents = git(root, "show", f"{revision}:{source}")
        replacements[name] = contents
        entry["sha256"] = hashlib.sha256(contents).hexdigest()
    manifest["source_commit"] = revision
    for name, contents in replacements.items():
        (directory / name).write_bytes(contents)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: refresh_upstream_workflow_fixtures.py <upstream-ref>", file=sys.stderr)
        return 2
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())
    refresh(root, sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
