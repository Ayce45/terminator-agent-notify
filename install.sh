#!/usr/bin/env bash
# Install Claude, Codex, or both adapters without replacing unrelated hooks.
set -euo pipefail

usage() { echo "Usage: $0 {claude|codex|both}" >&2; }
case "${1:-}" in
  claude) agents=(claude) ;;
  codex) agents=(codex) ;;
  both) agents=(claude codex) ;;
  *) usage; exit 2 ;;
esac
[ "$#" -eq 1 ] || { usage; exit 2; }

SRC_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
XDG_DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
XDG_STATE_HOME=${XDG_STATE_HOME:-"$HOME/.local/state"}
INSTALL_ROOT="$XDG_DATA_HOME/terminator-agent-notify"
STATE_DIR="$XDG_STATE_HOME/terminator-agent-notify"
STATE_FILE="$STATE_DIR/installed-adapters"
ENV_FILE="$STATE_DIR/environment"
MANAGED_FILES="$STATE_DIR/managed-files.json"
XWAYLAND_OWNED="$STATE_DIR/xwayland-owned"
XWAYLAND_DESKTOP_SNAPSHOT="$STATE_DIR/xwayland-owned.desktop"
TERMINATOR_ROOT="$XDG_CONFIG_HOME/terminator"
SYSTEMD_USER="$XDG_CONFIG_HOME/systemd/user"
UNIT_PREFIX="terminator-agent-notify"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
selected() {
  local wanted=$1 agent
  for agent in "${agents[@]}"; do
    [ "$agent" = "$wanted" ] && return 0
  done
  return 1
}
config_has_owned_hook() {
  local agent=$1 config
  case "$agent" in
    claude) config="$HOME/.claude/settings.json" ;;
    codex) config="$HOME/.codex/hooks.json" ;;
  esac
  python3 - "$config" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(1)
