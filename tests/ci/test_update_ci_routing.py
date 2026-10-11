"""Retained upstream classifier and ownership contracts.

The fork deliberately dispatches its bounded checks without classifier lanes.
Live workflow dispatch, selection and failure propagation are exercised in
``test_fork_workflow_dispatch.py``. These tests retain the classifier's import
ownership, cross-language fixture and expression/receipt contracts without
claiming the absent upstream jobs run in this fork.
"""

from __future__ import annotations

import ast
import importlib.util
import itertools
import json
import os
import re
import subprocess
import sys
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from hermes_platform.host.facts import native_arch
from tests.ci import _gha_expr as gha
from tests.ci import workflow_steps

_REPO = Path(__file__).resolve().parents[2]
_CLASSIFIER = _REPO / "scripts" / "ci" / "classify_changes.py"
_spec = importlib.util.spec_from_file_location("classify_changes_routing", _CLASSIFIER)
assert _spec is not None and _spec.loader is not None
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


# Portable receipt probes remain eligible for the upstream native Windows selector.
# This fork runs its deployed test jobs on Linux; selector eligibility does not
# claim that a Windows workflow is deployed here.
_NATIVE_WINDOWS_PLATFORMS = pytest.mark.platforms("linux", "windows")
# Not Windows on arm64: Git for Windows ships an x86-64 MSYS bash.exe there, run under emulation,
# and with the os-tests lane's 16 workers it dies with 0xC000026F (STATUS_WX86_INTERNAL_ERROR) or
# 0xC0000005 before running a line, on replays of unchanged workflows: one copy of this file red
# in ~3 when 16 run at once, against 0/64 on x64 Windows under the same load. The emulator's
# crash is not the replay's to assert on; the x64 Windows row runs the same layout natively.
_EMULATED_BASH = pytest.mark.skipif(
    sys.platform == "win32" and native_arch() == "arm64",
    reason="Git Bash is x86-64 under emulation on Windows arm64 and crashes under parallel load")


def _NATIVE_WINDOWS_TOO(test):
    return _NATIVE_WINDOWS_PLATFORMS(_EMULATED_BASH(test))
_lister_spec = importlib.util.spec_from_file_location("list_os_marked", _REPO / "scripts/ci/list_os_marked_tests.py")
assert _lister_spec is not None and _lister_spec.loader is not None
lister = importlib.util.module_from_spec(_lister_spec)
_lister_spec.loader.exec_module(lister)


# -- the real classifier, as the retained composite runs it ---------------------------------------------


def _real_classifier(paths: list[str]) -> dict[str, bool]:
    """``scripts/ci/classify_changes.py`` as a process, stdin -> ``key=value`` lines."""
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_OUTPUT", "GITHUB_EVENT_PATH")}
    env["EVENT_NAME"] = "pull_request_replay"  # never `pull_request`: no gh calls from a test
    out = subprocess.run([sys.executable, str(_CLASSIFIER)], input="\n".join(paths) + "\n",
                         capture_output=True, text=True, env=env, check=True, timeout=60).stdout
    pairs = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {k: v == "true" for k, v in pairs.items()}


# -- workflow replay ---------------------------------------------------------------------


@cache
def _yaml(rel: str) -> dict:
    import hermes_yaml

    return hermes_yaml.safe_load((_REPO / rel).read_text(encoding="utf-8-sig"))


def _classifier_action_outputs(lanes: dict[str, bool]) -> dict[str, Any]:
    """The real classifier's lines forwarded by the retained composite action."""
    raw = {k: gha.to_string(v) for k, v in lanes.items()}
    action = _yaml(".github/actions/detect-changes/action.yml")
    return {k: gha.render(v["value"], {"steps": {"classify": {"outputs": raw}}})
            for k, v in action["outputs"].items()}


_GATED = ("python", "desktop_updater", "e2e", "e2e_upgrade", "e2e_desktop_update")


