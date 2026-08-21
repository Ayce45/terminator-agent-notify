import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from terminator_agent_notify_core.runtime_state import RuntimeState
from adapters.codex.hooks import permission_request
from adapters.codex.hooks import user_prompt_submit
from adapters.codex import common as codex_common


ROOT = Path(__file__).parents[1]
ADAPTER = ROOT / "adapters" / "codex"
HOOKS = ADAPTER / "hooks"
FIXTURES = Path(__file__).parent / "fixtures" / "codex"
SUBPROCESS_TIMEOUT = 5


def _fake_gdbus(tmp_path):
    calls = tmp_path / "gdbus.calls"
    executable = tmp_path / "gdbus"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

from terminator_agent_notify_core.runtime_state import RuntimeState

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
    if os.environ.get("GDBUS_AMBIGUOUS_NOTIFY") == "1":
        orphan = os.environ["GDBUS_ORPHAN"]
        with open(orphan, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(values[:3]))
        RuntimeState().write_decision(values[0], values[1], values[2], "allow")
        RuntimeState().write_request_closed(values[0], values[1], values[2])
        raise SystemExit(1)
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
    if os.environ.get("GDBUS_RAW_DECISION"):
        RuntimeState().decision_path(values[0], values[1], values[2]).write_text(
            os.environ["GDBUS_RAW_DECISION"], encoding="utf-8"
        )
    if os.environ.get("GDBUS_CLOSE_REQUEST") == "1" or os.environ.get(
        "GDBUS_RETIRE_REQUEST"
    ) in {"default", "session"}:
        RuntimeState().write_request_closed(values[0], values[1], values[2])
    delay = float(os.environ.get("GDBUS_DELAY_NOTIFY", "0"))
    if delay:
        import time
        time.sleep(delay)
    print("(uint32 17,)")
else:
    if method.endswith(".DismissRequest") and os.environ.get("GDBUS_ORPHAN"):
        orphan = os.environ["GDBUS_ORPHAN"]
        if os.path.exists(orphan):
            with open(orphan, encoding="utf-8") as stream:
                active = json.load(stream)
            if active == values[:3]:
                os.unlink(orphan)
    if method.endswith(".DismissRequest") and os.environ.get(
        "GDBUS_DECISION_ON_DISMISS"
    ):
        RuntimeState().write_decision(
            values[0],
            values[1],
            values[2],
            os.environ["GDBUS_DECISION_ON_DISMISS"],
        )
    delay = 0
    if method.endswith(".DismissRequest"):
        delay = float(os.environ.get("GDBUS_DELAY_DISMISS", "0"))
    elif method.endswith(".SetPaneTitle"):
        delay = float(os.environ.get("GDBUS_DELAY_SET_TITLE", "0"))
    if delay:
        import time
        time.sleep(delay)
    if method.endswith(".DismissRequest") and os.environ.get(
        "GDBUS_BREAK_STATE_ON_DISMISS"
    ) == "1":
        state = RuntimeState()
        moved = state.root.with_name(state.root.name + "-moved")
        os.replace(state.root, moved)
        state.root.write_text("not a directory", encoding="utf-8")
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


def _run_hook(name, input_text, environment, timeout=SUBPROCESS_TIMEOUT):
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=input_text,
        text=True,
        env=environment,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _run_permission(environment, fixture="permission_bash.json"):
    environment = environment.copy()
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


def test_permission_hook_returns_immediately_and_leaves_notification_actionable(
    hook_environment,
):
    environment, calls_path = hook_environment

    started = time.monotonic()
    result = _run_permission(environment)
    elapsed = time.monotonic() - started

    assert result is None
    assert elapsed < 1
    methods = [_method_values(call)[0] for call in _calls(calls_path)]
    assert any(method.endswith(".Notify") for method in methods)
    assert not any(method.endswith(".DismissRequest") for method in methods)


@pytest.mark.parametrize("input_text", ["not json", "[]", "{}", '{"session_id": 7}'])
def test_malformed_input_exits_successfully_without_side_effects(
    hook_environment, input_text
):
    environment, calls_path = hook_environment

    result = _run_hook("permission_request.py", input_text, environment)

    assert result.returncode == 0
    assert result.stdout == ""
    assert _calls(calls_path) == []


def test_permission_notification_has_description_and_limited_command_preview(
    hook_environment
):
    environment, calls_path = hook_environment
    payload = json.loads(_fixture("permission_bash.json"))
    payload["tool_input"]["command"] = "x" * 500

    result = _run_hook("permission_request.py", json.dumps(payload), environment)

    assert result.returncode == 0
    notify = next(
        _method_values(call)[1]
        for call in _calls(calls_path)
        if _method_values(call)[0].endswith(".Notify")
    )
    notification = json.loads(notify[5])
    assert notification["title"] == "project"
    assert "Permission requested — Bash" in notification["body"]
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


