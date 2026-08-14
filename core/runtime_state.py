"""Private, agent-neutral runtime state shared by adapters."""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path


_AGENTS = frozenset(("claude", "codex"))
_DECISIONS = frozenset(("allow", "deny"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RuntimeState:
    """Read and write private pane and decision state for supported agents."""

    def __init__(self, root: Path | None = None):
        if root is None:
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
            root = (
                Path(runtime_dir) / "terminator-agent-notify"
                if runtime_dir
                else Path("/tmp") / f"terminator-agent-notify-{os.getuid()}"
            )
        self.root = Path(root)
        self._ensure_directory(self.root)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)

    @staticmethod
    def _check_agent(agent: str) -> None:
        if agent not in _AGENTS:
            raise ValueError(f"unsupported agent: {agent!r}")

    @staticmethod
    def _check_decision(decision: str) -> None:
        if decision not in _DECISIONS:
            raise ValueError(f"unsupported decision: {decision!r}")

    def pane_path(self, agent: str, session_id: str) -> Path:
        self._check_agent(agent)
        directory = self.root / "panes" / _digest(agent)
        self._ensure_directory(directory)
        return directory / f"{_digest(session_id)}.pane"

    def decision_path(self, agent: str, session_id: str, request_id: str) -> Path:
        self._check_agent(agent)
        directory = self.root / "decisions" / _digest(agent) / _digest(session_id)
        self._ensure_directory(directory)
        return directory / f"{_digest(request_id)}.decision"

    def record_pane(self, agent: str, session_id: str, pane: str) -> None:
        self._atomic_write(self.pane_path(agent, session_id), pane)

    def read_pane(self, agent: str, session_id: str) -> str | None:
        path = self.pane_path(agent, session_id)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write_decision(
        self, agent: str, session_id: str, request_id: str, decision: str
    ) -> None:
        self._check_decision(decision)
        self._atomic_write(self.decision_path(agent, session_id, request_id), decision)

    def consume_decision(
        self, agent: str, session_id: str, request_id: str
    ) -> str | None:
        path = self.decision_path(agent, session_id, request_id)
        claimed = path.with_name(f".{path.name}.{uuid.uuid4().hex}.consumed")
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            return None
        try:
            return claimed.read_text(encoding="utf-8")
        finally:
            try:
                claimed.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write(path: Path, value: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".runtime-state-", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

