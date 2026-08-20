#!/usr/bin/env python3
"""Dismiss informational notifications when the user submits a new prompt."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from adapters.codex.common import AGENT, dbus_call, dismiss_session, pane_for, read_event, text_field
from terminator_agent_notify_core.runtime_state import RuntimeState


def _local_title(prompt: str, limit: int = 60) -> str:
    printable = "".join(character if character.isprintable() else " " for character in prompt)
    title = " ".join(printable.split())
    if len(title) <= limit:
        return title
    prefix = title[:limit]
    if title[limit] != " " and " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip() + "…"


def _set_claimed_title(session_id: str) -> int:
    state = None
    succeeded = False
    try:
        state = RuntimeState()
        title = state.read_title_claim(AGENT, session_id)
        if not title:
            return 1
        pane = pane_for(session_id)
        if not pane:
            return 1
        result = dbus_call("SetPaneTitle", pane, title)
        if result is not None and result.returncode == 0 and "true" in result.stdout:
            state.record_title(AGENT, session_id, title)
            succeeded = True
    except OSError:
        pass
    finally:
        if state is not None:
            state.release_title_claim(AGENT, session_id)
    return 0 if succeeded else 1


def _schedule_first_title(session_id: str, prompt: str) -> None:
    if not prompt:
        return
    try:
        state = RuntimeState()
        title = _local_title(prompt)
        if not title or not state.claim_title(AGENT, session_id, title):
            return
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--set-title",
                    session_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError):
            state.release_title_claim(AGENT, session_id)
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--set-title":
        return _set_claimed_title(sys.argv[2])
    event = read_event()
    if event is None:
        return 0
    session_id = text_field(event, "session_id")
    if session_id:
        dismiss_session(session_id)
        _schedule_first_title(
            session_id,
            text_field(event, "prompt") or text_field(event, "message"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
