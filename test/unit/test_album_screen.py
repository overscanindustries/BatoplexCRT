import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import draw, inputs  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import album as am  # noqa: E402
from test_now_playing import App as NPApp, SAFE  # noqa: E402


def tracks(n, discs=False):
    return [Item("t%d" % i, "track", "Song %d" % i, duration_ms=180000, index=i % 7 + 1,
                 disc=(i // 7 + 1) if discs else 1, artist="Band" if i != 2 else "Guest",
                 album="LP", album_id="a1", artist_id="b1", server_id="S")
            for i in range(n)]


class Sources:
    def __init__(self, result):
        self.result = result

    def tracks(self, album):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class App(NPApp):
    def __init__(self, result):
        super().__init__()
        self.sources = Sources(result)
        self.music.state = "idle"
        self.played, self.posted = [], []

    def play_music(self, items, index=0, shuffle=False):
        self.played.append((len(items), index, shuffle))

    def post(self, fn):
        self.posted.append(fn)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.real = Screen.run_bg

        def sync(scr, fn, done, fail=None):
            try:
                r = fn()
            except Exception as e:
                scr.app.post(lambda e=e: fail(e))
                return
            scr.app.post(lambda: done(r))
        Screen.run_bg = sync

    def tearDown(self):
        Screen.run_bg = self.real

    def make(self, result, follow=None):
        app = App(result)
        alb = Item("a1", "album", "A Long Album Title That Wraps", artist="Band", artist_id="b1",
                   year=1984, server_id="S")
        s = am.AlbumScreen(app, alb, follow=follow)
        app.push(s)
        while app.posted:
            app.posted.pop(0)()
        return app, s


class TestAlbum(Base):
    def test_layout(self):
        app, s = self.make(tracks(12))
        for safe, n, top, art, btn in ((SAFE[0], 10, 80, (40, 80, 176), (40, 160, 168, 216)),
                                       (SAFE[1], 9, 96, (72, 96, 144), (72, 160, 168, 216))):
            app.settings["safe"] = safe
            g = s.geo()
            self.assertEqual((g["n"], g["list_top"]), (n, top))
            self.assertEqual((g["art"].x, g["art"].y, g["art"].w), art)
            b0, b1 = s.btn_rect(0, g), s.btn_rect(1, g)
            self.assertEqual((b0.x, b0.right, b1.x, b1.right), btn)
            self.assertEqual(b0.bottom, 480 - safe["bottom"] - 8)
            self.assertLessEqual(draw.width("Play", "label") + 32, b0.w)   # Shuffle: an icon
            # the meta line ends above the buttons with a 2-line title
            self.assertLessEqual(g["title_y"] + 56 + 4 + 24 + 24, g["btn_y"])
            s.draw(pygame.Surface((641, 480)))

    def test_play_shuffle_and_track(self):
        app, s = self.make(tracks(12))
        s.handle(inputs.OK)
        s.handle(inputs.RIGHT)
        s.handle(inputs.OK)
        s.handle(inputs.RIGHT)
        self.assertEqual(s.zone, "list")
        for _ in range(4):
            s.handle(inputs.DOWN)
        s.handle(inputs.OK)
        self.assertEqual(app.played, [(12, 0, False), (12, 0, True), (12, 4, False)])
        s.handle(inputs.LEFT)
        self.assertEqual((s.zone, s.btn), ("buttons", 0))

    def test_discs_take_no_focus_and_follow(self):
        app, s = self.make(tracks(10, discs=True), follow="t8")
        self.assertEqual(s.rows[:1], [("disc", 1)])
        self.assertIn(("disc", 2), s.rows)
        self.assertEqual((s.zone, s.focus), ("list", 8))
        s.handle(inputs.UP)
        s.handle(inputs.UP)
        self.assertEqual(s.focus, 6)                    # over the disc line, onto a track
        s.draw(pygame.Surface((641, 480)))

    def test_error_and_retry(self):
        app, s = self.make(OSError("down"))
        self.assertTrue(s.error)
        s.draw(pygame.Surface((641, 480)))
        app.sources.result = tracks(3)
        s.handle(inputs.OK)
        while app.posted:
            app.posted.pop(0)()
        self.assertEqual(len(s.tracks), 3)

    def test_menu_rows(self):
        app, s = self.make(tracks(3))
        self.assertEqual(s.menu_items(), [])            # no session, no artist screen yet
        app.music.state = "playing"
        s.handle(inputs.RIGHT)
        s.handle(inputs.RIGHT)
        self.assertEqual([t for t, _ in s.menu_items()], ["Play Next", "Add to Queue"])


if __name__ == "__main__":
    unittest.main()
