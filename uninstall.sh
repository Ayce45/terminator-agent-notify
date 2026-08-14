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
ENV_FILE="$STATE_DIR/environment"
MANAGED_FILES="$STATE_DIR/managed-files.json"
XWAYLAND_OWNED="$STATE_DIR/xwayland-owned"
XWAYLAND_DESKTOP_SNAPSHOT="$STATE_DIR/xwayland-owned.desktop"
TERMINATOR_ROOT="$XDG_CONFIG_HOME/terminator"
SYSTEMD_USER="$XDG_CONFIG_HOME/systemd/user"
UNIT_PREFIX="terminator-agent-notify"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
cleanup_bytecode_tree() {
  local tree=$1
  [ -d "$tree" ] || return 0
  find "$tree" -depth -type f -path '*/__pycache__/*.pyc' -delete 2>/dev/null || true
  find "$tree" -depth -type d -name __pycache__ -empty -delete 2>/dev/null || true
  find "$tree" -depth -type d -empty -delete 2>/dev/null || true
}
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
  if [ -f "$ENV_FILE" ]; then
    python3 "$SRC_DIR/scripts/persist-environment.py" disable \
      --file "$ENV_FILE" "$agent"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop "$UNIT_PREFIX-$agent-autoresume-*" 2>/dev/null || true
    systemctl --user stop "$agent-autoresume-*" 2>/dev/null || true
    systemctl --user disable --now "$UNIT_PREFIX-$agent-limit-poller.timer" 2>/dev/null || true
  fi
  if [ -f "$MANAGED_FILES" ]; then
    python3 "$SRC_DIR/scripts/manage-install-files.py" remove \
      --source "$SRC_DIR" --manifest "$MANAGED_FILES" \
      --environment-file "$ENV_FILE" --terminator-root "$TERMINATOR_ROOT" \
      --install-root "$INSTALL_ROOT" --systemd-user "$SYSTEMD_USER" \
      --home "$HOME" "$agent"
  fi
  cleanup_bytecode_tree "$INSTALL_ROOT/adapters/$agent"
  if [ "$agent" = codex ]; then
    rm -f "$INSTALL_ROOT/adapters/__pycache__/__init__.pyc" \
      "$INSTALL_ROOT/adapters/__pycache__"/__init__.*.pyc
    rmdir "$INSTALL_ROOT/adapters/__pycache__" 2>/dev/null || true
  fi
done
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload 2>/dev/null || true
fi

reconcile_state
if [ "${#active_agents[@]}" -eq 0 ]; then
  if [ -f "$MANAGED_FILES" ]; then
    python3 "$SRC_DIR/scripts/manage-install-files.py" remove --shared \
      --source "$SRC_DIR" --manifest "$MANAGED_FILES" \
      --environment-file "$ENV_FILE" --terminator-root "$TERMINATOR_ROOT" \
      --install-root "$INSTALL_ROOT" --systemd-user "$SYSTEMD_USER" \
      --home "$HOME"
  fi
  rm -f "$TERMINATOR_ROOT/plugins/__pycache__/agent_notify.pyc" \
    "$TERMINATOR_ROOT/plugins/__pycache__"/agent_notify.*.pyc
  rmdir "$TERMINATOR_ROOT/plugins/__pycache__" 2>/dev/null || true
  cleanup_bytecode_tree "$TERMINATOR_ROOT/terminator_agent_notify_core"
  cleanup_bytecode_tree "$INSTALL_ROOT/terminator_agent_notify_core"
  cleanup_bytecode_tree "$INSTALL_ROOT"
  rm -f "$ENV_FILE"
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
