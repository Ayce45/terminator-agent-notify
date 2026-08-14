#!/bin/bash
set -euo pipefail
trap 'status=$?; printf "installation test failed at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2; exit "$status"' ERR

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEST_ROOT=$(mktemp -d)
trap 'rm -rf "$TEST_ROOT"' EXIT

export HOME="$TEST_ROOT/home"
export XDG_CONFIG_HOME="$TEST_ROOT/config"
export XDG_DATA_HOME="$TEST_ROOT/data"
export XDG_STATE_HOME="$TEST_ROOT/state"
export XDG_SESSION_TYPE=x11
mkdir -p "$HOME/.claude" "$HOME/.codex" "$TEST_ROOT/bin"

TERMINATOR_SYSTEM_DESKTOP="$TEST_ROOT/terminator.desktop"
export TERMINATOR_SYSTEM_DESKTOP
cat > "$TERMINATOR_SYSTEM_DESKTOP" <<'EOF'
[Desktop Entry]
Name=Terminator CI fixture
Exec=terminator
EOF

SYSTEMCTL_LOG="$TEST_ROOT/systemctl.log"
export SYSTEMCTL_LOG
cat > "$TEST_ROOT/bin/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$SYSTEMCTL_LOG"
EOF
chmod +x "$TEST_ROOT/bin/systemctl"
cat > "$TEST_ROOT/bin/gsettings" <<'EOF'
#!/bin/sh
if [ "${GSETTINGS_FAIL_GET:-0}" = 1 ] && [ "${1:-}" = get ] && \
    [ "${3:-}" = command ]; then
  exit 1
fi
if [ "${1:-}" = get ] && [ "${3:-}" = custom-keybindings ]; then
  case "${GSETTINGS_TEST_MODE:-}" in
    unrelated-x11|existing-terminator-x11)
      printf "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/']\n"
      ;;
    owned-shortcut)
      if [ -f "$GSETTINGS_DB/custom1" ]; then
        printf "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/', '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom1/']\n"
      else
        printf "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/custom0/']\n"
      fi
      ;;
    *) printf '@as []\n' ;;
  esac
elif [ "${1:-}" = get ] && [ "${3:-}" = command ]; then
  case "${GSETTINGS_TEST_MODE:-}" in
    unrelated-x11) printf "'env GDK_BACKEND=x11 firefox'\n" ;;
    existing-terminator-x11)
      printf "'env GDK_BACKEND=x11 terminator --profile work'\n"
      ;;
    owned-shortcut)
      case "$2" in
        *custom0/) printf "'%s'\n" "$(cat "$GSETTINGS_DB/custom0")" ;;
        *custom1/) printf "'%s'\n" "$(cat "$GSETTINGS_DB/custom1")" ;;
      esac
      ;;
  esac
elif [ "${1:-}" = set ] && [ "${3:-}" = command ] && \
    [ "${GSETTINGS_TEST_MODE:-}" = owned-shortcut ]; then
  case "$2" in
    *custom0/) printf '%s\n' "$4" > "$GSETTINGS_DB/custom0" ;;
    *custom1/) printf '%s\n' "$4" > "$GSETTINGS_DB/custom1" ;;
  esac
fi
EOF
chmod +x "$TEST_ROOT/bin/gsettings"
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
test -f "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/runtime_state.py"
test ! -e "$XDG_CONFIG_HOME/terminator/core"
test -f "$XDG_DATA_HOME/terminator-agent-notify/adapters/__init__.py"
test -f "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude/autoresume.py"
test -f "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex/hooks/stop.py"
(cd "$TEST_ROOT" && python3 -I "$ROOT/tests/installed_layout_import.py" \
  "$XDG_CONFIG_HOME/terminator")
(cd "$TEST_ROOT" && printf '{}\n' | python3 -I \
  "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex/hooks/stop.py")
mkdir -p "$TEST_ROOT/foreign/adapters"
printf 'raise RuntimeError("foreign adapters package imported")\n' \
  > "$TEST_ROOT/foreign/adapters/__init__.py"
