# Manual Terminator release checklist

Run these checks in a real logged-in Linux desktop session, not in a headless
CI job. Capture screenshots with notification action buttons and terminal panes
visible where requested. Keep screenshots and copied log excerpts with the
release record; redact session IDs, paths, and command arguments when needed.

## Preconditions

- [ ] Record the desktop/compositor, `XDG_SESSION_TYPE`, Terminator version,
  Codex version, Claude Code version, notification daemon, and install target.
- [ ] Install with the exact command used, restart Terminator, and enable
  **AgentNotify** in Preferences.
- [ ] For Codex, inspect `~/.codex/hooks.json`, review each installed
  `terminator-agent-notify:` command path, and complete Codex's trust prompt.
- [ ] Start with `tail -f "${XDG_RUNTIME_DIR:-/tmp}/terminator-agent-notify.log"`
  and save relevant plugin log lines.

## Claude-only session

- [ ] Run `./install.sh claude`, start Claude Code in a known Terminator pane,
  then trigger a waiting/permission event and a completed task.
  Evidence: screenshot of each notification and matching plugin log entries.
- [ ] Click the notification body/default action from another Terminator window
  and confirm the exact originating tab and pane receive focus.
  Evidence: before/after screenshot and `focus:` log line.
- [ ] Approve and deny a Claude permission notification; confirm Enter and
  Escape respectively reach only the originating Claude pane.
  Evidence: terminal result and action log lines.
- [ ] Trigger a Claude usage limit and confirm the arm notification, scheduled
  timer fallback behavior, resume message, and post-reset action match the
  configured `CLAUDE_AUTORESUME_*` values.
  Evidence: `journalctl --user -u claude-limit-poller.service` excerpt.

## Codex-only session

- [ ] Run `./install.sh codex`, start Codex in a known Terminator pane, then
  trigger a completion. Confirm body click focuses the exact originating pane.
  Evidence: screenshot and plugin `focus:` log line.
- [ ] Trigger a native `PermissionRequest`. Test Approve and Deny in separate
  requests and confirm Codex receives the expected allow/deny outcome.
  Evidence: screenshots of actions, terminal results, and request-specific log
  lines (redact request IDs).
- [ ] Close/expire a `PermissionRequest` and wait for the timeout fallback.
  Confirm Codex presents its normal interactive approval UI, not an implicit
  approval or denial.
  Evidence: terminal screenshot and log/journal excerpt.
- [ ] With `CODEX_AUTORESUME=1 ./install.sh codex`, use a safe test account or
  fixture-shaped recent session to verify an unambiguous usage-limit reset is
  scheduled once. Also verify ambiguous/stale input schedules nothing.
  Evidence: `journalctl --user -u codex-limit-poller.service` and
  `systemctl --user list-timers` output.

## Simultaneous and display-backend checks

- [ ] Run `./install.sh both`, start one Claude and one Codex session in
  different panes/windows, and trigger overlapping notifications and approval
  requests. Confirm no notification action reaches the other agent or pane.
  Evidence: screenshots and plugin log lines showing distinct agent/session
  records.
- [ ] On X11 or XWayland, repeat a cross-window notification click and record
  exact-pane focus result.
- [ ] On native Wayland, repeat it and record whether the compositor permits
  the cross-window raise. If it refuses, confirm same-window tab/pane selection
  and document the limitation; do not mark this as an XWayland pass.
- [ ] If opting in to XWayland, run
  `FORCE_XWAYLAND=1 ./install.sh both`, completely relaunch Terminator, and
  repeat the cross-window focus test. Evidence: launch configuration plus
  before/after screenshots.

## Selective uninstall, migration, and rollback

- [ ] From a `both` installation, run `./uninstall.sh codex`; confirm Claude
  hooks and shared plugin remain, Codex hooks are removed, and Claude still
  notifies. Then test the inverse with `./uninstall.sh claude`.
  Evidence: relevant config snippets, plugin file presence, and a notification.
- [ ] Run `./uninstall.sh both`; confirm project hooks/plugin are removed while
  unrelated settings survive. Confirm copied `~/.claude/assets/claude.*` remain
  as documented; do not delete them without reviewing ownership.
- [ ] Follow the README migration path from `claude-terminator-notify`, checking
  unrelated hooks before and after. Record old/new versions and the exact old
  project removal command used.
- [ ] Exercise the documented rollback using selective uninstall and a scoped
  configuration restore. Evidence: settings diff and a working previous setup.

## Release record (complete during the release)

| Field | Record |
| --- | --- |
| Release / commit | Pending — enter tag and commit |
| Tester and date | Pending |
| Desktop/compositor and backend | Pending — include `XDG_SESSION_TYPE` |
| Terminator version | Pending — `terminator --version` |
| Codex version | Pending — `codex --version` |
| Claude Code version | Pending — `claude --version` |
| Notification daemon | Pending |
| Claude-only result and evidence location | Pending |
| Codex-only result and evidence location | Pending |
| Simultaneous-session result and evidence location | Pending |
| X11/XWayland focus result and evidence location | Pending |
| Native Wayland result/limitation and evidence location | Pending |
| Timeout fallback result and evidence location | Pending |
| Selective uninstall result and evidence location | Pending |
| Migration and rollback result and evidence location | Pending |
| Known exceptions / release decision | Pending |
