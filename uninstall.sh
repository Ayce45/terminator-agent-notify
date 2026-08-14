#!/usr/bin/env bash
# Selectively remove Claude, Codex, or both terminator-agent-notify adapters.
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
XWAYLAND_OWNED="$STATE_DIR/xwayland-owned"
XWAYLAND_DESKTOP_SNAPSHOT="$STATE_DIR/xwayland-owned.desktop"
TERMINATOR_ROOT="$XDG_CONFIG_HOME/terminator"
SYSTEMD_USER="$XDG_CONFIG_HOME/systemd/user"

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

for agent in "${agents[@]}"; do
  if [ "$agent" = claude ]; then
    python3 "$SRC_DIR/adapters/claude/configure.py" uninstall \
      --config "$HOME/.claude/settings.json"
  else
    python3 "$SRC_DIR/adapters/codex/configure.py" uninstall \
      --config "$HOME/.codex/hooks.json"
  fi
  rm -rf "$INSTALL_ROOT/adapters/$agent"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now "$agent-limit-poller.timer" 2>/dev/null || true
  fi
  rm -f "$SYSTEMD_USER/$agent-limit-poller.service" \
    "$SYSTEMD_USER/$agent-limit-poller.timer"
done
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload 2>/dev/null || true
fi

reconcile_state
if [ "${#active_agents[@]}" -eq 0 ]; then
  rm -f "$TERMINATOR_ROOT/plugins/agent_notify.py" \
    "$TERMINATOR_ROOT/core/__init__.py" \
    "$TERMINATOR_ROOT/core/runtime_state.py" \
    "$TERMINATOR_ROOT/assets/claude.png" \
    "$TERMINATOR_ROOT/assets/claude.svg"
  rm -f "$TERMINATOR_ROOT/plugins/__pycache__/agent_notify.pyc" \
    "$TERMINATOR_ROOT/plugins/__pycache__"/agent_notify.*.pyc
  rm -f "$TERMINATOR_ROOT/core/__pycache__/__init__.pyc" \
    "$TERMINATOR_ROOT/core/__pycache__"/__init__.*.pyc \
    "$TERMINATOR_ROOT/core/__pycache__/runtime_state.pyc" \
    "$TERMINATOR_ROOT/core/__pycache__"/runtime_state.*.pyc
  rmdir "$TERMINATOR_ROOT/plugins/__pycache__" 2>/dev/null || true
  rmdir "$TERMINATOR_ROOT/core/__pycache__" 2>/dev/null || true
  rm -rf "$INSTALL_ROOT/core"
  if [ -f "$XWAYLAND_OWNED" ] && [ -x "$SRC_DIR/scripts/force-xwayland.sh" ]; then
    if "$SRC_DIR/scripts/force-xwayland.sh" --undo-owned \
        "$XWAYLAND_OWNED" "$XWAYLAND_DESKTOP_SNAPSHOT"; then
      rm -f "$XWAYLAND_OWNED" "$XWAYLAND_DESKTOP_SNAPSHOT"
    else
      warn "Could not fully undo owned XWayland setup; ownership state retained."
    fi
  fi
fi

say "Uninstallation complete. Restart Terminator to unload removed components."
