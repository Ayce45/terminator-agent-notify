#!/usr/bin/env bash
# UserPromptSubmit hook: remove informational notices for this Claude session.
set -uo pipefail

BUS="io.github.TerminatorAgentNotify"
PATH_NAME="/io/github/TerminatorAgentNotify"
COMMAND_TIMEOUT_SECONDS="${TERMINATOR_AGENT_NOTIFY_COMMAND_TIMEOUT_SECONDS:-5}"
input="$(cat)"
command -v jq >/dev/null 2>&1 || exit 0
command -v gdbus >/dev/null 2>&1 || exit 0

session_id=$(jq -r '.session_id // ""' <<<"$input")
[ -z "$session_id" ] || [ "$session_id" = "null" ] && exit 0

timeout "${COMMAND_TIMEOUT_SECONDS}s" \
  gdbus call --session --dest "$BUS" --object-path "$PATH_NAME" \
  --method "${BUS}.DismissSession" "claude" "$session_id" >/dev/null 2>&1 || true
