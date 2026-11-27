import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ["BATOPLEX_HOME"] = tempfile.mkdtemp()

from batoplex import inputs, playback, tracks  # noqa: E402
from batoplex.model import Item, PlayInfo  # noqa: E402
from fake_mpv import FakeMpv  # noqa: E402

class StrictSettings(dict):
    """Like config.Settings: item access only (no .get), so tests catch .get() calls."""
    get = property(lambda self: (_ for _ in ()).throw(AttributeError("Settings has no get")))


SETTINGS = StrictSettings({"mpv": "mpv", "safe": {"left": 40, "right": 40, "top": 36, "bottom": 36},
                           "sub_scale": 1.0, "mpv_extra": [], "flicker_filter": "normal",
                           "rewind_on_resume_s": 0, "auto_play": True, "up_next": "credits",
                           "autoplay_countdown_s": 15, "passout_protection_h": 2,
                           "skip_intro": "manual", "skip_credits": "manual"})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Provider:
    def __init__(self, pi=None, fail=None):
        self.pi = pi
        self.fail = fail
        self.calls = []

    def play_info(self, item, resume=True):
        if self.fail:
            raise self.fail
        pi = self.pi or PlayInfo(url="http://x/file.mkv", headers={"X-Plex-Token": "t"},
                                 start_s=20.0 if resume else 0.0, duration_ms=60000, title="T")
        return pi, item

    def timeline(self, item, state, t_ms, d_ms, sess):
        self.calls.append(("timeline", state, t_ms))

    def scrobble(self, item):
        self.calls.append(("scrobble",))

    def set_streams(self, part_id, audio_id=None, sub_id=None):
        self.calls.append(("streams", part_id, audio_id, sub_id))

    def streams(self):
        return [c[1:] for c in self.calls if c[0] == "streams"]

    def states(self):
        return [c[1] if c[0] == "timeline" else c[0] for c in self.calls]


class Runner:
    """Deferred worker: jobs run when the test says so (sync=True runs at once)."""
    def __init__(self, sync=True):
        self.sync = sync
        self.jobs = []

    def __call__(self, fn, done, fail):
        if self.sync:
            self._run(fn, done, fail)
        else:
            self.jobs.append((fn, done, fail))

    @staticmethod
    def _run(fn, done, fail):
        try:
            r = fn()
        except Exception as e:
            fail(e)
            return
        done(r)

    def run_all(self):
        while self.jobs:
            self._run(*self.jobs.pop(0))


class FakeOverlay:
    text_mode = False

    def __init__(self):
        self.shown = {}
        self.log = []

    def show(self, cid, x, y, w, h, draw_fn, sig=None):
        self.shown[cid] = sig
        self.log.append(("show", cid, sig))

    def hide(self, cid):
        if cid in self.shown:
            self.log.append(("hide", cid))
        self.shown.pop(cid, None)

    def cleanup(self):
        self.log.append(("cleanup",))


class Base(unittest.TestCase):
    def make(self, sync=True, provider=None, memory=None):
        self.clock = Clock()
        self.runner = Runner(sync)
        self.mpvs = []

        def mk():
            m = FakeMpv()
            self.mpvs.append(m)
            return m
        self.overlays = []

        def mko(cmd, settings):
            o = FakeOverlay()
            self.overlays.append(o)
            return o
        self.held = set()
        self.c = playback.PlaybackController(SETTINGS, clock=self.clock, run=self.runner,
                                             make_mpv=mk, memory=memory, make_overlay=mko)
        self.c.is_held = lambda a: a in self.held
        self.p = provider or Provider()
        self.item = Item("1", "movie", "Movie", duration_ms=60000)
        return self.c

    def start(self):
        self.c.play(self.item, self.p)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 20.0)
        self.c.tick()
        return m


class TestStart(Base):
    def test_play_to_playing(self):
        self.make()
        self.c.play(self.item, self.p)
        self.assertEqual(self.c.session, playback.STARTING)
        self.c.tick()
        m = self.mpvs[-1]
        self.assertIn(["loadfile", "http://x/file.mkv", "replace"], m.cmds)
        self.assertIn(["set_property", "start", "20.0"], m.cmds)
        hdr = [c for c in m.cmds if c[:2] == ["set_property", "http-header-fields"]]
        self.assertEqual(hdr[0][2], "X-Plex-Token: t")
        m.fire("file-loaded")
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_b_cancels_start_at_once(self):
        self.make(sync=False)
        self.c.play(self.item, self.p)
        self.c.action(inputs.BACK)
        self.assertEqual(self.c.session, playback.IDLE)
        r = self.c.pop_result()
        self.assertTrue(r["user_stop"] and r["never_loaded"])
        # the worker finishes late: its mpv is killed, nothing else changes
        self.runner.run_all()
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)
        self.assertIsNone(self.c.pop_result())

    def test_cancel_then_restart_old_worker_late(self):
        self.make(sync=False)
        self.c.play(self.item, self.p)
        self.c.action(inputs.BACK)
        self.c.pop_result()
        other = Item("2", "movie", "Other", duration_ms=60000)
        self.c.play(other, self.p)
        gen2 = self.c.s
        # run the second start first, then the first (late) one
        new_job = self.runner.jobs.pop(1)
        Runner._run(*new_job)
        self.c.tick()
        new_mpv = gen2.mpv
        self.runner.run_all()            # cancelled start: raises Cancelled in work()
        self.c.tick()
        self.assertIs(self.c.s, gen2)
        self.assertEqual(self.c.session, playback.STARTING)
        self.assertTrue(new_mpv.alive)
        self.assertEqual(len(self.mpvs), 1)   # the cancelled start never made an mpv

    def test_unconnected_mpv_is_not_seen_as_dead(self):
        # The worker has made the process but not connected yet: the UI must wait.
        self.make(sync=False)
        self.c.play(self.item, self.p)
        fn, done, fail = self.runner.jobs.pop(0)
        m = fn()
        m.alive_ = False
        self.c.tick()
        self.assertEqual(self.c.session, playback.STARTING)
        m.alive_ = True
        done(m)
        self.c.tick()
        m.fire("file-loaded")
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_keyboard_through_script_message(self):
        self.make()
        m = self.start()
        m.fire("client-message", args=["bp", "right"])
        self.c.tick()
        self.assertIn(["seek", 30], m.cmds)
        m.fire("client-message", args=["bp", "back"])
        m.fire("client-message", args=["bp", "back"])
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)

    def test_actions_in_starting_are_not_seeks(self):
        self.make(sync=False)
        self.c.play(self.item, self.p)
        for a in (inputs.RIGHT, inputs.RIGHT, inputs.OK):
            self.c.action(a)
        self.runner.run_all()
        self.c.tick()
        self.assertNotIn("seek", self.mpvs[-1].names())

    def test_start_timeout_errors_and_closes_mpv(self):
        self.make()
        self.c.play(self.item, self.p)
        self.c.tick()
        self.clock.t += playback.START_TIMEOUT_S + 1
        self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)
        self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)
        self.c.action(inputs.OK)
        self.assertEqual(self.c.session, playback.IDLE)
        self.assertTrue(self.c.pop_result()["error"])

    def test_load_error_retries_safe_mode_then_errors(self):
        self.make()
        self.c.play(self.item, self.p)
        self.c.tick()
        self.mpvs[-1].fire("end-file", reason="error", file_error="bad")
        self.c.tick()
        self.assertTrue(self.c.safe_mode)
        self.assertEqual(len(self.mpvs), 2)
        self.c.tick()
        self.assertIn("--vo=x11", self.mpvs[-1].started_with)
        self.mpvs[-1].fire("end-file", reason="error", file_error="bad")
        self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)
        self.assertFalse(self.mpvs[-1].alive)

    def test_safe_mode_ends_with_the_failed_item(self):
        self.make()
        self.c.play(self.item, self.p)
        self.c.tick()
        self.mpvs[-1].fire("end-file", reason="error", file_error="bad")
        self.c.tick()
        self.assertIn("--hwdec=no", self.mpvs[-1].started_with)    # the retry of the same item
        self.mpvs[-1].fire("file-loaded")
        self.c.tick()
        self.c.action(inputs.BACK)
        self.c.pop_result()
        self.c.play(Item("2", "movie", "Other", duration_ms=60000), self.p)
        self.c.tick()
        self.assertEqual(len(self.mpvs), 3)
        self.assertNotIn("--hwdec=no", self.mpvs[-1].started_with)

    def test_play_info_failure_is_an_error(self):
        self.make(provider=Provider(fail=RuntimeError("HTTP 404")))
        self.c.play(self.item, self.p)
        with self.assertLogs("batoplex.playback", "INFO") as logs:
            self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)
        # Plain copy on screen; the cause stays in the log and the result.
        self.assertEqual(self.c.message, playback.ERROR_COPY)
        self.assertTrue(any("404" in ln for ln in logs.output))
        self.c.action(inputs.OK)
        self.assertIn("404", self.c.pop_result()["error"])