def test_replay_evaluator_follows_actions_semantics():
    ctx = {"needs": {"detect": {"outputs": {"python": "false", "e2e": "true"}}}, "inputs": {"flag": True}}
    assert gha.condition("needs.detect.outputs.python == 'true' || needs.detect.outputs.e2e == 'true'", ctx)
    assert not gha.condition("needs.detect.outputs.python == 'true'", ctx)
    assert not gha.evaluate("'true' == true", ctx)  # a string output is not the boolean
    assert gha.render('x "${{ inputs.flag }}" y', ctx) == 'x "true" y'
    assert gha.render("${{ inputs.flag && '1' || '' }}", ctx) == "1"
    assert gha.render("${{ inputs.missing && '1' || '' }}", ctx) == ""
    with pytest.raises(gha.Unsupported):
        gha.condition("hashFiles('x') != ''", ctx)  # unmodelled: fail, never guess


@pytest.mark.parametrize("combo", list(itertools.product((False, True), repeat=len(_GATED))),
                         ids=lambda c: "-".join(n for n, on in zip(_GATED, c) if on) or "none")
def test_classifier_action_preserves_each_lane_whatever_the_other_lanes_say(combo):
    lanes = {k: False for k in cc.classify(["README.md"])}
    lanes.update(zip(_GATED, combo))
    outputs = _classifier_action_outputs(lanes)
    assert outputs == {name: gha.to_string(value) for name, value in lanes.items()}


# One-file changes from the review's direct classifier run (acceptance/classifier.json),
# and the upstream lanes each must continue to select.
_R15_CASES = {
    "scripts/build/desktop.mjs": ("e2e_upgrade", "e2e_desktop_update"),
    "scripts/build/web.mjs": ("e2e_upgrade", "e2e_desktop_update"),
    "hermes_cli/_subprocess_compat.py": ("e2e_upgrade", "e2e_desktop_update"),
    "hermes_cli/desktop_build_lock.py": ("e2e_upgrade", "e2e_desktop_update"),
    "hermes_cli/memory_provider_migration.py": ("e2e_upgrade", "e2e_desktop_update"),
    "hermes_cli/source_build.py": ("e2e_upgrade", "e2e_desktop_update"),
    "hermes_cli/source_completion.py": ("e2e_upgrade", "e2e_desktop_update"),
    "apps/desktop/electron/update-marker.ts": ("desktop_updater", "e2e_desktop_update"),
    "apps/desktop/electron/handoff-result.ts": ("desktop_updater", "e2e_desktop_update"),
}


@pytest.mark.parametrize("path,lanes", list(_R15_CASES.items()))
def test_update_owner_change_selects_its_upstream_lanes(path, lanes):
    assert (_REPO / path).is_file(), f"{path} moved: update this table"
    classified = _real_classifier([path])
    for lane in lanes:
        assert classified[lane], f"{path}: classifier leaves {lane} off"


# Review R6 m11: update-path files whose suites the classifier left off. Some land in a sibling
# PR of the update stack (update-marker-gate.ts), so the table routes paths, not files on disk.
_R6_CASES = {
    # The Desktop gate's marker reader: the hand-off script's contract (desktop_updater) and the
    # Desktop update E2E that drives the gate.
    "apps/desktop/electron/update-marker-gate.ts": ("desktop_updater", "e2e_desktop_update"),
    # Stops the backend before a remote update: typecheck/vitest and the hand-off script tests.
    "apps/desktop/electron/remote-lifecycle.ts": ("frontend", "desktop_updater"),
    # The Tauri updater's marker claim: cargo, the only CI that builds it (review CI3).
    "apps/bootstrap-installer/src-tauri/src/marker.rs": ("rust",),
    # The launchers reach the launch repair: the Desktop update relaunches through them too.
    "hermes_cli/_launchers.py": ("e2e_upgrade", "e2e_desktop_update"),
}


