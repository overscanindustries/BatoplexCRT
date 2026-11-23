#!/bin/sh
# End-to-end desktop Linux test: Xvfb at the CRT mode size (641x480), mock Plex, real mpv.
set -e
OUT=/out; mkdir -p $OUT; rm -f $OUT/*.png /tmp/mock_plex.log
Xvfb :99 -screen 0 641x480x24 -nolisten tcp >/dev/null 2>&1 &
export DISPLAY=:99 SDL_AUDIODRIVER=dummy
export BATOPLEX_DIRTY_CHECK=1      # every band pass is checked against a full pass
sleep 1
MOCK_PEERS="mock2:Shared Movies:32401:mock2-token" python3 /app/test/mock_plex.py 32400 &
MOCK_PLAYLIST_WRITES=0 MOCK_LOG=/tmp/mock2.log MOCK_ID=mock2 MOCK_NAME="Shared Movies" MOCK_TOKEN=mock2-token MOCK_LAST_VIEWED=1800000000 \
    MOCK_MOVIES_TITLE="Shared Movies" python3 /app/test/mock_plex.py 32401 &
sleep 1
export BATOPLEX_HOME=/tmp/bphome; rm -rf $BATOPLEX_HOME; mkdir -p $BATOPLEX_HOME
cat > $BATOPLEX_HOME/auth.json <<J
{"client_id":"batoplex-test","server_token":"mock-token-123456","server_url":"http://127.0.0.1:32400","server_name":"Mock"}
J
cat > $BATOPLEX_HOME/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"]}
J
ROOTSHOT='xwd -root -silent | xwdtopnm 2>/dev/null | pnmtopng >'
cd /app
python3 -m batoplex --script "waitidle,wait:150,shot:$OUT/p01-home.png,ok,wait:90,shot:$OUT/p02-detail.png,ok,waitstart,wait:150,sh:$ROOTSHOT $OUT/p03-wide-play.png,sh:ps -eo args > $OUT/ps.txt,down,wait:40,sh:$ROOTSHOT $OUT/p03b-bar.png,back,wait:20,back,waitplay,wait:60,shot:$OUT/p04-detail-after.png,back,wait:60,shot:$OUT/p05-home-after.png,right,wait:30,ok,wait:90,shot:$OUT/p06-episodes.png,ok,waitstart,y,wait:150,sh:$ROOTSHOT $OUT/p07-43-play.png,waitstate:up_next,wait:60,sh:$ROOTSHOT $OUT/p07b-upnext.png,back,waitplay,wait:90,shot:$OUT/p08-episodes-after-eof.png,back,wait:90,shot:$OUT/p09-home-final.png,exit"
echo "app exit: $?"
cp $BATOPLEX_HOME/batoplex.log $OUT/app.log
cp /tmp/mock_plex.log $OUT/mock.log
grep -q "up next: " $OUT/app.log && echo "run1 up next: yes" || echo "run1 up next: no"
grep -q '"kind": "theme", "token": true' $OUT/mock.log && echo "run1 theme music: yes" || echo "run1 theme music: no"
pgrep -f batoplex-audio >/dev/null && echo "run1 audio mpv left running: YES" || echo "run1 audio mpv left running: no"
grep -q '"streams"' $OUT/mock.log && echo "run1 subtitle write-back: yes" || echo "run1 subtitle write-back: no"
grep '"streams"' $OUT/mock.log | head -3

# Run 2: SIGTERM during playback must send 'stopped' with the real time; a hand edit to
# settings.json made while the app runs must survive the exit.
: > /tmp/mock_plex.log
python3 -m batoplex --script "waitidle,wait:60,ok,wait:60,ok,waitstart,wait:150,sh:touch /tmp/playing" &
APP=$!
for i in $(seq 1 60); do [ -f /tmp/playing ] && break; sleep 0.5; done
python3 -c "import json;p='$BATOPLEX_HOME/settings.json';d=json.load(open(p));d['flicker_filter']='off';json.dump(d,open(p,'w'))"
kill -TERM $APP; wait $APP
echo "run2 exit: $?"
grep -E '"timeline"|"scrobble"' /tmp/mock_plex.log | tail -3
echo "run2 settings after exit: $(cat $BATOPLEX_HOME/settings.json | tr -d '\n ')"
pgrep -f batoplex-mpv >/dev/null && echo "run2 mpv left running: YES" || echo "run2 mpv left running: no"

# Run 3: an account token only. plex.tv is the mock; the owned server opens Home
# and the shared server connects in the background. No token in the log or in any URL.
H3=/tmp/bphome3; rm -rf $H3; mkdir -p $H3; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cat > $H3/auth.json <<J
{"client_id":"batoplex-test","user_token":"mock-account-token"}
J
cat > $H3/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":6},{"server":"mock","section":5},{"server":"mock2","section":6}],"seeded":true,"watch_seeded":true}}}
J
BATOPLEX_HOME=$H3 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,shot:$OUT/p09b-profiles.png,ok,wait:300,waitidle,wait:60,shot:$OUT/p10-home-account.png,hold,wait:20,shot:$OUT/p10b-hold-menu.png,back,wait:20,back,wait:30,shot:$OUT/p11-panel.png,down,down,menu,wait:20,shot:$OUT/p12-pin-menu.png,back,wait:10,down,down,down,down,ok,wait:120,shot:$OUT/p13-more.png,ok,wait:150,shot:$OUT/p14-library.png,rb,wait:150,shot:$OUT/p15-grid.png,down,down,rt,wait:90,shot:$OUT/p16-grid-letter.png,down,wait:30,shot:$OUT/p16b-grid-scrolled.png,exit"
echo "run3 exit: $?"
grep -q "server Shared Movies connected" $H3/batoplex.log && echo "run3 second server: yes" || echo "run3 second server: no"
grep -q '"path": "/hubs/continueWatching/items"' /tmp/mock2.log && echo "run3 merged continue watching: yes" || echo "run3 merged continue watching: no"
grep -q '"kind": "identity", "token": false' /tmp/mock2.log && echo "run3 probe without token: yes" || echo "run3 probe without token: no"
grep -q '"path": "/hubs/sections/6"' /tmp/mock_plex.log && echo "run3 library recommended: yes" || echo "run3 library recommended: no"
grep -q '"path": "/library/sections/6/all".*"start": 0, "size": 48' /tmp/mock_plex.log && echo "run3 grid page: yes" || echo "run3 grid page: no"
grep -q '"path": "/library/sections/6/firstCharacter"' /tmp/mock_plex.log && echo "run3 a-z counts: yes" || echo "run3 a-z counts: no"
grep -qE "mock-account-token|mock-token-123456|mock2-token" $H3/batoplex.log && echo "run3 token in log: YES" || echo "run3 token in log: no"
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run3 token in url: YES" || echo "run3 token in url: no"
grep -q '"kind": "avatar", "user": "u-owner", "token": false' /tmp/mock_plex.log && echo "run3 avatar without token: yes" || echo "run3 avatar without token: no"
python3 -c "import json;d=json.load(open('$H3/auth.json'));print('run3 auth servers:', sorted(d.get('servers', {})))"
cp $H3/batoplex.log $OUT/app3.log

# Run 4: Plex Home. The stored owner profile and three Home users: "Who's watching?" at launch.
# Mom has no PIN; the mock then sees Mom's server token. Run 5: Kid has PIN 1234; a wrong PIN
# first, then the right one.
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H3 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,shot:$OUT/p17-picker.png,right,ok,wait:300,waitidle,wait:90,shot:$OUT/p18-mom-home.png,exit"
echo "run4 exit: $?"
grep -q '"kind": "switch", "user": "u-mom"' /tmp/mock_plex.log && echo "run4 switch mom: yes" || echo "run4 switch mom: no"
grep -q '"kind": "get", "path": "/hubs/continueWatching/items", "user": "u-mom"' /tmp/mock_plex.log && echo "run4 mom token on server: yes" || echo "run4 mom token on server: no"
grep -q '"kind": "discover_list", "user": "u-mom"' /tmp/mock_plex.log && echo "run4 mom watchlist token: yes" || echo "run4 mom watchlist token: no"
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H3 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,right,right,ok,wait:30,d1,d1,d1,d1,wait:120,shot:$OUT/p19-pin-wrong.png,d1,d2,d3,d4,wait:300,waitidle,wait:90,shot:$OUT/p20-kid-home.png,exit"
echo "run5 exit: $?"
grep -q '"kind": "switch_denied", "user": "u-kid"' /tmp/mock_plex.log && echo "run5 wrong pin refused: yes" || echo "run5 wrong pin refused: no"
grep -q '"kind": "switch", "user": "u-kid"' /tmp/mock_plex.log && echo "run5 switch kid: yes" || echo "run5 switch kid: no"
grep -q "1234" $H3/batoplex.log && echo "run5 pin in log: YES" || echo "run5 pin in log: no"
grep -qE "user-u-kid|user-u-mom|mock-account-token" $H3/batoplex.log && echo "run5 token in log: YES" || echo "run5 token in log: no"
cp $H3/batoplex.log $OUT/app5.log

# Run 6: transcode. Home Streaming 480p converts on the local mock server: a
# decision with the target header, HLS with the token header on every segment, a seek that
# restarts the stream at a later offset, then 'stopped' and after it the transcode stop.
: > /tmp/mock_plex.log
python3 -c "import json;p='$BATOPLEX_HOME/settings.json';d=json.load(open(p));d['quality_home']='480p';json.dump(d,open(p,'w'))"
python3 -m batoplex --script "waitidle,wait:150,ok,wait:90,ok,waitstart,wait:200,sh:$ROOTSHOT $OUT/p21-transcode.png,right,right,wait:150,back,waitplay,wait:30,exit"
echo "run6 exit: $?"
grep -q '"kind": "tc_decision".*"target": true' /tmp/mock_plex.log && echo "run6 decision with target: yes" || echo "run6 decision with target: no"
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
starts = [r["offset"] for r in rows if r.get("kind") == "tc_start"]
print("run6 starts:", starts)
print("run6 restarts after seek: %s" % ("yes" if len(starts) >= 2 and starts[-1] > starts[0] else "no"))
segs = [r for r in rows if r.get("kind") == "tc_segment"]
print("run6 segments with token: %s" % ("yes" if segs and all(r["token"] for r in segs) else "no"))
bad = [r for r in rows if r.get("kind") == "unauthorized" and "/transcode/" in r.get("path", "")]
print("run6 segment without token: %s" % ("YES" if bad else "no"))
kinds = [(r.get("kind"), r.get("state")) for r in rows]
stopped = max([i for i, k in enumerate(kinds) if k == ("timeline", "stopped")], default=-1)
stop = max([i for i, k in enumerate(kinds) if k[0] == "tc_stop"], default=-1)
print("run6 stopped then stop: %s" % ("yes" if 0 <= stopped < stop else "no"))
print("run6 stopped at:", [r.get("time") for r in rows if r.get("kind") == "timeline" and r.get("state") == "stopped"])
P
pgrep -f batoplex-mpv >/dev/null && echo "run6 mpv left running: YES" || echo "run6 mpv left running: no"
cp $BATOPLEX_HOME/batoplex.log $OUT/app6.log

# Run 7: Search. Two servers with pins (the run 3 setup): Y on Home opens
# Search; four characters on four frames make one request per server (300 ms delay); the
# results open Detail; the query is saved as a recent search. No token in any URL.
H7=/tmp/bphome7; rm -rf $H7; mkdir -p $H7; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cat > $H7/auth.json <<J
{"client_id":"batoplex-test","user_token":"mock-account-token"}
J
cat > $H7/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":6},{"server":"mock","section":5},{"server":"mock2","section":6}],"seeded":true,"watch_seeded":true}}}
J
BATOPLEX_HOME=$H7 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:120,y,wait:30,shot:$OUT/p30-search-empty.png,char:w,char:i,char:d,char:e,wait:150,shot:$OUT/p31-search-results.png,down,wait:20,shot:$OUT/p32-search-focus.png,ok,wait:90,shot:$OUT/p33-search-detail.png,back,wait:30,back,wait:30,shot:$OUT/p34-home-after-search.png,exit"
echo "run7 exit: $?"
python3 - <<P
import json
for name, f in (("home", "/tmp/mock_plex.log"), ("shared", "/tmp/mock2.log")):
    rows = [json.loads(l) for l in open(f) if l.strip()]
    qs = [r.get("query") for r in rows if r.get("kind") == "search"]
    print("run7 %s searches: %s" % (name, "one" if qs == ["wide"] else qs))
d = json.load(open("$H7/settings.json"))
print("run7 recent:", d.get("profiles", {}).get("default", {}).get("recent_searches"))
P
grep -q "wide" $H7/batoplex.log && echo "run7 query in log: YES" || echo "run7 query in log: no"
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run7 token in url: YES" || echo "run7 token in url: no"
cp $H7/batoplex.log $OUT/app7.log

# Run 8: the screensaver. BATOPLEX_SAVER_S=8: it starts after 8 s with no
# input, draws posters from the pinned libraries (sort=random), and a key press wakes it
# without moving the focus.
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H7 BATOPLEX_SAVER_S=8 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:120,waitidle,wait:60,up,wait:20,shot:$OUT/p40-before-saver.png,wait:540,shot:$OUT/p41-saver.png,right,wait:30,shot:$OUT/p42-after-wake.png,exit"
echo "run8 exit: $?"
grep -q "screensaver on (bounce)" $H7/batoplex.log && echo "run8 saver on: yes" || echo "run8 saver on: no"
grep -q "screensaver off" $H7/batoplex.log && echo "run8 saver off: yes" || echo "run8 saver off: no"
grep -q "screensaver: [1-9]" $H7/batoplex.log && echo "run8 saver items: yes" || echo "run8 saver items: no"
python3 - <<P
import pygame
a, b = (pygame.image.load("$OUT/%s.png" % n) for n in ("p40-before-saver", "p42-after-wake"))
same = all(a.get_at((x, y)) == b.get_at((x, y)) for x in range(641) for y in range(80, 480))
print("run8 wake keeps the screen: %s" % ("yes" if same else "no"))   # the clock line can move on
P
cp $H7/batoplex.log $OUT/app8.log

# Run 10: music. A pinned music library: panel -> Music -> an artist ->
# an album -> Play. Two 6 s tracks play one after the other; reports carry the session
# header; the first track scrobbles; exit sends a last "stopped" and leaves no audio mpv.
H10=/tmp/bphome10; rm -rf $H10; mkdir -p $H10; : > /tmp/mock_plex.log
cat > $H10/auth.json <<J
{"client_id":"batoplex-test","user_token":"mock-account-token"}
J
cat > $H10/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":6},{"server":"mock","section":9}],"seeded":true,"watch_seeded":true}}}
J
BATOPLEX_HOME=$H10 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,ok,wait:150,shot:$OUT/p49-music-recommended.png,rb,wait:150,shot:$OUT/p50-music-library.png,ok,wait:120,shot:$OUT/p51-artist.png,ok,wait:120,shot:$OUT/p52-album.png,ok,waitmusic:playing,wait:60,shot:$OUT/p53-now-playing.png,wait:480,back,wait:30,shot:$OUT/p54-album-mark.png,exit"
echo "run10 exit: $?"
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
plays = [r["part"] for r in rows if r.get("kind") == "play" and str(r.get("file", "")).endswith(".flac")]
print("run10 parts:", sorted(set(plays)))
print("run10 two tracks: %s" % ("yes" if {"3204", "3205"} <= set(plays) else "no"))
print("run10 part token header: %s" % ("yes" if all(r.get("token") for r in rows if r.get("kind") == "play" and str(r.get("file", "")).endswith(".flac")) else "no"))
tl = [(r["ratingKey"], r["state"], r["session"]) for r in rows if r.get("kind") == "timeline" and str(r.get("ratingKey", "")).startswith("32")]
print("run10 timelines:", tl)
print("run10 track 1 stopped: %s" % ("yes" if ("3204", "stopped", True) in tl else "no"))
print("run10 session header: %s" % ("yes" if tl and all(s for _k, _s, s in tl) else "no"))
print("run10 last stopped at exit: %s" % ("yes" if tl and tl[-1][1] == "stopped" and tl[-1][0] != "3204" else "no"))
sc = [r.get("key") for r in rows if r.get("kind") == "scrobble"]
print("run10 scrobble track 1: %s" % ("yes" if "3204" in sc else "no"))
P
cat /tmp/mock_plex.log | grep -q TOKEN_IN_URL && echo "run10 token in url: YES" || echo "run10 token in url: no"
pgrep -f batoplex-audio >/dev/null && echo "run10 audio mpv left: YES" || echo "run10 audio mpv left: no"
cp $H10/batoplex.log $OUT/app10.log

# Run 11: Library Radio. Music library -> Recommended -> the Stations
# row -> A: the server makes a play queue (POST /playQueues, token in the header only).
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H10 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,ok,wait:150,down,wait:60,shot:$OUT/p55-stations.png,ok,waitmusic:playing,wait:60,shot:$OUT/p56-station-playing.png,exit"
echo "run11 exit: $?"
grep -q '"kind": "playqueue", "type": "audio", "uri": "server://[^"]*/com.plexapp.plugins.library/library/sections/9/stations/1"' /tmp/mock_plex.log && echo "run11 station queue: yes" || echo "run11 station queue: no"
grep -q TOKEN_IN_URL /tmp/mock_plex.log && echo "run11 token in url: YES" || echo "run11 token in url: no"

