# Terminator Agent Notify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a public Linux/Terminator notification system with exact-pane focus, actionable approvals, cleanup, and optional usage-limit auto-resume for Claude Code and Codex CLI.

**Architecture:** Import the proven `claude-terminator-notify` implementation, then extract agent-neutral runtime and D-Bus behavior behind a single Terminator plugin. Keep event parsing, configuration merging, approval semantics, and transcript parsing in Claude and Codex adapters.

**Tech Stack:** Bash, Python 3 standard library, pytest, jq, D-Bus/GLib, Terminator plugin API, systemd user units, GitHub Actions.

## Global Constraints

- Target Linux with Terminator and an `org.freedesktop.Notifications` daemon.
- Support X11/XWayland fully and document native Wayland cross-window limitations.
- Claude and Codex must work simultaneously through one D-Bus service.
- Never turn notification failure, closure, or timeout into an implicit approval.
- Preserve unrelated user hooks and configuration during install and uninstall.
- Codex usage-limit auto-resume is experimental and disabled by default.
- Runtime files must be user-private and namespaced by agent and session.
- The existing `claude-terminator-notify` repository remains unchanged.

---

### Task 1: Import the Proven Claude Baseline

**Files:**
- Create: `LICENSE`
- Create: `assets/claude.png`
- Create: `assets/claude.svg`
- Create: `scripts/force-xwayland.sh`
- Create: `scripts/relaunch-terminator.sh`
- Create: `tests/test_baseline_layout.py`

**Interfaces:**
- Consumes: source files from `Ayce45/claude-terminator-notify` at commit `d3dffde4630e6813d031ce08be253bfedb142b84`.
- Produces: preserved MIT license, Claude assets, and XWayland utilities used by later installer tasks.

- [ ] **Step 1: Write the baseline layout test**

```python
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
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/test_baseline_layout.py -v`

Expected: FAIL because the baseline files have not been imported.

- [ ] **Step 3: Copy the exact baseline files and record provenance**

Copy the five tested paths from the pinned checkout without modifying their behavior. Add this comment after the shebang in each imported script:

```bash
# Origin: Ayce45/claude-terminator-notify@d3dffde4630e6813d031ce08be253bfedb142b84
```

- [ ] **Step 4: Run the baseline test and shell syntax checks**

Run: `pytest tests/test_baseline_layout.py -v && bash -n scripts/force-xwayland.sh scripts/relaunch-terminator.sh`

Expected: all checks PASS.

- [ ] **Step 5: Commit**

```bash
git add LICENSE assets scripts tests/test_baseline_layout.py
git commit -m "chore: import proven Claude notification baseline"
```

### Task 2: Add Agent-Neutral Runtime State

**Files:**
- Create: `terminator_agent_notify_core/runtime_state.py`
- Create: `tests/test_runtime_state.py`

**Interfaces:**
- Produces: `RuntimeState(root: Path | None = None)`, `pane_path(agent, session_id)`, `decision_path(agent, session_id, request_id)`, `record_pane(...)`, `read_pane(...)`, `write_decision(...)`, and `consume_decision(...)`.
- Decision values are exactly `"allow"` and `"deny"`; all path components are SHA-256-derived rather than interpolated raw identifiers.

- [ ] **Step 1: Write failing isolation and single-use tests**

```python
from terminator_agent_notify_core.runtime_state import RuntimeState


def test_agent_sessions_are_isolated(tmp_path):
    state = RuntimeState(tmp_path)
    state.record_pane("claude", "same", "claude-pane")
    state.record_pane("codex", "same", "codex-pane")
    assert state.read_pane("claude", "same") == "claude-pane"
    assert state.read_pane("codex", "same") == "codex-pane"


def test_decision_is_consumed_once(tmp_path):
    state = RuntimeState(tmp_path)
    state.write_decision("codex", "s1", "r1", "allow")
    assert state.consume_decision("codex", "s1", "r1") == "allow"
    assert state.consume_decision("codex", "s1", "r1") is None
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `pytest tests/test_runtime_state.py -v`

Expected: FAIL with `ModuleNotFoundError: terminator_agent_notify_core.runtime_state`.

- [ ] **Step 3: Implement private, atomic state operations**

Use `${XDG_RUNTIME_DIR}/terminator-agent-notify`, falling back to `/tmp/terminator-agent-notify-<uid>`. Create directories with mode `0700`, files with mode `0600`, write through a sibling temporary file, and finalize with `os.replace`. Reject agents outside `{claude, codex}` and decisions outside `{allow, deny}`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_runtime_state.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add terminator_agent_notify_core/runtime_state.py tests/test_runtime_state.py
git commit -m "feat: add private agent-neutral runtime state"
```

