import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import audio, music  # noqa: E402
from batoplex.model import Item, PlayInfo  # noqa: E402
from fake_mpv import FakeMpv  # noqa: E402


class Clock:
    t = 100.0

    def __call__(self):
        return self.t


def sync(fn, done, fail):
    try:
        r = fn()
    except Exception as e:
        fail(e)
        return
    done(r)


class Prov:
    def __init__(self):
        self.reports, self.scrobbles = [], []

    def track_info(self, t):
        return PlayInfo(url="http://s/" + t.id, headers={"X-Plex-Token": "tok"},
                        duration_ms=t.duration_ms)

    def timeline(self, t, state, ms, dur, sess):
        self.reports.append((t.id, state, ms))

    def scrobble(self, t):
        self.scrobbles.append(t.id)


def tracks(n):
    return [Item("t%d" % i, "track", "T%d" % i, duration_ms=100000) for i in range(n)]


class Base(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.mpvs = []

        def mk():
            m = FakeMpv()
            self.mpvs.append(m)
            return m
        self.a = audio.AudioPlayer("mpv", clock=self.clk, run=sync, make_mpv=mk)
        self.p = Prov()
        self.c = music.MusicController(self.a, clock=self.clk, run=sync)
        self.items = tracks(4)

    @property
    def m(self):
        return self.mpvs[-1]

    def tick(self):
        self.a.tick()
        self.c.tick()

    def ev(self, event, **kw):
        self.m.fire(event, **kw)
        self.tick()

    def prop(self, name, data):
        self.ev("property-change", name=name, data=data)

    def loads(self):
        return [c[1:3] for c in self.m.cmds if c[0] == "loadfile"]

    def start(self, i=0, **kw):
        self.c.play(self.items, i, self.p, **kw)
        self.tick()


class TestQueue(Base):
    def test_two_entries_and_gapless_advance(self):
        self.start()
        self.assertEqual(self.loads(), [["http://s/t0", "replace"], ["http://s/t1", "append"]])
        self.assertEqual(self.c.state, "playing")
        self.assertEqual(self.p.reports, [("t0", "playing", 0)])
        self.prop("time-pos", 99.5)
        self.prop("playlist-pos", 1)                 # t0 ended, t1 plays
        self.assertEqual(self.c.index, 1)
        self.assertEqual(self.p.scrobbles, ["t0"])
        self.assertEqual(self.p.reports[-2:], [("t0", "stopped", 100000), ("t1", "playing", 0)])
        self.assertIn(["playlist-remove", 0], self.m.cmds)
        self.assertEqual(self.loads()[-1], ["http://s/t2", "append"])

    def test_reports_every_10s_pause_resume(self):
        self.start()
        self.clk.t += 10
        self.tick()
        self.assertEqual(self.p.reports[-1][1], "playing")
        self.assertEqual(len(self.p.reports), 2)
        self.c.toggle()
        self.assertEqual((self.c.state, self.p.reports[-1][1]), ("paused", "paused"))
        self.c.toggle()
        self.assertEqual(self.p.reports[-1][1], "playing")
        self.c.pause()
        self.clk.t += 600
        self.tick()
        self.assertEqual((self.c.state, self.p.reports[-1][1]), ("idle", "stopped"))

    def test_skip_scrobbles_only_at_90(self):
        self.start()
        self.prop("time-pos", 50)
        self.c.next()
        self.tick()
        self.assertEqual((self.c.index, self.p.scrobbles), (1, []))
        self.prop("time-pos", 95)
        self.c.next()
        self.tick()
        self.assertEqual(self.p.scrobbles, ["t1"])

    def test_prev_under_and_over_3s(self):
        self.start(2)
        self.prop("time-pos", 10)
        self.c.prev()
        self.assertEqual(self.c.index, 2)
        self.assertEqual(self.m.cmds[-1], ["seek", 0, "absolute"])
        self.prop("time-pos", 1)
        self.c.prev()
        self.tick()
        self.assertEqual(self.c.index, 1)

    def test_queue_end_and_repeat(self):
        self.start(3)
        self.assertEqual(len(self.loads()), 1)       # the last track: no next entry
        self.ev("end-file", reason="eof")
        self.assertEqual((self.c.state, self.p.scrobbles), ("idle", ["t3"]))
        self.c.cycle_repeat()                        # all
        self.start(3)
        self.assertEqual(self.loads()[-1], ["http://s/t0", "append"])
        self.c.cycle_repeat()                        # one: the same track again
        self.assertEqual(self.loads()[-1], ["http://s/t3", "append"])
        self.prop("playlist-pos", 1)
        self.assertEqual((self.c.index, self.p.scrobbles), (3, ["t3", "t3"]))

    def test_shuffle_keeps_current(self):
        self.start(2)
        cur = self.c.track
        self.c.set_shuffle(True)
        self.assertIs(self.c.track, cur)
        self.assertEqual(self.c.index, 0)
        self.c.set_shuffle(False)
        self.assertEqual((self.c.index, self.c.track), (2, cur))

    def test_errors_skip_then_end(self):
        self.start()
        self.ev("end-file", reason="error")
        self.assertEqual(self.c.index, 1)
        self.ev("end-file", reason="error")
        self.ev("end-file", reason="error")
        self.assertEqual(self.c.state, "idle")

    def test_add_on_last_track_sets_next(self):
        self.start(3)
        self.c.add(tracks(1))
        self.assertEqual(self.loads()[-1], ["http://s/t0", "append"])

    def test_next_entry_follows_the_item(self):
        # Remove and Play Next change the item under the next index.
        self.start()
        self.c.remove(1)
        self.assertEqual(self.loads()[-1], ["http://s/t2", "append"])
        self.prop("playlist-pos", 1)
        self.assertEqual(self.c.track.id, "t2")
        x = Item("x", "track", "X", duration_ms=100000)
        self.c.play_next([x])
        self.assertEqual(self.loads()[-1], ["http://s/x", "append"])
        self.prop("playlist-pos", 1)
        self.assertIs(self.c.track, x)

    def test_eof_before_the_new_next_entry_opens_it(self):
        held = []
        self.start()
        self.c.run = lambda fn, d, f: held.append((fn, d, f))
        self.c.remove(1)                             # the new entry is not set yet
        self.ev("end-file", reason="eof")
        self.c.run = sync
        for fn, d, f in held:
            d(fn())
        self.tick()
        self.assertEqual(self.c.state, "playing")
        self.assertEqual(self.c.track.id, "t2")

    def test_next_entry_set_while_mpv_starts(self):
        self.c.play(self.items, 3, self.p)           # mpv not up yet
        self.c.add(tracks(1))
        self.tick()
        self.assertEqual(self.loads()[-1], ["http://s/t0", "append"])

    def test_station_refills_near_the_end(self):
        calls = []

        def refill():
            calls.append(1)
            if len(calls) > 2:
                raise OSError("down")
            return tracks(2)
        self.c.play(self.items, 0, self.p, refill=refill)   # 3 left at once: a refill
        self.tick()
        self.assertEqual(len(self.c.items), 6)
        self.c.jump(4)                                       # a skip keeps the refill
        self.assertEqual(len(self.c.items), 8)
        self.c.jump(6)                                       # the refill fails: no more
        self.assertIsNone(self.c.refill)
        self.assertEqual(len(self.c.items), 8)

    def test_old_session_results_dropped(self):
        held = []
        self.c.run = lambda fn, d, f: held.append((fn, d, f))
        self.c.play(self.items, 0, self.p)
        self.c.stop()
        for fn, d, f in held:
            d(fn())
        self.assertEqual((self.c.state, self.a.owner), ("idle", None))

    def test_stop_reports_once(self):
        self.start()
        self.c.stop(wait_s=0.5)
        self.c.stop()
        self.assertEqual([r for r in self.p.reports if r[1] == "stopped"], [("t0", "stopped", 0)])


def stations(n):
    return [Item("https://r%d.example/s" % i, "station", "Station %d" % i,
                 extra={"url": "https://r%d.example/s" % i}) for i in range(n)]


class TestRadio(Base):
    def test_live_station_no_reports_no_plex_header(self):
        self.start()                                 # a Plex queue first: its token header
        self.c.play_live(stations(3), 0)
        self.tick()
        self.assertEqual(self.loads()[-1], ["https://r0.example/s", "replace"])
        hdr = [c for c in self.m.cmds if c[:2] == ["set_property", "http-header-fields"]]
        self.assertEqual(hdr[-1][2], "")
        n = len(self.p.reports)
        self.prop("metadata/by-key/icy-title", "Boards - Roygbiv")
        self.assertEqual(self.c.now_title, "Roygbiv")
        self.clk.t += 30
        self.tick()
        self.c.next()
        self.c.stop()
        self.assertEqual(len(self.p.reports), n)    # no timeline, no scrobble for radio
        self.assertEqual(self.p.scrobbles, [])

    def test_next_prev_wrap_and_mirror_list(self):
        self.c.play_live(stations(3), 0)
        self.tick()
        self.c.prev()
        self.assertEqual(self.c.index, 2)
        self.c.next()
        self.assertEqual(self.c.index, 0)
        self.prop("playlist-pos", 1)                 # mpv moved to a .pls mirror
        self.assertEqual((self.c.index, self.c.state), (0, "playing"))

    def test_drop_retries_then_gives_up(self):
        toasts = []
        self.c.toast = toasts.append
        self.c.play_live(stations(2), 1)
        self.tick()
        opens = len(self.loads())
        for wait in (2, 5, 10):
            self.ev("end-file", reason="eof")
            self.assertEqual(self.c.state, "loading")
            self.clk.t += wait - 0.5
            self.tick()
            self.assertEqual(len(self.loads()), opens)   # not yet
            self.clk.t += 0.5
            self.tick()
            opens += 1
            self.assertEqual(self.loads()[-1], ["https://r1.example/s", "replace"])
        self.ev("end-file", reason="error")
        self.assertEqual((self.c.state, toasts), ("idle", ["Can't play this station"]))

    def test_mirror_recovers_and_station_change_clears_retry(self):
        self.c.play_live(stations(2), 0)
        self.tick()
        self.ev("end-file", reason="error")          # the first .pls mirror is down
        self.assertIsNotNone(self.c.retry_at)
        self.prop("time-pos", 1.0)                   # mpv plays the next mirror
        self.assertEqual((self.c.state, self.c.retry_at), ("playing", None))
        self.ev("end-file", reason="eof")
        self.c.next()                                # a new station: no old retry
        self.assertIsNone(self.c.retry_at)
        self.assertEqual(self.c.errors, 0)

    def test_next_with_one_station_keeps_playing(self):
        self.c.play_live(stations(1), 0)
        self.tick()
        self.c.next()
        self.assertEqual(self.c.state, "playing")

    def test_removed_stations_leave_the_queue_current_plays_on(self):
        self.c.play_live(stations(4), 1)
        self.tick()
        self.c.drop_stations({"https://r1.example/s", "https://r3.example/s"})
        self.assertEqual([t.title for t in self.c.items], ["Station 0", "Station 1", "Station 2"])
        self.assertEqual((self.c.track.title, self.c.state), ("Station 1", "playing"))
        self.c.next()
        self.tick()
        self.assertEqual([t.title for t in self.c.items], ["Station 0", "Station 2"])
        self.assertEqual(self.c.track.title, "Station 2")
        self.assertEqual(self.loads()[-1], ["https://r2.example/s", "replace"])

    def test_pause_then_play_opens_again(self):
        self.c.play_live(stations(1), 0)
        self.tick()
        n = len(self.loads())
        self.c.pause()
        self.c.resume()
        self.tick()
        self.assertEqual(len(self.loads()), n + 1)
        self.assertEqual(self.c.state, "playing")


class TestThemeOwner(Base):
    def test_theme_waits_for_music(self):
        self.start()
        n = len(self.m.cmds)
        self.a.play("http://s/theme", {}, 50)
        self.a.stop()
        self.assertEqual(len(self.m.cmds), n)        # music owns the player
        self.c.stop()
        self.a.play("http://s/theme", {}, 50)
        self.assertEqual(self.loads()[-1], ["http://s/theme", "replace"])

    def test_shutdown_while_starting_kills_it(self):
        held = []
        a = audio.AudioPlayer("mpv", run=lambda fn, d, f: held.append((fn, d, f)),
                              make_mpv=lambda: self.mpvs.append(FakeMpv()) or self.mpvs[-1])
        a.music_open(PlayInfo(url="http://s/x", headers={}))
        killed = []
        self.mpvs[-1].kill = lambda wait=0: killed.append(1)
        a.shutdown()
        self.assertEqual(killed, [1])


if __name__ == "__main__":
    unittest.main()


class TestSleepEnd(Base):
    def test_end_of_track_drops_the_next_entry_and_ends(self):
        self.start(0)
        self.assertEqual(self.loads()[-1], ["http://s/t1", "append"])
        on = [True]
        self.c.sleep_end = lambda: on[0]
        self.c.sleep_changed()
        self.ev("end-file", reason="eof")
        self.assertEqual(self.c.state, "idle")


class TestLateNext(Base):
    def test_eof_with_a_next_entry_that_mpv_never_plays_opens_it(self):
        self.start(0)
        self.assertEqual(self.loads()[-1], ["http://s/t1", "append"])
        self.ev("end-file", reason="eof")             # mpv went idle; no playlist-pos 1
        self.assertEqual(self.c.index, 0)
        self.clk.t += music.ADVANCE_WAIT_S + 0.1
        self.tick()
        self.assertEqual(self.c.index, 1)
        self.assertEqual(self.loads()[-2], ["http://s/t1", "replace"])

    def test_normal_gapless_change_is_not_opened_twice(self):
        self.start(0)
        self.ev("end-file", reason="eof")
        self.prop("playlist-pos", 1)
        n = len(self.loads())
        self.clk.t += music.ADVANCE_WAIT_S + 0.1
        self.tick()
        self.assertEqual(self.c.index, 1)
        self.assertFalse(any(l == ["http://s/t1", "replace"] for l in self.loads()[n:]))

    def test_pause_in_the_window_holds_and_play_goes_on(self):
        self.start(0)
        self.ev("end-file", reason="eof")
        self.c.pause()
        self.clk.t += music.ADVANCE_WAIT_S + 0.1
        self.tick()
        self.assertEqual((self.c.index, self.c.state), (0, "paused"))
        self.c.resume()
        self.tick()
        self.assertEqual(self.c.index, 1)
        self.assertEqual(self.loads()[-2], ["http://s/t1", "replace"])

    def test_prev_in_the_window_opens_the_track_again(self):
        self.start(1)
        self.c.pos_s = 50
        self.ev("end-file", reason="eof")
        self.c.prev()
        self.tick()
        self.assertEqual(self.c.index, 1)
        self.assertEqual(self.loads()[-2], ["http://s/t1", "replace"])
