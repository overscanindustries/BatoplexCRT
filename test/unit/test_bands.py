"""Band passes for presses, worker results and the screensaver bounce: each
frame is drawn in full, filtered only in its band, and must equal a full filter pass of the
same canvas (a band too small leaves stale rows on the CRT)."""
import os
import random
import sys
import tempfile
import types
import unittest
import zlib

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import anim, config, draw, saver, tokens  # noqa: E402
from batoplex.app import App, band_union  # noqa: E402
from batoplex.model import Item, Row  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens.home import HomeScreen  # noqa: E402
from batoplex.screens.library import GridScreen, LibraryScreen  # noqa: E402
from batoplex.screens.menu import MenuScreen  # noqa: E402
from batoplex.screens.panel import PanelScreen  # noqa: E402
from batoplex.screens.system import SettingsScreen  # noqa: E402
from batoplex.screens.views import GridView  # noqa: E402

FIELD = tokens.FIELD_MS / 1000
_clock = []


def setUpModule():
    """A fixed clock: a minute change between a band pass and its full pass is not a bug."""
    from batoplex import lineup
    _clock.append(lineup.clock_hm)
    lineup.clock_hm = lambda: "12:00"


def tearDownModule():
    from batoplex import lineup
    lineup.clock_hm = _clock.pop()


def stripes(key, w, h):
    """A surface with fine stripes in colours from key: rows differ, so the filter matters."""
    c = zlib.crc32(repr(key).encode())
    s = pygame.Surface((w, h))
    for y in range(0, h, 3):
        s.fill(((c >> (y % 7)) & 255, (c >> 8) & 255, (c >> 16 + y % 5) & 255), (0, y, w, 3))
    return s


class Images:
    version = 0

    def __init__(self):
        self.kept = {}

    def prefetch(self, *a):
        pass

    def get(self, ref, w, h, gain=1.0, request=True):
        if ref is None:
            return None
        k = (ref, w, h)
        if k not in self.kept:
            self.kept[k] = stripes(k, w, h)
        return self.kept[k]


class Sources:
    home_id, local_images, providers, order = "A", False, {}, ["A"]

    def server_count(self):
        return 1

    def server_name(self, sid):
        return "Server"

    def section_type(self, lib):
        return "movie"

    def pinned(self, st):
        return []

    def has_playlists(self):
        return False

    def library_page(self, lib, so, f, st, n):
        return ITEMS[st:st + n], len(ITEMS)

    def first_characters(self, lib, f):
        return [("A", 10), ("B", 20), ("C", 30)]

    def library_meta(self, lib):
        return {"failed": True}

    def recommended(self, lib):
        return []


ITEMS = [Item(str(i), "movie", "Title %d" % i, year=2000 + i, poster="p%d" % i)
         for i in range(60)]


def fake_app():
    app = types.SimpleNamespace(
        settings=config.Settings(os.path.join(tempfile.mkdtemp(), "s.json")),
        images=Images(), canvas=pygame.Surface((641, 480)), sources=Sources(), music=None,
        ambient=None, stack=[], version="1", source_key="home", vqueue=[],
        secrets=types.SimpleNamespace(get=lambda k: None), home_users_n=lambda: 1,
        post=lambda fn: None, toast=lambda m: None, open_panel=lambda **k: None,
        lib_tabs={}, src_gen=0)
    return app


def home_rows():
    long = "A Very Long Title That Needs Two Lines On The Home Screen"
    movies = [Item("m%d" % i, "movie", long if i % 2 else "Short %d" % i, year=1990 + i,
                   duration_ms=6000000, poster="mp%d" % i, art="ma%d" % i) for i in range(9)]
    albums = [Item("a%d" % i, "album", "Album %d" % i, artist="Artist", year=2001,
                   poster="ap%d" % i) for i in range(7)]
    people = [Item("c%d" % i, "person", "Person Number %d" % i, poster="cp%d" % i,
                   extra={"role": "Role %d" % i}) for i in range(6)]
    clips = [Item("x%d" % i, "clip", "Trailer %d" % i, poster="xp%d" % i, duration_ms=120000,
                  extra={"subtype": "trailer"}) for i in range(6)]
    return [Row("cw", "Continue Watching in Movies", movies),
            Row("al", "Recently Added Music", albums),
            Row("pe", "Cast & Crew", people), Row("cl", "Extras", clips)]


