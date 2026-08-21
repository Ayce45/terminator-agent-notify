#!/usr/bin/env python3
"""Notify when Claude asks an interactive question."""

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


def main() -> int:
    if not notifications_enabled():
        return 0
    try:
        event = json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if not isinstance(event, dict) or event.get("tool_name") != "AskUserQuestion":
        return 0
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0
    try:
        state = RuntimeState()
        pane = state.read_pane("claude", session_id) or ""
    except (OSError, ValueError):
        return 0
    cwd = event.get("cwd")
    title = Path(cwd).name if isinstance(cwd, str) and cwd else "Attention required"
    payload = json.dumps(
        {"title": title, "body": "Claude asks a question — answer in the terminal."},
        ensure_ascii=False,
    ).replace("\\", "\\\\")
    try:
        result = subprocess.run(
            [
                "gdbus", "call", "--session", "--dest", BUS,
                "--object-path", OBJECT_PATH, "--method", f"{BUS}.Notify",
                "claude", session_id, "", pane, "waiting", payload,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=float(os.environ.get("TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS", "5")),
            check=False,
        )
        if result.returncode == 0 and POSITIVE_ID.search(result.stdout):
            state.record_attention("claude", session_id, "question")
            return 0
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
