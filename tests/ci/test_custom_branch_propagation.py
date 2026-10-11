"""Execute the propagation workflow's real Git preparation/publication in local remotes.

Package installation and Docker run only on the hosted workflow; their required
ordering and failure gates are replayed here without contacting external services.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess

import pytest

import hermes_yaml
from tests.ci import _gha_expr as gha
from tests.ci import workflow_steps
from tests.ci.test_fork_workflow_dispatch import _sync_run
from tests.scripts.test_sync_custom_branch import commit, create_repo, git, put

ROOT = Path(__file__).resolve().parents[2]
BRANCH = "local-first-acp-optimizations"
PREPARE = "Prepare ancestry-preserving custom merge"
PUBLISH = "Publish validated custom commit"
VALIDATORS = (
    "Require custom overlay validation contract", "Set up custom test environment",
    "Validate prepared custom overlay", "Validate prepared custom documentation",
    "Build prepared custom image without publishing",
    "Smoke test prepared custom image", "Verify prepared custom image SSH policy",
)


def steps():
    workflow = hermes_yaml.safe_load(
        (ROOT / ".github/workflows/upstream-sync.yml").read_text(encoding="utf-8-sig"))
    return workflow["jobs"]["rebase-custom-branches"]["steps"]


def step(name):
    return next(item for item in steps() if item.get("name") == name)


def run_step(name, root, temporary, ctx, check=True):
    body = step(name)
    output = temporary / "step-output"
    output.write_text("", encoding="utf-8")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", gha.render(body["run"], ctx)],
        cwd=root, env={**os.environ, "RUNNER_TEMP": str(temporary), "GITHUB_OUTPUT": str(output),
                       "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": str(ctx["github"].get("run_attempt", 1)),
                       **{key: gha.to_string(gha.render(value, ctx)) for key, value in body.get("env", {}).items()}},
        text=True, capture_output=True, timeout=30, check=False,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8-sig").splitlines())
    return result, outputs


@pytest.fixture
def publication(tmp_path):
    repo = create_repo(tmp_path)
    for name in ("sync_custom_branch.py", "documentation_health.py"):
        shutil.copyfile(ROOT / "scripts" / name, repo / "scripts" / name)
    commit(repo, "validated propagation tools")
    git(repo, "checkout", "custom")
    git(repo, "branch", "-m", BRANCH)
    put(repo, "overlay.txt", "reviewed custom overlay\n")
    # The custom tree owns this entrypoint. Its actual path-selection behavior is
    # covered by the overlay suite; main tests receipt only this external boundary.
    put(repo, "scripts/ci/validate_container_overlay.sh",
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CUSTOM_VALIDATION_RECEIPT"\n'
        'exit "${CUSTOM_VALIDATION_STATUS:-0}"\n')
    put(repo, "scripts/ci/check_container_ssh_image.sh", "#!/bin/sh\nexit 0\n")
    for name in ("validate_container_overlay.sh", "check_container_ssh_image.sh"):
        (repo / "scripts/ci" / name).chmod(0o755)
    original = commit(repo, "custom overlay")
    git(repo, "checkout", "main")
    put(repo, "upstream.txt", "validated upstream feature\n")
    main = commit(repo, "validated main feature")
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main", BRANCH)
    temporary = tmp_path / "runner"
    temporary.mkdir()
    ctx = {"inputs": {"force_rebase": False},
           "needs": {"sync-main": {"outputs": {"after": main, "changed": "false"}}},
           "github": {"event": {"repository": {"default_branch": "main"}}}, "steps": {}}
    run_step("Preserve validated propagation helper", repo, temporary, ctx)
    _, outputs = run_step(PREPARE, repo, temporary, ctx)
    ctx["steps"] = {"rebase": {"outputs": outputs}}
    return repo, remote, temporary, ctx, original, main


def test_prepare_backs_up_exact_original_and_does_not_publish(publication):
    root, remote, _, ctx, original, main = publication
    outputs = ctx["steps"]["rebase"]["outputs"]
    prepared = outputs["prepared_sha"]
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == original
    assert git(remote, "rev-parse", f"refs/heads/{outputs['backup_branch']}").stdout.strip() == original
    assert git(root, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == original
    assert git(root, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
    assert git(root, "rev-list", "--parents", "-n", "1", prepared).stdout.split()[1:] == [original, main]
    assert (root / "overlay.txt").read_text(encoding="utf-8-sig") == "reviewed custom overlay\n"


@pytest.mark.parametrize("change", [None, "remote-main", "remote-custom", "local-head", "dirty-tree"])
def test_publication_requires_exact_unchanged_heads_and_clean_tested_tree(publication, change):
    root, remote, temporary, ctx, original, main = publication
    prepared = ctx["steps"]["rebase"]["outputs"]["prepared_sha"]
    mutations = {
        None: lambda: None,
        "remote-main": lambda: git(remote, "update-ref", "refs/heads/main", original),
        "remote-custom": lambda: git(remote, "update-ref", f"refs/heads/{BRANCH}", main),
        "local-head": lambda: git(root, "checkout", "--detach", original),
        "dirty-tree": lambda: put(root, "overlay.txt", "unvalidated change\n"),
    }
    mutations[change]()

    result, _ = run_step(PUBLISH, root, temporary, ctx, check=False)

    assert (result.returncode == 0) is (change is None), result.stdout + result.stderr
    expected = prepared if change is None else main if change == "remote-custom" else original
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == expected


def test_missing_custom_branch_is_a_true_skip(publication):
    root, remote, temporary, ctx, _, _ = publication
    git(remote, "update-ref", "-d", f"refs/heads/{BRANCH}")
    _, outputs = run_step(PREPARE, root, temporary, ctx)
    assert outputs == {"branch": BRANCH}
    assert "prepared_sha" not in outputs


@pytest.mark.parametrize("missing", [
    "scripts/ci/validate_container_overlay.sh", "scripts/ci/check_container_ssh_image.sh",
])
@pytest.mark.parametrize("failure", ["missing", "not-executable"])
def test_missing_overlay_contract_fails_closed(publication, missing, failure):
    root, _, temporary, ctx, _, _ = publication
    run_step(VALIDATORS[0], root, temporary, ctx)
    if failure == "missing":
        (root / missing).unlink()
    else:
        (root / missing).chmod(0o644)
    result, _ = run_step(VALIDATORS[0], root, temporary, ctx, check=False)
    assert result.returncode != 0


def test_custom_validation_runs_the_checked_out_branch_entrypoint_with_exact_sha(publication, monkeypatch):
    root, _, temporary, ctx, original, main = publication
    setup = step("Set up custom test environment")
    workflow_steps.required(setup, ctx)
    assert setup["uses"] == "./.github/actions/setup-pm"
    assert setup["with"]["test-environment"] == "true"
    assert setup["with"]["extras"] == "[]"
    relative = "scripts/ci/validate_container_overlay.sh"
    assert git(root, "cat-file", "-e", f"{main}:{relative}", check=False).returncode != 0
    assert git(root, "cat-file", "-e", f"{original}:{relative}", check=False).returncode == 0
    receipt = temporary / "custom-validation-receipt"
    monkeypatch.setenv("CUSTOM_VALIDATION_RECEIPT", str(receipt))
    run_step("Validate prepared custom overlay", root, temporary, ctx)
    assert receipt.read_text(encoding="utf-8-sig").splitlines() == [
        ctx["steps"]["rebase"]["outputs"]["prepared_sha"]]
    receipt.unlink()
    ctx["steps"]["rebase"]["outputs"]["prepared_sha"] = original
    result, _ = run_step("Validate prepared custom overlay", root, temporary, ctx, check=False)
    assert result.returncode != 0
    assert not receipt.exists()


def dispatched_steps(*, failed=None, prepared=True):
    """Replay actual step conditions, including implicit success() failure gating."""
    success, seen = True, []
    ctx = {"steps": {"rebase": {"outputs": {"prepared_sha": "a" * 40 if prepared else ""}}}}
    for body in steps():
        name, condition = body.get("name"), body.get("if")
        if name not in (*VALIDATORS, PUBLISH):
            continue
        ctx["__status__"] = {"success": success, "failure": not success, "always": True, "cancelled": False}
        explicit_status = isinstance(condition, str) and re.search(r"\b(always|success|failure|cancelled)\(", condition)
        if not ((success or explicit_status) and gha.condition(condition, ctx)):
            continue
        seen.append(name)
        if name == failed and not gha.truthy(gha.render(body.get("continue-on-error", False), ctx)):
            success = False
    return seen


@pytest.mark.parametrize("failed", VALIDATORS)
def test_every_custom_validation_failure_prevents_publication(failed):
    assert dispatched_steps() == [*VALIDATORS, PUBLISH]
    assert PUBLISH not in dispatched_steps(failed=failed)
    assert dispatched_steps(prepared=False) == []


@pytest.mark.parametrize("name,expected", [
    ("Build prepared custom image without publishing", ["build", "--tag", "hermes-agent:custom-validation", "."]),
    ("Smoke test prepared custom image", ["run", "--rm", "--tmpfs", "/opt/data", "hermes-agent:custom-validation", "--help"]),
])
def test_container_commands_use_the_prepared_image_and_disposable_data(publication, monkeypatch, name, expected):
    root, _, temporary, ctx, _, _ = publication
    binary = temporary / "bin"
    binary.mkdir()
    receipt = temporary / "docker-receipt"
    shim = binary / "docker"
    shim.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DOCKER_RECEIPT"\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DOCKER_RECEIPT", str(receipt))
    run_step(name, root, temporary, ctx)
    assert receipt.read_text(encoding="utf-8-sig").splitlines() == expected


@pytest.mark.parametrize("dirty,status", [(True, 0), (False, 7)])
def test_custom_validation_rejects_dirty_tree_and_propagates_entrypoint_failure(publication, monkeypatch, dirty, status):
    root, _, temporary, ctx, _, _ = publication
    receipt = temporary / "custom-validation-receipt"
    monkeypatch.setenv("CUSTOM_VALIDATION_RECEIPT", str(receipt))
    monkeypatch.setenv("CUSTOM_VALIDATION_STATUS", str(status))
    if dirty:
        put(root, "overlay.txt", "unvalidated working-tree change\n")
    result, _ = run_step("Validate prepared custom overlay", root, temporary, ctx, check=False)
    assert result.returncode == (1 if dirty else status)
    assert receipt.exists() is (not dirty)



def test_unchanged_main_retries_custom_after_a_failed_prior_validation(publication, monkeypatch):
    root, remote, temporary, ctx, original, main = publication
    receipt = temporary / "custom-validation-receipt"
    monkeypatch.setenv("CUSTOM_VALIDATION_RECEIPT", str(receipt))
    monkeypatch.setenv("CUSTOM_VALIDATION_STATUS", "7")
    result, _ = run_step("Validate prepared custom overlay", root, temporary, ctx, check=False)
    assert result.returncode == 7
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == original
    assert _sync_run(changed=False)["rebase-custom-branches"]["result"] == "success"

    ctx["github"]["run_attempt"] = 2
    _, outputs = run_step(PREPARE, root, temporary, ctx)
    assert outputs["prepared_sha"] != original
    ctx["steps"] = {"rebase": {"outputs": outputs}}
    monkeypatch.setenv("CUSTOM_VALIDATION_STATUS", "0")
    run_step("Validate prepared custom overlay", root, temporary, ctx)
    run_step(PUBLISH, root, temporary, ctx)
    assert git(remote, "rev-parse", "refs/heads/main").stdout.strip() == main
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == outputs["prepared_sha"]


@pytest.mark.parametrize("forced", [False, True])
def test_current_custom_branch_skips_expensive_work_unless_forced(publication, monkeypatch, forced):
    root, remote, temporary, ctx, _, _ = publication
    prepared = ctx["steps"]["rebase"]["outputs"]["prepared_sha"]
    run_step(PUBLISH, root, temporary, ctx)
    backups = git(remote, "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads/automation-backups/").stdout
    ctx["github"]["run_attempt"] = 2
    ctx["inputs"]["force_rebase"] = forced
    assert _sync_run(changed=False, force=forced)["rebase-custom-branches"]["result"] == "success"

    _, outputs = run_step(PREPARE, root, temporary, ctx)

    assert ("prepared_sha" in outputs) is forced
    assert ("backup_branch" in outputs) is forced
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == prepared
    if not forced:
        assert git(remote, "for-each-ref", "--format=%(refname):%(objectname)", "refs/heads/automation-backups/").stdout == backups
        assert dispatched_steps(prepared=False) == []
        return
    assert outputs["prepared_sha"] == prepared
    assert git(remote, "rev-parse", f"refs/heads/{outputs['backup_branch']}").stdout.strip() == prepared
    receipt = temporary / "forced-validation-receipt"
    monkeypatch.setenv("CUSTOM_VALIDATION_RECEIPT", str(receipt))
    ctx["steps"] = {"rebase": {"outputs": outputs}}
    run_step("Validate prepared custom overlay", root, temporary, ctx)
    assert receipt.read_text(encoding="utf-8-sig").splitlines() == [prepared]
    assert dispatched_steps(prepared=True) == [*VALIDATORS, PUBLISH]
    run_step(PUBLISH, root, temporary, ctx)
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == prepared



def test_unchanged_main_also_propagates_when_custom_is_strictly_behind(publication):
    root, remote, temporary, ctx, original, _ = publication
    main = ctx["steps"]["rebase"]["outputs"]["prepared_sha"]
    git(root, "push", "origin", f"{main}:refs/heads/main")
    git(root, "merge-base", "--is-ancestor", original, main)
    ctx["needs"]["sync-main"]["outputs"]["after"] = main
    ctx["github"]["run_attempt"] = 2

    _, outputs = run_step(PREPARE, root, temporary, ctx)

    prepared = outputs["prepared_sha"]
    assert prepared != original
    assert git(root, "rev-list", "--parents", "-n", "1", prepared).stdout.split()[1:] == [original, main]
    assert git(remote, "rev-parse", f"refs/heads/{BRANCH}").stdout.strip() == original