class Bench:
    """Frames the way App._frame runs them, with the band check after each one."""

    def __init__(self, tc, scr, app, mode="normal", draw_fn=None):
        self.tc, self.scr, self.app, self.mode = tc, scr, app, mode
        self.t = anim.now() / 1000 + 1
        self.canvas = app.canvas
        self.draw_fn = draw_fn or scr.draw
        self.f = draw.FlickerFilter((641, 480))
        self.ref = draw.FlickerFilter((641, 480))
        app.stack[:] = [scr]
        anim.tick(self.t)
        scr.update()
        self.draw_fn(self.canvas)
        self.run(60)
        self.f.full(self.canvas, mode)

    def frame(self, extra=None):
        """One field: update, the band of the screen and of extra, a full draw, then the band
        filter checked against a full pass. Returns the band used (None: a full pass)."""
        self.t += FIELD
        anim.tick(self.t)
        dirty = bool(self.scr.update())
        parts = ([self.scr.band()] if dirty else []) + ([extra] if extra is not None else [])
        before = pygame.image.tobytes(self.canvas, "RGB")
        self.draw_fn(self.canvas)
        if not parts:                             # no redraw: nothing may have changed
            self.tc.assertEqual(before, pygame.image.tobytes(self.canvas, "RGB"),
                                type(self.scr).__name__)
            return None
        rect = band_union(parts)
        if rect is None:
            self.f.full(self.canvas, self.mode)
            return None
        out = self.f.apply_rect(self.canvas, rect, self.mode)
        ref = self.ref.apply(self.canvas, self.mode)
        self.tc.assertEqual(pygame.image.tobytes(ref, "RGB"), pygame.image.tobytes(out, "RGB"),
                            "%s band %s" % (type(self.scr).__name__, tuple(rect)))
        return rect

    def run(self, n):
        for _ in range(n):
            self.frame()

    def press(self, a, settle=40):
        self.scr.press_band()
        self.scr.handle(a)
        pb = self.scr.press_band()
        self.tc.assertIsNotNone(pb, "%s %s gave no band" % (type(self.scr).__name__, a))
        self.tc.assertIsNotNone(self.frame(pb), a)
        self.run(settle)
        return pb


