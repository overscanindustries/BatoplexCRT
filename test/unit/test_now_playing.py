import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, inputs  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import nowplaying as npm  # noqa: E402


class Music:
    def __init__(self):
        self.items = [Item("t%d" % i, "track", "Track %d" % i, duration_ms=200000, artist="Band",
                           album="LP") for i in range(14)]
        self.index, self.state, self.version = 2, "playing", 1
        self.session_id = "s1"
        self.pos_s, self.dur_s, self.shuffle, self.repeat, self.cache_wait = 30.0, 200.0, False, "off", False
        self.calls = []
        self.live, self.icy = False, ""

    @property
    def now_title(self):
        return self.track.title if self.track is not None else ""

    @property
    def active(self):
        return self.state != "idle"

    @property
    def track(self):
        return self.items[self.index] if self.active else None

    def __getattr__(self, name):          # toggle, next, prev, seek, jump, remove, ...
        return lambda *a: self.calls.append((name,) + a) or "all"


class App:
    def __init__(self):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.music, self.stack, self.images, self.toasts = Music(), [], None, []
        self.sources = None

    @property
    def top(self):
        return self.stack[-1]

    def push(self, s):
        self.stack.append(s)

    def pop(self, result=None):
        self.stack.pop()

    def toast(self, m):
        self.toasts.append(m)

    def open_visualizer(self):
        self.toasts.append("visualizer")


SAFE = ({"left": 40, "right": 40, "top": 36, "bottom": 36},
        {"left": 72, "right": 72, "top": 56, "bottom": 56})


class TestNowPlaying(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.app = App()
        self.s = npm.NowPlayingScreen(self.app)
        self.app.push(self.s)

    def test_layout(self):
        for safe, art, art_xy, btn_y in ((SAFE[0], 188, (226, 72), 396),
                                         (SAFE[1], 148, (246, 92), 376)):
            self.app.settings["safe"] = safe
            g = self.s.geo()
            self.assertEqual((g["art"], (g["art_x"], g["art_y"]), g["btn_y"]), (art, art_xy, btn_y))
            self.assertEqual((self.s.btn_rect(0, g).x, self.s.btn_rect(6, g).right), (128, 512))
            self.assertGreaterEqual(g["art_y"], g["T"] + 36)
            self.assertLessEqual(g["btn_y"] + 40, 480 - safe["bottom"])
            self.s.draw(pygame.Surface((641, 480)))

    def test_keys(self):
        m = self.app.music
        self.s.handle(inputs.OK)
        self.assertEqual(m.calls[-1], ("toggle",))
        self.s.handle(inputs.LEFT)
        self.s.handle(inputs.OK)
        self.assertEqual(m.calls[-1], ("prev",))
        self.s.handle(inputs.UP)
        self.s.handle(inputs.RIGHT)
        self.assertEqual(m.calls[-1], ("seek", 10))
        self.s.handle(inputs.RT)
        self.assertEqual(m.calls[-1], ("next",))
        self.s.handle(inputs.DOWN)
        for _ in range(5):
            self.s.handle(inputs.RIGHT)
        self.s.handle(inputs.OK)                       # the last button: Visualizer
        self.s.handle(inputs.Y)                        # Y: the same
        self.assertEqual(self.app.toasts, ["visualizer", "visualizer"])
        self.s.handle(inputs.LEFT)
        self.s.handle(inputs.OK)                       # Up Next: the queue view
        self.assertEqual(self.s.view, "queue")

    def test_closes_when_the_session_ends(self):
        self.app.music.state = "idle"
        self.s.update()
        self.assertEqual(self.app.stack, [])

    def test_queue_view(self):
        m, s = self.app.music, self.s
        s.handle(inputs.DOWN)                          # Down on the buttons
        self.assertEqual((s.view, s.qfocus), ("queue", 2))
        self.assertNotIn("Remove from Queue", [t for t, _ in s.menu_items()])   # current row
        s.handle(inputs.DOWN)
        s.handle(inputs.OK)
        self.assertEqual(m.calls[-1], ("jump", 3))
        m.index, m.version = 3, 2                      # the track changed: focus stays on it
        s.update()
        self.assertEqual(s.qfocus, 3)
        m.index, m.version = 4, 3                      # on the current row it follows
        s.update()
        self.assertEqual(s.qfocus, 4)
        s.handle(inputs.UP)
        s.handle(inputs.BACK)
        self.assertEqual(s.view, "player")
        for safe in SAFE:
            self.app.settings["safe"] = safe
            g = s.qgeo()
            self.assertEqual(g["top"] + g["n"] * 36 - 4 <= 480 - safe["bottom"] - 8, True)

    def test_remove_resolves_the_row_when_it_runs(self):
        m, s = self.app.music, self.s
        s.set_view("queue")
        s.qfocus = 5
        remove = dict(s.menu_items())["Remove from Queue"]
        m.items.insert(0, m.items.pop(9))              # the queue moved under the open menu
        remove()
        self.assertEqual(m.calls[-1], ("remove", 6))
        target = m.items[6]
        m.index = 6                                    # the row plays now: no removal
        self.assertFalse(s.remove_row("s1", target, 6))
        m.index = 2
        self.assertFalse(s.remove_row("s2", target, 6))   # another session
        self.assertIsNone(npm.resolve(m, "s1", Item("x", "track", "X"), 0))
        m.items.insert(1, target)                      # the same Item twice: the nearest
        self.assertEqual(npm.resolve(m, "s1", target, 6), 7)

    def test_no_queue_view_for_radio(self):
        self.app.music.live = True
        self.s.handle(inputs.DOWN)
        self.s.set_view("queue")
        self.assertEqual(self.s.view, "player")


class TestMark(unittest.TestCase):
    def test_mark_width_and_hidden_on_now_playing(self):
        pygame.init()
        app = App()
        scr = Screen(app)
        s = pygame.Surface((641, 480))
        w = scr.music_mark(s, 500)
        self.assertGreater(w, 30)
        self.assertLessEqual(w, 160 + 30)
        app.music.state = "idle"
        self.assertEqual(scr.music_mark(s, 500), 0)
        app.music.state = "playing"
        self.assertEqual(npm.NowPlayingScreen(app).music_mark(s, 500), 0)


if __name__ == "__main__":
    unittest.main()
