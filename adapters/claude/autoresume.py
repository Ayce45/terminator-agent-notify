#!/usr/bin/env python3
"""Schedule a Claude Code ``continue`` after a fresh usage-limit response."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.runtime_state import RuntimeState


SEND = ROOT / "adapters" / "claude" / "send.sh"
PROJECTS = Path.home() / ".claude" / "projects"
MESSAGE = os.environ.get("CLAUDE_AUTORESUME_MESSAGE", "continue")
BUFFER = int(os.environ.get("CLAUDE_AUTORESUME_BUFFER", "90"))
FRESH_SEC = 600
SCAN_MTIME_MIN = 15


def _texts(value, output):
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            output.append(value["text"])
        for child in value.values():
            _texts(child, output)
    elif isinstance(value, list):
        for child in value:
            _texts(child, output)


def _is_assistant_reply(value):
    if value.get("isApiErrorMessage"):
        return False
    message = value.get("message", {}) or {}
    return value.get("type") == "assistant" and (message.get("role") or value.get("role")) == "assistant"


def _parse_iso(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _marker_path(session_id, reset_epoch):
    state = RuntimeState()
    directory = state.root / "autoresume" / hashlib.sha256(b"claude").hexdigest()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return directory / f"{session_hash}-{reset_epoch // 60}.scheduled"


def _claim_marker(path):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


def _notify(session_id, title, body):
    """Announce an armed resume through the same long-lived notification owner."""
    pane = RuntimeState().read_pane("claude", session_id) or ""
    payload = json.dumps({"title": title, "body": body})
    try:
        response = subprocess.run(
            [
                "gdbus", "call", "--session", "--dest", "io.github.TerminatorAgentNotify",
                "--object-path", "/io/github/TerminatorAgentNotify",
                "--method", "io.github.TerminatorAgentNotify.Notify", "claude", session_id,
                "", pane, "waiting", payload,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
        if response.returncode == 0 and b"uint32 0" not in response.stdout:
            return
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.run(
            ["notify-send", "--app-name=Claude Code", title, body], timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _load_objects(path):
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    objects = []
    for line in lines[-60:]:
        try:
            objects.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return objects


def _latest_limit(objects):
    for index in range(len(objects) - 1, -1, -1):
        value = objects[index]
        if value.get("isApiErrorMessage") and str(value.get("apiErrorStatus")) == "429":
            texts = []
            _texts(value, texts)
            if re.search(r"resets\s+\d", " ".join(texts), re.I):
                return index, value
    return None, None


def process(path, sid=None):
    """Schedule one resume for a fresh, unrecovered 429 transcript event."""
    if os.environ.get("CLAUDE_AUTORESUME", "1") == "0":
        return False
    objects = _load_objects(path)
    index, limit = _latest_limit(objects)
    if limit is None or any(_is_assistant_reply(value) for value in objects[index + 1 :]):
        return False

    now = datetime.now().astimezone()
    event_time = _parse_iso(limit.get("timestamp"))
    if event_time and (now - event_time).total_seconds() > FRESH_SEC:
        return False
    texts = []
    _texts(limit, texts)
    match = re.search(r"resets\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", " ".join(texts), re.I)
    if not match:
        return False
    hour, minute, period = int(match.group(1)), int(match.group(2) or 0), match.group(3).lower()
    if period == "pm" and hour != 12:
        hour += 12
    if period == "am" and hour == 12:
        hour = 0

    anchor = event_time.astimezone() if event_time else now
    target = anchor.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < anchor:
        target += timedelta(days=1)
    target += timedelta(seconds=BUFFER)
    reset_epoch = int(target.timestamp())
    delay = max(1, int((target - now).total_seconds()))
    session_id = limit.get("session_id") or limit.get("sessionId") or sid or Path(path).stem
    marker = _marker_path(session_id, reset_epoch)
    if not _claim_marker(marker):
        return False

    unit = f"claude-autoresume-{session_id[:8]}-{reset_epoch}"
    try:
        subprocess.run(
            [
                "systemd-run", "--user", "--collect", f"--unit={unit}",
                f"--on-active={delay}s", "--timer-property=AccuracySec=1s",
                f"--description=Claude auto-resume after usage limit ({MESSAGE})",
                str(SEND), "--session", session_id, "--message", MESSAGE, "--notify", "--resume",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.SubprocessError):
        marker.unlink(missing_ok=True)
        return False
    if os.environ.get("CLAUDE_AUTORESUME_ARM_NOTIFY", "1") != "0":
        _notify(
            session_id,
            "Claude Code — reprise programmée",
            f"Limite atteinte. Je relancerai « {MESSAGE} » automatiquement à {target:%H:%M}.",
        )
    return True


def scan():
    cutoff = datetime.now().timestamp() - SCAN_MTIME_MIN * 60
    for filename in glob.glob(str(PROJECTS / "*" / "*.jsonl")):
        try:
            if os.path.getmtime(filename) >= cutoff:
                process(filename)
        except OSError:
            continue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript")
    parser.add_argument("--session")
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    if args.scan:
        scan()
    elif args.transcript:
        process(args.transcript, args.session)
    else:
        parser.error("need --scan or --transcript")


if __name__ == "__main__":
    main()
