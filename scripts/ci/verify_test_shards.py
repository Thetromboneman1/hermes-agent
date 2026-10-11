"""Fail-closed receipt gate for the two complete-unit-suite CI shards (stdlib only)."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from scripts import run_tests_parallel as runner


def verify(reports: Path, repo: Path, sha: str, result: str) -> str:
    if result != "success":
        raise ValueError(f"unit shards did not succeed: {result}")
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True, timeout=10).strip()
    if checkout != sha:
        raise ValueError("aggregate checkout does not match the required SHA")
    # Preserve the original full-unit selection, including the runner's Docker exclusion.
    args = argparse.Namespace(files=None, files_from=None, paths_positional=["tests/"],
                              paths="tests", include_integration=False)
    files, _ = runner._select_files(args, ["--ignore=tests/integration", "--ignore=tests/e2e"], repo)
    expected = Counter(file.relative_to(repo).as_posix() for file in files)
    if not expected:
        raise ValueError("canonical unit inventory is empty")
    actual, counts, retries, flaky = Counter(), [], 0, 0
    for index in (1, 2):
        directory = reports / f"unit-tests-{index}"
        data = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        if data["sha"] != sha or data["slice"] != f"{index}/2" or data["exit_code"] != 0:
            raise ValueError(f"shard {index}: wrong SHA/slice or unsuccessful receipt")
        selected = Counter(data["files"])
        partition = runner._slice_files(files, index, 2, {}, repo)
        if selected != Counter(file.relative_to(repo).as_posix() for file in partition):
            raise ValueError(f"shard {index}: selection differs from its complete canonical partition")
        results = data["results"]
        if Counter(item["file"] for item in results) != selected:
            raise ValueError(f"shard {index}: incomplete or duplicated completed files")
        if any(item["exit_code"] != 0 or type(item["retries"]) is not int or item["retries"] < 0
               for item in results):
            raise ValueError(f"shard {index}: failed file or invalid retry record")
        if not (directory / "output.log").stat().st_size:
            raise ValueError(f"shard {index}: missing runner/retry output")
        retries += sum(item["retries"] for item in results)
        flaky += sum(item["retries"] > 0 for item in results)
        actual.update(selected)
        counts.append(sum(selected.values()))
    if actual != expected:
        raise ValueError("unit shards overlap or omit canonical files")
    return (f"Complete unit coverage at {sha}: {counts[0]} + {counts[1]} = {sum(expected.values())} files; "
            f"no overlaps or omissions; {retries} retries across {flaky} flaky files (see shard logs).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    try:
        print(verify(args.reports, Path(__file__).resolve().parents[2], args.sha, args.result))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Unit coverage verification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