@pytest.mark.parametrize("path,lanes", list(_R6_CASES.items()))
def test_update_path_change_selects_every_suite_that_exercises_it(path, lanes):
    classified = _real_classifier([path])
    for lane in lanes:
        assert classified[lane], f"{path}: classifier leaves {lane} off"


# Review CI2/CI3: update-path files the classifier sent to the wrong suites.
_CI3_CASES = {
    # /api/health `commit`: host-backend-attach compares it before attaching after an update.
    "hermes_cli/web_routers/status.py": ("python", "e2e_desktop_update"),
    # The SSH remote's marker judge/gate: typecheck/vitest and the marker contract's script tests.
    "apps/desktop/electron/remote-update-marker-programs.ts": ("frontend", "desktop_updater"),
    "apps/desktop/electron/remote-update-marker-programs.test.ts": ("frontend", "desktop_updater"),
}


@pytest.mark.parametrize("path,lanes", list(_CI3_CASES.items()))
def test_update_path_change_selects_its_consumer_lanes(path, lanes):
    test_update_path_change_selects_every_suite_that_exercises_it(path, lanes)


_TAURI_UPDATER = tuple(f"apps/bootstrap-installer/src-tauri/src/{name}.rs" for name in ("marker", "update", "paths"))


@pytest.mark.parametrize("path", _TAURI_UPDATER)
def test_tauri_updater_routes_as_one_unit_to_the_lane_that_builds_it(path):
    """No slow lane builds the bootstrap installer: starting one for its updater runs nothing of it."""
    assert (_REPO / path).is_file(), f"{path} moved: update this table"
    classified = _real_classifier([path])
    assert classified["rust"], f"{path}: cargo test never runs"
    started = [lane for lane in ("e2e", "e2e_upgrade", "e2e_desktop_update", "e2e_desktop_core") if classified[lane]]
    assert not started, f"{path}: starts {started}, which never build the Tauri installer"


def test_gateway_status_routing_names_the_stamp_not_its_prefix_siblings():
    assert _real_classifier(["gateway/status.py"])["e2e_upgrade"]
    assert not _real_classifier(["gateway/status_phrases.py"])["e2e_upgrade"], \
        "status_phrases.py (turn-status wording) is not on the update path"


def test_classifier_action_declares_every_classifier_output():
    action = _yaml(".github/actions/detect-changes/action.yml")
    assert set(action["outputs"]) == set(_real_classifier(["README.md"]))


# -- shared cross-language fixtures (review D14) --------------------------------------------

_CORPUS = "tests/fixtures/update_marker_corpus.json"
_TEST_FILE = re.compile(r"(^tests/(?!ci/).*\.py$)|(_tests?\.rs$)|(\.test\.[cm]?[jt]sx?$)")


def test_marker_corpus_change_selects_every_language_that_reads_it():
    """Keep the shared Python, Rust, frontend and hand-off classifier contract."""
    classified = _real_classifier([_CORPUS])
    for lane in ("python", "rust", "frontend", "desktop_updater"):
        assert classified[lane], f"{_CORPUS}: classifier leaves its {lane} reader off"


def _tracked_mentions(name: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(_REPO), "grep", "-l", "-F", name, "--", "."],
                             capture_output=True, text=True, timeout=60, check=False)
    except OSError:
        pytest.skip("git unavailable: cannot list the fixture's consumers")
    if out.returncode not in (0, 1):
        pytest.skip(f"not a git checkout: {out.stderr.strip()[:200]}")
    return out.stdout.split()


# Listed consumers that land in a named sibling PR merged BEFORE this one (the update batch
# lands LOCK, COMMIT, WIN, DESK, SCRIPTS, POST, then this CI PR). Only these may be absent,
# and only while the sibling is unmerged: an entry whose file exists fails the guard, so the
# table empties when the siblings land and a later deletion of a consumer turns it red.
_LANDS_IN_SIBLING_PR: dict[str, str] = {}  # #132345 and #132354 landed


