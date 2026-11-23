#!/usr/bin/env python3
"""A stand-in for native/bpx-milkdrop in the e2e run: says ready, logs each command from the
control pipe to $FAKE_VIS_LOG, reads stdin, and ends on quit, EOF or SIGTERM.
FAKE_VIS_FAIL=1: exit at once (the app must use the showcqt fallback)."""
import os
import signal
import sys
import threading

if os.environ.get("FAKE_VIS_FAIL") == "1":
    sys.exit(2)
args = sys.argv[1:]
ctl = int(args[args.index("--ctl-fd") + 1])
log = open(os.environ.get("FAKE_VIS_LOG", "/tmp/fake_vis.log"), "a")
signal.signal(signal.SIGTERM, lambda *_: os._exit(0))


def drain():
    while sys.stdin.buffer.read(4096):
        pass


threading.Thread(target=drain, daemon=True).start()
log.write("start %s\n" % " ".join(args[:1]))
log.flush()
print("ready fake", flush=True)
with os.fdopen(ctl, "r") as f:
    for line in f:
        log.write(line)
        log.flush()
        if line.strip() == "quit":
            break
log.write("end\n")