# Run 12: Radio. The user's radio.m3u lists the mock stream; panel -> Radio ->
# A -> Now Playing (LIVE). The stream request has no Plex token or client id; no timeline.
cat > $H10/radio.m3u <<M
#EXTM3U
#EXTINF:-1,Test Station
http://127.0.0.1:32400/radio/live.flac
#EXTINF:-1,Second Station
http://127.0.0.1:32400/radio/live.flac?b=2
M
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H10 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,wait:20,shot:$OUT/p57-panel-radio.png,ok,wait:60,shot:$OUT/p58-radio.png,ok,waitmusic:playing,wait:60,shot:$OUT/p59-radio-playing.png,exit"
echo "run12 exit: $?"
grep -q '"kind": "radio", "token": false, "client": false' /tmp/mock_plex.log && echo "run12 stream without plex headers: yes" || echo "run12 stream without plex headers: no"
grep -q '"kind": "radio", "token": true' /tmp/mock_plex.log && echo "run12 token to radio: YES" || echo "run12 token to radio: no"
grep -q '"kind": "timeline"' /tmp/mock_plex.log && echo "run12 timeline: YES" || echo "run12 timeline: no"

# Run 13: the visualizer on the radio from run 12, with a fake engine and a fake
# parec. Y opens it; Right -> "next"; RT changes the station (a title overlay); A twice; B
# closes it. Then the screensaver opens it while the music plays, and Up closes it.
mkdir -p /tmp/fakebin; printf '#!/bin/sh\nexec cat /dev/zero\n' > /tmp/fakebin/parec; chmod 755 /tmp/fakebin/parec
python3 -c "import json;p='$H10/settings.json';d=json.load(open(p));d['visualizer']='/app/test/fake_vis.py';json.dump(d,open(p,'w'))"
rm -f /tmp/fake_vis.log
PATH=/tmp/fakebin:$PATH BATOPLEX_HOME=$H10 BATOPLEX_SAVER_S=12 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,wait:20,ok,wait:60,ok,waitmusic:playing,wait:30,y,waitvis:running,wait:30,right,wait:10,rt,waitmusic:playing,wait:30,ok,wait:10,ok,wait:10,back,waitvis:idle,wait:20,shot:$OUT/p60-after-vis.png,waitvis:running,wait:30,up,waitvis:idle,wait:20,exit"
echo "run13 exit: $?"
grep -qx "next" /tmp/fake_vis.log && echo "run13 vis next: yes" || echo "run13 vis next: no"
grep -q "^overlay /tmp/batoplex-vis-" /tmp/fake_vis.log && echo "run13 vis overlay: yes" || echo "run13 vis overlay: no"
[ "$(grep -c '^start' /tmp/fake_vis.log)" = 2 ] && echo "run13 vis started twice: yes" || echo "run13 vis started twice: no"
grep -q "screensaver on (visualizer)" $H10/batoplex.log && echo "run13 saver started vis: yes" || echo "run13 saver started vis: no"
pgrep -f fake_vis.py >/dev/null && echo "run13 vis left running: YES" || echo "run13 vis left running: no"
pgrep -f "cat /dev/zero" >/dev/null && echo "run13 parec left running: YES" || echo "run13 parec left running: no"
# The engine fails at once: the mpv showcqt spectrum takes over.
FAKE_VIS_FAIL=1 PATH=/tmp/fakebin:$PATH BATOPLEX_HOME=$H10 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,wait:20,ok,wait:60,ok,waitmusic:playing,wait:30,y,wait:120,exit"
echo "run13b exit: $?"
grep -q "visualizer start (showcqt)" $H10/batoplex.log && echo "run13 fallback mpv: yes" || echo "run13 fallback mpv: no"
pgrep -f "Batoplex Visualizer" >/dev/null && echo "run13 showcqt left running: YES" || echo "run13 showcqt left running: no"
cp $H10/batoplex.log $OUT/app13.log
rm -f $H10/radio.m3u

