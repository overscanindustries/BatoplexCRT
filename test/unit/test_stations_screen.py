import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, inputs, radio, stationdir  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import stations as sm  # noqa: E402
from test_stationdir import REPLY, make  # noqa: E402


class Music:
    active, live, track, version, state = False, False, None, 0, "idle"

    def __init__(self):
        self.dropped = []

    def drop_stations(self, urls):
        self.dropped.append(urls)


class App:
    def __init__(self):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.stack, self.posted, self.toasts = [], [], []
        self.music = Music()
        self.canvas = pygame.Surface((641, 480))

    def post(self, fn):
        self.posted.append(fn)

    def pop(self, result=None):
        self.stack.pop()

    def toast(self, msg):
        self.toasts.append(msg)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.real_bg, self.real_get = Screen.run_bg, stationdir.get
        self.held = None                       # a list: replies are kept here, not posted

        def run(scr, fn, done, fail=None):
            def go():
                try:
                    r = fn()
                except Exception as e:
                    scr.app.post(lambda e=e: fail and fail(e))
                    return
                scr.app.post(lambda: done(r))
            self.held.append(go) if self.held is not None else go()
        Screen.run_bg = run
        self.dir, self.op = make({"de1.api.radio-browser.info": REPLY})
        stationdir.get = lambda: self.dir
        if os.path.exists(radio.path()):
            os.remove(radio.path())
        self.app = App()
        self.s = sm.StationsScreen(self.app)
        self.app.stack.append(self.s)
        self.s.session.delay_s = 0

    def tearDown(self):
        Screen.run_bg, stationdir.get = self.real_bg, self.real_get
        if os.path.exists(radio.path()):
            os.remove(radio.path())

    def frames(self, n=3):
        for _ in range(n):
            while self.app.posted:
                self.app.posted.pop(0)()
            self.s.update()

    def press(self, *acts):
        for a in acts:
            self.s.handle(a)
        self.frames()


class TestStations(Base):
    def test_layout_in_safe_area_and_zones(self):
        self.frames()
        for safe in ({"left": 40, "right": 40, "top": 36, "bottom": 36},
                     {"left": 72, "right": 72, "top": 56, "bottom": 56}):
            self.app.settings["safe"] = safe
            self.s.zone = "keys"
            g = self.s.geo()
            last = self.s.key_rect(2, 12, g)
            self.assertGreaterEqual(g["keys_top"], g["T"] + 32 + 12)
            self.assertLessEqual(last.right, 640 - safe["right"])
            self.assertGreaterEqual(g["label_y"], last.bottom + 12)
            x, y, w, h = self.s.row_rect(0, g)
            self.assertGreaterEqual(x, safe["left"])
            self.assertLessEqual(x + w + 12, 640 - safe["right"])
            gap = sm.PITCH - sm.ROW_H
            self.assertLessEqual(y + 3 * sm.PITCH - gap, 480 - safe["bottom"])   # 3 rows show
            self.s.draw(self.app.canvas)
            self.s.zone = "list"
            g = self.s.geo()
            self.assertEqual(g["label_y"], g["T"] + 44)
            n = 5 if safe["top"] <= 36 else 4               # two-line rows of 60
            self.assertLessEqual(g["list_y"] + n * sm.PITCH - gap, 480 - safe["bottom"])
            self.s.draw(self.app.canvas)

    def test_popular_then_keys_hide_and_show(self):
        self.frames()
        self.assertEqual(self.op.reqs[0].full_url.split("/json/")[1],
                         "stations/topclick/50?hidebroken=true")
        self.assertEqual(self.s.session.rows[0].title, "Popular")
        self.press(inputs.DOWN, inputs.DOWN, inputs.DOWN)
        self.assertEqual(self.s.zone, "list")
        self.press(inputs.DOWN, inputs.UP, inputs.UP)
        self.assertEqual(self.s.zone, "keys")
        self.press("char:j")                       # one character: still Popular, no request
        self.assertEqual((len(self.op.reqs), self.s.session.rows[0].title), (1, "Popular"))

    def test_a_adds_then_removes(self):
        self.frames()
        self.press(inputs.DOWN, inputs.DOWN, inputs.DOWN)
        it = self.s.item
        self.assertFalse(self.s.added(it))
        self.press(inputs.OK)
        self.assertTrue(self.s.added(it))
        with open(radio.path()) as f:
            self.assertEqual(f.read(), "#EXTM3U\n#RADIOBROWSER:u-1\n#EXTINF:-1,Test Jazz One\n"
                                       "https://one.example/live\n")
        self.assertEqual([t for t, _f in self.s.menu_items()], ["Remove from Radio", "Play"])
        self.press(inputs.OK)
        self.assertFalse(self.s.added(it))
        with open(radio.path()) as f:
            self.assertEqual(f.read(), "#EXTM3U\n")
        self.assertEqual(self.app.music.dropped, [{"https://one.example/live"}])
        self.assertEqual(self.app.toasts, [])      # the row shows the result

    def test_hand_entry_of_the_alt_link_shows_added(self):
        with open(radio.path(), "w") as f:
            f.write("#EXTINF:-1,Mine\nhttps://one.example/listen.pls\n")
        self.s.on_resume()
        self.frames()
        self.assertTrue(self.s.added(self.s.rows[0]))

    def test_radio_is_full(self):
        with open(radio.path(), "w") as f:
            f.write("".join("https://h%d.example/\n" % i for i in range(radio.MAX_STATIONS)))
        self.frames()
        self.press(inputs.DOWN, inputs.DOWN, inputs.DOWN, inputs.OK)
        self.assertEqual(self.app.toasts, ["Radio is full"])
        self.assertFalse(self.s.added(self.s.item))

    def test_stale_reply_dropped_then_search(self):
        self.held = []
        self.frames()                              # Popular asked, its reply held
        self.press("char:j", "char:a")
        self.assertEqual(len(self.held), 1)
        go = self.held.pop()
        self.held = None
        go()
        self.frames()                              # the old reply drops, the search starts
        self.assertIn("search?name=ja&", self.op.reqs[-1].full_url)
        self.assertEqual(self.s.session.rows[0].title, "Stations")

    def test_late_reply_after_close_dropped(self):
        self.held = []
        self.frames()
        self.app.stack.clear()
        self.held.pop()()
        self.frames()
        self.assertEqual(self.s.rows, [])

    def test_error_then_retry(self):
        self.dir.opener.by_host["de1.api.radio-browser.info"] = OSError("down")
        self.frames()
        self.assertEqual(self.s.session.state, "error")
        self.s.draw(self.app.canvas)
        self.dir.opener.by_host["de1.api.radio-browser.info"] = REPLY
        self.s.keys.kr, self.s.keys.kc = 2, 12
        self.press(inputs.OK)                      # Clear tries again, also for Popular
        self.assertEqual(len(self.s.rows), 3)

    def test_query_not_in_log(self):
        lines = []
        h = logging.Handler()
        h.emit = lambda r: lines.append(r.getMessage())
        lg = logging.getLogger("batoplex")
        lg.addHandler(h)
        old = lg.level
        lg.setLevel(logging.DEBUG)
        try:
            self.press(*["char:" + c for c in "quokka"])
            self.frames()
        finally:
            lg.removeHandler(h)
            lg.setLevel(old)
        self.assertTrue(any("6 characters" in m for m in lines))
        self.assertFalse(any("quokka" in m for m in lines))


