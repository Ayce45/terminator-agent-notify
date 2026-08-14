"""Terminator D-Bus service for Claude and Codex notifications.

Install this plugin together with the sibling ``core`` directory:

    ~/.config/terminator/plugins/agent_notify.py
    ~/.config/terminator/core/runtime_state.py

The local import boundary below makes that installed layout work while keeping
the service constructor injectable for tests.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib, Gtk

from terminatorlib.plugin import Plugin
from terminatorlib.terminator import Terminator
from terminatorlib.util import err


def _load_runtime_state():
    """Load the shared core from the Terminator configuration root.

    Terminator imports plugins directly from its ``plugins`` directory, which
    does not normally include its parent on ``sys.path``.  The installer copies
    ``core`` beside that directory, so make this explicit rather than relying
    on the caller's working directory.
    """

    configuration_root = str(Path(__file__).resolve().parent.parent)
    if configuration_root not in sys.path:
        sys.path.insert(0, configuration_root)
    from core.runtime_state import RuntimeState

    return RuntimeState


RuntimeState = _load_runtime_state()

# Ensure notification action and close signals are received in Terminator's
# long-lived GLib main loop. It is harmless if another component did this too.
DBusGMainLoop(set_as_default=True)

AVAILABLE = ["AgentNotify"]
BUS_NAME = "io.github.TerminatorAgentNotify"
OBJ_PATH = "/io/github/TerminatorAgentNotify"
NOTIFS_BUS = "org.freedesktop.Notifications"
NOTIFS_PATH = "/org/freedesktop/Notifications"
KEY_APPROVE = "\r"
KEY_DENY = "\x1b"
ICON_PATH = str(Path(__file__).resolve().parent.parent / "assets" / "claude.png")
LOG_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "terminator-agent-notify.log"
)


def _log(message):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as stream:
            stream.write(
                "%s [plugin] %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), message)
            )
    except Exception:
        pass


def _escape_markup(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _normalize(uuid):
    if uuid is None:
        return ""
    return str(uuid).replace("urn:uuid:", "").strip().lower()


def _find_terminal(uuid):
    target = _normalize(uuid)
    if not target:
        return None
    for terminal in Terminator().terminals:
        if _normalize(terminal.uuid) == target:
            return terminal
    return None


def _find_notebook(terminal):
    widget = terminal
    parent = terminal.get_parent()
    while parent is not None:
        if isinstance(parent, Gtk.Notebook):
            return parent, widget
        widget = parent
        parent = parent.get_parent()
    return None, None


class FocusService(dbus.service.Object):
    """Own notifications so action signals have a durable receiver."""

    def __init__(self, bus_name, path, notifs_iface, state=None):
        super().__init__(bus_name, path)
        self._notifs = notifs_iface
        self._state = state if state is not None else RuntimeState()
        # notification id -> (agent, session id, request id, pane uuid, kind)
        self._notif_meta = {}
        # (agent, session id) -> set(notification id)
        self._session_notifs = {}
        # (agent, session id, request id) -> set(notification id)
        self._request_notifs = {}

    @dbus.service.method(BUS_NAME, in_signature="s", out_signature="b")
    def FocusTerminal(self, uuid):
        return self._focus(uuid)

    def _focus(self, uuid):
        try:
            terminal = _find_terminal(uuid)
            if terminal is None:
                _log("focus: pane not found %s" % uuid)
                return False

            notebook, page = _find_notebook(terminal)
            if notebook is not None and page is not None:
                page_num = notebook.page_num(page)
                if page_num != -1:
                    notebook.set_current_page(page_num)

            window = terminal.get_toplevel()
            raised = self._raise_window(window)
            self._apply_focus(window, terminal)
            GLib.timeout_add(150, self._apply_focus_once, window, terminal)
            _log("focus: uuid=%s tab_ok=%s raised=%s" % (uuid, notebook is not None, raised))
            return True
        except Exception as exception:
            err("AgentNotify.FocusTerminal failed: %s" % exception)
            _log("focus: exception %s" % exception)
            return False

    @staticmethod
    def _apply_focus(window, terminal):
        try:
            if window is not None:
                window.set_focus(terminal.vte)
        except Exception:
            pass
        try:
            terminal.vte.grab_focus()
        except Exception:
            pass

    def _apply_focus_once(self, window, terminal):
        if _find_terminal(terminal.uuid) is not None:
            self._apply_focus(window, terminal)
        return False

    @staticmethod
    def _raise_window(window):
        if window is None:
            return "no-window"
        try:
            gdk_window = window.get_window()
        except Exception:
            gdk_window = None

        timestamp = 0
        is_x11 = False
        if gdk_window is not None:
            try:
                from gi.repository import GdkX11

                if isinstance(gdk_window, GdkX11.X11Window):
                    is_x11 = True
                    timestamp = GdkX11.x11_get_server_time(gdk_window)
            except Exception:
                pass
        try:
            window.present_with_time(timestamp)
        except Exception:
            try:
                window.present()
            except Exception:
                pass
        if is_x11 and gdk_window is not None:
            try:
                gdk_window.focus(timestamp or Gtk.get_current_event_time())
                return "x11-focus ts=%s" % timestamp
            except Exception as exception:
                return "x11-focus-failed %s" % exception
        return "wayland-present (no real raise possible)"

    @dbus.service.method(BUS_NAME, in_signature="", out_signature="s")
    def GetFocusedUUID(self):
        """Return the UUID of Terminator's focused pane, if there is one."""
        try:
            for terminal in Terminator().terminals:
                if terminal.vte.has_focus():
                    return str(terminal.uuid)
        except Exception as exception:
            _log("GetFocusedUUID failed: %s" % exception)
        return ""

    @dbus.service.method(BUS_NAME, in_signature="ss", out_signature="b")
    def SendKeys(self, uuid, keys):
        return self._send_keys(uuid, str(keys))

    def _send_keys(self, uuid, text):
        terminal = _find_terminal(uuid)
        if terminal is None:
            _log("SendKeys: pane not found %s" % uuid)
            return False
        vte = terminal.vte
        for signature in ("str", "str+len", "bytes", "bytes+len"):
            try:
                if signature == "str":
                    vte.feed_child(text)
                elif signature == "str+len":
                    vte.feed_child(text, len(text))
                elif signature == "bytes":
                    vte.feed_child(text.encode("utf-8"))
                else:
                    encoded = text.encode("utf-8")
                    vte.feed_child(encoded, len(encoded))
                _log("SendKeys ok via %s uuid=%s keys=%r" % (signature, uuid, text))
                return True
            except TypeError:
                continue
            except Exception as exception:
                _log("SendKeys error via %s: %s" % (signature, exception))
                return False
        _log("SendKeys: no compatible feed_child signature")
        return False

    def _track(self, agent, session_id, request_id, pane_uuid, kind, notification_id):
        self._notif_meta[notification_id] = (
            agent,
            session_id,
            request_id,
            pane_uuid,
            kind,
        )
        self._session_notifs.setdefault((agent, session_id), set()).add(notification_id)
        self._request_notifs.setdefault((agent, session_id, request_id), set()).add(
            notification_id
        )

    def _untrack(self, notification_id):
        metadata = self._notif_meta.pop(notification_id, None)
        if metadata is None:
            return
        notification_ids = self._session_notifs.get((metadata[0], metadata[1]))
        if notification_ids is not None:
            notification_ids.discard(notification_id)
            if not notification_ids:
                self._session_notifs.pop((metadata[0], metadata[1]), None)
        request_ids = self._request_notifs.get((metadata[0], metadata[1], metadata[2]))
        if request_ids is not None:
            request_ids.discard(notification_id)
            if not request_ids:
                self._request_notifs.pop((metadata[0], metadata[1], metadata[2]), None)

    @dbus.service.method(BUS_NAME, in_signature="ssssss", out_signature="u")
    def Notify(self, agent, session_id, request_id, pane_uuid, kind, payload_json):
        return dbus.UInt32(
            self.notify(agent, session_id, request_id, pane_uuid, kind, payload_json)
        )

    def notify(self, agent, session_id, request_id, pane_uuid, kind, payload_json):
        if self._notifs is None:
            _log("Notify: Notifications interface unavailable")
            return 0
        try:
            payload = json.loads(str(payload_json))
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            title = str(payload.get("title", ""))
            body = str(payload.get("body", ""))
            agent = str(agent)
            session_id = str(session_id)
            request_id = str(request_id)
            pane_uuid = str(pane_uuid)
            kind = str(kind)
            if agent not in {"claude", "codex"}:
                raise ValueError("unsupported agent")
            if agent == "codex" and kind == "permission" and (
                not session_id or not request_id
            ):
                raise ValueError("Codex permission requires session and request ids")
        except (TypeError, ValueError, json.JSONDecodeError) as exception:
            _log("Notify: invalid request: %s" % exception)
            return 0

        actions = ["default", "Focus"]
        if kind == "permission":
            actions = ["approve", "Approve", "deny", "Deny"] + actions
        hints = {"urgency": dbus.Byte(1)}
        application = "Claude Code" if agent == "claude" else "Codex"
        icon = ICON_PATH if agent == "claude" else ""
        try:
            notification_id = int(
                self._notifs.Notify(
                    application,
                    dbus.UInt32(0),
                    icon,
                    _escape_markup(title),
                    _escape_markup(body),
                    actions,
                    hints,
                    0,
                )
            )
        except Exception as exception:
            _log("Notify failed: %s" % exception)
            return 0
        self._track(agent, session_id, request_id, pane_uuid, kind, notification_id)
        _log(
            "notify id=%d agent=%s sid=%s request=%s kind=%s pane=%s"
            % (notification_id, agent, session_id, request_id, kind, pane_uuid)
        )
        return notification_id

    @dbus.service.method(BUS_NAME, in_signature="ss", out_signature="b")
    def DismissSession(self, agent, session_id):
        key = (str(agent), str(session_id))
        notification_ids = list(self._session_notifs.get(key, set()))
        for notification_id in notification_ids:
            self._close_and_untrack(notification_id)
        _log("dismiss agent=%s sid=%s closed=%d" % (key[0], key[1], len(notification_ids)))
        return True

    @dbus.service.method(BUS_NAME, in_signature="sss", out_signature="b")
    def DismissRequest(self, agent, session_id, request_id):
        """Retire exactly one request without affecting concurrent prompts."""
        key = (str(agent), str(session_id), str(request_id))
        notification_ids = list(self._request_notifs.get(key, set()))
        for notification_id in notification_ids:
            self._close_and_untrack(notification_id)
        _log(
            "dismiss request agent=%s sid=%s request=%s closed=%d"
            % (key[0], key[1], key[2], len(notification_ids))
        )
        return True

    def _close_and_untrack(self, notification_id):
        self._untrack(notification_id)
        try:
            self._notifs.CloseNotification(dbus.UInt32(notification_id))
        except Exception:
            pass

    def dismiss_pane(self, pane_uuid):
        """Clear informational notices on focus, never pending approvals.

        Pane focus has no request identity, so it can span sessions.  The safe
        invariant is to close only non-permission notifications here; callers
        with a request identity must use :meth:`DismissRequest` instead.
        """
        target = _normalize(pane_uuid)
        notification_ids = [
            notification_id
            for notification_id, metadata in self._notif_meta.items()
            if _normalize(metadata[3]) == target and metadata[4] != "permission"
        ]
        for notification_id in notification_ids:
            self._close_and_untrack(notification_id)

    def on_action(self, notification_id, action_key):
        # Some servers signal one action twice; untracking on the first action
        # gives the second delivery no work to do.
        notification_id = int(notification_id)
        metadata = self._notif_meta.get(notification_id)
        action_key = str(action_key)
        _log("ActionInvoked id=%d key=%s meta=%s" % (notification_id, action_key, metadata))
        if metadata is None:
            return
        agent, session_id, request_id, pane_uuid, kind = metadata
        if kind == "permission" and action_key in {"approve", "deny"}:
            if agent == "claude":
                self._send_keys(pane_uuid, KEY_APPROVE if action_key == "approve" else KEY_DENY)
            else:
                self._state.write_decision(
                    agent,
                    session_id,
                    request_id,
                    "allow" if action_key == "approve" else "deny",
                )
        else:
            self._focus(pane_uuid)
        self._close_and_untrack(notification_id)

    def on_closed(self, notification_id, _reason):
        notification_id = int(notification_id)
        metadata = self._notif_meta.get(notification_id)
        try:
            if (
                metadata is not None
                and metadata[0] == "codex"
                and metadata[4] == "permission"
            ):
                self._state.write_request_closed(metadata[0], metadata[1], metadata[2])
        except (OSError, ValueError) as exception:
            _log("request closure state failed: %s" % exception)
        finally:
            self._untrack(notification_id)


