"""Exercise attribution against real PR/merge histories, without GitHub access."""

import subprocess

import pytest

from scripts import audit_pr_attribution as audit


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def commit(repo, name, email):
    (repo / name).write_text(name, encoding="utf-8")
    git(repo, "add", name)
    git(repo, "-c", f"user.email={email}", "commit", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def test_pr_head_is_checked_even_from_synthetic_merge(tmp_path, monkeypatch):
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "merge@example.com")
    base = commit(tmp_path, "base", "base@example.com")
    git(tmp_path, "checkout", "-b", "upstream")
    commit(tmp_path, "upstream", "upstream@example.com")
    git(tmp_path, "checkout", "-b", "pr", base)
    commit(tmp_path, "pr", "contributor@example.com")
    git(tmp_path, "merge", "--no-ff", "upstream", "-m", "Sync upstream")
    head = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "checkout", "main")
    git(tmp_path, "merge", "--no-ff", "pr", "-m", "Synthetic PR merge")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    assert audit.new_emails(base, head) == ["contributor@example.com"]
    assert not audit.is_mapped("contributor@example.com")
    mapping = tmp_path / "contributors" / "emails" / "contributor@example.com"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("contributor\n", encoding="utf-8")
    assert audit.is_mapped("contributor@example.com")


def test_invalid_commit_range_fails_instead_of_reporting_no_contributors(tmp_path, monkeypatch):
    git(tmp_path, "init", "-b", "main")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="git log"):
        audit.new_emails("missing-base", "missing-head")
