#!/usr/bin/env bash
# Notification and Stop hook for Claude Code.
set -uo pipefail

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

event=$(jq -r '.hook_event_name // "Notification"' <<<"$input")
session_id=$(jq -r '.session_id // ""' <<<"$input")
cwd=$(jq -r '.cwd // ""' <<<"$input")
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
pane_uuid=$(python3 - "$root" "$session_id" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from core.runtime_state import RuntimeState

print(RuntimeState().read_pane("claude", sys.argv[2]) or "")
PY
)

title="Claude Code"
if [ -n "$cwd" ]; then
  title="Claude Code — $(basename "$cwd")"
fi
kind="waiting"
case "$event" in
  Stop)
    kind="complete"
    message="Task complete — waiting for your next instruction."
    ;;
  *)
    message=$(jq -r '.message // "Waiting for your input."' <<<"$input")
    if printf '%s' "$message" | grep -qiE 'permission|approval|approve'; then
      kind="permission"
    fi
    ;;
esac
payload=$(jq -cn --arg title "$title" --arg body "$message" '{title: $title, body: $body}')

if command -v gdbus >/dev/null 2>&1; then
  if response=$(gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
      --method "${BUS}.Notify" "claude" "$session_id" "" "$pane_uuid" "$kind" "$payload" 2>&1) \
      && printf '%s' "$response" | grep -qE 'uint32 [1-9][0-9]*'; then
    exit 0
  fi
fi

if command -v notify-send >/dev/null 2>&1; then
  notify-send --app-name="Claude Code" --icon="${HOME}/.claude/assets/claude.png" \
    --expire-time=10000 "$title" "$message" >/dev/null 2>&1 || true
fi
