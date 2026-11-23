"""A scrobble marks Home stale; Home loads again when it shows."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex.app import App  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens.home import HomeScreen  # noqa: E402
from test_music import Base  # noqa: E402


class TestScrobbleCallback(Base):
    def test_success_only_with_the_stamp_of_its_start(self):
        got, stamp = [], ["g1"]
        self.c.stamp = lambda: stamp[0]
        self.c.on_played = lambda t, s: got.append((t.id, s))
        self.start()
        stamp[0] = "g2"                              # a profile change after the start
        self.prop("time-pos", 99.5)
        self.prop("playlist-pos", 1)
        self.assertEqual(got, [("t0", "g2")])        # stamped when the scrobble starts
        self.p.scrobble = lambda t: (_ for _ in ()).throw(OSError("down"))
        self.prop("time-pos", 99.5)
        self.prop("playlist-pos", 1)
        self.assertEqual(len(got), 1)                # a failed scrobble: no callback


class Home:
    def __init__(self):
        self.stale = 0

    def mark_stale(self):
        self.stale += 1


class TestAppFilter(unittest.TestCase):
    def setUp(self):
        self.home = HomeScreen.__new__(HomeScreen)
        self.home.library, self.home.loader = None, lambda: []
        self.calls = []
        self.home.mark_stale = lambda: self.calls.append("stale")
        self.app = types.SimpleNamespace(src_gen=3, stack=[self.home],
                                         sources=types.SimpleNamespace(home_id="A"))

    def played(self, server, stamp):
        App.music_played(self.app, Item("t", "track", "T", section_id=9, server_id=server), stamp)

    def test_home_server_and_current_generation_only(self):
        self.played("A", 2)                          # an earlier profile or server
        self.played("B", 3)                          # section 9 of another server
        self.assertEqual(self.calls, [])
        self.played("A", 3)
        self.assertEqual(self.calls, ["stale"])


class TestHomeReload(unittest.TestCase):
    def make(self, top_is_home):
        h = HomeScreen.__new__(HomeScreen)
        h.loader, h.loads = (lambda: []), []
        h.reload = lambda keep_focus=False: h.loads.append(keep_focus)
        other = types.SimpleNamespace(under=None)
        h.app = types.SimpleNamespace(stack=[h] if top_is_home else [h, other])
        h.app.top = h.app.stack[-1]
        return h, other

    def test_visible_now_or_on_return(self):
        h, _ = self.make(True)
        h.mark_stale()
        self.assertEqual(h.loads, [True])
        h, other = self.make(False)
        h.mark_stale()
        self.assertEqual(h.loads, [])
        h.on_resume()
        h.on_resume()
        self.assertEqual(h.loads, [True])            # once
        h.stale, other.under = False, h               # a menu over Home: it shows
        h.mark_stale()
        self.assertEqual(h.loads, [True, True])


if __name__ == "__main__":
    unittest.main()
