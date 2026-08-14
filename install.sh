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

missing=()
for dependency in jq gdbus; do
  command -v "$dependency" >/dev/null 2>&1 || missing+=("$dependency")
done
[ "${#missing[@]}" -eq 0 ] || warn "Missing optional runtime dependencies: ${missing[*]}"
command -v notify-send >/dev/null 2>&1 || warn "notify-send not found; fallback notifications are unavailable."
command -v python3 >/dev/null 2>&1 || { warn "python3 is required."; exit 1; }

umask 077
mkdir -p "$INSTALL_ROOT/adapters" "$INSTALL_ROOT/core" "$STATE_DIR"
chmod 700 "$STATE_DIR"

say "Installing shared Terminator plugin"
mkdir -p "$TERMINATOR_ROOT/plugins" "$TERMINATOR_ROOT/core" "$TERMINATOR_ROOT/assets"
install -m 0644 "$SRC_DIR/terminator-plugin/agent_notify.py" \
  "$TERMINATOR_ROOT/plugins/agent_notify.py"
install -m 0644 "$SRC_DIR/core/__init__.py" "$TERMINATOR_ROOT/core/__init__.py"
install -m 0644 "$SRC_DIR/core/runtime_state.py" "$TERMINATOR_ROOT/core/runtime_state.py"
for asset in "$SRC_DIR"/assets/*; do
  [ -f "$asset" ] && install -m 0644 "$asset" "$TERMINATOR_ROOT/assets/"
done

for agent in "${agents[@]}"; do
  say "Installing $agent adapter"
  rm -rf "$INSTALL_ROOT/adapters/$agent"
  mkdir -p "$INSTALL_ROOT/adapters/$agent"
  cp -a "$SRC_DIR/adapters/$agent/." "$INSTALL_ROOT/adapters/$agent/"
done
install -m 0644 "$SRC_DIR/core/__init__.py" "$INSTALL_ROOT/core/__init__.py"
install -m 0644 "$SRC_DIR/core/runtime_state.py" "$INSTALL_ROOT/core/runtime_state.py"

if selected claude; then
  mkdir -p "$HOME/.claude/assets"
  for asset in "$SRC_DIR"/assets/*; do
    [ -f "$asset" ] && install -m 0644 "$asset" "$HOME/.claude/assets/"
  done
  python3 "$SRC_DIR/adapters/claude/configure.py" install \
    --config "$HOME/.claude/settings.json" --install-root "$INSTALL_ROOT"
fi
if selected codex; then
  python3 "$SRC_DIR/adapters/codex/configure.py" install \
    --config "$HOME/.codex/hooks.json" --install-root "$INSTALL_ROOT"
fi

mkdir -p "$SYSTEMD_USER"
for agent in "${agents[@]}"; do
  sed "s|@INSTALL_ROOT@|$INSTALL_ROOT|g" \
    "$SRC_DIR/systemd/$agent-limit-poller.service.in" \
    > "$SYSTEMD_USER/$agent-limit-poller.service"
  install -m 0644 "$SRC_DIR/systemd/$agent-limit-poller.timer" \
    "$SYSTEMD_USER/$agent-limit-poller.timer"
done
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || warn "systemd user daemon reload failed."
  if selected claude; then
    systemctl --user enable --now claude-limit-poller.timer || \
      warn "Could not enable the Claude usage-limit timer."
  fi
  if selected codex && [ "${CODEX_AUTORESUME:-0}" = 1 ]; then
    systemctl --user enable --now codex-limit-poller.timer || \
      warn "Could not enable the experimental Codex usage-limit timer."
  fi
else
  warn "systemctl not found; poller units were installed but not enabled."
fi

state_tmp=$(mktemp "$STATE_DIR/.installed-adapters.XXXXXX")
{
  [ ! -f "$STATE_FILE" ] || cat "$STATE_FILE"
  printf '%s\n' "${agents[@]}"
} | sort -u > "$state_tmp"
chmod 600 "$state_tmp"
mv "$state_tmp" "$STATE_FILE"

# Keep the established opt-in behavior for reliable cross-window focus on Wayland.
if [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
  do_xwayland=${FORCE_XWAYLAND:-}
  if [ -z "$do_xwayland" ] && [ -t 0 ]; then
    warn "Cross-window focus on Wayland requires Terminator under XWayland."
    read -r -p "Configure Terminator to launch under XWayland? [y/N] " answer
    case "$answer" in y|Y) do_xwayland=1 ;; esac
  fi
  if [ "$do_xwayland" = 1 ]; then
    "$SRC_DIR/scripts/force-xwayland.sh"
  else
    warn "Skipped XWayland setup; run scripts/force-xwayland.sh later to enable it."
  fi
fi

if selected codex; then
  echo "Codex hooks installed. Start Codex and review and trust the new hooks when prompted."
fi
say "Installation complete. Restart Terminator and enable AgentNotify in Preferences if needed."
