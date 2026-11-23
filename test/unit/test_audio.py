import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import audio  # noqa: E402
from fake_mpv import FakeMpv  # noqa: E402
from test_playback import Clock, Runner  # noqa: E402


class TestAudioPlayer(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.mpvs = []

        def mk():
            m = FakeMpv()
            self.mpvs.append(m)
            return m
        self.a = audio.AudioPlayer("mpv", clock=self.clock, run=Runner(), make_mpv=mk)

    def vol(self):
        v = [c[2] for c in self.mpvs[-1].cmds if c[:2] == ["set_property", "volume"]]
        return v[-1] if v else None

    def test_starts_on_first_play_and_fades_in(self):
        self.a.play("http://s/theme", {"X-Plex-Token": "t"}, 50)
        self.assertEqual(len(self.mpvs), 1)
        self.a.tick()                                 # ready: load
        m = self.mpvs[-1]
        self.assertIn("--no-video", m.started_with)
        self.assertIn(["loadfile", "http://s/theme", "replace"], m.cmds)
        self.assertEqual(self.vol(), 0)
        self.clock.t += 0.5
        self.a.tick()
        self.assertEqual(self.vol(), 25)
        self.clock.t += 1
        self.a.tick()
        self.assertEqual(self.vol(), 50)

    def test_same_url_does_not_restart(self):
        self.a.play("u", {}, 50)
        self.a.tick()
        self.a.play("u", {}, 50)
        self.assertEqual(self.mpvs[-1].names().count("loadfile"), 1)

    def test_stop_at_once_for_video(self):
        self.a.play("u", {}, 50)
        self.a.tick()
        self.a.stop(fade=False)
        self.assertEqual(self.mpvs[-1].cmds[-1], ["stop"])
        self.assertIsNone(self.a.playing)

    def test_fade_out_then_stop(self):
        self.a.play("u", {}, 50)
        self.a.tick()
        self.clock.t += 2
        self.a.tick()
        self.a.stop()
        self.assertNotIn(["stop"], self.mpvs[-1].cmds)
        self.clock.t += audio.FADE_OUT_S + 0.01
        self.a.tick()
        self.assertIn(["stop"], self.mpvs[-1].cmds)
        self.assertEqual(self.vol(), 0)

    def test_play_after_stop_plays_again(self):
        self.a.play("u", {}, 50)
        self.a.tick()
        self.a.stop(fade=False)
        self.a.play("u", {}, 50)
        self.assertEqual(self.mpvs[-1].names().count("loadfile"), 2)

    def test_shutdown_reaps(self):
        self.a.play("u", {}, 50)
        self.a.tick()
        m = self.mpvs[-1]
        self.a.shutdown()
        self.assertFalse(m.alive)

    def test_start_failure_disables(self):
        def bad():
            raise RuntimeError("no mpv")
        a = audio.AudioPlayer("mpv", clock=self.clock, run=Runner(),
                              make_mpv=lambda: type("M", (), {"start": lambda *x, **k: bad()})())
        a.play("u", {}, 50)
        a.tick()
        self.assertTrue(a.failed)
        a.play("u2", {}, 50)                          # no retry loop


class TestThemeRule(unittest.TestCase):
    def make(self, screen, on=True):
        from batoplex.app import App
        app = App.__new__(App)
        app.settings = {"theme_music": on, "theme_volume": 50}
        app.stack = [screen]
        calls = []
        app.audio = type("A", (), {"play": lambda s, u, h, v: calls.append(("play", u, v)),
                                   "stop": lambda s, fade=True: calls.append(("stop",))})()
        from batoplex.sources import Sources
        app.sources = Sources([type("P", (), {"theme_url": lambda s, r: ("http://s" + r, {}),
                                              "name": "p"})()])
        app._theme_want = app._theme_played = None
        app._theme_since = 0.0
        app.music = type("M", (), {"active": False})()
        return app, calls

    def test_no_theme_while_music_plays(self):
        scr = type("S", (), {"theme_ref": lambda s: "/t/1"})()
        app, calls = self.make(scr)
        app.music.active = True
        app._theme_since -= 5
        from batoplex.app import App
        App._theme_tick(app)
        App._theme_tick(app)
        self.assertEqual(calls, [])

    def test_starts_after_1s_and_plays_once(self):
        import time as _t
        scr = type("S", (), {"theme_ref": lambda s: "/t/1"})()
        app, calls = self.make(scr)
        from batoplex.app import App
        App._theme_tick(app)
        self.assertNotIn(("play", "http://s/t/1", 50), calls)
        app._theme_since -= 1.1
        App._theme_tick(app)
        App._theme_tick(app)
        self.assertEqual(calls.count(("play", "http://s/t/1", 50)), 1)
        app.stack = [type("H", (), {})()]               # leave the show
        App._theme_tick(app)
        self.assertEqual(calls[-1], ("stop",))
        del _t

    def test_off_setting_plays_nothing(self):
        scr = type("S", (), {"theme_ref": lambda s: "/t/1"})()
        app, calls = self.make(scr, on=False)
        from batoplex.app import App
        app._theme_since = -10
        App._theme_tick(app)
        self.assertFalse([c for c in calls if c[0] == "play"])


if __name__ == "__main__":
    unittest.main()
