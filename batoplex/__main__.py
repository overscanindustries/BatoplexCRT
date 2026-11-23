import argparse
import sys

from . import config, log


def main(argv=None):
    p = argparse.ArgumentParser(prog="batoplex", description="Plex client for a 4:3 CRT")
    p.add_argument("--window", action="store_true", help="always use a 641x480 window")
    p.add_argument("--fullscreen", action="store_true", help="scaled full screen on non-CRT displays")
    p.add_argument("--script", help="test actions: comma list or file (up,down,ok,wait:N,shot:PATH,exit)")
    p.add_argument("--frames", type=int, default=0, help="exit after N frames (tests)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    L = log.setup(config.data_dir(), args.verbose)
    L.info("Batoplex %s starting (python %s)", config.VERSION, sys.version.split()[0])
    import faulthandler
    import os
    crash = open(os.path.join(config.data_dir(), "crash.log"), "a")   # stays open for the run
    faulthandler.enable(file=crash, all_threads=True)
    import signal
    from .app import App
    app = None
    try:
        app = App(args)
        signal.signal(signal.SIGTERM, lambda *_: app.quit())
        signal.signal(signal.SIGHUP, lambda *_: app.quit())
        app.run()
    except Exception:
        L.exception("fatal error")
        if app is not None:
            try:
                app.close()
            except Exception:
                L.exception("close after the fatal error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