def test_every_test_that_reads_a_shared_fixture_is_routed_by_it():
    """Derived, not remembered: every test file in the tree that names a shared fixture is
    a listed consumer, and the fixture selects each test lane that consumer's own edit would."""
    assert _CORPUS in cc._SHARED_FIXTURE_CONSUMERS
    every_listed = {c for listed in cc._SHARED_FIXTURE_CONSUMERS.values() for c in listed}
    stale = sorted(set(_LANDS_IN_SIBLING_PR) - every_listed)
    assert not stale, f"sibling-PR allowance names no listed consumer: {stale}"
    landed = sorted(c for c in _LANDS_IN_SIBLING_PR if (_REPO / c).is_file())
    assert not landed, f"sibling landed: delete its _LANDS_IN_SIBLING_PR allowance: {landed}"
    for fixture, listed in cc._SHARED_FIXTURE_CONSUMERS.items():
        assert (_REPO / fixture).is_file(), f"missing shared fixture: {fixture}"
        assert listed, f"{fixture}: no listed consumers"
        absent = [c for c in listed if not (_REPO / c).is_file()]
        unowned = [c for c in absent if c not in _LANDS_IN_SIBLING_PR]
        assert not unowned, f"{fixture}: missing consumer(s) no sibling PR lands: {unowned}"
        assert len(absent) < len(listed), f"{fixture}: every listed consumer is absent"
        readers = {p for p in _tracked_mentions(Path(fixture).name) if _TEST_FILE.search(p)}
        assert readers, f"{fixture}: no discovered test readers"
        assert readers <= set(listed), f"{fixture}: unlisted consumer(s) {sorted(readers - set(listed))}"
        on = cc.classify([fixture])
        for consumer in listed:
            own = cc.classify([consumer])
            lost = [lane for lane in cc._FIXTURE_CONSUMER_LANES if own[lane] and not on[lane]]
            assert not lost, f"{fixture}: editing it skips {lost}, which run {consumer}"


def test_sibling_allowance_expires_once_its_consumer_is_in_the_tree(monkeypatch):
    """An allowance that outlives its sibling's merge would excuse that consumer's later deletion."""
    present = next(c for c in cc._SHARED_FIXTURE_CONSUMERS[_CORPUS] if (_REPO / c).is_file())
    monkeypatch.setitem(_LANDS_IN_SIBLING_PR, present, "a sibling that already landed")
    with pytest.raises(AssertionError, match="sibling landed"):
        test_every_test_that_reads_a_shared_fixture_is_routed_by_it()


def test_native_windows_selector_includes_the_portable_receipt_probe():
    selected = {p.resolve() for p in lister.find_marked_files("windows", _REPO / "tests")}
    assert Path(__file__).resolve() in selected
    probe = test_replay_starts_the_python_and_tools_hosted_steps_call
    assert any(m.name == "platforms" and any(
        "windows" in lister.spec_hosts(str(a).lower()) for a in m.args)
        for m in getattr(probe, "pytestmark", []))


# -- replay guard sensitivity: mutate data, not the evaluator -------------------------------

def _mutated_yaml(monkeypatch, rel):
    from copy import deepcopy

    original = _yaml
    changed = deepcopy(original(rel))
    monkeypatch.setattr(sys.modules[__name__], "_yaml", lambda path: changed if path == rel else original(path))
    return changed


def test_classifier_action_replay_rejects_a_dropped_lane(monkeypatch):
    action = _mutated_yaml(monkeypatch, ".github/actions/detect-changes/action.yml")
    action["outputs"]["desktop_updater"]["value"] = "${{ false }}"
    with pytest.raises(AssertionError):
        test_classifier_action_preserves_each_lane_whatever_the_other_lanes_say(
            (True,) * len(_GATED))


