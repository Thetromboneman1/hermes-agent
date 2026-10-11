"""Archived workflow contracts track their exact upstream, never deploy it."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.refresh_upstream_workflow_fixtures import FIXTURES, refresh


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--initial-branch=main")
    git(root, "config", "user.name", "Fixture test")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    workflows = root / ".github/workflows"
    workflows.mkdir(parents=True)
    (workflows / "reference.yml").write_text("name: upstream contract\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "upstream workflow")
    revision = git(root, "rev-parse", "HEAD")
    # The checked-out workflow differs from the immutable upstream source.
    (workflows / "reference.yml").write_text("name: fork deployment\n", encoding="utf-8")
    directory = root / FIXTURES
    directory.mkdir(parents=True)
    (directory / "reference.yml").write_text("stale fixture\n", encoding="utf-8")
    manifest = {
        "source_repository": "https://github.com/NousResearch/hermes-agent", "source_commit": "old",
        "files": {"reference.yml": {"source_path": ".github/workflows/reference.yml", "sha256": "old"}},
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root, revision


def test_refreshes_exact_pinned_source_without_touching_live_workflow(repository):
    root, revision = repository
    refresh(root, revision)
    contents = (root / FIXTURES / "reference.yml").read_bytes()
    manifest = json.loads((root / FIXTURES / "manifest.json").read_text())
    assert contents == b"name: upstream contract\n"
    assert manifest["source_commit"] == revision
    assert manifest["files"]["reference.yml"]["sha256"] == hashlib.sha256(contents).hexdigest()
    assert (root / ".github/workflows/reference.yml").read_text() == "name: fork deployment\n"


def test_missing_upstream_file_leaves_entire_archive_unchanged(repository):
    root, revision = repository
    directory = root / FIXTURES
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["removed.yml"] = {"source_path": ".github/workflows/removed.yml", "sha256": "old"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(subprocess.CalledProcessError):
        refresh(root, revision)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("field,value", [
    ("source_repository", "another/repository"),
    ("files", {"../escape.yml": {"source_path": ".github/workflows/../escape.yml"}}),
    ("files", {"reference.yml": {"source_path": "README.md"}}),
])
def test_rejects_manifest_that_widens_the_archive_scope(repository, field, value):
    root, revision = repository
    path = root / FIXTURES / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        refresh(root, revision)
    assert (root / FIXTURES / "reference.yml").read_text() == "stale fixture\n"


def test_shipped_manifest_is_accepted_by_the_refresh_path(repository):
    import shutil

    root, _ = repository
    shipped = Path(__file__).resolve().parents[2] / FIXTURES
    manifest = json.loads((shipped / "manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        destination = root / entry["source_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(shipped / name, destination)
    git(root, "add", ".github/workflows")
    git(root, "commit", "-m", "exact shipped reference workflow contents")
    revision = git(root, "rev-parse", "HEAD")
    shutil.copyfile(shipped / "manifest.json", root / FIXTURES / "manifest.json")

    refresh(root, revision)

    generated = json.loads((root / FIXTURES / "manifest.json").read_text())
    assert generated == {**manifest, "source_commit": revision}
    for name in manifest["files"]:
        assert (root / FIXTURES / name).read_bytes() == (shipped / name).read_bytes()
