#!/usr/bin/env python3
"""Fail-closed, opt-in scheduling for Codex usage-limit resumes."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.runtime_state import RuntimeState


SEND = ROOT / "adapters" / "codex" / "send.sh"
SESSIONS = Path.home() / ".codex" / "sessions"
MAX_RECORDS = 60
FRESH_SECONDS = 600
# Auto-resume is intentionally limited to one week: a longer reset is likely a
# changed transcript format or a non-usage-limit message, so it must be ignored.
MAX_RESET_DELAY = timedelta(days=7)
SCAN_MTIME_SECONDS = 15 * 60
_RESET_TIMESTAMP = re.compile(
    r"\breset(?:s)?\s+(?:at|on)?\s*"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2}))",
    re.IGNORECASE,
)
_LIMIT_TEXT = re.compile(r"\b(?:rate[_ -]?limit(?:ed)?|usage[_ ]limit|limit[_ ]exceeded)\b", re.IGNORECASE)
_TIMESTAMP_KEYS = frozenset(("timestamp", "created_at", "createdat", "time"))
_STATUS_KEYS = frozenset(("status", "code", "http_status", "apierrorstatus", "error_code"))


class TranscriptError(ValueError):
    """A transcript could not be interpreted safely."""


def _log(message: str) -> None:
    print(f"codex-autoresume: {message}", file=sys.stderr)


def _strings(value: object, output: list[str]) -> None:
    if isinstance(value, str):
        output.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            _strings(child, output)
    elif isinstance(value, list):
        for child in value:
            _strings(child, output)


def _has_limit_signal(value: object) -> bool:
    strings: list[str] = []
    _strings(value, strings)
    if _LIMIT_TEXT.search(" ".join(strings)):
        return True

    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in _STATUS_KEYS and str(child).strip() == "429":
                return True
            if _has_limit_signal(child):
                return True
    elif isinstance(value, list):
        return any(_has_limit_signal(child) for child in value)
    return False


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _reset_time(value: object) -> datetime | None:
    strings: list[str] = []
    _strings(value, strings)
    matches = {match.group(1) for text in strings for match in _RESET_TIMESTAMP.finditer(text)}
    if len(matches) != 1:
        return None
    return _parse_timestamp(matches.pop())


def _event_time(value: object) -> datetime | None:
    candidates: list[datetime] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key.lower() in _TIMESTAMP_KEYS and isinstance(child, str):
                    parsed = _parse_timestamp(child)
                    if parsed is not None:
                        candidates.append(parsed)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    unique = {candidate.astimezone(timezone.utc) for candidate in candidates}
    return unique.pop() if len(unique) == 1 else None


def _session_id(value: object, fallback: str) -> str:
    found: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ("session_id", "sessionId") and isinstance(child, str) and child:
                    found.append(child)
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    unique = set(found)
    return unique.pop() if len(unique) == 1 else fallback


def _load_records(path: str | Path) -> list[dict]:
    """Read and parse only the final ``MAX_RECORDS`` JSONL records."""
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            position = stream.tell()
            blocks: list[bytes] = []
            newlines = 0
            while position > 0 and newlines <= MAX_RECORDS:
                size = min(8192, position)
                position -= size
                stream.seek(position)
                block = stream.read(size)
                blocks.append(block)
                newlines += block.count(b"\n")
        lines = b"".join(reversed(blocks)).splitlines()[-MAX_RECORDS:]
    except (OSError, UnicodeError) as error:
        raise TranscriptError(f"could not read transcript: {error}") from error
    records: list[dict] = []
    for line in lines[-MAX_RECORDS:]:
        try:
            value = json.loads(line.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise TranscriptError(f"could not parse transcript record: {error}") from error
        if isinstance(value, dict):
            records.append(value)
    return records


def _latest_limit(records: list[dict]) -> tuple[int | None, dict | None]:
    for index in range(len(records) - 1, -1, -1):
        record = records[index]
        if _has_limit_signal(record):
            return index, record
    return None, None


def _has_progress_signal(value: object) -> bool:
    """Recognize only explicit completion/progress records after a limit."""
    if _has_limit_signal(value):
        return False
    strings: list[str] = []
    _strings(value, strings)
    text = " ".join(strings).lower()
    return bool(
        re.search(
            r"\b(?:turn|task|request|response)[_ -]?(?:completed|complete|succeeded|success)\b"
            r"|\b(?:completed|succeeded|assistant)\b",
            text,
        )
    )


def _marker_path(state: RuntimeState, session_id: str, reset_epoch: int) -> Path:
    directory = state.root / "autoresume" / hashlib.sha256(b"codex").hexdigest()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return directory / f"{session_hash}-{reset_epoch}.scheduled"


def _claim_marker(path: Path) -> bool:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


def _schedule(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def process(
    path: str | Path,
    sid: str | None = None,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    scheduler: Callable[[list[str]], object] = _schedule,
    state: RuntimeState | None = None,
    logger: Callable[[str], object] = _log,
) -> bool:
    """Schedule a single fresh, explicitly timed Codex resume, or do nothing."""
    if os.environ.get("CODEX_AUTORESUME") != "1":
        return False
    try:
        records = _load_records(path)
        limit_index, limit = _latest_limit(records)
        if limit is None:
            return False
        now = clock().astimezone(timezone.utc)
        reset = _reset_time(limit)
        event = _event_time(limit)
        if reset is None or event is None:
            return False
        reset = reset.astimezone(timezone.utc)
        if (
            event > now
            or reset <= now
            or now - event > timedelta(seconds=FRESH_SECONDS)
            or reset - now > MAX_RESET_DELAY
        ):
            return False
        if any(_has_progress_signal(record) for record in records[(limit_index or 0) + 1 :]):
            return False
        session_id = _session_id(limit, sid or Path(path).stem)
        reset_epoch = int(reset.timestamp())
        delay = max(1, int((reset - now).total_seconds()))
        runtime = state or RuntimeState()
        marker = _marker_path(runtime, session_id, reset_epoch)
        if not _claim_marker(marker):
            return False
        message = os.environ.get("CODEX_AUTORESUME_MESSAGE", "continue")
        session_prefix = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8]
        command = [
            "systemd-run", "--user", "--collect",
            f"--unit=codex-autoresume-{session_prefix}-{reset_epoch}",
            f"--on-active={delay}s", "--timer-property=AccuracySec=1s",
            f"--description=Codex auto-resume after usage limit ({message})",
            str(SEND), "--session", session_id, "--message", message, "--notify",
        ]
        try:
            result = scheduler(command)
            if result is False:
                raise RuntimeError("scheduler rejected the unit")
        except Exception as error:
            marker.unlink(missing_ok=True)
            logger(f"could not schedule resume: {error}")
            return False
        return True
    except Exception as error:
        logger(f"could not parse transcript: {error}")
        return False


def scan(
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    scheduler: Callable[[list[str]], object] = _schedule,
    logger: Callable[[str], object] = _log,
) -> None:
    try:
        cutoff = clock().astimezone(timezone.utc).timestamp() - SCAN_MTIME_SECONDS
    except Exception as error:
        logger(f"could not establish scan cutoff: {error}")
        return
    for filename in glob.glob(str(SESSIONS / "**" / "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(filename) < cutoff:
                continue
            process(filename, clock=clock, scheduler=scheduler, logger=logger)
        except Exception as error:
            logger(f"scan skipped {filename}: {error}")


def main() -> int:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
