import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nif [[ \"$*\" == *'.Notify'* && \"${GDBUS_FAIL_NOTIFY:-0}\" = 1 ]]; then exit 1; fi\nprintf '(uint32 1,)\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return calls


def _hook_environment(tmp_path, monkeypatch):
    calls = _fake_gdbus(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return calls


def _run_hook(name, payload, environment):
    return subprocess.run(
        [str(ADAPTER / "hooks" / name)],
        input=json.dumps(payload),
        text=True,
        env=environment,
        check=True,
        capture_output=True,
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


def test_session_start_records_pane_in_the_claude_namespace(tmp_path, monkeypatch):
    _hook_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("TERMINATOR_UUID", "urn:uuid:claude-pane")

    _run_hook("session-start.sh", {"session_id": "same-session"}, os.environ.copy())

    state = RuntimeState()
    assert state.read_pane("claude", "same-session") == "urn:uuid:claude-pane"
    assert state.read_pane("codex", "same-session") is None


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
    assert scheduled[0][-6:] == [
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
