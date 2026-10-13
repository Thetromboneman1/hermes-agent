from pathlib import Path

import hermes_yaml


ROOT = Path(__file__).resolve().parents[2]


def test_generated_documentation_self_heals_only_the_default_branch():
    workflow = hermes_yaml.safe_load(
        (ROOT / ".github/workflows/documentation-health.yml").read_text(encoding="utf-8")
    )
    commit = next(
        step
        for step in workflow["jobs"]["documentation-health"]["steps"]
        if step.get("name") == "Commit self-healed documentation"
    )
    assert commit["if"] == (
        "github.event_name == 'push' && "
        "github.ref_name == github.event.repository.default_branch"
    )


def test_pull_requests_fail_when_generated_documentation_is_stale():
    workflow = hermes_yaml.safe_load(
        (ROOT / ".github/workflows/documentation-health.yml").read_text(encoding="utf-8")
    )
    require = next(
        step
        for step in workflow["jobs"]["documentation-health"]["steps"]
        if step.get("name") == "Require generated files in pull requests"
    )
    assert require["if"] == "github.event_name == 'pull_request'"
    assert "git status --porcelain" in require["run"]
    assert "exit 1" in require["run"]
