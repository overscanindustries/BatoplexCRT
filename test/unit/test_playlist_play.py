"""A fixed queue (a playlist or the local video queue) in the playback
controller."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

pygame.font.init()                 # the overlay toast measures its text

from test_playback import SETTINGS, Base, Provider, StrictSettings  # noqa: E402

from batoplex import inputs, playback  # noqa: E402
from batoplex.model import Item, PlayInfo, Queue  # noqa: E402


class ItemProvider(Provider):
    """play_info per item; failing ids raise."""

    def __init__(self, name="A", bad=()):
        super().__init__()
        self.name, self.bad = name, set(bad)

    def play_info(self, item, resume=True):
        self.calls.append(("play_info", item.id))
        if item.id in self.bad:
            raise OSError("no file")
        return PlayInfo(url="http://%s/%s.mkv" % (self.name, item.id), headers={},
                        duration_ms=60000, title=item.title,
                        start_s=10.0 if resume and item.view_offset_ms else 0.0), item

    def episodes(self, sid):
        self.calls.append(("episodes", sid))              # a fixed queue never asks
        return []


class FixedBase(Base):
    def go(self, n=3, bad=(), settings=None, other=None):
        self.make(provider=ItemProvider(bad=bad))
        if settings:
            self.c.settings = StrictSettings(dict(SETTINGS, **settings))
        self.items = [Item("m%d" % i, "episode" if i == 1 else "movie", "Item %d" % i,
                           duration_ms=60000, season_id="s1", server_id="A",
                           extra={"occ": "pl:%d" % (900 + i)}) for i in range(n)]
        provs = {"A": self.p}
        if other is not None:
            self.items[1].server_id = "B"
            provs["B"] = other
        self.c.provider_for = lambda it: provs.get(it.server_id)
        self.item = self.items[0]
        self.c.play(self.item, self.p, queue=Queue(self.items, 0, fixed=True))
        self.c.tick()
        return self.loaded()

    def loaded(self, t=20.0):
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", t)
        self.c.tick()
        return m

    def end(self, m):
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        for _ in range(4):
            self.c.tick()


class TestFixedQueue(FixedBase):
    def test_eof_goes_on_with_no_card_and_no_shrink(self):
        m = self.go()
        self.assertEqual([i.id for i in self.c.queue.items], ["m0", "m1", "m2"])   # not rebuilt
        self.end(m)
        self.assertEqual(self.c.session, playback.STARTING)
        self.assertIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)
        self.assertEqual(len(self.mpvs), 1)                                     # same mpv
        shown = [x for x in self.overlays[-1].log if x[0] == "show" and x[2] and x[2][0] == "upnext"]
        self.assertEqual(shown, [])
        self.assertFalse(any(c[1].startswith("video-margin-ratio-right") and c[2] > 0
                             for c in m.cmds if c[0] == "set_property"))
        self.assertEqual(self.p.states().count("scrobble"), 1)

    def test_auto_play_off_does_not_stop_the_list_and_last_item_ends(self):
        m = self.go(n=2, settings={"auto_play": False})
        self.end(m)
        self.assertIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)
        self.loaded()
        self.end(m)
        self.assertEqual(self.c.session, playback.IDLE)
        r = self.c.pop_result()
        self.assertEqual([f["item_id"] for f in r["files"]], ["m0", "m1"])
        self.assertEqual(r["entry"], "pl:901")              # the occurrence that played last
        self.assertNotIn("episodes", [c[0] for c in self.p.calls])

    def test_still_watching_shows_in_a_fixed_queue(self):
        m = self.go(n=2, settings={"auto_play": False})
        self.c.duration_s = 3600.0
        self.clock.t += 3 * 3600                          # no input for 3 hours
        m.prop("time-pos", 3599.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertTrue(self.c.upnext["still"])
        self.assertEqual(self.overlays[-1].shown[1][3], True)   # the prompt is drawn
        self.c.tick()
        self.assertNotIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)
        self.c.action(inputs.OK)                                # Continue
        self.c.tick()
        self.assertIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)

    def test_a_failed_next_item_is_skipped(self):
        m = self.go(n=3, bad={"m1"})
        self.end(m)
        self.assertIn(["loadfile", "http://A/m2.mkv", "replace"], m.cmds)
        self.assertEqual(list(self.c.notices), ["Can't play Item 1"])
        self.assertEqual(self.c.queue.index, 2)
        self.loaded()
        self.assertEqual(self.c.fixed_fails, 0)               # a load resets the count

    def test_two_failures_in_a_row_end_the_session(self):
        m = self.go(n=4, bad={"m1", "m2"})
        self.end(m)
        self.assertEqual(self.c.session, playback.IDLE)
        r = self.c.pop_result()
        self.assertEqual([f["item_id"] for f in r["files"]], ["m0"])
        self.assertNotIn(("play_info", "m3"), self.p.calls)

    def test_first_item_fails_to_start_the_next_one_spawns(self):
        self.make(provider=ItemProvider(bad={"m0"}))
        items = [Item("m%d" % i, "movie", "Item %d" % i, server_id="A") for i in range(3)]
        self.c.provider_for = lambda it: self.p
        self.c.play(items[0], self.p, queue=Queue(items, 0, fixed=True))
        self.c.tick()
        self.assertEqual(self.c.session, playback.STARTING)
        self.assertEqual(self.c.s.item.id, "m1")
        self.assertEqual(self.c.queue.index, 1)
        m = self.loaded()
        self.assertIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)
        self.c.action(inputs.BACK)
        r = self.c.pop_result()
        self.assertEqual([f["item_id"] for f in r["files"]], ["m1"])   # the failed one: nothing
        self.assertNotIn("scrobble", self.p.states())

    def test_each_item_plays_from_its_own_server(self):
        b = ItemProvider("B")
        m = self.go(n=2, other=b)
        self.end(m)
        self.assertIn(["loadfile", "http://B/m1.mkv", "replace"], m.cmds)
        self.loaded()
        self.c.action(inputs.BACK)
        self.assertIn("stopped", b.states())                    # its report goes to B
        self.assertNotIn(("play_info", "m1"), self.p.calls)

    def test_an_item_of_a_server_not_connected_is_skipped(self):
        m = self.go(n=3)
        self.items[1].server_id = "GONE"
        self.end(m)
        self.assertIn(["loadfile", "http://A/m2.mkv", "replace"], m.cmds)

    def test_keep_open_pause_at_the_end_is_the_end(self):
        m = self.go(n=2)
        m.prop("time-pos", 59.6)
        m.prop("pause", True)                  # keep-open, no eof-reached (e2e run 15)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)
        self.clock.t += 1
        for _ in range(3):
            self.c.tick()
        self.assertIn(["loadfile", "http://A/m1.mkv", "replace"], m.cmds)

    def test_a_user_pause_near_the_end_is_not(self):
        m = self.go(n=2)
        m.prop("time-pos", 59.6)
        self.c.tick()
        self.c.action(playback.PAUSE)
        m.prop("pause", True)
        self.clock.t += 5
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_bar_says_next_and_credits_do_not_open_up_next(self):
        from batoplex.model import Marker
        m = self.go(n=2)
        self.c.s.pi.markers = [Marker("credits", 50000, 58000, True)]
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)      # no Up Next in the credits
        self.assertIn(("next", "Next"), self.c._bar_buttons())


if __name__ == "__main__":
    unittest.main()
