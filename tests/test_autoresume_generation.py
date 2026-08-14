import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]


def _fake_gdbus(tmp_path: Path, monkeypatch) -> Path:
    calls = tmp_path / "gdbus.calls"
    executable = tmp_path / "gdbus"
    executable.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$GDBUS_CALLS\"\nprintf '(true,)\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return calls


def _write_config(path: Path, agent: str, enabled: str, generation: str) -> None:
    prefix = agent.upper()
    path.write_text(
        f"{prefix}_AUTORESUME={enabled}\n"
        f"{prefix}_AUTORESUME_GENERATION={generation}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _scheduled_send(
    agent: str,
    config: Path,
    generation: str,
    *,
    notify: bool = False,
    resume: bool = False,
) -> subprocess.CompletedProcess:
    command = [
        str(ROOT / "adapters" / agent / "send.sh"),
        "--pane",
        "safe-pane",
        "--message",
        "continue",
        "--generation",
        generation,
        "--config",
        str(config),
    ]
    if notify:
        command.append("--notify")
    if resume:
        command.append("--resume")
    return subprocess.run(
        command,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_stale_or_disabled_scheduled_generation_never_injects(
    tmp_path, monkeypatch, agent
):
    """A job armed before disable/reinstall must become inert at execution."""
    calls = _fake_gdbus(tmp_path, monkeypatch)
    config = tmp_path / "environment"
    _write_config(config, agent, "1", "current-generation")

    stale = _scheduled_send(agent, config, "stale-generation")
    assert stale.returncode == 0
    assert not calls.exists()

    _write_config(config, agent, "0", "current-generation")
    disabled = _scheduled_send(agent, config, "current-generation")
    assert disabled.returncode == 0
    assert not calls.exists()


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_current_enabled_scheduled_generation_can_inject(tmp_path, monkeypatch, agent):
    """The guard must not suppress the currently authorized transient job."""
    calls = _fake_gdbus(tmp_path, monkeypatch)
    config = tmp_path / "environment"
    _write_config(config, agent, "1", "current-generation")

    result = _scheduled_send(agent, config, "current-generation")

    assert result.returncode == 0, result.stderr
    assert ".SendKeys safe-pane" in calls.read_text(encoding="utf-8")


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_scheduled_send_loads_persisted_notification_disable(
    tmp_path, monkeypatch, agent
):
    calls = _fake_gdbus(tmp_path, monkeypatch)
    monkeypatch.delenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", raising=False)
    config = tmp_path / "environment"
    _write_config(config, agent, "1", "current-generation")
    with config.open("a", encoding="utf-8") as stream:
        stream.write("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=0\n")

    result = _scheduled_send(
        agent, config, "current-generation", notify=True
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text(encoding="utf-8")
    assert ".SendKeys safe-pane" in recorded
    assert ".Notify" not in recorded


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_legacy_scheduled_send_without_generation_is_inert(
    tmp_path, monkeypatch, agent
):
    """Pre-generation transient units must fail closed after an upgrade."""
    calls = _fake_gdbus(tmp_path, monkeypatch)
    command = [
        str(ROOT / "adapters" / agent / "send.sh"),
        "--pane",
        "safe-pane",
        "--message",
        "continue",
        "--notify",
    ]
    if agent == "claude":
        command.append("--resume")

    result = subprocess.run(
        command,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not calls.exists()


def test_claude_rechecks_generation_before_each_resume_injection(
    tmp_path, monkeypatch
):
    """Disabling during the dialog-safe sequence must stop remaining keys."""
    calls = tmp_path / "gdbus.calls"
    config = tmp_path / "environment"
    _write_config(config, "claude", "1", "current-generation")
    executable = tmp_path / "gdbus"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$GDBUS_CALLS\"\n"
        "if [ ! -e \"$ROTATION_MARKER\" ]; then\n"
        "  printf 'CLAUDE_AUTORESUME=0\\nCLAUDE_AUTORESUME_GENERATION=rotated\\n' > \"$ROTATE_CONFIG\"\n"
        "  : > \"$ROTATION_MARKER\"\n"
        "fi\n"
        "printf '(true,)\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    sleep = tmp_path / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    monkeypatch.setenv("GDBUS_CALLS", str(calls))
    monkeypatch.setenv("ROTATE_CONFIG", str(config))
    monkeypatch.setenv("ROTATION_MARKER", str(tmp_path / "rotated"))
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    result = _scheduled_send(
        "claude", config, "current-generation", resume=True
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").count(".SendKeys") == 1
