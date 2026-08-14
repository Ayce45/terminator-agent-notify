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
    AGENT,
    RuntimeState,
    dismiss_request,
    notify,
    pane_for,
    read_event,
    text_field,
)


DEFAULT_TIMEOUT = 300.0
MAX_POLL_INTERVAL = 0.1
COMMAND_PREVIEW_LIMIT = 240
INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class HookInterrupted(Exception):
    """Raised to leave the polling loop through its normal cleanup path."""


def _interrupt(_signum, _frame):
    raise HookInterrupted


def _defer_interrupts():
    if hasattr(signal, "pthread_sigmask"):
        return signal.pthread_sigmask(signal.SIG_BLOCK, INTERRUPT_SIGNALS)
    return None


def _restore_interrupts(previous_mask):
    if previous_mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def _approval_timeout() -> float:
    try:
        timeout = float(os.environ.get("CODEX_NOTIFY_APPROVAL_TIMEOUT", DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    if not math.isfinite(timeout):
        return DEFAULT_TIMEOUT
    return max(0.0, timeout)


def _notification_text(event):
    tool_name = text_field(event, "tool_name") or "Tool"
    description = text_field(event, "description")
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else ""
    command = command.strip() if isinstance(command, str) else ""
    if len(command) > COMMAND_PREVIEW_LIMIT:
        command = f"{command[: COMMAND_PREVIEW_LIMIT - 1]}…"
    lines = [description or "Approval requested."]
    if command:
        lines.append(f"Command: {command}")
    return f"Codex permission — {tool_name}", "\n".join(lines)


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

    request_id = f"{turn_id}:{secrets.token_hex(16)}"
    title, body = _notification_text(event)
    registered = False
    try:
        previous_mask = _defer_interrupts()
        try:
            registered = notify(
                session_id,
                request_id,
                pane_for(session_id),
                "permission",
                title,
                body,
            )
        finally:
            _restore_interrupts(previous_mask)
        if not registered:
            return None

        state = RuntimeState()
        deadline = time.monotonic() + _approval_timeout()
        while True:
            decision = state.consume_decision(AGENT, session_id, request_id)
            if decision is not None:
                return _decision_output(decision)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(MAX_POLL_INTERVAL, remaining))
    except (HookInterrupted, KeyboardInterrupt, OSError):
        return None
    finally:
        if registered:
            for signal_number in INTERRUPT_SIGNALS:
                signal.signal(signal_number, signal.SIG_IGN)
            dismiss_request(session_id, request_id)


def main() -> int:
    for signal_number in INTERRUPT_SIGNALS:
        signal.signal(signal_number, _interrupt)
    result = run()
    if result is not None:
        sys.stdout.write(json.dumps(result, separators=(",", ":")))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
