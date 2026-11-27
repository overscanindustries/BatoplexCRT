"""Transcode in the playback controller."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import inputs, playback, plex  # noqa: E402
from batoplex.model import Item, PlayInfo  # noqa: E402
from test_playback import SETTINGS, Base  # noqa: E402

TC_SETTINGS = type(SETTINGS)(dict(SETTINGS, quality_home="original", quality_remote="auto"))


class TcProvider:
    """Answers like PlexProvider.play_info: opts decide the mode."""

    def __init__(self, auto_mode="direct", conn="remote", refuse=False):
        self.auto_mode, self.conn, self.refuse = auto_mode, conn, refuse
        self.calls = []

    def transcode_choice(self, *a, **k):
        return None, ""

    def play_info(self, item, resume=True, opts=None):
        opts = opts or {}
        self.calls.append(("play_info", item.id, dict(opts)))
        mode = opts.get("mode") or self.auto_mode
        pi = PlayInfo(url="http://x/%s.mkv" % item.id, headers={"X-Plex-Token": "t"},
                      start_s=20.0, duration_ms=600000, title=item.title, part_id=7,
                      src_aspect=16 / 9.0)
        pi.conn = self.conn
        if mode == "transcode":
            if self.refuse:
                e = plex.TranscodeRefused("Den can't convert this video.", 2001, "x")
                e.screen = True                  # as plex.py raises it (with a code)
                raise e
            sess = opts.get("session")
            pi.mode, pi.session = "transcode", sess
            pi.quality = opts.get("quality") or "1.5 Mbps 480p"
            pi.url_at = lambda t, i=item.id, s=sess: "http://x/tc/%s.m3u8?offset=%d&s=%s" % (i, t, s)
            pi.offset_s = 20.0
            pi.url = pi.url_at(20.0)
        return pi, item

    def timeline(self, item, state, t_ms, d_ms, sess):
        self.calls.append(("timeline", state, t_ms, sess))

    def scrobble(self, item):
        self.calls.append(("scrobble",))

    def set_streams(self, part_id, audio_id=None, sub_id=None):
        self.calls.append(("streams", part_id, audio_id, sub_id))

    def stop_transcode(self, sess):
        self.calls.append(("stop", sess))

    def ping_transcode(self, sess):
        self.calls.append(("ping", sess))

    def names(self):
        return [c[0] if c[0] != "timeline" else c[1] for c in self.calls]


class TcBase(Base):
    def make(self, sync=True, provider=None, memory=None):
        c = super().make(sync, provider, memory)
        c.settings = TC_SETTINGS
        return c

    def loads(self, m):
        return [c[1] for c in m.cmds if c[0] == "loadfile"]


class TestTimeBase(TcBase):
    def test_offset_is_added_and_mpv_duration_ignored(self):
        self.make(provider=TcProvider(auto_mode="transcode"))
        m = self.start()
        self.assertIn(["set_property", "start", "none"], m.cmds)
        self.assertEqual(self.c.time_s, 40.0)              # 20 from mpv + 20 offset
        m.prop("duration", 30.0)                           # the stream from the offset
        self.c.tick()
        self.assertEqual(self.c.duration_s, 600.0)

    def test_session_id_is_the_timeline_session(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.start()
        sess = self.c.s.plex_session
        self.assertEqual(p.calls[0][2]["session"], sess)


class TestSourceAspect(TcBase):
    def test_transcode_gets_the_source_shape(self):
        self.make(provider=TcProvider(auto_mode="transcode"))
        m = self.start()
        self.assertEqual(m.props["video-aspect-override"], "1.777778")

    def test_direct_play_uses_the_file_aspect(self):
        self.make(provider=TcProvider(auto_mode="direct"))
        m = self.start()
        self.assertEqual(m.props["video-aspect-override"], "-1")


class TestSeek(TcBase):
    def test_taps_add_up_to_one_restart(self):
        self.make(provider=TcProvider(auto_mode="transcode"))
        m = self.start()
        n = len(self.loads(m))
        for k in range(3):
            self.c.action(inputs.RIGHT)
            m.prop("time-pos", 20.5 + k)                 # the old stream still plays
            self.c.tick()
            self.assertEqual(self.c.time_s, 40 + (k + 1) * playback.SEEK_FWD_S)
        self.assertEqual(len(self.loads(m)), n)            # still waiting for taps
        self.assertFalse([c for c in m.cmds if c[0] == "seek"])
        self.clock.t += 0.7
        self.c.tick()
        self.assertEqual(len(self.loads(m)), n + 1)
        self.assertIn("offset=%d" % (40 + 3 * playback.SEEK_FWD_S), self.loads(m)[-1])
        m.fire("start-file")
        m.prop("time-pos", 0.5)
        self.c.tick()
        self.assertAlmostEqual(self.c.time_s, 40 + 3 * playback.SEEK_FWD_S + 0.5)

    def test_play_from_start_restarts_at_once(self):
        self.make(provider=TcProvider(auto_mode="transcode"))
        m = self.start()
        self.c.action(inputs.MENU)
        rows = [r[0] for r in self.c.pmenu["rows"]]
        self.c.pmenu["focus"] = rows.index("start")
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertIn("offset=0", self.loads(m)[-1])

    def test_direct_seek_is_unchanged(self):
        self.make(provider=TcProvider())
        m = self.start()
        self.c.action(inputs.RIGHT)
        self.assertIn(["seek", playback.SEEK_FWD_S], m.cmds)


class TestQualityAndTracks(TcBase):
    def test_quality_row_switches_at_the_position(self):
        p = TcProvider()
        self.make(provider=p)
        m = self.start()
        self.c.action(inputs.MENU)
        rows = self.c.pmenu["rows"]
        self.assertIn(("quality", "Quality: Original"), rows)
        self.c.pmenu["focus"] = [r[0] for r in rows].index("quality")
        self.c.action(inputs.OK)
        labels = [lbl for _k, lbl in self.c.pmenu["rows"]]
        self.assertEqual(labels, ["Original", "8 Mbps 480p", "4 Mbps 480p", "2 Mbps 480p",
                                  "1.5 Mbps 480p", "720 kbps"])
        self.assertEqual((self.c.pmenu["checked"], self.c.pmenu["focus"]), (0, 0))
        self.c.pmenu["focus"] = labels.index("4 Mbps 480p")
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertEqual((self.c.s.pi.mode, self.c.s.pi.quality), ("transcode", "4 Mbps 480p"))
        self.assertEqual(self.c.toast[0], "Quality: 4 Mbps 480p")
        self.assertIn("offset=20", self.loads(m)[-1])
        last = [c for c in p.calls if c[0] == "play_info"][-1]
        self.assertEqual((last[2]["mode"], last[2]["quality"]), ("transcode", "4 Mbps 480p"))
        # back to Original: the transcode stops first, direct play starts at the position
        m.fire("start-file")
        m.prop("time-pos", 10.0)
        self.c.tick()
        self.c.action(inputs.MENU)
        self.c.pmenu["focus"] = [r[0] for r in self.c.pmenu["rows"]].index("quality")
        self.c.action(inputs.OK)
        self.assertEqual(self.c.pmenu["checked"], 2)          # 4 Mbps is checked
        self.c.action(inputs.BACK)                            # back to the playback menu
        self.assertEqual(self.c.pmenu["rows"][self.c.pmenu["focus"]][0], "quality")
        self.c.action(inputs.OK)
        self.c.pmenu["focus"] = 0
        self.c.action(inputs.OK)
        self.c.tick()
        self.assertEqual(self.c.s.pi.mode, "direct")
        self.assertIn("stop", p.names())
        self.assertIn(["set_property", "start", "30.0"], m.cmds)

    def test_track_change_puts_then_restarts(self):
        from batoplex.model import Track
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        m = self.start()
        t = Track(kind=3, id=55, index=2, language_code="en", codec="srt")
        self.c.s.pi.tracks = [t]
        n = len(self.loads(m))
        self.c.choose_track("sub", t)
        self.c.tick()
        names = p.names()
        self.assertLess(names.index("streams"), len(names) - 1)
        self.assertEqual(names[names.index("streams") + 1], "play_info")   # PUT first
        self.assertEqual(len(self.loads(m)), n + 1)
        self.assertFalse([c for c in m.cmds if c[:2] == ["set_property", "sid"]])


class TestRules(TcBase):
    def test_rule5_third_try_converts(self):
        p = TcProvider()
        self.make(provider=p)
        self.c.play(self.item, p)
        self.c.tick()
        self.mpvs[-1].fire("end-file", reason="error", file_error="unsupported")
        self.c.tick()                                     # safe mode
        self.mpvs[-1].fire("end-file", reason="error", file_error="unsupported")
        self.c.tick()                                     # conversion
        self.assertEqual(p.calls[-1][2].get("mode"), "transcode")
        self.assertTrue(p.calls[-1][2].get("retry"))
        self.assertEqual(self.c.session, playback.STARTING)
        self.mpvs[-1].fire("end-file", reason="error", file_error="unsupported")
        self.c.tick()
        self.assertEqual(self.c.session, playback.ERROR)

    def test_rule5_refused_is_an_error_card(self):
        p = TcProvider(refuse=True)
        self.make(provider=p)
        self.c.play(self.item, p)
        self.c.tick()
        for _ in range(2):
            self.mpvs[-1].fire("end-file", reason="error", file_error="x")
            self.c.tick()
        self.c.tick()                                     # the refusal lands
        self.assertEqual(self.c.session, playback.ERROR)
        self.assertEqual(self.c.message, "Den can't convert this video.")

    def test_rule6_buffering_switches_once(self):
        p = TcProvider()
        self.make(provider=p)
        m = self.start()
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += 5
        m.prop("paused-for-cache", False)
        self.c.tick()
        self.clock.t += 10
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += 3.5
        self.c.tick()
        self.c.tick()                                     # the new play info lands
        self.assertEqual(self.c.s.pi.mode, "transcode")
        self.assertEqual(self.c.toast[0], "Switched to 1.5 Mbps 480p")
        self.assertTrue(self.c.s.tc_tried)

    def stall(self, m, s=9):
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += s
        self.c.tick()
        self.c.tick()                                     # the new play info lands

    def test_rule6_steps_down_from_an_automatic_target(self):
        p = TcProvider(auto_mode="transcode")             # Automatic picked 1.5 Mbps
        self.make(provider=p)
        m = self.start()
        self.stall(m)
        self.assertEqual(self.c.s.pi.quality, "720 kbps")
        self.assertEqual(self.c.toast[0], "Switched to 720 kbps")
        n = len(p.calls)
        m.prop("paused-for-cache", False)
        m.fire("file-loaded")
        self.stall(m, 20)                                 # the lowest: it stays
        self.assertEqual([c for c in p.calls[n:] if c[0] == "play_info"], [])

    def test_rule6_from_original_uses_the_last_measurement(self):
        p = TcProvider()
        p.base = "https://remote.example:32400"
        plex._MEASURED[p.base] = (5000.0, 0.0)            # Automatic would pick 2 Mbps
        try:
            self.make(provider=p)
            m = self.start()
            self.stall(m)
        finally:
            plex._MEASURED.pop(p.base, None)
        self.assertEqual(self.c.s.pi.quality, "2 Mbps 480p")
        self.assertEqual(self.c.toast[0], "Switched to 2 Mbps 480p")

    def test_rule6_not_after_a_menu_choice(self):
        p = TcProvider()
        self.make(provider=p)
        m = self.start()
        self.c._restart(self.c.s, {"mode": "transcode", "quality": "4 Mbps 480p"})
        self.c.tick()
        self.stall(m, 20)
        self.assertEqual(self.c.s.pi.quality, "4 Mbps 480p")

    def test_rule6_waits_while_a_change_is_in_flight(self):
        p = TcProvider()
        self.make(provider=p)
        self.start()
        self.c.s.restart_busy = True
        self.assertIsNone(self.c._may_auto_switch(self.c.s))
        self.c.s.restart_busy, self.c.s.user_quality = False, True   # a menu choice, not landed
        self.assertIsNone(self.c._may_auto_switch(self.c.s))

    def test_step_down_never_above_the_file(self):
        p = TcProvider()
        p.base = "https://remote.example:32400"
        plex._MEASURED[p.base] = (5000.0, 0.0)            # Automatic would pick 2 Mbps
        try:
            self.assertEqual(playback._step_down("", p, 1000), "720 kbps")
            self.assertIsNone(playback._step_down("", p, 700))
            self.assertEqual(playback._step_down("", p, 0), "2 Mbps 480p")
        finally:
            plex._MEASURED.pop(p.base, None)

    def test_rule6_not_on_a_local_server(self):
        p = TcProvider(conn="local")
        self.make(provider=p)
        m = self.start()
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += 20
        self.c.tick()
        self.assertEqual(self.c.s.pi.mode, "direct")

    def test_rule6_window_forgets_old_stalls(self):
        p = TcProvider()
        self.make(provider=p)
        m = self.start()
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += 5
        m.prop("paused-for-cache", False)
        self.c.tick()
        self.clock.t += 70                                # out of the 60 s window
        m.prop("paused-for-cache", True)
        self.c.tick()
        self.clock.t += 4
        self.c.tick()
        self.c.tick()
        self.assertEqual(self.c.s.pi.mode, "direct")


class TestFixes(TcBase):
    def test_safe_retry_of_a_transcode_still_stops_at_the_end(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.c.play(self.item, p)
        self.c.tick()
        self.mpvs[-1].fire("end-file", reason="error", file_error="x")
        self.c.tick()
        self.c.tick()
        m = self.mpvs[-1]
        m.fire("file-loaded")
        m.prop("time-pos", 5.0)
        self.c.tick()
        before = p.names().count("stop")
        self.c.action(inputs.BACK)
        self.assertEqual(p.names()[-2:], ["stopped", "stop"])
        self.assertEqual(p.names().count("stop"), before + 1)

    def test_restart_while_paused_stays_paused(self):
        self.make(provider=TcProvider())
        m = self.start()
        m.prop("pause", True)
        self.c.tick()
        self.c._restart(self.c.s, {"mode": "transcode"})
        self.c.tick()
        self.assertEqual(self.c.s.pi.mode, "transcode")
        pauses = [c for c in m.cmds if c[:2] == ["set_property", "pause"]]
        self.assertEqual(pauses[-1][2], True)
        self.assertTrue(self.c.paused)

    def test_relay_track_change_keeps_720(self):
        from batoplex.model import Track
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.start()
        self.c.s.pi.quality, self.c.s.pi.reason = "720 kbps", "bandwidth"
        t = Track(id=55, kind="sub", index=2, language_code="eng", codec="srt")
        self.c.s.pi.tracks = [t]
        self.c.choose_track("sub", t)
        last = [c for c in p.calls if c[0] == "play_info"][-1][2]
        self.assertEqual((last["quality"], last["reason"]), ("720 kbps", "bandwidth"))

    def test_memory_that_matches_the_stream_does_not_restart(self):
        from batoplex import tracks as tr
        from batoplex.model import Track

        class Mem:
            def get(self, key):
                return {"sub": {"off": True}}

            def set(self, *a):
                pass
        p = TcProvider(auto_mode="transcode")
        orig = p.play_info

        def pinfo(item, resume=True, opts=None):
            pi, it = orig(item, resume, opts)
            pi.tracks = [Track(id=1, kind="audio", index=1, language_code="eng", selected=True),
                         Track(id=2, kind="sub", index=2, language_code="eng", codec="srt")]
            return pi, it
        p.play_info = pinfo
        self.make(provider=p, memory=Mem())
        ep = Item("e1", "episode", "Ep", show_id="sh", server_id="S", duration_ms=600000)
        self.item = ep
        self.start()
        self.c.tick()
        self.assertEqual([c[0] for c in p.calls].count("play_info"), 1)
        self.assertNotIn("streams", p.names())
        self.assertTrue(tr)


class TestStop(TcBase):
    def test_stopped_report_then_transcode_stop(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.start()
        self.c.action(inputs.BACK)
        names = p.names()
        self.assertEqual(names[-2:], ["stopped", "stop"])
        self.assertEqual(names.count("stop"), 1)

    def test_cancelled_start_stops_the_transcode(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.c.play(self.item, p)
        self.c.tick()
        self.c.action(inputs.BACK)
        self.assertEqual(p.names().count("stop"), 1)

    def test_up_next_stops_and_uses_its_own_session(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.start()
        s1 = self.c.s
        other = Item("2", "movie", "Two", duration_ms=600000)
        self.c._switch_to(s1, other, p.play_info(other, opts={"session": "NEXT"}), 1, "NEXT")
        self.assertIn(("stop", s1.plex_session), p.calls)
        self.assertEqual(self.c.s.plex_session, "NEXT")

    def test_paused_transcode_is_pinged(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        m = self.start()
        m.prop("pause", True)
        self.c.tick()
        self.clock.t += 31
        self.c.tick()
        self.assertIn("ping", p.names())

    def test_shutdown_stops_inside_the_budget(self):
        p = TcProvider(auto_mode="transcode")
        self.make(provider=p)
        self.start()
        self.c.shutdown()
        self.assertEqual(p.names().count("stop"), 1)
        self.assertIn("stopped", p.names())


if __name__ == "__main__":
    unittest.main()
