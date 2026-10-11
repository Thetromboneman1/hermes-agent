"""Missing, partial, failed or mismatched unit receipts cannot pass the aggregate."""

from __future__ import annotations

import json

import pytest

from scripts.ci import verify_test_shards as gate


@pytest.mark.parametrize("mutation", [
    "missing-report", "missing-log", "empty-log", "wrong-sha", "wrong-slice", "failed-run",
    "failed-file", "missing-completion", "duplicate-completion", "overlap", "omission", "duplicate-selection",
    "bad-retry", "failure", "cancelled", "skipped", "wrong-checkout",
])
def test_aggregate_rejects_incomplete_or_unsuccessful_evidence(tmp_path, monkeypatch, mutation):
    repo, reports, sha = tmp_path / "repo", tmp_path / "reports", "a" * 40
    (repo / "tests").mkdir(parents=True)
    for name in "abcde":
        (repo / "tests" / f"test_{name}.py").write_text("def test_pass(): pass\n")
    for excluded in ("e2e", "integration", "docker"):
        path = repo / "tests" / excluded
        path.mkdir()
        (path / "test_excluded.py").write_text("def test_external(): pass\n")
    monkeypatch.setattr(gate.subprocess, "check_output", lambda *args, **kwargs: sha + "\n")
    for index, names in ((1, "ace"), (2, "bd")):
        output = reports / f"unit-tests-{index}"
        output.mkdir(parents=True)
        files = [f"tests/test_{name}.py" for name in names]
        (output / "report.json").write_text(json.dumps({
            "sha": sha, "slice": f"{index}/2", "exit_code": 0, "files": files,
            "results": [{"file": file, "exit_code": 0, "retries": 0} for file in files],
        }))
        (output / "output.log").write_text("runner summary\n")
    assert "3 + 2 = 5 files" in gate.verify(reports, repo, sha, "success")
    path = reports / "unit-tests-2" / "report.json"
    receipt = json.loads(path.read_text())
    result = "success"
    mutations = {
        "missing-report": path.unlink,
        "missing-log": path.with_name("output.log").unlink,
        "empty-log": lambda: path.with_name("output.log").write_text(""),
        "wrong-sha": lambda: receipt.update(sha="b" * 40),
        "wrong-slice": lambda: receipt.update(slice="1/2"),
        "failed-run": lambda: receipt.update(exit_code=1),
        "failed-file": lambda: receipt["results"][0].update(exit_code=1),
        "missing-completion": receipt["results"].pop,
        "duplicate-completion": lambda: receipt["results"].append(receipt["results"][0]),
        "overlap": lambda: receipt["files"].__setitem__(0, "tests/test_a.py"),
        "omission": receipt["files"].pop,
        "duplicate-selection": lambda: receipt["files"].append(receipt["files"][0]),
        "bad-retry": lambda: receipt["results"][0].update(retries=-1),
        "wrong-checkout": lambda: monkeypatch.setattr(gate.subprocess, "check_output", lambda *a, **k: "b" * 40 + "\n"),
    }
    if mutation in {"failure", "cancelled", "skipped"}:
        result = mutation
    else:
        mutations[mutation]()
    if mutation != "missing-report":
        path.write_text(json.dumps(receipt))
    with pytest.raises((ValueError, OSError)):
        gate.verify(reports, repo, sha, result)
