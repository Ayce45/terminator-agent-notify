#!/usr/bin/env python3
"""Resolve a Codex PermissionRequest through an actionable notification."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import (
    fallback_notification,
    notify,
    notifications_enabled,
    pane_for,
    read_event,
    session_title,
    text_field,
)


COMMAND_PREVIEW_LIMIT = 240


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
    try:
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
    except (KeyboardInterrupt, OSError):
        return None
    return None


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
