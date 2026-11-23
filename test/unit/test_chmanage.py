"""Add a Channel and Manage Channels: suggestions leave out what the
file has, the wizard defaults (A A A saves), Move keeps the set of numbers, a stale stamp is
refused with a toast."""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import channels, inputs  # noqa: E402
from batoplex.screens import chsetup  # noqa: E402

TV = {"server": "", "section": 1, "title": "TV Shows", "type": "show", "episodes": 400,
      "minutes": 12000,
      "shows": [{"id": "10", "title": "Seinfeld", "episodes": 180, "minutes": 5400,
                 "network": "NBC"},
                {"id": "11", "title": "Frasier", "episodes": 264, "minutes": 7900,
                 "network": "NBC"}],
      "genres": [{"genre": "Comedy", "count": 2}], "collections": []}
MOVIES = {"server": "", "section": 2, "title": "Movies", "type": "movie", "movies": 40,
          "minutes": 4000, "genres": [{"genre": "Action", "count": 12}], "collections": []}


class Lineup:
    key, version = "k", 0

    def __init__(self):
        self.status, self.refreshed = {}, 0

    def refresh(self):
        self.refreshed += 1


class App:
    def __init__(self):
        self.settings = {"safe": {"left": 40, "top": 36, "right": 40, "bottom": 36}}
        self.lineup = Lineup()
        self.toasts, self.stack, self.menus = [], [], []

    def toast(self, msg, private=False):
        self.toasts.append(msg)

    def push(self, scr):
        self.stack.append(scr)

    def overlay(self, scr, kind="menu"):
        self.stack.append(scr)

    def pop(self, result=None):
        if self.stack:
            self.stack.pop()

    @property
    def top(self):
        return self.stack[-1] if self.stack else None

    def open_menu(self, rows, title=None, settings=True):
        self.menus.append(rows)


def write(chs):
    with open(channels.path(), "w") as f:
        json.dump({"version": 1, "channels": chs}, f)


def doc():
    with open(channels.path()) as f:
        return json.load(f)["channels"]


class Base(unittest.TestCase):
    def setUp(self):
        try:
            os.remove(channels.path())
        except OSError:
            pass
        chsetup._scan.update(key="k", t=time.monotonic(), res=([TV, MOVIES], []))
        self.app = App()


class TestAdd(Base):
    def test_suggestions_leave_out_existing_and_one_press_adds(self):
        write([{"id": "aaaaaa", "number": 2, "name": "FRASIER",
                "source": {"type": "show", "id": "11", "title": "Frasier"}}])
        scr = chsetup.AddScreen(self.app)
        titles = [p["channel"]["source"].get("title") for p in scr.props]
        self.assertNotIn("Frasier", titles)
        i = next(k for k, p in enumerate(scr.props)
                 if p["channel"]["source"].get("title") == "Seinfeld")
        scr._ok(("sug", i))
        scr._ok(("sug", i))                       # added already: no second channel
        shows = [c for c in doc() if c["source"].get("title") == "Seinfeld"]
        self.assertEqual(len(shows), 1)
        self.assertEqual(shows[0]["number"], 3)
        self.assertEqual(self.app.toasts, ["Added as Channel 03"])
        self.assertEqual(self.app.lineup.refreshed, 1)

    def test_wizard_defaults_save_with_a_a_a(self):
        scr = chsetup.AddScreen(self.app)
        rows = scr.rows()
        scr.focus = [r for k, r in enumerate(rows) if r[0] != "head"].index(("kind", "show"))
        for _ in range(3):
            scr.handle(inputs.OK)                 # kind, source (Frasier: first by title), Save
        self.assertEqual(scr.step, "closed")
        (c,) = doc()
        self.assertEqual((c["number"], c["name"], c["order"], c["source"]["id"]),
                         (2, "FRASIER", "order", "11"))

    def test_order_choices_and_numbers(self):
        cat = channels.catalog([TV, MOVIES])
        self.assertEqual([e["title"] for e in cat["show"]], ["Frasier", "Seinfeld"])
        self.assertEqual(cat["network"][0]["count"], 2)
        movie_genre = cat["genre"][0]
        self.assertEqual(movie_genre["title"], "Action")
        self.assertEqual(channels.order_choices("genre", "movie"), ["shuffle"])
        self.assertEqual(channels.order_choices("genre", "show"), ["blocks", "shuffle"])
        self.assertEqual(channels.step_number(3, {4, 5}, 1), 6)
        self.assertEqual(channels.step_number(2, {3}, -1), 2)


