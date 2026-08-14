#!/bin/bash
# Origin: Ayce45/claude-terminator-notify@d3dffde4630e6813d031ce08be253bfedb142b84
# Make Terminator always launch under XWayland (GDK_BACKEND=x11), whatever the
# launch path. On GNOME Wayland this is what lets a notification click actually
# raise/focus the right window (a native-Wayland window can't self-activate).
#
# It covers the two real launch paths:
#   1. GNOME custom keyboard shortcuts whose command is `terminator`
#      (e.g. your Ctrl+Alt+T) -> command becomes `env GDK_BACKEND=x11 terminator`
#   2. App grid / dash launches -> a user .desktop override in
#      ~/.local/share/applications/terminator.desktop with the same env prefix
#
# Both changes are reversible:  ./force-xwayland.sh --undo
#
# Note: launching `terminator` by hand from a shell where /usr/bin precedes
# ~/.local/bin won't pick this up — add an alias if you care:
#     alias terminator='env GDK_BACKEND=x11 terminator'

set -uo pipefail

MARKER="# claude-terminator-notify: forces XWayland (GDK_BACKEND=x11)"
SYS_DESKTOP="/usr/share/applications/terminator.desktop"
USER_DESKTOP="${HOME}/.local/share/applications/terminator.desktop"
PREFIX="env GDK_BACKEND=x11 "
MK_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*"; }

# Iterate the GNOME custom keybindings whose command launches terminator.
each_terminator_keybinding() {
  command -v gsettings >/dev/null 2>&1 || return 0
  local list
  list=$(gsettings get "$MK_SCHEMA" custom-keybindings 2>/dev/null) || return 0
  printf '%s\n' "$list" | grep -oE "/[^']*custom-keybindings/[^']*/" | while read -r path; do
    echo "$path"
  done
}

kb_command() { gsettings get "${KB_SCHEMA}:$1" command 2>/dev/null | sed "s/^'//; s/'$//"; }
kb_set()     { gsettings set "${KB_SCHEMA}:$1" command "$2" 2>/dev/null; }

do_install() {
  if [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
    say "Session is not Wayland — GDK_BACKEND=x11 is a no-op here, setting up anyway."
  fi

  # 1. Keyboard shortcuts.
  local touched=0
  while read -r path; do
    [ -n "$path" ] || continue
    local cmd; cmd=$(kb_command "$path")
    case "$cmd" in
      terminator|/usr/bin/terminator|*/terminator)
        kb_set "$path" "${PREFIX}${cmd}" && {
          say "Shortcut $path: '$cmd' -> '${PREFIX}${cmd}'"; touched=1; }
        ;;
    esac
  done < <(each_terminator_keybinding)
  [ "$touched" -eq 0 ] && warn "No terminator keyboard shortcut found to update."

  # 2. .desktop override.
  if [ -f "$SYS_DESKTOP" ]; then
    mkdir -p "$(dirname "$USER_DESKTOP")"
    {
      head -1 "$SYS_DESKTOP"          # [Desktop Entry]
      echo "$MARKER"
      tail -n +2 "$SYS_DESKTOP" | sed -E "s|^Exec=(env GDK_BACKEND=x11 )?|Exec=${PREFIX}|"
    } > "$USER_DESKTOP"
    say "Installed .desktop override: $USER_DESKTOP"
  else
    warn "No system $SYS_DESKTOP — skipping .desktop override."
  fi

  say "Done. Quit all Terminator windows and relaunch for it to take effect."
}

do_undo() {
  # 1. Keyboard shortcuts: strip the env prefix.
  while read -r path; do
    [ -n "$path" ] || continue
    local cmd; cmd=$(kb_command "$path")
    case "$cmd" in
      "${PREFIX}"*) kb_set "$path" "${cmd#"$PREFIX"}" && say "Reverted shortcut $path";;
    esac
  done < <(each_terminator_keybinding)

  # 2. .desktop override: only remove the one we created (marker present).
  if [ -f "$USER_DESKTOP" ] && grep -qF "$MARKER" "$USER_DESKTOP"; then
    rm -f "$USER_DESKTOP"
    say "Removed .desktop override: $USER_DESKTOP"
  fi
  say "Done."
}

do_undo_owned() {
  local ownership_file=$1 desktop_snapshot=$2 path original current
  local failed=0
  while IFS= read -r -d '' path && IFS= read -r -d '' original; do
    if ! current=$(kb_command "$path"); then
      warn "Could not read owned shortcut $path"
      failed=1
      continue
    fi
    if [ "$current" = "${PREFIX}${original}" ]; then
      if kb_set "$path" "$original"; then
        say "Reverted owned shortcut $path"
      else
        warn "Could not revert owned shortcut $path"
        failed=1
      fi
    fi
  done < "$ownership_file"

  if [ -f "$USER_DESKTOP" ] && [ -f "$desktop_snapshot" ] && \
      cmp -s "$USER_DESKTOP" "$desktop_snapshot"; then
    rm -f "$USER_DESKTOP"
    say "Removed .desktop override: $USER_DESKTOP"
  elif [ -f "$USER_DESKTOP" ] && grep -qF "$MARKER" "$USER_DESKTOP"; then
    warn "Owned desktop override was modified; leaving it unchanged."
  fi
  say "Done."
  return "$failed"
}

case "${1:-}" in
  --undo-owned)
    [ "$#" -eq 3 ] && [ -f "$2" ] || {
      warn "Usage: $0 --undo-owned OWNERSHIP_FILE DESKTOP_SNAPSHOT"
      exit 2
    }
    do_undo_owned "$2" "$3"
    ;;
  --undo) do_undo ;;
  *)      do_install ;;
esac
