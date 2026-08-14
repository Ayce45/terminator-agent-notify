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
> only if you have confirmed nothing else uses them.

## Requirements

- Linux with Terminator and a desktop notification daemon implementing
  `org.freedesktop.Notifications`.
- Python 3. The installed Terminator Python environment must have its normal
  GTK and D-Bus bindings (`gi` and `dbus`), as provided by a working Terminator
  installation.
- `gdbus` for the full plugin integration and `jq` for Claude hooks. The
  installer warns when either is missing. `notify-send` is optional and is only
  the non-actionable fallback when the plugin cannot be reached.
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
plugin below `${XDG_CONFIG_HOME:-~/.config}/terminator`, and records its
installation state below `${XDG_STATE_HOME:-~/.local/state}/terminator-agent-notify`.
It merges only entries marked `terminator-agent-notify:` into
`~/.claude/settings.json` or `~/.codex/hooks.json`; unrelated hooks and
settings remain in place. A changed JSON configuration is backed up beside the
original as `*.bak.<timestamp>`. Re-running an unchanged installation is
idempotent.

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

Codex handles its native `PermissionRequest` hook as follows:

- Approve emits Codex's `allow` hook response; Deny emits `deny`.
- The default wait is `CODEX_NOTIFY_APPROVAL_TIMEOUT=300` seconds. Invalid,
  negative, or non-finite values fall back to 300; `0` declines to wait.
- A notification close, expiry, service failure, timeout, interrupt, or stale
  action returns no decision, so Codex falls back to its normal interactive
  approval flow. Those cases never imply approval or denial.
- Decisions are private, atomic, single-use files keyed by agent, session, and
  request ID; a decision for another request cannot authorize the current one.

Usage-limit resume values are environment variables read by the adapters or
their installed systemd user services:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_AUTORESUME` | `1` | Set `0` to disable Claude's established auto-resume. |
| `CLAUDE_AUTORESUME_MESSAGE` | `continue` | Message sent after the Claude reset. |
| `CLAUDE_AUTORESUME_BUFFER` | `90` | Extra seconds after the parsed Claude reset time. |
| `CLAUDE_AUTORESUME_ARM_NOTIFY` | `1` | Set `0` to suppress the Claude “resume armed” notification. |
| `CODEX_AUTORESUME` | disabled | Codex resume is experimental and only runs when exactly `1`. |
| `CODEX_AUTORESUME_MESSAGE` | `continue` | Message sent after an eligible Codex reset. |
| `CODEX_NOTIFY_APPROVAL_TIMEOUT` | `300` | Seconds to wait for a Codex notification decision. |
| `FORCE_XWAYLAND` | unset | Set to `1` during Wayland installation to opt in to owned XWayland launch setup. |

The installer also follows the standard XDG location variables. If they are
unset, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_STATE_HOME` default to
`~/.config`, `~/.local/share`, and `~/.local/state` respectively.
`XDG_SESSION_TYPE=wayland` is what enables the installer’s XWayland prompt.
`XDG_RUNTIME_DIR` selects the private runtime state and plugin-log directory;
without it, runtime state falls back to `/tmp/terminator-agent-notify-<uid>`
and the plugin log falls back to `/tmp/terminator-agent-notify.log`.
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

## Logs and troubleshooting

The Terminator plugin appends to
`${XDG_RUNTIME_DIR:-/tmp}/terminator-agent-notify.log`; adapter auto-resume
diagnostics go to the invoked service or command's stderr (for example,
`journalctl --user -u codex-limit-poller.service`). Runtime pane mappings,
pending decisions, closure state, and resume de-duplication markers are private
(`0700` directories and `0600` files) below
`${XDG_RUNTIME_DIR}/terminator-agent-notify`, or
`/tmp/terminator-agent-notify-<uid>` when `XDG_RUNTIME_DIR` is absent.

Useful checks:

```bash
systemctl --user status claude-limit-poller.timer codex-limit-poller.timer
journalctl --user -u claude-limit-poller.service -u codex-limit-poller.service
tail -f "${XDG_RUNTIME_DIR:-/tmp}/terminator-agent-notify.log"
```

If focus works in the current window but not across windows on Wayland, use the
XWayland option above or accept the compositor limitation. If an approval
notification disappears, expires, or has no buttons, approve or deny in Codex's
normal terminal UI instead; do not assume any action was applied. If hooks do
not fire after installation, restart the agent, inspect its merged configuration
for `terminator-agent-notify:` entries, and complete Codex's hook trust review.

## Selective uninstall, migration, and rollback

Remove only the adapter you mean to remove:

```bash
./uninstall.sh claude
./uninstall.sh codex
./uninstall.sh both
```

Removing one adapter preserves the other and the shared plugin. The shared
plugin, shared core, and project-owned XWayland changes are removed only after
no managed adapter remains. Uninstall removes only project-marked hook entries;
it never restores an entire backup over newer user changes. It also deliberately
leaves `~/.claude/assets/claude.png` and `claude.svg` in place (see the artwork
notice above).

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
