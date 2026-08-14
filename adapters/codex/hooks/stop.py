#!/usr/bin/env python3
"""Post a completion notification for a Codex session."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import notify, pane_for, read_event, session_title, text_field


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    session_id = text_field(event, "session_id")
    if not session_id:
        return 0
    notify(
        session_id,
        "",
        pane_for(session_id),
        "complete",
        session_title(event),
        "Task complete — waiting for your next instruction.",
        fallback=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
