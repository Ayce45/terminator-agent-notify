import importlib.util
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_state import RuntimeState


ROOT = Path(__file__).parents[1]
ADAPTER = ROOT / "adapters" / "codex"
FIXTURES = Path(__file__).parent / "fixtures" / "codex"
NOW = datetime(2026, 8, 14, 13, 0, tzinfo=timezone.utc)


def _load_autoresume():
    spec = importlib.util.spec_from_file_location(
        "codex_autoresume", ADAPTER / "autoresume.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _environment(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("CODEX_AUTORESUME", "1")


def test_disabled_mode_never_schedules(tmp_path, monkeypatch):
    """Removing the opt-in must stop a valid limit response from scheduling."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    monkeypatch.setenv("CODEX_AUTORESUME", "0")
    scheduled = []

    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is False
    assert scheduled == []


def test_unambiguous_future_limit_schedules_once(tmp_path, monkeypatch):
    """Dropping deduplication would create multiple delayed resumes for one reset."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    scheduled = []

    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is True
    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is False
    assert scheduled == [
        [
            "systemd-run",
            "--user",
            "--collect",
            "--unit=codex-autoresume-57b756c6-1786712700",
            "--on-active=300s",
            "--timer-property=AccuracySec=1s",
            "--description=Codex auto-resume after usage limit (continue)",
            str(ADAPTER / "send.sh"),
            "--session",
            "codex-session-limit",
            "--message",
            "continue",
            "--notify",
        ]
    ]


def test_ambiguous_or_stale_limits_never_schedule(tmp_path, monkeypatch):
    """Choosing among reset times or scheduling after expiry would send at the wrong time."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    stale = tmp_path / "stale.jsonl"
    stale.write_text(
        '{"session_id":"stale","timestamp":"2026-08-14T12:40:00Z",'
        '"event":{"status":"rate_limit_exceeded",'
        '"message":"Usage limit reached. Resets at 2026-08-14T13:05:00Z."}}\n',
        encoding="utf-8",
    )
    future = tmp_path / "future.jsonl"
    future.write_text(
        '{"session_id":"future","timestamp":"2026-08-14T13:01:00Z",'
        '"event":{"status":"rate_limit_exceeded",'
        '"message":"Usage limit reached. Resets at 2026-08-14T13:05:00Z."}}\n',
        encoding="utf-8",
    )
    scheduled = []

    assert module.process(
        FIXTURES / "limit_ambiguous.jsonl", clock=lambda: NOW, scheduler=scheduled.append
    ) is False
    assert module.process(stale, clock=lambda: NOW, scheduler=scheduled.append) is False
    assert module.process(future, clock=lambda: NOW, scheduler=scheduled.append) is False
    assert scheduled == []


def test_parse_failure_returns_cleanly_and_logs(tmp_path, monkeypatch):
    """A malformed transcript must not crash the poller or arm a resume."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"event":\n', encoding="utf-8")
    records = []

    assert module.process(
        broken, clock=lambda: NOW, scheduler=lambda _command: None, logger=records.append
    ) is False
    assert len(records) == 1
    assert "parse" in records[0].lower()


def test_scheduler_error_releases_the_deduplication_marker(tmp_path, monkeypatch):
    """Leaving a marker after a failed scheduler call would make the failure permanent."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)

    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=lambda _command: (_ for _ in ()).throw(ValueError("scheduler unavailable")),
        logger=lambda _message: None,
    ) is False
    assert not list((tmp_path / "runtime" / "terminator-agent-notify" / "autoresume").rglob("*.scheduled"))


def test_send_resolves_the_current_pane_then_sends_message_and_enter(tmp_path, monkeypatch):
    """Using a stale pane mapping would type into a closed or wrong terminal."""
    _environment(tmp_path, monkeypatch)
    RuntimeState().record_pane("codex", "codex-session-limit", "codex-pane-now")
    calls = tmp_path / "gdbus.calls"
    gdbus = tmp_path / "gdbus"
    gdbus.write_text(
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nprintf '(uint32 1,)\\n'\n",
        encoding="utf-8",
    )
    gdbus.chmod(0o755)
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    subprocess.run(
        [str(ADAPTER / "send.sh"), "--session", "codex-session-limit", "--message", "resume work", "--notify"],
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    sent = calls.read_text(encoding="utf-8").splitlines()
    assert any(".SendKeys codex-pane-now $'resume work\\r'" in call for call in sent)
    assert any(".Notify codex codex-session-limit '' codex-pane-now waiting" in call for call in sent)
