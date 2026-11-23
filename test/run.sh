#!/bin/sh
# Build the bundle, run the Docker tests, and fail if any expected result is missing.
#   sh test/run.sh            (from the repo root; needs Docker)
# Generates the test media first if it is missing (needs ffmpeg).
set -eu
cd "$(dirname "$0")/.."
[ -f test/media/4x3.mkv ] && [ -f test/media/16x9.mkv ] || sh tools/make-test-media.sh
# HLS for the mock transcoder: 4 s MPEG-TS segments of 16x9.mkv.
if [ ! -f test/media/hls/index.m3u8 ]; then
    mkdir -p test/media/hls
    ffmpeg -loglevel error -y -i test/media/16x9.mkv -c:v libx264 -preset ultrafast -g 48 \
        -c:a aac -f hls -hls_time 4 -hls_playlist_type vod \
        -hls_segment_filename test/media/hls/seg%03d.ts test/media/hls/index.m3u8
fi
# Music tones for the mock: two 6 s FLAC files.
for n in 1 2; do
    [ -f test/media/tone$n.flac ] || ffmpeg -loglevel error -y -f lavfi \
        -i "sine=frequency=$((330 * n)):duration=6" -ac 2 -ar 44100 test/media/tone$n.flac
done
rm -rf dist test/out && mkdir -p test/out
sh tools/build.sh
docker build -q -t batoplex-test test >/dev/null
run() { docker run --rm -v "$PWD":/app -v "$PWD/test/out":/out batoplex-test "$@"; }

run sh -c "cd /app && python3 -m unittest discover -s test/unit" > test/out/unit.txt 2>&1 || true
run sh /app/test/e2e.sh > test/out/e2e.txt 2>&1 || true
run bash /app/test/install_test.sh > test/out/install.txt 2>&1 || true

