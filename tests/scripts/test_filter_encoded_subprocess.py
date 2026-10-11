"""The supply-chain scanner distinguishes encoded commands from text transport."""

from pathlib import Path
import os
import shlex
import subprocess
import sys

import pytest
from ruamel.yaml import YAML

from scripts.ci.filter_encoded_subprocess import is_critical_candidate


def candidate(arguments, *, suffix="", method="run", number=1):
    return f"{number}:+subprocess.{method}({arguments}){suffix}"


@pytest.mark.parametrize("arguments,suffix", [
    ('argv, input=message.encode("utf-8"), check=False', ""),
    ('argv, input=message.encode(), check=False', ""),
    ('argv, input=message.encode("utf8", "strict")', ""),
    ('args=argv, input=message.encode("utf-8")', ""),
    ('argv, capture_output=True', '.stdout.decode("utf-8", "replace")'),
    ('argv, capture_output=True', '.stderr.decode("utf-8")'),
    ('argv, input="café".encode("utf-8"), capture_output=True', '.stdout.decode("utf-8")'),
])
def test_text_io_is_not_an_encoded_command(arguments, suffix):
    assert not is_critical_candidate(candidate(arguments, suffix=suffix))


@pytest.mark.parametrize("method", ["Popen", "call", "run"])
@pytest.mark.parametrize("command", [
    "base64.b64decode(payload)",
    "base64.urlsafe_b64decode(payload)",
    "payload.decode('utf-8')",
    "payload.encode('utf-8')",
    "chr(115) + chr(104)",
    r"'\x73\x68'",
    "['sh', '-c', base64.b64decode(payload)]",
    "encoded_command",
])
def test_encoded_command_stays_critical_with_benign_input(method, command):
    arguments = f"{command}, input=message.encode('utf-8')"
    assert is_critical_candidate(candidate(arguments, method=method))


def test_named_encoded_command_stays_critical():
    assert is_critical_candidate(candidate("args=base64.b64decode(payload), input=text.encode('utf-8')"))


@pytest.mark.parametrize("arguments,suffix", [
    ("*argv, input=message.encode('utf-8')", ""),
    ("['sh', *argv], input=message.encode('utf-8')", ""),
    ("argv, **options", ".stdout.decode('utf-8')"),
    ("argv, args=other, input=message.encode('utf-8')", ""),
    ("input=message.encode('utf-8')", ""),
    ("argv, input=message.encode(codec)", ""),
    ("argv, input=message.encode(encoding='utf-8')", ""),
    ("argv, input=message.encode('base64')", ""),
    ("argv, input=base64.b64decode(payload)", ""),
    ("argv, input=decoded_payload.encode('utf-8')", ""),
    ("argv, input=message.encode('utf-8')", "; prepare_encoded_payload()"),
    ("argv, input=message.encode('utf-8')", " # base64 payload"),
])
def test_ambiguous_or_additional_markers_stay_critical(arguments, suffix):
    assert is_critical_candidate(candidate(arguments, suffix=suffix))


def test_multiple_subprocess_calls_stay_critical():
    first = candidate("argv, input=message.encode('utf-8')")
    second = candidate("other").partition(":+")[2]
    assert is_critical_candidate(f"{first}; {second}")


@pytest.mark.parametrize("line", [
    candidate("argv, input=message.encode('utf-8')")[:-1],
    candidate("argv, input=message.encode('utf-8')").replace("1:+", "unexpected:"),
    candidate("argv, input=message.encode('utf-8')").replace("1:+", "1:+statement = \""),
    "1:+" + repr(candidate("argv, input=message.encode('utf-8')")),
    candidate("argv, input=message.encode('utf-8')") + "\0",
])
def test_unparseable_or_non_python_candidates_stay_critical(line):
    assert is_critical_candidate(line)


@pytest.mark.parametrize("number", [703703, 703734])
def test_reported_log_decoding_false_positives(number):
    arguments = r'''f"grep -h '\\[{sid}\\]' ~/.hermes/logs/agent.log | grep 'API call #'", shell=True, capture_output=True, check=False'''
    line = candidate(arguments, suffix='.stdout.decode("utf-8", "replace")', number=number)
    line = line.replace(":+", ":+log = ", 1)
    assert not is_critical_candidate(line)


def test_reported_bom_aware_delivery_false_positive():
    line = candidate('argv, input=stream.read().encode("utf-8"), check=False, env=env',
                     suffix=".returncode", number=2098945)
    line = line.replace(":+", ":+                return ", 1)
    assert not is_critical_candidate(line)