class TestPreview(Base):
    """X plays the focused station with no change of screen."""

    def test_x_plays_here_and_stops_the_music_queue(self):
        from batoplex import audio, music
        from batoplex.app import App as RealApp
        from batoplex.model import Item, PlayInfo
        from fake_mpv import FakeMpv

        def sync(fn, done, fail):
            try:
                r = fn()
            except Exception as e:
                fail(e)
                return
            done(r)
        mpvs = []
        a = audio.AudioPlayer("mpv", run=sync, make_mpv=lambda: mpvs.append(FakeMpv()) or mpvs[-1])
        m = music.MusicController(a, run=sync)
        prov = type("P", (), {"track_info": lambda self, t: PlayInfo(
            url="http://s/" + t.id, headers={"X-Plex-Token": "tok"}, duration_ms=1000),
            "timeline": lambda self, *x: None, "scrobble": lambda self, t: None})()
        m.play([Item("t1", "track", "Song", duration_ms=1000)], 0, prov)
        self.assertEqual((m.live, m.track.id), (False, "t1"))
        shown = []
        self.app.music, self.app.audio, self.app.playing = m, a, False
        self.app._theme_want = None
        self.app.open_now_playing = lambda: shown.append(1)
        self.app.play_radio = lambda st, i, show=True: RealApp.play_radio(self.app, st, i, show)
        self.frames()
        self.press(inputs.DOWN, inputs.DOWN, inputs.DOWN, inputs.X)
        self.assertEqual((m.live, m.track.extra["url"]), (True, "https://one.example/live"))
        a.tick()
        m.tick()
        loads = [c[1] for x in mpvs for c in x.cmds if c[0] == "loadfile"]
        self.assertEqual(loads[-1], "https://one.example/live")
        self.assertEqual((shown, self.app.stack), ([], [self.s]))
        self.assertTrue(self.s.playing(self.s.item))
        self.assertFalse(self.s.added(self.s.item))        # a preview writes nothing
        self.assertEqual([t for t, _f in self.s.menu_items()], ["Add to Radio", "Play"])


if __name__ == "__main__":
    unittest.main()
