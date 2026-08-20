from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_baseline_files_are_present():
    for relative in (
        "LICENSE",
        "assets/claude.png",
        "assets/claude.svg",
        "adapters/__init__.py",
        "adapters/codex/__init__.py",
        "scripts/force-xwayland.sh",
        "scripts/relaunch-terminator.sh",
    ):
        assert (ROOT / relative).is_file(), relative


def test_readme_documents_installation_security_and_migration_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for required_text in (
        "./install.sh claude",
        "./install.sh codex",
        "./install.sh both",
        "PermissionRequest",
        "XWayland",
        "CODEX_AUTORESUME=1",
        "claude-terminator-notify",
        "./uninstall.sh claude",
        "./uninstall.sh codex",
        "./uninstall.sh both",
        "TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS",
        "TERMINATOR_AGENT_NOTIFY_EXPIRY_MS",
        "TERMINATOR_AGENT_NOTIFY_LOG_LEVEL",
        "returns immediately without a",
        "terminator-agent-notify-codex-limit-poller.timer",
        "terminator-agent-notify-claude-limit-poller.timer",
        "rotating generation token",
        "terminator_agent_notify_core",
    ):
        assert required_text in readme


def test_documented_and_ci_verification_cover_installed_layout():
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
    required_commands = (
        "bash tests/test_installation.sh",
        "python -m compileall -q terminator_agent_notify_core adapters terminator-plugin",
    )

    for command in required_commands:
        assert command in contributing
        assert command in workflow
