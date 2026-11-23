"""The Playlist screen (rows, play dispatch, paging, geometry)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import inputs, plex  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import playlist as plm  # noqa: E402
from test_now_playing import App as NPApp, SAFE  # noqa: E402


def entries(n, kind="track", disc_mix=True):
    out = []
    for i in range(n):
        if kind == "track":
            it = Item("t%d" % i, "track", "Song %d" % i, duration_ms=180000, index=1,
                      disc=(i % 2 + 1) if disc_mix else 1, artist="Band", album="LP",
                      album_id="a1", server_id="S")
        else:
            it = Item("m%d" % i, "movie", "Movie %d" % i, duration_ms=5400000, year=1990 + i,
                      server_id="S")
        it.extra.update(pl_item=str(500 + i), occ="pl:%d" % (500 + i))
        out.append(it)
    return out


class Sources:
    def __init__(self, items, page_fail=None):
        self.items, self.page_fail, self.pages = items, page_fail, []

    def playlist_items(self, pl, start, size=200):
        self.pages.append(start)
        if self.page_fail is not None and start in self.page_fail:
            raise self.page_fail[start]
        return self.items[start:start + size], len(self.items), len(self.items[start:start + size])

    def strict(self, sid):
        return object() if sid in ("S", None) else None

    def server_count(self):
        return 1

    def server_name(self, sid):
        return sid


class App(NPApp):
    def __init__(self, src):
        super().__init__()
        self.sources = src
        self.music.state = "idle"
        self.played, self.posted, self.lost, self.src_gen = [], [], 0, 0

    def play_music(self, items, index=0, shuffle=False, origin=None):
        self.played.append(("music", len(items), index, shuffle))

    def play_playlist(self, items, index=0, shuffle=False, resume=True, origin=None, local=False):
        self.played.append(("video", len(items), index, shuffle, resume))

    def auth_lost(self):
        self.lost += 1

    def post(self, fn):
        self.posted.append(fn)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.real = Screen.run_bg

        def sync(scr, fn, done, fail=None):
            try:
                r = fn()
            except Exception as e:
                scr.app.post(lambda e=e: fail(e))
                return
            scr.app.post(lambda: done(r))
        Screen.run_bg = sync

    def tearDown(self):
        Screen.run_bg = self.real

    def make(self, items, kind="audio", title="Road Trip", **kw):
        app = App(Sources(items, **kw))
        pl = Item("77", "playlist", title, server_id="S", leaf_count=len(items),
                  extra={"kind": kind, "smart": False})
        s = plm.PlaylistScreen(app, pl)
        app.push(s)
        self.drain(app)
        return app, s

    @staticmethod
    def drain(app):
        while app.posted:
            app.posted.pop(0)()


class TestPlaylistScreen(Base):
    def test_rows_are_places_with_no_disc_lines(self):
        app, s = self.make(entries(6))
        self.assertEqual(s.rows, [("track", i) for i in range(6)])
        s.draw(pygame.Surface((641, 480)))

    def test_play_dispatch(self):
        app, s = self.make(entries(5))
        s.handle(inputs.OK)                               # Play
        s.handle(inputs.RIGHT)
        s.handle(inputs.OK)                               # Shuffle
        s.handle(inputs.RIGHT)
        s.handle(inputs.DOWN)
        s.handle(inputs.OK)
        self.assertEqual(app.played, [("music", 5, 0, False), ("music", 5, 0, True),
                                      ("music", 5, 1, False)])
        app, s = self.make(entries(3, "movie"), kind="video")
        s.handle(inputs.RIGHT)
        s.handle(inputs.RIGHT)
        s.handle(inputs.DOWN)
        s.handle(inputs.OK)
        self.assertEqual(app.played, [("video", 3, 1, False, True)])
        s.tracks[2].view_offset_ms = 60000
        s.handle(inputs.DOWN)
        rows = dict(s.menu_items())
        rows["Play from Start"]()
        self.assertEqual(app.played[-1], ("video", 3, 2, False, False))

    def test_pages_of_200_and_play_waits_for_all(self):
        app = App(Sources(entries(450, disc_mix=False)))
        pl = Item("77", "playlist", "Big", server_id="S", extra={"kind": "audio"})
        s = plm.PlaylistScreen(app, pl)
        app.push(s)
        app.posted.pop(0)()                               # page 1 lands
        self.assertEqual((len(s.tracks), s.complete), (200, False))
        s.handle(inputs.OK)
        self.assertEqual(app.played, [])                  # not all pages in yet
        self.drain(app)
        self.assertEqual((len(s.tracks), s.complete, app.sources.pages), (450, True, [0, 200, 400]))
        s.handle(inputs.OK)
        self.assertEqual(app.played, [("music", 450, 0, False)])

    def test_failures(self):
        app, s = self.make(entries(3), page_fail={0: plex.PlexError("down")})
        self.assertTrue(s.error)
        s.draw(pygame.Surface((641, 480)))
        app, s = self.make(entries(3), page_fail={0: plex.AuthError("HTTP 401")})
        self.assertEqual(app.lost, 1)

    def test_focus_returns_to_the_occurrence_that_played(self):
        items = entries(4, "movie")
        items[3].id = items[1].id                         # the same movie twice
        app, s = self.make(items, kind="video")
        s.on_resume({"entry": "pl:503", "files": []})
        self.assertEqual((s.zone, s.focus), ("list", 3))
        s.apply_files([{"item_id": "m1", "server_id": "S", "position_ms": 0, "watched": True}])
        self.assertTrue(s.tracks[1].watched and s.tracks[3].watched)

    def test_text_fits_above_the_buttons(self):
        app, s = self.make(entries(3), title="A Very Long Playlist Name That Takes Two Lines")
        for safe in SAFE + ({"left": 40, "right": 40, "top": 56, "bottom": 56},):
            app.settings["safe"] = safe
            g = s.geo()
            end = g["title_y"] + 28 * len(g["title_lines"]) + 4 + 2 * 24
            self.assertLessEqual(end, g["btn_y"] - 8, safe)
            self.assertGreaterEqual(g["art"].y, safe["top"])
            s.draw(pygame.Surface((641, 480)))


if __name__ == "__main__":
    unittest.main()


class EditSources(Sources):
    def __init__(self, items, fail=None):
        super().__init__(items)
        self.writes, self.fail = [], fail
        self.home_id = "S"

    def _w(self, *a):
        self.writes.append(a)
        if self.fail:
            raise self.fail

    def playlist_move(self, pl, entry, after):
        self._w("move", entry.extra["pl_item"], after.extra["pl_item"] if after else None)

    def playlist_remove(self, pl, entry):
        self._w("remove", entry.extra["pl_item"])


class EditApp(App):
    def __init__(self, src):
        super().__init__(src)
        self.changed = []

    def playlist_changed(self, pl):
        self.changed.append(pl.id)

    def toast(self, m, private=False):
        self.toasts.append(m)


class TestEditing(Base):
    def setUp(self):
        super().setUp()
        import threading
        self.real_thread = threading.Thread

        class Now:                                     # _write_bg's worker runs at once
            def __init__(self, target=None, daemon=None):
                self.target = target

            def start(self):
                self.target()
        plm.threading.Thread = Now

    def tearDown(self):
        plm.threading.Thread = self.real_thread
        super().tearDown()

    def make_edit(self, n=4, fail=None, smart=False):
        app = EditApp(EditSources(entries(n, disc_mix=False), fail))
        pl = Item("77", "playlist", "Road Trip", server_id="S", extra={"kind": "audio", "smart": smart})
        s = plm.PlaylistScreen(app, pl)
        app.push(s)
        self.drain(app)
        s.handle(inputs.RIGHT)
        s.handle(inputs.RIGHT)                         # the list, first row
        return app, s

    def order(self, s):
        return [t.extra["pl_item"] for t in s.tracks]

    def test_move_sends_the_entry_above(self):
        app, s = self.make_edit()
        s.handle(inputs.DOWN)                          # entry 501
        dict(s.menu_items())["Move"]()
        s.handle(inputs.DOWN)
        s.handle(inputs.DOWN)
        s.handle(inputs.OK)
        self.drain(app)
        self.assertEqual(self.order(s), ["500", "502", "503", "501"])
        self.assertEqual(app.sources.writes, [("move", "501", "503")])
        dict(s.menu_items())["Move"]()
        s.handle(inputs.LT)                            # five up: the first place
        s.handle(inputs.OK)
        self.drain(app)
        self.assertEqual(app.sources.writes[-1], ("move", "501", None))
        self.assertEqual(app.changed, ["77", "77"])

    def test_cancel_and_same_place_send_nothing(self):
        app, s = self.make_edit()
        dict(s.menu_items())["Move"]()
        s.handle(inputs.DOWN)
        s.handle(inputs.BACK)
        self.assertEqual(self.order(s), ["500", "501", "502", "503"])
        self.assertEqual(s.focus, 0)
        dict(s.menu_items())["Move"]()
        s.handle(inputs.OK)
        self.assertEqual(app.sources.writes, [])

    def test_remove_targets_the_occurrence(self):
        app, s = self.make_edit()
        s.tracks[2].id = s.tracks[0].id                # the same song twice
        s.handle(inputs.DOWN)
        s.handle(inputs.DOWN)
        dict(s.menu_items())["Remove from Playlist"]()
        self.drain(app)
        self.assertEqual(app.sources.writes, [("remove", "502")])
        self.assertEqual(self.order(s), ["500", "501", "503"])

    def test_a_failed_write_loads_the_list_again(self):
        app, s = self.make_edit(fail=plex.PlexError("HTTP 403"))
        dict(s.menu_items())["Remove from Playlist"]()
        self.assertEqual(len(s.tracks), 3)             # the screen changed first
        self.drain(app)
        self.assertIn("Can't change this playlist", app.toasts)
        self.assertEqual(len(s.tracks), 4)             # loaded again
        self.assertFalse(s.writing)

    def test_smart_has_no_edit_rows(self):
        app, s = self.make_edit(smart=True)
        self.assertNotIn("Move", dict(s.menu_items()))
        self.assertNotIn("Remove from Playlist", dict(s.menu_items()))
        s.handle(inputs.LEFT)
        self.assertEqual(s.menu_items(), [])           # buttons: no Rename, no Delete
        app, s = self.make_edit()
        s.handle(inputs.LEFT)
        self.assertEqual([r[0] for r in s.menu_items()], ["Rename", "Delete Playlist"])


class TestConfirm(Base):
    def test_long_names_fit_and_keep_their_ends(self):
        from batoplex.screens import system
        a = "Delete “Summer Road Trip Songs For The Long Drive Up The Coast Part One”?"
        b = a.replace("Part One", "Part Two")
        la = system.fit_lines(a, "heading", 312, 3)
        lb = system.fit_lines(b, "heading", 312, 3)
        self.assertLessEqual(len(la), 3)
        self.assertNotEqual(la, lb)                     # the ends still differ
        app = App(Sources([]))
        app.settings["safe"] = SAFE[1]
        d = system.ConfirmDialog(app, Screen(app), a, "Delete", lambda: None)
        x, y, w, h, lines = d.geo()
        self.assertLessEqual(y + h, 480 - SAFE[1]["bottom"])
        self.assertEqual(d.focus, 1)                   # Cancel
