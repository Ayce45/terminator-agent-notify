#!/usr/bin/env python3
"""Merge or remove terminator-agent-notify hooks in Claude settings."""

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
PERMISSION_HOOK_TIMEOUT = 6


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


def _entry(
    agent: str,
    event: str,
    *commands: Path,
    matcher: str | None = None,
    timeout: int | None = None,
) -> dict:
    hooks = []
    for command in commands:
        hook = {"type": "command", "command": shlex.quote(str(command))}
        if timeout is not None:
            hook["timeout"] = timeout
        hooks.append(hook)
    entry = {
        "description": f"{MARKER}{agent}:{event}",
        "hooks": hooks,
    }
    if matcher is not None:
        entry["matcher"] = matcher
    return entry


def _legacy_owned(entry: object, expected_commands: tuple[Path, ...]) -> bool:
    if not isinstance(entry, dict) or entry.get("description") is not None:
        return False
    hooks = entry.get("hooks")
    if not isinstance(hooks, list) or len(hooks) != len(expected_commands):
        return False
    actual = []
    for hook in hooks:
        if not isinstance(hook, dict) or hook.get("type") != "command":
            return False
        try:
            words = shlex.split(str(hook.get("command", "")))
        except ValueError:
            return False
        if len(words) != 1:
            return False
        actual.append(Path(words[0]))
    return tuple(actual) == expected_commands


def _without_owned(
    config: dict, legacy_by_event: dict[str, tuple[Path, ...]] | None = None
) -> dict:
    hooks = config.get("hooks")
    if not isinstance(hooks, dict):
        return config
    for event in list(hooks):
        entries = hooks[event]
        legacy_commands = (legacy_by_event or {}).get(event)
        filtered = [
            entry
            for entry in entries
            if not _owned(entry)
            and not (
                legacy_commands is not None
                and _legacy_owned(entry, legacy_commands)
            )
        ]
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
    adapter = install_root / "adapters" / "claude"
    legacy_by_event = {
        "SessionStart": (adapter / "hooks" / "session-start.sh",),
        "Notification": (
            adapter / "hooks" / "notify-waiting.sh",
            adapter / "hooks" / "auto-resume-on-limit.sh",
        ),
        "Stop": (
            adapter / "hooks" / "notify-waiting.sh",
            adapter / "hooks" / "auto-resume-on-limit.sh",
        ),
        "UserPromptSubmit": (adapter / "hooks" / "notify-cleanup.sh",),
    }
    config = _without_owned(_read(config_path), legacy_by_event)
    hooks = config.setdefault("hooks", {})
    desired = {
        "SessionStart": _entry(
            "claude", "session-start", adapter / "hooks" / "session-start.sh"
        ),
        "PreToolUse": _entry(
            "claude",
            "pre-tool-use",
            adapter / "hooks" / "pre_tool_use.py",
            matcher="AskUserQuestion",
        ),
        "PermissionRequest": _entry(
            "claude",
            "permission-request",
            adapter / "hooks" / "permission_request.py",
            timeout=PERMISSION_HOOK_TIMEOUT,
        ),
        "Notification": _entry(
            "claude",
            "notification",
            adapter / "hooks" / "notify-waiting.sh",
            adapter / "hooks" / "auto-resume-on-limit.sh",
        ),
        "Stop": _entry(
            "claude",
            "stop",
            adapter / "hooks" / "notify-waiting.sh",
            adapter / "hooks" / "auto-resume-on-limit.sh",
        ),
        "UserPromptSubmit": _entry(
            "claude", "user-prompt-submit", adapter / "hooks" / "notify-cleanup.sh"
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
