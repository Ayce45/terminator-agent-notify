#!/usr/bin/env bash
# Type a configured Codex resume message into its currently mapped Terminator pane.
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
notify=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --session) session="$2"; shift 2 ;;
    --pane) pane="$2"; shift 2 ;;
    --message) message="$2"; shift 2 ;;
    --notify) notify=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$pane" ] && [ -n "$session" ]; then
  pane=$(python3 - "$root" "$session" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from core.runtime_state import RuntimeState

print(RuntimeState().read_pane("codex", sys.argv[2]) or "")
PY
)
fi
[ -n "$pane" ] || { echo "could not resolve a pane uuid" >&2; exit 1; }

send_reply=$(gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
  --method "${BUS}.SendKeys" "$pane" "${message}"$'\r')
[ "$send_reply" = "(true,)" ] || { echo "SendKeys was not acknowledged" >&2; exit 1; }

if [ "$notify" -eq 1 ]; then
  payload=$(python3 - "$message" <<'PY'
import json
import sys

print(json.dumps({"title": "Codex — relancé automatiquement", "body": f"« {sys.argv[1]} » envoyé après la fin de la limite."}))
PY
)
  notify_reply=$(gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
      --method "${BUS}.Notify" "codex" "$session" "" "$pane" "waiting" "$payload" \
      2>/dev/null || true)
  if ! [[ "$notify_reply" =~ ^\(uint32[[:space:]]+[1-9][0-9]*,\)$ ]]; then
    command -v notify-send >/dev/null 2>&1 && \
      notify-send --app-name="Codex" "Codex — relancé automatiquement" \
        "« ${message} » envoyé après la fin de la limite." >/dev/null 2>&1 || true
  fi
fi
