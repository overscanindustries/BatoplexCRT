"""mpv process and JSON IPC. No Plex code: video playback and the audio player both use it.

Threads: one reader thread per process reads the socket and only fills a queue.
The owner calls events() from the UI thread each frame, so all reply callbacks and
event handling run on the UI thread (pygame is not thread safe).
"""
import collections
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time

from . import config, log, sounds

L = log.get("mpv")

_probe_cache = {}
_probe_locks = {}
_probe_guard = threading.Lock()
PROBE_FILE = "mpv_probe.json"
PROBE_FMT = 2                 # 2: video outputs too; an older cache probes again
SAFE_VOS = ("x11", "wlshm", "drm")   # safe-mode video output, first one this mpv has
SEND_TIMEOUT_S = 2


def mpv_version(binary):
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"mpv v?(\d+)\.(\d+)", out)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    except Exception:
        return None


def mpv_options(binary):
    """Names of the options this mpv build knows. Builds differ: Batocera's mpv has no Lua,
    so it has no --osc, and mpv exits at start on an unknown option."""
    try:
        out = subprocess.run([binary, "--no-config", "--list-options"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return set()
    return set(re.findall(r"^\s+--([a-z0-9][a-z0-9-]*)", out, re.M))


def mpv_vos(binary):
    """Names of the video outputs this mpv build has (mpv --vo=help); [] when unknown."""
    try:
        out = subprocess.run([binary, "--no-config", "--vo=help"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception:
        return []
    return re.findall(r"^\s+([a-z0-9][a-z0-9_-]*)\s", out, re.M)


def _binary_id(binary):
    """[real path, mtime ns, size] of the mpv binary, or None when it is not found."""
    p = binary if os.sep in binary else shutil.which(binary)
    if not p:
        return None
    try:
        p = os.path.realpath(p)
        st = os.stat(p)
    except OSError:
        return None
    return [p, st.st_mtime_ns, st.st_size]


def _disk_probe(bid):
    """The probe result kept on disk for this exact binary, or None."""
    try:
        with open(os.path.join(config.data_dir(), "cache", PROBE_FILE)) as f:
            d = json.load(f)
        if (d.get("fmt") == PROBE_FMT and d.get("id") == bid and d.get("version")
                and d.get("options") and d.get("vos")):   # empty lists: a failed probe, a miss
            return tuple(d["version"]), set(d["options"]), list(d["vos"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _keep_probe(bid, v, known, vos):
    try:
        d = os.path.join(config.data_dir(), "cache")
        os.makedirs(d, exist_ok=True)
        config.atomic_write(os.path.join(d, PROBE_FILE), json.dumps(
            {"fmt": PROBE_FMT, "id": bid, "version": list(v), "options": sorted(known),
             "vos": list(vos)}), fsync=False)
    except OSError as e:
        L.info("mpv probe not kept: %s", e)


def probe(binary):
    """(version, known options), cached for the life of the app and on disk for this
    binary (path, mtime, size): the mpv runs happen only when mpv changes. A second
    caller waits for a probe already running (probe_async)."""
    return _probe(binary)[:2]


def _probe(binary):
    with _probe_guard:
        lock = _probe_locks.setdefault(binary, threading.Lock())
    with lock:
        if binary not in _probe_cache:
            bid = _binary_id(binary)
            r = _disk_probe(bid) if bid else None
            if r is None:
                v = mpv_version(binary)
                r = (v, mpv_options(binary) if v else set(), mpv_vos(binary) if v else [])
                if v and bid and r[1] and r[2]:  # a failed probe is not kept
                    _keep_probe(bid, *r)
            _probe_cache[binary] = r
        return _probe_cache[binary]


def safe_vo(binary):
    """The video output for a safe-mode retry: the first of SAFE_VOS that this mpv has.
    x11 when the list is not known (no probe yet, or mpv gave no list)."""
    r = _probe_cache.get(binary)
    vos = r[2] if r else []
    return next((v for v in SAFE_VOS if v in vos), "x11")


def probe_async(binary):
    """Start the probe on a worker (at app start, before the display opens); Mpv() then
    waits for it only if it has not finished."""
    threading.Thread(target=probe, args=(binary,), daemon=True).start()


def supported(args, known):
    """Drop the options that this mpv does not know. With no option list, keep all."""
    if not known:
        return args, []
    keep, drop = [args[0]], []
    for a in args[1:]:
        name = a[2:].split("=", 1)[0] if a.startswith("--") else None
        if name and name not in known and not (name.startswith("no-") and name[3:] in known):
            drop.append(name)
        else:
            keep.append(a)
    return keep, drop


class Cancelled(Exception):
    pass


class Mpv:
    """One mpv process. start() blocks (run it on a worker thread); everything else is
    safe to call from the UI thread."""

    def __init__(self, binary, name="mpv"):
        self.binary = binary
        self.name = name
        self.version, self.known = probe(binary)
        self.proc = None
        self.sock = None
        self.sock_path = None
        self.lock = threading.Lock()
        self.req = 0
        self.callbacks = {}            # request id -> fn(error, data)
        self.queue = collections.deque()
        self.observed = {}             # observe id -> property name
        self.closed = threading.Event()

    @property
    def available(self):
        return self.version is not None

    @property
    def alive(self):
        return (self.sock is not None and not self.closed.is_set()
                and (self.proc is None or self.proc.poll() is None))

    # ------------------------------------------------------------ start
    def start(self, args, sock_path, cancel=None, timeout=8.0):
        """Spawn mpv with args (binary first, no --input-ipc-server) and connect.
        Checks the cancel event every 50 ms. Raises Cancelled or RuntimeError."""
        sounds.close()                  # the menu sounds give the device back
        args, drop = supported(list(args) + ["--input-ipc-server=" + sock_path], self.known)
        if drop:
            L.info("%s lacks options, skipped: %s", self.name, " ".join(drop))
        try:
            os.unlink(sock_path)
        except OSError:
            pass
        self.sock_path = sock_path
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        threading.Thread(target=self._stderr, args=(self.proc,), daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                self.kill()
                raise Cancelled()
            if self.proc.poll() is not None:
                raise RuntimeError("mpv exited at start (code %s)" % self.proc.returncode)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(sock_path)
            except OSError:
                s.close()
                time.sleep(0.05)
                continue
            self.attach(s)
            return
        self.kill()
        raise RuntimeError("mpv IPC did not open")

    def attach(self, sock):
        """Use an already connected socket (tests use this with a fake server)."""
        try:
            # A send that blocks this long gives up (cmd runs on the UI thread). A send
            # timeout only: the reader thread's recv on the same socket still blocks.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO,
                            struct.pack("ll", SEND_TIMEOUT_S, 0))
        except (OSError, struct.error):
            pass
        self.sock = sock
        self.closed.clear()
        threading.Thread(target=self._reader, args=(sock,), daemon=True).start()

    def _stderr(self, proc):
        for line in proc.stderr:
            line = line.decode("utf-8", "replace").rstrip()
            if line:
                L.info("%s: %s", self.name, line)

    # ------------------------------------------------------------ IPC
    def cmd(self, *args, cb=None):
        """Send a command. cb(error, data) runs on the UI thread from events().
        Returns the request id, or None if mpv is gone."""
        with self.lock:
            if not self.sock:
                return None
            self.req += 1
            rid = self.req
            if cb:
                self.callbacks[rid] = cb
            msg = json.dumps({"command": list(args), "request_id": rid}) + "\n"
            try:
                self.sock.sendall(msg.encode())
            except OSError:
                self.callbacks.pop(rid, None)
                return None
            return rid

    def set(self, prop, value, cb=None):
        return self.cmd("set_property", prop, value, cb=cb)

    def observe(self, *props):
        for p in props:
            oid = len(self.observed) + 1
            self.observed[oid] = p
            self.cmd("observe_property", oid, p)

    def get(self, prop, timeout=1.0):
        """Blocking read for worker threads only. None on timeout or error."""
        box = {}
        done = threading.Event()

        def cb(err, data):
            box["d"] = None if err != "success" else data
            done.set()
        with self.lock:
            if not self.sock:
                return None
            self.req += 1
            rid = self.req
            self.callbacks[rid] = ("direct", cb)
            msg = json.dumps({"command": ["get_property", prop], "request_id": rid}) + "\n"
            try:
                self.sock.sendall(msg.encode())
            except OSError:
                return None
        done.wait(timeout)
        return box.get("d")

    def _reader(self, sock):
        buf = b""
        while True:
            try:
                chunk = sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if "request_id" in ev and "event" not in ev:
                    cb = self.callbacks.pop(ev.get("request_id"), None)
                    if isinstance(cb, tuple):           # blocking get(): answer at once
                        cb[1](ev.get("error"), ev.get("data"))
                    elif cb:
                        self.queue.append(("reply", cb, ev.get("error"), ev.get("data")))
                    continue
                self.queue.append(("event", ev))
        self.closed.set()
        self.queue.append(("event", {"event": "ipc-closed"}))

    def events(self):
        """Drain on the UI thread. Runs reply callbacks; returns the list of mpv events."""
        out = []
        while self.queue:
            e = self.queue.popleft()
            if e[0] == "reply":
                try:
                    e[1](e[2], e[3])
                except Exception:
                    L.exception("%s reply callback failed", self.name)
            else:
                out.append(e[1])
        return out

    # ------------------------------------------------------------ stop
    def kill(self, wait=2.0):
        if self.proc and self.proc.poll() is None:
            if self.sock is None:
                self.proc.terminate()        # no IPC yet: do not leave the window up for `wait`
            else:
                self.cmd("quit")
            try:
                self.proc.wait(wait)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(1)
                except subprocess.TimeoutExpired:
                    pass
        with self.lock:
            if self.sock:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = None
        self.closed.set()
        if self.sock_path:
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass
