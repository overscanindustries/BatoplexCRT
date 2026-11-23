#!/bin/bash
# BATOPLEX-LAUNCHER (marker used by uninstall.sh; do not remove)
# Batocera Ports launcher for Batoplex, a Plex client for the CRT.
# EmulationStation starts this through emulatorlauncher, which gives evmapy hotkeys
# (Hotkey+Start -> Alt+F4, see Batoplex.sh.keys) and runs the gameStart/gameStop scripts.
#
# The app never changes the video mode. This script records the mode before the start and
# puts it back at the end only if something changed it. Then it stops any mpv that remains.

APP=/userdata/system/batoplex
LOG=/userdata/system/batoplex/data/launcher.log
# X display :0 only when an X server runs (a Wayland or KMS box has none: SDL picks its own).
if [ -z "$DISPLAY" ] && [ -e /tmp/.X11-unix/X0 ]; then
    export DISPLAY=:0
fi
mkdir -p "$APP/data" && chmod 700 "$APP/data"

# Output name: Batocera setting first (for example DVI-I-1), else the first connected output.
OUT="$(grep -E '^global.videooutput=' /userdata/system/batocera.conf 2>/dev/null | cut -d= -f2)"
if [ -z "$OUT" ] && command -v xrandr >/dev/null 2>&1; then
    OUT="$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}')"
fi
SAVED="$(batocera-resolution --screen "$OUT" currentMode 2>/dev/null | head -n1)"
echo "$(date '+%F %T') start output=$OUT mode=$SAVED" >> "$LOG"

# EmulationStation reads emulatorlauncher's stdout through a pipe and waits until no process
# holds it. A pad that reconnects as the game stops makes emulatorlauncher start evmapy again
# after its own stop, and that evmapy keeps the pipe: ES hangs. Note the
# pipe now; at exit a detached guard (batoplex/evmapy_guard.py) waits for emulatorlauncher to
# end, then ends only an evmapy that still holds this pipe.
EL_PID=""; EL_START=""; EL_PIPES=""
if tr '\0' ' ' < "/proc/$PPID/cmdline" 2>/dev/null | grep -q emulatorlauncher; then
    EL_PID=$PPID
    EL_START="$(sed 's/.*) //' "/proc/$PPID/stat" 2>/dev/null | cut -d' ' -f20)"
    l="$(readlink "/proc/$PPID/fd/1" 2>/dev/null)"     # stdout only: stderr goes to a file
    case "$l" in pipe:\[*) EL_PIPES="$l" ;; esac
fi

PID=""
cleanup() {
    trap - EXIT INT TERM HUP
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill -TERM "$PID" 2>/dev/null
        for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
        kill -KILL "$PID" 2>/dev/null
    fi
    pkill -f 'input-ipc-server=/tmp/batoplex-mpv' 2>/dev/null
    pkill -f 'input-ipc-server=/tmp/batoplex-audio' 2>/dev/null
    pkill -f 'bpx-milkdrop.*--window' 2>/dev/null
    pkill -f 'client-name=batoplex-vis' 2>/dev/null
    pkill -f 'title=Batoplex Visualizer' 2>/dev/null
    NOW="$(batocera-resolution --screen "$OUT" currentMode 2>/dev/null | head -n1)"
    if [ -n "$SAVED" ] && [ "$NOW" != "$SAVED" ]; then
        echo "$(date '+%F %T') restore mode $NOW -> $SAVED" >> "$LOG"
        if ! batocera-resolution --screen "$OUT" setMode "$SAVED" 2>>"$LOG" \
                && [ -n "$OUT" ] && command -v xrandr >/dev/null 2>&1; then
            xrandr --output "$OUT" --mode "${SAVED%%.*}" 2>>"$LOG"
        fi
    fi
    if [ -n "$EL_PIPES" ] && [ -n "$EL_START" ]; then
        # No copy of our stdout or stderr: emulatorlauncher waits for them to close.
        DETACH=""; command -v setsid >/dev/null 2>&1 && DETACH=setsid
        (cd "$APP" && PYTHONPATH="$APP" exec $DETACH python3 -m batoplex.evmapy_guard \
            "$EL_PID" "$EL_START" "$EL_PIPES") </dev/null >>"$LOG" 2>&1 &
    fi
    echo "$(date '+%F %T') exit" >> "$LOG"
}
trap cleanup EXIT INT TERM HUP

# Run in the background and wait, so a TERM from Batocera runs cleanup at once.
cd "$APP" || exit 1
PYTHONPATH="$APP" python3 -m batoplex "$@" >> "$LOG" 2>&1 &
PID=$!
wait "$PID"