class TestEnd(Base):
    def test_b_stops_and_reports_once(self):
        self.make()
        m = self.start()
        m.prop("time-pos", 30.0)
        self.c.tick()
        self.c.action(inputs.BACK)                              # stock Plex: B stops
        self.assertEqual(self.c.session, playback.IDLE)
        r = self.c.pop_result()
        self.assertEqual(r["position_ms"], 30000)
        self.assertFalse(r["watched"])
        self.assertEqual(self.p.states().count("stopped"), 1)
        self.assertNotIn("scrobble", self.p.states())
        self.assertFalse(m.alive)

    def test_eof_scrobbles(self):
        self.make()
        m = self.start()
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        r = self.c.pop_result()
        self.assertTrue(r["watched"])
        self.assertEqual(r["position_ms"], 0)
        self.assertEqual(self.p.states()[-2:], ["stopped", "scrobble"])

    def test_extra_sends_nothing_and_returns_no_files(self):
        """An extra (no_state) sends no timeline and no scrobble at play, stop
        or end, and the result changes no played mark."""
        for end in ("eof", "stop"):
            self.make()
            self.item.extra["no_state"] = True
            m = self.start()
            self.clock.t += 20
            m.prop("time-pos", 59.0)
            self.c.tick()
            if end == "eof":
                m.prop("eof-reached", True)
                self.c.tick()
            else:
                self.c.action(inputs.BACK)
            r = self.c.pop_result()
            self.assertEqual(self.p.calls, [], end)
            self.assertEqual(r["files"], [], end)

    def test_eof_and_end_file_finalize_once(self):
        self.make()
        m = self.start()
        m.prop("eof-reached", True)
        m.fire("end-file", reason="eof")
        self.c.tick()
        self.assertEqual(self.p.states().count("stopped"), 1)

    def test_mpv_gone_while_playing_saves_position(self):
        self.make()
        m = self.start()
        m.prop("time-pos", 42.0)
        m.fire("ipc-closed")
        self.c.tick()
        r = self.c.pop_result()
        self.assertEqual(r["position_ms"], 42000)
        self.assertIsNone(r["error"])

    def test_ninety_percent_counts_as_watched(self):
        self.make()
        m = self.start()
        m.prop("time-pos", 55.0)
        self.c.tick()
        self.c.action(inputs.STOP if hasattr(inputs, "STOP") else playback.STOP)
        self.assertTrue(self.c.pop_result()["watched"])

    def test_shutdown_reports_in_foreground(self):
        self.make()
        m = self.start()
        m.prop("time-pos", 33.0)
        self.c.tick()
        self.c.shutdown()
        self.assertEqual(self.p.calls[-1], ("timeline", "stopped", 33000))
        self.assertFalse(m.alive)


class TestQaRound1(Base):
    def test_late_worker_after_error_does_not_play(self):
        self.make(sync=False)
        self.c.play(self.item, self.p)
        self.clock.t += playback.START_TIMEOUT_S + 1
        self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)
        msg = self.c.message
        self.runner.run_all()                  # the slow worker finishes now
        self.c.tick()
        self.assertEqual(self.c.message, msg)
        for m in self.mpvs:
            self.assertNotIn("loadfile", m.names())
            self.assertFalse(m.alive)
        self.c.action(inputs.OK)
        self.assertEqual(self.c.session, playback.IDLE)

    def test_retry_uses_a_new_socket(self):
        self.make()
        self.c.play(self.item, self.p)
        self.c.tick()
        self.mpvs[-1].fire("end-file", reason="error", file_error="bad")
        self.c.tick()
        a, b = (m.sock_path for m in self.mpvs)
        self.assertNotEqual(a, b)

    def test_never_loaded_keeps_resume_point(self):
        self.make(provider=Provider(fail=RuntimeError("HTTP 500")))
        self.item.view_offset_ms = 123000
        self.c.play(self.item, self.p)
        self.c.tick()
        self.c.action(inputs.OK)
        self.assertEqual(self.c.pop_result()["position_ms"], 123000)

    def test_eof_far_from_end_is_not_watched(self):
        self.make()
        m = self.start()
        m.fire("end-file", reason="eof")      # stream dropped at 20 of 60 s
        self.c.tick()
        r = self.c.pop_result()
        self.assertFalse(r["watched"])
        self.assertEqual(r["position_ms"], 20000)

    def test_growing_mpv_duration_does_not_mark_watched(self):
        self.make()
        m = self.start()
        m.prop("duration", 21.0)               # a stream that reports what it has read
        m.prop("time-pos", 20.0)
        self.c.tick()
        self.c.action(inputs.BACK)
        self.c.action(inputs.BACK)
        r = self.c.pop_result()
        self.assertFalse(r["watched"])
        self.assertEqual(r["position_ms"], 20000)

    def test_no_periodic_report_after_final(self):
        self.make(sync=False)
        self.c.play(self.item, self.p)
        self.runner.run_all()
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 30.0)
        self.c.tick()                           # queues a 'playing' report
        self.c.action(inputs.BACK)
        self.c.action(inputs.BACK)              # queues the final report
        self.runner.jobs.reverse()              # the old report arrives last
        self.runner.run_all()
        self.assertEqual(self.p.states(), ["stopped"])

    def test_shutdown_waits_for_a_recent_final_report(self):
        import threading
        self.make(sync=False)
        m = self.start_async()
        self.c.action(inputs.BACK)
        self.c.action(inputs.BACK)
        jobs = list(self.runner.jobs)
        self.runner.jobs.clear()
        threading.Timer(0.2, lambda: [Runner._run(*j) for j in jobs]).start()
        self.c.shutdown()
        self.assertIn("stopped", self.p.states())

    def test_shutdown_drops_overlay(self):
        self.make()
        self.start()
        o = self.overlays[-1]
        self.c.shutdown()
        self.assertIn(("cleanup",), o.log)

    def start_async(self):
        self.c.play(self.item, self.p)
        self.runner.run_all()
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 30.0)
        self.c.tick()
        self.runner.jobs.clear()
        return m


class TestReports(Base):
    def test_periodic_and_pause_reports(self):
        self.make()
        m = self.start()
        n = self.p.states().count("playing")
        self.assertEqual(n, 1)
        self.clock.t += 5
        self.c.tick()
        self.assertEqual(self.p.states().count("playing"), 1)
        self.clock.t += 6
        self.c.tick()
        self.assertEqual(self.p.states().count("playing"), 2)
        m.prop("pause", True)
        self.c.tick()
        self.assertEqual(self.p.states()[-1], "paused")


class TestTracks(Base):
    def test_forced_subtitle_applied(self):
        part = {"Stream": [
            {"id": 1, "streamType": 2, "index": 1, "languageCode": "eng", "language": "English",
             "selected": True},
            {"id": 2, "streamType": 3, "index": 2, "languageCode": "eng", "language": "English"},
            {"id": 3, "streamType": 3, "index": 3, "languageCode": "eng", "language": "English",
             "forced": True},
        ]}
        pi = PlayInfo(url="u", headers={}, duration_ms=60000, tracks=tracks.parse(part))
        self.make(provider=Provider(pi))
        m = self.mpvs
        self.c.play(self.item, self.p)
        self.c.tick()
        mp = m[-1]
        mp.props["track-list"] = [
            {"id": 1, "type": "video", "ff-index": 0},
            {"id": 1, "type": "audio", "ff-index": 1},
            {"id": 1, "type": "sub", "ff-index": 2},
            {"id": 2, "type": "sub", "ff-index": 3},
        ]
        mp.fire("file-loaded")
        self.c.tick()
        self.c.tick()
        self.assertIn(["set_property", "sid", 2], mp.cmds)
        self.assertIn(["set_property", "aid", 1], mp.cmds)

    def test_memory_off_keeps_subs_off(self):
        part = {"Stream": [
            {"id": 1, "streamType": 2, "index": 1, "languageCode": "eng"},
            {"id": 3, "streamType": 3, "index": 2, "languageCode": "eng", "forced": True},
        ]}
        pi = PlayInfo(url="u", headers={}, duration_ms=60000, tracks=tracks.parse(part))
        mem = tracks.Memory(os.path.join(tempfile.mkdtemp(), "p.json"))
        ep = Item("9", "episode", "Ep", show_id="77", server_id="srv", duration_ms=60000)
        mem.set(mem.key(None, "srv", "77"), "sub", {"off": True})
        self.make(provider=Provider(pi), memory=mem)
        self.c.play(ep, self.p)
        self.c.tick()
        mp = self.mpvs[-1]
        mp.props["track-list"] = [{"id": 1, "type": "sub", "ff-index": 2}]
        mp.fire("file-loaded")
        self.c.tick()
        self.c.tick()
        self.assertIn(["set_property", "sid", "no"], mp.cmds)

    def ep_session(self, part, mem=None, track_list=()):
        pi = PlayInfo(url="u", headers={}, duration_ms=60000, tracks=tracks.parse(part), part_id=500)
        ep = Item("9", "episode", "Ep", show_id="77", server_id="srv", duration_ms=60000)
        self.make(provider=Provider(pi), memory=mem)
        self.c.play(ep, self.p)
        self.c.tick()
        mp = self.mpvs[-1]
        mp.props["track-list"] = list(track_list)
        mp.fire("file-loaded")
        mp.prop("time-pos", 5.0)
        self.c.tick()
        self.c.tick()
        return mp

    PART = {"Stream": [
        {"id": 1, "streamType": 2, "index": 1, "languageCode": "jpn", "selected": True},
        {"id": 2, "streamType": 2, "index": 2, "languageCode": "eng"},
        {"id": 5, "streamType": 3, "index": 3, "languageCode": "eng", "codec": "srt"},
    ]}

    def test_user_choice_writes_back_and_remembers(self):
        mem = tracks.Memory(os.path.join(tempfile.mkdtemp(), "p.json"))
        self.ep_session(self.PART, mem, [{"id": 1, "type": "sub", "ff-index": 3}])
        self.assertEqual(self.p.streams(), [])            # Plex selection used as is
        self.c.action(inputs.Y)
        self.assertEqual(self.p.streams(), [(500, None, 5)])
        self.c.action(inputs.Y)                            # back to None: sub id 0
        self.assertEqual(self.p.streams()[-1], (500, None, 0))
        self.c.action(inputs.X)
        self.assertEqual(self.p.streams()[-1], (500, 2, None))
        rec = mem.get(mem.key(None, "srv", "77"))
        self.assertEqual(rec["sub"], {"off": True})
        self.assertEqual(rec["audio"]["lang"], "eng")

    def test_memory_choice_at_start_is_written_back(self):
        mem = tracks.Memory(os.path.join(tempfile.mkdtemp(), "p.json"))
        mem.set(mem.key(None, "srv", "77"), "sub", {"off": False, "lang": "eng", "forced": False,
                                                     "sdh": False})
        mp = self.ep_session(self.PART, mem, [{"id": 1, "type": "sub", "ff-index": 3}])
        self.assertIn(["set_property", "sid", 1], mp.cmds)
        self.assertEqual(self.p.streams(), [(500, None, 5)])


