"""Test doubles for mpv.

FakeMpv: in-process object with the Mpv interface. Records commands; tests fire events.
FakeMpvServer: a Unix socket server that speaks enough mpv JSON IPC for mpv.py tests.
Run as a script (`python fake_mpv.py --input-ipc-server=PATH ...`) it acts as an mpv binary.
"""
import collections
import json
import os
import socket
import sys
import threading


class FakeMpv:
    def __init__(self):
        self.cmds = []
        self.props = {}
        self.queue = collections.deque()
        self.alive_ = True
        self.observed = []
        self.version = (0, 41)
        self.known = set()
        self.started_with = None
        self.replies = {}            # command name -> (error, data)

    available = True

    @property
    def alive(self):
        return self.alive_

    def start(self, args, sock_path, cancel=None, timeout=8.0):
        self.started_with = list(args)
        self.sock_path = sock_path

    def cmd(self, *args, cb=None):
        self.cmds.append(list(args))
        if cb:
            err, data = self.replies.get(args[0], ("success", None))
            if args[0] == "get_property":
                data = self.props.get(args[1], data)
            self.queue.append(("reply", cb, err, data))
        return len(self.cmds)

    def set(self, prop, value, cb=None):
        self.props[prop] = value
        return self.cmd("set_property", prop, value, cb=cb)

    def observe(self, *props):
        self.observed += props

    def get(self, prop, timeout=1.0):
        return self.props.get(prop)

    def fire(self, event, **kw):
        ev = {"event": event}
        ev.update(kw)
        self.queue.append(("event", ev))

    def prop(self, name, value):
        self.props[name] = value
        self.fire("property-change", name=name, data=value)

    def events(self):
        out = []
        while self.queue:
            e = self.queue.popleft()
            if e[0] == "reply":
                e[1](e[2], e[3])
            else:
                out.append(e[1])
        return out

    def kill(self, wait=2.0):
        self.alive_ = False
        self.cmds.append(["<killed>"])

    def names(self):
        return [c[0] for c in self.cmds]


class FakeMpvServer:
    """Answers mpv IPC commands on a Unix socket. Records each command."""

    def __init__(self, path):
        self.path = path
        self.cmds = []
        self.props = {"pause": False, "time-pos": 0.0, "duration": 60.0,
                      "osd-dimensions": {"w": 641, "h": 480}}
        self.overlays = {}
        self.errors = []
        self.conns = []
        try:
            os.unlink(path)
        except OSError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        self.quit = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.quit.is_set():
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def send(self, c, obj):
        try:
            c.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    def broadcast(self, obj):
        for c in list(self.conns):
            self.send(c, obj)

    def _serve(self, c):
        buf = b""
        observed = {}
        while True:
            try:
                chunk = c.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                cmd = msg.get("command") or []
                rid = msg.get("request_id", 0)
                self.cmds.append(cmd)
                name = cmd[0] if cmd else ""
                err, data = "success", None
                if name == "get_property":
                    if cmd[1] in self.props:
                        data = self.props[cmd[1]]
                    else:
                        err = "property unavailable"
                elif name == "set_property":
                    self.props[cmd[1]] = cmd[2]
                    for oid, p in observed.items():
                        if p == cmd[1]:
                            self.send(c, {"event": "property-change", "id": oid, "name": p,
                                          "data": cmd[2]})
                elif name == "observe_property":
                    observed[cmd[1]] = cmd[2]
                    self.send(c, {"event": "property-change", "id": cmd[1], "name": cmd[2],
                                  "data": self.props.get(cmd[2])})
                elif name == "overlay-add":
                    oid, x, y, f, off, fmt, w, h, stride = cmd[1:10]
                    try:
                        with open(f, "rb") as fh:
                            fh.seek(off)
                            raw = fh.read(stride * h)
                    except OSError as e:
                        raw = b""
                        self.errors.append(str(e))
                    if len(raw) != stride * h:
                        err = "invalid parameter"
                        self.errors.append("size %d != %d" % (len(raw), stride * h))
                    else:
                        for i in range(0, len(raw), 4):   # premultiplied: B, G, R <= A
                            if max(raw[i], raw[i + 1], raw[i + 2]) > raw[i + 3]:
                                err = "invalid parameter"
                                self.errors.append("not premultiplied at %d" % (i // 4))
                                break
                    self.overlays[oid] = (x, y, w, h, raw)
                elif name == "overlay-remove":
                    self.overlays.pop(cmd[1], None)
                elif name == "loadfile":
                    self.props["path"] = cmd[1]
                    self.send(c, {"event": "start-file"})
                    self.send(c, {"event": "file-loaded"})
                elif name == "quit":
                    self.send(c, {"request_id": rid, "error": "success", "data": None})
                    self.close()
                    return
                self.send(c, {"request_id": rid, "error": err, "data": data})

    def close(self):
        self.quit.set()
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass
        try:
            self.srv.close()
        except OSError:
            pass


if __name__ == "__main__":
    if "--version" in sys.argv:
        print("mpv v0.41.0 (fake)")
        sys.exit(0)
    if "--list-options" in sys.argv:
        for o in ("idle", "input-ipc-server", "fs", "keep-open", "pause"):
            print(" --" + o)
        sys.exit(0)
    path = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--input-ipc-server="))
    s = FakeMpvServer(path)
    s.quit.wait(30)
