import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MARKER = "terminator-agent-notify:"


def load_configurator(agent):
    path = ROOT / "adapters" / agent / "configure.py"
    spec = importlib.util.spec_from_file_location(f"{agent}_configure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("agent", "filename", "unrelated_event"),
    [
        ("claude", "settings.json", "Stop"),
        ("codex", "hooks.json", "SessionStart"),
    ],
)
def test_install_merges_unrelated_hooks_and_is_byte_idempotent(
    tmp_path, agent, filename, unrelated_event
):
    """A duplicate-owned-entry bug or whole-array replacement breaks this test."""
    configure = load_configurator(agent)
    config_path = tmp_path / filename
    unrelated = {
        "description": "user-owned hook",
        "hooks": [{"type": "command", "command": "/opt/user/hook"}],
    }
    config_path.write_text(
        json.dumps({"theme": "dark", "hooks": {unrelated_event: [unrelated]}}),
        encoding="utf-8",
    )

    configure.install(config_path, Path("/opt/terminator-agent-notify"))
    first_bytes = config_path.read_bytes()
    first_backups = sorted(tmp_path.glob(f"{filename}.bak.*"))
    configure.install(config_path, Path("/opt/terminator-agent-notify"))

    assert config_path.read_bytes() == first_bytes
    assert sorted(tmp_path.glob(f"{filename}.bak.*")) == first_backups
    assert len(first_backups) == 1
    result = json.loads(first_bytes)
    assert result["theme"] == "dark"
    assert unrelated in result["hooks"][unrelated_event]
    owned = [
        entry
        for entries in result["hooks"].values()
        for entry in entries
        if entry.get("description", "").startswith(MARKER)
    ]
    assert owned
    assert all(entry["description"].startswith(f"{MARKER}{agent}:") for entry in owned)


@pytest.mark.parametrize(
    ("agent", "filename"),
    [("claude", "settings.json"), ("codex", "hooks.json")],
)
def test_uninstall_removes_only_owned_entries(tmp_path, agent, filename):
    """Removing whole hook events instead of marker-owned entries breaks this test."""
    configure = load_configurator(agent)
    config_path = tmp_path / filename
    unrelated = {
        "description": "keep me",
        "hooks": [{"type": "command", "command": "/opt/user/hook"}],
    }
    config_path.write_text(
        json.dumps({"hooks": {"SessionStart": [unrelated]}, "keep": True}),
        encoding="utf-8",
    )
    configure.install(config_path, Path("/opt/terminator-agent-notify"))

    configure.uninstall(config_path)

    result = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["keep"] is True
    assert unrelated in result["hooks"]["SessionStart"]
    assert not any(
        entry.get("description", "").startswith(MARKER)
        for entries in result["hooks"].values()
        for entry in entries
    )


def test_codex_permission_hook_outlives_default_approval_wait(tmp_path):
    """A hook timeout shorter than the 300-second approval wait breaks this test."""
    configure = load_configurator("codex")
    config_path = tmp_path / "hooks.json"

    configure.install(config_path, Path("/opt/terminator-agent-notify"))

    config = json.loads(config_path.read_text(encoding="utf-8"))
    owned = [
        entry
        for entry in config["hooks"]["PermissionRequest"]
        if entry["description"].startswith(MARKER)
    ]
    assert len(owned) == 1
    assert owned[0]["hooks"][0]["timeout"] >= 300


@pytest.mark.parametrize(
    ("agent", "filename"),
    [("claude", "settings.json"), ("codex", "hooks.json")],
)
def test_installed_commands_quote_paths_with_spaces(tmp_path, agent, filename):
    """Emitting an unquoted executable path under an XDG path with spaces breaks this."""
    configure = load_configurator(agent)
    config_path = tmp_path / filename

    configure.install(config_path, Path("/opt/agent notify"))

    config = json.loads(config_path.read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in config["hooks"].values()
        for entry in entries
        if entry.get("description", "").startswith(MARKER)
        for hook in entry["hooks"]
    ]
    assert commands
    assert all(command.startswith("'/opt/agent notify/") for command in commands)
    assert all(command.endswith("'") for command in commands)


def test_invalid_json_is_not_replaced_or_backed_up(tmp_path):
    """Replacing malformed user configuration instead of aborting breaks this test."""
    configure = load_configurator("claude")
    config_path = tmp_path / "settings.json"
    original = b'{"hooks": invalid}\n'
    config_path.write_bytes(original)

    with pytest.raises(json.JSONDecodeError):
        configure.install(config_path, Path("/opt/terminator-agent-notify"))

    assert config_path.read_bytes() == original
    assert list(tmp_path.glob("settings.json.bak.*")) == []


@pytest.mark.parametrize(
    ("agent", "filename"),
    [("claude", "settings.json"), ("codex", "hooks.json")],
)
def test_uninstall_without_owned_hooks_is_byte_noop(tmp_path, agent, filename):
    """Reserializing an unrelated-only config during uninstall breaks this test."""
    configure = load_configurator(agent)
    config_path = tmp_path / filename
    original = b'{ "hooks": {"Stop": []}, "user": true }\n'
    config_path.write_bytes(original)

    assert configure.uninstall(config_path) is False

    assert config_path.read_bytes() == original
    assert list(tmp_path.glob(f"{filename}.bak.*")) == []