# Run 14: Add Stations. No radio.m3u: panel -> More -> the Radio group's "Add
# Stations" -> Popular from the mock directory -> A adds the first station -> X previews it ->
# a search by name -> back to the panel, which now has Radio. Directory requests carry only the
# User-Agent and Accept headers; the stream is a continuous one with no length.
: > /tmp/mock_plex.log
BATOPLEX_HOME=$H10 BATOPLEX_RADIO_DIR=http://127.0.0.1:32400 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,wait:20,ok,wait:120,rt,rt,rt,rt,rt,rt,wait:20,shot:$OUT/p61-more-radio.png,ok,wait:120,shot:$OUT/p62-add-stations.png,down,down,down,wait:10,ok,wait:20,shot:$OUT/p63-added.png,x,waitmusic:playing,wait:30,char:r,char:o,char:c,char:k,wait:120,shot:$OUT/p64-station-search.png,back,wait:20,back,wait:30,shot:$OUT/p65-panel-radio.png,exit"
echo "run14 exit: $?"
python3 - <<P
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
d = [r for r in rows if r.get("kind") == "radiodir"]
print("run14 directory requests:", [(r["path"], r["name"]) for r in d])
print("run14 popular: %s" % ("yes" if any(r["path"] == "/json/stations/topclick/50" for r in d) else "no"))
print("run14 search: %s" % ("yes" if any(r["path"] == "/json/stations/search" and r["name"] == "rock" for r in d) else "no"))
print("run14 directory headers: %s" % ("yes" if d and all(r["ua"].startswith("BatoplexCRT/") and r["accept"] == "application/json" for r in d) else "no"))
print("run14 plex headers to directory: %s" % ("YES" if any(r["token"] or r["client"] for r in d) else "no"))
print("run14 click call: %s" % ("YES" if any(r["path"].startswith("/json/url") for r in d) else "no"))
s = [r for r in rows if r.get("kind") == "radio_loop"]
print("run14 stream without plex headers: %s" % ("yes" if s and not any(r["token"] or r["client"] for r in s) else "no"))
want = ("#EXTM3U\n#RADIOBROWSER:9a1c0000-0000-4000-8000-000000000001\n#EXTINF:-1,Test Jazz One\n"
        "http://127.0.0.1:32400/radio/loop.wav?s=1\n")
