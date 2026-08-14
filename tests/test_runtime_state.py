import os
import stat

from core.runtime_state import RuntimeState


def test_agent_sessions_are_isolated(tmp_path):
    state = RuntimeState(tmp_path)
    state.record_pane("claude", "same", "claude-pane")
    state.record_pane("codex", "same", "codex-pane")
    assert state.read_pane("claude", "same") == "claude-pane"
    assert state.read_pane("codex", "same") == "codex-pane"


def test_decision_is_consumed_once(tmp_path):
    state = RuntimeState(tmp_path)
    state.write_decision("codex", "s1", "r1", "allow")
    assert state.consume_decision("codex", "s1", "r1") == "allow"
    assert state.consume_decision("codex", "s1", "r1") is None


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
