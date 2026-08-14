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
