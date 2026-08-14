# Final Fix — Notification Safety Report

## Status

Complete. The implementation is committed as `49fb6a1` (`fix: harden plugin notification safety`). Only the five owned implementation/test files were staged in that commit. This report is committed separately because it records the implementation commit hash.

## Root-cause investigation

1. **Notification daemon generations were conflated.** Metadata contained only the numeric daemon ID and request fields. Signal handling queried the daemon's current owner at callback time, but metadata did not record the owner that created each ID. After a daemon restart, a reused numeric ID could attach a new daemon signal to stale request metadata. There was no `NameOwnerChanged` subscription to retire the old generation.
2. **Signal setup health was not part of the `Notify` gate.** Partial action/close registration removed the first match, but the exported service continued creating actionable notifications. Owner lookup was deferred until signal validation, leaving no fail-closed creation-time check.
3. **Duplicate IDs left cross-request indexes.** `_track` overwrote `_notif_meta[id]`, while the same ID remained in old session/request sets. Dismissing the old request could close or untrack the newer request that reused the ID.
4. **Runtime validation followed hostile paths.** `_ensure_directory` called `mkdir(..., exist_ok=True)` and then `Path.chmod`, which follows symlinks. It did not validate type or ownership before changing permissions. `_atomic_write` repeated the unsafe parent pattern.
5. **Logging used a predictable shared file.** Without XDG runtime state the path was `/tmp/terminator-agent-notify.log`; normal append-open followed symlinks and relied on the process umask.
6. **VTE reconciliation used one exception boundary.** One terminal's `vte.connect` failure escaped the loop and prevented all later terminals from being connected.

## Implemented behavior

- Every notification metadata record includes the notification daemon's cached unique D-Bus owner.
- The plugin registers `ActionInvoked`, `NotificationClosed`, and `org.freedesktop.DBus.NameOwnerChanged` before declaring tracking healthy, then resolves the current notification owner.
- Daemon loss/change retires every tracked Codex permission with a request-closed marker and clears all metadata/session/request indexes without closing an ID against the new daemon generation.
- Signal registration or owner lookup failure leaves tracking unhealthy. All plugin notifications are actionable (focus at minimum), so `Notify` fails safely with ID `0`.
- Callbacks compare against the cached generation, and `FocusService` also checks that a signal owner matches the owner stored with the notification.
- Duplicate IDs retire the displaced Codex request and scrub the ID from every index before tracking the replacement.
- `RuntimeState` uses `lstat` before permission changes, rejects symlinks, non-directories, and foreign ownership, then opens with `O_DIRECTORY|O_NOFOLLOW`, revalidates with `fstat`, and applies mode `0700` through the descriptor.
- Logs use `XDG_STATE_HOME/terminator-agent-notify/plugin.log` when configured, otherwise the private runtime root (including `/tmp/terminator-agent-notify-<uid>/plugin.log` without XDG). Creation uses `O_NOFOLLOW`, owner/type validation, and mode `0600`.
- Implemented `TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS` (enabled by default), `TERMINATOR_AGENT_NOTIFY_EXPIRY_MS` (default `0`), and `TERMINATOR_AGENT_NOTIFY_LOG_LEVEL` (`quiet`, `info` default, `debug`). Expiry is passed to the daemon.
- VTE connection failures are caught per terminal so reconciliation continues.

## TDD evidence

### Baseline

```text
pytest -q tests/test_runtime_state.py tests/test_plugin_metadata.py
.......................                                                  [100%]
23 passed in 0.15s
```

### RED 1 — runtime directory and log safety

The selected two runtime-root and two log tests produced:

```text
FFFF                                                                     [100%]
4 failed in 0.05s
```

Expected failures showed: a symlink root did not raise, foreign ownership did not raise, the log parent was shared rather than private, and a log symlink appended to its victim.

### GREEN 1

```text
....                                                                     [100%]
4 passed in 0.01s
```

Additional no-XDG UID-root and non-directory checks:

```text
..                                                                       [100%]
2 passed in 0.01s
```

### RED 2 — generation, unhealthy setup, duplicate IDs, env controls

The nine selected tests produced:

```text
FFFFFFFFF                                                                [100%]
9 failed in 0.07s
```

Failures matched missing behavior: only two receivers; unhealthy setup still returned ID `1`; metadata lacked an owner; no owner callback existed; duplicate IDs did not close displaced requests; notifications ignored disablement; expiry remained `0`; and `_log` had no level control.

### GREEN 2

```text
.........                                                                [100%]
9 passed in 0.07s
```

### RED/GREEN 3 — VTE isolation

RED:

```text
F                                                                        [100%]
1 failed in 0.04s
```

The first VTE registered, the second raised, and the third was absent. GREEN plus focused regression:

```text
.                                                                        [100%]
1 passed in 0.01s
...................................                                      [100%]
35 passed in 0.21s
```

## Final verification

Fresh pre-commit focused verification:

```text
pytest -q tests/test_runtime_state.py tests/test_plugin_metadata.py
.....................................                                    [100%]
37 passed in 0.19s
```

The same verification invocation ran the following; both exited `0` without output:

```text
python -m py_compile core/runtime_state.py terminator-plugin/agent_notify.py tests/fakes/terminator_runtime.py tests/test_runtime_state.py tests/test_plugin_metadata.py
git diff --check -- core/runtime_state.py terminator-plugin/agent_notify.py tests/fakes/terminator_runtime.py tests/test_runtime_state.py tests/test_plugin_metadata.py
```

Latest full shared-worktree verification after concurrent tracks updated:

```text
pytest -q
........................................................................ [ 53%]
..............................................................           [100%]
134 passed in 4.92s
```

An earlier full run observed seven cross-track adapter env failures (`125 passed, 7 failed`). Those files were not touched here. The latest run confirms the concurrent fixes have landed and the complete suite is green.

## Owned files

- `core/runtime_state.py`
- `terminator-plugin/agent_notify.py`
- `tests/fakes/terminator_runtime.py`
- `tests/test_runtime_state.py`
- `tests/test_plugin_metadata.py`
- `.superpowers/sdd/2026-08-14-terminator-agent-notify-implementation/final-fix-notification-report.md`

## Concerns / remaining validation

- Tests use in-process D-Bus/Terminator fakes. A real desktop release check should verify daemon-restart ordering and `NameOwnerChanged` delivery with the target D-Bus binding.
- Invalid expiry values intentionally use the persistent default (`0`); negative values clamp to `0`.
- No adapter, installer, README, systemd, CI, or layout-rename file was edited or staged by this track.
