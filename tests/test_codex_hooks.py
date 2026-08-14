import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.runtime_state import RuntimeState


ROOT = Path(__file__).parents[1]
ADAPTER = ROOT / "adapters" / "codex"
HOOKS = ADAPTER / "hooks"
FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _fake_gdbus(tmp_path):
    calls = tmp_path / "gdbus.calls"
    executable = tmp_path / "gdbus"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

from core.runtime_state import RuntimeState

arguments = sys.argv[1:]
with open(os.environ["GDBUS_CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")

method = arguments[arguments.index("--method") + 1]
values = arguments[arguments.index("--method") + 2:]
if method.endswith(".GetFocusedUUID"):
    if os.environ.get("GDBUS_FAIL_FOCUS") == "1":
        raise SystemExit(1)
    print("('urn:uuid:focused-pane',)")
elif method.endswith(".Notify"):
    if os.environ.get("GDBUS_FAIL_NOTIFY") == "1":
        raise SystemExit(1)
    if os.environ.get("GDBUS_ZERO_NOTIFY") == "1":
        print("(uint32 0,)")
        raise SystemExit(0)
    decision = os.environ.get("GDBUS_DECISION")
    if decision:
        request_id = values[2]
        if os.environ.get("GDBUS_STALE_DECISION") == "1":
            request_id += "-stale"
        RuntimeState().write_decision(values[0], values[1], request_id, decision)
    print("(uint32 17,)")
else:
    print("(true,)")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return calls


@pytest.fixture
def hook_environment(tmp_path, monkeypatch):
    calls = _fake_gdbus(tmp_path)
    monkeypatch.delenv("TERMINATOR_UUID", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return os.environ.copy(), calls


@pytest.fixture
def decision_writer(monkeypatch):
    def write(decision):
        monkeypatch.setenv("GDBUS_DECISION", decision)

    return write


def _fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def _run_hook(name, input_text, environment, timeout=5):
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=input_text,
        text=True,
        env=environment,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _run_permission(environment, fixture="permission_bash.json", timeout=None):
    environment = environment.copy()
    if timeout is not None:
        environment["CODEX_NOTIFY_APPROVAL_TIMEOUT"] = str(timeout)
    result = _run_hook("permission_request.py", _fixture(fixture), environment)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout else None


def _calls(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _method_values(call):
    index = call.index("--method")
    return call[index + 1], call[index + 2 :]


def test_hook_entry_points_are_executable():
    for name in (
        "permission_request.py",
        "session_start.py",
        "stop.py",
        "user_prompt_submit.py",
    ):
        assert HOOKS.joinpath(name).stat().st_mode & stat.S_IXUSR


def test_allow_returns_native_codex_shape(hook_environment, decision_writer):
    environment, _calls_path = hook_environment
    decision_writer("allow")
    environment["GDBUS_DECISION"] = "allow"

    assert _run_permission(environment) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def test_deny_returns_native_codex_shape(hook_environment):
    environment, _calls_path = hook_environment
    environment["GDBUS_DECISION"] = "deny"

    assert _run_permission(environment) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "deny"},
        }
    }


def test_timeout_returns_no_decision_and_dismisses_exact_request(hook_environment):
    environment, calls_path = hook_environment

    assert _run_permission(environment, timeout=0) is None

    calls = [_method_values(call) for call in _calls(calls_path)]
    notify = next(values for method, values in calls if method.endswith(".Notify"))
    dismiss = next(
        values for method, values in calls if method.endswith(".DismissRequest")
    )
    assert dismiss == notify[:3]


@pytest.mark.parametrize("input_text", ["not json", "[]", "{}", '{"session_id": 7}'])
def test_malformed_input_exits_successfully_without_side_effects(
    hook_environment, input_text
):
    environment, calls_path = hook_environment

    result = _run_hook("permission_request.py", input_text, environment)

    assert result.returncode == 0
    assert result.stdout == ""
    assert _calls(calls_path) == []


@pytest.mark.parametrize("failure", ["GDBUS_FAIL_NOTIFY", "GDBUS_ZERO_NOTIFY"])
def test_notification_failure_returns_no_decision_without_cleanup_of_unregistered_request(
    hook_environment, failure
):
    environment, calls_path = hook_environment
    environment[failure] = "1"
    environment["GDBUS_DECISION"] = "allow"

    assert _run_permission(environment) is None

    methods = [_method_values(call)[0] for call in _calls(calls_path)]
    assert sum(method.endswith(".Notify") for method in methods) == 1
    assert not any(method.endswith(".DismissRequest") for method in methods)


def test_stale_decision_cannot_authorize_current_request(hook_environment):
    environment, calls_path = hook_environment
    environment["GDBUS_DECISION"] = "allow"
    environment["GDBUS_STALE_DECISION"] = "1"

    assert _run_permission(environment, timeout=0) is None

    notify = next(
        _method_values(call)[1]
        for call in _calls(calls_path)
        if _method_values(call)[0].endswith(".Notify")
    )
    assert RuntimeState().consume_decision(
        "codex", notify[1], f"{notify[2]}-stale"
    ) == "allow"


