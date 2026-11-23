"""Live TV as a pin, and Home "On Right Now": on_now, the once-per-profile seed, the hidden row,
the sentinel kept out of libraries and LB/RB, no server signed in."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import config  # noqa: E402
from batoplex import lineup as LU  # noqa: E402
from batoplex import schedule as S  # noqa: E402
from batoplex.app import App  # noqa: E402
from batoplex.model import Item, Row  # noqa: E402
from batoplex.sources import Sources  # noqa: E402


class Prov:
    def __init__(self, sid, titles, local=False):
        self.server_id, self.server_name, self.titles = sid, "Server " + sid, titles
        self.local_images = local

    def libraries(self, cached=False):
        return [Item(str(i), "library", t, section_id=i, server_id=self.server_id)
                for i, t in self.titles]


def tape(t0, n=3, dur=600, server="S"):
    e = [S.Entry("%s/%d" % (server, i), str(10 + i), server, dur, "%s/9" % server, "episode", 1,
                 i, "E%d" % i, "Show", "", None) for i in range(n)]
    return S.Tape([S.Airing(t0 + dur * i, dur, "main", S.Pool([x]).units[0])
                   for i, x in enumerate(e)])


def chan(cid, number, off=False):
    return types.SimpleNamespace(id=cid, number=number, name="CH%d" % number, off=off)


def make_lineup(chans, tapes, status):
    lu = LU.Lineup(lambda fn: None, zone=S.zone("America/New_York"))
    lu.rules = types.SimpleNamespace(channels=chans)
    lu.tapes, lu.status = tapes, status
    return lu


def settings():
    return config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))


class TestOnNow(unittest.TestCase):
    def test_order_offline_off_and_boundary(self):
        t0 = 1_790_000_000
        lu = make_lineup([chan("a", 2), chan("b", 5), chan("c", 7, off=True), chan("d", 9)],
                         {"a": tape(t0), "b": tape(t0), "c": tape(t0), "d": tape(t0)},
                         {"a": LU.ON, "b": LU.OFFLINE, "c": LU.ON, "d": LU.ON})
        now = t0 + 600                                    # a programme boundary
        rows = lu.on_now(now)
        self.assertEqual([r["cid"] for r in rows], ["a", "b", "d"])   # number order, no Off
        self.assertEqual([r["offline"] for r in rows], [False, True, False])
        r = rows[0]
        self.assertEqual((r["entry"].title, r["frac"], r["left"]), ("E1", 0.0, 600))
        self.assertEqual(LU.mins_left(r["left"]), "10 min left")
        r = lu.on_now(now + 599)[0]                       # the last second of E1
        self.assertEqual(r["entry"].title, "E1")
        self.assertAlmostEqual(r["frac"], 599 / 600)
        self.assertEqual(LU.mins_left(r["left"]), "1 min left")
        self.assertEqual(lu.on_now(t0 + 1800), [])        # past the tape end

    def test_empty_lineup_gives_no_row(self):
        from batoplex.screens.home import live_row
        lu = make_lineup([], {}, {})
        self.assertEqual(lu.on_now(1_790_000_000), [])
        self.assertEqual(live_row(lu, 1_790_000_000), (None, None))

    def test_row_items_and_due(self):
        from batoplex.screens.home import live_row
        t0 = 1_790_000_000
        lu = make_lineup([chan("a", 2), chan("b", 3)], {"a": tape(t0), "b": tape(t0 + 100)},
                         {"a": LU.ON, "b": LU.ON})
        row, due = live_row(lu, t0 + 150)
        self.assertEqual(row.key, "on_now")
        a = row.items[0]
        self.assertEqual((a.type, a.extra["number"], a.poster), ("channel", "02",
                                                                 "S|/library/metadata/9/thumb"))
        self.assertAlmostEqual(a.progress, 0.25)
        self.assertEqual(due, t0 + 600)                   # the first airing end


class TestHomeRow(unittest.TestCase):
    def home(self, lu, st, local=False):
        from batoplex.screens.home import HomeScreen
        h = HomeScreen.__new__(HomeScreen)
        h.root, h.library, h.loader, h.embedded = True, None, (lambda: []), False
        src = Sources([Prov("A", [(1, "Movies")], local=local)], home_id="A")
        h.app = types.SimpleNamespace(sources=src, lineup=lu, settings=st)
        return h

    def test_below_continue_and_setting_off(self):
        import time as _t
        now = int(_t.time())
        lu = make_lineup([chan("a", 2)], {"a": tape(now - 100, dur=3600)}, {"a": LU.ON})
        st = settings()
        h = self.home(lu, st)
        rows = [Row("continue", "Continue Watching", [Item("1", "movie", "M")]),
                Row("recent", "Recently Added", [Item("2", "movie", "N")])]
        self.assertEqual([r.key for r in h._with_live(rows)], ["continue", "on_now", "recent"])
        self.assertEqual([r.key for r in h._with_live(rows[1:])], ["on_now", "recent"])
        st.set_live_home(False)
        self.assertEqual([r.key for r in h._with_live(rows)], ["continue", "recent"])
        st.set_live_home(True)
        self.assertEqual([r.key for r in self.home(lu, st, local=True)._with_live(rows)],
                         ["continue", "recent"])           # no server signed in: no row


class TestSeed(unittest.TestCase):
    def app(self, st, visible=True, local=False):
        lu = types.SimpleNamespace(visible=lambda: ["a"] if visible else [])
        src = Sources([Prov("A", [(1, "Movies"), (2, "TV")], local=local)], home_id="A")
        return types.SimpleNamespace(settings=st, sources=src, lineup=lu, stack=[])

    def test_seed_once_per_profile_and_unpin_stays(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        st = config.Settings(path)
        app = self.app(st, visible=False)
        App.live_seed(app)
        self.assertFalse(st.live_pinned())                # no channel yet
        app = self.app(st)
        app.sources.pinned(st)                            # the library seed
        App.live_seed(app)
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 2), ("livetv", 0)])   # last
        self.assertFalse(st.seeded("other"))
        st.set_pins([e for e in st.pin_entries() if e["server"] != "livetv"])  # unpin
        st.save()
        st = config.Settings(path)                        # a restart
        App.live_seed(self.app(st))                       # a new channel shows
        self.assertFalse(st.live_pinned())
        st.profile = "other"                              # another profile: its own seed
        App.live_seed(self.app(st))
        self.assertTrue(st.live_pinned())

    def test_live_seed_before_library_seed_keeps_both(self):
        st = settings()
        app = self.app(st)
        App.live_seed(app)                                # Live TV first: no library seed yet
        self.assertFalse(st.seeded())
        libs = app.sources.pinned(st)
        self.assertEqual([l.title for l in libs], ["Movies", "TV"])
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 2), ("livetv", 0)])

    def test_live_edit_before_library_seed_keeps_the_seed(self):
        st = settings()
        App.live_seed(self.app(st))                       # sections not loaded yet
        st.set_pins([])                                   # the user unpins Live TV
        self.assertFalse(st.seeded())
        libs = self.app(st).sources.pinned(st)            # the library seed still runs
        self.assertEqual([l.title for l in libs], ["Movies", "TV"])
        self.assertFalse(st.live_pinned())

    def test_user_pin_then_unpin_before_seed_stays_unpinned(self):
        st = settings()
        st.set_pins([dict(config.LIVE_ENTRY)])            # More: X before any channel shows
        st.set_pins([])                                   # X again
        App.live_seed(self.app(st))                       # the first channel shows
        self.assertFalse(st.live_pinned())

    def test_no_server_no_pin(self):
        st = settings()
        app = self.app(st, local=True)
        App.live_seed(app)
        self.assertFalse(st.live_pinned())


class TestSentinel(unittest.TestCase):
    def test_never_a_library_and_never_in_source_keys(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, dict(config.LIVE_ENTRY),
                     {"server": "A", "section": 2}])
        src = Sources([Prov("A", [(1, "Movies"), (2, "TV")])], home_id="A")
        libs = src.pinned(st)
        self.assertEqual([(l.title, bool(l.extra.get("down"))) for l in libs],
                         [("Movies", False), ("TV", False)])
        app = types.SimpleNamespace(sources=src, settings=st)
        self.assertEqual(App._source_keys(app), ["home", ("A", 1), ("A", 2)])
        self.assertEqual(st.live_index(), 1)


class TestPanel(unittest.TestCase):
    def panel(self, st, visible, local=False):
        from batoplex.screens.panel import PanelScreen
        p = PanelScreen.__new__(PanelScreen)
        lu = types.SimpleNamespace(visible=lambda: ["a"] if visible else [])
        src = Sources([Prov("A", [(1, "Movies"), (2, "TV")], local=local)], home_id="A")
        p.app = types.SimpleNamespace(settings=st, sources=src, lineup=lu, music=None, vqueue=[])
        return p

    def test_row_hides_with_no_channel_and_returns_in_place(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, dict(config.LIVE_ENTRY),
                     {"server": "A", "section": 2}])
        keys = [r[0] for r in self.panel(st, visible=False)._rows()]
        self.assertNotIn("livetv", keys)
        self.assertTrue(st.live_pinned())                 # the entry stays
        keys = [r[0] for r in self.panel(st, visible=True)._rows()]
        self.assertEqual(keys[2:5], [("A", 1), "livetv", ("A", 2)])
        rows = self.panel(st, visible=True)._rows()
        self.assertEqual([r[2] for r in rows if isinstance(r[0], tuple)], ["", ""])  # no tags

    def test_hidden_live_pin_keeps_its_place_on_a_move(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, {"server": "A", "section": 2},
                     dict(config.LIVE_ENTRY)])
        p = self.panel(st, visible=False)
        menus = []
        p.app.open_menu = lambda rows, **k: menus.append(rows)
        p.app.saved = lambda ok: None
        p.app.pins_changed = lambda: None
        p.refresh = lambda: None
        p.rows = p._rows()
        p.focus = next(i for i, r in enumerate(p.rows) if r[0] == ("A", 2))
        self.assertTrue(p._pin_menu())
        labels = [r[0] for r in menus[0]]
        self.assertNotIn("Move Down", labels)             # TV is the last visible pin
        dict(menus[0])["Move Up"]()
        self.assertEqual(st.pin_list(), [("A", 2), ("A", 1), ("livetv", 0)])

    def test_no_server_no_row(self):
        st = settings()
        st.set_pins([dict(config.LIVE_ENTRY)])
        keys = [r[0] for r in self.panel(st, visible=True, local=True)._rows()]
        self.assertNotIn("livetv", keys)


class TestMoreLive(unittest.TestCase):
    def test_rows_pin_and_order(self):
        from batoplex.screens.more import MoreScreen
        sys.path.insert(0, os.path.dirname(__file__))
        from test_more import App as MoreApp, Prov as MoreProv
        app = MoreApp([MoreProv("A", "Home Server", True, [(6, "Movies")])])
        app.lineup = types.SimpleNamespace(count=lambda: 3)
        app.settings.set_pins([{"server": "A", "section": 6}])
        from batoplex import inputs
        guides = []
        app.open_guide = lambda focus=None, panel=None: guides.append(panel)
        m = MoreScreen(app)
        self.assertEqual([(r[0], r[2], r[3]) for r in m.rows()[2:5]], [
            ("gap", "", ""), ("livepin", "Channels", ""), ("live", "Manage Channels", "3")])
        m.handle(inputs.DOWN)                       # from Movies to Channels
        self.assertEqual([t for t, _f in m.menu_items()], ["Pin"])
        m.handle(inputs.X)
        self.assertEqual(app.settings.pin_list(), [("A", 6), ("livetv", 0)])
        self.assertEqual(m.rows()[3][3], "Pinned")
        self.assertEqual([t for t, _f in m.menu_items()], ["Unpin", "Move Up"])
        dict(m.menu_items())["Move Up"]()
        self.assertEqual(app.settings.pin_list(), [("livetv", 0), ("A", 6)])
        m.handle(inputs.OK)
        self.assertEqual(guides, [None])            # from More: B comes back to More


if __name__ == "__main__":
    unittest.main()
