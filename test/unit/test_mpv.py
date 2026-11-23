import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from batoplex import mpv  # noqa: E402
from fake_mpv import FakeMpvServer  # noqa: E402

FAKE = os.path.join(os.path.dirname(__file__), "fake_mpv.py")


def wait_for(fn, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if r:
            return r
        time.sleep(0.01)
    return fn()


class TestSupported(unittest.TestCase):
    def test_drops_unknown(self):
        keep, drop = mpv.supported(["mpv", "--fs", "--no-osc", "--osc=no", "--no-fs", "file"],
                                   {"fs"})
        self.assertEqual(keep, ["mpv", "--fs", "--no-fs", "file"])
        self.assertEqual(drop, ["no-osc", "osc"])

    def test_keeps_all_without_list(self):
        self.assertEqual(mpv.supported(["mpv", "--x"], set()), (["mpv", "--x"], []))


class TestIpc(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "s.sock")
        self.srv = FakeMpvServer(self.path)
        self.m = mpv.Mpv(sys.executable)   # probe of python: version None, harmless
        import socket
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.path)
        self.m.attach(s)
        wait_for(lambda: self.srv.conns)      # the server has registered this client

    def tearDown(self):
        self.m.kill()
        self.srv.close()

    def pump(self, pred, timeout=2.0):
        got = []

        def step():
            got.extend(self.m.events())
            return pred(got)
        wait_for(step, timeout)
        return got

    def test_reply_callback_runs_on_events(self):
        seen = []
        self.m.cmd("get_property", "duration", cb=lambda e, d: seen.append((e, d)))
        time.sleep(0.1)
        self.assertEqual(seen, [])           # not on the reader thread
        self.pump(lambda g: seen)
        self.assertEqual(seen, [("success", 60.0)])

    def test_error_reply(self):
        seen = []
        self.m.cmd("get_property", "nope", cb=lambda e, d: seen.append(e))
        self.pump(lambda g: seen)
        self.assertEqual(seen, ["property unavailable"])

    def test_observe_and_events(self):
        self.m.observe("pause", "time-pos")
        self.m.set("pause", True)
        evs = self.pump(lambda g: any(e.get("name") == "pause" and e.get("data") is True for e in g))
        self.assertTrue(any(e.get("name") == "pause" and e.get("data") is True for e in evs))
        self.m.cmd("loadfile", "x.mkv", "replace")
        evs = self.pump(lambda g: any(e.get("event") == "file-loaded" for e in g))
        self.assertTrue(any(e.get("event") == "file-loaded" for e in evs))

    def test_blocking_get_from_worker(self):
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("v", self.m.get("duration")))
        t.start()
        t.join(2)
        self.assertEqual(box.get("v"), 60.0)

    def test_ipc_closed_event(self):
        self.srv.close()
        evs = self.pump(lambda g: any(e.get("event") == "ipc-closed" for e in g))
        self.assertTrue(any(e.get("event") == "ipc-closed" for e in evs))
        self.assertFalse(self.m.alive)
        self.assertIsNone(self.m.cmd("x"))


class TestSpawn(unittest.TestCase):
    def binary(self):
        d = tempfile.mkdtemp()
        b = os.path.join(d, "mpv")
        with open(b, "w") as f:
            f.write("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, FAKE))
        os.chmod(b, 0o755)
        return b, d

    def test_spawn_connect_and_filter(self):
        b, d = self.binary()
        m = mpv.Mpv(b)
        self.assertEqual(m.version, (0, 41))
        m.start([b, "--idle=yes", "--no-osc", "--fs"], os.path.join(d, "a.sock"))
        self.assertTrue(m.alive)
        self.assertNotIn("--no-osc", m.proc.args)
        m.kill()
        self.assertFalse(m.alive)
        self.assertFalse(os.path.exists(os.path.join(d, "a.sock")))

    def test_cancel_during_start(self):
        b, d = self.binary()
        m = mpv.Mpv(b)
        ev = threading.Event()
        ev.set()
        with self.assertRaises(mpv.Cancelled):
            m.start([b], os.path.join(d, "b.sock"), cancel=ev)
        self.assertFalse(m.alive)


class TestProbeCache(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        self.dir = tempfile.mkdtemp()
        self.bid = ["/bin/mpv", 1, 2]
        self.patches = [mock.patch.object(mpv.config, "data_dir", lambda: self.dir),
                        mock.patch.object(mpv, "_binary_id", lambda b: self.bid),
                        mock.patch.object(mpv, "mpv_version", lambda b: (0, 41, 0))]
        for p in self.patches:
            p.start()
        mpv._probe_cache.pop("mpvX", None)
        self.file = os.path.join(self.dir, "cache", mpv.PROBE_FILE)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        mpv._probe_cache.pop("mpvX", None)

    def _run(self, opts, vos):
        from unittest import mock
        with mock.patch.object(mpv, "mpv_options", lambda b: set(opts)), \
                mock.patch.object(mpv, "mpv_vos", lambda b: list(vos)):
            mpv._probe_cache.pop("mpvX", None)
            return mpv._probe("mpvX")

    def test_failed_probe_not_kept(self):
        self._run([], ["gpu"])
        self.assertFalse(os.path.exists(self.file))
        self._run(["fs"], [])
        self.assertFalse(os.path.exists(self.file))

    def test_good_probe_kept_and_read(self):
        self._run(["fs"], ["gpu"])
        self.assertEqual(mpv._disk_probe(self.bid), ((0, 41, 0), {"fs"}, ["gpu"]))

    def test_empty_options_on_disk_is_a_miss(self):
        mpv._keep_probe(self.bid, (0, 41, 0), set(), ["gpu"])
        self.assertIsNone(mpv._disk_probe(self.bid))
        self.assertEqual(self._run(["fs"], ["gpu"])[1], {"fs"})


if __name__ == "__main__":
    unittest.main()
