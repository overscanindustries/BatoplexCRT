import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, draw, inputs  # noqa: E402
from batoplex.app import App as RealApp  # noqa: E402
from batoplex.model import Item, Row  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import search as ss  # noqa: E402


class Prov:
    def __init__(self, sid, rows):
        self.server_id, self.rows, self.queries = sid, rows, []

    def search(self, q, limit=12):
        self.queries.append(q)
        return self.rows


class Sources:
    home_id = "H"

    def __init__(self, provs):
        self.provs = provs

    def search_providers(self, settings):
        return [(p.server_id, p) for p in self.provs]

    def server_name(self, sid):
        return "Den"


class App:
    def __init__(self, provs):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.sources = Sources(provs)
        self.src_gen, self.stack, self.images = 1, [], None
        self.opened, self.posted = [], []
        self.canvas = pygame.Surface((641, 480))

    def post(self, fn):
        self.posted.append(fn)

    def saved(self, ok):
        return ok

    def pop(self, result=None):
        self.stack.pop()

    top = property(lambda self: self.stack[-1])
    back = RealApp.back

    def open_item(self, it):
        self.opened.append(it)

    def auth_lost(self):
        pass


def mov(i, sid="H"):
    x = Item(str(i), "movie", "Movie %d" % i, year=1990, server_id=sid)
    x.extra["guid"] = "plex://movie/%d" % i
    return x


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.real_bg = Screen.run_bg

        def sync(scr, fn, done, fail=None):
            try:
                r = fn()
            except Exception as e:
                scr.app.post(lambda e=e: fail and fail(e))
                return
            scr.app.post(lambda: done(r))
        Screen.run_bg = sync
        self.home = Prov("H", [Row("movie", "Movies", [mov(i) for i in range(1, 9)]),
                               Row("show", "Shows", [Item("50", "show", "Show", server_id="H")])])
        self.app = App([self.home])
        self.s = ss.SearchScreen(self.app)
        self.app.stack.append(self.s)
        self.s.session.delay_s = 0

    def tearDown(self):
        Screen.run_bg = self.real_bg

    def frames(self, n=3):
        for _ in range(n):
            while self.app.posted:
                self.app.posted.pop(0)()
            self.s.update()

    def press(self, *acts):
        for a in acts:
            self.s.handle(a)

    def type(self, text):
        for c in text:
            self.s.handle("char:" + c)
        self.frames()


class TestKeys(Base):
    def test_grid_moves_no_wrap_and_side_column(self):
        self.press(inputs.LEFT, inputs.UP)
        self.assertEqual((self.s.kr, self.s.kc), (0, 0))
        self.press(*[inputs.RIGHT] * 20)
        self.assertEqual(self.s.kc, 12)
        self.press(inputs.OK)                      # Space: not typed at the start
        self.assertEqual(self.s.q, "")
        self.press(inputs.LEFT, inputs.OK, inputs.DOWN, inputs.OK)    # "l", then "x"
        self.assertEqual(self.s.q, "lx")
        self.press(inputs.RIGHT, inputs.OK)        # Delete
        self.assertEqual(self.s.q, "l")
        self.press(inputs.DOWN, inputs.OK)         # Clear
        self.assertEqual(self.s.q, "")

    def test_buttons_and_keyboard(self):
        self.press(inputs.OK, inputs.Y, inputs.OK)
        self.assertEqual(self.s.q, "a a")
        self.press(inputs.X, inputs.BACKSPACE)
        self.assertEqual(self.s.q, "a")
        self.press("char:b")
        self.assertTrue(self.s.typing)
        self.press(inputs.RIGHT)                   # an arrow shows the key focus again
        self.assertFalse(self.s.typing)
        self.assertEqual(self.s.kc, 0)

    def test_cut_at_40(self):
        self.type("x" * 50)
        self.assertEqual(len(self.s.q), 40)

    def test_back_pops(self):
        self.press(inputs.BACK)
        self.assertEqual(self.app.stack, [])


