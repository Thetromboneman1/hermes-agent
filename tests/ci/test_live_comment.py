"""Tests for scripts/ci/live_comment.py run selection.

The poller now reports on a run it is not part of, and merges jobs from
sibling runs of the same commit (the Docker image build, which left ci.yml
to stop holding the CI run open). ``select_watched_runs`` decides which
sibling runs count.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "live_comment.py"
_spec = importlib.util.spec_from_file_location("live_comment", _PATH)
if _spec is None or _spec.loader is None:
    raise ImportError("Failed to load live_comment.py")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["live_comment"] = _mod
_spec.loader.exec_module(_mod)

select_watched_runs = _mod.select_watched_runs
classify_jobs = _mod.classify_jobs

DOCKER = "Docker Build, Test, and Publish"


def _run(run_id: int, name: str, created_at: str) -> dict:
    return {"id": run_id, "name": name, "created_at": created_at}


def test_selects_only_named_workflows():
    runs = [
        _run(1, DOCKER, "2026-08-08T10:00:00Z"),
        _run(2, "Deploy site", "2026-08-08T10:00:00Z"),
        _run(3, "CI", "2026-08-08T10:00:00Z"),
    ]
    selected = select_watched_runs(runs, [DOCKER])
    assert [r["id"] for r in selected] == [1]


def test_keeps_newest_attempt_per_workflow():
    """A rerun makes a second run for the same commit; the old one is stale."""
    runs = [
        _run(1, DOCKER, "2026-08-08T10:00:00Z"),
        _run(2, DOCKER, "2026-08-08T11:30:00Z"),
    ]
    selected = select_watched_runs(runs, [DOCKER])
    assert [r["id"] for r in selected] == [2]


def test_excludes_the_ci_run_itself():
    runs = [_run(7, "CI", "2026-08-08T10:00:00Z")]
    assert select_watched_runs(runs, ["CI"], exclude_run_id="7") == []
    assert len(select_watched_runs(runs, ["CI"], exclude_run_id="8")) == 1


def test_no_watch_names_selects_nothing():
    runs = [_run(1, DOCKER, "2026-08-08T10:00:00Z")]
    assert select_watched_runs(runs, []) == []
    assert select_watched_runs(runs, [""]) == []


def test_watched_run_jobs_carry_the_workflow_name_into_the_comment():
    """A watched run's jobs must stay distinguishable from CI's own jobs."""
    jobs = [
        {"name": "build (amd64)", "status": "completed", "conclusion": "failure",
         "html_url": "https://example/1", "_workflow_name": DOCKER},
        {"name": "Python tests", "status": "completed", "conclusion": "success",
         "html_url": "https://example/2"},
    ]
    completed, pending, job_urls = classify_jobs(jobs)
    assert completed[f"{DOCKER} / build (amd64)"] == "failure"
    assert completed["Python tests"] == "success"
    assert pending == []
    assert job_urls[f"{DOCKER} / build (amd64)"] == "https://example/1"


def test_parse_watch_workflows_keeps_commas_inside_a_name():
    """Workflow names contain commas, so the list is newline-separated."""
    assert _mod.parse_watch_workflows("Docker Build, Test, and Publish\n") == [
        "Docker Build, Test, and Publish"
    ]
    assert _mod.parse_watch_workflows("A\nB\n\n  C  \n") == ["A", "B", "C"]
    assert _mod.parse_watch_workflows("") == []


def _workflow_watch_lists(doc: dict):
    # PyYAML's YAML 1.1 loader reads the GitHub Actions "on" key as True.
    triggers = doc.get("on", doc.get(True, {}))
    if isinstance(triggers, dict):
        workflow_run = triggers.get("workflow_run", {})
        if isinstance(workflow_run, dict) and "workflows" in workflow_run:
            yield workflow_run["workflows"]
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            env = step.get("env") or {}
            if "WATCH_WORKFLOWS" in env:
                yield _mod.parse_watch_workflows(env["WATCH_WORKFLOWS"])


def _watching_workflows():
    yaml = pytest.importorskip("hermes_yaml")
    root = Path(__file__).resolve().parents[2]
    docs = [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted((root / ".github/workflows").glob("*.y*ml"))
    ]
    assert docs, "found no workflows to validate"
    return docs


def test_workflow_watch_lists_name_workflows_that_exist():
    """Validate deployed watchers, including fork lifecycle notifications.

    A nonexistent workflow name silently drops that run from the report. The
    fork uses workflow_run notifications instead of the upstream comment job;
    both forms must reference the actual workflow names in this checkout.
    """
    docs = _watching_workflows()
    known = {doc["name"] for doc in docs}
    watched_lists = [watched for doc in docs for watched in _workflow_watch_lists(doc)]
    assert watched_lists, "found no workflow watchers — the walk is broken"
    for watched in watched_lists:
        assert watched, "the watcher is watching nothing"
        assert set(watched) <= known, f"unknown workflow names: {set(watched) - known}"


def test_workflow_watchers_never_watch_themselves():
    """Self-watching deadlocks a poller and loops workflow_run notifications."""
    for doc in _watching_workflows():
        for watched in _workflow_watch_lists(doc):
            assert doc["name"] not in watched


@pytest.mark.parametrize("triggers_key", ["on", True])
def test_watch_list_discovery_covers_notification_and_poller_workflows(triggers_key):
    doc = {
        triggers_key: {"workflow_run": {"workflows": [DOCKER]}},
        "jobs": {"comment": {"steps": [{"env": {"WATCH_WORKFLOWS": f"{DOCKER}\nCI"}}]}},
    }
    assert list(_workflow_watch_lists(doc)) == [[DOCKER], [DOCKER, "CI"]]


@pytest.mark.parametrize("gate", ["all-checks-pass", "All required checks pass (v2)"])
def test_merge_gate_jobs_are_omitted_from_the_comment(gate):
    """Infrastructure gates do not appear as user-facing test results."""
    completed, pending, _ = classify_jobs([
        {"name": gate, "status": "completed", "conclusion": "success"},
        {"name": "Python tests", "status": "completed", "conclusion": "failure"},
    ])
    assert (completed, pending) == ({"Python tests": "failure"}, [])


# ─── runs_all_completed ───────────────────────────────────────────────


def test_runs_all_completed_true_only_when_every_run_finished():
    done = {"status": "completed"}
    running = {"status": "in_progress"}
    queued = {"status": "queued"}
    assert _mod.runs_all_completed([done])
    assert _mod.runs_all_completed([done, done])
    assert not _mod.runs_all_completed([done, running])
    assert not _mod.runs_all_completed([queued])


def test_runs_all_completed_empty_list_is_not_done():
    """No run info at all must not read as 'everything passed'."""
    assert not _mod.runs_all_completed([])


def test_runs_all_completed_missing_status_is_not_done():
    assert not _mod.runs_all_completed([{}])
