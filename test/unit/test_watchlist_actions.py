"""Watchlist button and menu rows: when the button shows, the exact menu
rows, the episode list's show load, and the stale marks after a play."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config  # noqa: E402
from batoplex import watchlist as W  # noqa: E402
from batoplex.app import App  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen, detail  # noqa: E402
from batoplex.screens.episodes import EpisodeScreen  # noqa: E402
from batoplex.sources import Sources  # noqa: E402

pygame.init()


def sync(self, fn, done, fail=None):
    try:
        r = fn()
    except Exception as e:
        if fail:
            fail(e)
        return
    done(r)


class Images:
    version = 0

    def get(self, *a, **k):
        return None

    def prefetch(self, *a, **k):
        pass


class Prov:
    """A Plex-like provider: playlists (so the queue rows show) and details."""

    def __init__(self, guid="plex://movie/abc", typ="movie"):
        self.server_id, self.guid, self.typ = "A", guid, typ

    def playlists(self, kind):
        return []

    def by_guid(self, guids):
        return {}

    def details(self, item):
        full = Item(item.id, self.typ if item.type != "episode" else "episode", item.title,
                    server_id="A")
        if self.guid and full.type in ("movie", "show"):
            full.extra["guid"] = self.guid
        return full

    def seasons(self, show_id):
        return [Item("s1", "season", "Season 1", index=1, server_id="A")]

    def episodes(self, sid):
        return [Item("e1", "episode", "Pilot", index=1, server_id="A", show_id="S")]


def make_app(prov, titles=(), complete=True, token="tok", support=W.UNKNOWN):
    app = types.SimpleNamespace(settings=config.Settings(os.path.join(tempfile.mkdtemp(), "s.json")),
                                images=Images(), src_gen=0, music=None, stack=[], vqueue=[],
                                canvas=pygame.Surface((641, 480)), posted=[], toasts=[])
    app.sources = Sources([prov], home_id="A")
    app.provider_for = lambda it: prov
    app.post = app.posted.append
    app.toast = lambda m, private=False: app.toasts.append(m)
    app.saved = lambda ok: ok
    wl = W.Watchlist(app, root=tempfile.mkdtemp())
    wl.reset(None, token, "cid")
    wl.titles = [{"key": k, "guid": "plex://movie/" + k, "type": "movie"} for k in titles]
    wl.complete, wl.support = complete, support
    app.watchlist = wl
    app.watch_visible = lambda: App.watch_visible(app)
    return app


class Base(unittest.TestCase):
    def setUp(self):
        self.real = Screen.run_bg
        Screen.run_bg = sync

    def tearDown(self):
        Screen.run_bg = self.real


class TestButton(Base):
    def keys(self, app, item):
        return [k for k, _i, _l in detail.DetailScreen(app, item).buttons() if k != "more"]

    def test_when_the_button_shows(self):
        movie = Item("9", "movie", "M")
        self.assertEqual(self.keys(make_app(Prov()), movie), ["play", "watchlist", "watch"])
        self.assertEqual(self.keys(make_app(Prov("com.plexapp.agents.imdb://tt1")), movie),
                         ["play", "watch"])                        # a legacy agent: no key
        self.assertEqual(self.keys(make_app(Prov()), Item("e", "episode", "E")),
                         ["play", "watch"])
        self.assertEqual(self.keys(make_app(Prov(), token=None), movie), ["play", "watch"])
        self.assertEqual(self.keys(make_app(Prov(), support=W.NO), movie), ["play", "watch"])
        self.assertEqual(self.keys(make_app(Prov(), complete=False), movie), ["play", "watch"])

    def test_label_icon_and_press(self):
        app = make_app(Prov(), titles=["abc"])
        scr = detail.DetailScreen(app, Item("9", "movie", "M"))
        b = dict((k, (i, l)) for k, i, l in scr.buttons())
        self.assertEqual(b["watchlist"], ("bookmark_on", "Remove from Watchlist"))
        app.watchlist._send = lambda op: None
        scr.focus, scr.fkey = 1, "watchlist"
        from batoplex import inputs
        scr.handle(inputs.OK)
        b = dict((k, (i, l)) for k, i, l in scr.buttons())
        self.assertEqual(b["watchlist"], ("bookmark", "Add to Watchlist"))
        s = pygame.Surface((641, 480))
        scr.draw(s)                                                # five buttons draw


class TestMenus(Base):
    def test_exact_rows_of_each_menu(self):
        app = make_app(Prov())
        scr = detail.DetailScreen(app, Item("9", "movie", "M"))
        self.assertEqual([r[0] for r in scr.menu_items()],
                         ["Mark as Played", "Add to Watchlist", "Play Next", "Add to Queue",
                          "Add to Playlist"])
        self.assertEqual([r[0] for r in scr.more_items()],
                         ["Play Next", "Add to Queue", "Add to Playlist"])   # the button has it
        from batoplex.screens.watchlist import WatchlistScreen
        app.watchlist.found[("A", "plex://movie/abc")] = {"key": "9", "section": 1, "stamp": 1,
                                                         "tile": {"type": "movie", "title": "M"}}
        app.watchlist.titles = [{"key": "abc", "guid": "plex://movie/abc", "type": "movie"}]
        app.sources.home.search = lambda q: []
        app.watchlist.load = lambda: None                          # no network in a unit test
        grid = WatchlistScreen(app, root=True)
        self.assertEqual([r[0] for r in grid.menu_items()],
                         ["Mark as Played", "Remove from Watchlist", "Play Next", "Add to Queue",
                          "Add to Playlist", "Sort", "Filter"])

    def test_episode_list_loads_the_show_from_an_episode_entry(self):
        for entry in ("continue watching", "episode search"):
            app = make_app(Prov("plex://show/s1", "show"), titles=[])
            ep = Item("e1", "episode", "Pilot", show_id="S", show_title="Show", server_id="A")
            scr = EpisodeScreen(app, "S", "Show", focus_episode=ep)  # no show object
            rows = [r[0] for r in scr.menu_items()]
            self.assertEqual(rows.count("Add to Watchlist"), 1, entry)
            self.assertLess(rows.index("Show Details"), rows.index("Add to Watchlist"))
        app = make_app(Prov(None, "show"))                         # no guid: no row
        scr = EpisodeScreen(app, "S", "Show")
        self.assertNotIn("Add to Watchlist", [r[0] for r in scr.menu_items()])


class TestStale(unittest.TestCase):
    def app(self):
        a = make_app(Prov(), titles=["abc"])
        a.stack = [types.SimpleNamespace(is_watchlist=True)]
        a.loads = []
        a.watchlist.load = lambda: a.loads.append(1)
        return a

    def test_stale_only_after_a_good_mark(self):
        a = self.app()
        it = Item("9", "movie", "M", server_id="A")
        it.extra["guid"] = "plex://movie/abc"
        prov = types.SimpleNamespace(scrobble=mock.Mock(side_effect=Exception("HTTP 500")))
        a.provider_for = lambda i: prov
        a._refresh_homes = lambda *x, **k: None
        a.watch_played = lambda guid=None: App.watch_played(a, guid)
        with mock.patch("threading.Thread") as T:
            App.mark_watched(a, it, True)
            target = T.call_args.kwargs["target"]
        with self.assertRaises(Exception):
            target()
        for fn in a.posted:
            fn()
        self.assertFalse(a.watchlist.stale)
        a.posted.clear()
        prov.scrobble = mock.Mock()
        with mock.patch("threading.Thread") as T:
            App.mark_watched(a, it, True)
            T.call_args.kwargs["target"]()
        for fn in a.posted:
            fn()
        self.assertTrue(a.watchlist.stale)
        self.assertEqual(a.loads, [1])                              # the open screen loads again

    def test_one_more_load_30_s_later(self):
        a = make_app(Prov(), titles=["abc"])
        wl = a.watchlist
        now = [100.0]
        wl.clock, wl.need_load = (lambda: now[0]), False
        wl.mark_played("plex://movie/abc")
        wl._loaded(wl._ticket(), (True, [{"key": "abc", "guid": "plex://movie/abc",
                                          "type": "movie", "added_at": 1}]))
        self.assertEqual(wl.again, 130.0)                           # still on the list
        loads = []
        wl.load = lambda: loads.append(1)
        now[0] = 129.0
        wl.tick(True)
        self.assertEqual(loads, [])
        now[0] = 130.0
        wl.tick(True)
        self.assertEqual(loads, [1])
        wl.mark_played("plex://movie/abc")
        wl._loaded(wl._ticket(), (True, []))                        # Plex removed it
        self.assertIsNone(wl.again)


if __name__ == "__main__":
    unittest.main()
