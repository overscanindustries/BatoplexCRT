#!/bin/bash
# Install Batoplex on Batocera. Run as root on the Batocera PC from the unpacked bundle:
#   cd /userdata/system/batoplex-bundle && bash install.sh
# Writes only:
#   /userdata/system/batoplex/            app, fonts, sounds/ (menu sounds), art/ (screen savers), native/ (visualizer),
#                                         uninstall.sh,
#                                         data/ (settings, token, cache, visualizer/ presets)
#   /userdata/roms/ports/Batoplex.sh      menu launcher
#   /userdata/roms/ports/Batoplex.sh.keys Hotkey+Start exit
# Settings > Start on Boot (off by default) sets global.bootgame.* in batocera.conf.
# Nothing in /boot, /usr, /etc, or the overlay changes. Remove with
#   bash /userdata/system/batoplex/uninstall.sh
set -eu
SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/userdata/system/batoplex
PORTS=/userdata/roms/ports

[ -d /userdata/system ] || { echo "This is not a Batocera system (/userdata/system is missing)."; exit 1; }
for f in "$PORTS/Batoplex.sh" "$PORTS/Batoplex.sh.keys"; do
    if [ -e "$f" ] && ! grep -q -e BATOPLEX-LAUNCHER -e "Close playback, then exit Batoplex" "$f"; then
        echo "Refusing to overwrite $f: it was not written by Batoplex."; exit 1
    fi
done

echo "Checking the system (read-only)..."
python3 -c 'import sys; assert sys.version_info >= (3, 9)' || { echo "Python 3.9+ is required."; exit 1; }
python3 -c 'import pygame' 2>/dev/null || { echo "pygame is missing. Batocera 43 ships it; stop."; exit 1; }
command -v mpv >/dev/null || echo "WARNING: mpv was not found. Browsing works, playback does not."

echo "Installing to $APP ..."
mkdir -p "$APP" "$APP/data" "$PORTS"
chmod 700 "$APP/data"
rm -rf "$APP/batoplex.new"
cp -r "$SRC/batoplex" "$APP/batoplex.new"
find "$APP/batoplex.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$APP/batoplex" && mv "$APP/batoplex.new" "$APP/batoplex"
mkdir -p "$APP/fonts" && cp "$SRC"/fonts/* "$APP/fonts/"
if [ -d "$SRC/art" ]; then
    rm -rf "$APP/art" && cp -r "$SRC/art" "$APP/art"     # screen saver art
fi
if [ -d "$SRC/sounds/hearth" ]; then
    rm -rf "$APP/sounds" && mkdir -p "$APP/sounds" && cp -r "$SRC/sounds/hearth" "$APP/sounds/"
fi
if [ -d "$SRC/native" ]; then
    rm -rf "$APP/native.new"
    cp -r "$SRC/native" "$APP/native.new"
    if [ "$(uname -m)" != "x86_64" ]; then
        rm -f "$APP/native.new/bpx-milkdrop"   # an x86_64 build: of no use on this CPU
    fi
    [ -f "$APP/native.new/bpx-milkdrop" ] && chmod 755 "$APP/native.new/bpx-milkdrop"
    rm -rf "$APP/native" && mv "$APP/native.new" "$APP/native"
fi
mkdir -p "$APP/data/visualizer"
command -v parec >/dev/null || echo "NOTE: parec was not found. The visualizer will not follow the music."
cp "$SRC/uninstall.sh" "$SRC/VERSION" "$APP/"
[ -f "$SRC/LICENSE" ] && cp "$SRC/LICENSE" "$APP/"
cp "$SRC/launcher/Batoplex.sh" "$PORTS/Batoplex.sh"
cp "$SRC/launcher/Batoplex.sh.keys" "$PORTS/Batoplex.sh.keys"
chmod 755 "$PORTS/Batoplex.sh"
printf '%s\n' "$APP" "$PORTS/Batoplex.sh" "$PORTS/Batoplex.sh.keys" > "$APP/INSTALLED"

echo "Refreshing the game list..."
curl -s -m 5 http://localhost:1234/reloadgames >/dev/null 2>&1 || echo "Reload the game lists from the menu (or restart EmulationStation)."
echo "Done. Open Ports > Batoplex."