_PROBE_STEP = {"run": r'''python3 -c 'import sys; print("py3=" + sys.executable)' >> "$GITHUB_OUTPUT"
python -c 'import sys; print("py=" + sys.prefix)' >> "$GITHUB_OUTPUT"
echo "tools=$(printf 'b\na\n' | sort | tr -d '\r' | paste -sd , -)$(find . -maxdepth 0)" >> "$GITHUB_OUTPUT"
'''}


@_NATIVE_WINDOWS_TOO
def test_replay_starts_the_python_and_tools_hosted_steps_call():
    """Every host (native Windows included) runs python3/python and the coreutils selection steps use."""
    out = workflow_steps.outputs(_PROBE_STEP, {})
    assert (out["py"], out["tools"]) == (sys.prefix, "a,b.")
    assert subprocess.run([out["py3"], "-c", "import sys; print(sys.prefix)"], capture_output=True,
                          text=True, check=True, timeout=60).stdout.strip() == sys.prefix


@pytest.mark.platforms("posix")
def test_replay_python3_is_the_replay_interpreter_when_its_dir_has_only_python(tmp_path):
    """A Windows venv dir holds only ``python``: python3 must not fall through to a host interpreter."""
    interpreter = tmp_path / "Scripts" / "python"
    interpreter.parent.mkdir()
    interpreter.symlink_to(os.path.realpath(sys.executable))
    probe = ("import json, sys; from tests.ci import workflow_steps; "
             "print(json.dumps([sys.executable, workflow_steps.outputs(json.loads(sys.argv[1]), {})]))")
    env = {**os.environ, "PYTHONPATH": str(_REPO)}
    result = subprocess.run([str(interpreter), "-c", probe, json.dumps(_PROBE_STEP)], cwd=_REPO, env=env,
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    replay_python, out = json.loads(result.stdout)
    assert out["py3"] == replay_python


@pytest.mark.parametrize("missing", ["fixture", "consumer", "readers", "listed"])
def test_shared_fixture_guard_rejects_phantom_graph(monkeypatch, tmp_path, missing):
    fixture = "tests/fixtures/owned_corpus.json"
    consumer = "tests/test_owned_corpus.py"
    for rel in (fixture, consumer):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}" if rel == fixture else "def test_reader(): pass\n", encoding="utf-8")
    if missing in ("fixture", "consumer"):
        (tmp_path / (fixture if missing == "fixture" else consumer)).unlink()
    monkeypatch.setattr(sys.modules[__name__], "_REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_CORPUS", fixture)
    monkeypatch.setattr(cc, "_SHARED_FIXTURE_CONSUMERS", {fixture: () if missing == "listed" else (consumer,)})
    monkeypatch.setattr(sys.modules[__name__], "_tracked_mentions", lambda _: [] if missing in ("readers", "listed") else [consumer])
    with pytest.raises(AssertionError):
        test_every_test_that_reads_a_shared_fixture_is_routed_by_it()


@pytest.mark.parametrize("case,error", [
    ("sibling-absent", None),  # the stacked PR: one reader here, one landing in a sibling
    ("unowned-absent", "no sibling PR lands"),
    ("only-sibling", "every listed consumer is absent"),
    ("stale-allowance", "names no listed consumer"),
])
def test_sibling_pr_allowance_admits_only_named_pending_consumers(monkeypatch, tmp_path, case, error):
    fixture, here, pending = ("tests/fixtures/owned_corpus.json", "tests/test_owned_corpus.py",
                              "tests/scripts/test_sibling_corpus.py")
    (tmp_path / fixture).parent.mkdir(parents=True)
    (tmp_path / fixture).write_text("{}", encoding="utf-8")
    if case != "only-sibling":
        (tmp_path / here).write_text("def test_reader(): pass\n", encoding="utf-8")
    allowance = {} if case == "unowned-absent" else {pending: "#1 sibling"}
    if case == "stale-allowance":
        allowance["tests/test_removed_corpus.py"] = "#2 sibling"
    monkeypatch.setattr(sys.modules[__name__], "_REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "_CORPUS", fixture)
    monkeypatch.setattr(sys.modules[__name__], "_LANDS_IN_SIBLING_PR", allowance)
    monkeypatch.setattr(cc, "_SHARED_FIXTURE_CONSUMERS",
                        {fixture: (pending,) if case == "only-sibling" else (here, pending)})
    monkeypatch.setattr(sys.modules[__name__], "_tracked_mentions", lambda _: [here])
    if error is None:
        test_every_test_that_reads_a_shared_fixture_is_routed_by_it()
    else:
        with pytest.raises(AssertionError, match=error):
            test_every_test_that_reads_a_shared_fixture_is_routed_by_it()


# -- ownership: the update entry points' real imports ---------------------------------------

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".worktrees", "tests", "website", "__pycache__"}


