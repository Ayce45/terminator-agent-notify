import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

CONFIG_WITH_PLUGINS = """[global_config]
  enabled_plugins = LaunchpadBugURLHandler, APTURLHandler
[profiles]
  [[default]]
    background_color = "#300a24"
"""


def load_module():
    path = ROOT / "scripts" / "activate-plugin.py"
    spec = importlib.util.spec_from_file_location("activate_plugin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return load_module()


@pytest.fixture()
def paths(tmp_path):
    return tmp_path / "config", tmp_path / "plugin-activation-owned"


def backups(config):
    return sorted(config.parent.glob(config.name + ".bak.*"))


def test_install_appends_agent_notify_and_records_ownership(module, paths):
    config, owned = paths
    config.write_text(CONFIG_WITH_PLUGINS, encoding="utf-8")
    config.chmod(0o600)

    assert module.install(config, owned) == 0

    text = config.read_text(encoding="utf-8")
    assert (
        "  enabled_plugins = LaunchpadBugURLHandler, APTURLHandler, AgentNotify\n"
        in text
    )
    assert 'background_color = "#300a24"' in text
    assert owned.is_file()
    assert len(backups(config)) == 1
    assert (config.stat().st_mode & 0o777) == 0o600


def test_install_is_idempotent_without_new_backup(module, paths):
    config, owned = paths
    config.write_text(CONFIG_WITH_PLUGINS, encoding="utf-8")

    module.install(config, owned)
    first = config.read_text(encoding="utf-8")
    module.install(config, owned)

    assert config.read_text(encoding="utf-8") == first
    assert len(backups(config)) == 1


def test_install_leaves_missing_config_alone(module, paths, capsys):
    config, owned = paths

    assert module.install(config, owned) == 0

    assert not config.exists()
    assert not owned.exists()
    assert "enable AgentNotify" in capsys.readouterr().err


def test_install_leaves_config_without_enabled_plugins_alone(module, paths, capsys):
    config, owned = paths
    original = "[profiles]\n  [[default]]\n"
    config.write_text(original, encoding="utf-8")

    assert module.install(config, owned) == 0

    assert config.read_text(encoding="utf-8") == original
    assert not owned.exists()
    assert not backups(config)
    assert "enable AgentNotify" in capsys.readouterr().err


def test_install_does_not_claim_user_enabled_entry(module, paths):
    config, owned = paths
    config.write_text(
        "[global_config]\n  enabled_plugins = AgentNotify, APTURLHandler\n",
        encoding="utf-8",
    )
    original = config.read_text(encoding="utf-8")

    assert module.install(config, owned) == 0

    assert config.read_text(encoding="utf-8") == original
    assert not owned.exists()
    assert not backups(config)


def test_install_handles_empty_plugin_list(module, paths):
    config, owned = paths
    config.write_text("[global_config]\n  enabled_plugins = \n", encoding="utf-8")

    assert module.install(config, owned) == 0

    assert "  enabled_plugins = AgentNotify\n" in config.read_text(encoding="utf-8")
    assert owned.is_file()


def test_uninstall_removes_only_owned_entry(module, paths):
    config, owned = paths
    config.write_text(CONFIG_WITH_PLUGINS, encoding="utf-8")
    module.install(config, owned)

    assert module.uninstall(config, owned) == 0

    text = config.read_text(encoding="utf-8")
    assert "AgentNotify" not in text
    assert (
        "  enabled_plugins = LaunchpadBugURLHandler, APTURLHandler\n" in text
    )
    assert 'background_color = "#300a24"' in text
    assert not owned.exists()


def test_uninstall_without_ownership_leaves_config_alone(module, paths):
    config, owned = paths
    config.write_text(
        "[global_config]\n  enabled_plugins = AgentNotify, APTURLHandler\n",
        encoding="utf-8",
    )
    original = config.read_text(encoding="utf-8")

    assert module.uninstall(config, owned) == 0

    assert config.read_text(encoding="utf-8") == original


def test_uninstall_drops_stale_ownership_when_entry_already_gone(module, paths):
    config, owned = paths
    config.write_text(CONFIG_WITH_PLUGINS, encoding="utf-8")
    owned.write_text("AgentNotify\n", encoding="utf-8")

    assert module.uninstall(config, owned) == 0

    assert config.read_text(encoding="utf-8") == CONFIG_WITH_PLUGINS
    assert not owned.exists()


def test_uninstall_retains_ownership_when_config_unsupported(module, paths, capsys):
    config, owned = paths
    original = "[profiles]\n  [[default]]\n"
    config.write_text(original, encoding="utf-8")
    owned.write_text("AgentNotify\n", encoding="utf-8")

    assert module.uninstall(config, owned) == 0

    assert config.read_text(encoding="utf-8") == original
    assert owned.exists()
    assert "AgentNotify" in capsys.readouterr().err