def test_session_start_restores_saved_title_into_resumed_session_pane(hook_environment):
    environment, calls_path = hook_environment
    environment["TERMINATOR_UUID"] = "urn:uuid:resumed-pane"
    RuntimeState().record_title("codex", "resumed-session", "Original task title")

    result = _run_hook(
        "session_start.py",
        json.dumps({"session_id": "resumed-session"}),
        environment,
    )

    assert result.returncode == 0
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        title_calls = [
            values
            for method, values in map(_method_values, _calls(calls_path))
            if method.endswith(".SetPaneTitle")
        ]
        if title_calls:
            break
        time.sleep(0.01)
    assert title_calls == [["urn:uuid:resumed-pane", "Original task title"]]


def test_title_restore_keeps_the_pane_captured_by_each_session_start(hook_environment):
    environment, calls_path = hook_environment
    state = RuntimeState()
    state.record_title("codex", "shared-session", "Shared historic title")
    state.record_pane("codex", "shared-session", "pane-b")

    result = subprocess.run(
        [
            sys.executable,
            str(HOOKS / "session_start.py"),
            "--restore-title",
            "shared-session",
            "pane-a",
        ],
        text=True,
        env=environment,
        check=False,
        capture_output=True,
        timeout=SUBPROCESS_TIMEOUT,
    )

    assert result.returncode == 0
    title_calls = [
        values
        for method, values in map(_method_values, _calls(calls_path))
        if method.endswith(".SetPaneTitle")
    ]
    assert title_calls == [["pane-a", "Shared historic title"]]


def test_slow_title_restore_does_not_delay_session_start(hook_environment):
    environment, _calls_path = hook_environment
    environment["TERMINATOR_UUID"] = "resumed-pane"
    environment["GDBUS_DELAY_SET_TITLE"] = "2"
    RuntimeState().record_title("codex", "slow-resume", "Historic title")

    started = time.monotonic()
    result = _run_hook(
        "session_start.py",
        json.dumps({"session_id": "slow-resume"}),
        environment,
    )

    assert result.returncode == 0
    assert time.monotonic() - started < 1


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
        "title": "project",
        "body": "Task complete — waiting for your next instruction.",
    }


def test_generic_notification_disable_skips_codex_notification_boundaries(
    hook_environment,
):
    environment, calls_path = hook_environment
    environment["TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS"] = "0"

    result = _run_hook(
        "stop.py",
        json.dumps({"session_id": "codex-session-disabled"}),
        environment,
    )

    assert result.returncode == 0
    assert _calls(calls_path) == []


def test_persisted_notification_disable_reaches_codex_hook(hook_environment):
    environment, calls_path = hook_environment
    environment.pop("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", None)
    config = Path(environment["HOME"]) / "persisted-environment"
    config.write_text(
        "TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=0\n", encoding="utf-8"
    )
    config.chmod(0o600)
    environment["TERMINATOR_AGENT_NOTIFY_CONFIG"] = str(config)

    result = _run_hook(
        "stop.py",
        json.dumps({"session_id": "codex-persisted-disabled"}),
        environment,
    )

    assert result.returncode == 0
    assert _calls(calls_path) == []


def test_stop_notification_fallback_is_fire_and_forget(hook_environment):
    environment, calls_path = hook_environment
    environment["GDBUS_FAIL_NOTIFY"] = "1"
    notify_send = calls_path.parent / "notify-send"
    pid_file = calls_path.parent / "notify-send.pid"
    notify_send.write_text(
        """#!/usr/bin/env python3
import os
import time
from pathlib import Path

Path(os.environ["NOTIFY_SEND_PID"]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(30)
""",
        encoding="utf-8",
    )
    notify_send.chmod(0o755)
    environment["NOTIFY_SEND_PID"] = str(pid_file)

    try:
        result = _run_hook(
            "stop.py",
            json.dumps({"session_id": "codex-session-4"}),
            environment,
            timeout=2,
        )

        assert result.returncode == 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.01)
        assert pid_file.exists()
    finally:
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text(encoding="utf-8")), signal.SIGTERM)
            except ProcessLookupError:
                pass


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


