#!/bin/bash
# Remove Batoplex from Batocera. Default: remove everything, including the Plex sign-in token.
#   bash /userdata/system/batoplex/uninstall.sh            # remove all
#   bash /userdata/system/batoplex/uninstall.sh --keep-data # keep settings and sign-in
# After a full removal, also remove the "Batoplex" device at https://plex.tv/devices
set -eu
APP=/userdata/system/batoplex
PORTS=/userdata/roms/ports
KEEP=0; [ "${1:-}" = "--keep-data" ] && KEEP=1

pkill -f 'input-ipc-server=/tmp/batoplex-mpv' 2>/dev/null || true
pkill -f 'input-ipc-server=/tmp/batoplex-audio' 2>/dev/null || true
pkill -f 'python3 -m batoplex' 2>/dev/null || true
pkill -f 'bpx-milkdrop.*--window' 2>/dev/null || true
pkill -f 'title=Batoplex Visualizer' 2>/dev/null || true
pkill -f 'client-name=batoplex-vis' 2>/dev/null || true
for f in "$PORTS/Batoplex.sh" "$PORTS/Batoplex.sh.keys"; do
    if [ -e "$f" ] && grep -q -e BATOPLEX-LAUNCHER -e "Close playback, then exit Batoplex" "$f"; then
        rm -f "$f"; echo "removed $f"
    fi
done
# Start on Boot (Settings) writes the boot game keys of ES; remove them only if they are ours.
CONF=/userdata/system/batocera.conf
if [ -f "$CONF" ] && grep -qx "global.bootgame.path=$PORTS/Batoplex.sh" "$CONF"; then
    sed -i -e '/^global\.bootgame\.path=/d' -e '/^global\.bootgame\.cmd=/d' "$CONF"
    echo "removed Start on Boot from $CONF"
fi
if [ "$KEEP" = 1 ]; then
    find "$APP" -mindepth 1 -maxdepth 1 ! -name data -exec rm -rf {} +
    echo "removed the app; kept $APP/data"
else
    rm -rf "$APP"; echo "removed $APP (settings, token, cache)"
fi
curl -s -m 5 http://localhost:1234/reloadgames >/dev/null 2>&1 || true
echo "Batoplex is removed."