(cd "$TEST_ROOT" && printf '{}\n' | PYTHONPATH="$TEST_ROOT/foreign" python3 \
  "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex/hooks/stop.py")
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/__pycache__"
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex/__pycache__"
test "$(stat -c %a "$XDG_STATE_HOME/terminator-agent-notify")" = 700
test "$(stat -c %a "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters")" = 600
ENV_FILE="$XDG_STATE_HOME/terminator-agent-notify/environment"
test -f "$ENV_FILE"
test "$(stat -c %a "$ENV_FILE")" = 600
grep -qx 'CLAUDE_AUTORESUME=1' "$ENV_FILE"
grep -qx 'CODEX_AUTORESUME=0' "$ENV_FILE"
grep -qx 'TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=1' "$ENV_FILE"
grep -qx "TERMINATOR_AGENT_NOTIFY_EXPIRY_MS=''" "$ENV_FILE"
grep -qx 'TERMINATOR_AGENT_NOTIFY_LOG_LEVEL=info' "$ENV_FILE"
claude_generation_before=$(sed -n 's/^CLAUDE_AUTORESUME_GENERATION=//p' "$ENV_FILE")
codex_generation_before=$(sed -n 's/^CODEX_AUTORESUME_GENERATION=//p' "$ENV_FILE")
test "${#claude_generation_before}" -ge 32
test "${#codex_generation_before}" -ge 32
mkdir -p "$XDG_CONFIG_HOME/terminator/plugins/__pycache__"
touch "$XDG_CONFIG_HOME/terminator/plugins/__pycache__/agent_notify.cpython-311.pyc"
touch "$XDG_CONFIG_HOME/terminator/plugins/__pycache__/unrelated_plugin.cpython-311.pyc"
mkdir -p "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/__pycache__"
touch "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/__pycache__/__init__.cpython-311.pyc"
touch "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/__pycache__/runtime_state.cpython-311.pyc"
mkdir -p "$XDG_CONFIG_HOME/terminator/core/__pycache__"
touch "$XDG_CONFIG_HOME/terminator/core/__pycache__/user_core.cpython-311.pyc"

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

test -f "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.service"
test -f "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.timer"
test -f "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-codex-limit-poller.service"
test -f "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-codex-limit-poller.timer"
test ! -e "$XDG_CONFIG_HOME/systemd/user/claude-limit-poller.service"
test ! -e "$XDG_CONFIG_HOME/systemd/user/codex-limit-poller.service"
grep -Fq "EnvironmentFile=\"$ENV_FILE\"" \
  "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.service"
grep -Fq "EnvironmentFile=\"$ENV_FILE\"" \
  "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-codex-limit-poller.service"
if grep -q '^Environment=CODEX_AUTORESUME=1$' \
    "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-codex-limit-poller.service"; then
  echo "Codex service bypasses persisted opt-out" >&2
  exit 1
fi
grep -q -- '--user enable --now terminator-agent-notify-claude-limit-poller.timer' "$SYSTEMCTL_LOG"
grep -q -- '--user stop claude-autoresume-\*' "$SYSTEMCTL_LOG"
grep -q -- '--user stop codex-autoresume-\*' "$SYSTEMCTL_LOG"
if grep -q -- '--user enable --now terminator-agent-notify-codex-limit-poller.timer' "$SYSTEMCTL_LOG"; then
  echo "Codex timer enabled without CODEX_AUTORESUME=1" >&2
  exit 1
fi

$ROOT/uninstall.sh codex >/dev/null
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex"
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/__pycache__"
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx claude "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

: > "$SYSTEMCTL_LOG"
CODEX_AUTORESUME=1 CODEX_AUTORESUME_MESSAGE='resume codex safely' \
  $ROOT/install.sh codex >/dev/null
