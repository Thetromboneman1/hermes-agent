"""Real canonical-runner receipts preserve selected/completed files, retries and failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from scripts.ci.verify_test_shards import verify
from tests.scripts.test_run_tests_parallel import _probe_root


@pytest.mark.parametrize("failing", [False, True])
def test_two_real_shards_produce_complete_sha_bound_retry_receipts(tmp_path, failing):
    repo = _probe_root(tmp_path)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_pass(): assert True\n")
    (tests / "test_b.py").write_text('''from pathlib import Path

def test_retry():
    marker = Path(__file__).with_suffix(".seen")
    seen = marker.exists()
    marker.touch()
    assert seen, "first-attempt failure"
''')
    (tests / "test_c.py").write_text("def test_pass(): assert True\n")
    (tests / "test_d.py").write_text(f"def test_outcome(): assert {not failing}\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-qm", "test fixture"], cwd=repo, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    reports = tmp_path / "reports"
    for index in (1, 2):
        output = reports / f"unit-tests-{index}"
        output.mkdir(parents=True)
        child = subprocess.run(
            [sys.executable, str(repo / "scripts/run_tests_parallel.py"), "-j", "2",
             "--file-retries", "1", "--slice", f"{index}/2", "--report-json", str(output / "report.json"), "-q"],
            cwd=repo, env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
            text=True, capture_output=True, timeout=30, check=False,
        )
        (output / "output.log").write_text(child.stdout + child.stderr)
        receipt = json.loads((output / "report.json").read_text())
        assert receipt["sha"] == sha and receipt["slice"] == f"{index}/2"
        assert receipt["exit_code"] == child.returncode == int(failing and index == 2)
        expected = [f"tests/test_{letter}.py" for letter in ("ac" if index == 1 else "bd")]
        assert receipt["files"] == expected
        assert [row["file"] for row in receipt["results"]] == expected
        if index == 2:
            assert receipt["results"][0] == {"file": "tests/test_b.py", "exit_code": 0, "retries": 1}
            assert receipt["results"][1]["retries"] == int(failing)
            assert "first-attempt failure" in child.stdout and "retry output" in child.stdout
    if failing:
        with pytest.raises(ValueError, match="did not succeed"):
            verify(reports, repo, sha, "failure")
        with pytest.raises(ValueError, match="unsuccessful receipt"):
            verify(reports, repo, sha, "success")
    else:
        assert "2 + 2 = 4 files; no overlaps or omissions; 1 retries across 1 flaky files" in verify(reports, repo, sha, "success")
