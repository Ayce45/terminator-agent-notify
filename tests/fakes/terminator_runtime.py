"""Minimal in-process stand-ins for the Terminator plugin runtime."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from core.runtime_state import RuntimeState


class FakeNotifications:
    def __init__(self, notification_ids=None):
        self.created = []
        self.closed = []
        self.notification_ids = list(notification_ids or [])

    def Notify(self, *arguments):
        self.created.append(arguments)
        if self.notification_ids:
            return self.notification_ids.pop(0)
        return len(self.created)

    def CloseNotification(self, notification_id):
        self.closed.append(int(notification_id))


class FakeSignalMatch:
    def __init__(self):
        self.removed = False

    def remove(self):
        self.removed = True


class FakeBus:
    def __init__(
        self, notifications=None, fail_registration=None, fail_owner_lookup=False
    ):
        self.notifications = notifications or FakeNotifications()
        self.owner = ":1.notification-daemon"
        self.signal_receivers = []
        self.fail_registration = fail_registration
        self.fail_owner_lookup = fail_owner_lookup
        self.registration_calls = 0

    def get_object(self, _bus_name, _path):
        return self.notifications

    def get_name_owner(self, _bus_name):
        if self.fail_owner_lookup:
            raise RuntimeError("owner lookup failed")
        return self.owner

    def add_signal_receiver(self, callback, **kwargs):
        self.registration_calls += 1
        if self.registration_calls == self.fail_registration:
            raise RuntimeError("signal registration failed")
        match = FakeSignalMatch()
        self.signal_receivers.append((callback, kwargs, match))
        return match


def _install_modules():
    dbus = types.ModuleType("dbus")
    dbus.UInt32 = int
    dbus.Byte = int
    dbus.Interface = lambda proxy, interface: proxy
    dbus.SessionBus = lambda: None
    dbus_service = types.ModuleType("dbus.service")
    dbus_service.Object = type("Object", (), {"__init__": lambda self, *args: None})
    dbus_service.BusName = lambda *args: None
    dbus_service.method = lambda *args, **kwargs: lambda function: function
    dbus.service = dbus_service
    dbus_mainloop = types.ModuleType("dbus.mainloop")
    dbus_glib = types.ModuleType("dbus.mainloop.glib")
    dbus_glib.DBusGMainLoop = lambda **kwargs: None

    glib = types.SimpleNamespace(
        timeout_add=lambda *args: 0,
        timeout_add_seconds=lambda *args: 0,
        source_remove=lambda *args: None,
    )
    gtk = types.SimpleNamespace(Notebook=type("Notebook", (), {}), get_current_event_time=lambda: 0)
    gi = types.ModuleType("gi")
    gi_repository = types.ModuleType("gi.repository")
    gi_repository.GLib = glib
    gi_repository.Gtk = gtk

    plugin = types.ModuleType("terminatorlib.plugin")
    plugin.Plugin = type("Plugin", (), {"__init__": lambda self: None})
    terminator = types.ModuleType("terminatorlib.terminator")
    terminator.Terminator = lambda: types.SimpleNamespace(terminals=[])
    util = types.ModuleType("terminatorlib.util")
    util.err = lambda message: None

    sys.modules.update(
        {
            "dbus": dbus,
            "dbus.service": dbus_service,
            "dbus.mainloop": dbus_mainloop,
            "dbus.mainloop.glib": dbus_glib,
            "gi": gi,
            "gi.repository": gi_repository,
            "terminatorlib.plugin": plugin,
            "terminatorlib.terminator": terminator,
            "terminatorlib.util": util,
        }
    )


def _load_plugin():
    _install_modules()
    plugin_path = Path(__file__).parents[2] / "terminator-plugin" / "agent_notify.py"
    spec = importlib.util.spec_from_file_location("agent_notify_test", plugin_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_service(root):
    module = _load_plugin()
    notifications = FakeNotifications()
    state = RuntimeState(root)
    service = module.FocusService(None, "/test", notifications, state)
    service.focused = []
    service._focus = lambda pane_uuid: service.focused.append(str(pane_uuid)) or True
    return service, notifications, state


def make_plugin(
    root, fail_registration=None, fail_owner_lookup=False, notification_ids=None
):
    module = _load_plugin()
    bus = FakeBus(
        notifications=FakeNotifications(notification_ids),
        fail_registration=fail_registration,
        fail_owner_lookup=fail_owner_lookup,
    )
    module.dbus.SessionBus = lambda: bus
    module.RuntimeState = lambda: RuntimeState(root)
    plugin = module.AgentNotify()
    return plugin, bus
