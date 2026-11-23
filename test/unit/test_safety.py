import os
import stat
import sys
import tempfile
import time
import unittest

os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(__file__))

from batoplex import config, images, playback  # noqa: E402
from test_playback import SETTINGS  # noqa: E402


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = os.path.join(self.d, "f.json")

    def mode(self):
        return stat.S_IMODE(os.stat(self.p).st_mode)

    def test_str_and_bytes_new_file_and_no_temp_left(self):
        config.atomic_write(self.p, "héllo")
        with open(self.p, "rb") as f:
            self.assertEqual(f.read(), "héllo".encode("utf-8"))
        config.atomic_write(self.p, b"\x00\x01", fsync=False)
        with open(self.p, "rb") as f:
            self.assertEqual(f.read(), b"\x00\x01")
        self.assertEqual(os.listdir(self.d), ["f.json"])
        self.assertEqual(self.mode(), 0o644)

    def test_keeps_the_old_mode_or_takes_the_given_one(self):
        config.atomic_write(self.p, "a")
        os.chmod(self.p, 0o640)
        config.atomic_write(self.p, "b")
        self.assertEqual(self.mode(), 0o640)
        config.atomic_write(self.p, "c", mode=0o600)
        self.assertEqual(self.mode(), 0o600)

    def test_a_failed_replace_keeps_the_old_file_and_removes_the_temp(self):
        config.atomic_write(self.p, "old")
        real = os.replace

        def fail(a, b):
            raise OSError("disk")
        os.replace = fail
        try:
            with self.assertRaises(OSError):
                config.atomic_write(self.p, "new", prefix=".x-")
        finally:
            os.replace = real
        with open(self.p) as f:
            self.assertEqual(f.read(), "old")
        self.assertEqual(os.listdir(self.d), ["f.json"])

    def test_secrets_file_stays_0600(self):
        s = config.Secrets(os.path.join(self.d, "auth.json"))
        s.set(user_token="abcdefgh")
        self.assertEqual(stat.S_IMODE(os.stat(s.path).st_mode), 0o600)


class TestSaveFails(unittest.TestCase):
    def test_save_on_an_unwritable_directory_returns_false(self):
        d = os.path.join(tempfile.mkdtemp(), "gone")      # no directory: as a read-only disk
        st = config.Settings(os.path.join(d, "settings.json"))
        st["flicker_filter"] = "light"
        with self.assertLogs("batoplex.config", "WARNING"):
            self.assertFalse(st.save())
            self.assertFalse(config.Secrets(os.path.join(d, "auth.json")).save())
        self.assertEqual(st["flicker_filter"], "light")
        self.assertTrue(config.Settings(os.path.join(tempfile.mkdtemp(), "s.json")).save())


class TestPrune(unittest.TestCase):
    def test_oldest_files_go_first_down_to_the_limit(self):
        d = tempfile.mkdtemp()
        now = time.time()
        for i in range(5):
            sub = os.path.join(d, "%02d" % i)
            os.makedirs(sub)
            p = os.path.join(sub, "%d.img" % i)
            with open(p, "wb") as f:
                f.write(b"x" * 100)
            os.utime(p, (now - 1000 + i, now - 1000 + i))   # 0 is the oldest
        other = os.path.join(d, "keep.txt")
        with open(other, "w") as f:
            f.write("x" * 1000)
        self.assertEqual(images.prune(d, limit=250), 300)
        left = sorted(n for _r, _d, ns in os.walk(d) for n in ns)
        self.assertEqual(left, ["3.img", "4.img", "keep.txt"])
        self.assertEqual(images.prune(d, limit=250), 0)


class FakeProvider:
    name = "fake"

    def image(self, ref, w, h):
        return None


class TestRequestQueue(unittest.TestCase):
    def test_bounded_lifo_drops_the_oldest_and_it_can_be_asked_again(self):
        c = images.ImageCache(FakeProvider(), tempfile.mkdtemp(), workers=0, prune_disk=False)
        for i in range(images.QUEUE_MAX + 3):
            c.get("r%d" % i, 10, 10)
        self.assertEqual(len(c.q), images.QUEUE_MAX)
        self.assertEqual(c.q[-1][0], "r%d" % (images.QUEUE_MAX + 2))     # newest first out
        self.assertNotIn(c.key("r0", 10, 10, 1.0), c.pending)
        c.get("r0", 10, 10)
        self.assertEqual(c.q[-1][0], "r0")


class TestRamRule(unittest.TestCase):
    def meminfo(self, kb):
        p = os.path.join(tempfile.mkdtemp(), "meminfo")
        with open(p, "w") as f:
            f.write("MemTotal:       %d kB\nMemFree: 1 kB\n" % kb)
        return p

    def with_mem(self, n):
        old = list(config._mem_total)
        config._mem_total[:] = [n]
        self.addCleanup(lambda: config._mem_total.__setitem__(slice(None), old))

    def test_reader(self):
        config._mem_total.clear()
        try:
            self.assertEqual(config.mem_total(self.meminfo(1024)), 1024 * 1024)
        finally:
            config._mem_total.clear()
        config._mem_total.clear()
        try:
            self.assertIsNone(config.mem_total("/no/such/file"))
        finally:
            config._mem_total.clear()

    def test_small_box_gets_the_smaller_budgets(self):
        self.with_mem(1024 ** 3)
        self.assertTrue(config.low_ram())
        self.assertEqual(images.mem_budget(), 32 * images.MB)
        a = playback.video_args("mpv", SETTINGS, "/x", (0, 41))
        self.assertIn("--demuxer-max-bytes=48MiB", a)
        self.assertIn("--demuxer-max-back-bytes=16MiB", a)

    def test_big_or_unknown_box_is_unchanged(self):
        for n in (8 * 1024 ** 3, None):
            self.with_mem(n)
            self.assertFalse(config.low_ram())
            self.assertEqual(images.mem_budget(), 96 * images.MB)
            a = playback.video_args("mpv", SETTINGS, "/x", (0, 41))
            self.assertFalse(any(x.startswith("--demuxer-max") for x in a))

    def test_memory_cache_keeps_to_the_byte_budget(self):
        import pygame
        c = images.ImageCache(FakeProvider(), tempfile.mkdtemp(), workers=0, prune_disk=False,
                              max_bytes=3 * 100 * 100 * 4)
        for i in range(5):
            c._land(c.key("r%d" % i, 100, 100, 1.0), pygame.Surface((100, 100), 0, 32))
        self.assertEqual([k[0] for k in c.mem], ["r2", "r3", "r4"])
        self.assertEqual(c.mem_bytes, 3 * 100 * 100 * 4)


if __name__ == "__main__":
    unittest.main()
