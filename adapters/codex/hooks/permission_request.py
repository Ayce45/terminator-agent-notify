#!/usr/bin/env python3
"""Resolve a Codex PermissionRequest through an actionable notification."""

from __future__ import annotations

import json
import math
import os
import secrets
import signal
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import (
    fallback_notification,
    AGENT,
    RuntimeState,
    dismiss_request,
    notify,
    notifications_enabled,
    pane_for,
    read_event,
    session_title,
    text_field,
)


# The hooks.json host timeout reserves two five-second D-Bus call budgets and a
# cleanup margin beyond this ceiling.  Never let configuration consume that
# reserve, even when a larger finite environment value is supplied.
DEFAULT_TIMEOUT = 300.0
MAX_APPROVAL_WAIT_SECONDS = 300.0
MAX_POLL_INTERVAL = 0.1
COMMAND_PREVIEW_LIMIT = 240
INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_interrupted = False


def _interrupt(_signum, _frame):
    global _interrupted
    _interrupted = True


def _approval_timeout() -> float:
    try:
        timeout = float(os.environ.get("CODEX_NOTIFY_APPROVAL_TIMEOUT", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if not math.isfinite(timeout) or timeout < 0:
        return DEFAULT_TIMEOUT
    return min(timeout, MAX_APPROVAL_WAIT_SECONDS)


def _notification_text(event):
    tool_name = text_field(event, "tool_name") or "Tool"
    description = text_field(event, "description")
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    command = command.strip() if isinstance(command, str) else ""
    if len(command) > COMMAND_PREVIEW_LIMIT:
        command = f"{command[: COMMAND_PREVIEW_LIMIT - 1]}…"
    lines = [f"Permission requested — {tool_name}"]
    if description:
        lines.append(description)
    if command:
        lines.append(f"Command: {command}")
    return session_title(event), "\n".join(lines)


def _decision_output(decision: str):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": decision},
        }
    }


def run() -> dict | None:
    event = read_event()
    if event is None:
        return None
    session_id = text_field(event, "session_id")
    turn_id = text_field(event, "turn_id")
    if not session_id or not turn_id:
        return None
    if not notifications_enabled():
        return None

    request_id = f"{turn_id}:{secrets.token_hex(16)}"
    title, body = _notification_text(event)
    state = None
    try:
        try:
            state = RuntimeState()
        except OSError:
            pass
        registered = notify(
            session_id,
            request_id,
            pane_for(session_id),
            "permission",
            title,
            body,
        )
        if not registered:
            # Without the plugin there is nothing actionable, but the user
            # still deserves to know Codex is waiting in the terminal.
            fallback_notification(title, f"{body}\nAnswer in the terminal.")
            return None

        if state is None:
            return None
        deadline = time.monotonic() + _approval_timeout()
        while True:
            if _interrupted or time.monotonic() >= deadline:
                return None
            if state.consume_request_closed(AGENT, session_id, request_id):
                return None
            decision = state.consume_decision(AGENT, session_id, request_id)
            if decision is not None:
                if not _interrupted and time.monotonic() <= deadline:
                    return _decision_output(decision)
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(MAX_POLL_INTERVAL, remaining))
    except (KeyboardInterrupt, OSError):
        return None
    finally:
        dismiss_request(session_id, request_id)
        if state is not None:
            try:
                state.consume_decision(AGENT, session_id, request_id)
                state.consume_request_closed(AGENT, session_id, request_id)
            except OSError:
                pass


def main() -> int:
    global _interrupted
    _interrupted = False
    for signal_number in INTERRUPT_SIGNALS:
        signal.signal(signal_number, _interrupt)
    result = run()
    if result is not None:
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
