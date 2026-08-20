"""Private runtime state shared by Terminator Agent Notify adapters."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
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
        try:
            path_status = os.lstat(path)
        except FileNotFoundError:
            try:
                path.mkdir(mode=0o700, parents=True, exist_ok=False)
            except FileExistsError:
                pass
            path_status = os.lstat(path)

        if stat.S_ISLNK(path_status.st_mode):
            raise OSError(errno.ELOOP, "runtime directory must not be a symlink", path)
        if not stat.S_ISDIR(path_status.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "runtime path is not a directory", path)
        if path_status.st_uid != os.getuid():
            raise PermissionError(errno.EPERM, "runtime directory has the wrong owner", path)

        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened_status = os.fstat(descriptor)
            if not stat.S_ISDIR(opened_status.st_mode):
                raise NotADirectoryError(
                    errno.ENOTDIR, "runtime path is not a directory", path
                )
            if opened_status.st_uid != os.getuid():
                raise PermissionError(
                    errno.EPERM, "runtime directory has the wrong owner", path
                )
            os.fchmod(descriptor, 0o700)
        finally:
            os.close(descriptor)

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

    def title_path(self, agent: str, session_id: str) -> Path:
        self._check_agent(agent)
        directory = self.root / "titles" / _digest(agent)
        self._ensure_directory(directory)
        return directory / f"{_digest(session_id)}.title"

    def title_claim_path(self, agent: str, session_id: str) -> Path:
        self._check_agent(agent)
        directory = self.root / "title-claims" / _digest(agent)
        self._ensure_directory(directory)
        return directory / f"{_digest(session_id)}.claim"

    def request_closed_path(
        self, agent: str, session_id: str, request_id: str
    ) -> Path:
        self._check_agent(agent)
        directory = self.root / "request-closed" / _digest(agent) / _digest(session_id)
        self._ensure_directory(directory)
        return directory / f"{_digest(request_id)}.closed"

    def record_pane(self, agent: str, session_id: str, pane: str) -> None:
        self._atomic_write(self.pane_path(agent, session_id), pane)

    def read_pane(self, agent: str, session_id: str) -> str | None:
        path = self.pane_path(agent, session_id)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def record_title_set(self, agent: str, session_id: str) -> None:
        self._atomic_write(self.title_path(agent, session_id), "set")

    def title_was_set(self, agent: str, session_id: str) -> bool:
        return self.title_path(agent, session_id).is_file()

    def claim_title(self, agent: str, session_id: str, title: str = "") -> bool:
        if self.title_was_set(agent, session_id):
            return False
        path = self.title_claim_path(agent, session_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(title)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        return True

    def read_title_claim(self, agent: str, session_id: str) -> str | None:
        try:
            return self.title_claim_path(agent, session_id).read_text(encoding="utf-8")
        except (FileNotFoundError, UnicodeDecodeError):
            return None

    def release_title_claim(self, agent: str, session_id: str) -> None:
        try:
            self.title_claim_path(agent, session_id).unlink()
        except FileNotFoundError:
            pass

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
            try:
                decision = claimed.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return None
            return decision if decision in _DECISIONS else None
        finally:
            try:
                claimed.unlink()
            except FileNotFoundError:
                pass

    def write_request_closed(
        self, agent: str, session_id: str, request_id: str
    ) -> None:
        self._atomic_write(
            self.request_closed_path(agent, session_id, request_id), "closed"
        )

    def consume_request_closed(
        self, agent: str, session_id: str, request_id: str
    ) -> bool:
        path = self.request_closed_path(agent, session_id, request_id)
        claimed = path.with_name(f".{path.name}.{uuid.uuid4().hex}.consumed")
        try:
            os.replace(path, claimed)
        except FileNotFoundError:
            return False
        try:
            return True
        finally:
            try:
                claimed.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write(path: Path, value: str) -> None:
        RuntimeState._ensure_directory(path.parent)
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
