#!/usr/bin/env bash
# Record the Terminator pane for a Claude Code session.
set -uo pipefail

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
COMMAND_TIMEOUT_SECONDS="${TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS:-5}"
input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

session_id=$(jq -r '.session_id // ""' <<<"$input")
[ -z "$session_id" ] || [ "$session_id" = "null" ] && exit 0

uuid="${TERMINATOR_UUID:-}"
if [ -z "$uuid" ] && command -v gdbus >/dev/null 2>&1; then
  uuid=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
    --method "${BUS}.GetFocusedUUID" 2>/dev/null \
    | grep -oiE 'urn:uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
    | head -1)
fi
if [ -z "$uuid" ] && command -v gdbus >/dev/null 2>&1; then
  result=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    gdbus call --session \
    --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.ListNames 2>/dev/null \
    | tr ',' '\n' | grep -oE 'net\.tenshu\.Terminator2[a-f0-9]+' | head -1)
  if [ -n "$result" ]; then
    uuid=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
      gdbus call --session --dest "$result" \
      --object-path /net/tenshu/Terminator2 \
      --method "${result}.get_focused_terminal" 2>/dev/null \
      | grep -oE 'urn:uuid:[a-f0-9-]+' | head -1)
  fi
fi
[ -n "$uuid" ] || exit 0

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
python3 - "$root" "$session_id" "$uuid" <<'PY' || true
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.runtime_state import RuntimeState

RuntimeState().record_pane("claude", sys.argv[2], sys.argv[3])
PY
exit 0