try:
    got = open("$H10/radio.m3u").read()
except OSError:
    got = None
print("run14 radio.m3u three lines: %s" % ("yes" if got == want else repr(got)))
P
grep -q "rock" $H10/batoplex.log && echo "run14 query in log: YES" || echo "run14 query in log: no"
pgrep -f batoplex-audio >/dev/null && echo "run14 audio mpv left: YES" || echo "run14 audio mpv left: no"
cp $H10/batoplex.log $OUT/app14.log
rm -f $H10/radio.m3u

# Run 15: Playlists. Three servers' worth of pins (run 3 setup): panel ->
# Playlists -> Music -> "Drive Mix" -> Play (the music queue); then Video -> "Road Trip" -> Play:
# Right (+30 s) shortens the first item; each item plays to its end and the next one starts with no card. The third entry lives on
# the shared server (sourceURI): its media and reports go there, not to the home server. Then
# the music library's Playlists tab asks only its own server, with its section id. B on
# Playlists reopens the panel on its row: Up Up is the music library.
H15=/tmp/bphome15; rm -rf $H15; mkdir -p $H15; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cat > $H15/auth.json <<J
{"client_id":"batoplex-test","user_token":"mock-account-token"}
J
cat > $H15/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":6},{"server":"mock","section":9},{"server":"mock2","section":6}],"seeded":true,"watch_seeded":true}}}
J
BATOPLEX_HOME=$H15 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,wait:20,shot:$OUT/p70-panel-playlists.png,ok,wait:150,rb,wait:150,shot:$OUT/p71-playlists-music.png,ok,wait:120,shot:$OUT/p72-playlist.png,ok,waitmusic:playing,wait:30,back,wait:20,back,wait:20,lb,wait:90,shot:$OUT/p73-playlists-video.png,ok,wait:120,shot:$OUT/p74-playlist-video.png,ok,waitstart,wait:60,right,waitstate:starting,waitstart,waitstate:starting,waitstart,wait:30,right,wait:60,back,waitplay,wait:60,shot:$OUT/p75-playlist-after.png,back,wait:30,back,wait:30,up,up,ok,wait:150,rb,rb,rb,wait:150,shot:$OUT/p76-music-playlists-tab.png,exit"
echo "run15 exit: $?"
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
rows2 = [json.loads(l) for l in open("/tmp/mock2.log") if l.strip()]
plays = [r["part"] for r in rows if r.get("kind") == "play" and str(r.get("file", "")).endswith(".flac")]
print("run15 music playlist plays: %s" % ("yes" if "3204" in plays else plays))
tl = [r["ratingKey"] for r in rows if r.get("kind") == "timeline" and r.get("state") == "playing"]
order = [k for i, k in enumerate(tl) if k in ("101", "2001") and k not in tl[:i]]
print("run15 video timelines:", tl)
print("run15 video in order: %s" % ("yes" if order == ["101", "2001"] else "no"))
tl2 = [r["ratingKey"] for r in rows2 if r.get("kind") == "timeline"]
print("run15 entry on its own server: %s" % ("yes" if "111" in tl2 and "111" not in [r.get("ratingKey") for r in rows if r.get("kind") == "timeline"] else "no"))
lists = [(r.get("type"), r.get("section")) for r in rows if r.get("kind") == "playlist_list"]
lists2 = [(r.get("type"), r.get("section")) for r in rows2 if r.get("kind") == "playlist_list"]
print("run15 lists:", lists, lists2)
print("run15 library tab section: %s" % ("yes" if ("audio", "9") in lists and ("audio", "9") not in lists2 else "no"))
P
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run15 token in url: YES" || echo "run15 token in url: no"
grep -qE "Road Trip|Drive Mix" $H15/batoplex.log && echo "run15 names in log: YES" || echo "run15 names in log: no"
pgrep -f batoplex-mpv >/dev/null && echo "run15 mpv left running: YES" || echo "run15 mpv left running: no"
cp $H15/batoplex.log $OUT/app15.log

