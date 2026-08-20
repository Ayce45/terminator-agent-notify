#!/usr/bin/env python3
"""Notify when Codex asks the user an interactive question.

Codex surfaces its question UI through the ``request_user_input`` tool and
fires no dedicated hook for it, so PreToolUse is the only signal that the
session is waiting on an answer. The hook never blocks the tool: it emits no
JSON output and always exits 0.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import notify, pane_for, read_event, session_title, text_field


QUESTION_TOOLS = frozenset(("request_user_input",))


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    if text_field(event, "tool_name") not in QUESTION_TOOLS:
        return 0
    session_id = text_field(event, "session_id")
    if not session_id:
        return 0
    notify(
        session_id,
        "",
        pane_for(session_id),
        "waiting",
        session_title(event),
        "Codex asks a question — answer in the terminal.",
        fallback=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
