"""Loudness leveling: gain math, the metadata cache, the exact
loadfile commands, and the per-entry headers."""
import os
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import audio, config, loudness, plex  # noqa: E402
from batoplex.model import Item, PlayInfo  # noqa: E402
from fake_mpv import FakeMpv  # noqa: E402
from test_music import Base, Prov, sync, tracks  # noqa: E402

G = loudness.gain_db


class TestGain(unittest.TestCase):
    def test_album_first_then_track_then_loudness(self):
        self.assertEqual(G({"albumGain": 4.0, "albumPeak": 0.89, "gain": -3.0, "peak": 0.5}), 1.01)
        self.assertEqual(G({"gain": -3.456, "peak": 0.5}), -3.46)       # rounds down
        self.assertEqual(G({"loudness": -20.0, "peak": 0.5}), 2.0)      # -18 - loudness
        self.assertIsNone(G({}))
        self.assertIsNone(G({"peak": 0.5}))
        self.assertIsNone(G(None))

    def test_offset_only_on_a_plex_gain(self):
        with mock.patch.object(loudness, "PLEX_OFFSET_DB", 3.0):
            self.assertEqual(G({"gain": -5.0, "peak": 1.0}), -2.0)
            self.assertEqual(G({"albumGain": -5.0, "albumPeak": 1.0}), -2.0)
            self.assertEqual(G({"loudness": -20.0, "peak": 0.5}), 2.0)   # not +5

    def test_peak_limit_last_and_never_rounded_up(self):
        self.assertEqual(G({"gain": 3.0, "peak": 0.9}), 0.91)          # limit 0.915
        self.assertEqual(G({"gain": 3.0}), 0.0)                        # no peak: no boost
        self.assertEqual(G({"gain": 30.0, "peak": 0.01}), 12.0)        # upper clamp
        self.assertEqual(G({"gain": -40.0, "peak": 1.0}), -24.0)       # lower clamp
        self.assertEqual(G({"gain": -30.0, "peak": 100.0}), -40.0)     # the peak wins over -24

    def test_invalid_values(self):
        nan, inf = float("nan"), float("inf")
        self.assertEqual(G({"albumGain": nan, "gain": -2.0, "peak": 1.0}), -2.0)
        self.assertIsNone(G({"gain": inf}))
        self.assertIsNone(G({"gain": "x"}))
        self.assertEqual(G({"gain": 2.0, "peak": nan}), 0.0)
        self.assertEqual(G({"gain": 2.0, "peak": -1.0}), 0.0)

    def test_gapless_clamp(self):
        self.assertEqual(loudness.gapless_gain(3.0, 1.5), 1.5)
        self.assertEqual(loudness.gapless_gain(1.0, 1.5), 1.0)
        self.assertEqual(loudness.gapless_gain(2.0, None), 0.0)   # unknown peak: no boost
        self.assertIsNone(loudness.gapless_gain(None, 1.0))


def trk(rk, part=True, stream=None):
    m = {"ratingKey": rk, "type": "track", "title": "Song", "duration": 1000}
    p = {}
    if part:
        p["key"] = "/library/parts/%s/f.flac" % rk
    if stream is not None:
        p["Stream"] = stream
    m["Media"] = [{"Part": [p]}]
    return m


