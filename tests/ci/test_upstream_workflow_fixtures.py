"""Reference archives fail closed without concealing deployed-workflow defects."""

import hashlib
import json

import pytest

from tests.ci.upstream_workflow_fixtures import ARCHIVE, reference_workflow_path


def test_every_archived_reference_has_pinned_provenance_and_digest():
    manifest = json.loads((ARCHIVE / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    assert files
    assert set(files) == {path.name for path in ARCHIVE.glob("*.y*ml")}
    assert not {"ci.yaml", "tests.yml", "tests-os.yml"} & files.keys()
    for name in files:
        assert reference_workflow_path(name) == ARCHIVE / name


@pytest.fixture
def archive(tmp_path):
    data = b"name: Reference contract\n"
    (tmp_path / "owned.yml").write_bytes(data)
    manifest = {
        "source_repository": "https://github.com/NousResearch/hermes-agent",
        "source_commit": "a" * 40,
        "files": {"owned.yml": {
            "source_path": ".github/workflows/owned.yml",
            "sha256": hashlib.sha256(data).hexdigest(),
        }},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_modified_reference_bytes_are_rejected(archive):
    (archive / "owned.yml").write_text("name: Unreviewed contract\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="digest mismatch"):
        reference_workflow_path("owned.yml", archive=archive)


@pytest.mark.parametrize("name", ["tests.yml", "absent.yml", "../owned.yml"])
def test_unarchived_files_are_never_live_workflow_fallbacks(archive, name):
    with pytest.raises(AssertionError, match="unarchived reference"):
        reference_workflow_path(name, archive=archive)


def test_missing_archived_file_is_a_failure(archive):
    (archive / "owned.yml").unlink()
    with pytest.raises(FileNotFoundError):
        reference_workflow_path("owned.yml", archive=archive)


@pytest.mark.parametrize("field,value", [
    ("source_commit", "main"),
    ("source_repository", "https://example.invalid/untrusted"),
])
def test_reference_requires_exact_trusted_provenance(archive, field, value):
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AssertionError):
        reference_workflow_path("owned.yml", archive=archive)