grep -q -- '--user enable --now terminator-agent-notify-codex-limit-poller.timer' "$SYSTEMCTL_LOG"
grep -q -- '--user stop terminator-agent-notify-codex-autoresume-\*' "$SYSTEMCTL_LOG"
grep -q -- '--user stop codex-autoresume-\*' "$SYSTEMCTL_LOG"
grep -qx 'CODEX_AUTORESUME=1' "$ENV_FILE"
grep -qx "CODEX_AUTORESUME_MESSAGE='resume codex safely'" "$ENV_FILE"
codex_generation_enabled=$(sed -n 's/^CODEX_AUTORESUME_GENERATION=//p' "$ENV_FILE")
test "$codex_generation_enabled" != "$codex_generation_before"
: > "$SYSTEMCTL_LOG"
$ROOT/install.sh codex >/dev/null
grep -q -- '--user disable --now terminator-agent-notify-codex-limit-poller.timer' "$SYSTEMCTL_LOG"
grep -qx 'CODEX_AUTORESUME=0' "$ENV_FILE"
codex_generation_disabled=$(sed -n 's/^CODEX_AUTORESUME_GENERATION=//p' "$ENV_FILE")
test "$codex_generation_disabled" != "$codex_generation_enabled"

: > "$SYSTEMCTL_LOG"
CLAUDE_AUTORESUME=0 TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=0 \
  TERMINATOR_AGENT_NOTIFY_EXPIRY_MS=5000 \
  TERMINATOR_AGENT_NOTIFY_LOG_LEVEL=debug \
  $ROOT/install.sh claude >/dev/null
grep -q -- '--user disable --now terminator-agent-notify-claude-limit-poller.timer' "$SYSTEMCTL_LOG"
grep -q -- '--user stop terminator-agent-notify-claude-autoresume-\*' "$SYSTEMCTL_LOG"
grep -qx 'CLAUDE_AUTORESUME=0' "$ENV_FILE"
grep -qx 'TERMINATOR_AGENT_NOTIFY_NOTIFICATIONS=0' "$ENV_FILE"
grep -qx 'TERMINATOR_AGENT_NOTIFY_EXPIRY_MS=5000' "$ENV_FILE"
grep -qx 'TERMINATOR_AGENT_NOTIFY_LOG_LEVEL=debug' "$ENV_FILE"
claude_generation_disabled=$(sed -n 's/^CLAUDE_AUTORESUME_GENERATION=//p' "$ENV_FILE")
test "$claude_generation_disabled" != "$claude_generation_before"

: > "$SYSTEMCTL_LOG"
$ROOT/install.sh claude >/dev/null
grep -q -- '--user enable --now terminator-agent-notify-claude-limit-poller.timer' "$SYSTEMCTL_LOG"
grep -qx 'CLAUDE_AUTORESUME=1' "$ENV_FILE"
claude_generation_reenabled=$(sed -n 's/^CLAUDE_AUTORESUME_GENERATION=//p' "$ENV_FILE")
test "$claude_generation_reenabled" != "$claude_generation_disabled"
$ROOT/uninstall.sh claude >/dev/null
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
test -d "$XDG_DATA_HOME/terminator-agent-notify/adapters/codex"
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx codex "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

$ROOT/uninstall.sh codex >/dev/null
test ! -e "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
test ! -e "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/runtime_state.py"
test ! -e "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core"
test ! -e "$XDG_DATA_HOME/terminator-agent-notify"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
test ! -e "$XDG_CONFIG_HOME/terminator/plugins/__pycache__/agent_notify.cpython-311.pyc"
test -e "$XDG_CONFIG_HOME/terminator/plugins/__pycache__/unrelated_plugin.cpython-311.pyc"
test ! -e "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/__pycache__/__init__.cpython-311.pyc"
test ! -e "$XDG_CONFIG_HOME/terminator/terminator_agent_notify_core/__pycache__/runtime_state.cpython-311.pyc"
test -e "$XDG_CONFIG_HOME/terminator/core/__pycache__/user_core.cpython-311.pyc"

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

# Task 7 can add the adapter without changing the installer: when its file is
# present in a source checkout, CODEX_AUTORESUME=1 enables the timer normally.
FUTURE_ROOT="$TEST_ROOT/future-source"
cp -a "$ROOT" "$FUTURE_ROOT"
printf '#!/usr/bin/env python3\n' > "$FUTURE_ROOT/adapters/codex/autoresume.py"
chmod +x "$FUTURE_ROOT/adapters/codex/autoresume.py"
: > "$SYSTEMCTL_LOG"
CODEX_AUTORESUME=1 "$FUTURE_ROOT/install.sh" codex >/dev/null
grep -q -- '--user enable --now terminator-agent-notify-codex-limit-poller.timer' "$SYSTEMCTL_LOG"
"$FUTURE_ROOT/uninstall.sh" codex >/dev/null