class TestControls(Base):
    def bar(self):
        return self.overlays[-1].shown.get(0)

    def test_b_closes_the_bar_then_stops(self):
        self.make()
        self.start()
        self.c.action(inputs.DOWN)
        self.c.action(inputs.BACK)                    # closes the bar, keeps playing
        self.assertFalse(self.c.bar)
        self.assertEqual(self.c.session, playback.PLAYING)
        self.c.action(inputs.BACK)
        self.assertEqual(self.c.session, playback.IDLE)

    def test_a_opens_bar_on_pause_then_pauses(self):
        self.make()
        m = self.start()
        self.c.action(inputs.OK)                      # stock Plex: controls, Pause focused
        self.c.tick()
        self.assertNotIn(["set_property", "pause", True], m.cmds)
        self.assertEqual(self.bar()[0], "bar")
        self.assertEqual((self.c.bar_row, self.c.bar_btn), ("buttons", 0))
        self.c.action(inputs.OK)                      # second A pauses
        self.assertIn(["set_property", "pause", True], m.cmds)

    def test_rewind_on_resume_after_a_long_pause(self):
        self.make()
        m = self.start()
        self.c.settings = StrictSettings(dict(SETTINGS, rewind_on_resume_s=10))
        self.c.action(playback.PLAYPAUSE)               # pause
        self.clock.t += 3
        self.c.action(playback.PLAYPAUSE)               # short pause: no rewind
        self.assertNotIn(["seek", -10], m.cmds)
        self.c.action(playback.PLAYPAUSE)
        self.clock.t += 30
        self.c.action(playback.PLAYPAUSE)
        self.assertIn(["seek", -10], m.cmds)

    def test_bar_hides_after_5s_while_playing_not_paused(self):
        self.make()
        m = self.start()
        self.c.action(inputs.DOWN)                    # show bar, no pause
        self.assertNotIn(["set_property", "pause", True], m.cmds)
        self.clock.t += playback.BAR_HIDE_S + 0.1
        self.c.tick()
        self.assertIsNone(self.bar())
        self.c.action(inputs.OK)                      # bar on Pause
        self.c.action(inputs.OK)                      # pause
        m.prop("pause", True)
        self.clock.t += 60
        self.c.tick()
        self.assertEqual(self.bar()[0], "bar")

    def test_buttons_row(self):
        self.make()
        m = self.start()
        self.c.action(inputs.DOWN)
        self.c.action(inputs.DOWN)
        self.assertEqual(self.c.bar_row, "buttons")
        self.c.action(inputs.RIGHT)
        self.c.action(inputs.RIGHT)
        self.c.action(inputs.RIGHT)                   # stops at the last button
        self.assertEqual(self.c.bar_btn, 2)
        self.assertNotIn("seek", m.names())
        self.c.action(inputs.OK)                      # Stop
        self.assertEqual(self.c.session, playback.IDLE)

    def test_tap_seeks_at_once(self):
        self.make()
        m = self.start()
        self.c.action(inputs.RIGHT)
        self.c.tick()
        self.assertIn(["seek", 30], m.cmds)
        self.assertEqual(self.bar()[0], "mini")

    def test_hold_scrubs_and_seeks_once_on_release(self):
        self.make()
        m = self.start()                               # at 20 s of 60
        m.props["duration"] = 600
        m.prop("duration", 600.0)
        self.c.tick()
        self.held.add(inputs.RIGHT)
        self.c.action(inputs.RIGHT)                    # tap: +30 -> 50
        for _ in range(12):                            # 1.2 s held at 10 Hz
            self.clock.t += 0.1
            self.c.action(inputs.RIGHT, True)          # queue repeats are ignored
            self.c.tick()
        seeks = [c for c in m.cmds if c[0] == "seek"]
        self.assertEqual(seeks, [["seek", 30]])         # nothing sent while held
        self.held.clear()
        self.c.tick()
        seeks = [c for c in m.cmds if c[0] == "seek"]
        self.assertEqual(len(seeks), 2)
        self.assertEqual(seeks[1][2], "absolute")
        target = float(seeks[1][1])
        self.assertGreater(target, 50)
        self.assertEqual(target, self.c.time_s)

    def test_keyboard_repeats_scrub_then_commit(self):
        self.make()
        m = self.start()
        m.prop("duration", 600.0)
        self.c.tick()
        reports = len(self.p.calls)
        for _ in range(10):                    # 1 s of mpv key repeat (it sends 40 a second)
            for _ in range(4):
                m.fire("client-message", args=["bp", "right"])
            self.c.tick()
            self.clock.t += 0.1
        target = self.c.scrub["target"]
        # First step after HOLD_S, then one per SCRUB_EVERY_S, like the pad.
        self.assertEqual(target, 50.0 + 2 * playback.SCRUB_STEP_S)   # steps at 0.4 s and 0.7 s
        self.assertLessEqual(len(self.p.calls) - reports, 1)   # no report per repeat
        self.clock.t += playback.KB_COMMIT_S + 0.1
        self.c.tick()
        seeks = [c for c in m.cmds if c[0] == "seek"]
        self.assertEqual(seeks[0], ["seek", 30])
        self.assertEqual(seeks[-1][2], "absolute")
        self.assertEqual(len(seeks), 2)

    def test_chapter_ticks_and_names(self):
        self.make()
        m = self.start()
        m.prop("chapters", 3)
        m.prop("chapter", 0)
        m.prop("chapter-list", [{"time": 0.0, "title": "Opening"}, {"time": 20.0, "title": "Chapter 2"},
                                {"time": 40.0, "title": "The Heist"}])
        self.c.tick()
        self.assertEqual(self.c._bar_model(self.c.s)["chapters"], [20.0, 40.0])   # no tick at 0
        self.c.action(inputs.RT)
        self.assertEqual(self.c.toast[0], "Chapter 2")          # generic name: number only
        m.prop("chapter", 1)
        self.c.tick()
        self.c.action(inputs.RT)
        self.assertEqual(self.c.toast[0], "Chapter 3 • The Heist")

    def test_chapters_and_chapterless(self):
        self.make()
        m = self.start()
        self.c.action(inputs.RT)
        self.assertIn(["seek", playback.CHAPTERLESS_JUMP_S], m.cmds)
        m.prop("chapters", 5)
        m.prop("chapter", 1)
        self.c.tick()
        self.c.action(inputs.LT)
        self.assertIn(["add", "chapter", -1], m.cmds)
        self.assertEqual(self.c.toast[0], "Chapter 1")

    def test_y_cycles_subtitles_with_toast(self):
        part = {"Stream": [
            {"id": 1, "streamType": 2, "index": 1, "languageCode": "eng", "language": "English"},
            {"id": 2, "streamType": 3, "index": 2, "languageCode": "eng", "language": "English",
             "codec": "srt"},
        ]}
        pi = PlayInfo(url="u", headers={}, duration_ms=60000, tracks=tracks.parse(part))
        self.make(provider=Provider(pi))
        m = self.start()
        m.props["track-list"] = [{"id": 1, "type": "sub", "ff-index": 2}]
        self.c.tick()
        self.c.action(inputs.Y)
        self.c.tick()
        self.assertEqual(self.c.toast[0], "Subtitles: English (SRT)")
        self.assertIn(["set_property", "sid", 1], m.cmds)
        self.c.action(inputs.Y)
        self.c.tick()
        self.assertEqual(self.c.toast[0], "Subtitles: None")

    def test_start_card_until_loaded(self):
        self.make()
        self.c.play(self.item, self.p)
        self.c.tick()
        self.assertEqual(self.overlays[-1].shown.get(1)[0], "card")
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()
        self.assertNotIn(1, self.overlays[-1].shown)

    def test_media_keys(self):
        self.make()
        m = self.start()
        m.fire("client-message", args=["bp", "pause"])
        self.c.tick()
        self.assertTrue(self.c.paused)
        m.fire("client-message", args=["bp", "pause"])    # already paused: no toggle
        self.c.tick()
        self.assertEqual(m.cmds.count(["set_property", "pause", True]), 1)
        m.fire("client-message", args=["bp", "stop"])
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- Up Next

from batoplex.model import Marker  # noqa: E402


class EpProvider(Provider):
    """Two seasons: s1 has e1, e2; s2 has e3. Each episode has its own URL."""

    def __init__(self, markers=(), dur_ms=60000):
        super().__init__()
        self.markers = list(markers)
        self.dur_ms = dur_ms
        mk = lambda i, sid, si, n: Item(i, "episode", "Ep %s" % i, show_id="sh", season_id=sid,  # noqa: E731
                                        season_index=si, index=n, show_title="Show",
                                        duration_ms=dur_ms)
        self.eps = {"s1": [mk("e1", "s1", 1, 1), mk("e2", "s1", 1, 2)], "s2": [mk("e3", "s2", 2, 1)]}
        self.seas = [Item("s0", "season", "Specials", index=0), Item("s1", "season", "S1", index=1),
                     Item("s2", "season", "S2", index=2)]

    def episodes(self, sid):
        return list(self.eps[sid])

    def seasons(self, show_id):
        return list(self.seas)

    def play_info(self, item, resume=True):
        self.calls.append(("play_info", item.id))
        return PlayInfo(url="http://x/%s.mkv" % item.id, headers={}, duration_ms=self.dur_ms,
                        title=item.title, markers=list(self.markers)), item


class UpNextBase(Base):
    def go(self, provider=None, ep="e1"):
        self.make(provider=provider or EpProvider())
        self.item = self.p.eps["s1"][0] if ep == "e1" else self.p.eps["s1"][1]
        m = self.start()
        self.c.tick()                        # the queue lands
        return m

    def margins(self, m):
        return {c[1]: c[2] for c in m.cmds if c[0] == "set_property"
                and str(c[1]).startswith("video-margin-ratio-")}



