#!/usr/bin/env python3
"""Install and selectively remove hash-tracked fixed destination files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


UNIT_PREFIX = "terminator-agent-notify"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Candidate:
    payload: bytes
    mode: int
    scope: str
    retain: bool = False

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": MANIFEST_VERSION, "files": {}}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read managed-file manifest {path}: {error}") from error
    if not isinstance(value, dict) or value.get("version") != MANIFEST_VERSION:
        raise RuntimeError(f"unsupported managed-file manifest: {path}")
    files = value.get("files")
    if not isinstance(files, dict):
        raise RuntimeError(f"invalid managed-file manifest: {path}")
    return value


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_manifest(path: Path, manifest: dict) -> None:
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(path, payload, 0o600)


def _candidate_from_file(path: Path, scope: str, *, retain: bool = False) -> Candidate:
    return Candidate(
        path.read_bytes(), stat.S_IMODE(path.stat().st_mode), scope, retain
    )


def _add_tree(
    candidates: dict[Path, Candidate], source: Path, destination: Path, scope: str
) -> None:
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            relative = path.relative_to(source)
            candidates[destination / relative] = _candidate_from_file(path, scope)


def _systemd_escape(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def _candidates(arguments: argparse.Namespace) -> dict[Path, Candidate]:
    source = arguments.source
    candidates: dict[Path, Candidate] = {}
    candidates[arguments.terminator_root / "plugins" / "agent_notify.py"] = (
        _candidate_from_file(source / "terminator-plugin" / "agent_notify.py", "shared")
    )
    _add_tree(
        candidates,
        source / "terminator_agent_notify_core",
        arguments.terminator_root / "terminator_agent_notify_core",
        "shared",
    )
    _add_tree(
        candidates,
        source / "terminator_agent_notify_core",
        arguments.install_root / "terminator_agent_notify_core",
        "shared",
    )
    _add_tree(
        candidates,
        source / "assets",
        arguments.terminator_root / "assets",
        "shared",
    )
    candidates[arguments.install_root / "adapters" / "__init__.py"] = (
        _candidate_from_file(source / "adapters" / "__init__.py", "shared")
    )

    escaped_root = _systemd_escape(arguments.install_root)
    escaped_environment = _systemd_escape(arguments.environment_file)
    for agent in arguments.agents:
        scope = f"agent:{agent}"
        _add_tree(
            candidates,
            source / "adapters" / agent,
            arguments.install_root / "adapters" / agent,
            scope,
        )
        service_source = (
            source
            / "systemd"
            / f"{UNIT_PREFIX}-{agent}-limit-poller.service.in"
        )
        rendered = (
            service_source.read_text(encoding="utf-8")
            .replace("@INSTALL_ROOT@", escaped_root)
            .replace("@ENV_FILE@", escaped_environment)
            .encode("utf-8")
        )
        candidates[
            arguments.systemd_user
            / f"{UNIT_PREFIX}-{agent}-limit-poller.service"
        ] = Candidate(rendered, 0o644, scope)
        timer_source = (
            source / "systemd" / f"{UNIT_PREFIX}-{agent}-limit-poller.timer"
        )
        candidates[
            arguments.systemd_user / f"{UNIT_PREFIX}-{agent}-limit-poller.timer"
        ] = Candidate(timer_source.read_bytes(), 0o644, scope)
        if agent == "claude":
            for asset in sorted((source / "assets").iterdir()):
                if asset.is_file():
                    candidates[arguments.home / ".claude" / "assets" / asset.name] = (
                        _candidate_from_file(asset, scope, retain=True)
                    )
    return candidates


def _preflight(candidates: dict[Path, Candidate], manifest: dict) -> None:
    entries = manifest["files"]
    conflicts: list[str] = []
    for path, candidate in candidates.items():
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            conflicts.append(f"{path} is not a regular file")
            continue
        current = _hash(path)
        entry = entries.get(str(path))
        if entry is None:
            if current != candidate.digest:
                conflicts.append(f"{path} already exists with foreign content")
            continue
        if not isinstance(entry, dict) or current != entry.get("sha256"):
            conflicts.append(f"{path} changed since it was installed")
        elif not entry.get("owned", False) and current != candidate.digest:
            conflicts.append(f"{path} is preserved pre-existing content")
    if conflicts:
        raise RuntimeError("fixed destination collision:\n  " + "\n  ".join(conflicts))


def _allowed(path: Path, roots: tuple[Path, ...]) -> bool:
    path_text = os.path.abspath(path)
    return any(
        os.path.commonpath((path_text, os.path.abspath(root))) == os.path.abspath(root)
        for root in roots
    )


def _remove_entry(path: Path, entry: dict, roots: tuple[Path, ...]) -> None:
    if not _allowed(path, roots):
        raise RuntimeError(f"refusing manifest path outside managed roots: {path}")
    if entry.get("retain") or not entry.get("owned"):
        return
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode) or _hash(path) != entry.get("sha256"):
        print(f"preserving changed managed file: {path}", file=sys.stderr)
        return
    path.unlink()


def _cleanup_directories(paths: list[Path], roots: tuple[Path, ...]) -> None:
    directories = sorted(
        {parent for path in paths for parent in path.parents if _allowed(parent, roots)},
        key=lambda value: len(value.parts),
        reverse=True,
    )
    for directory in directories:
        if directory in roots:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass


def install(arguments: argparse.Namespace) -> None:
    manifest = _read_manifest(arguments.manifest)
    candidates = _candidates(arguments)
    _preflight(candidates, manifest)
    entries = manifest["files"]
    scopes = {"shared", *(f"agent:{agent}" for agent in arguments.agents)}
    roots = (
        arguments.terminator_root,
        arguments.install_root,
        arguments.systemd_user,
        arguments.home / ".claude" / "assets",
    )

    obsolete = [
        Path(path)
        for path, entry in entries.items()
        if isinstance(entry, dict)
        and entry.get("scope") in scopes
        and Path(path) not in candidates
    ]
    for path in obsolete:
        _remove_entry(path, entries.pop(str(path)), roots)

    for path, candidate in candidates.items():
        existing = entries.get(str(path))
        exists = _regular_file(path)
        current = _hash(path) if exists else None
        owned = bool(existing and existing.get("owned")) or not exists
        if current != candidate.digest:
            _atomic_write(path, candidate.payload, candidate.mode)
        elif stat.S_IMODE(path.stat().st_mode) != candidate.mode:
            os.chmod(path, candidate.mode)
        entries[str(path)] = {
            "mode": candidate.mode,
            "owned": owned,
            "retain": candidate.retain,
            "scope": candidate.scope,
            "sha256": candidate.digest,
        }
    _write_manifest(arguments.manifest, manifest)
    _cleanup_directories(obsolete, roots)


def remove(arguments: argparse.Namespace) -> None:
    manifest = _read_manifest(arguments.manifest)
    entries = manifest["files"]
    scopes = {f"agent:{agent}" for agent in arguments.agents}
    if arguments.shared:
        scopes.add("shared")
    roots = (
        arguments.terminator_root,
        arguments.install_root,
        arguments.systemd_user,
        arguments.home / ".claude" / "assets",
    )
    removed_paths: list[Path] = []
    for path_text in list(entries):
        entry = entries[path_text]
        if not isinstance(entry, dict) or entry.get("scope") not in scopes:
            continue
        path = Path(path_text)
        _remove_entry(path, entry, roots)
        removed_paths.append(path)
        del entries[path_text]
    if entries:
        _write_manifest(arguments.manifest, manifest)
    else:
        arguments.manifest.unlink(missing_ok=True)
    _cleanup_directories(removed_paths, roots)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--environment-file", required=True, type=Path)
    parser.add_argument("--terminator-root", required=True, type=Path)
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--systemd-user", required=True, type=Path)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--shared", action="store_true")
    parser.add_argument("agents", nargs="*")
    return parser


def main() -> int:
    arguments = _parser().parse_intermixed_args()
    try:
        invalid = sorted(set(arguments.agents) - {"claude", "codex"})
        if invalid:
            raise RuntimeError(f"unsupported agents: {', '.join(invalid)}")
        if arguments.action == "install":
            if not arguments.agents:
                raise RuntimeError("install needs at least one agent")
            install(arguments)
        else:
            remove(arguments)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"terminator-agent-notify: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
