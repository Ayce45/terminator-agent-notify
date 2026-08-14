from types import SimpleNamespace

from tests.fakes.terminator_runtime import make_plugin, make_service


PAYLOAD = '{"title":"Agent","body":"Action needed"}'


def test_approval_actions_are_bound_to_request(tmp_path):
    service, notifications, state = make_service(tmp_path)

    nid = service.notify(
        "codex",
        "s1",
        "r1",
        "pane",
        "permission",
        '{"title":"Codex","body":"Run command?"}',
    )
    service.on_action(nid, "approve")

    assert state.consume_decision("codex", "s1", "r1") == "allow"
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

    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    service.on_action(nid, "deny")

    assert state.consume_decision("codex", "s1", "r1") == "deny"


def test_duplicate_or_late_action_is_harmless(tmp_path):
    service, notifications, state = make_service(tmp_path)
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_action(nid, "approve")
    service.on_action(nid, "deny")

    assert state.consume_decision("codex", "s1", "r1") == "allow"
    assert notifications.closed == [nid]


def test_dismiss_request_retires_only_its_exact_request(tmp_path):
    service, notifications, state = make_service(tmp_path)
    first = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    second = service.notify("codex", "s1", "r2", "pane", "permission", PAYLOAD)

    assert service.DismissRequest("codex", "s1", "r1") is True
    service.on_action(first, "approve")
    service.on_action(second, "approve")

    assert notifications.closed == [first, second]
    assert state.consume_decision("codex", "s1", "r1") is None
    assert state.consume_decision("codex", "s1", "r2") == "allow"


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
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    service.on_action(nid, "approve")
    service.on_closed(nid, 3)

    assert state.consume_decision("codex", "s1", "r1") == "allow"
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


def test_focus_cleanup_only_dismisses_non_permission_notifications(tmp_path):
    service, notifications, _state = make_service(tmp_path)
    permission = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)
    complete = service.notify("codex", "s2", "", "pane", "complete", PAYLOAD)

    service.dismiss_pane("pane")

    assert notifications.closed == [complete]
    assert permission in service._notif_meta


def test_codex_permission_requires_session_and_request_ids(tmp_path):
    service, notifications, _state = make_service(tmp_path)

    assert service.notify("codex", "", "r1", "pane", "permission", PAYLOAD) == 0
    assert service.notify("codex", "s1", "", "pane", "permission", PAYLOAD) == 0
    assert notifications.created == []


def test_notification_signal_receivers_require_the_current_owner_and_unload(tmp_path):
    plugin, bus = make_plugin(tmp_path)
    service = plugin.service
    nid = service.notify("codex", "s1", "r1", "pane", "permission", PAYLOAD)

    assert len(bus.signal_receivers) == 2
    for _callback, registration, _match in bus.signal_receivers:
        assert registration["bus_name"] == "org.freedesktop.Notifications"
        assert registration["sender_keyword"] == "sender"

    action_callback, _registration, _match = bus.signal_receivers[0]
    action_callback(nid, "approve", sender=":1.forged")
    assert service._state.consume_decision("codex", "s1", "r1") is None

    action_callback(nid, "approve", sender=bus.owner)
    assert service._state.consume_decision("codex", "s1", "r1") == "allow"

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
