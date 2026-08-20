import stat
from pathlib import Path
from types import SimpleNamespace

from tests.fakes.terminator_runtime import make_plugin, make_service


PAYLOAD = '{"title":"Agent","body":"Action needed"}'


def test_approval_actions_are_bound_to_request(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True

    nid = service.notify(
        "codex",
        "s1",
        "r1",
        "pane",
        "permission",
        '{"title":"Codex","body":"Run command?"}',
    )
    service.on_action(nid, "approve")

    assert injected == [("pane", "\r")]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert notifications.closed == [nid]


def test_plain_notification_click_focuses_without_decision(tmp_path):
    service, notifications, state = make_service(tmp_path)

    nid = service.notify(
        "codex",
        "s1",
        "",
        "pane",
        "complete",
        '{"title":"Done","body":"Ready"}',
    )
    service.on_action(nid, "default")

    assert service.focused == ["pane"]
    assert state.consume_decision("codex", "s1", "") is None
    assert notifications.closed == [nid]


def test_claude_approval_and_denial_inject_keys(tmp_path):
    service, notifications, _state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True

    approve = service.notify("claude", "s1", "r1", "pane", "permission", PAYLOAD)
    deny = service.notify("claude", "s1", "r2", "pane", "permission", PAYLOAD)
    service.on_action(approve, "approve")
    service.on_action(deny, "deny")

    assert injected == [("pane", "\r"), ("pane", "\x1b")]
    assert notifications.closed == [approve, deny]


def test_codex_deny_is_bound_to_its_request(tmp_path):
    service, _notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True

    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    service.on_action(nid, "deny")

    assert injected == [("pane", "\x1b")]
    assert state.consume_decision("codex", "s1", "r1") is None


def test_codex_permission_actions_inject_terminal_prompt_keys(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True

    approve = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    deny = service.notify("codex", "s1", "r2", "pane", "permission", PAYLOAD)
    service.on_action(approve, "approve")
    service.on_action(deny, "deny")

    assert injected == [("pane", "\r"), ("pane", "\x1b")]
    assert notifications.closed == [approve, deny]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_decision("codex", "s1", "r2") is None


def test_duplicate_or_late_action_is_harmless(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_action(nid, "approve")
    service.on_action(nid, "deny")

    assert injected == [("pane", "\r")]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert notifications.closed == [nid]


def test_dismiss_request_retires_only_its_exact_request(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    first = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    second = service.notify("codex", "s1", "r2", "pane", "permission", PAYLOAD)

    assert service.DismissRequest("codex", "s1", "r1") is True
    service.on_action(first, "approve")
    service.on_action(second, "approve")

    assert notifications.closed == [first, second]
    assert injected == [("pane", "\r")]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_decision("codex", "s1", "r2") is None


def test_dismiss_request_retires_before_closing_notification(tmp_path):
    service, notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    close = notifications.CloseNotification

    def action_during_close(notification_id):
        service.on_action(notification_id, "approve")
        close(notification_id)

    notifications.CloseNotification = action_during_close

    service.DismissRequest("codex", "s1", "r1")

    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_request_closed("codex", "s1", "r1") is True
    assert notifications.closed == [nid]


def test_notification_closed_retires_request_and_late_action_is_harmless(tmp_path):
    service, _notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_closed(nid, 2)
    service.on_action(nid, "approve")

    assert state.consume_request_closed("codex", "s1", "r1") is True
    assert state.consume_decision("codex", "s1", "r1") is None


def test_action_driven_close_does_not_mark_request_closed(tmp_path):
    service, _notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_action(nid, "approve")
    service.on_closed(nid, 3)

    assert injected == [("pane", "\r")]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_request_closed("codex", "s1", "r1") is False


def test_default_focus_action_marks_codex_permission_closed(tmp_path):
    service, notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_action(nid, "default")
    service.on_closed(nid, 3)

    assert service.focused == ["pane"]
    assert notifications.closed == [nid]
    assert state.consume_request_closed("codex", "s1", "r1") is True
    assert state.consume_request_closed("codex", "s1", "r1") is False
    assert state.consume_decision("codex", "s1", "r1") is None


def test_session_dismissal_preserves_other_agent_namespace(tmp_path):
    service, notifications, state = make_service(tmp_path)
    codex = service.notify("codex", "same", "r1", "pane", "permission", PAYLOAD)
    claude = service.notify("claude", "same", "r1", "pane", "permission", PAYLOAD)

    assert service.DismissSession("codex", "same") is True
    service.on_closed(codex, 3)

    assert notifications.closed == [codex]
    assert claude in service._notif_meta
    assert state.consume_request_closed("codex", "same", "r1") is True
    assert state.consume_request_closed("codex", "same", "r1") is False
    assert state.consume_request_closed("claude", "same", "r1") is False


def test_focus_cleanup_dismisses_every_notification_of_the_pane(tmp_path):
    service, notifications, state = make_service(tmp_path)
    permission = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    complete = service.notify("codex", "s2", "", "pane", "complete", PAYLOAD)

    service.dismiss_pane("pane")

    assert sorted(notifications.closed) == sorted([permission, complete])
    assert permission not in service._notif_meta
    assert state.consume_request_closed("codex", "s1", "r1") is True
    assert state.consume_decision("codex", "s1", "r1") is None


def test_focus_cleanup_dismisses_claude_permission_without_key_injection(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    permission = service.notify("claude", "s1", "r1", "pane", "permission", PAYLOAD)

    service.dismiss_pane("pane")

    assert notifications.closed == [permission]
    assert injected == []
    assert state.consume_request_closed("claude", "s1", "r1") is False


def test_focus_cleanup_leaves_other_panes_untouched(tmp_path):
    service, notifications, state = make_service(tmp_path)
    other = service.notify("codex", "s1", "r1", "other-pane", "permission", PAYLOAD)
    service.notify("codex", "s2", "", "pane", "complete", PAYLOAD)

    service.dismiss_pane("pane")

    assert other in service._notif_meta
    assert other not in notifications.closed
    assert state.consume_request_closed("codex", "s1", "r1") is False


def test_notifications_carry_the_agent_icon(tmp_path):
    service, notifications, _state = make_service(tmp_path)

    service.notify("claude", "s1", "", "pane", "complete", PAYLOAD)
    service.notify("codex", "s2", "", "pane", "complete", PAYLOAD)

    icons = [arguments[2] for arguments in notifications.created]
    assert icons[0].endswith("assets/claude.png")
    assert icons[1].endswith("assets/codex-mark.png")


def test_notification_uses_custom_pane_title_without_changing_routing(tmp_path, monkeypatch):
    service, notifications, _state = make_service(tmp_path)
    terminal = SimpleNamespace(
        uuid="urn:uuid:pane",
        titlebar=SimpleNamespace(get_custom_string=lambda: "jira-run-bugs"),
    )
    monkeypatch.setitem(
        service.notify.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=[terminal]),
    )

    service.notify("claude", "s1", "", "pane", "waiting", PAYLOAD)

    assert notifications.created[0][3] == "Claude Code — jira-run-bugs"


def test_notification_uses_dynamic_terminal_title_when_no_custom_title(tmp_path, monkeypatch):
    service, notifications, _state = make_service(tmp_path)
    terminal = SimpleNamespace(
        uuid="urn:uuid:pane",
        titlebar=SimpleNamespace(get_custom_string=lambda: ""),
        get_window_title=lambda: "jira-run-bugs-automation",
    )
    monkeypatch.setitem(
        service.notify.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=[terminal]),
    )

    service.notify("claude", "s1", "", "pane", "waiting", PAYLOAD)

    assert notifications.created[0][3] == "Claude Code — jira-run-bugs-automation"


def test_find_notebook_accepts_terminator_notebook_compatible_container(tmp_path):
    service, _notifications, _state = make_service(tmp_path)
    module = service.notify.__globals__

    class Container:
        def __init__(self, parent=None):
            self.parent = parent

        def get_parent(self):
            return self.parent

    class Notebook(Container):
        def page_num(self, _page):
            return 2

        def set_current_page(self, _page):
            pass

        def get_n_pages(self):
            return 3

    notebook = Notebook()
    page = Container(notebook)
    terminal = Container(page)

    assert module["_find_notebook"](terminal) == (notebook, page)


def test_codex_permission_requires_session_and_request_ids(tmp_path):
    service, notifications, _state = make_service(tmp_path)

    assert service.notify("codex", "", "r1", "pane", "permission", PAYLOAD) == 0
    assert service.notify("codex", "s1", "", "pane", "permission", PAYLOAD) == 0
    assert notifications.created == []


def test_notification_signal_receivers_require_the_current_owner_and_unload(tmp_path):
    plugin, bus = make_plugin(tmp_path)
    service = plugin.service
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    assert len(bus.signal_receivers) == 3
    for _callback, registration, _match in bus.signal_receivers[:2]:
        assert registration["bus_name"] == "org.freedesktop.Notifications"
        assert registration["sender_keyword"] == "sender"
    owner_registration = bus.signal_receivers[2][1]
    assert owner_registration["signal_name"] == "NameOwnerChanged"
    assert owner_registration["arg0"] == "org.freedesktop.Notifications"

    action_callback, _registration, _match = bus.signal_receivers[0]
    action_callback(nid, "approve", sender=":1.forged")
    assert injected == []
    assert service._state.consume_decision("codex", "s1", "r1") is None

    action_callback(nid, "approve", sender=bus.owner)
    assert injected == [("pane", "\r")]
    assert service._state.consume_decision("codex", "s1", "r1") is None

    close_nid = service.notify("codex", "s1", "r2", "pane", "permission", PAYLOAD)
    close_callback, _registration, _match = bus.signal_receivers[1]
    close_callback(close_nid, 2, sender=":1.forged")
    assert close_nid in service._notif_meta
    close_callback(close_nid, 2, sender=bus.owner)
    assert close_nid not in service._notif_meta

    matches = [match for _callback, _registration, match in bus.signal_receivers]
    plugin.unload()
    assert all(match.removed for match in matches)


def test_partial_signal_registration_removes_the_first_match(tmp_path):
    plugin, bus = make_plugin(tmp_path, fail_registration=2)

    assert len(bus.signal_receivers) == 1
    assert bus.signal_receivers[0][2].removed is True
    assert plugin._notification_signal_matches == []
    assert plugin._notification_bus is None
    assert plugin.service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD) == 0
    assert bus.notifications.created == []


def test_owner_lookup_failure_rejects_actionable_notifications(tmp_path):
    plugin, bus = make_plugin(tmp_path, fail_owner_lookup=True)

    assert plugin.service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD) == 0
    assert bus.notifications.created == []


def test_daemon_restart_retires_requests_before_notification_id_reuse(tmp_path):
    plugin, bus = make_plugin(tmp_path, notification_ids=[41, 41])
    service = plugin.service
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    old_owner = bus.owner
    old_id = service.notify("codex", "s1", "old", "pane", "permission", PAYLOAD)
    assert service._notif_meta[old_id][5] == old_owner

    owner_callback = next(
        callback
        for callback, registration, _match in bus.signal_receivers
        if registration["signal_name"] == "NameOwnerChanged"
    )
    bus.owner = ":1.restarted-daemon"
    owner_callback(
        "org.freedesktop.Notifications",
        old_owner,
        bus.owner,
        sender="org.freedesktop.DBus",
    )

    assert service._notif_meta == {}
    assert service._session_notifs == {}
    assert service._request_notifs == {}
    assert service._state.consume_request_closed("codex", "s1", "old") is True

    new_id = service.notify("codex", "s1", "new", "pane", "permission", PAYLOAD)
    assert new_id == old_id
    action_callback = next(
        callback
        for callback, registration, _match in bus.signal_receivers
        if registration["signal_name"] == "ActionInvoked"
    )
    action_callback(new_id, "approve", sender=old_owner)
    assert injected == []
    assert service._state.consume_decision("codex", "s1", "new") is None
    action_callback(new_id, "approve", sender=bus.owner)
    assert injected == [("pane", "\r")]
    assert service._state.consume_decision("codex", "s1", "new") is None


def test_daemon_owner_loss_retires_requests_and_disables_notifications(tmp_path):
    plugin, bus = make_plugin(tmp_path)
    service = plugin.service
    notification_id = service.notify(
        "codex", "s1", "r1", "pane", "permission", PAYLOAD
    )
    owner_callback = next(
        callback
        for callback, registration, _match in bus.signal_receivers
        if registration["signal_name"] == "NameOwnerChanged"
    )

    owner_callback(
        "org.freedesktop.Notifications",
        bus.owner,
        "",
        sender="org.freedesktop.DBus",
    )

    assert notification_id not in service._notif_meta
    assert service._state.consume_request_closed("codex", "s1", "r1") is True
    assert service.notify("codex", "s1", "r2", "pane", "permission", PAYLOAD) == 0


def test_duplicate_notification_id_cannot_link_old_request_to_new_request(tmp_path):
    service, notifications, state = make_service(tmp_path)
    injected = []
    service._send_keys = lambda pane, keys: injected.append((pane, keys)) or True
    notifications.notification_ids[:] = [9, 9]
    first = service.notify("codex", "s1", "old", "pane", "permission", PAYLOAD)
    second = service.notify("codex", "s1", "new", "pane", "permission", PAYLOAD)

    assert first == second == 9
    assert state.consume_request_closed("codex", "s1", "old") is True
    service.DismissRequest("codex", "s1", "old")
    service.on_action(second, "approve")

    assert state.consume_decision("codex", "s1", "old") is None
    assert injected == [("pane", "\r")]
    assert state.consume_decision("codex", "s1", "new") is None
    assert notifications.closed == [second]


def test_nonpositive_notification_id_is_never_actionable(tmp_path):
    service, notifications, state = make_service(tmp_path)
    notifications.notification_ids[:] = [0]

    notification_id = service.notify(
        "codex", "s1", "r1", "pane", "permission", PAYLOAD
    )
    service.on_action(0, "approve")

    assert notification_id == 0
    assert service._notif_meta == {}
    assert service._session_notifs == {}
    assert service._request_notifs == {}
    assert state.consume_decision("codex", "s1", "r1") is None


def test_notifications_can_be_disabled_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "0")
    service, notifications, _state = make_service(tmp_path)

    assert service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD) == 0
    assert notifications.created == []


