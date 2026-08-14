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
TERMINATOR_ROOT="$XDG_CONFIG_HOME/terminator"
SYSTEMD_USER="$XDG_CONFIG_HOME/systemd/user"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
selected() {
  local wanted=$1 agent
  for agent in "${agents[@]}"; do
    [ "$agent" = "$wanted" ] && return 0
  done
  return 1
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

remaining=()
if [ -f "$STATE_FILE" ]; then
  while IFS= read -r agent; do
    [ -n "$agent" ] || continue
    selected "$agent" || remaining+=("$agent")
  done < "$STATE_FILE"
fi
if [ "${#remaining[@]}" -gt 0 ]; then
  state_tmp=$(mktemp "$STATE_DIR/.installed-adapters.XXXXXX")
  printf '%s\n' "${remaining[@]}" | sort -u > "$state_tmp"
  chmod 600 "$state_tmp"
  mv "$state_tmp" "$STATE_FILE"
else
  rm -f "$STATE_FILE"
  rm -f "$TERMINATOR_ROOT/plugins/agent_notify.py" \
    "$TERMINATOR_ROOT/core/__init__.py" \
    "$TERMINATOR_ROOT/core/runtime_state.py" \
    "$TERMINATOR_ROOT/assets/claude.png" \
    "$TERMINATOR_ROOT/assets/claude.svg"
  rm -rf "$TERMINATOR_ROOT/plugins/__pycache__" "$INSTALL_ROOT/core"
  if [ -x "$SRC_DIR/scripts/force-xwayland.sh" ]; then
    "$SRC_DIR/scripts/force-xwayland.sh" --undo
  fi
fi

say "Uninstallation complete. Restart Terminator to unload removed components."
