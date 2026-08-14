from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_baseline_files_are_present():
    for relative in (
        "LICENSE",
        "assets/claude.png",
        "assets/claude.svg",
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
    ):
        assert required_text in readme