# Run 16: Add to Playlist and the local video queue. Music -> an artist
# -> an album -> a track -> Start -> Add to Playlist -> New Playlist; the name is typed on the
# keyboard ("qzx", user text: never logged) -> Create. Then two movies on Home -> Add to Queue;
# the panel's Play Queue row opens the queue; Play starts the first one.
H16=/tmp/bphome16; rm -rf $H16; mkdir -p $H16; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cp $H15/auth.json $H15/settings.json $H16/
BATOPLEX_HOME=$H16 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,ok,wait:150,rb,wait:150,ok,wait:120,ok,wait:120,right,right,wait:10,menu,ok,wait:60,shot:$OUT/p77-add-to-playlist.png,ok,wait:30,x,x,x,x,x,x,x,x,x,x,x,x,char:q,char:z,char:x,down,wait:10,shot:$OUT/p78-name.png,ok,wait:60,back,wait:20,back,wait:20,back,wait:20,up,up,up,ok,wait:120,down,wait:30,menu,down,down,ok,wait:10,right,wait:30,menu,down,down,ok,wait:20,back,wait:20,down,down,down,down,down,down,ok,wait:60,shot:$OUT/p79-queue.png,ok,waitstart,wait:60,back,waitplay,wait:30,exit"
echo "run16 exit: $?"
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
c = [r for r in rows if r.get("kind") == "playlist_create"]
print("run16 create: %s" % ("yes" if [(r["type"], r["n"]) for r in c] == [("audio", 1)] else c))
tl = [r["ratingKey"] for r in rows if r.get("kind") == "timeline" and r.get("state") == "playing"]
print("run16 queue plays: %s" % ("yes" if tl else "no"))
P
grep -q "Added to Queue • 2 items" $H16/batoplex.log && echo "run16 queue of two: yes" || echo "run16 queue of two: no"
grep -q "qzx" $H16/batoplex.log && echo "run16 name in log: YES" || echo "run16 name in log: no"
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run16 token in url: YES" || echo "run16 token in url: no"
cp $H16/batoplex.log $OUT/app16.log