class TestManage(Base):
    def setUp(self):
        super().setUp()
        write([{"id": "aaaaaa", "number": 2, "name": "A", "source": {"type": "library", "section": 1}},
               {"id": "bbbbbb", "number": 4, "name": "B", "source": {"type": "library", "section": 2}},
               {"id": "cccccc", "number": 9, "name": "C", "source": {"type": "show", "id": "10"}}])

    def test_reorder_keeps_the_numbers(self):
        lu = channels.load()
        self.assertEqual(channels.reorder(lu.channels, ["cccccc", "aaaaaa", "bbbbbb"]),
                         {"cccccc": 2, "aaaaaa": 4, "bbbbbb": 9})
        scr = chsetup.ManageScreen(self.app)
        scr.move_start("cccccc")
        scr.handle(inputs.UP)
        scr.handle(inputs.UP)
        scr.handle(inputs.OK)
        self.assertEqual({c["id"]: c["number"] for c in doc()},
                         {"aaaaaa": 4, "bbbbbb": 9, "cccccc": 2})

    def test_stale_stamp_is_refused_with_a_toast(self):
        scr = chsetup.ManageScreen(self.app)
        time.sleep(0.01)
        with open(channels.path()) as f:          # a hand edit after the screen read the file
            raw = f.read()
        with open(channels.path(), "w") as f:
            f.write(raw + " ")
        scr.set_name("aaaaaa", "NEWS")
        self.assertEqual(self.app.toasts, ["channels.json changed"])
        self.assertEqual(doc()[0]["name"], "A")
        scr.set_name("aaaaaa", "NEWS")            # the screen read the file again
        self.assertEqual(doc()[0]["name"], "NEWS")

    def test_start_over_clears_after_the_confirm(self):
        scr = chsetup.ManageScreen(self.app)
        self.app.stack.append(scr)
        self.assertEqual(scr.rows()[-1], ("over",))
        scans = []
        old = chsetup.SetupScreen._scan
        chsetup.SetupScreen._scan = lambda s: scans.append(s)
        try:
            scr.ask_start_over()
            dlg = self.app.top
            self.assertEqual(len(doc()), 3)                   # nothing before the confirm
            dlg.on_yes()
        finally:
            chsetup.SetupScreen._scan = old
        self.assertEqual(doc(), [])
        self.assertIsInstance(self.app.top, chsetup.SetupScreen)
        self.assertEqual(scans, [self.app.top])

    def test_off_and_status(self):
        scr = chsetup.ManageScreen(self.app)
        scr.set_off("bbbbbb", True)
        self.assertTrue(doc()[1]["off"])
        c = channels.load().channels[1]
        self.assertEqual(chsetup.status_text(c, {}), "Off")
        scr.set_off("bbbbbb", False)
        self.assertNotIn("off", doc()[1])
        self.assertEqual(chsetup.status_text(channels.load().channels[1], {"bbbbbb": "missing"}),
                         "Source not found")


