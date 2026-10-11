"""Real Git regressions for ancestry-preserving custom-branch propagation."""
from pathlib import Path
import base64
import os
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/sync_custom_branch.py"
START = "<!-- documentation-health:start -->"
END = "<!-- documentation-health:end -->"
NOTICE = "This file is generated from the tracked repository tree."


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def put(repo, path, content):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def readme(human="base human text", generated="base fingerprint", tail="base closing text\n"):
    return f"# Product\n\n{human}\n\n{START}\n{generated}\n{END}\n\n{tail}"


def create_repo(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Sync test")
    git(root, "config", "user.email", "sync-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    put(root, "product.txt", "base\n")
    put(root, "README.md", readme())
    put(root, "docs/REPOSITORY_STATE.md", f"# Repository State\n\n{NOTICE}\nbase\n")
    put(root, "docs/architecture/test-system-architecture.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>\n')
    (root / "docs/architecture/test-system-architecture.png").write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aNKsAAAAASUVORK5CYII="))
    put(root, ".github/workflows/documentation-health.yml", "name: Documentation health\n")
    put(root, "scripts/documentation_health.py", "# Existing generated-document contract\n")
    commit(root, "base")
    git(root, "branch", "custom")
    return root


@pytest.fixture
def repo(tmp_path):
    return create_repo(tmp_path)


def sync(repo, main=None, expected=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), main or git(repo, "rev-parse", "main").stdout.strip(),
         "--expected-head", expected or git(repo, "rev-parse", "HEAD").stdout.strip()],
        cwd=repo, env={**os.environ, "REPOSITORY_DEFAULT_BRANCH": "main"},
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )


def assert_restored(repo, original, tree):
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == original
    assert git(repo, "rev-parse", "HEAD^{tree}").stdout.strip() == tree
    assert not git(repo, "status", "--porcelain").stdout
    assert git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode != 0


def test_merge_preserves_reviewed_reconciliation_that_plain_rebase_silently_loses(repo):
    git(repo, "checkout", "custom")
    put(repo, "overlay.txt", "original overlay\n")
    overlay = commit(repo, "original overlay")
    git(repo, "checkout", "main")
    put(repo, "upstream.txt", "first upstream sync\n")
    commit(repo, "first upstream sync")
    git(repo, "checkout", "custom")
    git(repo, "merge", "--no-ff", "--no-commit", "main")
    put(repo, "overlay.txt", "reviewed reconciled overlay\n")
    promotion = commit(repo, "reviewed overlay reconciliation")
    git(repo, "branch", "inherited-rebase")
    git(repo, "checkout", "main")
    put(repo, "upstream.txt", "second upstream sync\n")
    main = commit(repo, "next upstream sync")
    git(repo, "checkout", "inherited-rebase")
    git(repo, "rebase", "main")
    assert (repo / "overlay.txt").read_text(encoding="utf-8-sig") == "original overlay\n"
    git(repo, "checkout", "custom")

    result = sync(repo)

    assert result.returncode == 0, result.stderr
    assert (repo / "overlay.txt").read_text(encoding="utf-8-sig") == "reviewed reconciled overlay\n"
    for ancestor in (overlay, promotion, main):
        assert git(repo, "merge-base", "--is-ancestor", ancestor, "HEAD", check=False).returncode == 0
    assert git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:] == [promotion, main]
    after = git(repo, "rev-parse", "HEAD").stdout
    assert sync(repo).returncode == 0
    assert git(repo, "rev-parse", "HEAD").stdout == after
    assert not git(repo, "status", "--porcelain").stdout


def test_generated_conflicts_keep_human_changes_from_both_branches(repo):
    put(repo, "README.md", readme(human="new main human text", generated="main fingerprint"))
    put(repo, "docs/REPOSITORY_STATE.md", f"# Repository State\n\n{NOTICE}\nmain fingerprint\n")
    main = commit(repo, "main documentation")
    git(repo, "checkout", "custom")
    put(repo, "README.md", readme(generated="custom fingerprint", tail="custom closing text\n"))
    put(repo, "docs/REPOSITORY_STATE.md", f"# Repository State\n\n{NOTICE}\ncustom fingerprint\n")
    custom = commit(repo, "custom documentation")

    result = sync(repo)

    assert result.returncode == 0, result.stderr
    text = (repo / "README.md").read_text(encoding="utf-8-sig")
    assert text.startswith("# Product\n\nnew main human text\n\n" + START)
    assert text.endswith(END + "\n\ncustom closing text\n")
    assert "main fingerprint" not in text and "custom fingerprint" not in text
    assert "Implementation fingerprint" in text
    assert git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:] == [custom, main]
    assert not git(repo, "status", "--porcelain").stdout


