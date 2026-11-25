import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import anim, inputs  # noqa: E402
from batoplex.screens import views  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens.views import GridView  # noqa: E402

TITLES = ["Alpha", "Apex", "Bravo", "Brine", "Bunt", "Charlie", "Delta", "Dune", "Echo"]


class Images:
    def prefetch(self, *a):
        pass

    def get(self, *a, **k):
        return None


class App:
    def __init__(self):
        self.images, self.toasts, self.menus = Images(), [], []
        self.top = None

    def toast(self, m):
        self.toasts.append(m)

    def open_menu(self, rows, title=None, settings=True):
        self.menus.append((title, rows))


class Scr:
    safe = {"left": 40, "right": 40, "top": 36, "bottom": 36}

    def __init__(self):
        self.app = App()
        self.app.top = self
        self.pending = []        # worker calls held until run() so tests can reorder them
        self.hold = False
        self.pscrim = {}

    def run_bg(self, fn, done, fail=None):
        if self.hold:
            self.pending.append((fn, done, fail))
            return
        try:
            r = fn()
        except Exception as e:
            if fail:
                fail(e)
            return
        done(r)

    def unplayed_filter(self):
        return {"unwatched": 1}

    def open_grid_item(self, it):
        self.opened = it

    def retry(self):
        self.retried = True


def items(titles):
    return [Item(str(i), "movie", t) for i, t in enumerate(titles)]


def make(titles=TITLES):
    data = items(titles)

    def fetch(sort, f, start, size):
        rows = sorted(data, key=lambda i: i.title, reverse=sort.endswith(":desc"))
        if f.get("only"):
            rows = [i for i in rows if i.title[0] in f["only"]]
        return rows[start:start + size], len(rows)

    def letters(f):
        out = []
        for i in sorted(data, key=lambda i: i.title):
            if f.get("only") and i.title[0] not in f["only"]:
                continue
            c = i.title[0]
            if out and out[-1][0] == c:
                out[-1] = (c, out[-1][1] + 1)
            else:
                out.append((c, 1))
        return out
    scr = Scr()
    return scr, GridView(scr, fetch, 112, letters=letters)