# Run 17: editing. "Drive Mix" on the home server: Move the first track
# one place down, Remove it, Rename (a "z" added), Delete Playlist (the confirm dialog). One
# mock log line each, for the right playlistItemID. Then "Drive Mix" on the shared server, which
# refuses playlist writes: Remove fails, the toast shows, the list loads again.
H17=/tmp/bphome17; rm -rf $H17; mkdir -p $H17; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cp $H15/auth.json $H15/settings.json $H17/
BATOPLEX_HOME=$H17 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:60,back,wait:20,down,down,down,down,down,ok,wait:150,rb,wait:150,ok,wait:120,right,right,menu,down,down,ok,down,wait:10,shot:$OUT/p80-move.png,ok,wait:30,menu,down,down,down,ok,wait:30,left,menu,ok,wait:20,char:z,down,ok,wait:30,menu,down,ok,wait:20,shot:$OUT/p81-confirm.png,left,ok,wait:120,shot:$OUT/p82-after-delete.png,ok,wait:120,right,right,menu,down,down,down,ok,wait:90,shot:$OUT/p83-refused.png,exit"
echo "run17 exit: $?"
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
rows2 = [json.loads(l) for l in open("/tmp/mock2.log") if l.strip()]
k = [r for r in rows if r.get("kind", "").startswith("playlist_") and r["kind"] not in ("playlist_list", "playlist_items")]
print("run17 writes:", [(r["kind"], r.get("item"), r.get("after")) for r in k])
names = [r["kind"] for r in k]
print("run17 one call each: %s" % ("yes" if names == ["playlist_move", "playlist_remove", "playlist_rename", "playlist_delete"] else "no"))
mv = k[0] if k else {}
print("run17 right entries: %s" % ("yes" if len(k) == 4 and k[1]["item"] == mv.get("item") and all(r["id"] == "802" for r in k) else "no"))
print("run17 shared refused: %s" % ("yes" if any(r.get("kind") == "playlist_refused" for r in rows2) else "no"))
P
grep -q "toast: Can't change this playlist" $H17/batoplex.log && echo "run17 failed write toast: yes" || echo "run17 failed write toast: no"
grep -qE "Drive Mix" $H17/batoplex.log && echo "run17 names in log: YES" || echo "run17 names in log: no"
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run17 token in url: YES" || echo "run17 token in url: no"
cp $H17/batoplex.log $OUT/app17.log