def test_cli_preserves_critical_lines_and_original_diff_numbers():
    ordinary = candidate('argv, input=message.encode("utf-8")', number=101)
    critical = candidate("base64.b64decode(payload)", number=202)
    malformed = candidate("argv, input=message.encode('utf-8')", number=303)[:-1]
    script = Path(__file__).resolve().parents[2] / "scripts/ci/filter_encoded_subprocess.py"
    result = subprocess.run([sys.executable, "-I", str(script)],
                            input=f"{ordinary}\n{critical}\n{malformed}\n\n",
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{critical}\n{malformed}\n"

@pytest.mark.parametrize("wrapper", ["exec", "eval", "consume"])
def test_outer_call_cannot_hide_execution_of_decoded_output(wrapper):
    source = candidate("argv, capture_output=True", suffix=".stdout.decode('utf-8')").partition(":+")[2]
    assert is_critical_candidate(f"1:+{wrapper}({source})")


def test_multiple_statements_stay_critical():
    source = candidate("argv, input=message.encode('utf-8')")
    assert is_critical_candidate(f"{source}; other()")


def test_unknown_output_codec_stays_critical():
    assert is_critical_candidate(candidate("argv", suffix=".stdout.decode(codec)"))


def test_duplicate_input_keywords_stay_critical():
    arguments = "argv, input=message.encode('utf-8'), input=other.encode('utf-8')"
    assert is_critical_candidate(candidate(arguments))


def run_workflow_scan(tmp_path, additions, *, helper_fails=False):
    """Execute the checked-in scan shell and its failure gate with a synthetic git diff."""
    root = Path(__file__).resolve().parents[2]
    workflow = YAML(typ="safe").load((root / ".github/workflows/supply-chain-audit.yml").read_text(encoding="utf-8-sig"))
    steps = workflow["jobs"]["scan"]["steps"]
    scan = next(step["run"] for step in steps if step.get("id") == "scan")
    gate = next(step["run"] for step in steps if step["name"] == "Fail on critical findings")
    scan = scan.replace("$" + "{{ github.event.pull_request.base.sha }}", "BASE")
    scan = scan.replace("$" + "{{ github.event.pull_request.head.sha }}", "HEAD")
    scan = scan.replace("/tmp/findings.md", shlex.quote(str(tmp_path / "findings.md")))
    fake_git = "git() { if [ \"$2\" != --name-only ]; then printf '%s\\n' \"$TEST_DIFF\"; fi; }\n"
    failure = "python3() { return 99; }\n" if helper_fails else ""
    output = tmp_path / "outputs"
    env = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(output), "TEST_DIFF": additions}
    scanned = subprocess.run(["bash", "-c", fake_git + failure + scan], cwd=root, env=env,
                             capture_output=True, text=True, check=False)
    assert scanned.returncode == 0, scanned.stdout + scanned.stderr
    results = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8-sig").splitlines())
    failed = subprocess.run(["bash", "-c", gate if results["critical"] == "true" else ":"],
                            cwd=root, env=env, capture_output=True, text=True, check=False)
    return results, failed.returncode, scanned.stdout


@pytest.mark.platforms("linux")
def test_workflow_clears_all_three_reported_subprocess_false_positives(tmp_path):
    grep_args = r'''f"grep -h '\\[{sid}\\]' ~/.hermes/logs/agent.log | grep 'API call #'", shell=True, capture_output=True, check=False'''
    log = candidate(grep_args, suffix='.stdout.decode("utf-8", "replace")').partition(":+")[2]
    delivery = candidate('argv, input=stream.read().encode("utf-8"), check=False, env=env',
                         suffix=".returncode").partition(":+")[2]
    additions = f"+log = {log}\n+log = {log}\n+                return {delivery}"
    results, gate_code, _ = run_workflow_scan(tmp_path, additions)
    assert results["critical"] == "false"
    assert gate_code == 0


@pytest.mark.parametrize("kind", ["command", "malformed", "outer_execution"])
@pytest.mark.platforms("linux")
def test_workflow_keeps_commands_and_ambiguity_critical(tmp_path, kind):
    sources = {
        "command": candidate("base64.b64decode(payload), input=text.encode('utf-8')"),
        "malformed": candidate("argv, input=text.encode('utf-8')")[:-1],
        "outer_execution": "1:+exec(" + candidate("argv", suffix=".stdout.decode('utf-8')").partition(":+")[2] + ")",
    }
    additions = "+" + sources[kind].partition(":+")[2]
    results, gate_code, _ = run_workflow_scan(tmp_path, additions)
    assert results["critical"] == "true"
    assert gate_code != 0


@pytest.mark.platforms("linux")
def test_workflow_helper_failure_retains_original_candidates_and_fails_gate(tmp_path):
    additions = "+" + candidate("argv, input=text.encode('utf-8')").partition(":+")[2]
    results, gate_code, output = run_workflow_scan(tmp_path, additions, helper_fails=True)
    assert results["critical"] == "true"
    assert gate_code != 0
    assert "retaining all findings" in output


@pytest.mark.parametrize("decoder", ["b64decode", "decodebytes", "urlsafe_b64decode"])
@pytest.mark.platforms("linux")
def test_workflow_base64_execution_gate_is_unchanged(tmp_path, decoder):
    # Build the hostile line at runtime so the repository-level scanner does
    # not flag its own positive fixture while this test still exercises the
    # exact encoded-execution pattern that must remain critical.
    sample = "+exec(base64." + decoder + "('eA=='))"
    results, gate_code, _ = run_workflow_scan(tmp_path, sample)
    assert results["critical"] == "true"
    assert gate_code != 0