class TestUpNext(UpNextBase):
    def test_build_queue_season_then_next_season(self):
        p = EpProvider()
        q = playback.build_queue(p, p.eps["s1"][1])
        self.assertEqual(([i.id for i in q.items], q.index), (["e1", "e2", "e3"], 1))
        q = playback.build_queue(p, p.eps["s1"][0])
        self.assertEqual([i.id for i in q.items], ["e1", "e2", "e3"])   # crosses the season
        self.make()
        self.assertEqual([i.id for i in playback.build_queue(p, self.item).items], ["1"])

    def test_eof_up_next_countdown_plays_next_in_same_mpv(self):
        m = self.go()
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertGreater(self.margins(m)["video-margin-ratio-right"], 0.3)
        self.assertEqual(self.overlays[-1].shown[1][0], "upnext")
        self.assertEqual(self.overlays[-1].shown[1][2], 15)       # "Starts in 15"
        self.clock.t += 15
        self.c.tick()
        self.assertEqual(self.c.session, playback.STARTING)
        self.assertEqual(len(self.mpvs), 1)                       # same mpv
        self.assertIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)
        self.assertEqual(self.margins(m)["video-margin-ratio-right"], 0)
        self.assertEqual(self.p.states().count("stopped"), 1)
        self.assertEqual(self.p.states().count("scrobble"), 1)
        m.fire("file-loaded")
        m.prop("time-pos", 10.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)
        self.c.action(inputs.BACK)
        r = self.c.pop_result()
        self.assertEqual(r["item_id"], "e2")
        self.assertEqual([(f["item_id"], f["watched"]) for f in r["files"]],
                         [("e1", True), ("e2", False)])

    def test_a_plays_now(self):
        m = self.go()
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)

    def test_b_in_up_next_stops(self):
        m = self.go()
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.c.action(inputs.BACK)
        self.assertEqual(self.c.session, playback.IDLE)
        r = self.c.pop_result()
        self.assertTrue(r["watched"])
        self.assertEqual([f["item_id"] for f in r["files"]], ["e1"])
        self.assertNotIn("http://x/e2.mkv", str(m.cmds))

    def test_auto_play_off_finishes_at_eof(self):
        self.make(provider=EpProvider())
        self.c.settings = StrictSettings(dict(SETTINGS, auto_play=False))
        self.item = self.p.eps["s1"][0]
        m = self.start()
        self.c.tick()
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)

    def test_last_episode_finishes(self):
        p = EpProvider()
        p.seas = p.seas[:2]                   # no season 2
        m = self.go(p, ep="e2")
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)

    def test_final_credits_shrink_and_back_to_video(self):
        m = self.go(EpProvider(markers=[Marker("credits", 50000, 58000, True)]))
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertFalse(self.c.eof)
        self.c.action(inputs.LEFT)                    # focus the small video
        self.c.tick()
        self.assertIn(4, self.overlays[-1].shown)     # focus frame
        self.c.action(inputs.OK)                      # back to full screen
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)
        self.assertEqual(self.margins(m)["video-margin-ratio-right"], 0)
        self.assertNotIn(4, self.overlays[-1].shown)
        m.prop("time-pos", 52.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)  # not again in these credits
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)  # but at the end of the file

    def test_up_next_setting_end_ignores_credits(self):
        self.make(provider=EpProvider(markers=[Marker("credits", 50000, 58000, True)]))
        self.c.settings = StrictSettings(dict(SETTINGS, up_next="end"))
        self.item = self.p.eps["s1"][0]
        m = self.start()
        self.c.tick()
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_passout_prompt(self):
        p = EpProvider(dur_ms=45 * 60 * 1000)
        m = self.go(p)
        self.clock.t += 3 * 3600                      # no input for 3 hours (2 h setting)
        m.prop("time-pos", 45 * 60 - 1.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertTrue(self.c.upnext["still"])
        self.clock.t += 60
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)     # no countdown
        self.c.action(inputs.OK)                                # Continue
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)

    def test_passout_no_answer_stops(self):
        p = EpProvider(dur_ms=45 * 60 * 1000)
        m = self.go(p)
        self.clock.t += 3 * 3600
        m.prop("time-pos", 45 * 60 - 1.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.clock.t += playback.PASSOUT_ANSWER_S + 1
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)

    def test_short_video_has_no_passout(self):
        m = self.go()
        self.clock.t += 3 * 3600
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertFalse(self.c.upnext["still"])


class TestQaRound2(UpNextBase):
    def eof_at(self, m, t):
        m.prop("time-pos", t)
        m.prop("eof-reached", True)
        self.c.tick()

    def test_mark_watched_does_not_crash(self):
        from batoplex.app import App
        app = App.__new__(App)
        from batoplex.sources import Sources
        app.stack = []
        app.sources = Sources([self.p if hasattr(self, "p") else Provider()])
        it = Item("1", "movie", "Movie", duration_ms=60000)
        import collections
        app.posted = collections.deque()
        App.mark_watched(app, it, True)          # was a TypeError that quit the app
        self.assertTrue(it.watched)

    def test_autoplay_crosses_season_from_first_episode(self):
        m = self.go()
        self.eof_at(m, 59.0)
        self.c.action(inputs.OK)
        self.c.tick()
        m.fire("start-file")
        m.fire("file-loaded")
        m.prop("time-pos", 5.0)
        self.c.tick()
        self.c.tick()
        self.eof_at(m, 59.0)                     # end of e2
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertEqual(self.c.upnext["item"].id, "e3")

    def test_stale_old_file_events_are_dropped(self):
        m = self.go(EpProvider(markers=[Marker("credits", 50000, 58000, True)]))
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.c.action(inputs.OK)                 # play e2 now
        self.c.tick()
        m.prop("time-pos", 55.0)                 # queued from the old file
        m.prop("duration", 3000.0)
        m.fire("start-file")
        m.fire("file-loaded")
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)   # no Up Next at once on e2
        self.assertEqual(self.c.duration_s, 60.0)
        self.assertLess(self.c.time_s, 50.0)

    def test_dropped_stream_does_not_autoplay(self):
        m = self.go()
        self.eof_at(m, 20.0)                     # eof at a third: the stream dropped
        self.assertEqual(self.c.session, playback.IDLE)
        self.assertNotIn("http://x/e2.mkv", str(m.cmds))

    def test_leaving_in_the_credits_counts_as_watched(self):
        m = self.go(EpProvider(markers=[Marker("credits", 45000, 58000, True)]))
        m.prop("time-pos", 46.0)                 # 77 %: credits start early
        self.c.tick()
        self.c.action(inputs.BACK)
        r = self.c.pop_result()
        self.assertTrue(r["watched"])

    def test_next_play_info_failure_in_credits_returns_to_video(self):
        p = EpProvider(markers=[Marker("credits", 30000, 58000, True)])
        real = p.play_info
        p.play_info = lambda item, resume=True: (_ for _ in ()).throw(RuntimeError("net")) \
            if item.id == "e2" else real(item, resume)
        m = self.go(p)
        m.prop("time-pos", 31.0)
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)
        self.assertTrue(m.alive)
        self.assertEqual(self.margins(m)["video-margin-ratio-right"], 0)

    def test_eof_under_video_focus_moves_focus_to_next(self):
        m = self.go(EpProvider(markers=[Marker("credits", 50000, 58000, True)]))
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.c.action(inputs.LEFT)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertEqual(self.c.upnext["focus"], "next")

    def test_lost_in_up_next_finishes(self):
        m = self.go()
        self.eof_at(m, 59.0)
        m.fire("shutdown")
        self.c.tick()
        self.assertEqual(self.c.session, playback.IDLE)

    def test_aid_reset_for_each_file(self):
        m = self.go()
        self.assertIn(["set_property", "aid", "auto"], m.cmds)


class TestQaRound3(UpNextBase):
    def test_mid_file_credits_marker_is_not_the_end(self):
        m = self.go(EpProvider(markers=[Marker("credits", 5000, 15000, False)]))
        m.prop("time-pos", 6.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)     # no Up Next at 10 %
        m.prop("time-pos", 20.0)
        self.c.tick()
        self.c.action(inputs.BACK)
        self.assertFalse(self.c.pop_result()["watched"])

    def test_late_non_final_credits_marker_still_counts(self):
        m = self.go(EpProvider(markers=[Marker("credits", 50000, 58000, False)]))
        m.prop("time-pos", 51.0)
        self.c.tick()
        self.assertEqual(self.c.session, playback.UP_NEXT)

    def test_seasons_failure_keeps_the_season_queue(self):
        p = EpProvider()
        p.seasons = lambda show_id: (_ for _ in ()).throw(RuntimeError("timeout"))
        q = playback.build_queue(p, p.eps["s1"][0])
        self.assertEqual([i.id for i in q.items], ["e1", "e2"])


class SkipBase(UpNextBase):
    INTRO = Marker("intro", 2000, 12000)

    def play_with(self, markers, **settings):
        p = EpProvider(markers=markers)
        self.make(provider=p)
        if settings:
            self.c.settings = StrictSettings(dict(SETTINGS, **settings))
        self.item = p.eps["s1"][0]
        m = self.start()
        self.c.tick()
        return m

    def to(self, m, t):
        m.prop("time-pos", t)
        self.c.tick()



