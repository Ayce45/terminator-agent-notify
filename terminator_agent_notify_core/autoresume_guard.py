"""Validate rotating auto-resume generations from a private config file."""

from __future__ import annotations

import hmac
import os
import shlex
import stat
from pathlib import Path


PERSISTED_KEYS = frozenset(
    {
        "TERMINATOR_AGENT_NOTIFY_CONFIG",
        "TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS",
        "TERMINATOR_AGENT_NOTIFY_EXPIRY_MS",
        "TERMINATOR_AGENT_NOTIFY_LOG_LEVEL",
        "CLAUDE_AUTORESUME",
        "CLAUDE_AUTORESUME_GENERATION",
        "CLAUDE_AUTORESUME_MESSAGE",
        "CLAUDE_AUTORESUME_BUFFER",
        "CLAUDE_AUTORESUME_ARM_NOTIFY",
        "CODEX_AUTORESUME",
        "CODEX_AUTORESUME_GENERATION",
        "CODEX_AUTORESUME_MESSAGE",
    }
)


def _read_private_environment(path: str | Path) -> dict[str, str]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            return {}
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            return {}
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            lines = stream.read().splitlines()
    except (OSError, UnicodeError):
        return {}
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    values: dict[str, str] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        try:
            words = shlex.split(line, comments=True, posix=True)
        except ValueError:
            return {}
        if len(words) != 1 or "=" not in words[0]:
            return {}
        key, value = words[0].split("=", 1)
        values[key] = value
    return values


def generation_is_current(agent: str, path: str | Path, expected: str) -> bool:
    """Return true only for the currently enabled exact generation token."""
    if agent not in {"claude", "codex"} or not expected or not str(path):
        return False
    try:
        values = _read_private_environment(path)
    except OSError:
        return False
    prefix = agent.upper()
    current = values.get(f"{prefix}_AUTORESUME_GENERATION", "")
    return values.get(f"{prefix}_AUTORESUME") == "1" and hmac.compare_digest(
        current, expected
    )


def read_persisted_environment(path: str | Path | None = None) -> dict[str, str]:
    """Read installer-owned controls without mutating the process environment."""
    if path is None:
        path = os.environ.get("TERMINATOR_AGENT_NOTIFY_CONFIG")
    if not path:
        state_home = os.environ.get("XDG_STATE_HOME")
        if not state_home:
            home = os.environ.get("HOME")
            state_home = str(Path(home) / ".local" / "state") if home else ""
        if not state_home:
            return {}
        path = Path(state_home) / "terminator-agent-notify" / "environment"
    try:
        values = _read_private_environment(path)
    except OSError:
        return {}
    return {key: value for key, value in values.items() if key in PERSISTED_KEYS}