class TestHomeBands(unittest.TestCase):
    def setUp(self):
        pygame.display.init()
        pygame.font.init()

    def check(self, **kw):
        app = fake_app()
        h = HomeScreen(app, rows=home_rows(), **kw)

        def page(s):                              # with no backdrop the parent fills the bg
            if not h.backdrop:
                s.fill(draw.C["bg"])
            h.draw(s)
        b = Bench(self, h, app, draw_fn=page)
        for a in ("right", "right", "left", "right", "down", "right", "down", "right", "right",
                  "down", "right", "up", "up", "up"):
            b.press(a, settle=2 if a == "right" else 40)   # a press while the row slides too
        h.rows[0].loader, h.rows[0].total = (lambda st, n: [Item("n", "movie", "New")]), 99
        h.r, h.rows[0].focus, h.anim = 0, 7, True
        b.run(40)
        h.run_bg = lambda fn, done, fail=None: done(fn())   # a page lands (a worker result)
        b.press("right")
        self.assertEqual(h.rows[0].items[-1].id, "n")
        b.scr.press_band()
        h._maybe_page(h.rows[1])                             # nothing to load: no band
        self.assertIsNone(h.press_band())

    def test_home_moves(self):
        self.check()

    def test_detail_rows_without_backdrop(self):
        self.check(backdrop=False, root=False)

    def test_library_rows_view(self):
        app = fake_app()
        app.lib_tabs = {("A", 1): 0}

        class Lib(LibraryScreen):
            def run_bg(self, fn, done, fail=None):
                pass
        lib = Lib(app, Item("1", "library", "Movies", section_id=1, server_id="A"))
        v = lib.view
        v.set_rows(home_rows())
        v.loading = False
        b = Bench(self, lib, app)
        for a in ("right", "down", "right", "left", "down", "up", "up"):
            b.press(a)
        lib.press_band()
        lib.handle("up")                                  # into the tabs: a full pass
        self.assertIsNone(lib.press_band())

    def test_back_plate_equals_the_two_blits(self):
        app = fake_app()
        h = HomeScreen(app, rows=home_rows())
        for back in (None, stripes("b", 641, tokens.BACKDROP[1])):
            h.back_cur = back
            for depth in (32, 16):
                s = pygame.Surface((641, 480), 0, depth)
                h.draw(s)
                ref = pygame.Surface((641, 480), 0, depth)
                plate = h._back_plate

                def old(c):                       # the two blits on the bg fill, as before
                    if h.back_cur is not None:
                        c.blit(h.back_cur, (0, 0))
                    c.blit(h.scrim, (0, 0))
                    return pygame.Surface((0, 0))
                h._back_plate = old
                h.draw(ref)
                h._back_plate = plate
                self.assertEqual(pygame.image.tobytes(ref, "RGB"), pygame.image.tobytes(s, "RGB"),
                                 depth)


class Grid(GridScreen):
    def __init__(self, app):
        Screen.__init__(self, app)
        self.lib, self.title = Item("1", "library", "Movies", section_id=1), "Movies"
        self.grid = GridView(self, lambda so, f, st, n: (ITEMS[st:st + n], len(ITEMS)),
                             self.safe["top"] + 32, chips=("sort",),
                             letters=lambda f: [("A", 10), ("B", 20), ("C", 30)])

    def run_bg(self, fn, done, fail=None):
        done(fn())


class TestListBands(unittest.TestCase):
    def setUp(self):
        pygame.display.init()
        pygame.font.init()

    def test_grid_moves(self):
        app = fake_app()
        g = Grid(app)
        g.grid._load(1)                           # both pages in: a worker result is a full pass
        b = Bench(self, g, app)
        for a in ("right", "right", "down", "down", "down", "up", "rt", "lt", "right", "right",
                  "right", "right", "right", "down", "left", "up", "up", "up", "up", "up",
                  "right", "left", "down"):
            b.press(a)

    def test_library_grid_tab(self):
        app = fake_app()
        app.lib_tabs = {("A", 1): 1}

        class Lib(LibraryScreen):
            def run_bg(self, fn, done, fail=None):
                done(fn())
        lib = Lib(app, Item("1", "library", "Movies", section_id=1, server_id="A"))
        b = Bench(self, lib, app)
        for a in ("down", "right", "down", "up", "up", "up", "up"):   # the last: into the tabs
            b.press(a)

    def test_settings_list(self):
        app = fake_app()
        b = Bench(self, SettingsScreen(app), app)
        for a in ["down"] * 30 + ["up"] * 32:
            b.press(a, settle=1)

    def test_menu_list(self):
        app = fake_app()
        under = SettingsScreen(app)
        m = MenuScreen(app, under, "TITLE", [("Row %d" % i, None) for i in range(12)])
        b = Bench(self, m, app)
        for a in ["down"] * 13 + ["up"] * 13:
            b.press(a, settle=1)

    def test_panel_list(self):
        app = fake_app()
        under = SettingsScreen(app)
        p = PanelScreen(app, under)
        b = Bench(self, p, app)
        for a in ["down"] * 6 + ["up"] * 6:
            b.press(a, settle=1)