class TestSkip(SkipBase):
    def test_manual_button_shows_8s_and_a_skips(self):
        m = self.play_with([self.INTRO])
        self.to(m, 1.5)
        self.to(m, 2.2)                                   # natural entry
        self.assertEqual(self.overlays[-1].shown.get(3)[1], "Skip Intro")
        self.c.action(inputs.OK)
        self.assertIn(["seek", "12.0", "absolute"], m.cmds)
        self.assertEqual(self.c.toast[0], "Skipped intro")
        self.assertFalse(self.c.bar)                      # A skipped; it did not open the bar

    def test_manual_button_hides_after_8s(self):
        m = self.play_with([self.INTRO])
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.clock.t += 8.5
        self.to(m, 10.0)
        self.assertNotIn(3, self.overlays[-1].shown)
        self.c.action(inputs.OK)                          # now A opens the bar
        self.assertTrue(self.c.bar)

    def test_auto_skips_on_natural_entry_only(self):
        m = self.play_with([self.INTRO], skip_intro="auto")
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.assertIn(["seek", "12.0", "absolute"], m.cmds)
        m.cmds.clear()
        self.to(m, 14.0)
        self.to(m, 5.0)                                   # seek back into the intro
        self.assertNotIn(["seek", "12.0", "absolute"], m.cmds)

    def test_disabled_shows_nothing(self):
        m = self.play_with([self.INTRO], skip_intro="disabled")
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.assertNotIn(3, self.overlays[-1].shown)

    def test_final_credits_belong_to_up_next(self):
        m = self.play_with([Marker("credits", 50000, 58000, True)])
        self.to(m, 49.5)
        self.to(m, 50.5)
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertNotIn(3, self.overlays[-1].shown)

    def test_skip_credits_without_a_next_item_goes_to_the_end(self):
        m = self.play_with([Marker("credits", 50000, 58000, True)], auto_play=False)
        self.to(m, 49.5)
        self.to(m, 50.5)
        self.assertEqual(self.overlays[-1].shown.get(3)[1], "Skip Credits")
        self.c.action(inputs.OK)
        self.assertIn(["seek", "59.5", "absolute"], m.cmds)


class PanelBase(Base):
    PART = {"Stream": [
        {"id": 1, "streamType": 2, "index": 1, "languageCode": "jpn", "language": "Japanese",
         "selected": True},
        {"id": 2, "streamType": 2, "index": 2, "languageCode": "eng", "language": "English"},
        {"id": 5, "streamType": 3, "index": 3, "languageCode": "eng", "language": "English",
         "codec": "srt"},
    ]}

    def open(self):
        pi = PlayInfo(url="u", headers={}, duration_ms=60000, tracks=tracks.parse(self.PART),
                      part_id=7)
        self.make(provider=Provider(pi))
        m = self.start()
        m.props["track-list"] = [{"id": 1, "type": "sub", "ff-index": 3},
                                 {"id": 1, "type": "audio", "ff-index": 1},
                                 {"id": 2, "type": "audio", "ff-index": 2}]
        self.c.action(inputs.DOWN)
        self.c.action(inputs.DOWN)              # buttons row
        self.c.action(inputs.RIGHT)             # Audio & Subtitles
        self.c.action(inputs.OK)
        self.c.tick()
        return m



class TestPanel(PanelBase):
    def test_panel_opens_on_subtitles_and_selects(self):
        m = self.open()
        sig = self.overlays[-1].shown.get(1)
        self.assertEqual(sig[0], "panel")
        self.assertFalse(self.c.bar)
        self.assertEqual(self.c.panel["tab"], "sub")
        self.c.action(inputs.DOWN)              # None -> English (SRT)
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertIsNone(self.c.panel)
        self.assertEqual(self.c.toast[0], "Subtitles: English (SRT)")
        self.assertIn(("streams", 7, None, 5), self.p.calls)
        self.assertNotIn(1, self.overlays[-1].shown)

    def test_tabs_and_audio_choice(self):
        self.open()
        self.c.action(inputs.RB)
        self.assertEqual(self.c.panel["tab"], "audio")
        self.assertEqual(self.c.panel["focus"], 0)          # Japanese is active
        self.c.action(inputs.DOWN)
        self.c.action(inputs.OK)
        self.assertIn(("streams", 7, 2, None), self.p.calls)

    def test_b_closes_panel_and_keeps_playing(self):
        self.open()
        self.c.action(inputs.BACK)
        self.c.tick()
        self.assertIsNone(self.c.panel)
        self.assertEqual(self.c.session, playback.PLAYING)
        self.assertNotIn(1, self.overlays[-1].shown)

    def test_panel_is_not_resent_each_frame(self):
        self.open()
        n = len(self.overlays[-1].log)
        for _ in range(5):
            self.c.tick()
        hides = [e for e in self.overlays[-1].log[n:] if e[0] == "hide" and e[1] == 1]
        self.assertEqual(hides, [])


class TestQaRound4(SkipBase):
    def test_tap_seek_into_intro_does_not_auto_skip(self):
        m = self.play_with([Marker("intro", 22000, 90000)], skip_intro="auto")
        self.to(m, 20.4)
        self.to(m, 20.6)
        self.c.action(inputs.RIGHT)               # tap +30 to 50.6
        self.c.tick()
        self.to(m, 50.6)                          # mpv reports the seek target
        self.assertNotIn(["seek", "90.0", "absolute"], m.cmds)
        self.assertEqual(self.overlays[-1].shown.get(3)[1], "Skip Intro")

    def test_resume_into_intro_shows_button_not_auto(self):
        m = self.play_with([Marker("intro", 1000, 60000)], skip_intro="auto")
        self.to(m, 30.0)                          # first position after a resume
        self.assertNotIn(["seek", "60.0", "absolute"], m.cmds)

    def test_fresh_start_in_intro_auto_skips(self):
        p = EpProvider(markers=[Marker("intro", 0, 30000)])
        self.make(provider=p)
        self.c.settings = StrictSettings(dict(SETTINGS, skip_intro="auto"))
        self.c.play(p.eps["s1"][0], self.p)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.to(m, 0.2)
        self.assertIn(["seek", "30.0", "absolute"], m.cmds)

    def test_no_flash_before_first_position(self):
        p = EpProvider(markers=[Marker("intro", 0, 30000)])
        self.make(provider=p)
        self.c.play(p.eps["s1"][0], self.p)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()                              # no time-pos yet: nothing decided
        self.assertNotIn(3, self.overlays[-1].shown)
        self.assertEqual(self.c.marker_seen, set())

    def test_button_returns_with_the_bar(self):
        m = self.play_with([Marker("intro", 2000, 60000)])
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.clock.t += 9
        self.to(m, 12.0)
        self.assertNotIn(3, self.overlays[-1].shown)
        self.c.action(inputs.UP)                   # open the controls
        self.to(m, 12.5)
        self.assertEqual(self.overlays[-1].shown.get(3)[1], "Skip Intro")

    def test_stale_button_cleared_in_next_marker(self):
        m = self.play_with([Marker("intro", 2000, 12000), Marker("credits", 14000, 30000)],
                           skip_credits="disabled")
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.to(m, 15.0)
        self.assertIsNone(self.c.skip)

    def test_skip_cancels_a_scrub(self):
        m = self.play_with([self.INTRO])
        self.to(m, 1.5)
        self.to(m, 2.2)
        self.c.scrub = {"dir": inputs.RIGHT, "src": "kb", "t0": self.clock.t, "next": 0,
                        "target": 40.0, "last": self.clock.t}
        self.c.action(inputs.OK)
        self.assertIsNone(self.c.scrub)

    def test_up_next_closes_panel_and_skip(self):
        m = self.play_with([Marker("credits", 50000, 58000, True)])
        self.c.panel = {"tab": "sub", "focus": 0, "top": 0}
        self.to(m, 49.5)
        self.to(m, 50.5)
        self.assertEqual(self.c.session, playback.UP_NEXT)
        self.assertIsNone(self.c.panel)


class TestQaRound4Panel(PanelBase):
    def test_held_remote_arrow_switches_tab_once(self):
        m = self.open()
        for _ in range(6):
            m.fire("client-message", args=["bp", "right"])
        self.c.tick()
        self.assertEqual(self.c.panel["tab"], "audio")

    def test_play_pause_key_works_under_panel(self):
        m = self.open()
        self.c.action(playback.PLAYPAUSE)
        self.assertIn(["set_property", "pause", True], m.cmds)
        self.assertIsNotNone(self.c.panel)

    def test_panel_hides_the_mini_bar(self):
        self.open()
        self.assertEqual(self.c.mini_until, 0.0)


class TestPlaybackMenu(UpNextBase):
    def test_start_opens_menu_and_next_episode_jumps(self):
        m = self.go()
        m.prop("time-pos", 20.0)
        self.c.tick()
        self.c.action(inputs.MENU)
        self.c.tick()
        rows = [k for k, _l in self.c.pmenu["rows"]]
        self.assertEqual(rows, ["subs", "audio", "aspect", "start", "next", "stop"])
        self.assertEqual(self.overlays[-1].shown.get(1)[0], "pmenu")
        for _ in range(4):
            self.c.action(inputs.DOWN)          # Next Episode
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)
        r_files = self.c.files
        self.assertEqual([(f["item_id"], f["watched"]) for f in r_files], [("e1", False)])
        self.assertEqual(self.p.states().count("stopped"), 1)

    def test_bar_has_prev_next_for_episodes(self):
        m = self.go()
        self.assertEqual([k for k, _l in self.c._bar_buttons()], ["pause", "next", "subs", "stop"])
        self.c.action(playback.NEXT)            # remote Next key: next episode
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)
        m.fire("start-file")
        m.fire("file-loaded")
        m.prop("time-pos", 3.0)
        self.c.tick()
        self.assertEqual([k for k, _l in self.c._bar_buttons()], ["pause", "prev", "next", "subs",
                                                                   "stop"])
        self.c.action(playback.PREV)
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e1.mkv", "replace"], m.cmds)

    def test_play_from_start_and_close(self):
        m = self.go()
        self.c.action(inputs.MENU)
        for _ in range(3):
            self.c.action(inputs.DOWN)          # Play from Start
        self.c.action(inputs.OK)
        self.assertIn(["seek", "0", "absolute"], m.cmds)
        self.assertIsNone(self.c.pmenu)
        self.c.action(inputs.MENU)
        self.c.action(inputs.BACK)
        self.assertIsNone(self.c.pmenu)
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_movie_menu_has_no_episode_rows_and_next_key_is_chapter(self):
        self.make()
        m = self.start()
        self.c.tick()
        self.c.action(inputs.MENU)
        self.assertEqual([k for k, _l in self.c.pmenu["rows"]], ["subs", "audio", "aspect", "start",
                                                               "stop"])
        self.c.action(inputs.BACK)
        self.c.action(playback.NEXT)
        self.assertIn(["seek", playback.CHAPTERLESS_JUMP_S], m.cmds)


