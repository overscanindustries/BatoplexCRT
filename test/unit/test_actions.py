"""Item rows, the local video queue, Add to Playlist, names, music checks."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex.model import Item  # noqa: E402
from batoplex.screens import actions, name  # noqa: E402


class Prov:
    def __init__(self, sid, playlists=True):
        self.server_id = sid
        if playlists:
            self.playlists = lambda kind, section_id=None: []


class Src:
    local_images = False
    home_id = "A"

    def __init__(self, provs):
        self.provs = {p.server_id: p for p in provs}
        self.lists, self.calls = {}, []

    def strict(self, sid):
        return self.provs.get(sid)

    def server_name(self, sid):
        return {"A": "Home Server", "B": "Shared"}.get(sid, "")

    def server_playlists(self, kind, sid):
        self.calls.append(("list", kind, sid))
        return self.lists.get((kind, sid), [])


class Music:
    def __init__(self):
        self.state, self.live, self.servers, self.session_id = "idle", False, set(), "s1"
        self.added = []

    @property
    def active(self):
        return self.state != "idle"

    def add(self, items):
        self.added.append(("add", [i.id for i in items]))

    def play_next(self, items):
        self.added.append(("next", [i.id for i in items]))


class App:
    def __init__(self, src):
        self.sources, self.vqueue, self.toasts, self.menus, self.pushed = src, [], [], [], []
        self.music = Music()
        self.src_gen = 1
        self.changed = 0

    def toast(self, m, private=False):
        self.toasts.append(m)

    def queue_changed(self):
        self.changed += 1

    def open_menu(self, rows, title=None, settings=True):
        self.menus.append((title, [r[0] for r in rows], rows))

    def push(self, s):
        self.pushed.append(s)

    def auth_lost(self):
        self.toasts.append("auth")


class Scr:
    def __init__(self, app):
        self.app = app

    def run_bg(self, fn, done, fail=None):
        try:
            r = fn()
        except Exception as e:
            fail(e)
            return
        done(r)


def mk(t, i="1", sid="A", **kw):
    return Item(i, t, "%s %s" % (t, i), server_id=sid, **kw)


class TestItemRows(unittest.TestCase):
    def setUp(self):
        self.app = App(Src([Prov("A"), Prov("B")]))
        self.scr = Scr(self.app)

    def labels(self, it):
        return [r[0] for r in actions.item_rows(self.scr, it)]

    def test_rows_per_type(self):
        three = ["Play Next", "Add to Queue", "Add to Playlist"]
        self.assertEqual(self.labels(mk("movie")), three)
        self.assertEqual(self.labels(mk("episode")), three)
        for t in ("show", "season", "album", "artist", "track"):
            self.assertEqual(self.labels(mk(t)), ["Add to Playlist"], t)
        self.assertEqual(self.labels(mk("collection")), [])

    def test_no_playlists_call_no_rows(self):
        app = App(Src([Prov("D", playlists=False)]))
        self.assertEqual(actions.item_rows(Scr(app), mk("movie", sid="D")), [])
        app.sources.local_images = True                     # no server signed in
        self.assertEqual(actions.item_rows(Scr(app), mk("movie", sid="D")), [])

    def test_video_queue(self):
        a, b, c = mk("movie", "1"), mk("episode", "2"), mk("movie", "3", sid="B")
        actions.queue_video(self.app, [a])
        actions.queue_video(self.app, [b], first=True)        # Play Next: at the start
        actions.queue_video(self.app, [a])                    # twice is fine: two occurrences
        self.assertEqual([i.id for i in self.app.vqueue], ["2", "1", "1"])
        occ = [i.extra["occ"] for i in self.app.vqueue]
        self.assertEqual(len(set(occ)), 3)
        self.assertIsNot(self.app.vqueue[1], a)               # a copy, not the screen's item
        self.assertEqual(self.app.toasts[-1], "Added to Queue • 3 items")
        actions.queue_video(self.app, [c])                    # another server
        self.assertEqual(len(self.app.vqueue), 3)
        self.assertEqual(self.app.toasts[-1], "This queue plays from Home Server")
        self.assertEqual(actions.queue_row(self.app)[0], "Play Queue (3)")
        self.app.vqueue.clear()
        self.assertIsNone(actions.queue_row(self.app))

    def test_add_to_playlist_lists_that_servers_lists_without_smart(self):
        src = self.app.sources
        src.lists[("audio", "B")] = [Item("9", "playlist", "zz", server_id="B"),
                                     Item("8", "playlist", "Mix", server_id="B"),
                                     Item("7", "playlist", "Smart", server_id="B",
                                          extra={"smart": True})]
        actions.add_to_playlist(self.scr, mk("album", sid="B"))
        self.assertEqual(src.calls, [("list", "audio", "B")])
        title, labels, _rows = self.app.menus[-1]
        self.assertEqual((title, labels), ("ADD TO PLAYLIST", ["New Playlist", "Mix", "zz"]))

    def test_list_failure(self):
        def boom(kind, sid):
            raise OSError("down")
        self.app.sources.server_playlists = boom
        actions.add_to_playlist(self.scr, mk("movie"))
        self.assertEqual(self.app.toasts, ["Can't load playlists"])


class TestMusicChecks(unittest.TestCase):
    def test_session_and_servers_at_insert_time(self):
        app = App(Src([Prov("A"), Prov("B")]))
        m = app.music
        self.assertFalse(actions.music_accepts(app, [mk("track")]))         # no session
        m.state, m.servers = "playing", {"A"}
        self.assertTrue(actions.music_accepts(app, [mk("track")], "s1"))
        self.assertFalse(actions.music_accepts(app, [mk("track")], "old"))  # the session changed
        self.assertFalse(actions.music_accepts(app, [mk("track"), mk("track", "2", sid="B")]))
        self.assertEqual(app.toasts[-1], "This queue plays from Home Server")
        m.live = True                                                       # radio
        self.assertFalse(actions.music_accepts(app, [mk("track")]))


class TestNames(unittest.TestCase):
    def test_suggest(self):
        self.assertEqual(name.suggest(mk("album")), "album 1")
        self.assertEqual(name.suggest(Item("s", "season", "Season 2", show_title="Lost")),
                         "Lost • Season 2")
        self.assertEqual(name.suggest(mk("track")), "New Playlist")
        self.assertEqual(name.suggest(None, ["new playlist", "New Playlist 2"]), "New Playlist 3")

    def test_grid_capitals(self):
        t = ""
        for c in "road trip":
            t += name.grid_char(t, c)
        self.assertEqual(t, "Road Trip")
        self.assertEqual(name.grid_char("A", "1"), "1")


if __name__ == "__main__":
    unittest.main()