class Tiles(Screen):
    """A screen of striped tiles: a press, a screen band or an image changes one tile."""

    def __init__(self):
        Screen.__init__(self, None)
        self.tiles, self.dirty, self.ib = {}, None, None

    def set(self, r, key):
        self.tiles[tuple(r)] = key

    def update(self):
        self.b, self.dirty = self.dirty, None
        return self.b is not None

    def band(self):
        return self.b

    def image_band(self):
        return self.ib

    def draw(self, s):
        s.fill((12, 12, 12))
        for r, key in self.tiles.items():
            s.blit(stripes(key, r[2], r[3]), r[:2])


class TestAppFrame(unittest.TestCase):
    def test_frame_bands_equal_a_full_pass(self):
        pygame.display.init()
        pygame.font.init()
        a = App.__new__(App)
        a.pg = types.SimpleNamespace(Rect=pygame.Rect, Surface=pygame.Surface,
                                     display=types.SimpleNamespace(flip=lambda: None))
        scr = Tiles()
        r1, r2, r3 = (40, 61, 200, 33), (300, 200, 120, 51), (100, 381, 400, 17)
        for r in (r1, r2, r3):
            scr.set(r, ("start", r))
        a.stack, a.saver, a.trans, a.base, a.comp = [scr], None, None, None, None
        a.ambient_ok, a.ambient = False, types.SimpleNamespace(update=lambda *k: False)
        a.playback = types.SimpleNamespace(session="idle")
        a.images = types.SimpleNamespace(version=0, pump=lambda: None)
        a.music, a._music_v = types.SimpleNamespace(mark_version=0), 0
        a.settings = {"flicker_filter": "normal"}
        a.canvas, a.screen = pygame.Surface((641, 480)), pygame.Surface((641, 480))
        a.filter, ref = draw.FlickerFilter((641, 480)), draw.FlickerFilter((641, 480))
        a._glob, a._sig, a._pband, a.input_t, a.dirty_check = False, None, None, 0.0, False
        a._toast, a.toast_out, a._stats, a.last_minute = None, None, [0, 0], None
        bands = []
        rect_pass = a.filter.apply_rect
        a.filter.apply_rect = lambda c, r, m: (bands.append(tuple(r)), rect_pass(c, r, m))[1]

        def frame(why):
            a._frame()
            pre = a.saver is not None and a.saver.prefiltered      # sprites filtered one time
            want = a.canvas if pre else ref.apply(a.canvas, "normal")
            self.assertEqual(pygame.image.tobytes(want, "RGB"),
                             pygame.image.tobytes(a.base, "RGB"), why)
        frame("first")
        for k in range(3):
            n = len(bands)
            a._banded(lambda: (scr.set(r1, ("press", k)), setattr(scr, "_press", pygame.Rect(r1))))
            frame("press")
            scr.set(r2, ("screen", k))
            scr.dirty = pygame.Rect(r2)
            frame("screen")
            scr.ib = pygame.Rect(r3)
            scr.set(r3, ("image", k))
            a.images.version += 1
            frame("image")
            a._banded(lambda: (scr.set(r1, ("all", k)), setattr(scr, "_press", pygame.Rect(r1))))
            scr.set(r2, ("all", k))
            scr.dirty = pygame.Rect(r2)
            scr.set(r3, ("all", k))
            a.images.version += 1
            frame("all three")
            self.assertEqual(len(bands), n + 4)               # each one a band pass
        app = fake_app()
        app.saver = None
        app.settings["screensaver_type"] = "art"         # Entertainment: the name takes bands
        art, t = ["al1"], [0.0]
        app.now_playing_art = lambda: art[0]
        a.saver = app.saver = saver.Screensaver(app, clock=lambda: t[0] / 1000,
                                                rng=random.Random(5), ms=lambda: t[0])
        n = len(bands)
        for k in range(300):
            t[0] += tokens.FIELD_MS
            art[0] = {100: None, 151: "al2", 250: None}.get(k, art[0])
            frame("saver %d" % k)
        self.assertGreater(len(bands) - n, 40)          # the name; the album art is prefiltered


