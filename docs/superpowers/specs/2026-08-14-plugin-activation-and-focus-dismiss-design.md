# Plugin Activation and Focus Dismissal — Design

## Objective

Restore two Claude-baseline behaviors without weakening Codex request safety:
activate the Terminator plugin automatically when the existing configuration
can be updated safely, and dismiss only the notifications belonging to the
pane that the user focuses.

## Automatic activation

Installation updates `${XDG_CONFIG_HOME:-$HOME/.config}/terminator/config` only
when the file exists and contains an `enabled_plugins` assignment. It appends
`AgentNotify` while preserving every existing plugin and creates a timestamped
backup before changing bytes. Reinstallation is idempotent.

The installer records whether it added the entry. Final uninstall removes only
that owned `AgentNotify` entry and preserves all other plugins and configuration
bytes. If the file is absent, malformed for this narrow edit, or later changed
so ownership cannot be established safely, installation or removal leaves it
alone and prints the existing manual-enable guidance.

## Focus dismissal

The plugin already tracks each notification with its agent, session, request,
pane, and kind. A pane focus event selects notification IDs whose normalized
pane UUID equals the focused pane UUID and closes all of them. Notifications
for every other pane remain untouched.

Closing a Claude permission notification takes no additional action; the user
answers in the focused terminal. Closing a Codex permission notification first
writes that exact request's closed state, causing its waiting hook to return no
decision and fall back to Codex's terminal prompt. Focus never means allow or
deny, and a request in another pane cannot be affected.

## Failure handling

Notification close failures are best effort after metadata is retired, matching
the existing lifecycle. Configuration writes use the project's atomic,
permission-preserving file-write conventions. Failure to prove configuration
ownership is fail-safe and requires manual plugin activation or removal.

## Verification

Automated regressions cover Claude and Codex permission dismissal, preservation
of notifications in another pane, exact Codex closed-request state, automatic
activation with other plugins present, idempotent reinstall, selective removal,
and refusal to rewrite unsupported configurations. The full Python and shell
integration suites remain required. A real Terminator session remains a manual
check for plugin loading and focus-event delivery.