class TestPlexTrackInfo(unittest.TestCase):
    def setUp(self):
        self.real = plex._request
        self.calls, self.replies = [], {}
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.p = plex.PlexProvider("http://srv:32400", "tok", "cid", st, server_id="S")

        def req(url, h, timeout=None, **k):
            path = urllib.parse.urlparse(url).path
            self.calls.append((path, timeout))
            r = self.replies.get(path, {})
            if isinstance(r, Exception):
                raise r
            return {"MediaContainer": r}
        plex._request = req

    def tearDown(self):
        plex._request = self.real

    def test_item_reads_the_audio_stream(self):
        it = self.p._item(trk("1", stream=[{"streamType": 1},
                                           {"streamType": 2, "gain": "4.0", "albumGain": -1,
                                            "peak": 0.89, "loudness": -22}]))
        self.assertEqual(it.extra["loud"], {"gain": 4.0, "albumGain": -1.0, "peak": 0.89,
                                            "albumPeak": None, "loudness": -22.0})
        pi = self.p.track_info(it)
        self.assertEqual(self.calls, [])                       # known: no request
        self.assertEqual(pi.gain_db, -1.0)
        self.assertNotIn("loud", self.p._item(trk("2")).extra)  # no Stream: not fetched

    def test_one_request_per_track_then_cached(self):
        self.replies["/library/metadata/5"] = {"Metadata": [trk("5", stream=[
            {"streamType": 2, "gain": 2.0, "peak": 0.5}])]}
        pi = self.p.track_info(self.p._item(trk("5")))
        self.assertEqual(self.calls, [("/library/metadata/5", 3)])
        self.assertEqual((pi.gain_db, round(pi.gain_max_db, 2)), (2.0, 6.02))
        pi = self.p.track_info(self.p._item(trk("5")))           # another Item, same track
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(pi.gain_db, 2.0)
        pi = self.p.track_info(Item("5", "track", "Song"))      # no part key: from the cache
        self.assertEqual((len(self.calls), pi.url), (1, "http://srv:32400/library/parts/5/f.flac"))

    def test_fetched_without_loudness_is_cached(self):
        self.replies["/library/metadata/6"] = {"Metadata": [trk("6", stream=[{"streamType": 2}])]}
        self.assertIsNone(self.p.track_info(self.p._item(trk("6"))).gain_db)
        self.assertIsNone(self.p.track_info(self.p._item(trk("6"))).gain_db)
        self.assertEqual(len(self.calls), 1)

    def test_request_error(self):
        self.replies["/library/metadata/7"] = plex.PlexError("network: timed out")
        pi = self.p.track_info(self.p._item(trk("7")))           # part known: plays, no gain
        self.assertEqual((pi.gain_db, pi.url), (None, "http://srv:32400/library/parts/7/f.flac"))
        self.p.track_info(self.p._item(trk("7")))
        self.assertEqual(len(self.calls), 2)                     # a failure is not cached
        with self.assertRaises(plex.PlexError):
            self.p.track_info(Item("7", "track", "Song"))        # no part: it fails
        self.assertEqual(self.calls[-1], ("/library/metadata/7", 8))


def pinfo(url, tok="tok", gain=None, lim=None):
    return PlayInfo(url=url, headers={"X-Plex-Token": tok} if tok else {}, gain_db=gain,
                    gain_max_db=lim)


