"""Real Git contracts for the fork's upstream merge policy."""
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_upstream.sh"


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


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Sync test")
    git(root, "config", "user.email", "sync-test@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    put(root, "product.txt", "base\n")
    put(root, "docs/upstream.md", "upstream guide\n")
    put(root, ".github/workflows/tests.yml", "base workflow\n")
    commit(root, "base")
    git(root, "branch", "upstream")
    return root


def sync(repo):
    return subprocess.run(
        ["bash", str(SCRIPT), "upstream"], cwd=repo, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
    )


def test_preserves_added_fork_paths_and_exact_workflows(repo):
    put(repo, "docs/fork.md", "fork custom guide\n")
    put(repo, ".github/workflows/tests.yml", "fork tests\n")
    put(repo, ".github/workflows/custom.yml", "fork custom\n")
    before = commit(repo, "fork additions")
    workflows = git(repo, "rev-parse", "HEAD:.github/workflows").stdout
    git(repo, "checkout", "upstream")
    (repo / "website/docs/developer-guide").mkdir(parents=True)
    git(repo, "mv", "docs/upstream.md", "website/docs/developer-guide/upstream.md")
    put(repo, ".github/workflows/tests.yml", "upstream tests\n")
    put(repo, ".github/workflows/upstream-only.yml", "upstream paid lane\n")
    upstream = commit(repo, "move docs and change workflows")
    git(repo, "checkout", "main")

    result = sync(repo)

    assert result.returncode == 0, result.stderr
    assert (repo / "docs/fork.md").read_text() == "fork custom guide\n"
    assert not (repo / "website/docs/developer-guide/fork.md").exists()
    assert (repo / "website/docs/developer-guide/upstream.md").exists()
    assert git(repo, "rev-parse", "HEAD:.github/workflows").stdout == workflows
    assert git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:] == [before, upstream]
    assert not git(repo, "status", "--porcelain").stdout


def test_product_conflict_aborts_without_discarding_either_side(repo):
    put(repo, "product.txt", "fork behavior\n")
    before = commit(repo, "fork product change")
    git(repo, "checkout", "upstream")
    put(repo, "product.txt", "upstream behavior\n")
    upstream = commit(repo, "upstream product change")
    git(repo, "checkout", "main")

    result = sync(repo)

    assert result.returncode != 0
    assert "product.txt" in result.stderr
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert git(repo, "rev-parse", "upstream").stdout.strip() == upstream
    assert (repo / "product.txt").read_text() == "fork behavior\n"
    assert not git(repo, "status", "--porcelain").stdout
    assert git(repo, "rev-parse", "--verify", "MERGE_HEAD", check=False).returncode != 0


def test_fast_forward_candidate_retains_workflows_without_rewriting_upstream(repo):
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    workflows = git(repo, "rev-parse", "HEAD:.github/workflows").stdout
    git(repo, "checkout", "upstream")
    put(repo, "product.txt", "new upstream feature\n")
    git(repo, "rm", ".github/workflows/tests.yml")
    put(repo, ".github/workflows/new.yml", "upstream only\n")
    upstream = commit(repo, "advance upstream")
    git(repo, "checkout", "main")

    result = sync(repo)

    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()[1:] == [before, upstream]
    assert git(repo, "rev-parse", "upstream").stdout.strip() == upstream
    assert git(repo, "rev-parse", "HEAD:.github/workflows").stdout == workflows
    assert (repo / "product.txt").read_text() == "new upstream feature\n"
    first_merge = git(repo, "rev-parse", "HEAD").stdout
    assert sync(repo).returncode == 0
    assert git(repo, "rev-parse", "HEAD").stdout == first_merge


def test_dirty_checkout_is_rejected_before_merge(repo):
    put(repo, "product.txt", "uncommitted work\n")
    result = sync(repo)
    assert result.returncode != 0
    assert "clean checkout" in result.stderr
    assert (repo / "product.txt").read_text() == "uncommitted work\n"


@pytest.mark.parametrize("remove_upstream", [False, True])
def test_reference_archive_refreshes_or_fails_closed(repo, remove_upstream):
    import json
    import shutil

    from scripts.refresh_upstream_workflow_fixtures import FIXTURES

    directory = repo / FIXTURES
    directory.mkdir(parents=True)
    put(repo, str(FIXTURES / "tests.yml"), "previous reference\n")
    put(repo, str(FIXTURES / "manifest.json"), json.dumps({
        "source_repository": "https://github.com/NousResearch/hermes-agent", "source_commit": "old",
        "files": {"tests.yml": {"source_path": ".github/workflows/tests.yml", "sha256": "old"}},
    }))
    (repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT.with_name("refresh_upstream_workflow_fixtures.py"),
                    repo / "scripts/refresh_upstream_workflow_fixtures.py")
    before = commit(repo, "fork reference contracts")
    git(repo, "checkout", "upstream")
    if remove_upstream:
        git(repo, "rm", ".github/workflows/tests.yml")
    else:
        put(repo, ".github/workflows/tests.yml", "new reference workflow\n")
    upstream = commit(repo, "upstream contract change")
    git(repo, "checkout", "main")

    result = sync(repo)

    if remove_upstream:
        assert result.returncode != 0
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
        assert (directory / "tests.yml").read_text() == "previous reference\n"
    else:
        assert result.returncode == 0, result.stderr
        assert (directory / "tests.yml").read_text() == "new reference workflow\n"
        assert json.loads((directory / "manifest.json").read_text())["source_commit"] == upstream
    assert (repo / ".github/workflows/tests.yml").read_text() == "base workflow\n"
    assert not git(repo, "status", "--porcelain").stdout