class TestSchedule(Base):
    """Time blocks: Add a Time Block is kind, source, Save; the first
    block keeps the channel's own source from the day start; a hand-written key stays."""

    def setUp(self):
        super().setUp()
        write([{"id": "aaaaaa", "number": 2, "name": "A", "order": "order", "mine": 1,
                "source": {"type": "show", "id": "10", "title": "Seinfeld"}}])

    def test_add_edit_delete_blocks(self):
        sched = chsetup.ScheduleScreen(self.app, "aaaaaa")
        self.app.stack.append(sched)
        sched.handle(inputs.OK)                   # Add a Time Block
        add = self.app.top
        add.focus = [r for r in add.rows() if r[0] != "head"].index(("kind", "library"))
        add.handle(inputs.OK)                     # A Library
        add.handle(inputs.OK)                     # TV Shows
        blk = self.app.top
        self.assertEqual((blk.b["start"], blk.b["days"]), (20 * 60, None))   # smart defaults
        blk.handle(inputs.OK)                     # Save Block
        (c,) = doc()
        self.assertEqual(c["mine"], 1)
        self.assertEqual(c["blocks"], [
            {"start": "04:00", "source": {"type": "show", "id": "10", "title": "Seinfeld"},
             "order": "order"},
            {"start": "20:00", "source": {"type": "library", "section": 1}, "order": "blocks"}])
        ch = channels.load().channels[0]
        self.assertEqual(chsetup.kind_name(ch), "Time Blocks")
        # edit the 20:00 block: Weekends, 30 min later
        sched.reload()
        blk = chsetup.BlockScreen(self.app, sched, 1, dict(ch.blocks[1]), {})
        blk.adjust("days", 1)
        blk.adjust("days", 1)
        blk.adjust("start", 1)
        blk.save()
        self.assertEqual(doc()[0]["blocks"][1]["days"], ["sat", "sun"])
        self.assertEqual(doc()[0]["blocks"][1]["start"], "20:30")
        # a start in use on the same days is passed
        sched.reload()
        blk = chsetup.BlockScreen(self.app, sched, 0, dict(sched.ch.blocks[0]), {})
        blk.b["start"] = 20 * 60
        blk.adjust("start", 1)
        self.assertEqual(blk.b["start"], 21 * 60)          # 20:30 is taken every day
        # delete the 20:30 block: the channel plays its source all day again
        sched.reload()
        blk = chsetup.BlockScreen(self.app, sched, 1, dict(sched.ch.blocks[1]), {})
        blk.delete()
        (c,) = doc()
        self.assertNotIn("blocks", c)
        self.assertEqual((c["source"]["id"], c["order"], c["mine"]), ("10", "order", 1))

    def test_movie_block_plays_one_movie(self):
        movie = {"type": "theme", "theme": "comedy", "kind": "movie", "sections": [["", 2]]}
        write([{"id": "bbbbbb", "number": 3, "name": "B", "blocks": [
            {"start": "04:00", "source": dict(movie, kind="show"), "order": "blocks"},
            {"start": "20:00", "source": movie, "order": "shuffle", "once": True}]}])
        sched = chsetup.ScheduleScreen(self.app, "bbbbbb")
        blk = chsetup.BlockScreen(self.app, sched, 1, dict(sched.ch.blocks[1]), {})
        self.assertIn(("once",), blk.rows())
        blk.adjust("once", 1)
        blk.save()
        self.assertNotIn("once", doc()[0]["blocks"][1])
        show = chsetup.BlockScreen(self.app, sched, 0, dict(sched.ch.blocks[0]), {})
        self.assertNotIn(("once",), show.rows())
        self.assertEqual(show.orders(), ["blocks", "shuffle"])

    def test_days_names(self):
        self.assertEqual(channels.days_name(None), "Every Day")
        self.assertEqual(channels.days_name(frozenset({"sat", "sun"})), "Weekends")
        self.assertEqual(channels.days_name(frozenset({"fri", "mon"})), "Mon, Fri")


