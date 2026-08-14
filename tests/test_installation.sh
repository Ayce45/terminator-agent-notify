#!/bin/bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

export HOME="$TEST_ROOT/home"
export XDG_CONFIG_HOME="$TEST_ROOT/config"
export XDG_DATA_HOME="$TEST_ROOT/data"
export XDG_STATE_HOME="$TEST_ROOT/state"
export XDG_SESSION_TYPE=x11
mkdir -p "$HOME/.claude" "$HOME/.codex" "$TEST_ROOT/bin"

SYSTEMCTL_LOG="$TEST_ROOT/systemctl.log"
export SYSTEMCTL_LOG
cat > "$TEST_ROOT/bin/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
EOF
chmod +x "$TEST_ROOT/bin/systemctl"
export PATH="$TEST_ROOT/bin:$PATH"

cat > "$HOME/.claude/settings.json" <<'EOF'
{"keep":"claude","hooks":{"Stop":[{"description":"user hook","hooks":[{"type":"command","command":"/bin/true"}]}]}}
EOF
cat > "$HOME/.codex/hooks.json" <<'EOF'
{"keep":"codex","hooks":{"SessionStart":[{"description":"user hook","hooks":[{"type":"command","command":"/bin/true"}]}]}}
EOF

if "$ROOT/install.sh" invalid >/dev/null 2>&1; then
  echo "invalid install target unexpectedly succeeded" >&2
  exit 1
fi
test ! -e "$XDG_DATA_HOME/terminator-agent-notify"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify"

install_output=$($ROOT/install.sh both)
case "$install_output" in
  *"review and trust"*) ;;
  *) echo "missing Codex trust-review reminder" >&2; exit 1 ;;
esac

test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
test -f "$XDG_CONFIG_HOME/terminator/core/runtime_state.py"
test -f "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude/autoresume.py"
test -f "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex/hooks/stop.py"
test "$(stat -c %a "$XDG_STATE_HOME/terminator-agent-notify")" = 700
test "$(stat -c %a "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters")" = 600

python3 - "$HOME/.claude/settings.json" "$HOME/.codex/hooks.json" <<'PY'
import json
import sys
for path, expected in zip(sys.argv[1:], ("claude", "codex")):
    data = json.load(open(path, encoding="utf-8"))
    assert data["keep"] == expected
    assert any(
        item.get("description") == "user hook"
        for items in data["hooks"].values()
        for item in items
    )
PY

claude_before=$(sha256sum "$HOME/.claude/settings.json")
codex_before=$(sha256sum "$HOME/.codex/hooks.json")
backup_count_before=$(find "$HOME" -type f -name '*.bak.*' | wc -l)
$ROOT/install.sh both >/dev/null
test "$(sha256sum "$HOME/.claude/settings.json")" = "$claude_before"
test "$(sha256sum "$HOME/.codex/hooks.json")" = "$codex_before"
test "$(find "$HOME" -type f -name '*.bak.*' | wc -l)" = "$backup_count_before"

grep -q -- '--user enable --now claude-limit-poller.timer' "$SYSTEMCTL_LOG"
if grep -q -- '--user enable --now codex-limit-poller.timer' "$SYSTEMCTL_LOG"; then
  echo "Codex timer enabled without CODEX_AUTORESUME=1" >&2
  exit 1
fi

$ROOT/uninstall.sh codex >/dev/null
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex"
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx claude "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

CODEX_AUTORESUME=1 $ROOT/install.sh codex >/dev/null
grep -q -- '--user enable --now codex-limit-poller.timer' "$SYSTEMCTL_LOG"
$ROOT/uninstall.sh claude >/dev/null
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex"
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx codex "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

$ROOT/uninstall.sh codex >/dev/null
test ! -e "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
test ! -e "$XDG_CONFIG_HOME/terminator/core/runtime_state.py"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

python3 - "$HOME/.claude/settings.json" "$HOME/.codex/hooks.json" <<'PY'
import json
import sys
for path, expected in zip(sys.argv[1:], ("claude", "codex")):
    data = json.load(open(path, encoding="utf-8"))
    assert data["keep"] == expected
    assert not any(
        item.get("description", "").startswith("terminator-agent-notify:")
        for items in data["hooks"].values()
        for item in items
    )
PY

echo "installation integration checks passed"