# Unit rendering must preserve a hostile-but-valid XDG data path exactly under
# systemd's command-line quoting/specifier rules.
export HOME="$TEST_ROOT/special home"
export XDG_CONFIG_HOME="$TEST_ROOT/special config"
SPECIAL_SUFFIX='data &|\quote"% space'
export XDG_DATA_HOME="$TEST_ROOT/$SPECIAL_SUFFIX"
export XDG_STATE_HOME="$TEST_ROOT/special state"
mkdir -p "$HOME"
"$ROOT/install.sh" claude >/dev/null
python3 - "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.service" \
  "$TEST_ROOT" <<'PY'
import sys

unit_path, test_root = sys.argv[1:]
install_root = f'{test_root}/data &|\\\\quote\\"%% space/terminator-agent-notify'
expected = (
    'ExecStart=/usr/bin/env python3 '
    f'"{install_root}/adapters/claude/autoresume.py" --scan'
)
lines = open(unit_path, encoding="utf-8").read().splitlines()
assert expected in lines, (expected, lines)
environment_file = f'{test_root}/special state/terminator-agent-notify/environment'
assert f'EnvironmentFile="{environment_file}"' in lines, lines
assert not list(__import__("pathlib").Path(unit_path).parent.glob(".*.tmp"))
PY
"$ROOT/uninstall.sh" claude >/dev/null

# Pre-existing user XWayland setup is not project-owned and must survive both
# install and final uninstall byte-for-byte.
export HOME="$TEST_ROOT/preexisting-xwayland/home"
export XDG_CONFIG_HOME="$TEST_ROOT/preexisting-xwayland/config"
export XDG_DATA_HOME="$TEST_ROOT/preexisting-xwayland/data"
export XDG_STATE_HOME="$TEST_ROOT/preexisting-xwayland/state"
export XDG_SESSION_TYPE=wayland
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/terminator.desktop" <<'EOF'
[Desktop Entry]
Name=User Terminator
Exec=env GDK_BACKEND=x11 terminator --user-option
EOF
preexisting_hash=$(sha256sum "$HOME/.local/share/applications/terminator.desktop")
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
test "$(sha256sum "$HOME/.local/share/applications/terminator.desktop")" = "$preexisting_hash"
"$ROOT/uninstall.sh" claude >/dev/null
test "$(sha256sum "$HOME/.local/share/applications/terminator.desktop")" = "$preexisting_hash"

# A custom desktop override that does not yet request X11 is still user-owned;
# installation must not overwrite it in order to make XWayland reversible.
export HOME="$TEST_ROOT/custom-desktop/home"
export XDG_CONFIG_HOME="$TEST_ROOT/custom-desktop/config"
export XDG_DATA_HOME="$TEST_ROOT/custom-desktop/data"
export XDG_STATE_HOME="$TEST_ROOT/custom-desktop/state"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/terminator.desktop" <<'EOF'
[Desktop Entry]
Name=Custom Terminator
Exec=terminator --profile work
EOF
custom_desktop_hash=$(sha256sum "$HOME/.local/share/applications/terminator.desktop")
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
test "$(sha256sum "$HOME/.local/share/applications/terminator.desktop")" = \
  "$custom_desktop_hash"
"$ROOT/uninstall.sh" claude >/dev/null
test "$(sha256sum "$HOME/.local/share/applications/terminator.desktop")" = \
  "$custom_desktop_hash"

# Existing Terminator X11 shortcuts may include arguments and remain user-owned.
export HOME="$TEST_ROOT/existing-shortcut/home"
export XDG_CONFIG_HOME="$TEST_ROOT/existing-shortcut/config"
export XDG_DATA_HOME="$TEST_ROOT/existing-shortcut/data"
export XDG_STATE_HOME="$TEST_ROOT/existing-shortcut/state"
mkdir -p "$HOME"
GSETTINGS_TEST_MODE=existing-terminator-x11 FORCE_XWAYLAND=1 \
  "$ROOT/install.sh" claude >/dev/null
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
"$ROOT/uninstall.sh" claude >/dev/null

