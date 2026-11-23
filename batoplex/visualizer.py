"""The music visualizer: MilkDrop through native/bpx-milkdrop (projectM), or the
mpv showcqt spectrum when that binary cannot start.

parec reads the PipeWire monitor (what the speakers play) and feeds the engine on stdin, so the
audio mpv does not change and Radio works too. The app keeps the pad: it sends commands to the
binary on a pipe (fd --ctl-fd) and reads "ready" and "key <action>" lines from its stdout.

No pygame here. The UI thread calls tick() each frame; reader threads only fill queues.
"""
import collections
import os
import platform
import tempfile
import shutil
import signal
import subprocess
import threading
import time

from . import config, log

L = log.get("visualizer")

READY_S = 5.0        # projectM must say "ready" in this time
EARLY_EXIT_S = 5.0   # showcqt that exits this soon did not work
TERM_S = 0.5         # after "quit": SIGTERM
KILL_S = 1.5         # then SIGKILL
AUDIO_OK_S = 5.0     # parec that ends sooner never worked: no restart, the picture runs on
AUDIO_RETRY_S = 2.0  # wait for PipeWire before the restart
CLIENT = "batoplex-vis"
PAREC = ["--client-name=" + CLIENT, "--latency-msec=20", "-d", "@DEFAULT_MONITOR@",
         "--format=s16le", "--rate=44100", "--channels=2", "--raw"]
NATIVE = os.path.join(config.APP_DIR, "native")
BINARY = os.path.join(NATIVE, "bpx-milkdrop")
PRESETS = os.path.join(NATIVE, "presets")
# bpx-milkdrop is an x86_64 build: another CPU uses showcqt and never tries to run it.
NATIVE_OK = platform.machine().lower() in ("x86_64", "amd64")


def user_dir():
    return os.path.join(config.data_dir(), "visualizer")


# The showcqt window has the keyboard focus: its back keys end mpv (exit 0), and the app then
# closes the screen. The pad goes to the app as always.
SHOWCQT_KEYS = "".join("%s quit\n" % k for k in ("ESC", "BS", "Alt+F4", "CLOSE_WIN", "GO_BACK", "HOMEPAGE"))


def showcqt_args(mpv, input_conf):
    return [mpv, "--no-config", "--really-quiet", "--title=Batoplex Visualizer", "--fs",
            "--force-window=immediate", "--no-input-default-bindings",
            "--input-conf=" + input_conf,
            "--demuxer=rawaudio", "--demuxer-rawaudio-format=s16le",
            "--demuxer-rawaudio-rate=44100", "--demuxer-rawaudio-channels=2",
            "--lavfi-complex=[aid1]showcqt=s=640x480:fps=30:axis=0:count=2[vo]",
            "--ao=null", "--untimed", "--cache=no", "--brightness=-10", "-"]


