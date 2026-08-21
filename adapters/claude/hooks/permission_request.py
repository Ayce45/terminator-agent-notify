#!/usr/bin/env python3
"""Publish actionable Claude tool permission notifications."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from terminator_agent_notify_core.runtime_state import RuntimeState
from adapters.claude.common import notifications_enabled


BUS = "io.github.TerminatorAgentNotify"
OBJECT_PATH = "/io/github/TerminatorAgentNotify"
POSITIVE_ID = re.compile(r"\buint32\s+[1-9][0-9]*\b")
COMMAND_PREVIEW_LIMIT = 240


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def main() -> int:
    if not notifications_enabled():
        return 0
    try:
        event = json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if not isinstance(event, dict):
        return 0
    tool_name = _text(event.get("tool_name"))
    session_id = _text(event.get("session_id"))
    if not session_id or not tool_name or tool_name == "AskUserQuestion":
        return 0

    tool_input = event.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    description = _text(event.get("description")) or _text(tool_input.get("description"))
    command = _text(tool_input.get("command"))
    if len(command) > COMMAND_PREVIEW_LIMIT:
        command = f"{command[: COMMAND_PREVIEW_LIMIT - 1]}…"
    body = [f"Permission requested — {tool_name}"]
    if description:
        body.append(description)
    if command:
        body.append(f"Command: {command}")

    try:
        state = RuntimeState()
        pane = state.read_pane("claude", session_id) or ""
    except (OSError, ValueError):
        return 0
    cwd = _text(event.get("cwd"))
    title = Path(cwd).name if cwd else "Attention required"
    payload = json.dumps(
        {"title": title, "body": "\n".join(body)}, ensure_ascii=False
    ).replace("\\", "\\\\")
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session", "--dest", BUS,
                "--object-path", OBJECT_PATH, "--method", f"{BUS}.Notify",
                "claude", session_id, "", pane, "permission", payload,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=float(os.environ.get("TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS", "5")),
            check=False,
        )
        if result.returncode == 0 and POSITIVE_ID.search(result.stdout):
            state.record_attention("claude", session_id, "permission")
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
