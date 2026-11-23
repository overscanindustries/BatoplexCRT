#!/bin/sh
# Build native/bpx-milkdrop for Batocera x86_64 in debian:bookworm (glibc 2.36) with Docker.
# libprojectM is static; libstdc++/libgcc are static; GL/EGL/GBM/SDL2 come from the box.
# Also writes native/.cache/projectm-<tag>-src.tar.gz (LGPL-2.1: the source we built from).
# Skips the build when the binary is newer than its sources.
# HEADLESS=1 builds native/bpx-milkdrop-headless instead (the --headless test mode; needs EGL and GBM).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
HEADLESS=${HEADLESS:-}
PM_TAG=v4.1.8
PM_COMMIT=4967dc003e5206d7df3ff447287f7609c47d2343   # fail if the tag moved
NAME=bpx-milkdrop${HEADLESS:+-headless}
OUT="$HERE/$NAME"
SRC_TGZ="$HERE/.cache/projectm-${PM_TAG#v}-src.tar.gz"

if [ -f "$OUT" ] && [ -f "$SRC_TGZ" ] && [ "$OUT" -nt "$HERE/bpx-milkdrop.cpp" ] && [ "$OUT" -nt "$0" ]; then
    echo "bpx-milkdrop is up to date"
    exit 0
fi
mkdir -p "$HERE/.cache"
docker build -q --platform linux/amd64 -t bpx-milkdrop-build - >/dev/null <<'DF'
FROM debian:bookworm
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends build-essential cmake \
    git ca-certificates pkg-config libgl-dev libopengl-dev libegl-dev libgbm-dev libsdl2-dev && rm -rf /var/lib/apt/lists/*
DF
docker run --rm --platform linux/amd64 -v "$HERE:/w" -e PM_TAG="$PM_TAG" -e PM_COMMIT="$PM_COMMIT" \
  -e NAME="$NAME" -e HEADLESS="$HEADLESS" \
  bpx-milkdrop-build sh -ec '
cd /w/.cache
[ -d projectm ] || git clone -q --depth 1 --branch "$PM_TAG" --recurse-submodules \
  --shallow-submodules https://github.com/projectM-visualizer/projectm.git
HEAD=$(git -C projectm rev-parse HEAD)
[ -z "$PM_COMMIT" ] || [ "$HEAD" = "$PM_COMMIT" ] || { echo "projectm $PM_TAG is $HEAD, not $PM_COMMIT"; exit 1; }
SRC=projectm-${PM_TAG#v}-src.tar.gz
[ -f "$SRC" ] || tar --exclude=.git -czf "$SRC" projectm
if [ ! -d pm ]; then
  cmake -S projectm -B pm-build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DENABLE_PLAYLIST=OFF -DENABLE_SDL_UI=OFF -DBUILD_TESTING=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DCMAKE_INSTALL_PREFIX=/w/.cache/pm >/dev/null
  cmake --build pm-build -j"$(nproc)" >/dev/null
  cmake --install pm-build >/dev/null
fi
PM=/w/.cache/pm
EXTRA=""; [ -z "$HEADLESS" ] || EXTRA="-DBPX_HEADLESS -lEGL -lgbm"
g++ -O2 -std=c++17 -Wall -o /w/$NAME.tmp /w/bpx-milkdrop.cpp \
  -I$PM/include $(find $PM/lib -name "libprojectM-4.a") \
  $(find $PM/lib -name "libprojectM_eval.a" -o -name "libprojectM-eval.a" | head -1) \
  $(pkg-config --cflags sdl2) -lSDL2 $EXTRA -lOpenGL -lGLX -lpthread -ldl \
  -static-libstdc++ -static-libgcc -Wl,--as-needed -s
G=$(objdump -T /w/$NAME.tmp | grep -o "GLIBC_[0-9.]*" | sort -Vu | tail -1)
case "$G" in GLIBC_2.3[0-6]|GLIBC_2.[12]*) ;; *) echo "binary needs $G (max GLIBC_2.36)"; exit 1;; esac
mv /w/$NAME.tmp /w/$NAME
echo "$NAME built (projectm $HEAD, needs $G)"
'