class TestAppBand(unittest.TestCase):
    def test_band_only_from_the_same_top(self):
        pygame.display.init()
        a = App.__new__(App)
        a.pg = pygame
        s1, s2 = Screen(None), Screen(None)
        a.stack, a._glob, a._pband = [s1], False, None
        s1._press = pygame.Rect(0, 0, 10, 10)            # an old value: never counts
        a._banded(lambda: None)
        self.assertTrue(a._glob)
        a._glob = False
        a._banded(lambda: setattr(s1, "_press", pygame.Rect(0, 100, 10, 10)))
        a._banded(lambda: setattr(s1, "_press", pygame.Rect(0, 200, 10, 10)))
        self.assertEqual((a._glob, tuple(a._pband)), (False, (0, 100, 10, 110)))
        a._banded(lambda: (setattr(s1, "_press", pygame.Rect(0, 0, 1, 1)), a.stack.append(s2)))
        self.assertTrue(a._glob)                         # a new screen on top: a full pass


class TestSaverBand(unittest.TestCase):
    def test_name_band_equals_a_full_pass(self):
        """Entertainment's name fallback takes the frame filter in bands."""
        pygame.display.init()
        pygame.font.init()
        app = fake_app()
        app.settings["screensaver_type"] = "art"
        app.sources = types.SimpleNamespace(saver_items=lambda st: [])
        t = [0.0]
        sv = saver.Screensaver(app, clock=lambda: t[0] / 1000, rng=random.Random(3),
                               ms=lambda: t[0])
        app.saver = sv
        sv.update()
        canvas = app.canvas
        sv.draw(canvas)
        f, ref = draw.FlickerFilter((641, 480)), draw.FlickerFilter((641, 480))
        f.full(canvas, "normal")
        bands = 0
        for n in range(600):
            t[0] += tokens.FIELD_MS
            if not sv.update():
                continue
            sv.draw(canvas)
            band = sv.band()
            if band is None:
                f.full(canvas, "normal")
                continue
            bands += 1
            out = f.apply_rect(canvas, band, "normal")
            self.assertEqual(pygame.image.tobytes(ref.apply(canvas, "normal"), "RGB"),
                             pygame.image.tobytes(out, "RGB"))
        self.assertGreater(bands, 250)

    def test_prefiltered_sprites_equal_the_frame_filter(self):
        """Bounce (posters, logos, the name) and the album art: a frame of sprites filtered one
        time equals the frame filter of the same frame drawn without it (objects apart)."""
        pygame.display.init()
        pygame.font.init()
        for kind, art in (("posters", "posters"), ("dvd", "dvd"), ("name", "posters"),
                          ("music", "dvd")):
            app = fake_app()
            from batoplex import saveropts
            saveropts.put(app.settings, "bounce", "art", art)
            saveropts.put(app.settings, "bounce", "music", "album")
            alb = ["al1" if kind == "music" else None]
            app.now_playing_art = lambda: alb[0]
            app.sources = types.SimpleNamespace(saver_items=lambda st: [
                Item(str(i), "movie", "T", poster="sp%d" % i) for i in range(3)])
            frames = []
            for mode in ("off", "normal"):
                app.settings["flicker_filter"] = mode
                t = [0.0]
                sv = saver.Screensaver(app, clock=lambda: t[0] / 1000, rng=random.Random(3),
                                       ms=lambda: t[0])
                app.saver = sv
                if kind != "name":
                    sv._loaded(sv.gen, app.sources.saver_items(None))
                for n in range(90):
                    t[0] += tokens.FIELD_MS
                    sv.update()
                self.assertTrue(sv.prefiltered, kind)
                c = pygame.Surface((641, 480))
                sv.draw(c)
                frames.append(c)
            want = draw.FlickerFilter((641, 480)).apply(frames[0], "normal")
            self.assertEqual(pygame.image.tobytes(want, "RGB"),
                             pygame.image.tobytes(frames[1], "RGB"), kind)


if __name__ == "__main__":
    unittest.main()