class TestQaRound5(UpNextBase):
    def test_previous_from_a_mid_season_start(self):
        p = EpProvider()
        m = self.go(p, ep="e2")
        self.assertIn("prev", [k for k, _l in self.c._bar_buttons()])
        self.c.action(playback.PREV)
        self.c.tick()
        self.assertIn(["loadfile", "http://x/e1.mkv", "replace"], m.cmds)

    def test_previous_survives_the_queue_rebuild(self):
        m = self.go()
        for url in ("e2", "e3"):
            self.c.action(playback.NEXT)
            self.c.tick()
            self.assertIn(["loadfile", "http://x/%s.mkv" % url, "replace"], m.cmds)
            m.fire("start-file")
            m.fire("file-loaded")
            m.prop("time-pos", 3.0)
            self.c.tick()
            self.c.tick()
        self.assertEqual(self.c.queue.current.id, "e3")
        self.assertEqual(self.c.queue.prev.id, "e2")

    def test_jump_puts_the_subtitle_margin_back(self):
        m = self.go()
        self.c.action(inputs.DOWN)                 # bar open: subtitles raised
        self.c.action(playback.NEXT)
        self.c.tick()
        margins = [c[2] for c in m.cmds if c[:2] == ["set_property", "sub-margin-y"]]
        self.assertEqual(margins[-1], int(SETTINGS["safe"]["bottom"] * 1.5) + 12)

    def test_bar_focus_keeps_its_button_when_the_queue_lands(self):
        self.make(provider=EpProvider(), sync=False)
        p = self.p
        self.c.play(p.eps["s1"][0], p)
        self.runner.run_all()
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 2.0)
        self.c.tick()
        self.c.action(inputs.DOWN)
        self.c.action(inputs.DOWN)
        self.c.action(inputs.RIGHT)                # Audio & Subtitles (queue not here yet)
        self.runner.run_all()                       # the queue lands: Next appears
        self.c.tick()
        b = self.c._bar_buttons()
        self.assertEqual(b[self.c.bar_btn][0], "subs")

    def test_next_key_before_the_queue_does_not_seek(self):
        self.make(provider=EpProvider(), sync=False)
        p = self.p
        self.c.play(p.eps["s1"][0], p)
        self.runner.jobs[:1] and Runner._run(*self.runner.jobs.pop(0))
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()
        self.c.action(playback.NEXT)
        self.assertNotIn("seek", m.names())

    def test_play_from_start_resets_markers(self):
        m = self.go(EpProvider(markers=[Marker("intro", 2000, 12000)]))
        m.prop("time-pos", 1.5)
        self.c.tick()
        m.prop("time-pos", 2.2)
        self.c.tick()
        self.c.action(inputs.MENU)
        for _ in range(3):
            self.c.action(inputs.DOWN)
        self.c.action(inputs.OK)                   # Play from Start
        self.assertEqual(self.c.marker_seen, set())

    def test_skip_hidden_under_the_menu(self):
        m = self.go(EpProvider(markers=[Marker("intro", 2000, 12000)]))
        m.prop("time-pos", 1.5)
        self.c.tick()
        m.prop("time-pos", 2.2)
        self.c.tick()
        self.c.action(inputs.MENU)
        self.c.tick()
        self.assertNotIn(3, self.overlays[-1].shown)


class TestApplyFiles(unittest.TestCase):
    def test_episodes_screen_takes_results_from_above(self):
        from batoplex.screens.episodes import EpisodeScreen
        scr = EpisodeScreen.__new__(EpisodeScreen)
        e1 = Item("e1", "episode", "E1", duration_ms=60000)
        scr.eps, scr.seasons, scr.top, scr.focus, scr.s = {"s1": [e1]}, [], {}, {}, 0
        scr.apply_files([{"item_id": "e1", "position_ms": 0, "watched": True}], exact=True)
        self.assertTrue(e1.watched)
        scr.apply_files([{"item_id": "e1", "position_ms": 0, "watched": False}], exact=True)
        self.assertFalse(e1.watched)


class TestAspect(Base):
    def go(self, saved=None):
        mem = tracks.Memory(os.path.join(tempfile.mkdtemp(), "prefs.json"))
        if saved:
            mem.set(tracks.Memory.key(None, None, "item:1"), "aspect", saved)
        self.make(memory=mem)
        self.mem = mem
        m = self.start()
        m.prop("video-params", {"w": 640, "h": 480, "aspect": 4 / 3})
        self.c.tick()
        return m

    def to_row(self, key):
        self.c.action(inputs.MENU)
        while self.c.pmenu["rows"][self.c.pmenu["focus"]][0] != key:
            self.c.action(inputs.DOWN)

    def test_menu_changes_aspect_in_place_and_remembers_it(self):
        m = self.go()
        self.assertEqual(m.props["panscan"], 0.0)
        m.prop("video-params", {"w": 1920, "h": 1080, "aspect": 16 / 9})
        self.c.tick()
        self.to_row("aspect")
        self.c.action(inputs.RIGHT)
        self.assertIsNotNone(self.c.pmenu)               # stays open
        self.assertIn(("aspect", "Aspect: 14:9"), self.c.pmenu["rows"])
        self.assertAlmostEqual(m.props["panscan"], 0.4286, places=3)
        self.c.action(inputs.RIGHT)
        self.assertEqual(m.props["panscan"], 1.0)
        self.assertEqual(self.mem.get(tracks.Memory.key(None, None, "item:1"))["aspect"], "zoom")
        self.c.action(inputs.LEFT)
        self.c.action(inputs.LEFT)
        self.assertEqual(m.props["panscan"], 0.0)
        self.assertNotIn("vf", m.names())                # letterbox: no bar detection

    def test_zoom_cuts_bars_in_a_4x3_file(self):
        m = self.go(saved="zoom")
        self.assertEqual(self.c.aspect, "zoom")
        self.assertEqual(m.props["panscan"], 0.0)        # a 4:3 picture: nothing to fill yet
        m.props["vf-metadata/bpxcrop"] = {"lavfi.cropdetect.x1": "0", "lavfi.cropdetect.x2": "639",
                                          "lavfi.cropdetect.y1": "60", "lavfi.cropdetect.y2": "419"}
        m.prop("time-pos", 21.0)
        self.c.tick()                                    # the wait starts
        self.clock.t += 2.5
        self.c.tick()
        self.assertIn(["vf", "add", playback.aspect.DETECT_FILTER], m.cmds)
        self.c.tick()                                    # reply: run
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()                                    # reply: the box
        self.assertIn(["vf", "remove", "@bpxcrop"], m.cmds)
        self.assertEqual(m.props["video-crop"], "640x360+0+60")
        self.assertEqual(m.props["panscan"], 1.0)        # the 16:9 picture fills the tube
        self.assertIsNone(self.c.cropjob)

    def test_a_opens_the_aspect_list(self):
        m = self.go()
        m.prop("video-params", {"w": 1920, "h": 1080, "aspect": 16 / 9})
        self.c.tick()
        self.to_row("aspect")
        self.c.action(inputs.OK)
        self.assertEqual(self.c.pmenu["title"], "Aspect")
        self.assertEqual(self.c.pmenu["checked"], 0)       # Letterbox
        self.c.action(inputs.BACK)                         # back, on the Aspect row
        self.assertIsNone(self.c.pmenu.get("title"))
        self.assertEqual(self.c.pmenu["rows"][self.c.pmenu["focus"]][0], "aspect")
        self.c.action(inputs.OK)
        self.c.action(inputs.DOWN)
        self.c.action(inputs.DOWN)
        self.c.action(inputs.OK)
        self.assertIsNone(self.c.pmenu)
        self.assertEqual(self.c.aspect, "zoom")
        self.assertEqual(m.props["panscan"], 1.0)

    def detect(self, m):
        m.props["vf-metadata/bpxcrop"] = {"lavfi.cropdetect.x1": "0", "lavfi.cropdetect.x2": "639",
                                          "lavfi.cropdetect.y1": "60", "lavfi.cropdetect.y2": "419"}
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.clock.t += 2.5
        self.c.tick()
        self.c.tick()
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()

    def test_bars_are_kept_for_the_file(self):
        m = self.go(saved="zoom")
        self.detect(m)
        self.assertEqual(m.props["video-crop"], "640x360+0+60")
        adds = m.cmds.count(["vf", "add", playback.aspect.DETECT_FILTER])
        self.c.set_aspect("letterbox")
        self.assertEqual(m.props["video-crop"], "")
        self.assertEqual(m.props["panscan"], 0.0)
        self.c.set_aspect("zoom")                          # at once: no new look
        self.assertEqual(m.props["video-crop"], "640x360+0+60")
        self.assertEqual(m.props["panscan"], 1.0)
        self.assertIsNone(self.c.cropjob)
        # A quality change: the same file at a new size keeps the same bars.
        self.c._aspect_reset(self.c.s, m)
        self.assertEqual(m.props["video-crop"], "")
        m.prop("video-params", {"w": 320, "h": 240, "aspect": 4 / 3})
        self.c.tick()
        self.assertEqual(m.props["video-crop"], "320x180+0+30")
        self.assertIsNone(self.c.cropjob)
        self.assertEqual(m.cmds.count(["vf", "add", playback.aspect.DETECT_FILTER]), adds)

    def test_a_change_in_play_looks_without_the_start_wait(self):
        m = self.go()
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.c.set_aspect("zoom")                          # in the first seconds: wait
        self.c.tick()
        self.assertNotIn(["vf", "add", playback.aspect.DETECT_FILTER], m.cmds)
        self.c.set_aspect("letterbox")
        self.clock.t += 30
        self.c.set_aspect("zoom")                          # later: at once
        self.c.tick()
        self.assertIn(["vf", "add", playback.aspect.DETECT_FILTER], m.cmds)

    def test_empty_metadata_is_not_kept_as_no_bars(self):
        m = self.go(saved="zoom")
        m.props["vf-metadata/bpxcrop"] = {}
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.clock.t += 2.5
        self.c.tick()
        self.c.tick()
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()
        self.assertIsNone(self.c.bars)
        self.assertEqual(self.c.cropjob["state"], "wait")

    def test_dark_scene_tries_again_and_filter_error_stops(self):
        m = self.go(saved="14:9")
        m.props["vf-metadata/bpxcrop"] = {"lavfi.cropdetect.x1": "639", "lavfi.cropdetect.x2": "0",
                                          "lavfi.cropdetect.y1": "479", "lavfi.cropdetect.y2": "0"}
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.clock.t += 2.5
        self.c.tick()
        self.c.tick()
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.cropjob["state"], "wait")    # all black: later
        self.assertEqual(m.props["video-crop"], "")
        m.replies["vf"] = ("error", None)
        self.clock.t += playback.aspect.DETECT_RETRY_S
        self.c.tick()
        self.c.tick()
        self.assertIsNone(self.c.cropjob)                    # mpv refused the filter


