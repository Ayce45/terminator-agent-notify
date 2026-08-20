import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from terminator_agent_notify_core.runtime_state import RuntimeState


def test_agent_sessions_are_isolated(tmp_path):
    state = RuntimeState(tmp_path)
    state.record_pane("claude", "same", "claude-pane")
    state.record_pane("codex", "same", "codex-pane")
    assert state.read_pane("claude", "same") == "claude-pane"
    assert state.read_pane("codex", "same") == "codex-pane"


def test_session_title_marker_is_persisted_per_agent_session(tmp_path):
    state = RuntimeState(tmp_path)

    assert state.title_was_set("codex", "s1") is False
    state.record_title_set("codex", "s1")

    assert state.title_was_set("codex", "s1") is True
    assert state.title_was_set("codex", "s2") is False


def test_title_claim_is_atomic_and_can_be_released(tmp_path):
    state = RuntimeState(tmp_path)

    assert state.claim_title("codex", "s1", "Private title") is True
    assert state.read_title_claim("codex", "s1") == "Private title"
    assert state.claim_title("codex", "s1") is False
    state.release_title_claim("codex", "s1")
    assert state.claim_title("codex", "s1") is True


def test_decision_is_consumed_once(tmp_path):
    state = RuntimeState(tmp_path)
    state.write_decision("codex", "s1", "r1", "allow")
    assert state.consume_decision("codex", "s1", "r1") == "allow"
    assert state.consume_decision("codex", "s1", "r1") is None


def test_request_closed_marker_is_separate_and_consumed_once(tmp_path):
    state = RuntimeState(tmp_path)
    state.write_decision("codex", "s1", "r1", "allow")

    state.write_request_closed("codex", "s1", "r1")

    assert state.consume_request_closed("codex", "s1", "r1") is True
    assert state.consume_request_closed("codex", "s1", "r1") is False
    assert state.consume_decision("codex", "s1", "r1") == "allow"


def test_invalid_decision_is_consumed_without_escaping_domain(tmp_path):
    state = RuntimeState(tmp_path)
    state.decision_path("codex", "s1", "r1").write_text("maybe", encoding="utf-8")
    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_decision("codex", "s1", "r1") is None


def test_paths_hash_identifiers_and_state_is_private(tmp_path):
    state = RuntimeState(tmp_path)
    pane = state.pane_path("claude", "session/with spaces")
    decision = state.decision_path("codex", "session", "request")
    assert "claude" not in str(pane)
    assert "session" not in str(pane)
    assert "codex" not in str(decision)
    assert "request" not in str(decision)

    state.record_pane("claude", "session/with spaces", "value")
    state.write_decision("codex", "session", "request", "deny")
    assert stat.S_IMODE(state.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(pane.stat().st_mode) == 0o600
    assert stat.S_IMODE(decision.stat().st_mode) == 0o600


def test_invalid_agent_and_decision_are_rejected(tmp_path):
    state = RuntimeState(tmp_path)
    for method, args in (
        (state.pane_path, ("other", "s")),
        (state.record_pane, ("other", "s", "value")),
        (state.write_decision, ("claude", "s", "r", "maybe")),
    ):
        try:
            method(*args)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid runtime state input was accepted")


def test_default_root_uses_xdg_runtime_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    state = RuntimeState()
    assert state.root == tmp_path / "terminator-agent-notify"


def test_default_root_without_xdg_is_private_and_uid_scoped(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    state = RuntimeState()

    assert state.root == Path("/tmp") / f"terminator-agent-notify-{os.getuid()}"
    assert stat.S_IMODE(state.root.stat().st_mode) == 0o700


def test_existing_runtime_root_symlink_is_rejected_before_chmod(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    root = tmp_path / "runtime-link"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError):
        RuntimeState(root)

    assert stat.S_IMODE(target.stat().st_mode) == 0o755


def test_existing_runtime_root_file_is_rejected_before_chmod(tmp_path):
    root = tmp_path / "runtime-file"
    root.write_text("not a directory", encoding="utf-8")
    root.chmod(0o640)

    with pytest.raises(NotADirectoryError):
        RuntimeState(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o640


def test_existing_runtime_root_owned_by_another_uid_is_rejected(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    real_lstat = os.lstat

    def foreign_owner(path):
        result = real_lstat(path)
        if os.fspath(path) == os.fspath(root):
            return SimpleNamespace(st_mode=result.st_mode, st_uid=os.getuid() + 1)
        return result

    monkeypatch.setattr(os, "lstat", foreign_owner)

    with pytest.raises(PermissionError):
        RuntimeState(root)
