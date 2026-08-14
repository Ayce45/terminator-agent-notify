#!/usr/bin/env python3
"""Persist installer environment controls for hooks and user services."""

from __future__ import annotations

import argparse
import os
import secrets
import shlex
import tempfile
from pathlib import Path


AGENTS = ("claude", "codex")
ORDER = (
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
)


def _read(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        if not line or line.lstrip().startswith("#"):
            continue
        try:
            words = shlex.split(line, comments=True, posix=True)
        except ValueError:
            continue
        if len(words) != 1 or "=" not in words[0]:
            continue
        key, value = words[0].split("=", 1)
        if key in ORDER:
            values[key] = value
    return values


def _clean(value: str, default: str) -> str:
    value = value.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return value if value else default


def _nonnegative_integer(value: str, default: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return str(parsed) if parsed >= 0 else default


def _defaults(path: Path) -> dict[str, str]:
    return {
        "TERMINATOR_AGENT_NOTIFY_CONFIG": str(path),
        "TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS": "1",
        # Empty preserves each existing notification boundary's historical
        # default. A configured non-negative value is passed through exactly.
        "TERMINATOR_AGENT_NOTIFY_EXPIRY_MS": "",
        "TERMINATOR_AGENT_NOTIFY_LOG_LEVEL": "info",
        "CLAUDE_AUTORESUME": "1",
        "CLAUDE_AUTORESUME_GENERATION": "",
        "CLAUDE_AUTORESUME_MESSAGE": "continue",
        "CLAUDE_AUTORESUME_BUFFER": "90",
        "CLAUDE_AUTORESUME_ARM_NOTIFY": "1",
        "CODEX_AUTORESUME": "0",
        "CODEX_AUTORESUME_GENERATION": "",
        "CODEX_AUTORESUME_MESSAGE": "continue",
    }


def _write(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = "".join(f"{key}={shlex.quote(values[key])}\n" for key in ORDER)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update(path: Path, action: str, agents: list[str]) -> None:
    values = _defaults(path)
    values.update(_read(path))
    values["TERMINATOR_AGENT_NOTIFY_CONFIG"] = str(path)

    if action == "install":
        values["TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS"] = (
            "0" if os.environ.get("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS") == "0" else "1"
        )
        values["TERMINATOR_AGENT_NOTIFY_EXPIRY_MS"] = _nonnegative_integer(
            os.environ.get("TERMINATOR_AGENT_NOTIFY_EXPIRY_MS", ""), ""
        )
        level = os.environ.get("TERMINATOR_AGENT_NOTIFY_LOG_LEVEL", "info").lower()
        values["TERMINATOR_AGENT_NOTIFY_LOG_LEVEL"] = (
            level if level in {"quiet", "info", "debug"} else "info"
        )

    for agent in agents:
        prefix = agent.upper()
        values[f"{prefix}_AUTORESUME_GENERATION"] = secrets.token_hex(24)
        if action == "disable":
            values[f"{prefix}_AUTORESUME"] = "0"
            continue
        if agent == "claude":
            values["CLAUDE_AUTORESUME"] = (
                "0" if os.environ.get("CLAUDE_AUTORESUME", "1") == "0" else "1"
            )
            values["CLAUDE_AUTORESUME_MESSAGE"] = _clean(
                os.environ.get("CLAUDE_AUTORESUME_MESSAGE", "continue"), "continue"
            )
            values["CLAUDE_AUTORESUME_BUFFER"] = _nonnegative_integer(
                os.environ.get("CLAUDE_AUTORESUME_BUFFER", "90"), "90"
            )
            values["CLAUDE_AUTORESUME_ARM_NOTIFY"] = (
                "0" if os.environ.get("CLAUDE_AUTORESUME_ARM_NOTIFY", "1") == "0" else "1"
            )
        else:
            values["CODEX_AUTORESUME"] = (
                "1" if os.environ.get("CODEX_AUTORESUME", "0") == "1" else "0"
            )
            values["CODEX_AUTORESUME_MESSAGE"] = _clean(
                os.environ.get("CODEX_AUTORESUME_MESSAGE", "continue"), "continue"
            )
    _write(path, values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "disable"))
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("agents", nargs="+", choices=AGENTS)
    arguments = parser.parse_args()
    update(arguments.file, arguments.action, arguments.agents)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