def test_concurrent_requests_use_distinct_decisions_and_cleanup(hook_environment):
    environment, calls_path = hook_environment
    allow_environment = environment.copy()
    deny_environment = environment.copy()
    allow_environment["GDBUS_DECISION"] = "allow"
    deny_environment["GDBUS_DECISION"] = "deny"
    command = [sys.executable, str(HOOKS / "permission_request.py")]
    payload = _fixture("permission_bash.json")

    allow_process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=allow_environment,
    )
    deny_process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=deny_environment,
    )
    allow_stdout, allow_stderr = allow_process.communicate(payload, timeout=5)
    deny_stdout, deny_stderr = deny_process.communicate(payload, timeout=5)

    assert allow_process.returncode == deny_process.returncode == 0
    assert allow_stderr == deny_stderr == ""
    assert json.loads(allow_stdout)["hookSpecificOutput"]["decision"] == {
        "behavior": "allow"
    }
    assert json.loads(deny_stdout)["hookSpecificOutput"]["decision"] == {
        "behavior": "deny"
    }
    calls = [_method_values(call) for call in _calls(calls_path)]
    requests = [values[2] for method, values in calls if method.endswith(".Notify")]
    dismissed = [values[2] for method, values in calls if method.endswith(".DismissRequest")]
    assert len(requests) == len(set(requests)) == 2
    assert sorted(dismissed) == sorted(requests)


def test_permission_notification_has_description_and_limited_command_preview(
    hook_environment
):
    environment, calls_path = hook_environment
    payload = json.loads(_fixture("permission_bash.json"))
    payload["tool_input"]["command"] = "x" * 500

    result = _run_hook(
        "permission_request.py", json.dumps(payload), {**environment, "CODEX_NOTIFY_APPROVAL_TIMEOUT": "0"}
    )

    assert result.returncode == 0
    notify = next(
        _method_values(call)[1]
        for call in _calls(calls_path)
        if _method_values(call)[0].endswith(".Notify")
    )
    notification = json.loads(notify[5])
    assert "Bash" in notification["title"]
    assert "Inspect the working tree" in notification["body"]
    assert "x" * 200 in notification["body"]
    assert "x" * 300 not in notification["body"]


def test_session_start_records_inherited_pane(hook_environment):
    environment, _calls_path = hook_environment
    environment["TERMINATOR_UUID"] = "urn:uuid:inherited-pane"

    result = _run_hook(
        "session_start.py", json.dumps({"session_id": "codex-session-2"}), environment
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert RuntimeState().read_pane("codex", "codex-session-2") == "urn:uuid:inherited-pane"


def test_session_start_falls_back_to_plugin_focused_pane(hook_environment):
    environment, calls_path = hook_environment

    result = _run_hook(
        "session_start.py", json.dumps({"session_id": "codex-session-3"}), environment
    )

    assert result.returncode == 0
    assert RuntimeState().read_pane("codex", "codex-session-3") == "urn:uuid:focused-pane"
    assert any(
        _method_values(call)[0].endswith(".GetFocusedUUID")
        for call in _calls(calls_path)
    )


def test_stop_posts_completion_notification(hook_environment):
    environment, calls_path = hook_environment
    RuntimeState().record_pane("codex", "codex-session-4", "pane-4")

    result = _run_hook(
        "stop.py",
        json.dumps({"session_id": "codex-session-4", "cwd": "/work/project"}),
        environment,
    )

    assert result.returncode == 0
    notify = next(
        _method_values(call)[1]
        for call in _calls(calls_path)
        if _method_values(call)[0].endswith(".Notify")
    )
    assert notify[:5] == ["codex", "codex-session-4", "", "pane-4", "complete"]
    assert json.loads(notify[5]) == {
        "title": "Codex — project",
        "body": "Task complete — waiting for your next instruction.",
    }


def test_user_prompt_submit_dismisses_session_notifications(hook_environment):
    environment, calls_path = hook_environment

    result = _run_hook(
        "user_prompt_submit.py",
        json.dumps({"session_id": "codex-session-5"}),
        environment,
    )

    assert result.returncode == 0
    assert [
        _method_values(call) for call in _calls(calls_path)
    ] == [
        (
            "io.github.TerminatorAgentNotify.DismissSession",
            ["codex", "codex-session-5"],
        )
    ]


def test_signal_interrupt_exits_successfully_and_dismisses_exact_request(
    hook_environment
):
    environment, calls_path = hook_environment
    environment["CODEX_NOTIFY_APPROVAL_TIMEOUT"] = "30"
    process = subprocess.Popen(
        [sys.executable, str(HOOKS / "permission_request.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stdin is not None
    process.stdin.write(_fixture("permission_bash.json"))
    process.stdin.close()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(
            _method_values(call)[0].endswith(".Notify") for call in _calls(calls_path)
        ):
            break
        time.sleep(0.01)
    else:
        process.kill()
        raise AssertionError("permission notification was not registered")

    process.send_signal(signal.SIGTERM)
    process.wait(timeout=5)
    stdout = process.stdout.read() if process.stdout else ""
    stderr = process.stderr.read() if process.stderr else ""

    assert process.returncode == 0
    assert stdout == stderr == ""
    calls = [_method_values(call) for call in _calls(calls_path)]
    notify = next(values for method, values in calls if method.endswith(".Notify"))
    dismiss = next(
        values for method, values in calls if method.endswith(".DismissRequest")
    )
    assert dismiss == notify[:3]
