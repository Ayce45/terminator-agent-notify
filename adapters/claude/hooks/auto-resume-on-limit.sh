#!/usr/bin/env bash
# Best-effort hook trigger; periodic callers can invoke autoresume.py --scan.
set -uo pipefail

command -v python3 >/dev/null 2>&1 || exit 0
command -v timeout >/dev/null 2>&1 || exit 0
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
config_file="${TERMINATOR_AGENT_NOTIFY_CONFIG:-${XDG_STATE_HOME:-$HOME/.local/state}/terminator-agent-notify/environment}"
readarray -d '' -t persisted < <(python3 - "$root" "$config_file" \
    CLAUDE_AUTORESUME CLAUDE_AUTORESUME_GENERATION CLAUDE_AUTORESUME_MESSAGE \
    CLAUDE_AUTORESUME_BUFFER CLAUDE_AUTORESUME_ARM_NOTIFY \
    TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS TERMINATOR_AGENT_NOTIFY_EXPIRY_MS \
    TERMINATOR_AGENT_NOTIFY_LOG_LEVEL <<'PY'
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
resolve() {
  local offset=$1 name=$2 default=$3
  if [ "${persisted[$offset]:-0}" = 1 ]; then
    printf '%s' "${persisted[$((offset + 1))]-}"
  else
    printf '%s' "${!name:-$default}"
  fi
}

claude_autoresume=$(resolve 0 CLAUDE_AUTORESUME 1)
claude_generation=$(resolve 2 CLAUDE_AUTORESUME_GENERATION '')
claude_message=$(resolve 4 CLAUDE_AUTORESUME_MESSAGE continue)
claude_buffer=$(resolve 6 CLAUDE_AUTORESUME_BUFFER 90)
claude_arm_notify=$(resolve 8 CLAUDE_AUTORESUME_ARM_NOTIFY 1)
notifications=$(resolve 10 TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS 1)
expiry_ms=$(resolve 12 TERMINATOR_AGENT_NOTIFY_EXPIRY_MS '')
log_level=$(resolve 14 TERMINATOR_AGENT_NOTIFY_LOG_LEVEL info)

[ "$claude_autoresume" = "0" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

input="$(cat)"
session_id=$(jq -r '.session_id // ""' <<<"$input")
transcript=$(jq -r '.transcript_path // ""' <<<"$input")
[ -n "$session_id" ] || exit 0
if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  transcript=$(find "$HOME/.claude/projects" -type f -name "${session_id}.jsonl" -print 2>/dev/null | head -1)
fi
[ -f "$transcript" ] || exit 0

engine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/autoresume.py"
hook_timeout_seconds="${CLAUDE_AUTORESUME_HOOK_TIMEOUT_SECONDS:-20}"
timeout "${hook_timeout_seconds}s" env \
  TERMINATOR_AGENT_NOTIFY_CONFIG="$config_file" \
  TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS="$notifications" \
  TERMINATOR_AGENT_NOTIFY_EXPIRY_MS="$expiry_ms" \
  TERMINATOR_AGENT_NOTIFY_LOG_LEVEL="$log_level" \
  CLAUDE_AUTORESUME="$claude_autoresume" \
  CLAUDE_AUTORESUME_GENERATION="$claude_generation" \
  CLAUDE_AUTORESUME_MESSAGE="$claude_message" \
  CLAUDE_AUTORESUME_BUFFER="$claude_buffer" \
  CLAUDE_AUTORESUME_ARM_NOTIFY="$claude_arm_notify" \
  python3 "$engine" --transcript "$transcript" --session "$session_id" \
  >/dev/null 2>&1 || true
