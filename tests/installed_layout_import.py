"""Import the deployed plugin with no repository modules on ``sys.path``."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def install_boundary_stubs() -> None:
    dbus = types.ModuleType("dbus")
    dbus.UInt32 = int
    dbus.Byte = int
    dbus.Interface = lambda proxy, _interface: proxy
    dbus.SessionBus = lambda: None
    service = types.ModuleType("dbus.service")
    service.Object = type("Object", (), {"__init__": lambda self, *args: None})
    service.BusName = lambda *args: None
    service.method = lambda *args, **kwargs: lambda function: function
    dbus.service = service
    mainloop = types.ModuleType("dbus.mainloop")
    glib_mainloop = types.ModuleType("dbus.mainloop.glib")
    glib_mainloop.DBusGMainLoop = lambda **kwargs: None

    gi = types.ModuleType("gi")
    repository = types.ModuleType("gi.repository")
    repository.GLib = types.SimpleNamespace(
        timeout_add=lambda *args: 0,
        timeout_add_seconds=lambda *args: 0,
        source_remove=lambda *args: None,
    )
    repository.Gtk = types.SimpleNamespace(
        Notebook=type("Notebook", (), {}), get_current_event_time=lambda: 0
    )
    plugin = types.ModuleType("terminatorlib.plugin")
    plugin.Plugin = type("Plugin", (), {"__init__": lambda self: None})
    terminator = types.ModuleType("terminatorlib.terminator")
    terminator.Terminator = lambda: types.SimpleNamespace(terminals=[])
    util = types.ModuleType("terminatorlib.util")
    util.err = lambda _message: None
    sys.modules.update(
        {
            "dbus": dbus,
            "dbus.service": service,
            "dbus.mainloop": mainloop,
            "dbus.mainloop.glib": glib_mainloop,
            "gi": gi,
            "gi.repository": repository,
            "terminatorlib.plugin": plugin,
            "terminatorlib.terminator": terminator,
            "terminatorlib.util": util,
        }
    )


def main() -> None:
    terminator_root = Path(sys.argv[1]).resolve()
    install_boundary_stubs()
    plugin_path = terminator_root / "plugins" / "agent_notify.py"
    specification = importlib.util.spec_from_file_location(
        "installed_agent_notify", plugin_path
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    assert module.RuntimeState.__module__ == (
        "terminator_agent_notify_core.runtime_state"
    )
    assert "core" not in sys.modules


if __name__ == "__main__":
    main()