def test_first_codex_prompt_sets_a_short_local_pane_title_once(hook_environment):
    environment, calls_path = hook_environment
    RuntimeState().record_pane("codex", "title-session", "pane-title")
    event = {
        "session_id": "title-session",
        "prompt": "  Corriger   les notifications Terminator avec un titre vraiment beaucoup trop long pour rester entier  ",
    }

    first = _run_hook("user_prompt_submit.py", json.dumps(event), environment)
    second = _run_hook("user_prompt_submit.py", json.dumps(event), environment)

    assert first.returncode == second.returncode == 0
    deadline = time.monotonic() + 2
    expected_title = "Corriger les notifications Terminator avec un titre vraiment…"
    while time.monotonic() < deadline:
        title_call_recorded = any(
            method.endswith(".SetPaneTitle")
            for method, _values in map(_method_values, _calls(calls_path))
        )
        if title_call_recorded and RuntimeState().read_title(
            "codex", "title-session"
        ) == expected_title:
            break
        time.sleep(0.01)
    calls = [_method_values(call) for call in _calls(calls_path)]
    title_calls = [values for method, values in calls if method.endswith(".SetPaneTitle")]
    assert title_calls == [
        ["pane-title", expected_title]
    ]
    assert RuntimeState().read_title("codex", "title-session") == expected_title


def test_slow_title_update_does_not_delay_prompt_hook(hook_environment):
    environment, _calls_path = hook_environment
    environment["GDBUS_DELAY_SET_TITLE"] = "2"
    RuntimeState().record_pane("codex", "slow-title", "pane-title")

    started = time.monotonic()
    result = _run_hook(
        "user_prompt_submit.py",
        json.dumps({"session_id": "slow-title", "prompt": "Fix notifications"}),
        environment,
    )

    assert result.returncode == 0
    assert time.monotonic() - started < 1


def test_title_worker_argv_never_contains_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    launched = []
    secret = "secret-token\x00" + "x" * 200_000
    monkeypatch.setattr(
        user_prompt_submit.subprocess,
        "Popen",
        lambda arguments, **_kwargs: launched.append(arguments),
    )

    user_prompt_submit._schedule_first_title("argv-session", secret)

    assert launched == [[
        sys.executable,
        str(user_prompt_submit.Path(user_prompt_submit.__file__).resolve()),
        "--set-title",
        "argv-session",
    ]]
    claimed = RuntimeState().read_title_claim("codex", "argv-session")
    assert claimed is not None
    assert "\x00" not in claimed
    assert len(claimed) <= 61


def _recording_notify_send(calls_path, environment):
    fallback = calls_path.parent / "notify-send.calls"
    notify_send = calls_path.parent / "notify-send"
    notify_send.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n',
        encoding="utf-8",
    )
    notify_send.chmod(0o755)
    environment["NOTIFY_SEND_CALLS"] = str(fallback)
    return fallback


def test_permission_falls_back_to_plain_notification_when_plugin_unreachable(
    hook_environment,
):
    environment, calls_path = hook_environment
    environment["GDBUS_FAIL_NOTIFY"] = "1"
    fallback = _recording_notify_send(calls_path, environment)

    assert _run_permission(environment) is None

    recorded = fallback.read_text(encoding="utf-8")
    assert "project" in recorded
    assert "Permission requested — Bash" in recorded
    assert "Answer in the terminal." in recorded


def test_pre_tool_use_notifies_for_request_user_input(hook_environment):
    environment, calls_path = hook_environment
    event = {
        "session_id": "codex-session-q",
        "hook_event_name": "PreToolUse",
        "tool_name": "request_user_input",
        "cwd": "/work/project",
    }

    result = _run_hook("pre_tool_use.py", json.dumps(event), environment)

    assert result.returncode == 0
    assert result.stdout == ""
    calls = [
        call
        for call in _calls(calls_path)
        if any(str(part).endswith(".Notify") for part in call)
    ]
    assert len(calls) == 1
    assert "waiting" in calls[0]
    assert "question" in " ".join(calls[0]).lower()


def test_pre_tool_use_ignores_other_tools(hook_environment):
    environment, calls_path = hook_environment
    event = {
        "session_id": "codex-session-q",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }

    result = _run_hook("pre_tool_use.py", json.dumps(event), environment)

    assert result.returncode == 0
    assert result.stdout == ""
    assert _calls(calls_path) == []


def test_permission_notification_matches_the_claude_message_format():
    event = {
        "session_id": "s1",
        "turn_id": "t1",
        "cwd": "/workspaces/important-project",
        "tool_name": "Bash",
        "tool_input": {"command": "git status --short"},
    }

    title, body = permission_request._notification_text(event)

    assert title == "important-project"
    assert body.startswith("Permission requested — Bash")
    assert "Command: git status --short" in body


def test_gdbus_argument_preserves_json_escape_sequences():
    payload = '{"title":"Codex","body":"line 1\\nline 2\\tC:\\\\tmp"}'

    assert codex_common.gvariant_text(payload) == (
        '{"title":"Codex","body":"line 1\\\\nline 2\\\\tC:\\\\\\\\tmp"}'
    )