# An unrelated X11 application shortcut is not evidence that Terminator is
# already configured; the helper must still run and the project must own it.
export HOME="$TEST_ROOT/unrelated-x11/home"
export XDG_CONFIG_HOME="$TEST_ROOT/unrelated-x11/config"
export XDG_DATA_HOME="$TEST_ROOT/unrelated-x11/data"
export XDG_STATE_HOME="$TEST_ROOT/unrelated-x11/state"
mkdir -p "$HOME"
GSETTINGS_TEST_MODE=unrelated-x11 FORCE_XWAYLAND=1 \
  "$ROOT/install.sh" claude >/dev/null
test -f "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
grep -q '^Name=Terminator CI fixture$' \
  "$HOME/.local/share/applications/terminator.desktop"
"$ROOT/uninstall.sh" claude >/dev/null

# When this project applies the helper, it records ownership and undoes only
# that owned setup after the last adapter is removed.
export HOME="$TEST_ROOT/owned-xwayland/home"
export XDG_CONFIG_HOME="$TEST_ROOT/owned-xwayland/config"
export XDG_DATA_HOME="$TEST_ROOT/owned-xwayland/data"
export XDG_STATE_HOME="$TEST_ROOT/owned-xwayland/state"
mkdir -p "$HOME"
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
test -f "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
test -f "$HOME/.local/share/applications/terminator.desktop"
"$ROOT/uninstall.sh" claude >/dev/null
test ! -e "$HOME/.local/share/applications/terminator.desktop"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"

# If the user edits the generated desktop while it is installed, the marker is
# not enough to prove ownership of the new bytes; preserve the edited override.
export HOME="$TEST_ROOT/edited-owned-desktop/home"
export XDG_CONFIG_HOME="$TEST_ROOT/edited-owned-desktop/config"
export XDG_DATA_HOME="$TEST_ROOT/edited-owned-desktop/data"
export XDG_STATE_HOME="$TEST_ROOT/edited-owned-desktop/state"
mkdir -p "$HOME"
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
printf '# user edit\n' >> "$HOME/.local/share/applications/terminator.desktop"
"$ROOT/uninstall.sh" claude >/dev/null
grep -qx '# user edit' "$HOME/.local/share/applications/terminator.desktop"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"

# Record exact keyboard shortcuts changed by the helper. A new user-owned X11
# shortcut created later must retain its prefix when project changes are undone.
export HOME="$TEST_ROOT/owned-shortcut/home"
export XDG_CONFIG_HOME="$TEST_ROOT/owned-shortcut/config"
export XDG_DATA_HOME="$TEST_ROOT/owned-shortcut/data"
export XDG_STATE_HOME="$TEST_ROOT/owned-shortcut/state"
export GSETTINGS_TEST_MODE=owned-shortcut
export GSETTINGS_DB="$TEST_ROOT/owned-shortcut/gsettings"
mkdir -p "$HOME" "$GSETTINGS_DB"
printf 'terminator\n' > "$GSETTINGS_DB/custom0"
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
test "$(cat "$GSETTINGS_DB/custom0")" = 'env GDK_BACKEND=x11 terminator'
printf 'terminator\n' > "$GSETTINGS_DB/custom1"
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
test "$(cat "$GSETTINGS_DB/custom1")" = terminator
printf 'env GDK_BACKEND=x11 terminator\n' > "$GSETTINGS_DB/custom1"
"$ROOT/uninstall.sh" claude >/dev/null
test "$(cat "$GSETTINGS_DB/custom0")" = terminator
test "$(cat "$GSETTINGS_DB/custom1")" = 'env GDK_BACKEND=x11 terminator'
unset GSETTINGS_TEST_MODE GSETTINGS_DB