class TestSleepTimer(UpNextBase):
    def setUp(self):
        from batoplex.sleep import SleepTimer
        self.SleepTimer = SleepTimer

    def test_end_of_episode_stops_instead_of_up_next(self):
        m = self.go()
        self.c.sleep = self.SleepTimer(clock=self.clock)
        self.c.action(inputs.MENU)
        while self.c.pmenu["rows"][self.c.pmenu["focus"]][0] != "sleep":
            self.c.action(inputs.DOWN)
        self.c.action(inputs.LEFT)                   # Off -> End of Episode
        self.assertIn(("sleep", "Sleep Timer: End of Episode"), self.c.pmenu["rows"])
        self.c.action(inputs.BACK)
        m.prop("time-pos", 59.0)
        m.prop("eof-reached", True)
        self.c.tick()
        self.assertNotEqual(self.c.session, playback.UP_NEXT)
        self.assertNotIn(["loadfile", "http://x/e2.mkv", "replace"], m.cmds)

    def test_minutes_in_menu_and_stop_now_keeps_progress(self):
        m = self.go()
        self.c.sleep = self.SleepTimer(clock=self.clock)
        self.c.action(inputs.MENU)
        while self.c.pmenu["rows"][self.c.pmenu["focus"]][0] != "sleep":
            self.c.action(inputs.DOWN)
        self.c.action(inputs.OK)
        self.c.action(inputs.RIGHT)
        self.assertIn(("sleep", "Sleep Timer: 30 min"), self.c.pmenu["rows"])
        self.assertIsNotNone(self.c.pmenu)
        self.c.stop_now()
        self.c.tick()
        self.assertIn("stopped", self.p.states())
        self.assertIsNone(self.c.pmenu)

    def test_night_audio_adds_the_filter(self):
        st = dict(SETTINGS, night_audio=True)
        self.assertIn("--af=" + playback.NIGHT_AF, playback.video_args("mpv", st, "/x", (0, 41)))
        self.assertFalse(any(a.startswith("--af") for a in
                             playback.video_args("mpv", SETTINGS, "/x", (0, 41))))


class TestQaAspect(TestAspect):
    def test_letterbox_cancels_a_waiting_detection(self):
        m = self.go(saved="zoom")
        self.assertIsNotNone(self.c.cropjob)
        self.c.set_aspect("letterbox")
        self.assertIsNone(self.c.cropjob)
        m.prop("time-pos", 21.0)
        self.clock.t += 10
        self.c.tick()
        self.assertNotIn("vf", m.names())

    def test_no_frame_yet_tries_again(self):
        m = self.go(saved="zoom")
        m.replies["get_property"] = ("property unavailable", None)
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.clock.t += 2.5
        self.c.tick()
        self.c.tick()
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.cropjob["state"], "wait")

    def test_menu_fits_the_safe_area(self):
        from batoplex import overlay
        safe = {"left": 40, "right": 40, "top": 36, "bottom": 56}
        n = overlay.menu_fit(safe)
        x, y, w, h = overlay.menu_rect(9, safe)
        self.assertGreaterEqual(y, safe["top"])
        self.assertLess(n, 9)


class TestQaSleep(UpNextBase):
    def test_crop_filter_is_removed_on_a_same_mpv_switch(self):
        self.make(provider=EpProvider())
        self.item = self.p.eps["s1"][0]
        self.c.settings = dict(SETTINGS, aspect="zoom")
        m = self.start()
        self.c.tick()
        m.prop("video-params", {"w": 640, "h": 480, "aspect": 4 / 3})
        m.prop("time-pos", 21.0)
        self.c.tick()
        self.clock.t += 2.5
        self.c.tick()
        self.assertIn(["vf", "add", playback.aspect.DETECT_FILTER], m.cmds)
        self.c.action(playback.NEXT)                   # next episode in the same mpv
        self.c.tick()
        self.assertIn(["vf", "remove", "@bpxcrop"], m.cmds)
        self.assertFalse(m.bpx_crop_on)


class TuneProvider(Provider):
    """Takes the tune offset (play_info opts at_s), as plex.PlexProvider does."""
    def transcode_choice(self, *a, **k):
        return None, ""

    def play_info(self, item, resume=True, opts=None):
        pi = PlayInfo(url="http://x/%s.mkv" % item.id, headers={}, duration_ms=600000, title="T",
                      start_s=float((opts or {}).get("at_s") or 0))
        return pi, item


class FakeTune:
    def __init__(self, cid, n, at):
        from types import SimpleNamespace
        self.cid, self.at, self.c, self.a_start = cid, at, 0, n * 600
        self.item = Item(str(n), "episode", "E%d" % n, duration_ms=600000)
        self.entry = SimpleNamespace(key="S/%d" % n)


class FakeSurfer:
    order_ = ["a", "b", "c"]

    def __init__(self):
        self.noted = []
        self.lineup = None

    def step(self, cur, d):
        return self.order_[(self.order_.index(cur) + d) % 3]

    def last(self, cur):
        return self.noted[-2] if len(self.noted) > 1 else None

    def note(self, cid):
        self.noted.append(cid)

    def tune(self, cid):
        return FakeTune(cid, 10 + self.order_.index(cid), 300)

    def next_tune(self, t, bad=(), behind=True):
        return FakeTune(t.cid, int(t.item.id) + 100, 0)

    def provider(self, t):
        return self.prov

    def banner(self, cid, t=None, **k):
        return None


class TestChannel(Base):
    """Live TV: tune offset, Up/LB change channel, end of item, no watch state."""

    def test_channel_session(self):
        self.make(provider=TuneProvider())
        sf = FakeSurfer()
        sf.prov = self.p
        self.c.play_channel(sf.tune("a"), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        self.assertIn(["set_property", "start", "300.0"], m.cmds)      # mid-programme
        m.fire("file-loaded")
        m.prop("time-pos", 301.0)
        self.c.tick()
        self.clock.t += 30
        self.c.tick()
        self.c.action(inputs.LEFT)                                    # no seek on a channel
        self.assertFalse(any(c[:1] == ["seek"] for c in m.cmds))
        self.c.action(inputs.UP)                                      # Up: the next number
        self.c.tick()
        self.assertEqual(self.c.s.channel.cid, "a")                   # waits 150 ms
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()                                                 # the play_info result
        self.assertEqual(self.c.s.channel.cid, "b")
        self.assertIn(["loadfile", "http://x/11.mkv", "replace"], m.cmds)
        m.fire("file-loaded")
        self.c.tick()
        self.c.action(inputs.LB)                                      # last channel
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.channel.cid, "a")
        m.fire("file-loaded")
        self.c.tick()
        m.prop("eof-reached", True)                                   # the next tape item at 0
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.item.id, "110")
        m.fire("file-loaded")
        self.c.tick()
        self.c.action(inputs.BACK)                                    # the banner goes
        self.clock.t += 10
        self.c.action(inputs.BACK)                                    # stop: the guide
        r = self.c.pop_result()
        self.assertEqual((r["files"], r["channel"], r["guide"]), ([], "a", False))
        self.assertEqual(self.p.calls, [])                            # no timeline, no scrobble
        self.assertIsNone(self.c.ch)


class S5(FakeSurfer):
    order_ = ["02", "03", "04", "05", "06"]

    def step(self, cur, d):
        return self.order_[(self.order_.index(cur) + d) % 5]


