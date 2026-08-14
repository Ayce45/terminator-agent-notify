from tests.fakes.terminator_runtime import make_service


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
