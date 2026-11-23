#!/bin/sh
# Build the install bundle: dist/batoplex-<version>.tar.gz
set -eu
cd "$(dirname "$0")/.."
V=$(python3 -c 'import re;print(re.search(r"VERSION = \"(.+)\"", open("batoplex/config.py").read()).group(1))')
D=dist/batoplex-$V
rm -rf "$D" && mkdir -p "$D/launcher"
cp -r batoplex fonts sounds art "$D/"
# Visualizer (x86_64, built in Docker). BATOPLEX_NO_NATIVE=1 skips it.
if [ "${BATOPLEX_NO_NATIVE:-0}" != 1 ]; then
    sh native/build.sh
    mkdir -p "$D/native"
    cp native/bpx-milkdrop native/THIRD-PARTY.md "$D/native/"
    cp -r native/presets "$D/native/presets"
    chmod 755 "$D/native/bpx-milkdrop"
    cp native/.cache/projectm-4.1.8-src.tar.gz dist/
fi
find "$D" -name __pycache__ -prune -exec rm -rf {} +
cp launcher/Batoplex.sh launcher/Batoplex.sh.keys "$D/launcher/"
cp launcher/install.sh launcher/uninstall.sh tools/probe.sh LICENSE "$D/"
cp docs/install.md "$D/INSTALL.md"
echo "$V" > "$D/VERSION"
chmod 755 "$D"/*.sh "$D/launcher/Batoplex.sh"
COPYFILE_DISABLE=1 tar --no-xattrs -C dist -czf "dist/batoplex-$V.tar.gz" "batoplex-$V"
(cd dist && shasum -a 256 "batoplex-$V.tar.gz" | tee "batoplex-$V.tar.gz.sha256")
