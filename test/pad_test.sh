#!/bin/bash
mkdir -p /out; rm -f /out/pad-*.png
Xvfb :99 -screen 0 641x480x24 -nolisten tcp >/dev/null 2>&1 & sleep 1
export DISPLAY=:99 SDL_AUDIODRIVER=dummy BATOPLEX_HOME=/tmp/padhome
python3 /app/test/mock_plex.py 32400 >/dev/null 2>&1 & sleep 1
mkdir -p $BATOPLEX_HOME; echo '{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"]}' > $BATOPLEX_HOME/settings.json
echo '{"server_token":"mock-token-123456","server_url":"http://127.0.0.1:32400","server_name":"Mock"}' > $BATOPLEX_HOME/auth.json
python3 /app/test/pad_test.py
grep -E "controller|joystick|play |stopped|return" $BATOPLEX_HOME/batoplex.log
