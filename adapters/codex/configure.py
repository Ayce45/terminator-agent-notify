#!/usr/bin/env python3
"""Merge or remove terminator-agent-notify hooks in Codex hooks.json."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import tempfile
import time
from pathlib import Path


MARKER = "terminator-agent-notify:"
# PermissionRequest performs one bounded D-Bus registration call before waiting
# and one bounded dismissal call during cleanup.  Keep the Codex host deadline
# strictly beyond both calls, the maximum local wait, and explicit cleanup time.
MAX_APPROVAL_WAIT_SECONDS = 300
DBUS_CALL_BUDGET_SECONDS = 5
APPROVAL_CLEANUP_MARGIN_SECONDS = 5
PERMISSION_HOOK_TIMEOUT = (
    MAX_APPROVAL_WAIT_SECONDS
    + 2 * DBUS_CALL_BUDGET_SECONDS
    + APPROVAL_CLEANUP_MARGIN_SECONDS
    + 1
)


def _owned(entry: object) -> bool:
    return isinstance(entry, dict) and str(entry.get("description", "")).startswith(
        MARKER
    )


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    hooks = value.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"{path}: hooks must be a JSON object")
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError(f"{path}: hooks.{event} must be a JSON array")
    return value


def _write_changed(path: Path, value: dict) -> bool:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    previous = path.read_bytes() if path.exists() else None
    if previous == payload.encode("utf-8"):
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with temporary.open(encoding="utf-8") as stream:
            json.load(stream)
        os.chmod(temporary, mode)
        if path.exists():
            backup = path.with_name(f"{path.name}.bak.{time.time_ns()}")
            shutil.copy2(path, backup)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _entry(event: str, command: Path, *, timeout: int | None = None) -> dict:
    hook = {"type": "command", "command": shlex.quote(str(command))}
    if timeout is not None:
        hook["timeout"] = timeout
    return {
        "description": f"{MARKER}codex:{event}",
        "hooks": [hook],
    }


def _without_owned(config: dict) -> dict:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return config
    for event in list(hooks):
        entries = hooks[event]
        filtered = [entry for entry in entries if not _owned(entry)]
        if filtered:
            hooks[event] = filtered
        elif filtered != entries:
            del hooks[event]
    if not hooks:
        config.pop("hooks", None)
    return config


def _has_owned(config: dict) -> bool:
    return any(
        _owned(entry)
        for entries in config.get("hooks", {}).values()
        for entry in entries
    )


def install(config_path: Path, install_root: Path) -> bool:
    config = _without_owned(_read(config_path))
    hook_dir = install_root / "adapters" / "codex" / "hooks"
    hooks = config.setdefault("hooks", {})
    desired = {
        "SessionStart": _entry("session-start", hook_dir / "session_start.py"),
        "UserPromptSubmit": _entry(
            "user-prompt-submit", hook_dir / "user_prompt_submit.py"
        ),
        "Stop": _entry("stop", hook_dir / "stop.py"),
        "PreToolUse": _entry("pre-tool-use", hook_dir / "pre_tool_use.py"),
        "PermissionRequest": _entry(
            "permission-request",
            hook_dir / "permission_request.py",
            timeout=PERMISSION_HOOK_TIMEOUT,
        ),
    }
    for event, entry in desired.items():
        hooks.setdefault(event, []).append(entry)
    return _write_changed(config_path, config)


def uninstall(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    config = _read(config_path)
    if not _has_owned(config):
        return False
    return _write_changed(config_path, _without_owned(config))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--install-root", type=Path)
    args = parser.parse_args()
    if args.action == "install":
        if args.install_root is None:
            parser.error("--install-root is required for install")
        install(args.config, args.install_root)
    else:
        uninstall(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