def test_persisted_notification_disable_reaches_plugin(tmp_path, monkeypatch):
    monkeypatch.delenv("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", raising=False)
    state_home = tmp_path / "state"
    config = state_home / "terminator-agent-notify" / "environment"
    config.parent.mkdir(mode=0o700, parents=True)
    config.write_text(
        "TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=0\n", encoding="utf-8"
    )
    config.chmod(0o600)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_CONFIG", str(config))
    service, notifications, _state = make_service(tmp_path / "runtime")

    assert service.notify("claude", "s1", "", "pane", "complete", PAYLOAD) == 0
    assert notifications.created == []


def test_notification_expiry_is_forwarded_to_daemon(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_EXPIRY_MS", "4500")
    service, notifications, _state = make_service(tmp_path)

    assert service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD) != 0
    assert notifications.created[0][-1] == 4500


def test_quiet_and_debug_log_levels_control_plugin_output(tmp_path, monkeypatch):
    quiet_root = tmp_path / "quiet"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(quiet_root))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_LOG_LEVEL", "quiet")
    quiet_service, _notifications, _state = make_service(tmp_path / "quiet-state")
    quiet_module = quiet_service.notify.__globals__
    quiet_module["_log"]("hidden", level="info")
    assert not Path(quiet_module["LOG_PATH"]).exists()

    debug_root = tmp_path / "debug"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(debug_root))
    monkeypatch.setenv("TERMINATOR_AGENT_NOTIFY_LOG_LEVEL", "debug")
    debug_service, _notifications, _state = make_service(tmp_path / "debug-state")
    debug_module = debug_service.notify.__globals__
    debug_module["_log"]("diagnostic", level="debug")
    assert "diagnostic" in Path(debug_module["LOG_PATH"]).read_text(encoding="utf-8")


