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
generation=""
config=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --session) session="$2"; shift 2 ;;
    --pane) pane="$2"; shift 2 ;;
    --message) message="$2"; shift 2 ;;
    --notify) notify=1; shift ;;
    --generation) generation="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

generation_guard=0
if [ "$notify" -eq 1 ] || [ -n "$generation" ] || [ -n "$config" ]; then
  [ -n "$generation" ] && [ -n "$config" ] || exit 0
  generation_guard=1
fi
generation_current() {
  python3 - "$root" "$config" "$generation" <<'PY' || exit 0
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.autoresume_guard import generation_is_current

raise SystemExit(0 if generation_is_current("codex", sys.argv[2], sys.argv[3]) else 1)
PY
}
if [ "$generation_guard" -eq 1 ]; then
  generation_current || exit 0
fi

notifications_enabled="${TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS:-1}"
notification_expiry_ms="${TERMINATOR_AGENT_NOTIFY_EXPIRY_MS:-}"
if [ "$generation_guard" -eq 1 ]; then
  readarray -d '' -t persisted < <(python3 - "$root" "$config" \
      TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS TERMINATOR_AGENT_NOTIFY_EXPIRY_MS <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.autoresume_guard import read_persisted_environment

values = read_persisted_environment(sys.argv[2])
for key in sys.argv[3:]:
    present = "1" if key in values else "0"
    sys.stdout.buffer.write(present.encode() + b"\0")
    sys.stdout.buffer.write(values.get(key, "").encode() + b"\0")
PY
  )
  [ "${persisted[0]:-0}" = 1 ] && notifications_enabled="${persisted[1]-}"
  [ "${persisted[2]:-0}" = 1 ] && notification_expiry_ms="${persisted[3]-}"
fi

if [ -z "$pane" ] && [ -n "$session" ]; then
  pane=$(python3 - "$root" "$session" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.runtime_state import RuntimeState

print(RuntimeState().read_pane("codex", sys.argv[2]) or "")
PY
)
fi
[ -n "$pane" ] || { echo "could not resolve a pane uuid" >&2; exit 1; }

if [ "$generation_guard" -eq 1 ]; then
  generation_current || exit 0
fi
send_reply=$(gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
  --method "${BUS}.SendKeys" "$pane" "${message}"$'\r')
[ "$send_reply" = "(true,)" ] || { echo "SendKeys was not acknowledged" >&2; exit 1; }

if [ "$notify" -eq 1 ] && [ "$notifications_enabled" != "0" ]; then
  generation_current || exit 0
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
    notify_args=(--app-name="Codex")
    if [ -n "$notification_expiry_ms" ]; then
      notify_args+=(--expire-time="$notification_expiry_ms")
    fi
    command -v notify-send >/dev/null 2>&1 && \
      notify-send "${notify_args[@]}" "Codex — relancé automatiquement" \
        "« ${message} » envoyé après la fin de la limite." >/dev/null 2>&1 || true
  fi
fi
