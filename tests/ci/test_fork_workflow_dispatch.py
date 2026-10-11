"""Replay the deployed fork's read-only validation before downstream rebases.

Only job expressions and safe test-selection commands are evaluated locally.
Sync, rebase, installation, Docker and Nix commands are never executed here.
Classifier ownership tests live separately in test_update_ci_routing.py: the
fork intentionally runs these bounded checks without upstream lane gates.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from functools import cache
from pathlib import Path
import re
import sys

import pytest

from scripts import run_tests_parallel as runner
from tests.ci import _gha_expr as gha
from tests.ci import workflow_steps

_REPO = Path(__file__).resolve().parents[2]
_SYNC = ".github/workflows/upstream-sync.yml"
_TESTS = ".github/workflows/tests.yml"
_SHA = "a" * 40
_STALE_SHA = "b" * 40
_CHECKS = (
    "validate-main-tests", "validate-main-nix", "validate-main-lockfiles",
    "validate-main-docs-site", "validate-main-container", "validate-main-documentation",
)
_REBASE = "rebase-custom-branches"
_E2E_SMOKES = {
    "tests/e2e/test_discord_adapter.py",
    "tests/e2e/test_platform_commands.py",
    "tests/e2e/test_relay_native_anthropic_stream.py",
    "tests/e2e/test_relay_native_openai_stream.py",
}
_ACP_TESTS = {"tests/acp_adapter/test_session.py", "tests/acp_adapter/test_server.py"}
_TEST_STEPS = {"acp-regression": "Run ACP regression suite", "test": "Run tests", "e2e": "Run e2e tests"}


@cache
def _yaml(rel):
    import hermes_yaml

    return hermes_yaml.safe_load((_REPO / rel).read_text(encoding="utf-8-sig"))


def _on(workflow):
    return workflow.get("on", workflow.get(True)) or {}


def _needs(body):
    needs = body.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def _inputs_for(called, given):
    declared = (_on(_yaml(called)).get("workflow_call") or {}).get("inputs") or {}
    assert not set(given) - set(declared), f"{called}: undeclared inputs {set(given) - set(declared)}"
    for name, spec in declared.items():
        assert not spec.get("required") or name in given, f"{called}: missing required input {name}"
    return {name: given.get(name, spec.get("default")) for name, spec in declared.items()}


def _aggregate(jobs):
    """Propagate failures for required fork calls; reject an all-skipped call.

    This is not a general model of hosted reusable-workflow conclusions: the
    fork's validators require runnable jobs, checked individually below too.
    """
    results = {node["result"] for node in jobs.values()}
    if "failure" in results:
        return "failure"
    if "cancelled" in results:
        return "cancelled"
    assert "success" in results, "required reusable validation selected no successful jobs"
    return "success"


def _run_workflow(rel, *, inputs, outcomes=None, step_outputs=None, github=None, prefix=()):
    """Replay needs, implicit success(), expressions, outputs and reusable calls.

    Inject only external outcomes and the sync step's output, never an expected
    graph. A failed child contributes to its caller's result. No workflow run
    command is executed; test-selection receipts are handled separately.
    """
    outcomes, step_outputs = outcomes or {}, step_outputs or {}
    github = github or {"event_name": "workflow_dispatch", "sha": _STALE_SHA, "ref": "refs/heads/main"}
    jobs, done = _yaml(rel)["jobs"], {}
    pending = list(jobs)
    while pending:
        progressed = False
        for name in list(pending):
            body, path = jobs[name], (*prefix, name)
            needs = _needs(body)
            if any(dep not in done for dep in needs):
                continue
            pending.remove(name)
            progressed = True
            ancestors = set(needs)
            for dep in needs:
                ancestors.update(done[dep]["ancestors"])
            success = all(done[dep]["result"] == "success" for dep in needs)
            ctx = {
                "inputs": inputs, "github": github,
                "needs": {dep: {"outputs": done[dep]["outputs"], "result": done[dep]["result"]}
                          for dep in needs},
                "steps": step_outputs.get(path, {}),
                "__status__": {
                    "always": True, "success": success,
                    "failure": any(done[dep]["result"] == "failure" for dep in ancestors),
                    "cancelled": any(done[dep]["result"] == "cancelled" for dep in ancestors),
                },
            }
            cond = body.get("if")
            uses_status = isinstance(cond, str) and re.search(r"\b(always|success|failure|cancelled)\(", cond)
            active = (success or bool(uses_status)) and gha.condition(cond, ctx)
            node = {"body": body, "ctx": ctx, "outputs": {}, "result": "skipped", "ancestors": ancestors}
            done[name] = node
            if not active:
                continue
            result = outcomes.get(path, "success")
            assert result in {"success", "failure", "cancelled", "skipped"}
            uses = body.get("uses")
            if isinstance(uses, str) and uses.startswith("./.github/workflows/"):
                called = uses[2:]
                given = {key: gha.render(value, ctx) for key, value in (body.get("with") or {}).items()}
                node["inputs"] = _inputs_for(called, given)
                node["jobs"] = _run_workflow(
                    called, inputs=node["inputs"], outcomes=outcomes, step_outputs=step_outputs,
                    github=github, prefix=path)
                result = outcomes.get(path, _aggregate(node["jobs"]))
            if result == "failure" and gha.truthy(gha.render(body.get("continue-on-error", False), ctx)):
                result = "success"
            node["result"] = result
            if result == "success":
                node["outputs"] = {key: gha.render(value, ctx) for key, value in body.get("outputs", {}).items()}
        assert progressed, f"{rel}: unresolved or cyclic needs among {pending}"
    return done


def _sync_run(*, changed=True, force=False, outcomes=None, event="workflow_dispatch"):
    return _run_workflow(
        _SYNC, inputs={"force_rebase": force}, outcomes=outcomes,
        step_outputs={("sync-main",): {"sync": {"outputs": {
            "before": _STALE_SHA, "after": _SHA, "changed": gha.to_string(changed),
        }}}},
        github={"event_name": event, "sha": _STALE_SHA, "ref": "refs/heads/main"})


def _leaf_jobs(node):
    if "jobs" not in node:
        yield node
    else:
        for child in node["jobs"].values():
            yield from _leaf_jobs(child)


def _assert_checks(run):
    assert run["sync-main"]["outputs"]["after"] == _SHA
    assert set(_needs(run[_REBASE]["body"])) == {"sync-main", *_CHECKS}
    for name in _CHECKS:
        node = run[name]
        assert node["result"] == "success", f"{name}: {node['result']}"
        assert _needs(node["body"]) == ["sync-main"], f"{name}: not dependent on sync"
        assert node["body"].get("permissions") == {"contents": "read"}, name
        workflow_steps.required(node["body"], node["ctx"])
        if "jobs" in node:
            assert not node["body"].get("secrets"), f"{name}: validation must not receive secrets"
            assert node["inputs"]["validation_ref"] == _SHA, name
            called = _yaml(node["body"]["uses"][2:])
            declaration = _on(called)["workflow_call"]["inputs"]["validation_ref"]
            assert declaration["type"] == "string" and declaration["required"] is True
            assert called["permissions"] == {"contents": "read"}, name
        for leaf in _leaf_jobs(node):
            assert leaf["result"] == "success", name
            workflow_steps.required(leaf["body"], leaf["ctx"])
            assert leaf["body"].get("permissions", {"contents": "read"}) == {"contents": "read"}
            timeout = leaf["body"].get("timeout-minutes")
            assert isinstance(timeout, int) and 0 < timeout <= 90, f"{name}: unbounded timeout"
            checkouts = [step for step in leaf["body"]["steps"]
                         if step.get("uses", "").startswith("actions/checkout@")]
            assert checkouts, f"{name}: no checkout"
            for checkout in checkouts:
                workflow_steps.required(checkout, leaf["ctx"])
                assert gha.render(checkout["with"]["ref"], leaf["ctx"]) == _SHA, name
                assert checkout["with"].get("persist-credentials") is False, name


def _test_step(node, job):
    matches = [step for step in node["body"]["steps"] if step.get("name") == _TEST_STEPS[job]]
    assert len(matches) == 1, f"{job}: missing/ambiguous test step"
    return matches[0]


def _selected_tests(node, job, repo=_REPO):
    """Execute the actual shell selector; feed its argv receipt to the real runner."""
    workflow_steps.required(node["body"], node["ctx"])
    step = _test_step(node, job)
    _, calls = workflow_steps._run(step, node["ctx"], repo, receipt=True)
    assert len(calls) == 1 and calls[0][0] == "pytest", f"{job}: no single test invocation: {calls}"
    argv = calls[0][1:]
    split = argv.index("--") if "--" in argv else len(argv)
    selection, passthrough = argv[:split], argv[split + 1:]
    include = "--include-integration" in selection
    paths = [arg for arg in selection if arg != "--include-integration"]
    assert paths and all(path.startswith("tests/") for path in paths), argv
    assert all(arg in {"-q", "-v", "--tb=short"} or arg.startswith("--ignore=")
               for arg in passthrough), f"unsupported selection-affecting arguments: {argv}"
    assert all((repo / path).exists() for path in paths), f"nonexistent test selection: {paths}"
    args = argparse.Namespace(files=None, files_from=None, paths_positional=paths,
                              paths="tests", include_integration=include)
    skips = runner._SKIP_PARTS
    try:
        files, _ = runner._select_files(args, passthrough, repo)
    finally:
        runner._SKIP_PARTS = skips
    assert files and all(path.stat().st_size for path in files), f"{job}: empty test selection"
    return {path.relative_to(repo).as_posix() for path in files}


def _assert_test_selection(run, repo=_REPO):
    jobs = run["validate-main-tests"]["jobs"]
    assert set(jobs) == set(_TEST_STEPS), "the bounded test jobs changed"
    assert _selected_tests(jobs["acp-regression"], "acp-regression", repo) == _ACP_TESTS
    assert _selected_tests(jobs["e2e"], "e2e", repo) == _E2E_SMOKES
    selected = _selected_tests(jobs["test"], "test", repo)
    assert _ACP_TESTS <= selected
    expected_units = {path.relative_to(repo).as_posix() for path in (repo / "tests").rglob("test_*.py")
                      if not {"integration", "e2e", "docker"}.intersection(path.relative_to(repo).parts)}
    assert selected == expected_units, "unit validation must cover the complete maintained unit tree"
    assert not any(path.startswith(("tests/e2e/", "tests/integration/")) for path in selected)
    for node in jobs.values():
        setups = [step for step in node["body"]["steps"] if step.get("uses") == "./.github/actions/setup-pm"]
        assert len(setups) == 1
        workflow_steps.required(setups[0], node["ctx"])
        assert setups[0]["with"]["test-environment"] == "true"


def _mutated_yaml(monkeypatch, rel):
    original, changed = _yaml, deepcopy(_yaml(rel))
    monkeypatch.setattr(sys.modules[__name__], "_yaml", lambda path: changed if path == rel else original(path))
    return changed


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
@pytest.mark.parametrize("changed,force,rebase", [(True, False, True), (False, False, False), (False, True, True)])
def test_sync_dispatch_validates_exact_sha_even_when_unchanged(event, changed, force, rebase):
    run = _sync_run(changed=changed, force=force, event=event)
    _assert_checks(run)
    assert (run[_REBASE]["result"] == "success") is rebase


@pytest.mark.parametrize("dependency", ["sync-main", *_CHECKS])
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("force", [False, True])
def test_any_unsuccessful_dependency_blocks_rebase_even_when_forced(dependency, result, force):
    run = _sync_run(force=force, outcomes={(dependency,): result})
    assert run[dependency]["result"] == result
    assert run[_REBASE]["result"] == "skipped"
    if dependency == "sync-main":
        assert all(run[name]["result"] == "skipped" for name in _CHECKS)
    else:
        assert all(run[name]["result"] == "success" for name in _CHECKS if name != dependency)


@pytest.mark.parametrize("check,job", [
    *(("validate-main-tests", job) for job in _TEST_STEPS),
    ("validate-main-nix", "nix"), ("validate-main-lockfiles", "check"),
    ("validate-main-docs-site", "docs-site-checks"),
])
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_failed_reusable_job_propagates_through_validation_to_rebase(check, job, result):
    run = _sync_run(force=True, outcomes={(check, job): result})
    assert run[check]["jobs"][job]["result"] == result
    assert run[check]["result"] == result
    assert run[_REBASE]["result"] == "skipped"


def test_fork_runs_full_unit_suite_and_exact_existing_e2e_smokes():
    _assert_test_selection(_sync_run())


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
def test_direct_test_events_use_event_sha_and_reach_all_bounded_jobs(event):
    assert event in _on(_yaml(_TESTS))
    jobs = _run_workflow(_TESTS, inputs={}, github={"event_name": event, "sha": _STALE_SHA})
    assert set(jobs) == set(_TEST_STEPS)
    for node in jobs.values():
        assert node["result"] == "success"
        checkout = next(step for step in node["body"]["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert gha.render(checkout["with"]["ref"], node["ctx"]) == _STALE_SHA


@pytest.mark.parametrize("mutation", ["stale-ref", "wrong-output", "missing-dependency", "write-permission", "unbounded", "disabled", "advisory"])
def test_validation_contract_rejects_unsafe_graph_mutations(monkeypatch, mutation):
    _assert_checks(_sync_run())
    workflow = _mutated_yaml(monkeypatch, _SYNC)
    node = workflow["jobs"]["validate-main-container"]
    if mutation == "stale-ref":
        node["steps"][0]["with"]["ref"] = "${{ github.sha }}"
    elif mutation == "wrong-output":
        workflow["jobs"]["sync-main"]["outputs"]["after"] = "${{ steps.sync.outputs.before }}"
    elif mutation == "missing-dependency":
        workflow["jobs"][_REBASE]["needs"].remove("validate-main-container")
    elif mutation == "write-permission":
        node["permissions"] = {"contents": "write"}
    elif mutation == "unbounded":
        node.pop("timeout-minutes")
    elif mutation == "disabled":
        node["if"] = "${{ false }}"
    else:
        node["continue-on-error"] = True
    with pytest.raises(AssertionError):
        _assert_checks(_sync_run())


@pytest.mark.parametrize("mutation", ["wrong-input", "wrong-checkout", "disabled-child", "advisory-child", "inherit-secrets", "passed-secrets"])
def test_validation_contract_rejects_broken_reusable_dispatch(monkeypatch, mutation):
    _assert_checks(_sync_run())
    if mutation in {"wrong-input", "inherit-secrets", "passed-secrets"}:
        workflow = _mutated_yaml(monkeypatch, _SYNC)
        node = workflow["jobs"]["validate-main-tests"]
        if mutation == "wrong-input":
            node["with"]["validation_ref"] = "${{ github.sha }}"
        else:
            node["secrets"] = "inherit" if mutation == "inherit-secrets" else {"release_token": "${{ secrets.RELEASE_TOKEN }}"}
    else:
        workflow = _mutated_yaml(monkeypatch, _TESTS)
        node = workflow["jobs"]["test"]
        if mutation == "wrong-checkout":
            node["steps"][0]["with"]["ref"] = "${{ github.sha }}"
        elif mutation == "disabled-child":
            node["if"] = False
        else:
            node["continue-on-error"] = True
    with pytest.raises(AssertionError):
        _assert_checks(_sync_run())


@pytest.mark.parametrize("mutation", ["always", "missing-dependency", "advisory-check"])
def test_failure_guard_detects_rebase_bypass(monkeypatch, mutation):
    workflow = _mutated_yaml(monkeypatch, _SYNC)
    if mutation == "always":
        workflow["jobs"][_REBASE]["if"] = "always()"
    elif mutation == "missing-dependency":
        workflow["jobs"][_REBASE]["needs"].remove("validate-main-container")
    else:
        workflow["jobs"]["validate-main-container"]["continue-on-error"] = True
    with pytest.raises(AssertionError):
        test_any_unsuccessful_dependency_blocks_rebase_even_when_forced("validate-main-container", "failure", True)


@pytest.mark.parametrize("job", list(_TEST_STEPS))
@pytest.mark.parametrize("mutation", ["disabled", "advisory", "no-command", "empty-selection"])
def test_selection_receipt_rejects_ineffective_required_step(monkeypatch, job, mutation):
    workflow = _mutated_yaml(monkeypatch, _TESTS)
    step = next(step for step in workflow["jobs"][job]["steps"] if step.get("name") == _TEST_STEPS[job])
    if mutation == "disabled":
        step["if"] = False
    elif mutation == "advisory":
        step["continue-on-error"] = True
    elif mutation == "no-command":
        step["run"] = "if false; then\n" + step["run"] + "\nfi\n"
    else:
        step["run"] = step["run"].replace("tests/", "tests/absent/")
    with pytest.raises(AssertionError):
        _assert_test_selection(_sync_run())


def test_e2e_receipt_does_not_require_a_hosted_jobs_venv(tmp_path):
    for rel in _E2E_SMOKES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_smoke(): pass\n", encoding="utf-8")
    node = _sync_run()["validate-main-tests"]["jobs"]["e2e"]
    assert _selected_tests(node, "e2e", tmp_path) == _E2E_SMOKES


def test_e2e_receipt_rejects_unrequested_expensive_suites(monkeypatch):
    workflow = _mutated_yaml(monkeypatch, _TESTS)
    step = next(step for step in workflow["jobs"]["e2e"]["steps"] if step.get("name") == _TEST_STEPS["e2e"])
    step["run"] = "scripts/run_tests.sh --include-integration tests/e2e/ -- -v --tb=short"
    with pytest.raises(AssertionError):
        _assert_test_selection(_sync_run())


@pytest.mark.parametrize("condition,expected", [
    (None, False), ("true", False), ("inputs.force_rebase == true", False),
    ("always()", True), ("failure()", True), ("success()", False),
])
def test_replay_models_implicit_success_and_explicit_status_overrides(monkeypatch, condition, expected):
    workflow = _mutated_yaml(monkeypatch, _SYNC)
    workflow["jobs"][_REBASE]["if"] = condition
    run = _sync_run(force=True, outcomes={("validate-main-tests", "test"): "failure"})
    assert (run[_REBASE]["result"] == "success") is expected


def test_replay_rejects_all_skipped_required_reusable_jobs(monkeypatch):
    workflow = _mutated_yaml(monkeypatch, ".github/workflows/nix.yml")
    workflow["jobs"]["nix"]["if"] = False
    with pytest.raises(AssertionError, match="selected no successful jobs"):
        _sync_run()


def test_unit_receipt_rejects_narrowing_to_only_acp_and_ci(monkeypatch):
    workflow = _mutated_yaml(monkeypatch, _TESTS)
    step = next(step for step in workflow["jobs"]["test"]["steps"] if step.get("name") == _TEST_STEPS["test"])
    step["run"] = "scripts/run_tests.sh tests/acp_adapter/ tests/ci/ -- -q --tb=short"
    with pytest.raises(AssertionError, match="complete maintained unit tree"):
        _assert_test_selection(_sync_run())