def test_reconcile_continues_after_one_vte_connect_failure(tmp_path, monkeypatch):
    plugin, _bus = make_plugin(tmp_path)

    class Vte:
        def __init__(self, handler_id=None, broken=False):
            self.handler_id = handler_id
            self.broken = broken

        def connect(self, _signal, _callback):
            if self.broken:
                raise RuntimeError("broken terminal")
            return self.handler_id

    first = Vte(handler_id=11)
    broken = Vte(broken=True)
    third = Vte(handler_id=33)
    terminals = [
        SimpleNamespace(vte=first),
        SimpleNamespace(vte=broken),
        SimpleNamespace(vte=third),
    ]
    monkeypatch.setitem(
        plugin._reconcile_handlers.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=terminals),
    )

    plugin._reconcile_handlers()

    assert plugin._focus_handlers == {first: (11, 11, 11), third: (33, 33, 33)}


def test_reconcile_dismisses_on_focus_mouse_and_keyboard_interaction(tmp_path, monkeypatch):
    plugin, _bus = make_plugin(tmp_path)

    class Vte:
        def __init__(self):
            self.callbacks = {}

        def connect(self, signal, callback):
            self.callbacks[signal] = callback
            return len(self.callbacks)

    vte = Vte()
    terminal = SimpleNamespace(vte=vte, uuid="urn:uuid:pane")
    monkeypatch.setitem(
        plugin._reconcile_handlers.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=[terminal]),
    )
    dismissed = []
    plugin.service.dismiss_pane = lambda pane: dismissed.append(str(pane))

    plugin._reconcile_handlers()
    for signal in ("focus-in-event", "button-press-event", "key-press-event"):
        vte.callbacks[signal](vte, None)

    assert dismissed == ["urn:uuid:pane"] * 3