class TestDiscard(Base):
    """B with unsaved edits asks "Discard changes?"; no edit, or an edit put
    back, closes at once."""
    MOVIE = {"type": "theme", "theme": "comedy", "kind": "movie", "sections": [["", 2]]}

    def block(self, index=1, b=None):
        write([{"id": "bbbbbb", "number": 3, "name": "B", "blocks": [
            {"start": "04:00", "source": dict(self.MOVIE, kind="show"), "order": "blocks"},
            {"start": "20:00", "source": self.MOVIE, "order": "shuffle"}]}])
        sched = chsetup.ScheduleScreen(self.app, "bbbbbb")
        blk = chsetup.BlockScreen(self.app, sched, index, b or dict(sched.ch.blocks[1]), {})
        self.app.stack[:] = [sched, blk]
        return sched, blk

    def test_block_no_edit_closes(self):
        sched, blk = self.block()
        blk.handle(inputs.BACK)
        self.assertIs(self.app.top, sched)

    def test_block_edit_asks_and_cancel_stays(self):
        from batoplex.screens.system import ConfirmDialog
        sched, blk = self.block()
        blk.adjust("days", 1)
        blk.handle(inputs.BACK)
        dlg = self.app.top
        self.assertIsInstance(dlg, ConfirmDialog)
        self.assertEqual((dlg.title, dlg.labels, dlg.focus),
                         ("Discard changes?", ("Discard", "Cancel"), 1))
        dlg.handle(inputs.OK)                         # Cancel
        self.assertIs(self.app.top, blk)
        blk.handle(inputs.BACK)
        self.app.top.handle(inputs.BACK)              # B on the dialog: Cancel too
        self.assertIs(self.app.top, blk)
        blk.handle(inputs.BACK)
        self.app.top.handle(inputs.LEFT)
        self.app.top.handle(inputs.OK)                # Discard
        self.assertIs(self.app.top, sched)
        self.assertEqual(doc()[0]["blocks"][1].get("days"), None)   # nothing written

    def test_block_edit_and_back_again_closes(self):
        sched, blk = self.block()
        blk.adjust("start", 1)
        blk.adjust("start", -1)
        blk.handle(inputs.BACK)
        self.assertIs(self.app.top, sched)

    def test_new_block_once_twice_closes(self):
        b = {"start": 12 * 60, "days": None, "source": dict(self.MOVIE), "order": "shuffle"}
        sched, blk = self.block(index=None, b=b)
        self.assertIn(("once",), blk.rows())
        blk.adjust("once", 1)
        blk.adjust("once", 1)
        self.assertIs(blk.b["once"], False)           # the key is there now, False
        blk.handle(inputs.BACK)
        self.assertIs(self.app.top, sched)

    def test_days_list_or_tuple_same(self):
        b = {"start": 60, "days": ["sun", "sat"], "source": dict(self.MOVIE)}
        self.assertEqual(chsetup.block_state(b), chsetup.block_state(dict(b, days=("sat", "sun"))))

    def test_setup_result(self):
        scr = chsetup.SetupScreen(self.app)
        self.app.stack[:] = [scr]
        scr.props = [{"default": True}, {"default": False}]
        scr.on, scr.on0, scr.state = [True, False], [True, False], "result"
        scr.handle(inputs.BACK)                       # no edit: back to the intro at once
        self.assertEqual((scr.state, self.app.top), ("intro", scr))
        scr.state = "result"
        scr.handle(inputs.OK)                         # one edit
        scr.handle(inputs.BACK)
        self.assertIsNot(self.app.top, scr)
        self.app.top.handle(inputs.OK)                # Cancel
        self.assertEqual((scr.state, self.app.top), ("result", scr))
        scr.handle(inputs.OK)                         # put back
        scr.handle(inputs.BACK)
        self.assertEqual((scr.state, self.app.top), ("intro", scr))
        scr.state = "result"
        scr.handle(inputs.OK)
        scr.handle(inputs.BACK)
        self.app.top.handle(inputs.LEFT)
        self.app.top.handle(inputs.OK)                # Discard
        self.assertEqual((scr.state, self.app.top), ("intro", scr))


if __name__ == "__main__":
    unittest.main()