class Visualizer:
    """States: idle, starting, running, stopping. Engine: "projectm" or "showcqt".
    broken: projectM failed in this app run. unavailable: both engines failed."""

    def __init__(self, settings, spawn=subprocess.Popen, clock=time.monotonic,
                 which=shutil.which):
        self.settings = settings
        self.spawn = spawn
        self.clock = clock
        self.which = which
        self.state = "idle"
        self.engine = None
        self.broken = False
        self.unavailable = False
        self.crashes = 0
        self.audio_restarts = 0       # parec ended under a running projectM (a PipeWire restart)
        self.audio_lost_t = None
        self.restarting = False       # a failed restart is not a failed projectM
        self.proc = None
        self.parec = None
        self.ctl = None
        self.t0 = 0.0
        self.stop_t = 0.0
        self.termed = False
        self.want = False             # a start that waits for the stop to finish
        self.lines = collections.deque()
        self.keys = collections.deque()
        self.events = collections.deque()   # "crashed" | "unavailable" for the app
        self.overlay_path = "/tmp/batoplex-vis-%d.rgba" % os.getpid()

    # ------------------------------------------------------------ public
    @property
    def active(self):
        return self.state != "idle"

    def binary(self):
        return self.settings["visualizer"] or BINARY

    def start(self):
        if self.unavailable:
            return False
        if self.state == "stopping":
            self.want = True
            return True
        if self.state != "idle":
            return True
        self.audio_restarts, self.audio_lost_t, self.restarting = 0, None, False
        if not self.broken and self._start_projectm():
            return True
        return self._start_showcqt()

    def stop(self):
        self.want = False
        if self.state in ("idle", "stopping"):
            return
        self._send("quit")
        self._close_ctl()
        self.state, self.stop_t, self.termed = "stopping", self.clock(), False

    def send(self, line):
        """A command for the binary; dropped when it is not running or the pipe is full."""
        if self.state == "running" and self.engine == "projectm":
            self._send(line)

    def overlay(self, path, w, h, x, y, secs):
        self.send("overlay %s %d %d %d %d %.1f" % (path, w, h, x, y, secs))

    def pop_key(self):
        return self.keys.popleft() if self.keys else None

    def pop_event(self):
        return self.events.popleft() if self.events else None

    def tick(self):
        while self.lines:
            line = self.lines.popleft()
            if line.startswith("ready") and self.state == "starting":
                L.info("projectM ready: %s", line[6:])
                self.state, self.restarting = "running", False
            elif line.startswith("key ") and self.state == "running":
                self.keys.append(line[4:].strip())
        if self.state == "idle":
            return
        now = self.clock()
        dead = self.proc is None or self.proc.poll() is not None
        if self.state == "stopping":
            if dead:
                self._reap()
                if self.want:
                    self.want = False
                    self.start()
            elif not self.termed and now - self.stop_t >= TERM_S:
                self.termed = True
                self._signal(signal.SIGTERM)
            elif now - self.stop_t >= KILL_S:
                self._signal(signal.SIGKILL)
            return
        if self.engine == "projectm":
            if self.state == "starting" and self.restarting and (dead or now - self.t0 >= READY_S):
                L.warning("projectM did not start again")
                self.restarting = False
                self._kill_now()
                self.events.append("crashed")
            elif self.state == "starting" and (dead or now - self.t0 >= READY_S):
                L.warning("projectM did not start (%s); showcqt instead",
                          "exit %s" % self.proc.returncode if dead else "no ready")
                self._kill_now()
                self.broken = True
                self._start_showcqt()
            elif self.state == "running" and not dead and self.parec is not None \
                    and self.parec.poll() is not None and self.audio_restarts < 2:
                # The picture lost its audio (a PipeWire restart): start again after a wait.
                # A parec that ended in the first AUDIO_OK_S never worked: no restart.
                if self.audio_lost_t is None:
                    self.audio_lost_t = now
                    L.warning("parec stopped (exit %s)", self.parec.returncode)
                elif self.audio_lost_t - self.t0 >= AUDIO_OK_S \
                        and now - self.audio_lost_t >= AUDIO_RETRY_S:
                    self.audio_restarts += 1
                    self.audio_lost_t = None
                    L.info("projectM starts again for the audio")
                    self._kill_now()
                    if self._start_projectm():
                        self.restarting = True
                    else:
                        self.events.append("crashed")
            elif self.state == "running" and dead:
                self.crashes += 1
                self.broken = self.crashes >= 2
                L.warning("projectM stopped (exit %s)", self.proc.returncode)
                self._kill_now()
                self.events.append("crashed")
        elif dead and self.proc.returncode == 0 and self.parec is not None \
                and self.parec.poll() is None:
            # showcqt: a back key ended it (parec still runs). With parec gone it was an EOF.
            self._kill_now()
        elif dead:                                       # showcqt
            early = now - self.t0 < EARLY_EXIT_S
            L.warning("showcqt stopped (exit %s)", self.proc.returncode)
            self._kill_now()
            if early:
                self.unavailable = True
                self.events.append("unavailable")
            else:
                self.events.append("crashed")

    def shutdown(self, wait=1.0):
        """App exit: stop now, block for `wait` seconds at most."""
        self.stop()
        for p in (self.overlay_path, self.overlay_path + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass
        end = self.clock() + wait
        while self.active and self.clock() < end:
            self.tick()
            if self.active:
                time.sleep(0.02)
        if self.active:
            self._signal(signal.SIGKILL)
            self._reap()

    # ------------------------------------------------------------ engines
    def _start_parec(self):
        exe = self.which("parec")
        if not exe:
            L.info("parec not found: no audio for the visualizer")
            return None
        try:
            return self.spawn([exe] + PAREC, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
        except OSError as e:
            L.info("parec did not start: %s", e)
            return None

    def _start_projectm(self):
        if not NATIVE_OK and not self.settings["visualizer"]:
            return False
        exe = self.binary()
        if not os.path.isfile(exe):
            L.info("no visualizer binary at %s", exe)
            return False
        try:
            os.makedirs(user_dir(), exist_ok=True)
        except OSError as e:
            L.info("no %s: %s", user_dir(), e)
        r, w = os.pipe()
        self.parec = self._start_parec()
        args = [exe, "--window", "--ctl-fd", str(r), "--tex", user_dir(), PRESETS, user_dir()]
        stdin = self.parec.stdout if self.parec else subprocess.DEVNULL
        try:
            try:
                self.proc = self._spawn_main(args, stdin, r)
            except PermissionError:
                exe = self._exec_copy(exe)     # a /userdata mounted with no exec
                try:
                    self.proc = self._spawn_main([exe] + args[1:], stdin, r)
                finally:
                    try:
                        os.remove(exe)         # a running binary can go; no copy is left
                    except OSError:
                        pass
        except OSError as e:
            L.warning("visualizer did not start: %s", e)
            os.close(r)
            os.close(w)
            self._end_parec()
            return False
        os.close(r)
        os.set_blocking(w, False)
        self.ctl = w
        self.engine, self.state, self.t0 = "projectm", "starting", self.clock()
        L.info("visualizer start (projectM)")
        return True

    def _start_showcqt(self):
        mpv = self.settings["mpv"] or "mpv"
        self.parec = self._start_parec()
        if self.parec is None:
            self.unavailable = True
            self.events.append("unavailable")
            return False
        conf = os.path.join(config.data_dir(), "visualizer-input.conf")
        try:
            with open(conf, "w") as f:
                f.write(SHOWCQT_KEYS)
        except OSError:
            pass                               # mpv runs without it; only the back keys go
        try:
            self.proc = self._spawn_main(showcqt_args(mpv, conf), self.parec.stdout, None)
        except OSError as e:
            L.warning("showcqt did not start: %s", e)
            self._end_parec()
            self.unavailable = True
            self.events.append("unavailable")
            return False
        self.engine, self.state, self.t0 = "showcqt", "running", self.clock()
        L.info("visualizer start (showcqt)")
        return True

    def _spawn_main(self, args, stdin, ctl_fd):
        p = self.spawn(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       pass_fds=(ctl_fd,) if ctl_fd is not None else ())
        if self.parec is not None and self.parec.stdout is not None:
            self.parec.stdout.close()          # the engine has it now: parec gets SIGPIPE
        threading.Thread(target=self._read_out, args=(p.stdout,), daemon=True).start()
        threading.Thread(target=self._read_err, args=(p.stderr,), daemon=True).start()
        return p

    @staticmethod
    def _exec_copy(exe):
        fd, dst = tempfile.mkstemp(prefix="bpx-milkdrop-", dir="/tmp")
        with os.fdopen(fd, "wb") as out, open(exe, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chmod(dst, 0o755)
        return dst

    # ------------------------------------------------------------ internals
    def _read_out(self, f):
        try:
            for raw in f:
                line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else raw.strip()
                if line:
                    self.lines.append(line)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _read_err(f):
        n = 0
        try:
            for raw in f:
                line = raw.decode("utf-8", "replace").rstrip() if isinstance(raw, bytes) else raw.rstrip()
                if line and "libEGL warning" not in line and n < 50:
                    n += 1
                    L.info("engine: %s", line)
        except (OSError, ValueError):
            pass

    def _send(self, line):
        if self.ctl is None:
            return
        try:
            os.write(self.ctl, (line + "\n").encode())
        except BlockingIOError:
            L.info("visualizer command dropped: %s", line.split(" ", 1)[0])
        except OSError:
            self._close_ctl()

    def _close_ctl(self):
        if self.ctl is not None:
            try:
                os.close(self.ctl)
            except OSError:
                pass
            self.ctl = None

    def _signal(self, sig):
        for p in (self.proc, self.parec):
            if p is not None and p.poll() is None:
                try:
                    p.send_signal(sig)
                except OSError:
                    pass

    def _end_parec(self):
        p, self.parec = self.parec, None
        if p is None:
            return
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                p.kill()
        if p.stdout is not None:
            try:
                p.stdout.close()
            except OSError:
                pass

    def _reap(self):
        self._close_ctl()
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
        self._end_parec()
        self.state, self.engine = "idle", None
        L.info("visualizer stopped")

    def _kill_now(self):
        """A failed or dead engine: kill it and its parec now (the engine is dead or hung)."""
        self._close_ctl()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self._reap()