### Task 3: Generalize the Terminator D-Bus Plugin

**Files:**
- Create: `terminator-plugin/agent_notify.py`
- Create: `tests/test_plugin_metadata.py`
- Create: `tests/fakes/terminator_runtime.py`

**Interfaces:**
- Consumes: `RuntimeState.write_decision(agent, session_id, request_id, decision)`.
- Produces D-Bus methods `FocusTerminal(s) -> b`, `SendKeys(ss) -> b`, `Notify(ssssss) -> u`, and `DismissSession(ss) -> b` on `io.github.TerminatorAgentNotify`.
- `Notify` arguments are `(agent, session_id, request_id, pane_uuid, kind, payload_json)`.

- [ ] **Step 1: Write failing metadata tests**

```python
from tests.fakes.terminator_runtime import make_service


def test_approval_actions_are_bound_to_request(tmp_path):
    service, notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "r1", "pane", "permission", '{"title":"Codex","body":"Run command?"}')
    service.on_action(nid, "approve")
    assert state.consume_decision("codex", "s1", "r1") == "allow"


def test_plain_notification_click_focuses_without_decision(tmp_path):
    service, notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "", "pane", "complete", '{"title":"Done","body":"Ready"}')
    service.on_action(nid, "default")
    assert service.focused == ["pane"]
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/test_plugin_metadata.py -v`

Expected: FAIL because the generalized service does not exist.

- [ ] **Step 3: Port and generalize the Claude plugin**

Start from `terminator-plugin/dbus_focus.py` in the pinned Claude source. Preserve tab lookup, window raising, delayed VTE focus, key injection compatibility, notification ownership, action de-duplication, and focus-triggered cleanup. Replace Claude-specific constants and methods with the interfaces above. Route Claude approval actions to key injection and Codex actions to `RuntimeState.write_decision`.

- [ ] **Step 4: Run plugin tests and compile check**

Run: `pytest tests/test_plugin_metadata.py -v && python -m py_compile terminator-plugin/agent_notify.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add terminator-plugin tests/fakes tests/test_plugin_metadata.py
git commit -m "feat: generalize Terminator notification service"
```

### Task 4: Preserve Claude Code Behavior Through an Adapter

**Files:**
- Create: `adapters/claude/hooks/session-start.sh`
- Create: `adapters/claude/hooks/notify-waiting.sh`
- Create: `adapters/claude/hooks/notify-cleanup.sh`
- Create: `adapters/claude/hooks/auto-resume-on-limit.sh`
- Create: `adapters/claude/autoresume.py`
- Create: `adapters/claude/send.sh`
- Create: `tests/test_claude_adapter.py`
- Create: `tests/fixtures/claude/permission.json`
- Create: `tests/fixtures/claude/limit.jsonl`

**Interfaces:**
- Consumes: shared D-Bus `Notify`, `DismissSession`, and `SendKeys`; runtime pane mapping.
- Produces Claude lifecycle hooks compatible with `SessionStart`, `Notification`, `Stop`, and `UserPromptSubmit`.

- [ ] **Step 1: Add fixtures and failing adapter tests**

Test that a permission message normalizes to `kind=permission`, `Stop` normalizes to `kind=complete`, pane mappings use agent `claude`, and a fresh 429 fixture produces one scheduled resume while a recovered transcript produces none.

- [ ] **Step 2: Run tests and verify missing adapter failures**

Run: `pytest tests/test_claude_adapter.py -v`

Expected: FAIL because adapter scripts and parser are absent.

- [ ] **Step 3: Port Claude hooks and auto-resume**

Port from the pinned source while changing only paths, D-Bus method names, normalized kinds, and runtime namespacing. Preserve the dialog-safe resume sequence: Enter, two-second wait, message, one-second wait, Enter, one-second wait, safety Enter.

- [ ] **Step 4: Run tests and syntax checks**

Run: `pytest tests/test_claude_adapter.py -v && bash -n adapters/claude/hooks/*.sh adapters/claude/send.sh && python -m py_compile adapters/claude/autoresume.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adapters/claude tests/test_claude_adapter.py tests/fixtures/claude
git commit -m "feat: preserve Claude notification and resume behavior"
```

