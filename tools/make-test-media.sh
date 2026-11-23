#!/bin/sh
# Generate the two 60-second test clips used by the mock Plex server in the Docker tests.
#   test/media/4x3.mkv   640x480 29.97p, H.264 + AAC, embedded English subtitle (emb.srt)
#   test/media/16x9.mkv  1280x720 23.976p, H.264 + AAC
# A ring is drawn over color bars: if it is not round on screen, the aspect ratio is wrong.
# Needs ffmpeg with libx264.
set -eu
cd "$(dirname "$0")/../test/media"

make_clip() {  # out size rate [subtitle.srt]
    out=$1 size=$2 rate=$3 srt=${4:-}
    w=${size%x*} h=${size#*x}
    set --
    [ -n "$srt" ] && set -- -i "$srt"
    subs=""
    [ -n "$srt" ] && subs="-map 3:s -c:s srt -metadata:s:s:0 language=eng"
    # shellcheck disable=SC2086
    ffmpeg -v error -y \
        -f lavfi -i "smptebars=s=$size:r=$rate:d=60" \
        -f lavfi -i "color=c=black@0:s=$size:r=$rate:d=60,format=rgba,geq=r=255:g=255:b=255:a='if(between(hypot(X-W/2,Y-H/2),H/3-4,H/3+4),230,0)'" \
        -f lavfi -i "sine=frequency=440:sample_rate=48000:d=60" \
        "$@" \
        -filter_complex "[0:v][1:v]overlay=format=auto,format=yuv420p[v]" \
        -map "[v]" -map 2:a -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 96k -ac 1 \
        $subs -t 60 "$out"
    echo "wrote test/media/$out ($w x $h)"
}

make_clip 4x3.mkv 640x480 30000/1001 emb.srt
make_clip 16x9.mkv 1280x720 24000/1001
