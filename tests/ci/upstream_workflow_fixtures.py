"""Explicit, integrity-checked upstream reference workflows, never live fallbacks."""

import hashlib
import json
import re
from pathlib import Path


ARCHIVE = Path(__file__).parent / "fixtures" / "upstream-workflows"


def reference_workflow_path(name: str, *, archive: Path = ARCHIVE) -> Path:
    """Select an archived upstream contract independently of deployed fork files."""
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source_repository"] == "https://github.com/NousResearch/hermes-agent"
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]), "unresolved upstream provenance"
    assert Path(name).name == name and name in manifest["files"], f"unarchived reference workflow: {name}"
    provenance = manifest["files"][name]
    assert provenance["source_path"] == f".github/workflows/{name}"
    path = archive / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == provenance["sha256"], f"upstream workflow fixture digest mismatch: {name}"
    return path
