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

from batoplex import config, rain, saver, saveropts  # noqa: E402
from batoplex.model import Item  # noqa: E402


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


class Images:
    def __init__(self):
        self.ready = set()
        self.asked = []

    def get(self, ref, w, h, gain=1.0, request=True):
        if request:
            self.asked.append((ref, w, h))
        return pygame.Surface((w, h)) if ref in self.ready else None


class TestBounce(unittest.TestCase):
    def test_stays_inside_even_y_and_moves_every_second_field(self):
        area = pygame.Rect(40, 36, 560, 408)
        for seed in range(20):
            b = saver.Bounce(area, (112, 168), random.Random(seed))
            moves = 0
            for f in range(2000):
                moved = b.step()
                moves += moved
                self.assertEqual(moved, f % 2 == 1)
                self.assertTrue(area.contains(pygame.Rect(b.x, b.y, b.w, b.h)))
                self.assertEqual(b.y % 2, 0)
            self.assertEqual(moves, 1000)

    def test_turns_at_the_edge(self):
        b = saver.Bounce(pygame.Rect(0, 0, 100, 100), (10, 10), random.Random(1))
        b.x, b.y, b.dx, b.dy = 88, 50, 2, 2
        b.step()
        b.step()
        self.assertEqual((b.x, b.dx, b.hit), (90, 2, False))
        b.step()
        b.step()
        self.assertEqual((b.dx, b.hit), (-2, True))
        self.assertLessEqual(b.x + b.w, 100)

    def test_text_line(self):
        s1 = pygame.Rect(40, 36, 560, 408)
        s2 = pygame.Rect(72, 56, 496, 368)
        self.assertEqual((saver.text_y(s1, 360, 480), saver.text_y(s1, 0, 120)), (380, 56))
        self.assertEqual((saver.text_y(s2, 360, 480), saver.text_y(s2, 0, 120)), (372, 68))


class App:
    def __init__(self, kind):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings["screensaver_type"] = kind
        self.secrets = types.SimpleNamespace(get=lambda k, d=None: "Den" if k == "server_name" else d)
        self.sources = types.SimpleNamespace(home=types.SimpleNamespace(server_name="Den"))
        self.images = Images()
        self.src_gen = 1
        self.saver = None


def items(n):
    return [Item(str(i), "movie", "M%d" % i, year=1990, poster="p%d" % i, art="a%d" % i)
            for i in range(n)]


class TestModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.real_load = saver.Screensaver.load
        saver.Screensaver.load = lambda self: None          # no worker in a unit test

    def tearDown(self):
        saver.Screensaver.load = self.real_load

    def make(self, kind, **opts):
        app = App(kind)
        for k, v in opts.items():
            saveropts.put(app.settings, kind, k, v)
        clk = Clock()
        fields = iter(range(1, 10 ** 9))                     # the frame clock: a field a call
        sv = saver.Screensaver(app, clock=clk, rng=random.Random(3),
                               ms=lambda: next(fields) * saver.tokens.FIELD_MS)
        app.saver = sv
        return app, sv, clk

    def test_name_until_items_then_poster(self):
        app, sv, clk = self.make("bounce")
        b = sv.impl
        self.assertEqual((sv.mode, sv.prefiltered), ("bounce", True))
        self.assertIsNotNone(b.name)
        sv._loaded(1, items(3))
        self.assertIsNotNone(b.name)                        # the image is not in yet
        app.images.ready.update(["p0", "p1", "p2"])
        sv.update()
        self.assertIsNone(b.name)
        o = b.objs[0]
        self.assertEqual((o.b.w, o.b.h), (112, 168))
        k = o.k
        for _ in range(4000):                                # wall hits before 20 s: no change
            sv.update()
        self.assertEqual(o.k, k)
        clk.t += 21
        for _ in range(4000):
            sv.update()
            if o.k != k:
                break
        self.assertNotEqual(o.k, k)
        self.assertTrue(o.b.hit)
        sv.draw(pygame.Surface((641, 480)))

    def test_count_paths_and_distinct_posters(self):
        app, sv, clk = self.make("bounce", count=5, size="small")
        sv._loaded(1, items(8))
        app.images.ready.update("p%d" % i for i in range(8))
        for _ in range(10):
            sv.update()
        objs = sv.impl.objs
        self.assertEqual(len(objs), 5)
        self.assertEqual(len({o.k for o in objs}), 5)
        self.assertEqual({(o.b.w, o.b.h) for o in objs}, {(80, 120)})
        starts = {(o.b.x, o.b.y, o.b.dx, o.b.dy) for o in objs}
        self.assertEqual(len(starts), 5)
        self.make("bounce", count=5)[1]._loaded(1, items(2))   # fewer posters than objects

    def test_dvd_logo_changes_colour_only_at_a_wall(self):
        app, sv, clk = self.make("bounce", art="dvd", count=3)
        self.assertFalse(sv.needs_items())
        objs = sv.impl.objs
        self.assertEqual(len(objs), 3)
        hits = 0
        for _ in range(6000):
            before = [o.color for o in objs]
            sv.update()
            for o, c in zip(objs, before):
                if o.b.hit:
                    hits += 1
                    self.assertNotEqual(o.color, c)
                else:
                    self.assertEqual(o.color, c)
        self.assertGreater(hits, 20)
        sv.draw(pygame.Surface((641, 480)))
        app, sv, clk = self.make("bounce", art="dvd", colors=False)
        self.assertIsNone(sv.impl.objs[0].color)

    def test_speeds(self):
        for speed, (px, every) in (("slow", (2, 2)), ("normal", (2, 1)), ("fast", (4, 1))):
            app, sv, clk = self.make("bounce", art="dvd", speed=speed)
            b = sv.impl.objs[0].b
            moves = 0
            for f in range(1200):
                x, y = b.x, b.y
                if sv.update():
                    moves += 1
                    self.assertLessEqual(abs(b.x - x), px)
                self.assertEqual(b.y % 2, 0)
                self.assertTrue(sv.area.contains(pygame.Rect(b.x, b.y, b.w, b.h)))
            self.assertAlmostEqual(moves, 600 // every, delta=2)

    def test_stale_items_dropped(self):
        app, sv, clk = self.make("posters")
        app.src_gen = 2                                      # signed out meanwhile
        sv._loaded(1, items(3))
        self.assertEqual(sv.items, [])
        app.src_gen = 1
        app.saver = None                                     # woke meanwhile
        sv._loaded(1, items(3))
        self.assertEqual(sv.items, [])

    def test_no_items_keeps_the_name(self):
        app, sv, clk = self.make("art")
        sv._loaded(1, [])
        for _ in range(10):
            sv.update()
        self.assertEqual(sv.mode, "name")
        s = pygame.Surface((641, 480))
        sv.draw(s)

    def test_entertainment_changes_and_band_side(self):
        app, sv, clk = self.make("art")
        sv._loaded(1, items(3))
        app.images.ready.update(["a0", "a1", "a2"])
        self.assertTrue(sv.update())
        self.assertEqual(sv.mode, "art")
        quiet = [sv.update() for _ in range(100)]
        self.assertFalse(any(quiet))                         # nothing to draw between changes
        clk.t += 21
        fades = [sv.update() for _ in range(30)]
        self.assertEqual(sum(fades), saver.FADE_FIELDS)
        self.assertEqual(sv.k, 1)
        sv.draw(pygame.Surface((641, 480)))

    def test_failed_reload_keeps_items_and_new_items_reset_the_art(self):
        app, sv, clk = self.make("art")
        sv._loaded(1, items(3))
        app.images.ready.update(["a0", "a1", "a2"])
        sv.update()
        self.assertEqual(sv.mode, "art")
        sv._loaded(1, [])
        self.assertEqual((len(sv.items), sv.mode), (3, "art"))
        sv._loaded(1, items(2))
        self.assertEqual((sv.cur, sv.mode), (None, "name"))
        sv.update()
        self.assertEqual(sv.mode, "art")


    def test_rain_prefiltered_only_while_it_shows_and_music_wins(self):
        app, sv, clk = self.make("rain")
        loads = []
        sv.load = lambda: loads.append(1)
        sv.sources_changed()                                 # a server join: no item load
        self.assertEqual(loads, [])
        self.assertTrue(sv.prefiltered)
        app.images.ready.add("cover")
        app.now_playing_art = lambda: "cover"                # a track plays: its art bounces
        sv.update()
        self.assertEqual(sv.mode, "music")
        self.assertTrue(sv.prefiltered)                      # album art: a filtered sprite
        app.now_playing_art = lambda: None                   # pause or stop: the rain again
        self.assertTrue(sv.update())
        self.assertEqual(sv.mode, "rain")
        self.assertTrue(sv.prefiltered)
        sv.draw(pygame.Surface((641, 480)))


class TestOptions(unittest.TestCase):
    def settings(self, d):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        with open(p, "w") as f:
            import json
            json.dump(d, f)
        return config.Settings(p)

    def test_migration(self):
        for old, new in (("posters", "bounce"), ("name", "bounce"), ("bogus", "bounce"),
                         ("rain", "rain"), ("art", "art")):
            st = self.settings({"screensaver_type": old})
            self.assertEqual(st["screensaver_type"], new, old)
        st = self.settings({"saver_opts": "junk"})
        self.assertEqual(st["saver_opts"], {})

    def test_defaults_put_and_hidden_rows(self):
        st = self.settings({})
        self.assertEqual(saveropts.get(st, "bounce", "art"), "posters")
        self.assertEqual(saveropts.get(st, "rain", "music"), "vis")
        keys = [o[0] for o in saveropts.visible(st, "bounce")]
        self.assertNotIn("colors", keys)                     # posters: no colour row
        saveropts.put(st, "bounce", "art", "dvd")
        self.assertIn("colors", [o[0] for o in saveropts.visible(st, "bounce")])
        self.assertEqual(keys[-1], "music")
        saveropts.put(st, "bounce", "art", "posters")        # back to the default: not kept
        self.assertEqual(st["saver_opts"], {})
        st["saver_opts"] = {"rain": {"size": "huge"}}        # a value no longer offered
        self.assertEqual(saveropts.get(st, "rain", "size"), "medium")
        self.assertEqual([o[0] for o in saveropts.visible(st, "art")], ["music"])


class TestSaverScreen(unittest.TestCase):
    def test_rows_steps_and_preview(self):
        from batoplex import inputs
        from batoplex.screens import saverset
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        calls = []
        app = types.SimpleNamespace(settings=st, saved=lambda ok: None, push=calls.append,
                                    pop=lambda: calls.append("pop"),
                                    start_saver=lambda k, preview: calls.append((k, preview)))
        scr = saverset.SaverScreen(app)
        keys = [r[1] for r in scr.rows()]
        self.assertEqual(keys, ["min", "type", "opt:art", "opt:count", "opt:speed", "opt:size",
                                "opt:music", "preview"])
        scr.focus = 2
        scr.handle(inputs.RIGHT)                              # Art: DVD Video Logo
        self.assertIn("opt:colors", [r[1] for r in scr.rows()])
        scr.focus = 1
        scr.handle(inputs.RIGHT)                              # Type: Entertainment
        self.assertEqual(st["screensaver_type"], "art")
        self.assertEqual([r[1] for r in scr.rows()], ["min", "type", "opt:music", "preview"])
        for _ in range(len(saveropts.SAVERS)):
            scr.handle(inputs.RIGHT)                          # clamps at the last type
        self.assertEqual(st["screensaver_type"], list(saveropts.SAVERS)[-1])
        st["screensaver_type"] = "rain"
        scr.focus = 0
        scr.handle(inputs.LEFT)
        scr.handle(inputs.OK)                                 # A wraps: 3 -> 5
        self.assertEqual(st["screensaver_min"], 5)
        scr.focus = len(scr.rows()) - 1
        scr.handle(inputs.OK)
        self.assertEqual(calls[-1], ("rain", True))
        scr.focus = 1
        scr.handle(inputs.OK)                                 # A on Type: the type list
        self.assertIsInstance(calls[-1], saverset.TypeScreen)
        self.assertEqual(saveropts.get(st, "bounce", "art"), "dvd")   # kept for each type

    def test_aquarium_rows_and_species_list(self):
        from batoplex import inputs
        from batoplex.screens import saverset
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        st["screensaver_type"] = "aquarium"
        calls = []
        app = types.SimpleNamespace(settings=st, saved=lambda ok: None, push=calls.append,
                                    pop=lambda: None)
        scr = saverset.SaverScreen(app)
        rows = [r[1] for r in scr.rows()]
        self.assertEqual(rows[2:6], ["opt:tank", "opt:fish", "species", "opt:frogs"])
        self.assertIn("opt:big", rows)
        self.assertEqual(scr.rows()[4][3], "14 of 18")
        saveropts.put(st, "aquarium", "tank", "reef")         # no frogs; sharks stay
        rows = [r[1] for r in scr.rows()]
        self.assertNotIn("opt:frogs", rows)
        self.assertIn("opt:big", rows)
        saveropts.put(st, "aquarium", "tank", "fresh")        # frogs; no sharks
        rows = [r[1] for r in scr.rows()]
        self.assertIn("opt:frogs", rows)
        self.assertNotIn("opt:big", rows)
        self.assertEqual(scr.rows()[4][3], "8 of 8")
        sp = saverset.species_screen(app)
        self.assertEqual(len(sp.keys), 8)
        sp.handle(inputs.OK)                                  # Goldfish off
        self.assertEqual(saveropts.aq_species(st, "fresh"), (saveropts.aq_choices("fresh")[1:],
                                                              False))
        sp.handle(inputs.OK)                                  # on again: the default, not kept
        self.assertEqual(st["saver_opts"]["aquarium"], {"tank": "fresh"})
        for k in range(8):                                    # all off but the last one
            sp.focus = k
            if k < 7:
                sp.handle(inputs.OK)
        sp.focus = 7
        sp.handle(inputs.OK)
        self.assertEqual(saveropts.aq_species(st, "fresh")[0], ["cory"])
        self.assertEqual(saveropts.aq_species(st, "reef"), (saveropts.aq_choices("reef"), True))
        saveropts.put(st, "aquarium", "tank", "mixed")
        mixed = saverset.species_screen(app)
        self.assertEqual(len(mixed.keys), 18)
        self.assertEqual([lb for k, lb in mixed.all if k == "head"], ["Reef", "Freshwater"])

    def test_preview_lb_rb_show_rare_moments_and_other_buttons_wake(self):
        import collections
        from batoplex.app import App
        woke = []
        moments = []
        sv = types.SimpleNamespace(preview=True, rare=lambda step: moments.append(step) or True)
        q = collections.deque()
        inp = types.SimpleNamespace(queue=q, take_activity=lambda: True, flush=q.clear)
        app = types.SimpleNamespace(inputs=inp, saver=sv, ambient=types.SimpleNamespace(
            stop=lambda: False), drop_saver=lambda: woke.append(1), _snapshot=lambda: None,
            top=types.SimpleNamespace(anim=False), music=types.SimpleNamespace(active=False))
        q.extend([("rb", False), ("rb", False), ("lb", False)])
        App._idle_tick(app)
        self.assertEqual((moments, woke, list(q)), ([1, 1, -1], [], []))
        q.extend([("rb", False), ("ok", False)])
        App._idle_tick(app)
        self.assertEqual(woke, [1])
        sv.preview, woke[:] = False, []                       # the idle saver: RB wakes it
        q.append(("rb", False))
        App._idle_tick(app)
        self.assertEqual(woke, [1])


class TestRainOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_green_is_the_first_table_and_colours_are_crt_safe(self):
        self.assertEqual(rain.levels("green"), rain.LEVELS)
        for c in ("amber", "white", "accent"):
            lv = rain.levels(c, accent=(224, 164, 44))
            self.assertEqual(len(lv), 5)
            for rgb in lv:
                self.assertLessEqual(max(rgb), 235)
                self.assertLessEqual((max(rgb) - min(rgb)) / max(rgb), 0.63)
            self.assertGreater(max(lv[0]), max(lv[4]))       # head brightest, tail darkest

    def test_grids_fit_and_glyphs_fit_their_cells(self):
        for size in ("small", "medium", "large"):
            g = rain.Grid(size)
            self.assertLessEqual(g.x0 + g.cols * g.cw, 641)
            self.assertEqual(g.rows * g.ch, 480)
            self.assertEqual(g.ch % 4, 0)
            for glyphs in ("kana", "ascii", "binary"):
                atlas = rain.build_atlas("normal", grid=g, glyphs=glyphs)
                self.assertEqual(len(atlas[0]), len(rain.GLYPHS[glyphs][1]))
                for cell in atlas[0]:
                    self.assertEqual(cell.get_size(), (g.cw, g.ch))
                    for x in range(g.cw):                      # edge rows stay black
                        self.assertEqual(cell.get_at((x, 0))[:3], (0, 0, 0))
                        self.assertEqual(cell.get_at((x, g.ch - 1))[:3], (0, 0, 0))

    def test_options_change_the_run(self):
        import random
        r = rain.Rain("off", random.Random(1), size="small", speed="fast", density="high",
                      glyphs="binary", color="amber")
        self.assertEqual((r.COLS, r.ROWS, r.nchars), (53, 24, 2))
        t = 0.0
        for _ in range(300):
            t += 1 / 60.0
            r.step(t)
        self.assertTrue(r.lit)
        a = rain.Rain("off", random.Random(5), size="large")
        self.assertEqual((a.COLS, a.ROWS, a.grid.x0), (26, 15, 8))


if __name__ == "__main__":
    unittest.main()
