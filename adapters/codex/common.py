"""Shared helpers for short-lived Codex lifecycle hooks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.runtime_state import RuntimeState


AGENT = "codex"
BUS = "io.github.TerminatorAgentNotify"
OBJECT_PATH = "/io/github/TerminatorAgentNotify"
_POSITIVE_NOTIFICATION_ID = re.compile(r"\buint32\s+([1-9][0-9]*)\b")
_QUOTED_VALUE = re.compile(r"['\"]([^'\"]+)['\"]")


def read_event() -> dict[str, Any] | None:
    """Return a JSON object from stdin, or ``None`` for malformed input."""
    try:
        value = json.load(sys.stdin)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def text_field(event: dict[str, Any], name: str) -> str:
    value = event.get(name)
    return value.strip() if isinstance(value, str) else ""


def dbus_call(method: str, *arguments: str) -> subprocess.CompletedProcess[str] | None:
    """Make a bounded, quiet call to the shared Terminator service."""
    try:
        return subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                BUS,
                "--object-path",
                OBJECT_PATH,
                "--method",
                f"{BUS}.{method}",
                *arguments,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def notify(
    session_id: str,
    request_id: str,
    pane: str,
    kind: str,
    title: str,
    body: str,
    *,
    fallback: bool = False,
) -> bool:
    payload = json.dumps({"title": title, "body": body})
    result = dbus_call(
        "Notify", AGENT, session_id, request_id, pane, kind, payload
    )
    if (
        result is not None
        and result.returncode == 0
        and _POSITIVE_NOTIFICATION_ID.search(result.stdout)
    ):
        return True
    if fallback:
        _fallback_notification(title, body)
    return False


def _fallback_notification(title: str, body: str) -> None:
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(
            ["notify-send", "--app-name=Codex", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def dismiss_request(session_id: str, request_id: str) -> None:
    dbus_call("DismissRequest", AGENT, session_id, request_id)


def dismiss_session(session_id: str) -> None:
    dbus_call("DismissSession", AGENT, session_id)


def focused_pane() -> str:
    result = dbus_call("GetFocusedUUID")
    if result is None or result.returncode != 0:
        return ""
    match = _QUOTED_VALUE.search(result.stdout)
    return match.group(1).strip() if match else ""


def pane_for(session_id: str) -> str:
    try:
        return RuntimeState().read_pane(AGENT, session_id) or ""
    except OSError:
        return ""


def session_title(event: dict[str, Any]) -> str:
    cwd = text_field(event, "cwd")
    return f"Codex — {Path(cwd).name}" if cwd and Path(cwd).name else "Codex"
