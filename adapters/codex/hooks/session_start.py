#!/usr/bin/env python3
"""Record the Terminator pane associated with a Codex session."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import AGENT, RuntimeState, focused_pane, read_event, text_field


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    session_id = text_field(event, "session_id")
    if not session_id:
        return 0
    pane = os.environ.get("TERMINATOR_UUID", "").strip() or focused_pane()
    if not pane:
        return 0
    try:
        RuntimeState().record_pane(AGENT, session_id, pane)
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
