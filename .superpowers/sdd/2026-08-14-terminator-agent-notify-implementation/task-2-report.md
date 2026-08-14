# Task 2 report: private agent-neutral runtime state

## Delivered

- Added `core.runtime_state.RuntimeState` with optional explicit root and the XDG runtime directory/default `/tmp` fallback.
- Added SHA-256-derived paths for pane and decision state; raw agent, session, and request identifiers are never interpolated into persistent paths.
- Added atomic 0600 file writes through sibling temporary files and `os.replace`.
- Added one-shot decision consumption using an atomic `os.replace` claim before reading and removing the claimed file.
- Added validation for the supported agents (`claude`, `codex`) and decision values (`allow`, `deny`).
- Added focused tests for isolation, single-use decisions, path hashing/privacy, permissions, validation, and default-root selection.

## Verification

- `python -m pytest tests/test_runtime_state.py -v`: 5 passed.
- `python -m pytest -v`: 6 passed.

The bare `pytest tests/test_runtime_state.py -v` launcher in this environment does not put the repository root on `sys.path` and therefore reports an import error; `python -m pytest` (and `PYTHONPATH=. pytest`) passes.

## Concerns

No functional concerns. The runtime state root is chmod-ed to 0700 when initialized and state directories are likewise enforced; callers should still avoid placing unrelated files under an explicitly supplied root.