class TestChannelQa(Base):
    """Live TV fix round: skips in a row, fast presses, the load ladder, the start timeout, B."""

    def start(self, sf=None):
        self.make(provider=TuneProvider())
        sf = sf or FakeSurfer()
        sf.prov = self.p
        self.c.play_channel(sf.tune(sf.order_[0]), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()
        return m

    def test_skips_count_in_a_row(self):
        m = self.start()
        for _k in range(playback.CH_SKIPS + 1):
            m.fire("end-file", reason="error", file_error="bad file")
            self.c.tick()
            self.c.tick()
            m.fire("file-loaded")                                 # the next item plays
            self.c.tick()
        self.assertEqual(self.c.session, playback.PLAYING)

    def test_presses_step_from_the_tune_on_its_way(self):
        m = self.start(S5())
        self.runner.sync = False
        for _k in range(3):                                       # 300 ms apart
            self.c.action(inputs.UP)
            self.clock.t += 0.3
            self.c.tick()
        while self.runner.jobs:
            self.runner._run(*self.runner.jobs.pop(0))
            self.c.tick()
            m.fire("file-loaded")
            self.c.tick()
            self.clock.t += 0.2
            self.c.tick()
        self.assertEqual(self.c.s.channel.cid, "05")
        loads = [c[1] for c in m.cmds if c[0] == "loadfile"]
        self.assertEqual(loads, ["http://x/10.mkv", "http://x/13.mkv"])   # 03 never loads

    def test_failed_load_tries_safe_mode_first(self):
        self.start()
        self.c.action(inputs.UP)
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()                                             # channel b is loading
        n = len(self.mpvs)
        self.mpvs[-1].fire("start-file")
        self.mpvs[-1].fire("end-file", reason="error", file_error="bad file")
        self.c.tick()
        self.assertTrue(self.c.safe_mode)                         # the same item again
        self.assertEqual(len(self.mpvs), n + 1)
        self.assertEqual(self.c.s.item.id, "11")

    def test_start_timeout_skips_once(self):
        self.start()
        self.c.action(inputs.UP)
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()                                             # channel b never loads
        self.runner.sync = False
        self.clock.t += playback.START_TIMEOUT_S + 1
        for _k in range(5):
            self.c.tick()
        self.assertEqual((self.c.ch["skips"], len(self.runner.jobs)), (1, 1))

    def test_b_while_tuning_closes_the_banner_first(self):
        self.start()
        self.c.action(inputs.UP)
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.session, playback.STARTING)
        self.c.action(inputs.BACK)
        self.assertEqual(self.c.session, playback.STARTING)      # the banner goes
        self.c.action(inputs.BACK)
        self.assertNotEqual(self.c.session, playback.STARTING)   # the second B leaves


class _DownProvider(TuneProvider):
    fail_ids = {"11"}                          # channel b: the server drops

    def play_info(self, item, resume=True, opts=None):
        if item.id in self.fail_ids:
            raise RuntimeError("server down")
        return TuneProvider.play_info(self, item, resume, opts)


class TestChannelOwed(Base):
    def test_error_during_a_failed_change_still_skips(self):
        """The old item dies while a change is on its way, then the change fails: the
        old channel goes on with its next item (no dead picture)."""
        from types import SimpleNamespace as NS
        self.make(provider=_DownProvider())
        sf = FakeSurfer()
        sf.prov = self.p
        sf.lineup = NS(channel=lambda c: None)
        self.c.play_channel(sf.tune("a"), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()
        self.runner.sync = False
        self.c.action(inputs.UP)
        self.clock.t += 0.2
        self.c.tick()
        m.fire("end-file", reason="error", file_error="net drop")
        self.c.tick()
        while self.runner.jobs:
            self.runner._run(*self.runner.jobs.pop(0))
            self.c.tick()
        self.assertEqual(self.c.s.item.id, "110")
        self.assertIn("http://x/110.mkv", [c[1] for c in m.cmds if c[0] == "loadfile"])


class _CountProvider(TuneProvider):
    def __init__(self):
        TuneProvider.__init__(self)
        self.asked = []

    def play_info(self, item, resume=True, opts=None):
        self.asked.append(item.id)
        return TuneProvider.play_info(self, item, resume, opts)


class TestPrefetch(Base):
    """Player finish: play_info fetched ahead for the neighbours and the next
    item; a hit loads with no request."""

    def test_cache_hit_expiry_and_generation(self):
        clock = Clock()
        pf = playback.Prefetch(clock)
        prov, t = object(), FakeTune("a", 10, 300)
        pi = PlayInfo(url="http://x/10.mkv", headers={}, start_s=300.0)
        self.assertTrue(pf.wanted(t, prov, 0))
        pf.put(t, prov, 0, (pi, None))
        self.assertFalse(pf.wanted(t, prov, 0))
        t2 = FakeTune("a", 10, 312)                     # 12 s later: the offset of the tune
        self.assertEqual(pf.take(t2, prov, 0)[0].start_s, 312.0)
        self.assertEqual(pi.start_s, 300.0)             # the kept result is not changed
        self.assertIsNone(pf.take(t2, prov, 0))         # used once
        pf.put(t, prov, 0, (pi, None))
        self.assertIsNone(pf.take(t, prov, 1))          # another lineup version
        pf.put(t, prov, 0, (pi, None))
        self.assertIsNone(pf.take(t, object(), 0))      # another server
        pf.put(t, prov, 0, (pi, None))
        clock.t += playback.PREFETCH_TTL_S + 1
        self.assertIsNone(pf.take(t, prov, 0))          # too old
        tc = PlayInfo(url="http://x/t.m3u8", headers={}, mode="transcode")
        self.assertFalse(pf.put(t, prov, 0, (tc, None)))  # no transcode in advance
        pf.fail(t)
        self.assertFalse(pf.wanted(t, prov, 0))
        clock.t += playback.PREFETCH_RETRY_S
        self.assertTrue(pf.wanted(t, prov, 0))

    def start(self):
        self.make(provider=_CountProvider())
        sf = FakeSurfer()
        sf.prov = self.p
        self.c.play_channel(sf.tune("a"), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 301.0)
        self.c.tick()
        return m

    def test_neighbours_then_a_change_with_no_request(self):
        m = self.start()
        self.c.tick()
        self.assertEqual(self.p.asked, ["10"])          # nothing before PREFETCH_S
        self.clock.t += playback.PREFETCH_S
        for _k in range(3):
            self.c.tick()
            self.clock.t += 1.0
        self.assertEqual(self.p.asked, ["10", "11", "12"])   # Up first, then Down
        self.c.action(inputs.DOWN)
        self.clock.t += 0.2
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.channel.cid, "c")
        self.assertEqual(self.p.asked, ["10", "11", "12"])   # the change asked nothing
        self.assertIn(["loadfile", "http://x/12.mkv", "replace"], m.cmds)

    def test_next_item_fetched_early(self):
        m = self.start()
        m.prop("time-pos", 600 - playback.NEXT_EARLY_S - 5)
        self.c.tick()
        self.assertEqual(self.p.asked, ["10"])
        self.clock.t += 1.0
        m.prop("time-pos", 600 - playback.NEXT_EARLY_S + 1)
        self.c.tick()
        self.assertEqual(self.p.asked, ["10", "110"])
        m.prop("eof-reached", True)
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.item.id, "110")
        self.assertEqual(self.p.asked, ["10", "110"])
        self.assertIn(["loadfile", "http://x/110.mkv", "replace"], m.cmds)

    def test_result_after_the_session_ended_is_dropped(self):
        self.start()
        self.runner.sync = False
        self.clock.t += playback.PREFETCH_S
        self.c.tick()
        self.assertEqual(len(self.runner.jobs), 1)
        self.c.action(inputs.BACK)                      # the banner
        self.clock.t += 10
        self.c.action(inputs.BACK)                      # stop
        self.runner.run_all()
        self.c.tick()
        self.assertIsNone(self.c.ch)



class _FakeGuide:
    def __init__(self, cid):
        self.cid, self.got = cid, []

    def handle(self, a):
        self.got.append(a)
        return {inputs.OK: ("tune", "c"), inputs.BACK: ("close",), inputs.RB: ("close",)}.get(a)

    def update(self):
        pass

    def top_y(self):
        return 152

    def sig(self):
        return ("guide", tuple(self.got))

    def draw_over(self, surf):
        pass


class TestPictureGuide(Base):
    """The guide over the picture: RB opens it and the picture plays on; it has the presses;
    A tunes at once; B closes it and the next B works on the picture again."""

    def test_routing(self):
        from batoplex import overlay
        self.make(provider=TuneProvider())
        sf = FakeSurfer()
        sf.prov = self.p
        made = []
        self.c.guide_view = lambda cid: made.append(_FakeGuide(cid)) or made[-1]
        self.c.play_channel(sf.tune("a"), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        self.c.tick()
        self.c.action(inputs.RB)
        self.assertEqual((self.c.session, made[0].cid), (playback.PLAYING, "a"))
        self.c.tick()
        self.assertIn(overlay.GUIDE, self.overlays[-1].shown)
        self.c.action(inputs.UP)                      # the guide moves, not the channel
        self.clock.t += 0.5
        self.c.tick()
        self.assertEqual((made[0].got, self.c.s.channel.cid), ([inputs.UP], "a"))
        self.c.action(inputs.OK)                      # tune channel c: no 150 ms wait
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.channel.cid, "c")
        self.assertIsNone(self.c.ch.get("gv"))
        m.fire("file-loaded")
        self.c.tick()
        self.assertNotIn(overlay.GUIDE, self.overlays[-1].shown)
        self.c.action(inputs.RB)
        self.c.action(inputs.BACK)                    # closes the guide only
        self.assertEqual(self.c.session, playback.PLAYING)
        self.assertIsNone(self.c.ch.get("gv"))


class _TcProvider(_CountProvider):
    def __init__(self):
        super().__init__()
        self.stops = 0

    def play_info(self, item, resume=True, opts=None):
        pi, it = super().play_info(item, resume, opts)
        pi.mode = "transcode"
        return pi, it

    def stop_transcode(self, s):
        self.stops += 1


class TestPrefetchTranscode(TestPrefetch):
    def test_a_transcoding_server_is_not_asked_every_second(self):
        """A transcode result is not kept; the next try waits PREFETCH_RETRY_S, and each
        decision is stopped (it was 61 requests a minute)."""
        self.make(provider=_TcProvider())
        sf = FakeSurfer()
        sf.prov = self.p
        self.c.play_channel(sf.tune("a"), self.p, sf)
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 301.0)
        self.c.tick()
        self.clock.t += playback.PREFETCH_S
        for _ in range(60):
            self.c.tick()
            self.clock.t += 1.0
        self.assertLessEqual(len(self.p.asked), 4)
        self.assertGreaterEqual(self.p.stops, len(self.p.asked) - 1)