# Run 18: the detail page rows. A fresh mock (its own watch state): the first movie,
# Down to Cast & Crew, Extras, More Like This; the trailer plays from the start with Home
# Streaming at 480p (an extra never converts) and sends no timeline and no scrobble; a related
# title opens its page; a person opens the person page.
H18=/tmp/bphome18; rm -rf $H18; mkdir -p $H18; : > /tmp/mock18.log
MOCK_LOG=/tmp/mock18.log python3 /app/test/mock_plex.py 32402 &
MOCK18=$!
sleep 1
cat > $H18/auth.json <<J
{"client_id":"batoplex-test","server_token":"mock-token-123456","server_url":"http://127.0.0.1:32402","server_name":"Mock"}
J
cat > $H18/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],"quality_home":"480p"}
J
BATOPLEX_HOME=$H18 python3 -m batoplex --script "waitidle,wait:150,ok,wait:90,down,wait:40,shot:$OUT/p90-cast.png,down,wait:60,shot:$OUT/p91-extras.png,down,wait:60,shot:$OUT/p92-related.png,up,wait:20,ok,waitstart,wait:150,back,waitplay,wait:60,down,wait:20,ok,wait:90,shot:$OUT/p93-related-page.png,back,wait:30,up,up,wait:20,ok,wait:150,shot:$OUT/p94-person.png,exit"
echo "run18 exit: $?"
kill $MOCK18 2>/dev/null || true
python3 - <<'P'
import json
rows = [json.loads(l) for l in open("/tmp/mock18.log") if l.strip()]
plays = [r.get("part") for r in rows if r.get("kind") == "play"]
print("run18 trailer played: %s" % ("yes" if "1501" in plays else plays))
rep = [r for r in rows if r.get("kind") in ("timeline", "scrobble", "tc_decision")]
print("run18 extra reports: %s" % ("YES %s" % rep if rep else "no"))
paths = [r.get("path") for r in rows if r.get("kind") == "get"]
print("run18 rows asked: %s" % ("yes" if "/library/metadata/101/extras" in paths and "/library/metadata/101/related" in paths else "no"))
print("run18 person titles: %s" % ("yes" if "/library/sections/5/all" in paths else "no"))
P
grep -q TOKEN_IN_URL /tmp/mock18.log && echo "run18 token in url: YES" || echo "run18 token in url: no"
cp $H18/batoplex.log $OUT/app18.log
# Run 19: Live TV. A mock with a 50 h show library: More > Set Up Live
# TV, Make Channels for Me, the suggested show turned on, Start (Save): the guide. A tunes, Up
# changes the channel, RB opens the guide over the picture, Up A tunes from it, LB is the last
# channel, B B to the guide. No timeline or scrobble. Run 19b: a second start on the same day fetches no pool.
MOCK_LIVE=1 MOCK_LOG=/tmp/mock3.log python3 /app/test/mock_plex.py 32402 &
sleep 2
H19=/tmp/bphome19; rm -rf $H19; mkdir -p $H19; : > /tmp/mock3.log
cat > $H19/auth.json <<J
{"client_id":"batoplex-test","server_token":"mock-token-123456","server_url":"http://127.0.0.1:32402","server_name":"Mock","server_id":"mock"}
J
cat > $H19/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":7}],"seeded":true,"watch_seeded":true}}}
J
BATOPLEX_HOME=$H19 python3 -m batoplex \
    --script "waitidle,wait:150,back,wait:20,down,down,down,down,ok,wait:90,rt,rt,up,ok,wait:30,ok,wait:300,down,ok,menu,wait:600,shot:$OUT/l90-guide.png,ok,waitstart,wait:150,sh:$ROOTSHOT $OUT/l91-channel.png,up,wait:20,sh:$ROOTSHOT $OUT/l92-banner.png,wait:240,rb,wait:60,sh:$ROOTSHOT $OUT/l93-guide-over.png,up,wait:10,ok,wait:120,lb,waitstart,wait:120,back,wait:10,back,waitplay,wait:60,shot:$OUT/l94-guide-end.png,back,wait:30,exit"
echo "run19 exit: $?"
python3 - <<'P'
import json
doc = json.load(open("/tmp/bphome19/channels.json"))
print("run19 channels saved: %d" % len(doc["channels"]))
log = open("/tmp/bphome19/batoplex.log").read()
plays = [l for l in log.splitlines() if " play Pattern " in l]
print("run19 tunes: %s" % ("yes" if len(plays) >= 3 else len(plays)))   # tune, Up, guide or LB
print("run19 guide over: %s" % ("yes" if "guide over the picture" in log else "no"))
rows = [json.loads(l) for l in open("/tmp/mock3.log") if l.strip()]
print("run19 timeline: %s" % ("yes" if any(r["kind"] in ("timeline", "scrobble") for r in rows) else "no"))
print("run19 pool fetched: %s" % ("yes" if any(r.get("path") == "/library/metadata/7000/allLeaves" for r in rows) else "no"))
print("run19 guide opened: %s" % ("yes" if "guide open" in log else "no"))
P
grep -q TOKEN_IN_URL /tmp/mock3.log && echo "run19 token in url: YES" || echo "run19 token in url: no"
pgrep -f batoplex-mpv >/dev/null && echo "run19 mpv left running: YES" || echo "run19 mpv left running: no"
cp $H19/batoplex.log $OUT/app19.log
: > /tmp/mock3.log
BATOPLEX_HOME=$H19 python3 -m batoplex --script "waitidle,wait:480,exit"
echo "run19b exit: $?"
grep -qE '"/library/metadata/7000/allLeaves"|"/library/sections/7/all"' /tmp/mock3.log && echo "run19b pool request: YES" || echo "run19b pool request: no"
grep "lineup:" $H19/batoplex.log | tail -2

