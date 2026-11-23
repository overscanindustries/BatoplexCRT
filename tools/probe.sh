#!/bin/bash
# Read-only probe of the Batocera PC for Batoplex. It changes nothing.
# Run on the Batocera PC:  bash probe.sh [plex-server-url] > /userdata/system/batoplex-probe.txt 2>&1
# Example: bash probe.sh http://192.168.1.20:32400
export DISPLAY="${DISPLAY:-:0}"
echo "== version";   cat /usr/share/batocera/batocera.version 2>/dev/null; uname -m
echo "== python";    python3 --version; python3 -c 'import pygame; print("pygame", pygame.version.ver, "SDL", pygame.get_sdl_version())'
echo "== pygame controller"; python3 -c 'from pygame._sdl2 import controller; print("ok")'
echo "== mpv";       command -v mpv && mpv --version | head -3
echo "== mpv vo/hwdec"; mpv --vo=help 2>/dev/null | head -12; mpv --hwdec=help 2>/dev/null | grep -i -E "vaapi|auto" | head -6
echo "== vaapi";     command -v vainfo >/dev/null && vainfo 2>&1 | head -12 || echo "vainfo not present"
echo "== xrandr";    xrandr 2>/dev/null | head -8
echo "== mode";      batocera-resolution --screen DVI-I-1 currentMode 2>&1 | head -2
echo "== conf";      grep -E '^(global.videooutput|es.resolution|es.customsargs)=' /userdata/system/batocera.conf
echo "== pads";      cat /proc/bus/input/devices 2>/dev/null | grep -E '^N: Name' | head -12
echo "== ports";     ls /userdata/roms/ports | head -20
echo "== evmapy";    ls /userdata/system/configs/evmapy 2>/dev/null; ls /usr/share/evmapy 2>/dev/null | grep -E 'ports|sh|any|hotkeys'
if [ -n "${1:-}" ]; then
    echo "== plex";  curl -s -m 4 -o /dev/null -w '%{http_code}\n' "${1%/}/identity"
fi
