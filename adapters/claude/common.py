"""Shared controls for structured Claude lifecycle hooks."""

from __future__ import annotations

import os

from terminator_agent_notify_core.autoresume_guard import read_persisted_environment


def notifications_enabled() -> bool:
    explicit = os.environ.get("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS")
    if explicit is not None:
        return explicit != "0"
    persisted = read_persisted_environment()
    return persisted.get("TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS", "1") != "0"
