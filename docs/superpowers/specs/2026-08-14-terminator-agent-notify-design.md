# Terminator Agent Notify — Design

## Objective

Create a public, agent-neutral successor to `claude-terminator-notify` that
provides the same desktop notification, pane focus, approval, cleanup, and
usage-limit resume experience for both Claude Code and Codex CLI on Linux with
Terminator.

The project will live at `Ayce45/terminator-agent-notify`. The existing Claude
repository remains unchanged during the initial release so current users keep a
stable installation and have an explicit migration path.

## Scope

Version 1 supports:

- Linux desktops exposing `org.freedesktop.Notifications`;
- Terminator panes, tabs, and windows;
- X11 and XWayland for cross-window activation;
- native Wayland with the existing compositor limitations clearly reported;
- Claude Code, Codex CLI, or both installed at the same time;
- task-complete and attention-required notifications;
- approval and denial actions from notifications;
- click-to-focus for the exact originating pane;
- dismissal after a user reply or manual pane focus;
- concurrent sessions and concurrent approval requests;
- stable Claude usage-limit auto-resume;
- experimental, fail-closed Codex usage-limit auto-resume.

macOS, Windows, terminals other than Terminator, mobile push services, and a
graphical settings application are outside version 1.

## Repository Layout

```text
terminator-agent-notify/
├── terminator_agent_notify_core/
│   ├── autoresume_guard.py
│   └── runtime_state.py
├── adapters/
│   ├── claude/
│   │   ├── hooks/
│   │   ├── autoresume.py
│   │   └── configure.sh
│   └── codex/
│       ├── hooks/
│       ├── autoresume.py
│       └── configure.sh
├── terminator-plugin/
│   └── agent_notify.py
├── scripts/
├── systemd/
├── assets/
├── tests/
├── install.sh
├── uninstall.sh
└── README.md
```

The uniquely named core package owns agent-independent runtime-state and
auto-resume generation conventions. Each adapter owns lifecycle event parsing,
configuration changes, agent-specific decisions, transcript handling, and
user-facing wording.

The Terminator plugin is a single long-lived notification owner. It owns D-Bus
notification IDs, receives action signals, focuses panes, optionally injects
keys, and resolves pending approval decisions. A single plugin avoids D-Bus name
collisions when Claude and Codex run simultaneously.

## Identifiers and Runtime State

Every event is normalized to:

- `agent`: `claude` or `codex`;
- `session_id`: the agent session identifier;
- `request_id`: the approval-specific identifier, or an internally generated
  nonce when the upstream event has none;
- `pane_uuid`: the Terminator pane UUID;
- `kind`: `waiting`, `complete`, `permission`, `limit`, or `resume`;
- title and body text.

Runtime keys always include the agent name. This prevents a Claude session and
a Codex session with coincidentally identical IDs from sharing state.

State is stored below `${XDG_RUNTIME_DIR}/terminator-agent-notify/`, falling back
to a private directory below `/tmp` only when necessary. Directories and files
are user-owned and use restrictive permissions. Pane mappings are persisted as
small files so short-lived hooks and systemd jobs can resolve their session.

Pending Codex decisions use unique files keyed by agent, session, and request.
Decisions are written atomically. They are single-use and deleted after being
consumed or expired.

## Notification and Focus Flow

On `SessionStart`, the adapter records the inherited `TERMINATOR_UUID` for the
normalized session key. If it is absent, the hook may query Terminator's focused
pane as a documented, less reliable fallback.

For waiting and completion events, the adapter calls the shared D-Bus service.
The plugin creates and tracks the notification synchronously. Clicking the body
switches to the correct tab and pane, then raises its window when the compositor
permits it.

When the matching pane receives focus, the plugin dismisses all non-approval
notifications for that agent session. A `UserPromptSubmit` hook also dismisses
the session's notifications. Closing or dismissing a notification always
removes its in-memory metadata.

If the plugin or notification daemon is unavailable, adapters use a
fire-and-forget `notify-send` fallback. The fallback has no action buttons and
must never block an agent process.

## Approval Flow

### Claude Code

Claude retains the existing interaction model. The notification plugin labels
an approval request with the normalized session and pane. `Approve` injects the
configured Enter sequence; `Deny` injects Escape. This preserves current
behavior while keeping Claude-specific prompt detection in its adapter.

### Codex CLI

Codex uses its native `PermissionRequest` hook. The command hook:

1. parses `session_id`, `turn_id`, tool name, tool input, and description;
2. generates a unique request ID and registers it with the notification plugin;
3. displays an approval notification containing a concise tool description;
4. waits for a matching decision until the configured hook timeout;
5. prints the documented Codex JSON response for `allow` or `deny`.

The plugin writes the user's action to the request's private decision file. The
hook consumes it exactly once. A deny response can include a short message.

