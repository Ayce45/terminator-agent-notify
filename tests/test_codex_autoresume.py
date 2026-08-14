import importlib.util
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from terminator_agent_notify_core.runtime_state import RuntimeState


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
    monkeypatch.setenv("CODEX_AUTORESUME_GENERATION", "codex-generation")
    config = tmp_path / "environment"
    config.write_text(
        "CODEX_AUTORESUME=1\nCODEX_AUTORESUME_GENERATION=codex-generation\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_CONFIG", str(config))


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


def test_generic_quiet_log_level_suppresses_codex_diagnostics(
    monkeypatch, capsys
):
    module = _load_autoresume()
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_LOG_LEVEL", "quiet")

    module._log("transcript unavailable")

    assert capsys.readouterr().err == ""


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
            "--unit=terminator-agent-notify-codex-autoresume-57b756c6e2183717-1786712700",
            "--on-active=300s",
            "--timer-property=AccuracySec=1s",
            "--description=Codex auto-resume after usage limit (continue)",
            str(ADAPTER / "send.sh"),
            "--session",
            "codex-session-limit",
            "--message",
            "continue",
            "--notify",
            "--generation",
            "codex-generation",
            "--config",
            str(tmp_path / "environment"),
        ]
    ]


def test_generation_rotation_allows_same_codex_limit_to_be_rescheduled(
    tmp_path, monkeypatch
):
    """A stale marker must not suppress the replacement job after reinstall."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    config = Path(os.environ["TERMINATOR_AGENT_NOTIFY_CONFIG"])
    scheduled = []

    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is True
    monkeypatch.setenv("CODEX_AUTORESUME_GENERATION", "second-generation")
    config.write_text(
        "CODEX_AUTORESUME=1\nCODEX_AUTORESUME_GENERATION=second-generation\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is True

    assert [command[command.index("--generation") + 1] for command in scheduled] == [
        "codex-generation",
        "second-generation",
    ]


def test_distinct_reset_instants_in_one_minute_schedule_independently(tmp_path, monkeypatch):
    """Rounding markers to minutes incorrectly drops a later reset in that minute."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    second = tmp_path / "second-reset.jsonl"
    second.write_text(
        '{"session_id":"codex-session-limit","timestamp":"2026-08-14T13:00:00Z",'
        '"event":{"status":"rate_limit_exceeded",'
        '"message":"Usage limit reached. Resets at 2026-08-14T13:05:59Z."}}\n',
        encoding="utf-8",
    )
    scheduled = []

    assert module.process(FIXTURES / "limit_with_reset.jsonl", clock=lambda: NOW, scheduler=scheduled.append) is True
    assert module.process(second, clock=lambda: NOW, scheduler=scheduled.append) is True
    assert len(scheduled) == 2


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