fail=0
expect() {  # file pattern
    if grep -qF -- "$2" "$1"; then echo "ok    $2"; else echo "FAIL  $2  (in $1)"; fail=1; fi
}
U=test/out/unit.txt
if tail -3 $U | grep -qx "OK"; then echo "ok    unit tests: $(grep '^Ran' $U)"; else echo "FAIL  unit tests  (in $U)"; fail=1; fi
E=test/out/e2e.txt
expect $E "app exit: 0"
expect $E "run2 exit: 0"
expect $E '"state": "stopped"'
expect $E '"flicker_filter":"off"'
expect $E "run2 mpv left running: no"
expect $E "run3 exit: 0"
expect $E "run3 second server: yes"
expect $E "run3 merged continue watching: yes"
expect $E "run3 probe without token: yes"
expect $E "run3 token in log: no"
expect $E "run3 token in url: no"
expect $E "run3 avatar without token: yes"
expect $E "run3 auth servers: ['mock', 'mock2']"
expect $E "run3 library recommended: yes"
expect $E "run3 grid page: yes"
expect $E "run3 a-z counts: yes"
expect $E "run4 exit: 0"
expect $E "run4 switch mom: yes"
expect $E "run4 mom token on server: yes"
expect $E "run5 exit: 0"
expect $E "run5 wrong pin refused: yes"
expect $E "run5 switch kid: yes"
expect $E "run5 pin in log: no"
expect $E "run5 token in log: no"
expect $E "run6 exit: 0"
expect $E "run6 decision with target: yes"
expect $E "run6 restarts after seek: yes"
expect $E "run6 segments with token: yes"
expect $E "run6 segment without token: no"
expect $E "run6 stopped then stop: yes"
expect $E "run6 mpv left running: no"
expect $E "run7 exit: 0"
expect $E "run7 home searches: one"
expect $E "run7 shared searches: one"
expect $E "run7 recent: ['wide']"
expect $E "run7 query in log: no"
expect $E "run7 token in url: no"
expect $E "run8 exit: 0"
expect $E "run8 saver on: yes"
expect $E "run8 saver off: yes"
expect $E "run8 saver items: yes"
expect $E "run8 wake keeps the screen: yes"
expect $E "run10 exit: 0"
expect $E "run10 two tracks: yes"
expect $E "run10 part token header: yes"
expect $E "run10 track 1 stopped: yes"
expect $E "run10 session header: yes"
expect $E "run10 last stopped at exit: yes"
expect $E "run10 scrobble track 1: yes"
expect $E "run10 token in url: no"
expect $E "run10 audio mpv left: no"
expect $E "run11 exit: 0"
expect $E "run11 station queue: yes"
expect $E "run11 token in url: no"
expect $E "run12 exit: 0"
expect $E "run12 stream without plex headers: yes"
expect $E "run12 token to radio: no"
expect $E "run12 timeline: no"
expect $E "run13 exit: 0"
expect $E "run13 vis next: yes"
expect $E "run13 vis overlay: yes"
expect $E "run13 vis started twice: yes"
expect $E "run13 saver started vis: yes"
expect $E "run13 vis left running: no"
expect $E "run13 parec left running: no"
expect $E "run13b exit: 0"
expect $E "run13 fallback mpv: yes"
expect $E "run13 showcqt left running: no"
expect $E "run14 exit: 0"
expect $E "run14 popular: yes"
expect $E "run14 search: yes"
expect $E "run14 directory headers: yes"
expect $E "run14 plex headers to directory: no"
expect $E "run14 click call: no"
expect $E "run14 stream without plex headers: yes"
expect $E "run14 radio.m3u three lines: yes"
expect $E "run14 query in log: no"
expect $E "run14 audio mpv left: no"
expect $E "run15 exit: 0"
expect $E "run15 music playlist plays: yes"
expect $E "run15 video in order: yes"
expect $E "run15 entry on its own server: yes"
expect $E "run15 library tab section: yes"
expect $E "run15 token in url: no"
expect $E "run15 names in log: no"
expect $E "run15 mpv left running: no"
expect $E "run16 exit: 0"
expect $E "run16 create: yes"
expect $E "run16 queue plays: yes"
expect $E "run16 queue of two: yes"
expect $E "run16 name in log: no"
expect $E "run16 token in url: no"
expect $E "run17 exit: 0"
expect $E "run17 one call each: yes"
expect $E "run17 right entries: yes"
expect $E "run17 shared refused: yes"
expect $E "run17 failed write toast: yes"
expect $E "run17 names in log: no"
expect $E "run17 token in url: no"
expect $E "run18 exit: 0"
expect $E "run18 trailer played: yes"
expect $E "run18 extra reports: no"
expect $E "run18 rows asked: yes"
expect $E "run18 person titles: yes"
expect $E "run18 token in url: no"
expect $E "run19 exit: 0"
expect $E "run19 channels saved: 2"
expect $E "run19 tunes: yes"
expect $E "run19 timeline: no"
expect $E "run19 pool fetched: yes"
expect $E "run19 guide opened: yes"
expect $E "run19 guide over: yes"
expect $E "run19 token in url: no"
expect $E "run19 mpv left running: no"
expect $E "run19b exit: 0"
expect $E "run19b pool request: no"
expect $E "run4 mom watchlist token: yes"
expect $E "run20 exit: 0"
expect $E "run20 four titles: yes"
expect $E "run20 home copy plays: yes"
expect $E "run20 one remove one add: yes"
expect $E "run20 discover calls only the three: yes"
expect $E "run20 guid lookups: yes"
expect $E "run20 pinned again: yes"
expect $E "run20 token in url: no"
expect $E "run20 titles in log: no"
expect $E "run20 token in log: no"
expect $E "run20 cache file: yes"
expect $E "run20b exit: 0"
expect $E "run20b second server copy plays: yes"
expect $E "run1 subtitle write-back: yes"
expect $E "run1 up next: yes"
expect $E "run1 theme music: yes"
expect $E "run1 audio mpv left running: no"
n=$(ls test/out/p0*.png 2>/dev/null | wc -l | tr -d ' ')
[ "$n" -ge 9 ] && echo "ok    $n screenshots" || { echo "FAIL  only $n of 9 screenshots"; fail=1; }
bad=$(grep -lE "frame failed|fatal error" test/out/app*.log 2>/dev/null | tr '\n' ' ')
[ -z "$bad" ] && echo "ok    no frame failed or fatal error in the app logs" || { echo "FAIL  frame failed or fatal error  (in $bad)"; fail=1; }

I=test/out/install/result.txt
expect $I "run1 mpv while playing: 1"
expect $I "run1 mpv after alt+F4: 0"
expect $I "run1 app after 2nd alt+F4: exited"
expect $I "run1 launcher exit: 0"
expect $I "run2 mpv while playing: 1"
expect $I "run2 mpv after TERM: 0"
expect $I "run2 app after TERM: exited"
expect $I "run2 mode after: 641x480.60.00"
expect $I "rain font and license: yes"
# tools/build.sh is owned by another session: this check starts once it copies sounds/.
if grep -q sounds tools/build.sh; then expect $I "hearth sounds: 4"; else echo "skip  hearth sounds (tools/build.sh does not copy sounds/ yet)"; fi
expect $I "700 /userdata/system/batoplex/data"
expect $I "600 /userdata/system/batoplex/data/auth.json"
expect $I "install over foreign file exit: 1"
expect $I "Batoplex is removed."

[ "$fail" = 0 ] && echo "All checks passed." || { echo "Some checks failed. Logs: test/out/"; exit 1; }
