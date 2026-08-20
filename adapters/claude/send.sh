#!/usr/bin/env bash
# Type a Claude Code message into its mapped Terminator pane.
set -euo pipefail

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
COMMAND_TIMEOUT_SECONDS="${TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS:-5}"
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
generation=""
config=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --session) session="$2"; shift 2 ;;
    --pane) pane="$2"; shift 2 ;;
    --focused) focused=1; shift ;;
    --message) message="$2"; shift 2 ;;
    --no-enter) enter=0; shift ;;
    --notify) notify=1; shift ;;
    --resume) resume=1; shift ;;
    --generation) generation="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

generation_guard=0
if [ "$resume" -eq 1 ] || [ -n "$generation" ] || [ -n "$config" ]; then
  [ -n "$generation" ] && [ -n "$config" ] || exit 0
  generation_guard=1
fi
generation_current() {
  python3 - "$root" "$config" "$generation" <<'PY' || exit 0
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.autoresume_guard import generation_is_current

raise SystemExit(0 if generation_is_current("claude", sys.argv[2], sys.argv[3]) else 1)
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

print(RuntimeState().read_pane("claude", sys.argv[2]) or "")
PY
)
fi
if [ -z "$pane" ] && [ "$focused" -eq 1 ]; then
  pane=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
    --method "${BUS}.GetFocusedUUID" 2>/dev/null \
    | grep -oiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
fi
[ -n "$pane" ] || { echo "could not resolve a pane uuid" >&2; exit 1; }

send() {
  if [ "$generation_guard" -eq 1 ]; then
    generation_current || return 1
  fi
  send_reply=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
    --method "${BUS}.SendKeys" "$pane" "$2")
  [ "$send_reply" = "(true,)" ] || {
    echo "SendKeys was not acknowledged" >&2
    return 1
  }
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

if [ "$notify" -eq 1 ] && [ "$notifications_enabled" != "0" ]; then
  if [ "$generation_guard" -eq 1 ]; then
    generation_current || exit 0
  fi
  payload=$(python3 - "$message" <<'PY'
import json
import sys

print(json.dumps({"title": "Claude Code — resumed automatically", "body": f"\"{sys.argv[1]}\" sent after the usage limit reset."}))
PY
)
  notify_reply=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
      --method "${BUS}.Notify" "claude" "$session" "" "$pane" "waiting" "$payload" \
      2>/dev/null || true)
  if ! [[ "$notify_reply" =~ ^\(uint32[[:space:]]+[1-9][0-9]*,\)$ ]]; then
    notify_args=(--app-name="Claude Code" --icon="${HOME}/.claude/assets/claude.png")
    if [ -n "$notification_expiry_ms" ]; then
      notify_args+=(--expire-time="$notification_expiry_ms")
    fi
    command -v notify-send >/dev/null 2>&1 && \
      timeout "${COMMAND_TIMEOUT_SECONDS}s" \
        notify-send "${notify_args[@]}" \
        "Claude Code — resumed automatically" \
        "\"${message}\" sent after the usage limit reset." >/dev/null 2>&1 || true
  fi
fi
