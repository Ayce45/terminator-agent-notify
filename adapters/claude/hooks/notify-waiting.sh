#!/usr/bin/env bash
# Notification and Stop hook for Claude Code.
set -uo pipefail

command -v python3 >/dev/null 2>&1 || exit 0
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
config_file="${TERMINATOR_AGENT_NOTIFY_CONFIG:-${XDG_STATE_HOME:-$HOME/.local/state}/terminator-agent-notify/environment}"
readarray -d '' -t persisted < <(python3 - "$root" "$config_file" \
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
if [ "${persisted[0]:-0}" = 1 ]; then
  notifications="${persisted[1]-}"
else
  notifications="${TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS:-1}"
fi
if [ "${persisted[2]:-0}" = 1 ]; then
  notification_expiry_ms="${persisted[3]-}"
else
  notification_expiry_ms="${TERMINATOR_AGENT_NOTIFY_EXPIRY_MS:-10000}"
fi
[ -n "$notification_expiry_ms" ] || notification_expiry_ms=10000

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
COMMAND_TIMEOUT_SECONDS="${TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS:-5}"
input="$(cat)"
[ "$notifications" = "0" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

event=$(jq -r '.hook_event_name // "Notification"' <<<"$input")
session_id=$(jq -r '.session_id // ""' <<<"$input")
cwd=$(jq -r '.cwd // ""' <<<"$input")
pane_uuid=$(python3 - "$root" "$session_id" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from terminator_agent_notify_core.runtime_state import RuntimeState

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
    # Claude Code words every prompt as a permission request, including the
    # question tool, where approve/deny would only pick the default answer.
    tool=$(printf '%s' "$message" |
      sed -nE 's/.*permission to use ([A-Za-z_][A-Za-z0-9_-]*).*/\1/p')
    if [ "$tool" = "AskUserQuestion" ]; then
      message="Claude asks a question — answer in the terminal."
    elif printf '%s' "$message" | grep -qiE 'permission|approval|approve'; then
      kind="permission"
      [ -n "$tool" ] && message="Permission requested — $tool"
    fi
    ;;
esac
payload=$(jq -cn --arg title "$title" --arg body "$message" '{title: $title, body: $body}')
# gdbus parses each CLI argument as GVariant text. Preserve JSON's escape
# sequences so \n and similar sequences reach the plugin as valid JSON.
payload=${payload//\\/\\\\}

if command -v gdbus >/dev/null 2>&1; then
  if response=$(timeout "${COMMAND_TIMEOUT_SECONDS}s" \
      gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
      --method "${BUS}.Notify" "claude" "$session_id" "" "$pane_uuid" "$kind" "$payload" 2>&1) \
      && [[ "$response" =~ ^\(uint32[[:space:]]+[1-9][0-9]*,\)$ ]]; then
    exit 0
  fi
fi

if command -v notify-send >/dev/null 2>&1; then
  timeout "${COMMAND_TIMEOUT_SECONDS}s" \
    notify-send --app-name="Claude Code" --icon="${HOME}/.claude/assets/claude.png" \
    --expire-time="$notification_expiry_ms" "$title" "$message" >/dev/null 2>&1 || true
fi