### Task 5: Implement Native Codex Approval Decisions

**Files:**
- Create: `adapters/codex/hooks/session_start.py`
- Create: `adapters/codex/hooks/permission_request.py`
- Create: `adapters/codex/hooks/stop.py`
- Create: `adapters/codex/hooks/user_prompt_submit.py`
- Create: `adapters/codex/common.py`
- Create: `tests/test_codex_hooks.py`
- Create: `tests/fixtures/codex/permission_bash.json`

**Interfaces:**
- Consumes: `RuntimeState`, shared D-Bus methods, Codex hook JSON on stdin.
- Produces exact Codex output `{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow|deny","message"?:str}}}` or no stdout when undecided.
- Uses `CODEX_NOTIFY_APPROVAL_TIMEOUT`, default `300` seconds, with polling capped at `0.1` second.

- [ ] **Step 1: Write failing decision tests**

```python
def test_allow_returns_native_codex_shape(run_permission_hook, decision_writer):
    decision_writer("allow")
    result = run_permission_hook("permission_bash.json")
    assert result == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def test_timeout_returns_no_decision(run_permission_hook):
    assert run_permission_hook("permission_bash.json", timeout=0) is None
```

Also test deny, malformed input, notification failure, stale request isolation, session start pane recording, completion notification, and cleanup.

- [ ] **Step 2: Run tests and verify they fail**

Run: `pytest tests/test_codex_hooks.py -v`

Expected: FAIL because Codex hooks are absent.

- [ ] **Step 3: Implement minimal Codex hooks**

Parse documented fields defensively. Derive request ID from `turn_id` plus a random nonce. Display tool name, human description when present, and a length-limited command preview. On notification closure or timeout, emit no JSON so Codex continues its normal approval flow. Never write an allow decision inside the hook itself.

- [ ] **Step 4: Run Codex tests and compile checks**

Run: `pytest tests/test_codex_hooks.py -v && python -m py_compile adapters/codex/*.py adapters/codex/hooks/*.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add adapters/codex tests/test_codex_hooks.py tests/fixtures/codex
git commit -m "feat: add native Codex approval notifications"
```

### Task 6: Build Idempotent Multi-Agent Installation

**Files:**
- Create: `install.sh`
- Create: `uninstall.sh`
- Create: `adapters/claude/configure.py`
- Create: `adapters/codex/configure.py`
- Create: `systemd/claude-limit-poller.service.in`
- Create: `systemd/claude-limit-poller.timer`
- Create: `systemd/codex-limit-poller.service.in`
- Create: `systemd/codex-limit-poller.timer`
- Create: `tests/test_configuration.py`
- Create: `tests/test_installation.sh`

**Interfaces:**
- Consumes: target argument exactly `claude`, `codex`, or `both`.
- Produces merged Claude `~/.claude/settings.json`, merged Codex `~/.codex/hooks.json`, installed adapter files, shared plugin, and selective uninstall.

- [ ] **Step 1: Write failing merge tests**

Create temporary homes containing unrelated hooks. Assert two installs are byte-for-byte idempotent, unrelated hooks survive, uninstalling Codex preserves Claude, uninstalling Claude preserves Codex, and the plugin is removed only after both adapters are absent.

- [ ] **Step 2: Run tests and verify failures**

Run: `pytest tests/test_configuration.py -v && bash tests/test_installation.sh`

Expected: FAIL because installers and configurators are absent.

- [ ] **Step 3: Implement ownership-aware configuration merging**

Mark every installed hook with description prefix `terminator-agent-notify:`. Merge by that marker instead of replacing entire event arrays. Write configuration to a temporary sibling, validate JSON, create a timestamped backup, then atomically replace. Install Codex hooks in `~/.codex/hooks.json` and print the required Codex trust-review reminder.

- [ ] **Step 4: Implement selective install and uninstall**

Validate the target before filesystem changes. Track installed adapters in a private state file. Enable Claude's timer by default; install but do not enable Codex's experimental timer unless `CODEX_AUTORESUME=1`. Preserve the original XWayland helper behavior.

- [ ] **Step 5: Run installer tests and syntax checks**

