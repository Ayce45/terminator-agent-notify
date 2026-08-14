import importlib.util
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.runtime_state import RuntimeState


ROOT = Path(__file__).parents[1]
ADAPTER = ROOT / "adapters" / "claude"
FIXTURES = Path(__file__).parent / "fixtures" / "claude"


def _load_autoresume():
    spec = importlib.util.spec_from_file_location(
        "claude_autoresume", ADAPTER / "autoresume.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fake_gdbus(tmp_path):
    calls = tmp_path / "gdbus.calls"
    executable = tmp_path / "gdbus"
    executable.write_text(
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nif [[ \"$*\" == *'.Notify'* && \"${GDBUS_FAIL_NOTIFY:-0}\" = 1 ]]; then exit 1; fi\nif [[ \"$*\" == *'.SendKeys'* ]]; then printf '%s\\n' \"${GDBUS_SEND_REPLY:-(true,)}\"; elif [[ \"$*\" == *'.Notify'* ]]; then printf '%s\\n' \"${GDBUS_NOTIFY_REPLY:-(uint32 1,)}\"; else printf '(true,)\\n'; fi\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return calls


def _hook_environment(tmp_path, monkeypatch):
    calls = _fake_gdbus(tmp_path)
    monkeypatch.delenv("TERMINATOR_UUID", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return calls


def _run_hook(name, payload, environment, check=True, timeout=5):
    return subprocess.run(
        [str(ADAPTER / "hooks" / name)],
        input=json.dumps(payload),
        text=True,
        env=environment,
        check=check,
        capture_output=True,
        timeout=timeout,
    )


def test_permission_notification_uses_the_claude_namespace(tmp_path, monkeypatch):
    calls = _hook_environment(tmp_path, monkeypatch)
    payload = json.loads((FIXTURES / "permission.json").read_text(encoding="utf-8"))
    RuntimeState().record_pane("claude", payload["session_id"], "claude-pane")

    _run_hook("notify-waiting.sh", payload, os.environ.copy())

    call = calls.read_text(encoding="utf-8")
    assert ".Notify claude claude-session-1 '' claude-pane permission" in call
    assert "important-project" in call


def test_stop_notification_normalizes_to_complete(tmp_path, monkeypatch):
    calls = _hook_environment(tmp_path, monkeypatch)
    payload = {"hook_event_name": "Stop", "session_id": "claude-session-stop"}
    RuntimeState().record_pane("claude", payload["session_id"], "claude-pane")

    _run_hook("notify-waiting.sh", payload, os.environ.copy())

    call = calls.read_text(encoding="utf-8")
    assert ".Notify claude claude-session-stop '' claude-pane complete" in call


def test_generic_notification_disable_skips_claude_notification_boundaries(
    tmp_path, monkeypatch
):
    calls = _hook_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "0")

    _run_hook(
        "notify-waiting.sh",
        {"session_id": "disabled-session"},
        os.environ.copy(),
    )

    assert not calls.exists()


def test_session_start_records_pane_in_the_claude_namespace(tmp_path, monkeypatch):
    _hook_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("TERMINATOR_UUID", "urn:uuid:claude-pane")

    _run_hook("session-start.sh", {"session_id": "same-session"}, os.environ.copy())

    state = RuntimeState()
    assert state.read_pane("claude", "same-session") == "urn:uuid:claude-pane"
    assert state.read_pane("codex", "same-session") is None


def test_session_start_ignores_runtime_state_failures(tmp_path, monkeypatch):
    _hook_environment(tmp_path, monkeypatch)
    failing_python = tmp_path / "python3"
    failing_python.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    failing_python.chmod(0o755)
    monkeypatch.setenv("TERMINATOR_UUID", "urn:uuid:claude-pane")

    result = _run_hook(
        "session-start.sh",
        {"session_id": "same-session"},
        os.environ.copy(),
        check=False,
    )

    assert result.returncode == 0


def test_session_start_prefers_shared_focused_uuid_before_legacy_service(
    tmp_path, monkeypatch
):
    calls = _hook_environment(tmp_path, monkeypatch)
    shared_gdbus = tmp_path / "gdbus"
    shared_gdbus.write_text(
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nif [[ \"$*\" == *'.GetFocusedUUID'* ]]; then printf \"('urn:uuid:11111111-2222-3333-4444-555555555555',)\\n\"; else exit 9; fi\n",
        encoding="utf-8",
    )
    shared_gdbus.chmod(0o755)

    _run_hook("session-start.sh", {"session_id": "shared-focus"}, os.environ.copy())

    assert RuntimeState().read_pane("claude", "shared-focus") == (
        "urn:uuid:11111111-2222-3333-4444-555555555555"
    )
    recorded = calls.read_text(encoding="utf-8")
    assert ".GetFocusedUUID" in recorded
    assert ".ListNames" not in recorded


def test_cleanup_dismisses_only_the_claude_session(tmp_path, monkeypatch):
    calls = _hook_environment(tmp_path, monkeypatch)

    _run_hook("notify-cleanup.sh", {"session_id": "claude-session-1"}, os.environ.copy())

    assert ".DismissSession claude claude-session-1" in calls.read_text(encoding="utf-8")


def test_fresh_limit_schedules_once_and_recovered_transcript_does_not(
    tmp_path, monkeypatch
):
    module = _load_autoresume()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CLAUDE_AUTORESUME_ARM_NOTIFY", "0")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)

    scheduled = []

    def run(command, **_kwargs):
        scheduled.append(command)
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(module.subprocess, "run", run)
    transcript = FIXTURES / "limit.jsonl"

    assert module.process(transcript, "claude-session-429") is True
    assert module.process(transcript, "claude-session-429") is False
    assert len(scheduled) == 1
    now = datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc).astimezone()
    target = now.replace(hour=13, minute=5, second=0, microsecond=0)
    if target < now:
        target += timedelta(days=1)
    target += timedelta(seconds=90)
    assert scheduled[0] == [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit=claude-autoresume-claude-s-{int(target.timestamp())}",
        f"--on-active={int((target - now).total_seconds())}s",
        "--timer-property=AccuracySec=1s",
        "--description=Claude auto-resume after usage limit (continue)",
        str(module.SEND),
        "--session",
        "claude-session-429",
        "--message",
        "continue",
        "--notify",
        "--resume",
    ]

    recovered = tmp_path / "recovered.jsonl"
    recovered.write_text(
        transcript.read_text(encoding="utf-8")
        + '\n{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Recovered."}]}}\n',
        encoding="utf-8",
    )
    assert module.process(recovered, "recovered-session") is False
    assert len(scheduled) == 1


def test_schedule_failure_removes_its_marker(tmp_path, monkeypatch):
    module = _load_autoresume()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CLAUDE_AUTORESUME_ARM_NOTIFY", "0")

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(module, "datetime", FixedDateTime)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, "systemd-run")
        ),
    )

    assert module.process(FIXTURES / "limit.jsonl", "claude-session-429") is False
    assert not list((tmp_path / "runtime" / "terminator-agent-notify" / "autoresume").rglob("*.scheduled"))


def test_load_objects_discards_scalar_and_list_records(tmp_path):
    module = _load_autoresume()
    transcript = tmp_path / "mixed.jsonl"
    transcript.write_text('null\n["unexpected"]\n"scalar"\n{"type":"assistant"}\n', encoding="utf-8")

    assert module._load_objects(transcript) == [{"type": "assistant"}]


def test_scan_continues_after_one_transcript_has_an_unexpected_format(monkeypatch):
    module = _load_autoresume()
    paths = ["/tmp/malformed.jsonl", "/tmp/recovered.jsonl"]
    processed = []
    monkeypatch.setattr(module.glob, "glob", lambda _pattern: paths)
    monkeypatch.setattr(module.os.path, "getmtime", lambda _path: datetime.now().timestamp())

    def process(path):
        processed.append(path)
        if path == paths[0]:
            raise ValueError("unexpected transcript record")

    monkeypatch.setattr(module, "process", process)

    module.scan()

    assert processed == paths


def test_armed_resume_notification_uses_the_claude_namespace(tmp_path, monkeypatch):
    module = _load_autoresume()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    RuntimeState().record_pane("claude", "armed-session", "claude-pane")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"(uint32 1,)")

    monkeypatch.setattr(module.subprocess, "run", run)

    module._notify("armed-session", "Resume armed", "A resume was scheduled.")

    assert calls[0][-8:] == [
        "--method",
        "io.github.TerminatorAgentNotify.Notify",
        "claude",
        "armed-session",
        "",
        "claude-pane",
        "waiting",
        '{"title": "Resume armed", "body": "A resume was scheduled."}',
    ]


def test_generic_notification_disable_skips_claude_autoresume_notice(
    tmp_path, monkeypatch
):
    module = _load_autoresume()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "0")
    RuntimeState().record_pane("claude", "disabled-session", "claude-pane")
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_kwargs: calls.append(command))

    module._notify("disabled-session", "Resume armed", "Scheduled")

    assert calls == []


