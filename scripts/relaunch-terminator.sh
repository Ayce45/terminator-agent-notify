#!/bin/bash
# Origin: Ayce45/claude-terminator-notify@d3dffde4630e6813d031ce08be253bfedb142b84
# Quit the running Terminator and relaunch it under XWayland (GDK_BACKEND=x11).
# Handy for a one-off test before (or instead of) the persistent setup in
# force-xwayland.sh. Nothing permanent is changed here.
#
# Your current Terminator (and any Claude session inside it) WILL be closed —
# reopen Claude afterwards with:  claude --resume
#
# Run from any pane:  ./scripts/relaunch-terminator.sh

set -u

echo "Relaunching Terminator under XWayland in ~2s…"
echo "This window will close. Reconnect with: claude --resume"

# Detached relauncher in its own session, so the kill below can't touch it.
# Its argv contains 'terminator' but NOT '/usr/bin/terminator', so the pkill
# pattern below won't match it.
setsid bash -c 'sleep 2; GDK_BACKEND=x11 exec terminator' </dev/null >/dev/null 2>&1 &
disown

# Kill the current Terminator (runs as `/usr/bin/python3 /usr/bin/terminator`).
pkill -f '/usr/bin/terminator'