Run: `pytest tests/test_configuration.py -v && bash tests/test_installation.sh && bash -n install.sh uninstall.sh`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add install.sh uninstall.sh adapters/*/configure.py systemd tests/test_configuration.py tests/test_installation.sh
git commit -m "feat: add idempotent multi-agent installation"
```

### Task 7: Add Fail-Closed Experimental Codex Auto-Resume

**Files:**
- Create: `adapters/codex/autoresume.py`
- Create: `adapters/codex/send.sh`
- Create: `tests/test_codex_autoresume.py`
- Create: `tests/fixtures/codex/limit_with_reset.jsonl`
- Create: `tests/fixtures/codex/limit_ambiguous.jsonl`

**Interfaces:**
- Consumes: recent Codex transcript paths and runtime pane mappings.
- Produces no schedule unless `CODEX_AUTORESUME=1`, a limit signal is recognized, and one future reset timestamp is parsed unambiguously.

- [ ] **Step 1: Write fail-closed tests**

Assert disabled mode never schedules, the unambiguous fixture schedules once, a repeated scan does not duplicate, ambiguous or stale fixtures do not schedule, and any parse exception returns cleanly with a log record.

- [ ] **Step 2: Run tests and verify failures**

Run: `pytest tests/test_codex_autoresume.py -v`

Expected: FAIL because the Codex resume adapter is absent.

- [ ] **Step 3: Implement isolated version-tolerant parsing**

Inspect only recent records, recursively extract text without depending on one nesting shape, require both rate-limit language/status and an explicit reset timestamp, and use injectable clock/scheduler functions in tests. Deduplicate with the shared private runtime directory.

- [ ] **Step 4: Implement scheduled pane resume**

Use `systemd-run --user --collect` with an explicit unit name containing a hashed session prefix. At execution, resolve the pane again and inject the configured message followed by Enter. Post a notification after successful injection.

- [ ] **Step 5: Run focused and full automated tests**

Run: `pytest tests/test_codex_autoresume.py -v && pytest -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add adapters/codex/autoresume.py adapters/codex/send.sh tests/test_codex_autoresume.py tests/fixtures/codex
git commit -m "feat: add experimental Codex limit auto-resume"
```

### Task 8: Documentation, CI, and Release Verification

**Files:**
- Create: `README.md`
- Create: `CONTRIBUTING.md`
- Create: `.github/workflows/test.yml`
- Create: `tests/manual-checklist.md`
- Modify: `docs/superpowers/specs/2026-08-14-terminator-agent-notify-design.md`

**Interfaces:**
- Consumes: all user-facing commands and environment variables implemented by Tasks 1–7.
- Produces: installation, migration, security, troubleshooting, rollback, development, and release documentation plus continuous tests.

- [ ] **Step 1: Write a documentation contract test**

Add assertions to `tests/test_baseline_layout.py` that README contains `./install.sh claude`, `./install.sh codex`, `./install.sh both`, `PermissionRequest`, `XWayland`, `CODEX_AUTORESUME=1`, migration from `claude-terminator-notify`, and uninstall examples.

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `pytest tests/test_baseline_layout.py -v`

Expected: FAIL because README is absent.

- [ ] **Step 3: Write README, contributing guide, and manual checklist**

Document exact dependencies, installation targets, Codex hook trust review, permission security semantics, auto-resume defaults, native Wayland limitation, log locations, selective uninstall, old-repository migration, rollback, and expected screenshots/log evidence for manual checks.

- [ ] **Step 4: Add CI**

Configure GitHub Actions on Ubuntu to install pytest, run `pytest -v`, execute `bash tests/test_installation.sh`, run `bash -n` over tracked shell scripts, and run `python -m compileall terminator_agent_notify_core adapters terminator-plugin` without requiring a graphical session.

- [ ] **Step 5: Run all non-graphical verification**

Run: `pytest -v && bash tests/test_installation.sh && find . -type f -name '*.sh' -print0 | xargs -0 bash -n && python -m compileall -q terminator_agent_notify_core adapters terminator-plugin && git diff --check`

Expected: all commands exit `0`.

- [ ] **Step 6: Perform manual Terminator verification**

Follow `tests/manual-checklist.md` for Claude-only, Codex-only, and simultaneous sessions. Record tested desktop, display backend, Terminator version, Codex version, Claude version, notification action results, exact-pane focus results, timeout fallback, and selective uninstall results in the checklist's release record.

- [ ] **Step 7: Commit and push**

```bash
git add README.md CONTRIBUTING.md .github tests docs/superpowers/specs
git commit -m "docs: add setup, migration, CI, and release checks"
git push origin main
```