def test_resume_send_uses_the_dialog_safe_key_sequence(tmp_path, monkeypatch):
    calls = _hook_environment(tmp_path, monkeypatch)
    sleeps = tmp_path / "sleeps"
    sleep = tmp_path / "sleep"
    sleep.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$1" >> "$SLEEPS"\n', encoding="utf-8")
    sleep.chmod(0o755)
    monkeypatch.setenv("SLEEPS", str(sleeps))
    RuntimeState().record_pane("claude", "resume-session", "claude-pane")

    subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "resume-session", "--message", "continue", "--resume"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    sent = calls.read_text(encoding="utf-8").splitlines()
    assert [line.split()[-1] for line in sent] == ["$'\\r'", "continue", "$'\\r'", "$'\\r'"]
    assert sleeps.read_text(encoding="utf-8").splitlines() == ["2", "1", "1"]


@pytest.mark.parametrize(
    "reply",
    ["(false,)", "false", "(true, )", "(uint32 1,)", "malformed"],
)
def test_send_rejects_every_sendkeys_reply_except_exact_true_tuple(
    tmp_path, monkeypatch, reply
):
    _hook_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("GDBUS_SEND_REPLY", reply)
    RuntimeState().record_pane("claude", "send-session", "claude-pane")

    result = subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "send-session", "--notify"],
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0