# A transient settings read failure must retain the exact ownership ledger so
# a later uninstall can retry restoring the project-mutated shortcut.
export HOME="$TEST_ROOT/owned-retry/home"
export XDG_CONFIG_HOME="$TEST_ROOT/owned-retry/config"
export XDG_DATA_HOME="$TEST_ROOT/owned-retry/data"
export XDG_STATE_HOME="$TEST_ROOT/owned-retry/state"
export GSETTINGS_TEST_MODE=owned-shortcut
export GSETTINGS_DB="$TEST_ROOT/owned-retry/gsettings"
mkdir -p "$HOME" "$GSETTINGS_DB"
printf 'terminator\n' > "$GSETTINGS_DB/custom0"
FORCE_XWAYLAND=1 "$ROOT/install.sh" claude >/dev/null
export GSETTINGS_FAIL_GET=1
"$ROOT/uninstall.sh" claude >/dev/null
test -f "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
test "$(cat "$GSETTINGS_DB/custom0")" = 'env GDK_BACKEND=x11 terminator'
unset GSETTINGS_FAIL_GET
"$ROOT/uninstall.sh" claude >/dev/null
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/xwayland-owned"
test "$(cat "$GSETTINGS_DB/custom0")" = terminator
unset GSETTINGS_TEST_MODE GSETTINGS_DB
export XDG_SESSION_TYPE=x11

# Missing state must not let selective uninstall remove shared files while the
# other adapter still has installed files/hooks.
export HOME="$TEST_ROOT/missing-state/home"
export XDG_CONFIG_HOME="$TEST_ROOT/missing-state/config"
export XDG_DATA_HOME="$TEST_ROOT/missing-state/data"
export XDG_STATE_HOME="$TEST_ROOT/missing-state/state"
mkdir -p "$HOME"
"$ROOT/install.sh" both >/dev/null
rm "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
touch "$XDG_STATE_HOME/terminator-agent-notify/.installed-adapters.interrupted"
"$ROOT/uninstall.sh" codex >/dev/null
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx claude "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/.installed-adapters.interrupted"
"$ROOT/uninstall.sh" claude >/dev/null

# A truncated state file must be repaired from the adapter that still exists.
export HOME="$TEST_ROOT/truncated-state/home"
export XDG_CONFIG_HOME="$TEST_ROOT/truncated-state/config"
export XDG_DATA_HOME="$TEST_ROOT/truncated-state/data"
export XDG_STATE_HOME="$TEST_ROOT/truncated-state/state"
mkdir -p "$HOME"
"$ROOT/install.sh" both >/dev/null
printf 'cla' > "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
truncated_output=$("$ROOT/uninstall.sh" claude 2>&1)
case "$truncated_output" in
  *"Ignoring invalid installed-adapters state: cla"*) ;;
  *) echo "truncated state was not reported" >&2; exit 1 ;;
esac
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx codex "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
"$ROOT/uninstall.sh" codex >/dev/null

# Corrupt state without any installed-directory or owned-hook evidence must not
# retain shared files forever. Invalid values are reported and discarded.
export HOME="$TEST_ROOT/corrupt-state/home"
export XDG_CONFIG_HOME="$TEST_ROOT/corrupt-state/config"
export XDG_DATA_HOME="$TEST_ROOT/corrupt-state/data"
export XDG_STATE_HOME="$TEST_ROOT/corrupt-state/state"
mkdir -p "$HOME"
"$ROOT/install.sh" claude >/dev/null
python3 "$ROOT/adapters/claude/configure.py" uninstall \
  --config "$HOME/.claude/settings.json"
rm -rf "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
printf 'claude\nnot-an-agent\n' \
  > "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
corrupt_output=$("$ROOT/uninstall.sh" codex 2>&1)
case "$corrupt_output" in
  *"Ignoring invalid installed-adapters state"*) ;;
  *) echo "corrupt state was not reported" >&2; exit 1 ;;
esac
test ! -e "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
test ! -e "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"

# An interrupted install can leave only an owned hook. That hook is sufficient
# evidence to preserve shared files until its own adapter is uninstalled.
export HOME="$TEST_ROOT/hook-only/home"
export XDG_CONFIG_HOME="$TEST_ROOT/hook-only/config"
export XDG_DATA_HOME="$TEST_ROOT/hook-only/data"
export XDG_STATE_HOME="$TEST_ROOT/hook-only/state"
mkdir -p "$HOME"
"$ROOT/install.sh" claude >/dev/null
rm "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
rm -rf "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
"$ROOT/uninstall.sh" codex >/dev/null
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx claude "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
"$ROOT/uninstall.sh" claude >/dev/null