class AgentNotify(Plugin):
    """Expose focus, key injection, and notifications on the session bus."""

    capabilities = ["agent_notify"]

    def __init__(self):
        Plugin.__init__(self)
        self.bus_name = None
        self.service = None
        self._focus_handlers = {}
        self._reconcile_id = None
        self._notification_signal_matches = []
        self._notification_bus = None
        bus = self._session_bus()
        notifications = self._notifications_interface(bus)
        if bus is not None:
            try:
                self.bus_name = dbus.service.BusName(BUS_NAME, bus)
                self.service = FocusService(self.bus_name, OBJ_PATH, notifications)
                _log("plugin registered as %s" % BUS_NAME)
            except Exception as exception:
                err("AgentNotify plugin failed to register: %s" % exception)
        if bus is not None and self.service is not None:
            try:
                self._notification_bus = bus
                self._notification_signal_matches.append(
                    bus.add_signal_receiver(
                        self._on_action_signal,
                        signal_name="ActionInvoked",
                        dbus_interface=NOTIFS_BUS,
                        path=NOTIFS_PATH,
                        bus_name=NOTIFS_BUS,
                        sender_keyword="sender",
                    )
                )
                self._notification_signal_matches.append(
                    bus.add_signal_receiver(
                        self._on_closed_signal,
                        signal_name="NotificationClosed",
                        dbus_interface=NOTIFS_BUS,
                        path=NOTIFS_PATH,
                        bus_name=NOTIFS_BUS,
                        sender_keyword="sender",
                    )
                )
            except Exception as exception:
                err("AgentNotify: cannot subscribe to notification signals: %s" % exception)
                self._remove_notification_signal_matches()
        self._reconcile_id = GLib.timeout_add_seconds(2, self._reconcile_handlers)
        self._reconcile_handlers()

    @staticmethod
    def _session_bus():
        try:
            return dbus.SessionBus()
        except Exception as exception:
            err("AgentNotify: cannot get session bus: %s" % exception)
            return None

    @staticmethod
    def _notifications_interface(bus):
        if bus is None:
            return None
        try:
            return dbus.Interface(bus.get_object(NOTIFS_BUS, NOTIFS_PATH), NOTIFS_BUS)
        except Exception as exception:
            err("AgentNotify: cannot bind Notifications interface: %s" % exception)
            return None

    def _reconcile_handlers(self):
        try:
            current_vtes = set()
            for terminal in Terminator().terminals:
                vte = getattr(terminal, "vte", None)
                if vte is None:
                    continue
                current_vtes.add(vte)
                if vte not in self._focus_handlers:
                    self._focus_handlers[vte] = vte.connect("focus-in-event", self._on_focus_in)
            for vte in list(self._focus_handlers):
                if vte not in current_vtes:
                    del self._focus_handlers[vte]
        except Exception as exception:
            err("AgentNotify._reconcile_handlers failed: %s" % exception)
        return True

    def _on_focus_in(self, vte, _event):
        if self.service is None:
            return False
        try:
            for terminal in Terminator().terminals:
                if getattr(terminal, "vte", None) is vte:
                    self.service.dismiss_pane(terminal.uuid)
                    break
        except Exception as exception:
            err("AgentNotify._on_focus_in cleanup failed: %s" % exception)
        return False

    def _trusted_notification_sender(self, sender):
        """Check the sender against the notification service's current owner."""
        if not sender or self._notification_bus is None:
            return False
        try:
            return str(sender) == str(self._notification_bus.get_name_owner(NOTIFS_BUS))
        except Exception as exception:
            _log("notification sender validation failed: %s" % exception)
            return False

    def _on_action_signal(self, notification_id, action_key, sender=None):
        if not self._trusted_notification_sender(sender):
            _log("ignored ActionInvoked from untrusted sender %s" % sender)
            return
        if self.service is not None:
            self.service.on_action(notification_id, action_key)

    def _on_closed_signal(self, notification_id, reason, sender=None):
        if not self._trusted_notification_sender(sender):
            _log("ignored NotificationClosed from untrusted sender %s" % sender)
            return
        if self.service is not None:
            self.service.on_closed(notification_id, reason)

    def _remove_notification_signal_matches(self):
        for match in self._notification_signal_matches:
            try:
                match.remove()
            except Exception:
                pass
        self._notification_signal_matches = []
        self._notification_bus = None

    def unload(self):
        self._remove_notification_signal_matches()
        if self._reconcile_id:
            try:
                GLib.source_remove(self._reconcile_id)
            except Exception:
                pass
            self._reconcile_id = None
        for vte, handler_id in list(self._focus_handlers.items()):
            try:
                vte.disconnect(handler_id)
            except Exception:
                pass
        self._focus_handlers.clear()
        try:
            if self.service is not None:
                self.service.remove_from_connection()
        except Exception as exception:
            err("AgentNotify unload error: %s" % exception)
