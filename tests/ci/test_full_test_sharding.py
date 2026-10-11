"""The bounded hosted unit shards must cover the original suite and fail together."""

from __future__ import annotations

from copy import deepcopy
import json
import subprocess

import pytest

from tests.ci import test_fork_workflow_dispatch as fork
from tests.ci import workflow_steps


def test_full_unit_matrix_is_bounded_and_covers_every_file_once():
    run = fork._sync_run()
    node = run["validate-main-tests"]["jobs"]["test-shards"]
    strategy = node["body"]["strategy"]
    assert strategy == {"fail-fast": False, "max-parallel": 2, "matrix": {"slice": [1, 2]}}
    assert node["body"]["runs-on"] == "ubuntu-latest"
    assert node["body"]["timeout-minutes"] == 60
    fork._assert_test_selection(run)
    cells = list(fork._leaf_jobs(node))
    shards = [fork._selected_tests(cell, "test-shards") for cell in cells]
    assert len(shards) == 2 and abs(len(shards[0]) - len(shards[1])) <= 1
    for cell in cells:
        _, calls = workflow_steps._run(fork._test_step(cell, "test-shards"), cell["ctx"], fork._REPO, receipt=True)
        assert calls[0][1:5] == ["-j", "4", "--slice", f'{cell["ctx"]["matrix"]["slice"]}/2']
        assert calls[0][5] == "--report-json"
        assert calls[0][7:] == ["tests/", "--", "-q", "--ignore=tests/integration", "--ignore=tests/e2e", "--tb=short"]


@pytest.mark.parametrize("slice_index", [1, 2])
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_either_unit_shard_blocks_validation_and_rebase(slice_index, result):
    run = fork._sync_run(force=True, outcomes={("validate-main-tests", "test-shards", str(slice_index)): result})
    check = run["validate-main-tests"]
    assert check["jobs"]["test-shards"]["matrix_jobs"][(slice_index,)]["result"] == result
    assert check["jobs"]["test-shards"]["result"] == result
    assert check["result"] == result
    assert run[fork._REBASE]["result"] == "skipped"


@pytest.mark.parametrize("result", [0, 1])
@pytest.mark.parametrize("tee_result", [0, 2])
def test_shard_logs_keep_retry_output_and_do_not_mask_failures(tmp_path, monkeypatch, result, tee_result):
    node = fork._sync_run()["validate-main-tests"]["jobs"]["test-shards"]
    cells = list(fork._leaf_jobs(node))
    assert len(cells) == 2
    monkeypatch.setattr(workflow_steps, "_RECEIPTS", workflow_steps._RECEIPTS + r'''
    scripts/run_tests.sh() { printf 'FLAKY: first failure and retry output\n'; return "$PROBE_RESULT"; }
    tee() { command tee "$@"; return "$TEE_RESULT"; }
    ''')
    for cell in cells:
        step = deepcopy(fork._test_step(cell, "test-shards"))
        step["env"].update(RUNNER_TEMP=str(tmp_path), PROBE_RESULT=str(result), TEE_RESULT=str(tee_result))
        if result or tee_result:
            with pytest.raises(AssertionError, match=f"replayed step exited {result or tee_result}"):
                workflow_steps._run(step, cell["ctx"], fork._REPO, receipt=True)
        else:
            workflow_steps._run(step, cell["ctx"], fork._REPO, receipt=True)
        shard = cell["ctx"]["matrix"]["slice"]
        assert (tmp_path / f"unit-tests-{shard}" / "output.log").read_text() == "FLAKY: first failure and retry output\n"
        uploads = [s for s in cell["body"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
        assert len(uploads) == 1
        upload = uploads[0]
        for succeeded in (True, False):
            ctx = {**cell["ctx"], "__status__": {"always": True, "success": succeeded}}
            workflow_steps.required(upload, ctx)
        assert upload["with"]["if-no-files-found"] == "error"
        assert fork.gha.render(upload["with"]["name"], cell["ctx"]) == f"unit-tests-{shard}"
        assert fork.gha.render(upload["with"]["path"], {**cell["ctx"], "runner": {"temp": str(tmp_path)}}) == str(tmp_path / f"unit-tests-{shard}") + "/"


@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped"])
def test_stable_test_gate_runs_and_fails_closed_on_partial_runs(tmp_path, result):
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=fork._REPO, text=True).strip()
    jobs = fork._run_workflow(fork._TESTS, inputs={"validation_ref": sha}, outcomes={("test-shards",): result})
    gate = jobs["test"]
    assert fork._needs(gate["body"]) == ["test-shards"]
    workflow_steps.required(gate["body"], gate["ctx"])
    steps = gate["body"]["steps"]
    downloads = [step for step in steps if step.get("uses", "").startswith("actions/download-artifact@")]
    assert len(downloads) == 1
    workflow_steps.required(downloads[0], gate["ctx"])
    assert downloads[0]["with"]["pattern"] == "unit-tests-*"
    step = deepcopy(next(step for step in steps if step.get("name") == "Require complete successful unit coverage"))
    step["env"]["RUNNER_TEMP"] = str(tmp_path)
    if result == "success":
        for cell in fork._leaf_jobs(jobs["test-shards"]):
            index = cell["ctx"]["matrix"]["slice"]
            files = sorted(fork._selected_tests(cell, "test-shards"))
            output = tmp_path / "unit-test-reports" / f"unit-tests-{index}"
            output.mkdir(parents=True)
            (output / "report.json").write_text(json.dumps({
                "sha": sha, "slice": f"{index}/2", "exit_code": 0, "files": files,
                "results": [{"file": file, "exit_code": 0, "retries": 0} for file in files],
            }))
            (output / "output.log").write_text("complete runner output\n")
        workflow_steps._run(step, gate["ctx"], fork._REPO)
        (output / "report.json").unlink()
    with pytest.raises(AssertionError, match="replayed step exited 1"):
        workflow_steps._run(step, gate["ctx"], fork._REPO)
