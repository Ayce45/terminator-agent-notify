# Task 1 Report: Import the Proven Claude Baseline

Status: DONE_WITH_CONCERNS

## Changes

- Added the pinned MIT `LICENSE` from `Ayce45/claude-terminator-notify@d3dffde4630e6813d031ce08be253bfedb142b84`.
- Republished the pinned `assets/claude.png` and `assets/claude.svg` unchanged.
- Republished `scripts/force-xwayland.sh` and `scripts/relaunch-terminator.sh` unchanged in behavior, adding the required origin comment after each shebang.
- Added `tests/test_baseline_layout.py` to assert all five baseline paths are present.

## Verification

- RED: `pytest tests/test_baseline_layout.py -v` failed because `LICENSE` was absent.
- GREEN: `pytest tests/test_baseline_layout.py -v` — 1 passed.
- Shell syntax: `bash -n scripts/force-xwayland.sh scripts/relaunch-terminator.sh` — passed.

## Concern for later documentation

The existing README warning should be preserved: the Claude icon is Anthropic artwork and is not covered by this project's MIT license. The assets were imported because republishing them was explicitly approved, but this licensing distinction should remain visible in the final documentation.