class TestGrid(unittest.TestCase):
    def test_sort_change_while_on_the_rail(self):
        scr, g = make()
        g.zone = "rail"
        scr.hold = True
        g.sort = dict(g.sort, dir="desc")
        g.query()                                   # letters not back yet
        self.assertNotEqual(g.zone, "rail")
        for a in (inputs.RIGHT, inputs.LEFT, inputs.OK, inputs.UP):
            g.handle(a)
        g.update()

    def test_fewer_letters_after_filter(self):
        scr, g = make()
        g.zone = "rail"
        for _ in range(4):
            g.handle(inputs.DOWN)                    # letter E
        g.filters = {"only": "AB"}
        g.query()
        g.zone = "rail"
        g.handle(inputs.OK)                         # must not index past the new letters
        self.assertEqual(g.zone, "grid")

    def test_rail_from_row_end_and_back_keeps_focus(self):
        scr, g = make()
        for _ in range(4):
            g.handle(inputs.RIGHT)                   # Bunt, index 4, the last column
        self.assertEqual(g.zone, "grid")
        g.handle(inputs.RIGHT)
        self.assertEqual(g.zone, "rail")
        self.assertEqual(g._offsets()[g.letter_i][0], "B")
        g.handle(inputs.LEFT)
        self.assertEqual((g.zone, g.focus), ("grid", 4))

    def test_rail_letter_settles_then_jumps(self):
        scr, g = make()
        g.handle(inputs.RIGHT)
        g.zone, g.letter_i = "rail", 0
        g.handle(inputs.DOWN)                        # B
        self.assertEqual(g.focus, 1)                 # the grid waits for the settle
        for _ in range(12):
            g.update()
        self.assertEqual(g.item().title, "Bravo")

    def test_scroll_lifts_chrome_then_follows_rows(self):
        scr, g = make(TITLES * 3)                    # 27 items, 6 rows
        geo = g.geo()
        t = [0.0]

        def field():
            t[0] += 1 / 59.94
            anim.tick(t[0])
            g.update()
        anim.tick(t[0])
        g.handle(inputs.DOWN)
        g.update()
        self.assertTrue(0 < g.scroll < geo["lift"])  # the press field already moves
        seen = [g.scroll]
        for _ in range(8):
            field()
            seen.append(g.scroll)
        self.assertEqual(g.scroll, geo["lift"])
        steps = [b - a for a, b in zip(seen, seen[1:]) if b != a]
        self.assertTrue(all(s % 4 == 0 and s >= 8 for s in steps), steps)
        g.handle(inputs.DOWN)
        field()
        mid = g.scroll
        g.handle(inputs.DOWN)                        # a new press: from the place shown
        g.update()
        self.assertGreater(g.scroll, mid)
        for _ in range(8):
            field()
        self.assertEqual(g.scroll, geo["lift"] + 2 * g.L["pitch"])
        g.handle(inputs.RT)                          # a letter jump: a cut
        g.update()
        self.assertEqual(g.scroll, g._target())

    def test_scroll_steps_on_4_px_at_every_margin(self):
        """Pitch and lift are multiples of 4 at every calibration step."""
        for music in (False, True):
            for side in range(16, 73, 4):
                for top in range(16, 73, 4):
                    scr, g = make()
                    g.L = views.MUSIC if music else views.VIDEO
                    scr.safe = dict(scr.safe, left=side, right=side, top=top)
                    geo = g.geo()
                    self.assertEqual(geo["pitch"] % 4, 0, (music, side, top))
                    self.assertEqual(geo["lift"] % 4, 0, (music, side, top))

    def test_tab_line_and_chips_bring_the_chrome_back(self):
        # A scrolled grid hid the focus on the tabs and the chips.
        scr, g = make(TITLES * 3)
        for _ in range(3):
            g.handle(inputs.DOWN)
        g.update()
        self.assertGreater(g.scroll, 0)
        scr.in_tabs = True
        g.update()
        self.assertEqual(g.scroll, 0)
        g.zone = "rail"                              # tabs reached from the rail (RB, Up)
        g.update()
        self.assertEqual(g.scroll, 0)
        scr.in_tabs = False
        g.zone = "chips"
        g.update()
        self.assertEqual(g.scroll, 0)
        g.handle(inputs.DOWN)                        # back to the focused poster
        g.update()
        self.assertEqual(g.scroll, g.geo()["lift"] + 2 * g.geo()["pitch"])

    def test_wide_margin_keeps_the_rail_clear(self):
        scr, g = make()
        scr.safe = dict(scr.safe, left=72, right=72)
        geo = g.geo()
        self.assertLessEqual(geo["x0"] + geo["grid_w"] + 12, geo["x1"] - 20)

    def test_letter_jump_and_late_page(self):
        scr, g = make()
        g.handle(inputs.RT)
        self.assertEqual(g.item().title, "Bravo")
        g.handle(inputs.RT)
        g.handle(inputs.LT)
        self.assertEqual(g.item().title, "Bravo")
        scr.hold = True
        g.query()
        old = scr.pending[:]
        g.query()                                   # a newer generation
        for fn, done, fail in old:
            done(fn())                              # late results of the old query
        self.assertEqual(g.pages, {})

    def test_error_retry_only_in_grid(self):
        scr, g = make()
        g.error = "boom"
        g.pages = {}
        g.zone = "chips"
        g.handle(inputs.OK)
        self.assertFalse(getattr(scr, "retried", False))
        self.assertTrue(scr.app.menus)              # the Sort menu opened
        g.zone = "grid"
        g.handle(inputs.OK)
        self.assertTrue(scr.retried)


if __name__ == "__main__":
    unittest.main()
