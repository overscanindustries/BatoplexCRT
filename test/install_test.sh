#!/bin/bash
# Simulated Batocera layout: install, launch from the Ports script, Alt+F4 during playback
# (what Hotkey+Start sends), Alt+F4 in the app, TERM during playback, uninstall.
set -u
OUT=/out/install; rm -rf $OUT; mkdir -p $OUT
mkdir -p /userdata/system /userdata/roms/ports
echo "global.videooutput=screen" > /userdata/system/batocera.conf
echo "641x480.60.00" > /tmp/mode
cat > /usr/local/bin/batocera-resolution <<'S'
#!/bin/sh
[ "$1" = "--screen" ] && shift 2
case "$1" in currentMode) cat /tmp/mode;; setMode) echo "$2" > /tmp/mode; echo "setMode $2" >> /tmp/res.log;; esac
S
chmod +x /usr/local/bin/batocera-resolution
Xvfb :99 -screen 0 641x480x24 -nolisten tcp >/dev/null 2>&1 & sleep 1
export DISPLAY=:99 SDL_AUDIODRIVER=dummy
python3 /app/test/mock_plex.py 32400 >/dev/null 2>&1 & sleep 1
TGZ=$(ls /app/dist/batoplex-*.tar.gz | tail -1); B=$(basename "$TGZ" .tar.gz)
cd /tmp && tar xzf "$TGZ" && bash "$B/install.sh" | tee $OUT/install.txt
echo '{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"]}' > /userdata/system/batoplex/data/settings.json
echo '{"server_token":"mock-token-123456","server_url":"http://127.0.0.1:32400","server_name":"Mock"}' \
    > /userdata/system/batoplex/data/auth.json
ls -la /userdata/roms/ports /userdata/system/batoplex /userdata/system/batoplex/data > $OUT/tree.txt
echo "hearth sounds: $(ls /userdata/system/batoplex/sounds/hearth/*.wav 2>/dev/null | wc -l)" >> $OUT/result.txt
[ -f /userdata/system/batoplex/fonts/OFL-NotoSansJP.txt ] && [ -f /userdata/system/batoplex/fonts/NotoSansJP-Rain.ttf ] \
    && echo "rain font and license: yes" >> $OUT/result.txt || echo "rain font and license: no" >> $OUT/result.txt

mpvcount() { pgrep -f 'input-ipc-server=/tmp/batoplex-mpv' | wc -l; }
# Run 1: open a movie, play, Alt+F4 to mpv (returns to app), Alt+F4 to app (exits).
/userdata/roms/ports/Batoplex.sh --script "waitidle,wait:120,ok,wait:60,ok,waitstart,wait:90,sh:echo playing > /tmp/r1" &
L=$!
for i in $(seq 1 60); do [ -f /tmp/r1 ] && break; sleep 0.5; done
echo "run1 mpv while playing: $(mpvcount)" >> $OUT/result.txt
xdotool search --name Batoplex 2>/dev/null | tail -1 > /dev/null
xdotool key alt+F4; sleep 3
echo "run1 mpv after alt+F4: $(mpvcount)" >> $OUT/result.txt
pgrep -f 'python3 -m batoplex' >/dev/null && echo "run1 app still open: yes" >> $OUT/result.txt
xdotool key alt+F4; sleep 3
pgrep -f 'python3 -m batoplex' >/dev/null && echo "run1 app after 2nd alt+F4: open" >> $OUT/result.txt || echo "run1 app after 2nd alt+F4: exited" >> $OUT/result.txt
wait $L; echo "run1 launcher exit: $?" >> $OUT/result.txt

# Run 2: TERM to the launcher during playback (what a Batocera kill does), after a mode change.
/userdata/roms/ports/Batoplex.sh --script "waitidle,wait:120,ok,wait:60,ok,waitstart,wait:90,sh:echo playing > /tmp/r2" &
L=$!
for i in $(seq 1 60); do [ -f /tmp/r2 ] && break; sleep 0.5; done
echo "320x240.60.00" > /tmp/mode
echo "run2 mpv while playing: $(mpvcount)" >> $OUT/result.txt
kill -TERM $L; sleep 4
echo "run2 mpv after TERM: $(mpvcount)" >> $OUT/result.txt
pgrep -f 'python3 -m batoplex' >/dev/null && echo "run2 app after TERM: open" >> $OUT/result.txt || echo "run2 app after TERM: exited" >> $OUT/result.txt
echo "run2 mode after: $(cat /tmp/mode)" >> $OUT/result.txt
cat /tmp/res.log >> $OUT/result.txt 2>/dev/null
cp /userdata/system/batoplex/data/launcher.log $OUT/ 2>/dev/null
stat -c '%a %n' /userdata/system/batoplex/data /userdata/system/batoplex/data/auth.json >> $OUT/result.txt

# Refuse to overwrite a foreign file, then uninstall
bash /userdata/system/batoplex/uninstall.sh >> $OUT/result.txt
echo "echo foreign" > /userdata/roms/ports/Batoplex.sh
bash "/tmp/$B/install.sh" >> $OUT/result.txt 2>&1; echo "install over foreign file exit: $?" >> $OUT/result.txt
rm /userdata/roms/ports/Batoplex.sh
echo "after uninstall:" >> $OUT/result.txt; ls -la /userdata/roms/ports /userdata/system >> $OUT/result.txt
