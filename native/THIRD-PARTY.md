# Third-party parts of the visualizer

## projectM (libprojectM 4.1.8)

- Licence: GNU LGPL 2.1 or later.
- Source: https://github.com/projectM-visualizer/projectm, tag `v4.1.8`, commit `4967dc003e5206d7df3ff447287f7609c47d2343`.
- `bpx-milkdrop` links libprojectM statically. Each release has `projectm-4.1.8-src.tar.gz`, the exact source of the build. To link your own projectM, build with `native/build.sh` (this repo is MIT, with full source).

## MilkDrop presets (`presets/`)

- Source: https://github.com/projectM-visualizer/presets-cream-of-the-crop. `presets/SOURCES.txt` lists each file and its path there.
- The presets have no formal licence. The projectM team writes that the presets were released freely and are "safe to assume ... public domain", and that they remove a preset when its author asks. We do the same: open an issue and we remove it.
- Two file names were shortened. `SOURCES.txt` gives the original names.
