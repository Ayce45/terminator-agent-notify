#!/usr/bin/env python3
"""Dismiss informational notifications when the user submits a new prompt."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import dismiss_session, read_event, text_field


def main() -> int:
    event = read_event()
    if event is None:
        return 0
    session_id = text_field(event, "session_id")
    if session_id:
        dismiss_session(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
