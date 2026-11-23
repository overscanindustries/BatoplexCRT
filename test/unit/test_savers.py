"""The savers (240p canvas): Pong rules, Orbit, field equality."""
import os
import random
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, saver, saveropts, tokens  # noqa: E402
from batoplex.savers import Orbit, pong  # noqa: E402


def make(kind, seed=1):
    st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
    app = types.SimpleNamespace(settings=st, images=None, src_gen=1, sources=None,
                                secrets=types.SimpleNamespace(get=lambda k, d=None: d),
                                saver=None)
    t = [0.0]
    sv = saver.Screensaver(app, clock=lambda: t[0] / 1000, rng=random.Random(seed),
                           ms=lambda: t[0], kind=kind)
    app.saver = sv
    return sv, t


class TestCanvasSavers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_each_runs_moves_on_slots_and_fields_are_equal(self):
        for kind in saveropts.MODULES:
            if kind == "weather":
                continue                                  # path C: the frame filter
            sv, t = make(kind)
            self.assertTrue(sv.prefiltered)
            s = pygame.Surface((641, 480))
            drawn = []
            for f in range(240):
                t[0] += tokens.FIELD_MS
                drawn.append(sv.update())
            self.assertTrue(all(drawn[k] != drawn[k + 1] or not drawn[k]
                                for k in range(10, 238)), kind)       # never 2 fields in a row
            sv.draw(s)
            raw = pygame.image.tobytes(s, "RGB")
            row = 641 * 3
            for y in range(0, 480, 2):                                  # both fields equal
                self.assertEqual(raw[y * row:(y + 1) * row], raw[(y + 1) * row:(y + 2) * row],
                                 (kind, y))
            pix = [s.get_at((x, y))[:3] for x in range(0, 640, 7) for y in range(0, 480, 7)]
            self.assertTrue(all(max(p) <= 235 for p in pix), kind)
            self.assertTrue(any(max(p) > 30 for p in pix), kind)          # it drew something

    def test_orbit_stays_small(self):
        o = Orbit()
        pts = {o.at(ms) for ms in range(0, 3600 * 1000, 7000)}
        self.assertEqual(len(pts), len(Orbit.PATH))
        self.assertTrue(all(0 <= x <= 4 and 0 <= y <= 4 for x, y in pts))


class TestPong(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_points_are_scored_and_games_restart(self):
        for seed in range(20):
            sv, t = make("pong", seed)
            p = sv.impl
            first = None
            for f in range(60 * 60):                    # 60 s
                t[0] += tokens.FIELD_MS
                sv.update()
                if p.wait <= 0:
                    self.assertTrue(pong.TOP <= p.by <= pong.BOTTOM - pong.BALL)
                if first is None and sum(p.score):
                    first = f
            self.assertIsNotNone(first, seed)
        sv, t = make("pong", 3)
        p = sv.impl
        p.score = [10, 4]
        p.bx, p.vx, p.wait = pong.W + 1, 100.0, 0.0     # the ball leaves at the right
        p.step(0.01, 0)
        self.assertEqual((p.score, p.wait), ([11, 4], pong.END_S))
        p.step(pong.END_S + 0.1, 0)
        self.assertEqual(p.score, [0, 0])


REPLY = {"current": {"temperature_2m": 61.3, "relative_humidity_2m": 84, "apparent_temperature": 60,
                     "weather_code": 61, "wind_speed_10m": 6.9, "wind_direction_10m": 50,
                     "wind_gusts_10m": 14.3, "pressure_msl": 1019.1, "dew_point_2m": 56.4,
                     "is_day": 1},
         "daily": {"time": ["2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"],
                   "weather_code": [61, 3, 0, 95], "temperature_2m_max": [65.3, 66.1, 46, 48],
                   "temperature_2m_min": [52.8, 41.2, 38.1, 36.9],
                   "precipitation_probability_max": [72, 8, 0, 3]}}


class TestWeather(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_text_units_and_icons(self):
        from batoplex import weather
        self.assertEqual(weather.units_for("US"), "f")
        self.assertEqual(weather.units_for("GB"), "c")
        self.assertEqual(weather.sky(0, day=False), ("Clear", "clear"))
        self.assertEqual(weather.sky(2, day=False)[1], "partly_night")
        for code in weather.WMO:
            self.assertTrue(weather.sky(code)[0])
        lf = weather.local_forecast(REPLY)
        self.assertEqual([x[0] for x in lf], ["Today", "Tonight", "Tomorrow"])
        self.assertIn("Chance of precipitation 70 percent", lf[0][1])
        ex = weather.extended(REPLY)
        self.assertEqual([d[0] for d in ex], ["MON", "TUE", "WED"])
        self.assertEqual(ex[2][2], "storm")
        self.assertEqual(weather.wind_text(50, 6.9, "f"), "NE 7")
        self.assertEqual(weather.wind_text(0, 0.2, "f"), "Calm")
        g = {"hours": ["1 PM", "2 PM"], "cells": [[[0, 0.3], [0, 0]], [[0, 0], [0, 0]]]}
        fr = weather.grid_frames(g, "f", 4, 3)
        self.assertEqual((len(fr), len(fr[0])), (2, 12))
        self.assertEqual(fr[0], bytes(12))
        self.assertEqual(fr[1][0], 4)                      # 0.3 inch = 7.6 mm: level 4

    def test_pages_no_network_in_tests_and_music_rule(self):
        from batoplex import weather
        calls = []
        real = weather._get
        weather._get = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(OSError("off"))
        try:
            sv, t = make("weather")
            w = sv.impl
            sv.app.post = lambda fn: fn()
            self.assertFalse(sv.prefiltered)
            s = pygame.Surface((641, 480))
            for f in range(30):
                t[0] += tokens.FIELD_MS
                if sv.update():
                    sv.draw(s)
            self.assertEqual(w.pages, ["none"])
            w._fetched(({"name": "Den", "lat": 1, "lon": 2, "country": "US"}, "f", REPLY,
                        {"hours": ["1 PM"], "cells": [[[0.1]] * 3] * 3}))
            self.assertEqual(w.pages, ["current", "local", "extended", "precip"])
            bands = 0
            for f in range(60 * 45):                    # 45 s: each page, the crawl, the clock
                t[0] += tokens.FIELD_MS
                if sv.update():
                    sv.draw(s)
                    bands += sv.band() is not None
            self.assertGreater(bands, 1000)
            # the wake stops only the playlist Weather started
            mus = types.SimpleNamespace(session_id="x", active=True, stopped=0)
            mus.stop = lambda: setattr(mus, "stopped", mus.stopped + 1)
            sv.app.music = mus
            w.music_sid = "y"
            sv.close()
            self.assertEqual(mus.stopped, 0)
            w.music_sid = "x"
            sv.close()
            self.assertEqual(mus.stopped, 1)
        finally:
            weather._get = real


if __name__ == "__main__":
    unittest.main()