If the notification is closed, expires, the plugin disappears, or no decision
arrives before the local wait deadline, the hook returns no decision. Codex then
uses its normal interactive approval flow. Therefore notification failures
never become implicit approvals or denials.

A response is accepted only when agent, session ID, and request ID all match.
Stale decisions cannot authorize a later request.

## Usage-Limit Auto-Resume

Claude keeps the existing transcript scanner and `systemd --user` scheduling
behavior, adapted only to shared paths and naming.

Codex auto-resume is disabled by default and explicitly marked experimental.
When enabled, its adapter scans recent Codex state using the narrowest available
documented or observed signal. It schedules a resume only when both a genuine
usage-limit event and an unambiguous reset timestamp are present. Missing,
ambiguous, stale, or changed data causes no action and produces a diagnostic
log entry.

Scheduled jobs are deduplicated per agent, session, and reset time. At the
scheduled time the shared send utility injects the configured continuation
message into the recorded pane and posts a resume notification. No parser
assumes the transcript format is stable; fixtures and version-tolerant parsing
isolate format changes to the relevant adapter.

## Installation and Migration

The installer accepts exactly one target argument:

```text
./install.sh claude
./install.sh codex
./install.sh both
```

It checks dependencies, installs the shared Terminator plugin once, installs
only the selected adapters, merges lifecycle hooks without deleting unrelated
hooks, and makes timestamped backups before modifying configuration. Re-running
the installer is idempotent.

Claude configuration is updated in `~/.claude/settings.json`. Codex lifecycle
hooks are installed through `~/.codex/hooks.json`, leaving unrelated
`config.toml` settings intact. Codex hook definitions must pass Codex's normal
review-and-trust flow after installation.

The uninstaller accepts the same targets. Removing one adapter preserves the
other adapter and the shared plugin. It removes the shared plugin only when no
installed adapter remains. It does not restore an entire old configuration
backup over newer user changes; it removes only entries owned by this project.

The README documents migration from `claude-terminator-notify`, including how
to remove the old plugin and hooks without losing unrelated Claude settings.

## Configuration

Configuration is environment-variable based in version 1, with agent-specific
prefixes where behavior differs. Supported controls include:

- notification enablement and expiry;
- Codex approval wait timeout;
- Claude and Codex auto-resume enablement;
- resume message and post-reset buffer;
- XWayland installation behavior;
- log verbosity.

Defaults preserve current Claude behavior. Codex approvals are interactive via
notifications, while Codex auto-resume remains off until explicitly enabled.

## Logging and Failure Handling

Logs live under an XDG state directory when available, with separate adapter
labels in every record. Hook failures are fail-safe and must not break normal
agent operation.

Expected failure modes and behavior:

- missing `jq` or `gdbus`: log and use the safest available fallback;
- missing notification daemon: leave the normal terminal interaction intact;
- unavailable or restarted Terminator plugin: Codex returns no hook decision;
- expired request: discard any late action;
- unknown event fields: ignore unknown fields and log at debug level;
- invalid configuration merge: abort installation before replacing the file;
- systemd unavailable: skip auto-resume and report it during installation;
- native Wayland activation refusal: focus the pane within its current window
  where possible and report the cross-window limitation.

## Testing

Automated tests use fixtures for Claude and Codex hook inputs and cover:

- normalization of sessions, requests, titles, bodies, and tool descriptions;
- Codex `allow`, `deny`, close, timeout, stale action, and malformed decision;
- concurrent sessions and multiple requests in one session;
- pane lookup and agent namespace isolation;
- notification metadata cleanup;
- configuration merge and selective uninstall behavior;
- repeated installation idempotency;
- Claude and Codex limit parsing and deduplication;
- shell syntax and Python unit tests.

D-Bus and Terminator boundaries are wrapped so most behavior can be tested
without a graphical session. Manual release checks verify notifications,
buttons, exact-pane focus, X11/XWayland behavior, simultaneous Claude/Codex
sessions, fallback behavior, and both migration directions.

## Release Criteria

Version 1 is ready when:

- Claude retains all non-experimental behavior from the original repository;
- Codex completion and approval notifications work in real Terminator sessions;
- an approval action cannot be replayed or applied to another request;
- `claude`, `codex`, and `both` installation paths pass automated tests;
- selective uninstall preserves unrelated configuration and the other adapter;
- the public README documents prerequisites, security behavior, limitations,
  migration, troubleshooting, and rollback.

The release documentation also identifies the exact installation and selective
uninstall commands, Codex's post-install hook trust review, environment
defaults, runtime/plugin log locations, and the native Wayland cross-window
focus limitation. It states that the imported Claude artwork remains Anthropic
property and is outside the MIT license, including the retained
`~/.claude/assets/` copies. A versioned manual release record captures actual
desktop/backend/version data and screenshots or log evidence; automated CI
cannot substitute for those graphical checks.
