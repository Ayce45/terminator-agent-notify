# Terminator Agent Notify

Desktop notifications, exact-pane focus, and usage-limit resume support for
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) and Codex CLI in
Linux [Terminator](https://gnome-terminator.org/) sessions.

The project installs one long-lived Terminator plugin shared by the selected
agent adapters. Notifications for completion and attention requests can focus
the originating pane; Codex `PermissionRequest` notifications also expose
Approve and Deny actions.

> **Artwork notice.** `assets/claude.png` and `assets/claude.svg` were retained
> under the user-approved import from the Claude baseline. They are Anthropic
> property and are **not licensed under this repository's MIT license**. A
> Claude installation copies them to `~/.claude/assets/`; selective uninstall
> intentionally retains those copied Claude assets, so remove them manually
> only if you have confirmed nothing else uses them. `assets/codex-mark.png` and
> `assets/codex-mark.svg` are the OpenAI logo, OpenAI property, and are likewise
> **not licensed under this repository's MIT license**.

## Requirements

- Linux with Terminator and a desktop notification daemon implementing
  `org.freedesktop.Notifications`.
- Python 3. The installed Terminator Python environment must have its normal
  GTK and D-Bus bindings (`gi` and `dbus`), as provided by a working Terminator
  installation.
- `gdbus` for the full plugin integration, `jq` for Claude hooks, and GNU
  `timeout` to bound Claude desktop commands. The installer warns when any of
  these commands is missing. `notify-send` is optional and is only the
  non-actionable fallback when the plugin cannot be reached.
- Claude Code for the Claude adapter, Codex CLI for the Codex adapter, or both.
- `systemd --user` is needed only for automatic usage-limit resume. Units are
  installed without it but cannot be enabled.

The notification service and focus actions require a graphical user session;
automated tests do not need one.

## Install

Clone this repository and run exactly one target command from its root:

```bash
./install.sh claude
./install.sh codex
./install.sh both
```

The installer copies adapters below
`${XDG_DATA_HOME:-~/.local/share}/terminator-agent-notify`, installs the shared
plugin and uniquely named `terminator_agent_notify_core` package below
`${XDG_CONFIG_HOME:-~/.config}/terminator`, and records its
installation state below `${XDG_STATE_HOME:-~/.local/state}/terminator-agent-notify`.
It merges only entries marked `terminator-agent-notify:` into
`~/.claude/settings.json` or `~/.codex/hooks.json`; unrelated hooks and
settings remain in place. A changed JSON configuration is backed up beside the
original as `*.bak.<timestamp>`. Re-running an unchanged installation is
idempotent.

Fixed plugin, core-package, adapter, and systemd destinations are hash tracked.
The installer refuses to overwrite an unrelated or locally changed file at one
of those paths. Selective uninstall removes only a file whose current hash
still matches the project-owned manifest; locally changed files are preserved
with a warning. The user units are uniquely prefixed, for example
`terminator-agent-notify-claude-limit-poller.timer` and
`terminator-agent-notify-codex-limit-poller.timer`.

Restart Terminator, then enable **AgentNotify** in Terminator Preferences if it
is not already enabled. Start a Codex session after installing its adapter and
review and trust the newly installed hooks when Codex asks. Treat that prompt as
a code review: inspect `~/.codex/hooks.json` and the command paths it contains,
verify that they point to the installation directory you expect, and trust them
only if you accept their notification, pane-focus, and permission behavior.

### Wayland and XWayland

Within an existing Terminator window the plugin can still select the tab and
pane. Native Wayland compositors may refuse a notification-triggered
cross-window raise/focus, however. On a Wayland session the installer offers an
opt-in XWayland setup; non-interactive use can request it with:

```bash
FORCE_XWAYLAND=1 ./install.sh both
```

It changes only launch entries it can record as project-owned, using
`GDK_BACKEND=x11`. Existing user Terminator desktop overrides or X11 shortcuts
are left alone. The final selective uninstall reverses only the exact owned
changes; if a restore cannot be verified, the ownership record is retained for
a later retry. You can also run `scripts/force-xwayland.sh` manually, but its
generic `--undo` mode is separate from installer ownership tracking. Fully quit
and relaunch Terminator after changing its launch backend.

## Behavior and configuration

Completion and waiting notifications are dismissed when the matching pane gets
focus or when a new prompt is submitted. If the plugin or notification daemon
is unavailable, hooks do not block the agent: completion notifications may use
`notify-send`, while normal terminal interaction remains available.

Notification titles use the originating pane title without repeating the
agent name; the Claude or Codex icon already identifies the application.
Claude's own AI-generated terminal title is preserved. Codex has no equivalent
title field, so its `UserPromptSubmit` hook derives a local, whitespace-normalized
title from the first prompt (up to roughly 60 characters) and keeps it for the
session. The title is stored privately and restored into the new pane when an
existing session is opened with `codex resume`. Sessions created before title
tracking receive a title from their first new prompt. This does not call an AI
service and never delays prompt submission.

Codex handles its native `PermissionRequest` hook as follows:

- The hook registers the notification and returns immediately without a
  decision, so Codex's native terminal approval prompt is never held behind a
  notification wait.
- Approve sends Enter and Deny sends Escape to the exact originating pane. The
  terminal prompt remains the source of truth and can always be answered
  directly.
- A pane focus, mouse click, or key press dismisses that pane's notifications.
  A notification close, expiry, service failure, or stale action never implies
  approval or denial.

Generic notification and auto-resume values are captured by `install.sh` in the
private `${XDG_STATE_HOME:-~/.local/state}/terminator-agent-notify/environment`
file. Re-run the matching install command with new values to change them. Hooks
and the installed systemd user services read that file. Restart Terminator
after changing notification, expiry, or plugin log controls because the
long-lived plugin reads them when it loads. Runtime-only values such as the
Codex approval timeout are read directly from the agent process environment:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS` | `1` | Set exactly `0` to suppress desktop notifications without disabling pane tracking or cleanup. |
| `TERMINATOR_AGENT_NOTIFY_EXPIRY_MS` | unset | Non-negative expiry passed to notification backends. Leave unset to preserve each backend's established default; use a positive value for a consistent finite expiry. |
| `TERMINATOR_AGENT_NOTIFY_LOG_LEVEL` | `info` | `quiet` disables plugin logging, `info` records normal events, and `debug` adds diagnostic events. Invalid values use `info`. |
| `CLAUDE_AUTORESUME` | `1` | Set `0` to disable Claude's established auto-resume. |
| `CLAUDE_AUTORESUME_MESSAGE` | `continue` | Message sent after the Claude reset. |
| `CLAUDE_AUTORESUME_BUFFER` | `90` | Extra seconds after the parsed Claude reset time. |
| `CLAUDE_AUTORESUME_ARM_NOTIFY` | `1` | Set `0` to suppress the Claude “resume armed” notification. |
| `CODEX_AUTORESUME` | disabled | Codex resume is experimental and only runs when exactly `1`. |
| `CODEX_AUTORESUME_MESSAGE` | `continue` | Message sent after an eligible Codex reset. |
| `FORCE_XWAYLAND` | unset | Set to `1` during Wayland installation to opt in to owned XWayland launch setup. |

The installer also follows the standard XDG location variables. If they are
unset, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_STATE_HOME` default to
`~/.config`, `~/.local/share`, and `~/.local/state` respectively.
`XDG_SESSION_TYPE=wayland` is what enables the installer’s XWayland prompt.
`XDG_RUNTIME_DIR` selects the private runtime state directory. If
`XDG_STATE_HOME` is explicitly set, the plugin log is
`$XDG_STATE_HOME/terminator-agent-notify/plugin.log`; otherwise it is
`$XDG_RUNTIME_DIR/terminator-agent-notify/plugin.log`. Without either XDG
location, both use the private per-UID `/tmp/terminator-agent-notify-<uid>`
directory. Runtime and log roots reject symlinks, non-directories, and paths
owned by another UID before changing permissions; log files are opened without
following symlinks and kept at mode `0600`.
`TERMINATOR_UUID` is normally inherited from Terminator by a session-start hook
and should not need to be set manually; when absent, the hook tries the current
focused pane as a less reliable fallback.

To enable the experimental Codex timer during installation:

```bash
CODEX_AUTORESUME=1 ./install.sh codex
```

Codex auto-resume is fail-closed: it schedules only a fresh usage-limit event
with one unambiguous timezone-aware reset timestamp, no later progress signal,
and a reset within one week. It does nothing when session data is missing,
ambiguous, stale, or changes format. Claude and Codex scans run once per minute
when their user timers are enabled. The `continue` message is injected into the
mapped pane, so review any custom resume message as carefully as a command you
would type yourself.

Every delayed resume carries a rotating generation token from the private
environment file and validates that token immediately before injecting keys.
Reinstalling, disabling an adapter, or uninstalling it rotates/invalidates the
token and stops project-owned transient jobs, so an already scheduled old job
becomes inert. Claude auto-resume is enabled unless installed with
`CLAUDE_AUTORESUME=0`; Codex auto-resume is disabled unless installed with
`CODEX_AUTORESUME=1`.

## Logs and troubleshooting

The Terminator plugin appends to the private `plugin.log` location described
above; adapter auto-resume diagnostics go to the invoked service or command's
stderr (for example, `journalctl --user -u
terminator-agent-notify-codex-limit-poller.service`). Runtime pane mappings,
closure state and resume de-duplication markers are private
(`0700` directories and `0600` files) below
`${XDG_RUNTIME_DIR}/terminator-agent-notify`, or
`/tmp/terminator-agent-notify-<uid>` when `XDG_RUNTIME_DIR` is absent.

Useful checks:

```bash
systemctl --user status terminator-agent-notify-claude-limit-poller.timer terminator-agent-notify-codex-limit-poller.timer
journalctl --user -u terminator-agent-notify-claude-limit-poller.service -u terminator-agent-notify-codex-limit-poller.service
# Log: $XDG_STATE_HOME/terminator-agent-notify/plugin.log when XDG_STATE_HOME is set;
# otherwise $XDG_RUNTIME_DIR/terminator-agent-notify/plugin.log or
# /tmp/terminator-agent-notify-$(id -u)/plugin.log.
```

If focus works in the current window but not across windows on Wayland, use the
XWayland option above or accept the compositor limitation. If an approval
notification disappears, expires, or has no buttons, approve or deny in Codex's
normal terminal UI instead; do not assume any action was applied. If hooks do
not fire after installation, restart the agent, inspect its merged configuration
for `terminator-agent-notify:` entries, and complete Codex's hook trust review.
Restarting or replacing the desktop notification daemon retires all tracked
requests; pending Codex approvals then fall back to the terminal rather than
accepting an action from a reused daemon notification ID.

## Selective uninstall, migration, and rollback

Remove only the adapter you mean to remove:

```bash
./uninstall.sh claude
./uninstall.sh codex
./uninstall.sh both
```

Removing one adapter preserves the other and the shared plugin. The shared
plugin, shared core package, and project-owned XWayland changes are removed only
after no managed adapter remains. Uninstall removes only project-marked hook
entries; it never restores an entire backup over newer user changes. It also
deliberately leaves `~/.claude/assets/claude.png` and `claude.svg` in place (see
the artwork notice above).

To migrate from `claude-terminator-notify` safely:

1. Back up `~/.claude/settings.json`, `~/.codex/hooks.json` if present, and any
   custom Terminator configuration.
2. In the old `claude-terminator-notify` checkout, use its documented uninstall
   procedure to remove its plugin and hooks; do not overwrite current settings
   with an old whole-file backup.
3. Install this project with `./install.sh claude` or `./install.sh both`,
   restart Terminator, and check that unrelated hook entries are still present.
4. Complete the Claude-only manual checklist before deleting the old checkout.

For rollback, run this project's matching `./uninstall.sh` command, restart
Terminator, then restore only the specific configuration entries you backed up
and need. Reinstall the previous project only from its own documented release
instructions. Preserve timestamped `*.bak.*` files until you have verified the
rollback; they are snapshots, not an automatic rollback mechanism.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). The release operator checklist is
[tests/manual-checklist.md](tests/manual-checklist.md).
