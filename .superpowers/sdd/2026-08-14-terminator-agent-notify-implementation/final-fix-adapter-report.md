# Final Adapter Hardening Report

Date: 2026-08-14

Implementation commit: `792a7ea33684764f4768f728f3c3a61766d6d99b`

## Scope and outcomes

- Codex approval waits now default to 300 seconds, accept zero, clamp every larger valid finite value to 300, and fall back to 300 for negative, non-finite, or malformed values.
- The emitted Codex PermissionRequest host timeout is 316 seconds. This is strictly greater than the 300-second maximum local wait plus two 5-second D-Bus budgets plus a 5-second cleanup margin.
- Every Claude shell invocation of `gdbus` and `notify-send` is bounded with GNU `timeout`; the two Python notification calls were already bounded and remain so.
- Claude `send.sh` accepts only the exact `gdbus` SendKeys response `(true,)`. Its notification path accepts only an exact positive `uint32` tuple and otherwise uses `notify-send` fallback.
- Claude SessionStart now queries `io.github.TerminatorAgentNotify.GetFocusedUUID` first and only tries the legacy Terminator D-Bus discovery when the shared service returns no UUID.
- Codex autoresume recognizes exact structured `rate_limit_exceeded` status values, accepts nested structured reset fields and timezone offsets, prunes successful markers older than seven days even when the current transcript has no limit, and uses a 16-hex session hash in transient unit names.
- The adapters consume the generic `TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS`, `TERMINATOR_AGENT_NOTIFY_EXPIRY_MS`, and `TERMINATOR_AGENT_NOTIFY_LOG_LEVEL` controls. Unset variables preserve prior adapter behavior.
- Durable autoresume generation gating, installer/uninstaller/systemd changes, README changes, package renaming, plugin changes, and core runtime changes were intentionally left to the controller/other tracks.

## Root causes

1. `permission_request._approval_timeout()` validated finite/nonnegative values but returned them without an upper bound. Meanwhile, `configure.py` emitted a 310-second host timeout, which could not cover the 300-second wait, both possible 5-second D-Bus calls, and cleanup margin.
2. Claude shell adapters called desktop processes directly. A stuck D-Bus service or notification daemon therefore inherited the lifetime of the hook. `send.sh` also checked only process exit status, so false and malformed D-Bus values were treated as acknowledgements.
3. Claude SessionStart skipped the shared service and started with legacy service-name discovery, missing the stable cross-agent API.
4. Codex's prose regex deliberately used word boundaries; because underscore is a word character, the exact structured value `rate_limit_exceeded` was not recognized. Reset extraction accepted only prose such as “resets at”, not structured nested reset fields. Successful marker files had no retention policy, and eight hex characters were unnecessarily collision-prone for transient unit names.
5. Adapter paths did not consume the generic notification, expiry, and log-level values now persisted by the controller track.

## TDD evidence

### Initial boundary regressions — RED

Command:

```text
pytest -q tests/test_configuration.py::test_codex_permission_hook_leaves_cleanup_margin_after_maximum_wait tests/test_codex_hooks.py::test_approval_timeout_clamps_large_values_and_rejects_invalid_values tests/test_claude_adapter.py::test_session_start_prefers_shared_focused_uuid_before_legacy_service tests/test_claude_adapter.py::test_send_rejects_every_sendkeys_reply_except_exact_true_tuple tests/test_claude_adapter.py::test_send_notify_uses_fallback_unless_reply_is_positive_uint32 tests/test_claude_adapter.py::test_claude_hooks_bound_hung_desktop_commands tests/test_claude_adapter.py::test_claude_send_bounds_hung_sendkeys_call tests/test_codex_autoresume.py::test_nested_status_only_limit_with_offset_reset_schedules tests/test_codex_autoresume.py::test_near_match_status_does_not_count_as_rate_limit tests/test_codex_autoresume.py::test_old_successful_markers_are_pruned_but_recent_markers_remain
```

Observed: `18 failed, 8 passed`. Failures showed missing timeout constants, unbounded large waits, ignored D-Bus reply bodies, no shared SessionStart lookup, hung processes exceeding the test deadline, failure to parse the structured nested fixture, and retained old markers. The near-match status test already passed, confirming the desired exact-status addition would not need broader substring matching.

### Focused boundary regressions — GREEN

The same focused set after minimal fixes reported:

```text
26 passed in 1.23s
```

### Marker lifecycle strengthening — RED/GREEN

After changing the marker regression to use a transcript with no current limit:

```text
pytest -q tests/test_codex_autoresume.py::test_old_successful_markers_are_pruned_but_recent_markers_remain
```

Observed RED: `1 failed`; old markers were only pruned on an actionable schedule. Moving pruning to the start of enabled processing produced GREEN: `1 passed`.

### Generic environment controls — RED/GREEN

Seven focused tests covering notification disablement and fallback expiry initially reported `7 failed`. After propagating the exact generic variables, the same command reported `7 passed in 0.27s`.

The log-level regression initially reported `1 failed` because `_log()` always wrote to stderr. Respecting `TERMINATOR_AGENT_NOTIFY_LOG_LEVEL=quiet` produced `1 passed`.

## Final verification

Fresh pre-commit verification:

```text
pytest -q tests/test_claude_adapter.py tests/test_codex_hooks.py tests/test_codex_autoresume.py tests/test_configuration.py
96 passed in 4.77s
```

Also passed with exit code 0:

```text
bash -n adapters/claude/send.sh adapters/claude/hooks/auto-resume-on-limit.sh adapters/claude/hooks/notify-cleanup.sh adapters/claude/hooks/notify-waiting.sh adapters/claude/hooks/session-start.sh adapters/codex/send.sh
python3 -m compileall -q adapters/claude adapters/codex
git diff --check -- <all adapter-owned paths>
```

## Files in the implementation commit

- `adapters/claude/autoresume.py`
- `adapters/claude/send.sh`
- `adapters/claude/hooks/session-start.sh`
- `adapters/claude/hooks/notify-waiting.sh`
- `adapters/claude/hooks/notify-cleanup.sh`
- `adapters/codex/autoresume.py`
- `adapters/codex/common.py`
- `adapters/codex/configure.py`
- `adapters/codex/send.sh`
- `adapters/codex/hooks/permission_request.py`
- `tests/test_claude_adapter.py`
- `tests/test_codex_autoresume.py`
- `tests/test_codex_hooks.py`
- `tests/test_configuration.py`
- `tests/fixtures/codex/limit_status_only_nested.jsonl`

## Concerns and coordination notes

- GNU `timeout` is now required by the Claude shell adapter boundaries. It is provided by GNU coreutils on the supported Linux target, but dependency checking/documentation belongs to the controller's installer/README scope.
- Persisted hook/service environment wiring is owned by the controller. Adapter consumption uses the exact variable names the controller confirmed.
- An independent review subagent was requested as required by the review skill, but the shared thread limit was full. A manual requirement/diff audit and fresh complete focused verification were performed; the controller should include this commit in its final integrated review.
- Other agents had concurrent unstaged installer/systemd changes in the shared worktree. They were not staged or committed by this track.