def test_recovered_limit_never_schedules(tmp_path, monkeypatch):
    """Ignoring later completion records would resume an already recovered session."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    scheduled = []

    assert module.process(
        FIXTURES / "limit_recovered.jsonl", clock=lambda: NOW, scheduler=scheduled.append
    ) is False
    assert scheduled == []


def test_nested_status_only_limit_with_offset_reset_schedules(tmp_path, monkeypatch):
    """Requiring rate-limit prose would miss the structured Codex status signal."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    scheduled = []

    assert module.process(
        FIXTURES / "limit_status_only_nested.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is True
    assert scheduled[0][3].startswith(
        "--unit=terminator-agent-notify-codex-autoresume-"
    )
    assert scheduled[0][4] == "--on-active=300s"
    assert scheduled[0][scheduled[0].index("--session") + 1] == (
        "codex-session-nested"
    )


def test_missing_generation_or_config_never_schedules(tmp_path, monkeypatch):
    """Every delayed Codex execution needs a durable generation guard."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    monkeypatch.delenv("CODEX_AUTORESUME_GENERATION")
    scheduled = []

    assert module.process(
        FIXTURES / "limit_with_reset.jsonl",
        clock=lambda: NOW,
        scheduler=scheduled.append,
    ) is False
    assert scheduled == []


def test_near_match_status_does_not_count_as_rate_limit():
    """Substring matching could arm a resume for an unrelated custom status."""
    module = _load_autoresume()

    assert module._has_limit_signal({"status": "not_rate_limit_exceeded"}) is False


def test_old_successful_markers_are_pruned_but_recent_markers_remain(
    tmp_path, monkeypatch
):
    """Permanent successful markers would grow runtime state without bound."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    runtime = RuntimeState()
    directory = runtime.root / "autoresume" / hashlib.sha256(b"codex").hexdigest()
    directory.mkdir(mode=0o700, parents=True)
    old_epoch = int(NOW.timestamp()) - 8 * 24 * 60 * 60
    recent_epoch = int(NOW.timestamp()) - 60
    old = directory / f"{'a' * 64}-{old_epoch}.scheduled"
    recent = directory / f"{'b' * 64}-{recent_epoch}.scheduled"
    old.touch()
    recent.touch()
    no_limit = tmp_path / "no-limit.jsonl"
    no_limit.write_text('{"event":{"status":"ok"}}\n', encoding="utf-8")

    module.process(
        no_limit,
        clock=lambda: NOW,
        scheduler=lambda _command: True,
        state=runtime,
    )

    assert not old.exists()
    assert recent.exists()


def test_far_future_or_overflowing_reset_never_schedules(tmp_path, monkeypatch):
    """Unbounded or unrepresentable timestamps must fail closed rather than arm a timer."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    far = tmp_path / "far.jsonl"
    far.write_text(
        '{"session_id":"far","timestamp":"2026-08-14T13:00:00Z",'
        '"event":{"status":"rate_limit_exceeded",'
        '"message":"Usage limit reached. Resets at 2026-08-22T13:00:00Z."}}\n',
        encoding="utf-8",
    )
    overflow = tmp_path / "overflow.jsonl"
    overflow.write_text(
        '{"session_id":"overflow","timestamp":"9999-12-31T23:59:59-23:59",'
        '"event":{"status":"rate_limit_exceeded",'
        '"message":"Usage limit reached. Resets at 9999-12-31T23:59:59-23:59."}}\n',
        encoding="utf-8",
    )
    scheduled, records = [], []

    assert module.process(far, clock=lambda: NOW, scheduler=scheduled.append, logger=records.append) is False
    assert module.process(overflow, clock=lambda: NOW, scheduler=scheduled.append, logger=records.append) is False
    assert scheduled == []
    assert records


def test_scan_skips_old_paths_and_reads_only_the_recent_tail(tmp_path, monkeypatch):
    """Scanning stale paths or whole transcripts can re-arm historical limits."""
    module = _load_autoresume()
    _environment(tmp_path, monkeypatch)
    old = tmp_path / "old.jsonl"
    old.write_text((FIXTURES / "limit_with_reset.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    recent = tmp_path / "recent.jsonl"
    recent.write_text(
        "\n".join('{"ignored":%d}' % number for number in range(5000))
        + "\n"
        + (FIXTURES / "limit_with_reset.jsonl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    scheduled = []
    monkeypatch.setattr(module.glob, "glob", lambda *_args, **_kwargs: [str(old), str(recent)])
    monkeypatch.setattr(module.os.path, "getmtime", lambda path: NOW.timestamp() - (1200 if path == str(old) else 1))
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("whole-file read")))

    module.scan(clock=lambda: NOW, scheduler=scheduled.append)

    assert len(scheduled) == 1


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


def _fake_gdbus(tmp_path):
    calls = tmp_path / "gdbus.calls"
    gdbus = tmp_path / "gdbus"
    gdbus.write_text(
        "#!/usr/bin/env bash\nprintf '%q ' \"$@\" >> \"$GDBUS_CALLS\"\nprintf '\\n' >> \"$GDBUS_CALLS\"\nif [[ \"$*\" == *'.SendKeys'* ]]; then printf '(%s,)\\n' \"${GDBUS_SEND_RESULT:-true}\"; else printf '(uint32 %s,)\\n' \"${GDBUS_NOTIFY_ID:-1}\"; fi\n",
        encoding="utf-8",
    )
    gdbus.chmod(0o755)
    return calls


def _send_environment(tmp_path, monkeypatch):
    _environment(tmp_path, monkeypatch)
    RuntimeState().record_pane("codex", "codex-session-limit", "codex-pane-now")
    calls = _fake_gdbus(tmp_path)
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return calls


def _send_command(*arguments):
    return [
        str(ADAPTER / "send.sh"),
        *arguments,
        "--generation",
        os.environ["CODEX_AUTORESUME_GENERATION"],
        "--config",
        os.environ["TERMINATOR_AGENT_NOTIFY_CONFIG"],
    ]


def test_send_resolves_the_current_pane_then_sends_message_and_enter(tmp_path, monkeypatch):
    """Using a stale pane mapping would type into a closed or wrong terminal."""
    calls = _send_environment(tmp_path, monkeypatch)

    subprocess.run(
        _send_command(
            "--session",
            "codex-session-limit",
            "--message",
            "resume work",
            "--notify",
        ),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    sent = calls.read_text(encoding="utf-8").splitlines()
    assert any(".SendKeys codex-pane-now $'resume work\\r'" in call for call in sent)
    assert any(".Notify codex codex-session-limit '' codex-pane-now waiting" in call for call in sent)


def test_false_send_ack_fails_without_notification(tmp_path, monkeypatch):
    """Treating a false SendKeys reply as success would report a message never sent."""
    calls = _send_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("GDBUS_SEND_RESULT", "false")

    result = subprocess.run(
        _send_command("--session", "codex-session-limit", "--notify"),
        env=os.environ.copy(), capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert not any(".Notify" in call for call in calls.read_text(encoding="utf-8").splitlines())


def test_zero_notification_reply_uses_fallback(tmp_path, monkeypatch):
    """A zero notification ID is not a successful notification acknowledgement."""
    _send_environment(tmp_path, monkeypatch)
    fallback = tmp_path / "notify-send.calls"
    notify_send = tmp_path / "notify-send"
    notify_send.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n', encoding="utf-8")
    notify_send.chmod(0o755)
    monkeypatch.setenv("GDBUS_NOTIFY_ID", "0")
    monkeypatch.setenv("NOTIFY_SEND_CALLS", str(fallback))

    subprocess.run(
        _send_command("--session", "codex-session-limit", "--notify"),
        env=os.environ.copy(), check=True, capture_output=True, text=True,
    )

    assert fallback.exists()


def test_generic_notification_controls_reach_codex_send_fallback(
    tmp_path, monkeypatch
):
    calls = _send_environment(tmp_path, monkeypatch)
    fallback = tmp_path / "notify-send.calls"
    notify_send = tmp_path / "notify-send"
    notify_send.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$NOTIFY_SEND_CALLS"\n',
        encoding="utf-8",
    )
    notify_send.chmod(0o755)
    monkeypatch.setenv("GDBUS_NOTIFY_ID", "0")
    monkeypatch.setenv("NOTIFY_SEND_CALLS", str(fallback))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_EXPIRY_MS", "4321")

    subprocess.run(
        _send_command("--session", "codex-session-limit", "--notify"),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    assert "--expire-time=4321" in fallback.read_text(encoding="utf-8")
    assert any(".Notify" in call for call in calls.read_text(encoding="utf-8").splitlines())


def test_generic_notification_disable_keeps_codex_send_but_skips_notice(
    tmp_path, monkeypatch
):
    calls = _send_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "0")

    subprocess.run(
        _send_command("--session", "codex-session-limit", "--notify"),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
    )

    recorded = calls.read_text(encoding="utf-8")
    assert ".SendKeys" in recorded
    assert ".Notify" not in recorded
