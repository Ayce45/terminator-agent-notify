#!/usr/bin/env python3
"""Record the Terminator pane associated with a Codex session."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import (
    AGENT,
    RuntimeState,
    dbus_call,
    focused_pane,
    read_event,
    text_field,
)


def _restore_title(session_id: str, pane: str) -> int:
    try:
        state = RuntimeState()
        title = state.read_title(AGENT, session_id)
        if not title or not pane:
            return 0
        result = dbus_call("SetPaneTitle", pane, title)
        return 0 if result is not None and result.returncode == 0 else 1
    except OSError:
        return 1


def _schedule_title_restore(session_id: str, pane: str) -> None:
    try:
        pane = pane.strip()
        if (
            not pane
            or len(pane) > 256
            or not pane.isprintable()
            or RuntimeState().read_title(AGENT, session_id) is None
        ):
            return
        subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--restore-title",
                session_id,
                pane,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except (OSError, ValueError):
        pass


def main() -> int:
    if len(sys.argv) == 4 and sys.argv[1] == "--restore-title":
        return _restore_title(sys.argv[2], sys.argv[3])
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
        _schedule_title_restore(session_id, pane)
    except (OSError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