# Ownership predates agent-specific descriptions; a legacy base-marker hook in
# an agent's config remains evidence that the corresponding adapter is active.
export HOME="$TEST_ROOT/legacy-hook/home"
export XDG_CONFIG_HOME="$TEST_ROOT/legacy-hook/config"
export XDG_DATA_HOME="$TEST_ROOT/legacy-hook/data"
export XDG_STATE_HOME="$TEST_ROOT/legacy-hook/state"
mkdir -p "$HOME"
"$ROOT/install.sh" claude >/dev/null
python3 - "$HOME/.claude/settings.json" <<'PY'
import json
import sys

path = sys.argv[1]
value = json.load(open(path, encoding="utf-8"))
for entries in value["hooks"].values():
    for entry in entries:
        if entry.get("description", "").startswith("terminator-agent-notify:"):
            entry["description"] = "terminator-agent-notify:legacy"
with open(path, "w", encoding="utf-8") as stream:
    json.dump(value, stream)
PY
rm "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
rm -rf "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
"$ROOT/uninstall.sh" codex >/dev/null
test -f "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx claude "$XDG_STATE_HOME/terminator-agent-notify/installed-adapters"
"$ROOT/uninstall.sh" claude >/dev/null

# Fixed-path collisions are rejected before hooks, adapters, or another fixed
# file can be mutated.
export HOME="$TEST_ROOT/fixed-collision/home"
export XDG_CONFIG_HOME="$TEST_ROOT/fixed-collision/config"
export XDG_DATA_HOME="$TEST_ROOT/fixed-collision/data"
export XDG_STATE_HOME="$TEST_ROOT/fixed-collision/state"
mkdir -p "$HOME/.claude" "$XDG_CONFIG_HOME/terminator/plugins"
printf 'user-owned plugin\n' > "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
collision_hash=$(sha256sum "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py")
if "$ROOT/install.sh" claude >/dev/null 2>&1; then
  echo "foreign plugin collision unexpectedly overwritten" >&2
  exit 1
fi
test "$(sha256sum "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py")" = "$collision_hash"
test ! -e "$XDG_DATA_HOME/terminator-agent-notify/adapters/claude"
if grep -q 'terminator-agent-notify:' "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "collision mutated Claude hooks" >&2
  exit 1
fi

export HOME="$TEST_ROOT/unit-collision/home"
export XDG_CONFIG_HOME="$TEST_ROOT/unit-collision/config"
export XDG_DATA_HOME="$TEST_ROOT/unit-collision/data"
export XDG_STATE_HOME="$TEST_ROOT/unit-collision/state"
mkdir -p "$HOME" "$XDG_CONFIG_HOME/systemd/user"
foreign_unit="$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.timer"
printf 'user-owned timer\n' > "$foreign_unit"
unit_collision_hash=$(sha256sum "$foreign_unit")
if "$ROOT/install.sh" claude >/dev/null 2>&1; then
  echo "foreign systemd collision unexpectedly overwritten" >&2
  exit 1
fi
test "$(sha256sum "$foreign_unit")" = "$unit_collision_hash"
test ! -e "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"

# Uninstall removes a managed fixed file only while its bytes still match the
# installation manifest. User edits are preserved.
export HOME="$TEST_ROOT/edited-managed/home"
export XDG_CONFIG_HOME="$TEST_ROOT/edited-managed/config"
export XDG_DATA_HOME="$TEST_ROOT/edited-managed/data"
export XDG_STATE_HOME="$TEST_ROOT/edited-managed/state"
mkdir -p "$HOME"
"$ROOT/install.sh" claude >/dev/null
manifest="$XDG_STATE_HOME/terminator-agent-notify/managed-files.json"
test -f "$manifest"
test "$(stat -c %a "$manifest")" = 600
printf '# user plugin edit\n' >> "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
printf '# user unit edit\n' >> \
  "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.service"
"$ROOT/uninstall.sh" claude >/dev/null
grep -qx '# user plugin edit' "$XDG_CONFIG_HOME/terminator/plugins/agent_notify.py"
grep -qx '# user unit edit' \
  "$XDG_CONFIG_HOME/systemd/user/terminator-agent-notify-claude-limit-poller.service"

echo "installation integration checks passed"