class TestLoadfile(unittest.TestCase):
    def make(self, version=(0, 41)):
        self.mpvs = []

        def mk():
            m = FakeMpv()
            m.version = version
            self.mpvs.append(m)
            return m
        a = audio.AudioPlayer("mpv", run=sync, make_mpv=mk)
        return a

    def loads(self):
        return [c for c in self.mpvs[-1].cmds if c[0] == "loadfile"]

    def test_exact_commands_0_41(self):
        a = self.make()
        a.music_open(pinfo("http://s/1", gain=1.01, lim=1.01), pinfo("http://s/2", lim=3.0))
        a.tick()
        self.assertEqual(self.loads(), [
            ["loadfile", "http://s/1", "replace", -1,
             {"http-header-fields": "X-Plex-Token: tok", "replaygain-fallback": "1.01"}],
            ["loadfile", "http://s/2", "append", -1, {"http-header-fields": "X-Plex-Token: tok"}]])
        a.music_next(pinfo("http://s/3", gain=-6.5, lim=2.0))
        self.assertEqual(self.loads()[-1], ["loadfile", "http://s/3", "append", -1,
                         {"http-header-fields": "X-Plex-Token: tok", "replaygain-fallback": "-6.50"}])
        for c in self.loads():
            self.assertTrue(all(isinstance(v, str) for v in c[-1].values()))

    def test_gapless_clamp_to_the_previous_peak(self):
        a = self.make()
        a.music_open(pinfo("http://s/1", gain=0.5, lim=1.5), pinfo("http://s/2", gain=4.0, lim=6.0))
        a.tick()
        self.assertEqual(self.loads()[-1][-1]["replaygain-fallback"], "1.50")
        a.music_drop_first()                     # s/2 plays now: its limit is the current one
        a.music_next(pinfo("http://s/3", gain=5.0, lim=9.0))
        self.assertEqual(self.loads()[-1][-1]["replaygain-fallback"], "5.00")

    def test_old_and_unknown_versions(self):
        a = self.make((0, 37))
        a.music_open(pinfo("http://s/1", gain=2.0, lim=3.0))
        a.tick()
        self.assertEqual(self.loads(), [["loadfile", "http://s/1", "replace",
                         {"http-header-fields": "X-Plex-Token: tok", "replaygain-fallback": "2.00"}]])
        a = self.make((0, 0))
        a.music_open(pinfo("http://s/1", gain=2.0, lim=3.0))
        a.tick()
        m = self.mpvs[-1]
        self.assertEqual(self.loads(), [["loadfile", "http://s/1", "replace"]])
        hdr = [c[2] for c in m.cmds if c[:2] == ["set_property", "http-header-fields"]]
        self.assertEqual(hdr[-1], "X-Plex-Token: tok")    # the header still goes

    def test_comma_header_left_out(self):
        self.assertEqual(audio.header_value({"A": "1,2", "X-Plex-Token": "t"}), "X-Plex-Token: t")

    def test_theme_music_radio_headers(self):
        a = self.make()
        a.play("http://A/theme", {"X-Plex-Token": "themetok"}, 50)
        a.tick()
        m = self.mpvs[-1]
        self.assertEqual(self.loads(), [["loadfile", "http://A/theme", "replace"]])
        a.music_open(pinfo("http://B/1", tok="btok"))
        a.music_close()
        a.music_open(pinfo("https://radio/s", tok=None))
        after = m.cmds[m.cmds.index(["loadfile", "http://A/theme", "replace"]) + 1:]
        self.assertNotIn("themetok", repr(after))
        hdr = [c[2] for c in after if c[:2] == ["set_property", "http-header-fields"]]
        self.assertEqual(hdr, ["", ""])                       # cleared before music and radio
        self.assertEqual(self.loads()[-2:], [
            ["loadfile", "http://B/1", "replace", -1, {"http-header-fields": "X-Plex-Token: btok"}],
            ["loadfile", "https://radio/s", "replace", -1, {"http-header-fields": ""}]])


class GainProv(Prov):
    def __init__(self, fail=()):
        super().__init__()
        self.fail = fail

    def track_info(self, t):
        if t.id in self.fail:
            raise plex.PlexError("network: down")
        return PlayInfo(url="http://%s/%s" % (t.server_id, t.id),
                        headers={"X-Plex-Token": "tok-" + t.server_id}, duration_ms=t.duration_ms,
                        gain_db=-1.0 if t.id == "t1" else None, gain_max_db=2.0)


class TestMusicEntries(Base):
    def full_loads(self):
        return [c for c in self.m.cmds if c[0] == "loadfile"]

    def test_next_entry_carries_its_gain_and_header(self):
        for t in self.items:
            t.server_id = "A"
        self.p = GainProv()
        self.start()
        self.assertEqual(self.full_loads(), [
            ["loadfile", "http://A/t0", "replace", -1, {"http-header-fields": "X-Plex-Token: tok-A"}],
            ["loadfile", "http://A/t1", "append", -1,
             {"http-header-fields": "X-Plex-Token: tok-A", "replaygain-fallback": "-1.00"}]])

    def test_no_next_entry_across_servers(self):
        self.items = tracks(2)
        self.items[0].server_id, self.items[1].server_id = "A", "B"
        self.p = GainProv()
        self.start()
        self.assertEqual(self.loads(), [["http://A/t0", "replace"]])
        self.ev("end-file", reason="eof")             # _finish opens t1 with B's header
        self.assertEqual(self.full_loads()[-1], ["loadfile", "http://B/t1", "replace", -1,
                         {"http-header-fields": "X-Plex-Token: tok-B", "replaygain-fallback": "-1.00"}])
        self.assertEqual(self.c.index, 1)

    def test_failed_next_lookup_keeps_the_current_track(self):
        for t in self.items:
            t.server_id = "A"
        self.p = GainProv(fail=("t1",))
        self.start()
        self.assertEqual((self.c.state, self.loads()), ("playing", [["http://A/t0", "replace"]]))


if __name__ == "__main__":
    unittest.main()