class TestResults(Base):
    def test_enter_goes_to_results_and_opens(self):
        self.type("mo")
        self.assertEqual(self.home.queries, ["mo"])
        self.assertEqual([r.key for r in self.s.rows], ["movie", "show"])
        self.press(inputs.OK)                      # typed: Enter goes to the first result
        self.assertEqual(self.s.zone, "results")
        self.press(inputs.RIGHT, inputs.RIGHT, inputs.RIGHT, inputs.RIGHT, inputs.RIGHT)
        self.assertEqual(self.s.win["movie"], 2)
        self.press(inputs.OK)
        self.assertEqual(self.app.opened[-1].id, "6")
        self.assertEqual(self.app.settings.recent_searches(), ["mo"])
        self.press(inputs.DOWN, inputs.UP, inputs.UP)
        self.assertEqual(self.s.zone, "keys")

    def test_focus_kept_when_a_late_server_lands(self):
        late = Prov("O", [Row("movie", "Movies", [mov(90, "O"), mov(91, "O")])])
        self.app.sources.provs.append(late)
        held = []
        real = Screen.run_bg
        self.home.queries = []
        Screen.run_bg = lambda scr, fn, done, fail=None: (
            held.append((fn, done)) if len(held) == 0 and self.home.queries else
            real(scr, fn, done, fail))
        self.type("mo")
        self.press(inputs.OK, inputs.RIGHT, inputs.RIGHT)
        focused = self.s.item
        self.assertEqual(focused.id, "3")
        fn, done = held.pop()
        r = fn()
        self.app.post(lambda: done(r))
        self.frames()
        self.assertEqual([i.id for i in self.s.rows[0].items][:4], ["1", "90", "2", "91"])
        self.assertIs(self.s.item, focused)        # same item, new index
        Screen.run_bg = real

    def test_no_results_and_error_states(self):
        self.home.rows = []
        self.type("zz")
        self.assertEqual(self.s.session.state, "done")
        self.s.draw(self.app.canvas)
        self.home.search = lambda q, limit=12: (_ for _ in ()).throw(OSError("down"))
        self.type("z")
        self.assertEqual(self.s.session.state, "error")
        self.s.draw(self.app.canvas)

    def test_late_reply_after_close_is_dropped(self):
        self.s.handle("char:m")
        self.s.handle("char:o")
        self.s.session.tick()
        self.app.stack.clear()
        self.frames()
        self.assertEqual(self.s.rows, [])

    def test_server_join_without_new_targets_keeps_results(self):
        self.type("mo")
        self.press(inputs.OK, inputs.RIGHT)
        rows = self.s.rows
        self.s.sources_changed()
        self.frames()
        self.assertIs(self.s.rows, rows)
        self.assertEqual((self.s.zone, self.s.item.id), ("results", "2"))
        self.app.sources.provs.append(Prov("O", []))     # a new target: search again, rows stay
        self.s.sources_changed()
        self.assertIs(self.s.rows, rows)
        self.frames()
        self.assertEqual((self.s.zone, self.s.item.id), ("results", "2"))


class TestRecent(Base):
    def test_recent_rows_and_clear(self):
        self.app.settings.add_recent_search("alien")
        self.app.settings.add_recent_search("brazil")
        self.press(inputs.DOWN, inputs.DOWN, inputs.DOWN)
        self.assertEqual(self.s.zone, "recent")
        self.press(inputs.DOWN)
        self.assertEqual([t for t, _ in self.s.menu_items()], ["Clear Recent Searches"])
        self.press(inputs.OK)
        self.assertEqual((self.s.q, self.s.zone), ("alien", "keys"))
        self.s._clear_recent()
        self.assertEqual(self.app.settings.recent_searches(), [])


class TestLayout(Base):
    def test_safe_area_and_delete_fits(self):
        for safe in ({"left": 40, "right": 40, "top": 36, "bottom": 36},
                     {"left": 72, "right": 72, "top": 56, "bottom": 56}):
            self.app.settings["safe"] = safe
            g = self.s.geo()
            self.assertGreaterEqual(g["keys_top"] - (g["T"] + 32), 12)
            last = self.s.key_rect(2, 12, g)
            self.assertLessEqual(last.right, 640 - safe["right"])
            self.assertLessEqual(g["poster_y"] + 168, 480 - safe["bottom"])
            self.assertLessEqual(draw.width("Delete", "list"), last.w)
            self.assertGreaterEqual(g["label_y"], last.bottom + 8)
            self.assertEqual(g["keys_top"], g["T"] + 44)


if __name__ == "__main__":
    unittest.main()
