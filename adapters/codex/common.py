"""Shared helpers for short-lived Codex lifecycle hooks."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from terminator_agent_notify_core.autoresume_guard import read_persisted_environment
from terminator_agent_notify_core.runtime_state import RuntimeState


_PERSISTED_ENVIRONMENT = read_persisted_environment()


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


def gvariant_text(value: str) -> str:
    """Keep backslash escapes intact through gdbus' GVariant parser."""
    return value.replace("\\", "\\\\")


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
    if not notifications_enabled():
        return False
    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    result = dbus_call(
        "Notify", AGENT, session_id, request_id, pane, kind, gvariant_text(payload)
    )
    if (
        result is not None
        and result.returncode == 0
        and _POSITIVE_NOTIFICATION_ID.search(result.stdout)
    ):
        return True
    if fallback:
        fallback_notification(title, body)
    return False


def fallback_notification(title: str, body: str) -> None:
    if shutil.which("notify-send") is None:
        return
    command = ["notify-send", "--app-name=Codex"]
    icon = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "terminator"
        / "assets"
        / "codex-mark.png"
    )
    if icon.is_file():
        command.append(f"--icon={icon}")
    expiry = _environment_value("TERMINATOR_AGENT_NOTIFY_EXPIRY_MS")
    if expiry:
        command.append(f"--expire-time={expiry}")
    command.extend((title, body))
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        pass


def notifications_enabled() -> bool:
    return _environment_value("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "1") != "0"


def _environment_value(name: str, default: str | None = None) -> str | None:
    """Resolve an explicit process override before installer-persisted controls."""
    return os.environ.get(name, _PERSISTED_ENVIRONMENT.get(name, default))


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