@pytest.mark.parametrize("reply", ["(uint32 0,)", "(false,)", "(uint32 1, )", "bad"])
def test_send_notify_uses_fallback_unless_reply_is_positive_uint32(
    tmp_path, monkeypatch, reply
):
    _hook_environment(tmp_path, monkeypatch)
    fallback = tmp_path / "notify-send.calls"
    notify_send = tmp_path / "notify-send"
    notify_send.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n',
        encoding="utf-8",
    )
    notify_send.chmod(0o755)
    monkeypatch.setenv("GDBUS_NOTIFY_REPLY", reply)
    monkeypatch.setenv("NOTIFY_SEND_CALLS", str(fallback))
    RuntimeState().record_pane("claude", "notify-session", "claude-pane")

    result = subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "notify-session", "--notify"],
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert fallback.exists()


def test_send_resolves_and_targets_the_plugin_focused_pane(tmp_path, monkeypatch):
    calls = _hook_environment(tmp_path, monkeypatch)
    focused_gdbus = tmp_path / "gdbus"
    focused_gdbus.write_text(
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nif [[ \"$*\" == *'.GetFocusedUUID'* ]]; then printf \"('urn:uuid:11111111-2222-3333-4444-555555555555',)\\n\"; else printf '(true,)\\n'; fi\n",
        encoding="utf-8",
    )
    focused_gdbus.chmod(0o755)

    subprocess.run(
        [str(ADAPTER / "send.sh"), "--focused", "--message", "hello"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    sent = calls.read_text(encoding="utf-8")
    assert ".GetFocusedUUID" in sent
    assert ".SendKeys 11111111-2222-3333-4444-555555555555" in sent


def test_send_falls_back_to_notify_send_when_the_service_is_unavailable(
    tmp_path, monkeypatch
):
    _hook_environment(tmp_path, monkeypatch)
    fallback = tmp_path / "notify-send.calls"
    notify_send = tmp_path / "notify-send"
    notify_send.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n', encoding="utf-8")
    notify_send.chmod(0o755)
    monkeypatch.setenv("GDBUS_FAIL_NOTIFY", "1")
    monkeypatch.setenv("NOTIFY_SEND_CALLS", str(fallback))
    RuntimeState().record_pane("claude", "notify-session", "claude-pane")

    subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "notify-session", "--notify"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Claude Code — relancé automatiquement" in fallback.read_text(encoding="utf-8")


def test_generic_expiry_reaches_claude_notify_send_fallback(tmp_path, monkeypatch):
    _hook_environment(tmp_path, monkeypatch)
    fallback = tmp_path / "notify-send.calls"
    notify_send = tmp_path / "notify-send"
    notify_send.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n',
        encoding="utf-8",
    )
    notify_send.chmod(0o755)
    monkeypatch.setenv("GDBUS_FAIL_NOTIFY", "1")
    monkeypatch.setenv("NOTIFY_SEND_CALLS", str(fallback))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_EXPIRY_MS", "4321")

    _run_hook(
        "notify-waiting.sh",
        {"session_id": "expiry-session"},
        os.environ.copy(),
    )

    assert "--expire-time=4321" in fallback.read_text(encoding="utf-8")


def test_generic_notification_disable_keeps_send_but_skips_notice(
    tmp_path, monkeypatch
):
    calls = _hook_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "0")
    RuntimeState().record_pane("claude", "disabled-send", "claude-pane")

    result = subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "disabled-send", "--notify"],
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    recorded = calls.read_text(encoding="utf-8")
    assert ".SendKeys" in recorded
    assert ".Notify" not in recorded


@pytest.mark.parametrize(
    ("script", "payload"),
    [
        ("session-start.sh", {"session_id": "hung-session"}),
        ("notify-cleanup.sh", {"session_id": "hung-session"}),
        ("notify-waiting.sh", {"session_id": "hung-session"}),
    ],
)
def test_claude_hooks_bound_hung_desktop_commands(
    tmp_path, monkeypatch, script, payload
):
    _hook_environment(tmp_path, monkeypatch)
    for command in ("gdbus", "notify-send"):
        executable = tmp_path / command
        executable.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
        executable.chmod(0o755)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS", "0.1")

    started = time.monotonic()
    result = _run_hook(script, payload, os.environ.copy(), check=False, timeout=2)

    assert result.returncode == 0
    assert time.monotonic() - started < 1.5


def test_claude_send_bounds_hung_sendkeys_call(tmp_path, monkeypatch):
    _hook_environment(tmp_path, monkeypatch)
    gdbus = tmp_path / "gdbus"
    gdbus.write_text("#!/usr/bin/env bash\nsleep 30\n", encoding="utf-8")
    gdbus.chmod(0o755)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS", "0.1")
    RuntimeState().record_pane("claude", "hung-send", "claude-pane")

    started = time.monotonic()
    result = subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "hung-send"],
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert result.returncode != 0
    assert time.monotonic() - started < 1.5
