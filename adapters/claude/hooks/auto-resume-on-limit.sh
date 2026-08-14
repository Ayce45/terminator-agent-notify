#!/usr/bin/env bash
# Best-effort hook trigger; periodic callers can invoke autoresume.py --scan.
set -uo pipefail

[ "${CLAUDE_AUTORESUME:-1}" = "0" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

input="$(cat)"
session_id=$(jq -r '.session_id // ""' <<<"$input")
transcript=$(jq -r '.transcript_path // ""' <<<"$input")
[ -n "$session_id" ] || exit 0
if [ -z "$transcript" ] || [ ! -f "$transcript" ]; then
  transcript=$(find "$HOME/.claude/projects" -type f -name "${session_id}.jsonl" -print 2>/dev/null | head -1)
fi
[ -f "$transcript" ] || exit 0

engine="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/autoresume.py"
python3 "$engine" --transcript "$transcript" --session "$session_id" >/dev/null 2>&1 || true
