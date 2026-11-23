"""Watchlist source and screen: watch_sort, the filter, the focus after a
remove, the empty text, the pin (seed, unpin, move, hidden place, More), root keys and the rows
that hide with no Watchlist."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, inputs  # noqa: E402
from batoplex import watchlist as W  # noqa: E402
from batoplex.app import App  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import watchlist as WS  # noqa: E402
from batoplex.sources import Sources  # noqa: E402

pygame.init()


def it(key, typ="movie", title=None, added=0, date=None, year=None):
    i = Item(key, typ, title or key, year=year, server_id="A")
    i.extra.update(added_at=added, guid="plex://%s/%s" % (typ, key))
    if date:
        i.extra["date"] = date
    return i


class Images:
    def prefetch(self, *a):
        pass

    def get(self, *a, **k):
        return None


class Prov:
    def __init__(self, sid, libs=((1, "Movies"), (2, "TV")), local=False):
        self.server_id, self.server_name, self.local_images = sid, "Server " + sid, local
        self.libs = libs

    def libraries(self, cached=False):
        return [Item(str(i), "library", t, section_id=i, server_id=self.server_id)
                for i, t in self.libs]


class FakeWl:
    def __init__(self, items, visible=True):
        self.items, self.version, self._visible = list(items), 1, visible
        self.loading = self.error = self.complete = False
        self.titles, self.found, self.resolving, self.loads = [1], {1: 1}, 0, 0
        self.loaded_at = 0.0

    def view_titles(self):
        return list(self.titles)

    def shown(self):
        return list(self.items)

    def visible(self):
        return self._visible

    def first_load(self):
        return False

    def opened(self):
        self.loads += 1

    def load(self):
        self.loads += 1


def app_with(items=(), visible=True, local=False):
    st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
    src = Sources([Prov("A", local=local)], home_id="A")
    app = types.SimpleNamespace(settings=st, sources=src, images=Images(), watchlist=FakeWl(
        items, visible), menus=[], toasts=[], panels=[], sources_next=[], searches=0,
        popped=0, opened=[], music=None, vqueue=[], lineup=None, stack=[])
    app.open_menu = lambda rows, title=None, settings=True: app.menus.append((title, rows))
    app.toast = lambda m, private=False: app.toasts.append(m)
    app.open_panel = lambda focus=None: app.panels.append(focus)
    app.next_source = lambda d: app.sources_next.append(d)
    app.saved = lambda ok: ok
    app.pop = lambda *a: setattr(app, "popped", app.popped + 1)
    app.open_item = app.opened.append
    app.watch_visible = lambda: App.watch_visible(app)

    def search():
        app.searches += 1
    app.open_search = search
    return app


class TestSortAndFilter(unittest.TestCase):
    def test_added_and_release_orders_differ_and_missing_dates(self):
        items = [it("a", title="Alpha", added=3, date="1990-01-01"),
                 it("b", title="Bravo", added=1, date="2010-05-05"),
                 it("c", title="Charlie", added=2),                    # no date, no year
                 it("d", title="The Delta", added=4, year=2010)]
        key = lambda xs: [x.id for x in xs]
        self.assertEqual(key(WS.watch_sort(items, "added")), ["d", "a", "c", "b"])
        self.assertEqual(key(WS.watch_sort(items, "release")), ["b", "d", "a", "c"])
        self.assertEqual(key(WS.watch_sort(items, "release", desc=False)), ["a", "d", "b", "c"])
        self.assertEqual(key(WS.watch_sort(items, "title")), ["a", "b", "c", "d"])

    def test_each_filter(self):
        items = [it("m1"), it("s1", "show"), it("m2")]
        self.assertEqual([x.id for x in WS.watch_filter(items, "all")], ["m1", "s1", "m2"])
        self.assertEqual([x.id for x in WS.watch_filter(items, "movie")], ["m1", "m2"])
        self.assertEqual([x.id for x in WS.watch_filter(items, "show")], ["s1"])


class TestScreen(unittest.TestCase):
    def test_remove_keeps_the_slot_and_empty_text(self):
        items = [it("k%d" % i, added=10 - i) for i in range(7)]
        app = app_with(items)
        scr = WS.WatchlistScreen(app, root=True)
        g = scr.grid
        self.assertEqual(g.total, 7)
        g.focus = 6
        g.handle(inputs.LEFT)                       # slot 5
        del app.watchlist.items[5]                  # removed: the tile goes at once
        app.watchlist.version += 1
        scr.update()
        self.assertEqual((g.total, g.focus, g.item().id), (6, 5, "k6"))
        app.watchlist.items = []
        app.watchlist.version += 1
        scr.update()
        self.assertEqual(g.total, 0)
        self.assertEqual(g.empty, "No titles")
        s = pygame.Surface((641, 480))
        scr.draw(s)                                 # draws with no item

    def test_sort_and_filter_are_kept_for_the_profile(self):
        app = app_with([it("m1", added=1), it("s1", "show", added=2)])
        scr = WS.WatchlistScreen(app, root=True)
        scr.grid.open_filter_menu()
        dict(app.menus[-1][1])["TV Shows"]()
        self.assertEqual([x.id for x in scr.items], ["s1"])
        scr.grid.open_sort_menu()
        dict((k.split("  ")[0], f) for k, f in app.menus[-1][1])["Title"]()
        self.assertEqual(app.settings.watch_view(),
                         {"sort": "title", "desc": False, "filter": "show"})
        again = WS.WatchlistScreen(app, root=True)
        self.assertEqual((again.grid.sort["key"], again.grid.filter_label), ("title", "TV Shows"))

    def test_root_keys(self):
        app = app_with([it("a"), it("b")])
        scr = WS.WatchlistScreen(app, root=True)
        scr.handle(inputs.BACK)
        scr.handle(inputs.LEFT)                     # the first column
        self.assertEqual(app.panels, [None, None])
        scr.handle(inputs.LB)
        scr.handle(inputs.RB)
        self.assertEqual(app.sources_next, [-1, 1])
        menus = []
        app.open_menu = lambda rows=None, title=None, settings=True: menus.append(title)
        scr.handle(inputs.Y)                        # filter, not Search
        self.assertEqual((app.searches, menus), (0, ["FILTER"]))
        pushed = WS.WatchlistScreen(app)            # from More: not a root
        pushed.handle(inputs.BACK)
        pushed.handle(inputs.LB)
        self.assertEqual((app.popped, app.sources_next), (1, [-1, 1]))

    def test_a_version_change_updates_an_open_screen(self):
        app = app_with([it("a")])
        scr = WS.WatchlistScreen(app, root=True)
        app.watchlist.items.append(it("b"))
        app.watchlist.version += 1                  # pins_changed, a lookup, a server
        scr.update()
        self.assertEqual(scr.grid.total, 2)


class TestPin(unittest.TestCase):
    def test_seed_once_unpin_stays_and_source_keys(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        st = config.Settings(path)
        st.set_pins([{"server": "A", "section": 1}, {"server": "A", "section": 2}])
        self.assertTrue(st.seed_watch())
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 2), ("watchlist", 0)])
        self.assertFalse(st.seed_watch())
        st.set_pins([e for e in st.pin_entries() if e["server"] != "watchlist"])
        st.save()
        st = config.Settings(path)
        self.assertFalse(st.seed_watch())           # an unpin stays
        st.profile = "other"
        self.assertTrue(st.seed_watch())            # each profile its own seed

    def test_watch_seed_before_library_seed_keeps_both(self):
        app = app_with()
        st = app.settings
        st.seed_watch()
        self.assertFalse(st.seeded())
        libs = app.sources.pinned(st)
        self.assertEqual([l.title for l in libs], ["Movies", "TV"])
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 2), ("watchlist", 0)])
        self.assertEqual(App._source_keys(app), ["home", ("A", 1), ("A", 2), "watchlist"])
        st.set_pins([{"server": "A", "section": 1}, dict(config.WATCH_ENTRY),
                     dict(config.LIVE_ENTRY), {"server": "A", "section": 2}])
        self.assertEqual(App._source_keys(app), ["home", ("A", 1), "watchlist", ("A", 2)])
        self.assertEqual((st.watch_index(), st.live_index()), (1, 1))

    def panel(self, app):
        from batoplex.screens.panel import PanelScreen
        p = PanelScreen.__new__(PanelScreen)
        p.app = app
        return p

    def test_panel_row_order_and_hidden_rows(self):
        app = app_with()
        st = app.settings
        st.set_pins([{"server": "A", "section": 1}, dict(config.WATCH_ENTRY),
                     {"server": "A", "section": 2}])
        keys = [r[0] for r in self.panel(app)._rows()]
        self.assertEqual(keys[2:5], [("A", 1), "watchlist", ("A", 2)])
        app.watchlist._visible = False              # no token, or support "no"
        self.assertNotIn("watchlist", [r[0] for r in self.panel(app)._rows()])
        self.assertNotIn("watchlist", App._source_keys(app))
        self.assertTrue(st.watch_pinned())          # the entry stays
        local = app_with(local=True)
        local.settings.set_pins([dict(config.WATCH_ENTRY)])
        self.assertNotIn("watchlist", [r[0] for r in self.panel(local)._rows()])

    def test_hidden_watch_pin_keeps_its_place_on_a_move(self):
        app = app_with(visible=False)
        st = app.settings
        st.set_pins([{"server": "A", "section": 1}, {"server": "A", "section": 2},
                     dict(config.WATCH_ENTRY)])
        p = self.panel(app)
        p.app.pins_changed = lambda: None
        p.refresh = lambda: None
        p.rows = p._rows()
        p.focus = next(i for i, r in enumerate(p.rows) if r[0] == ("A", 2))
        self.assertTrue(p._pin_menu())
        rows = dict(app.menus[-1][1])
        self.assertNotIn("Move Down", rows)
        rows["Move Up"]()
        self.assertEqual(st.pin_list(), [("A", 2), ("A", 1), ("watchlist", 0)])

    def test_panel_unpin_and_more_pin_and_open(self):
        from batoplex.screens.more import MoreScreen
        from test_more import App as MoreApp, Prov as MoreProv
        app = MoreApp([MoreProv("A", "Home Server", True, [(6, "Movies")])])
        app.watchlist = FakeWl([])
        app.watch_visible = lambda: True
        app.settings.set_pins([{"server": "A", "section": 6}, dict(config.WATCH_ENTRY)])
        m = MoreScreen(app)
        self.assertEqual([(r[0], r[2], r[3]) for r in m.rows()[2:4]],
                         [("gap", "", ""), ("watchpin", "Watchlist", "Pinned")])
        m.handle(inputs.DOWN)                        # from Movies to Watchlist
        self.assertEqual([t for t, _f in m.menu_items()], ["Unpin", "Move Up"])
        m.handle(inputs.X)
        self.assertEqual(app.settings.pin_list(), [("A", 6)])
        self.assertEqual(m.rows()[3][3], "")
        app.images = Images()
        m.handle(inputs.OK)                          # opens it, not as a root
        scr = app.pushed[-1]
        self.assertIsInstance(scr, WS.WatchlistScreen)
        self.assertFalse(scr.root)
        m.handle(inputs.X)
        self.assertEqual(app.settings.pin_list(), [("A", 6), ("watchlist", 0)])
        app.watch_visible = lambda: False
        self.assertNotIn("Watchlist", [r[2] for r in m.rows()])


class TestVisible(unittest.TestCase):
    def test_visible_needs_a_token_and_no_refusal(self):
        wl = W.Watchlist(types.SimpleNamespace(src_gen=0), root=tempfile.mkdtemp())
        self.assertFalse(wl.visible())
        wl.reset(None, "tok", "cid")
        self.assertTrue(wl.visible())
        wl.support = W.NO
        self.assertFalse(wl.visible())


if __name__ == "__main__":
    unittest.main()


class TestEmptyLine(unittest.TestCase):
    """The how-to line only for a Watchlist with no title."""

    def test_line_only_when_the_list_has_no_title(self):
        app = app_with([])
        scr = WS.WatchlistScreen(app, root=True)
        self.assertIsNone(scr._empty_line())          # a saved title a filter hides
        app.watchlist.titles = []
        self.assertEqual(scr._empty_line(), "Use Add to Watchlist on a movie or show")
        app.watchlist.loaded_at = None                # not loaded yet
        self.assertIsNone(scr._empty_line())