@pytest.mark.parametrize("path", ["product.txt", ".github/workflows/documentation-health.yml", "README.md"])
def test_real_conflicts_fail_closed_and_restore_original_branch(repo, path):
    main_text = readme(human="main decision") if path == "README.md" else "main decision\n"
    custom_text = readme(human="custom decision") if path == "README.md" else "custom decision\n"
    put(repo, path, main_text)
    commit(repo, "main decision")
    git(repo, "checkout", "custom")
    put(repo, path, custom_text)
    original = commit(repo, "custom decision")
    tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()

    result = sync(repo)

    assert result.returncode != 0
    assert path in result.stderr
    assert_restored(repo, original, tree)
    assert (repo / path).read_text(encoding="utf-8-sig") == custom_text


@pytest.mark.parametrize("failure", ["malformed-readme", "missing-rendering", "invalid-state-conflict", "commit-hook"])
def test_failed_generation_validation_or_commit_restores_original(repo, failure):
    put(repo, "upstream.txt", "main feature\n")
    if failure == "malformed-readme":
        put(repo, "README.md", readme() + START + "\n")
    elif failure == "missing-rendering":
        git(repo, "rm", "docs/architecture/test-system-architecture.png")
    elif failure == "invalid-state-conflict":
        put(repo, "docs/REPOSITORY_STATE.md", "handwritten state requires review\n")
    commit(repo, "main change")
    git(repo, "checkout", "custom")
    put(repo, "custom.txt", "custom feature\n")
    if failure == "invalid-state-conflict":
        put(repo, "docs/REPOSITORY_STATE.md", f"# Repository State\n\n{NOTICE}\ncustom fingerprint\n")
    original = commit(repo, "custom feature")
    tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    if failure == "commit-hook":
        hook = repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

    result = sync(repo)

    assert result.returncode != 0
    assert_restored(repo, original, tree)


def test_dirty_checkout_and_changed_head_are_rejected_before_merge(repo):
    original = git(repo, "rev-parse", "HEAD").stdout.strip()
    put(repo, "main.txt", "main feature\n")
    main = commit(repo, "advance main")
    git(repo, "checkout", "custom")
    put(repo, "product.txt", "uncommitted work\n")
    result = sync(repo, expected=main)
    assert result.returncode != 0 and "expected original SHA" in result.stderr
    result = sync(repo, expected=original)
    assert result.returncode != 0 and "clean checkout" in result.stderr
    assert (repo / "product.txt").read_text(encoding="utf-8-sig") == "uncommitted work\n"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == original


@pytest.mark.parametrize("conflicting", [False, True])
def test_crlf_readme_fails_closed_without_rewriting_human_bytes(repo, conflicting):
    put(repo, "upstream.txt", "main feature\n")
    if conflicting:
        (repo / "README.md").write_bytes(readme(generated="main fingerprint").replace("\n", "\r\n").encode())
    commit(repo, "main feature")
    git(repo, "checkout", "custom")
    original_readme = readme(generated="custom fingerprint").replace("\n", "\r\n").encode()
    (repo / "README.md").write_bytes(original_readme)
    original = commit(repo, "custom documentation")
    tree = git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()

    result = sync(repo)

    assert result.returncode != 0
    assert "CR/CRLF" in result.stderr
    assert_restored(repo, original, tree)
    assert (repo / "README.md").read_bytes() == original_readme


def test_unchanged_branch_still_rejects_stale_documentation(repo):
    original = git(repo, "rev-parse", "HEAD").stdout.strip()
    result = sync(repo)
    assert result.returncode != 0
    assert "stale-state" in result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == original
    assert not git(repo, "status", "--porcelain").stdout
