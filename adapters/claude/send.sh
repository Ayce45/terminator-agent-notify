#!/usr/bin/env bash
# Type a Claude Code message into its mapped Terminator pane.
set -euo pipefail

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
: "${DBUS_SESSION_BUS_ADDRESS:=unix:path=${XDG_RUNTIME_DIR}/bus}"
export DBUS_SESSION_BUS_ADDRESS

message="continue"
session=""
pane=""
focused=0
enter=1
notify=0
resume=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --session) session="$2"; shift 2 ;;
    --pane) pane="$2"; shift 2 ;;
    --focused) focused=1; shift ;;
    --message) message="$2"; shift 2 ;;
    --no-enter) enter=0; shift ;;
    --notify) notify=1; shift ;;
    --resume) resume=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$pane" ] && [ -n "$session" ]; then
  pane=$(python3 - "$root" "$session" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from core.runtime_state import RuntimeState

print(RuntimeState().read_pane("claude", sys.argv[2]) or "")
PY
)
fi
if [ -z "$pane" ] && [ "$focused" -eq 1 ]; then
  pane=$(gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
    --method "${BUS}.GetFocusedUUID" 2>/dev/null \
    | grep -oiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
fi
[ -n "$pane" ] || { echo "could not resolve a pane uuid" >&2; exit 1; }

send() {
  gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
    --method "${BUS}.SendKeys" "$pane" "$2" >/dev/null
}

if [ "$resume" -eq 1 ]; then
  # Do not collapse this sequence: the first Enter dismisses a rate-limit dialog.
  send dismiss-dialog $'\r'
  sleep 2
  send message "$message"
  sleep 1
  send enter $'\r'
  sleep 1
  send enter-safety $'\r' || true
else
  keys="$message"
  [ "$enter" -eq 1 ] && keys+=$'\r'
  send single "$keys"
fi

if [ "$notify" -eq 1 ]; then
  payload=$(python3 - "$message" <<'PY'
import json
import sys

print(json.dumps({"title": "Claude Code — relancé automatiquement", "body": f"« {sys.argv[1]} » envoyé après la fin de la limite."}))
PY
)
  if ! gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
      --method "${BUS}.Notify" "claude" "$session" "" "$pane" "waiting" "$payload" \
      >/dev/null 2>&1; then
    command -v notify-send >/dev/null 2>&1 && \
      notify-send --app-name="Claude Code" --icon="${HOME}/.claude/assets/claude.png" \
        "Claude Code — relancé automatiquement" \
        "« ${message} » envoyé après la fin de la limite." >/dev/null 2>&1 || true
  fi
fi