def test_reconcile_keeps_supported_interaction_handlers_when_one_signal_fails(
    tmp_path, monkeypatch
):
    plugin, _bus = make_plugin(tmp_path)

    class Vte:
        def connect(self, signal, _callback):
            if signal == "button-press-event":
                raise RuntimeError("unsupported")
            return {"focus-in-event": 11, "key-press-event": 33}[signal]

    vte = Vte()
    monkeypatch.setitem(
        plugin._reconcile_handlers.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=[SimpleNamespace(vte=vte)]),
    )

    plugin._reconcile_handlers()

    assert plugin._focus_handlers == {vte: (11, 33)}


def test_get_focused_uuid_returns_the_focused_fake_terminal(tmp_path, monkeypatch):
    service, _notifications, _state = make_service(tmp_path)
    focused = SimpleNamespace(uuid="urn:uuid:focused-pane", vte=SimpleNamespace(has_focus=lambda: True))
    unfocused = SimpleNamespace(uuid="urn:uuid:other-pane", vte=SimpleNamespace(has_focus=lambda: False))
    monkeypatch.setitem(
        service.GetFocusedUUID.__globals__,
        "Terminator",
        lambda: SimpleNamespace(terminals=[unfocused, focused]),
    )

    assert service.GetFocusedUUID() == "urn:uuid:focused-pane"


def test_plugin_log_uses_private_runtime_root_and_secure_file_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    service, _notifications, _state = make_service(tmp_path / "service-state")
    module = service.notify.__globals__

    module["_log"]("created securely")

    log_path = Path(module["LOG_PATH"])
    assert log_path.parent == tmp_path / "terminator-agent-notify"
    assert stat.S_IMODE(log_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_plugin_log_refuses_to_follow_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    service, _notifications, _state = make_service(tmp_path / "service-state")
    module = service.notify.__globals__
    log_path = Path(module["LOG_PATH"])
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    victim = tmp_path / "victim"
    victim.write_text("unchanged", encoding="utf-8")
    log_path.symlink_to(victim)

    module["_log"]("must not escape")

    assert victim.read_text(encoding="utf-8") == "unchanged"
