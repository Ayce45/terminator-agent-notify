#!/usr/bin/env python3
"""Enable or disable the AgentNotify Terminator plugin in the user config.

Only a narrow, provably safe edit is attempted: the config file must exist and
contain an ``enabled_plugins`` assignment. Anything else falls back to the
manual guidance without touching bytes. An ownership record makes uninstall
remove only the entry this installation added.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = "AgentNotify"
LINE_RE = re.compile(r"^(?P<prefix>\s*enabled_plugins\s*=)(?P<value>.*)$")
MANUAL_GUIDANCE = (
    "Could not update the Terminator config safely; "
    "enable AgentNotify manually in Terminator Preferences."
)


def _warn(message: str) -> None:
    print(message, file=sys.stderr)


def _find_line(lines: list[str]) -> tuple[int, str, list[str]] | None:
    for index, line in enumerate(lines):
        match = LINE_RE.match(line)
        if match:
            plugins = [p.strip() for p in match.group("value").split(",") if p.strip()]
            return index, match.group("prefix"), plugins
    return None


def _write_config(config: Path, lines: list[str]) -> None:
    mode = config.stat().st_mode & 0o777
    fd, tmp_name = tempfile.mkstemp(dir=str(config.parent), prefix=f".{config.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, config)
    except BaseException:
        os.unlink(tmp_name)
        raise


def _backup(config: Path) -> None:
    mode = config.stat().st_mode & 0o777
    backup = config.with_name(f"{config.name}.bak.{int(time.time())}")
    backup.write_bytes(config.read_bytes())
    backup.chmod(mode)


def install(config: Path, owned: Path) -> int:
    try:
        lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError):
        _warn(MANUAL_GUIDANCE)
        return 0
    found = _find_line(lines)
    if found is None:
        _warn(MANUAL_GUIDANCE)
        return 0
    index, prefix, plugins = found
    if PLUGIN in plugins:
        return 0
    _backup(config)
    plugins.append(PLUGIN)
    lines[index] = f"{prefix} {', '.join(plugins)}\n"
    _write_config(config, lines)
    owned.parent.mkdir(parents=True, exist_ok=True)
    owned.write_text(f"{PLUGIN}\n", encoding="utf-8")
    owned.chmod(0o600)
    return 0


def uninstall(config: Path, owned: Path) -> int:
    if not owned.is_file():
        return 0
    try:
        lines = config.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError):
        _warn(f"Could not read the Terminator config; remove {PLUGIN} manually.")
        return 0
    found = _find_line(lines)
    if found is None:
        _warn(
            "Terminator config no longer has an enabled_plugins entry; "
            f"remove {PLUGIN} manually if it is still enabled."
        )
        return 0
    index, prefix, plugins = found
    if PLUGIN in plugins:
        _backup(config)
        plugins = [p for p in plugins if p != PLUGIN]
        lines[index] = f"{prefix} {', '.join(plugins)}\n" if plugins else f"{prefix} \n"
        _write_config(config, lines)
    owned.unlink()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--owned", required=True, type=Path)
    args = parser.parse_args()
    handler = install if args.action == "install" else uninstall
    return handler(args.config, args.owned)


if __name__ == "__main__":
    raise SystemExit(main())