marker = "terminator-agent-notify:"
hooks = value.get("hooks", {}) if isinstance(value, dict) else {}
found = any(
    isinstance(entry, dict) and str(entry.get("description", "")).startswith(marker)
    for entries in hooks.values()
    if isinstance(entries, list)
    for entry in entries
)
raise SystemExit(0 if found else 1)
PY
}
reconcile_state() {
  local agent state_tmp
  active_agents=()
  if [ -f "$STATE_FILE" ]; then
    while IFS= read -r agent || [ -n "$agent" ]; do
      case "$agent" in
        ""|claude|codex) ;;
        *) warn "Ignoring invalid installed-adapters state: $agent" ;;
      esac
    done < "$STATE_FILE"
  fi
  for agent in claude codex; do
    if [ -d "$INSTALL_ROOT/adapters/$agent" ] || config_has_owned_hook "$agent"; then
      active_agents+=("$agent")
    fi
  done
  rm -f "$STATE_DIR"/.installed-adapters.*
  if [ "${#active_agents[@]}" -eq 0 ]; then
    rm -f "$STATE_FILE"
    return
  fi
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  state_tmp=$(mktemp "$STATE_DIR/.installed-adapters.XXXXXX")
  printf '%s\n' "${active_agents[@]}" > "$state_tmp"
  chmod 600 "$state_tmp"
  mv "$state_tmp" "$STATE_FILE"
}
xwayland_already_configured() {
  local desktop="$HOME/.local/share/applications/terminator.desktop"
  local list path command_value
  if [ -f "$desktop" ] && \
      grep -qE '^Exec=env[[:space:]]+GDK_BACKEND=x11([[:space:]]|$)' "$desktop"; then
    return 0
  fi
  command -v gsettings >/dev/null 2>&1 || return 1
  list=$(gsettings get org.gnome.settings-daemon.plugins.media-keys \
    custom-keybindings 2>/dev/null) || return 1
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    command_value=$(gsettings get \
      "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$path" \
      command 2>/dev/null) || continue
    command_value=${command_value#\'}
    command_value=${command_value%\'}
    case "$command_value" in
      "env GDK_BACKEND=x11 "*)
        command_value=${command_value#"env GDK_BACKEND=x11 "}
        case "$command_value" in
          terminator|terminator\ *|/usr/bin/terminator|/usr/bin/terminator\ *|*/terminator|*/terminator\ *)
            return 0
            ;;
        esac
        ;;
    esac
  done < <(printf '%s\n' "$list" | grep -oE "/[^']*custom-keybindings/[^']*/" || true)
  return 1
}
prepare_xwayland_ownership() {
  local ownership_tmp list path command_value owns_desktop=0
  ownership_tmp=$(mktemp "$STATE_DIR/.xwayland-owned.XXXXXX")
  if command -v gsettings >/dev/null 2>&1; then
    list=$(gsettings get org.gnome.settings-daemon.plugins.media-keys \
      custom-keybindings 2>/dev/null) || list=
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      command_value=$(gsettings get \
        "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$path" \
        command 2>/dev/null) || continue
      command_value=${command_value#\'}
      command_value=${command_value%\'}
      case "$command_value" in
        terminator|/usr/bin/terminator|*/terminator)
          printf '%s\0%s\0' "$path" "$command_value" >> "$ownership_tmp"
          ;;
      esac
    done < <(printf '%s\n' "$list" | grep -oE "/[^']*custom-keybindings/[^']*/" || true)
  fi
  if [ -f /usr/share/applications/terminator.desktop ] && \
      [ ! -e "$HOME/.local/share/applications/terminator.desktop" ]; then
    owns_desktop=1
  fi
  if [ -s "$ownership_tmp" ] || [ "$owns_desktop" = 1 ]; then
    chmod 600 "$ownership_tmp"
    mv "$ownership_tmp" "$XWAYLAND_OWNED"
  else
    rm -f "$ownership_tmp"
  fi
}
snapshot_xwayland_desktop() {
  local desktop="$HOME/.local/share/applications/terminator.desktop"
  local snapshot_tmp
  [ -f "$XWAYLAND_OWNED" ] && [ -f "$desktop" ] || return 0
  snapshot_tmp=$(mktemp "$STATE_DIR/.xwayland-owned.desktop.XXXXXX")
  install -m 0600 "$desktop" "$snapshot_tmp"
  mv "$snapshot_tmp" "$XWAYLAND_DESKTOP_SNAPSHOT"
}

missing=()
for dependency in jq gdbus; do
  command -v "$dependency" >/dev/null 2>&1 || missing+=("$dependency")
done
[ "${#missing[@]}" -eq 0 ] || warn "Missing optional runtime dependencies: ${missing[*]}"
command -v notify-send >/dev/null 2>&1 || warn "notify-send not found; fallback notifications are unavailable."
command -v python3 >/dev/null 2>&1 || { warn "python3 is required."; exit 1; }

umask 077
python3 "$SRC_DIR/scripts/manage-install-files.py" install \
  --source "$SRC_DIR" --manifest "$MANAGED_FILES" \
  --environment-file "$ENV_FILE" --terminator-root "$TERMINATOR_ROOT" \
  --install-root "$INSTALL_ROOT" --systemd-user "$SYSTEMD_USER" \
  --home "$HOME" "${agents[@]}"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"
python3 "$SRC_DIR/scripts/persist-environment.py" install \
  --file "$ENV_FILE" "${agents[@]}"

for agent in "${agents[@]}"; do
  say "Installing $agent adapter"
done

if selected claude; then
  python3 "$SRC_DIR/adapters/claude/configure.py" install \
    --config "$HOME/.claude/settings.json" --install-root "$INSTALL_ROOT"
fi
if selected codex; then
  python3 "$SRC_DIR/adapters/codex/configure.py" install \
    --config "$HOME/.codex/hooks.json" --install-root "$INSTALL_ROOT"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || warn "systemd user daemon reload failed."
  if selected claude; then
    systemctl --user stop "$UNIT_PREFIX-claude-autoresume-*" 2>/dev/null || true
    if grep -qx 'CLAUDE_AUTORESUME=1' "$ENV_FILE"; then
      systemctl --user enable --now "$UNIT_PREFIX-claude-limit-poller.timer" || \
        warn "Could not enable the Claude usage-limit timer."
    else
      systemctl --user disable --now "$UNIT_PREFIX-claude-limit-poller.timer" 2>/dev/null || \
        warn "Could not disable the Claude usage-limit timer."
    fi
  fi
  if selected codex; then
    systemctl --user stop "$UNIT_PREFIX-codex-autoresume-*" 2>/dev/null || true
    if [ ! -f "$INSTALL_ROOT/adapters/codex/autoresume.py" ]; then
      systemctl --user disable --now "$UNIT_PREFIX-codex-limit-poller.timer" 2>/dev/null || true
      if [ "${CODEX_AUTORESUME:-0}" = 1 ]; then
        warn "Codex auto-resume adapter is unavailable; leaving its timer disabled."
      fi
    elif grep -qx 'CODEX_AUTORESUME=1' "$ENV_FILE"; then
      systemctl --user enable --now "$UNIT_PREFIX-codex-limit-poller.timer" || \
        warn "Could not enable the experimental Codex usage-limit timer."
    else
      systemctl --user disable --now "$UNIT_PREFIX-codex-limit-poller.timer" 2>/dev/null || \
        warn "Could not disable the experimental Codex usage-limit timer."
    fi
  fi
else
  warn "systemctl not found; poller units were installed but not enabled."
fi

reconcile_state

# Keep the established opt-in behavior for reliable cross-window focus on Wayland.
if [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
  do_xwayland=${FORCE_XWAYLAND:-}
  if [ -z "$do_xwayland" ] && [ -t 0 ]; then
    warn "Cross-window focus on Wayland requires Terminator under XWayland."
    read -r -p "Configure Terminator to launch under XWayland? [y/N] " answer
    case "$answer" in y|Y) do_xwayland=1 ;; esac
  fi
  if [ "$do_xwayland" = 1 ]; then
    if [ -f "$XWAYLAND_OWNED" ]; then
      say "XWayland setup is already owned by this installation."
    elif [ -e "$HOME/.local/share/applications/terminator.desktop" ]; then
      warn "Existing user Terminator desktop override detected; leaving it unchanged."
    elif xwayland_already_configured; then
      warn "Existing user XWayland setup detected; leaving it unchanged."
    else
      prepare_xwayland_ownership
      if "$SRC_DIR/scripts/force-xwayland.sh"; then
        snapshot_xwayland_desktop
      fi
    fi
  else
    warn "Skipped XWayland setup; run scripts/force-xwayland.sh later to enable it."
  fi
fi

if selected codex; then
  echo "Codex hooks installed. Start Codex and review and trust the new hooks when prompted."
fi
say "Installation complete. Restart Terminator and enable AgentNotify in Preferences if needed."