# Run 20: Watchlist. Two servers with pins and an account token. The owner's list
# has the title on both mocks (113 here, 125 on mock2), two movies and the show of this mock, and
# one title on no server: the grid shows 4 titles, not 5. The shared title opens and plays the
# home server's copy; Remove in the grid; Add on a movie page of the shared server; the grid
# shows it first. Unpin from the panel, open from More, pin again.
H20=/tmp/bphome20; rm -rf $H20; mkdir -p $H20; : > /tmp/mock_plex.log; : > /tmp/mock2.log
cat > $H20/auth.json <<J
{"client_id":"batoplex-test","user_token":"mock-account-token"}
J
cat > $H20/settings.json <<J
{"mpv_extra":["--vo=x11","--hwdec=no","--ao=null"],
 "profiles":{"default":{"pins":[{"server":"mock","section":6},{"server":"mock","section":5},{"server":"mock2","section":6}],"seeded":true}}}
J
BATOPLEX_HOME=$H20 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:120,back,wait:20,down,down,down,down,down,wait:10,shot:$OUT/w00-panel.png,ok,wait:180,shot:$OUT/w01-grid.png,ok,wait:90,shot:$OUT/w02-detail.png,ok,waitstart,wait:90,back,waitplay,wait:60,back,wait:30,menu,wait:10,shot:$OUT/w03-menu.png,down,ok,wait:90,shot:$OUT/w04-removed.png,lb,wait:150,ok,wait:120,right,right,wait:10,shot:$OUT/w05-button.png,ok,wait:90,shot:$OUT/w06-added.png,back,wait:30,back,wait:30,down,ok,wait:150,shot:$OUT/w07-grid-added.png,back,wait:30,menu,wait:10,ok,wait:30,shot:$OUT/w08-unpinned.png,down,ok,wait:90,shot:$OUT/w09-more.png,rt,rt,up,up,ok,wait:120,shot:$OUT/w10-from-more.png,back,wait:30,x,wait:20,shot:$OUT/w11-more-pinned.png,exit"
echo "run20 exit: $?"
python3 - <<P
import json
rows = [json.loads(l) for l in open("/tmp/mock_plex.log") if l.strip()]
rows2 = [json.loads(l) for l in open("/tmp/mock2.log") if l.strip()]
log = open("$H20/batoplex.log").read()
counts = [l.split("watchlist screen: ")[1].split()[0] for l in log.splitlines() if "watchlist screen: " in l]
print("run20 counts:", counts)
print("run20 four titles: %s" % ("yes" if "4" in counts else "no"))
tl = [(r.get("ratingKey"), r.get("user")) for r in rows if r.get("kind") == "timeline"]
print("run20 home copy plays: %s" % ("yes" if ("113", "owner") in tl else tl))
w = [(r["kind"], r.get("key")) for r in rows if r.get("kind") in ("watchlist_add", "watchlist_remove")]
print("run20 writes:", w)
print("run20 one remove one add: %s" % ("yes" if w == [("watchlist_remove", "wlboth"), ("watchlist_add", "mock2-101")] else "no"))
disc = {r["kind"] for r in rows if r.get("kind", "").startswith("discover")}
print("run20 discover calls only the three: %s" % ("yes" if disc == {"discover_list"} else disc))
print("run20 guid lookups: %s" % ("yes" if any(r.get("kind") == "guid_lookup" for r in rows2) else "no"))
d = json.load(open("$H20/settings.json"))
pins = [e["server"] for e in d["profiles"]["default"]["pins"]]
print("run20 pinned again: %s" % ("yes" if pins[-1:] == ["watchlist"] else pins))
P
cat /tmp/mock_plex.log /tmp/mock2.log | grep -q TOKEN_IN_URL && echo "run20 token in url: YES" || echo "run20 token in url: no"
grep -qE "Nowhere Movie|Filler Movie 2" $H20/batoplex.log && echo "run20 titles in log: YES" || echo "run20 titles in log: no"
grep -qE "mock-account-token|mock-token-123456|mock2-token" $H20/batoplex.log && echo "run20 token in log: YES" || echo "run20 token in log: no"
ls $H20/cache/watchlist/*.json >/dev/null 2>&1 && echo "run20 cache file: yes" || echo "run20 cache file: no"
cp $H20/batoplex.log $OUT/app20.log

# Run 20b: the home server does not answer (plex.tv and Discover still do). The title on both
# servers opens and plays the shared server's copy, with that server's token.
pkill -f "mock_plex.py 32400" || true
sleep 1
: > /tmp/mock_plex.log; : > /tmp/mock2.log
MOCK_SERVER_DOWN=1 MOCK_PEERS="mock2:Shared Movies:32401:mock2-token" python3 /app/test/mock_plex.py 32400 &
sleep 1
BATOPLEX_HOME=$H20 BATOPLEX_PLEX_TV=http://127.0.0.1:32400 python3 -m batoplex \
    --script "waitidle,wait:150,ok,wait:300,waitidle,wait:120,back,wait:20,down,down,down,down,down,ok,wait:180,shot:$OUT/w12-grid-home-down.png,ok,wait:90,ok,waitstart,wait:90,back,waitplay,wait:30,exit"
echo "run20b exit: $?"
python3 - <<'P'
import json
rows2 = [json.loads(l) for l in open("/tmp/mock2.log") if l.strip()]
tl = [(r.get("ratingKey"), r.get("user")) for r in rows2 if r.get("kind") == "timeline"]
print("run20b second server copy plays: %s" % ("yes" if ("125", "owner") in tl else tl))
P
cp $H20/batoplex.log $OUT/app20b.log