def _product_python() -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(_REPO):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        out += [Path(dirpath) / f for f in filenames if f.endswith(".py")]
    return out


def _module_file(module: str) -> Path | None:
    base = _REPO / module.replace(".", "/")
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


@cache
def _imports(path: Path) -> frozenset[str]:
    """Repo modules ``path`` imports anywhere (module level or lazily in a function)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError):
        return frozenset()
    package = path.relative_to(_REPO).with_suffix("").parts[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = list(package[: len(package) - (node.level - 1)])
                base = ".".join([*parts, base] if base else parts)
            names.add(base)
            names.update(f"{base}.{a.name}" for a in node.names)
    # Importing a.b.c runs a/__init__.py and a/b/__init__.py first.
    names |= {".".join(n.split(".")[:i]) for n in names for i in range(1, n.count(".") + 1)}
    found = set()
    for name in names:
        target = _module_file(name)
        if target is not None and target != path:
            found.add(target.relative_to(_REPO).as_posix())
    return frozenset(found)


def _entry_modules(prefixes: tuple[str, ...]) -> list[Path]:
    hits = [p for p in _product_python() if p.relative_to(_REPO).as_posix().startswith(prefixes)]
    assert hits, f"no module matches {prefixes}: the entry-point list went stale"
    return sorted(hits)


@cache
def _importers() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _product_python():
        for target in _imports(path):
            counts[target] = counts.get(target, 0) + 1
    return counts


def _unowned(entries: tuple[str, ...], lane: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for module in _entry_modules(entries):
        for target in _imports(module):
            if not cc.classify([target])[lane] and target not in cc._SHARED_HUBS:
                out.setdefault(target, []).append(module.relative_to(_REPO).as_posix())
    return out


@pytest.mark.parametrize("entries,lane", [
    ("_UPDATE_ENTRY_POINTS", "e2e_upgrade"),
    ("_DESKTOP_BUILD_ENTRY_POINTS", "e2e_desktop_update"),
])
def test_every_module_an_update_entry_point_imports_starts_its_suite(entries, lane):
    """Add the module to the classifier's owner list for this lane. Only a shared
    hub (``_SHARED_HUBS``: imported across the product, covered by the unit lanes
    and by this suite on every push to main) may stay out."""
    missing = _unowned(getattr(cc, entries), lane)
    assert missing == {}, f"{lane}: imported by the update path but not routed to it: {missing}"


def test_entry_points_themselves_start_their_suites():
    for module in _entry_modules(cc._UPDATE_ENTRY_POINTS):
        assert cc.classify([module.relative_to(_REPO).as_posix()])["e2e_upgrade"], module
    for module in _entry_modules(cc._DESKTOP_BUILD_ENTRY_POINTS):
        rel = module.relative_to(_REPO).as_posix()
        assert cc.classify([rel])["e2e_desktop_update"] and cc.classify([rel])["e2e_upgrade"], rel


def test_shared_hubs_are_real_hubs_still_on_the_update_path():
    """The hub exemption cannot hide an update-specific module: each hub must be
    imported by many product modules, and still be imported by an entry point."""
    counts = _importers()
    thin = {h: counts.get(h, 0) for h in cc._SHARED_HUBS if counts.get(h, 0) < cc.HUB_MIN_IMPORTERS}
    assert thin == {}, f"not a shared hub (fewer than {cc.HUB_MIN_IMPORTERS} importers): own it instead"
    reached = set()
    for entries in (cc._UPDATE_ENTRY_POINTS, cc._DESKTOP_BUILD_ENTRY_POINTS):
        for module in _entry_modules(entries):
            reached |= _imports(module)
    assert set(cc._SHARED_HUBS) - reached == set(), "stale hub exemption: no entry point imports it"


# -- ownership: the JavaScript compilers an update runs -------------------------------------

_SCRIPT_LITERAL = re.compile(r"""["']((?:scripts/build|apps/desktop/scripts)/[\w./-]+\.(?:mjs|js|py|ps1))["']""")
_ESM_IMPORT = re.compile(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""")
_STEP = re.compile(r"""step\(\s*['"]([\w./-]+\.mjs)['"]""")


def _js_closure(seeds: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        rel = stack.pop()
        if rel in seen:
            continue
        path = _REPO / rel
        assert path.is_file(), f"{rel} is referenced on the update path but does not exist"
        seen.add(rel)
        if not rel.endswith((".mjs", ".js")):
            continue
        text = path.read_text(encoding="utf-8-sig")
        for spec in _ESM_IMPORT.findall(text):
            target = (path.parent / spec).resolve()
            if target.is_file() and _REPO in target.parents:
                stack.append(target.relative_to(_REPO).as_posix())
        stack += _STEP.findall(text)
    return seen


def _python_script_seeds(entries: tuple[str, ...]) -> set[str]:
    seeds = set()
    for module in _entry_modules(entries):
        seeds |= set(_SCRIPT_LITERAL.findall(module.read_text(encoding="utf-8-sig")))
    return seeds


def test_build_scripts_the_update_runs_start_the_upgrade_suite():
    reached = _js_closure(_python_script_seeds(cc._UPDATE_ENTRY_POINTS))
    assert "scripts/build/web.mjs" in reached, "seed scan broke: source_build runs web.mjs"
    unowned = sorted(f for f in reached if not cc.classify([f])["e2e_upgrade"])
    assert unowned == []


def test_desktop_build_scripts_start_the_desktop_update_suite():
    """`hermes desktop --build-only` (the update's Desktop rebuild) runs
    apps/desktop's `build` script, which steps through scripts/build/desktop.mjs."""
    package = json.loads((_REPO / "apps/desktop/package.json").read_text(encoding="utf-8-sig"))
    build = re.search(r"node\s+(\S+\.mjs)", package["scripts"]["build"])
    assert build, package["scripts"]["build"]
    seeds = {("apps/desktop/" + build.group(1)).replace("/./", "/")}
    seeds |= _python_script_seeds(cc._DESKTOP_BUILD_ENTRY_POINTS)
    reached = _js_closure(seeds)
    assert "scripts/build/desktop.mjs" in reached, "seed scan broke: the Desktop build runs desktop.mjs"
    unowned = sorted(f for f in reached if not cc.classify([f])["e2e_desktop_update"])
    assert unowned == []


# The runner's strict-acceptance environment remains a generic contract even
# though the fork does not deploy the upstream expensive strict E2E workflows.

def test_run_tests_forwards_the_strict_switch():
    """run_tests.sh starts pytest under `env -i`: an unlisted variable never arrives."""
    text = (_REPO / "scripts/run_tests.sh").read_text(encoding="utf-8-sig")
    allow = re.search(r"for _test_var in (.*?); do", text, re.DOTALL)
    assert allow and "HERMES_E2E_STRICT_ACCEPTANCE" in allow.group(1).split()
