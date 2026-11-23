"""On the migrated screens, text is 18 px or more (tag caps 16) and stays
inside the safe area at the smallest and largest margins."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import draw, fonts, tokens  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import nowplaying as npm, album as am  # noqa: E402
from test_now_playing import App as NPApp  # noqa: E402
from test_album_screen import App as AlbumApp, tracks  # noqa: E402

MARGINS = ({"left": 16, "right": 16, "top": 16, "bottom": 16},
           {"left": 72, "right": 72, "top": 56, "bottom": 56})


class TextRecorder:
    def __init__(self):
        self.rects, self.real = [], draw.text

    def __enter__(self):
        def rec(surf, s, style, color, x, y, w=None, align="left", alpha=None):
            used = self.real(surf, s, style, color, x, y, w=w, align=align, alpha=alpha)
            _f, line, _u = fonts.style(style)
            tx = x - used if align == "right" else (x + ((w or 0) - used) // 2
                                                     if align == "center" else x)
            if s:
                self.rects.append((style, s, pygame.Rect(int(tx), int(y), used, line)))
            return used
        draw.text = rec
        return self

    def __exit__(self, *a):
        draw.text = self.real


class TestScreenText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def check(self, scr, app):
        for safe in MARGINS:
            app.settings["safe"] = safe
            box = pygame.Rect(safe["left"], safe["top"], 640 - safe["left"] - safe["right"],
                              480 - safe["top"] - safe["bottom"])
            with TextRecorder() as t:
                scr.draw(pygame.Surface((641, 480)))
            self.assertTrue(t.rects)
            for style, s, r in t.rects:
                self.assertTrue(box.contains(r), (type(scr).__name__, s, r, safe))
                self.assertGreaterEqual(tokens.TYPE[style][1], 16 if style == "tag" else 18,
                                        (s, style))

    def test_now_playing_and_up_next(self):
        app = NPApp()
        s = npm.NowPlayingScreen(app)
        app.push(s)
        self.check(s, app)
        s.set_view("queue")
        self.check(s, app)

    def test_album(self):
        real = Screen.run_bg
        Screen.run_bg = lambda scr, fn, done, fail=None: done(fn())
        try:
            app = AlbumApp(tracks(14, discs=True))
            s = am.AlbumScreen(app, am.Item("a1", "album", "A Long Album Title That Wraps",
                                            artist="Band", year=1984, server_id="S"),
                               library="Music")
            app.push(s)
            s.handle("right")
            self.check(s, app)
        finally:
            Screen.run_bg = real
