#!/usr/bin/env python3
"""Merge a validated main SHA into a clean custom branch without rewriting history.

Only the marked README section and the wholly generated repository-state file
may be regenerated. All other conflicts require review. Failed merges or local
validation leave the original branch and tracked tree intact.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import documentation_health as documentation

GENERATED = {"README.md", "docs/REPOSITORY_STATE.md"}
STATE_NOTICE = "This file is generated from the tracked repository tree."


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    raw = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=300, check=False)
    # text=True would normalize CRLF inside Git blobs before preservation checks.
    result = subprocess.CompletedProcess(raw.args, raw.returncode,
                                         raw.stdout.decode("utf-8"), raw.stderr.decode("utf-8"))
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def readme_parts(text: str) -> tuple[str, str, str]:
    """Recognize exactly one full-line generated block, or no block at all."""
    if "\r" in text:
        raise RuntimeError("README.md contains unsupported CR/CRLF line endings; manual review required")
    start, end = documentation.MARKER_START, documentation.MARKER_END
    if start not in text and end not in text:
        return text, "", ""
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("README.md has ambiguous generated-section markers")
    match = re.search(r"(?m)^" + re.escape(start) + r"\n.*?^" + re.escape(end) + r"(?=\n|$)", text, re.DOTALL)
    if match is None:
        raise RuntimeError("README.md has malformed generated-section markers")
    return text[:match.start()], match.group(), text[match.end():]


def normalized_readme(text: str) -> str:
    before, block, after = readme_parts(text)
    placeholder = documentation.MARKER_START + "\n" + documentation.MARKER_END
    return before + (placeholder if block else "") + after


def conflict_versions(root: Path, path: str) -> dict[int, str]:
    versions = {}
    for entry in git(root, "ls-files", "--unmerged", "-z", "--", path).stdout.split("\0"):
        if not entry:
            continue
        metadata, _ = entry.split("\t", 1)
        mode, blob, stage = metadata.split()
        if mode != "100644":
            raise RuntimeError(f"Non-regular generated-document conflict requires review: {path}")
        versions[int(stage)] = git(root, "cat-file", "blob", blob).stdout
    if 2 not in versions or 3 not in versions:
        raise RuntimeError(f"Deleted generated-document conflict requires review: {path}")
    return versions


def resolve_readme(root: Path, versions: dict[int, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="hermes-readme-merge-") as temporary:
        paths = [Path(temporary) / name for name in ("ours", "base", "theirs")]
        for path, stage in zip(paths, (2, 1, 3)):
            path.write_text(normalized_readme(versions.get(stage, "")), encoding="utf-8")
        result = git(root, "merge-file", "-p", *map(str, paths), check=False)
    if result.returncode:
        raise RuntimeError("Human README.md content conflicts; manual review required")
    (root / "README.md").write_text(result.stdout, encoding="utf-8")
    git(root, "add", "--", "README.md")


def resolve_generated_conflicts(root: Path, conflicts: list[str]) -> None:
    other = sorted(set(conflicts) - GENERATED)
    if other:
        raise RuntimeError("Custom-branch conflicts require review: " + ", ".join(other))
    for path in conflicts:
        versions = conflict_versions(root, path)
        if path == "README.md":
            resolve_readme(root, versions)
        else:
            if any(not text.startswith("# Repository State\n") or STATE_NOTICE not in text
                   for text in versions.values()):
                raise RuntimeError("Unrecognized docs/REPOSITORY_STATE.md content requires review")
            # Resolve the index before computing its implementation fingerprint.
            (root / path).write_text(versions[2], encoding="utf-8")
            git(root, "add", "--", path)


def refresh_generated_state(root: Path) -> None:
    for path in sorted(GENERATED):
        entry = git(root, "ls-files", "--stage", "--", path).stdout
        if not entry.startswith("100644 ") or "\n" in entry.rstrip("\n"):
            raise RuntimeError(f"Tracked regular generated document required: {path}")
    state = (root / "docs/REPOSITORY_STATE.md").read_text(encoding="utf-8-sig")
    if not state.startswith("# Repository State\n") or STATE_NOTICE not in state:
        raise RuntimeError("Unrecognized docs/REPOSITORY_STATE.md content requires review")
    readme = root / "README.md"
    before, block, after = readme_parts(readme.read_bytes().decode("utf-8"))
    images = sorted((root / "docs/architecture").glob("*system-architecture.png"))
    if not images or not images[0].with_suffix(".svg").is_file():
        raise RuntimeError("Existing architecture source/rendering required; no broad documentation repair is allowed")
    section = documentation.readme_section(root, images[0])
    # Preserve all human text byte-for-byte, including whitespace outside the block.
    text = before + section + after if block else before + "\n\n" + section + "\n"
    readme.write_text(text, encoding="utf-8")
    documentation.write_repository_state(root)
    git(root, "add", "--", *sorted(GENERATED))
    validate_documentation(root)


def validate_documentation(root: Path) -> None:
    readme_parts((root / "README.md").read_bytes().decode("utf-8"))
    findings = documentation.verify(root)
    if findings:
        raise RuntimeError("Documentation validation failed: " + "; ".join(
            f"{finding.code}: {finding.message}" for finding in findings))


def synchronize(root: Path, main_sha: str, expected_head: str) -> str:
    for name, sha in (("main", main_sha), ("custom head", expected_head)):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuntimeError(f"Expected a full immutable SHA for {name}")
        git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    if git(root, "rev-parse", "HEAD").stdout.strip() != expected_head:
        raise RuntimeError("Custom branch HEAD does not match its expected original SHA")
    if git(root, "status", "--porcelain").stdout or git(root, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
        raise RuntimeError("Custom synchronization requires a clean checkout with no merge in progress")
    try:
        result = git(root, "-c", "merge.directoryRenames=false", "merge", "--no-ff", "--no-commit", main_sha, check=False)
        merging = git(root, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0
        conflicts = [path for path in git(root, "diff", "--name-only", "--diff-filter=U", "-z").stdout.split("\0") if path]
        if result.returncode and (not merging or not conflicts):
            raise RuntimeError(result.stderr.strip() or "Merge failed without resolvable generated-document conflicts")
        if not merging:
            validate_documentation(root)
            return expected_head
        resolve_generated_conflicts(root, conflicts)
        refresh_generated_state(root)
        git(root, "diff", "--cached", "--check")
        git(root, "commit", "--no-edit")
    except BaseException:
        if git(root, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode == 0:
            git(root, "merge", "--abort")
        raise
    return git(root, "rev-parse", "HEAD").stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("main_sha", help="Exact main commit that passed all required validators")
    parser.add_argument("--expected-head", required=True, help="Exact original custom-branch commit")
    args = parser.parse_args()
    try:
        root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.strip())
        print(synchronize(root, args.main_sha, args.expected_head))
    except (RuntimeError, OSError, UnicodeError) as error:
        print(f"Custom branch was not published: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
