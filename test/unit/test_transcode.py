import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import config, plex  # noqa: E402
from batoplex.model import Item  # noqa: E402

MD = {"ratingKey": "9", "type": "movie", "title": "Big", "duration": 6000000, "viewOffset": 61500,
      "Media": [{"bitrate": 9000, "Part": [{"id": 90, "key": "/library/parts/90/1/file.mkv",
                                            "Stream": []}]}]}


class Base(unittest.TestCase):
    BASE = "https://8-8-4-4.abc.plex.direct:32400"

    def setUp(self):
        self.real = plex._request
        self.calls = []
        self.decision = {"generalDecisionCode": 1001, "transcodeDecisionCode": 1001}
        plex.CONN_RANK.clear()
        plex._MEASURED.clear()

        def req(url, h, method="GET", timeout=8, raw=False):
            u = urllib.parse.urlparse(url)
            self.calls.append((u.path, dict(urllib.parse.parse_qsl(u.query)), dict(h)))
            if u.path.startswith("/library/metadata/"):
                return {"MediaContainer": {"Metadata": [MD]}}
            if u.path.endswith("/decision"):
                if isinstance(self.decision, Exception):
                    raise self.decision
                return {"MediaContainer": self.decision}
            return {}
        plex._request = req
        self.st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.p = plex.PlexProvider(self.BASE, "tok", "cid", self.st, server_id="S")
        self.p.server_name = "Den"
        self.p.measure = lambda key: self.kbps
        self.kbps = 50000
        self.item = Item("9", "movie", "Big", server_id="S")

    def tearDown(self):
        plex._request = self.real

    def play(self, **opts):
        return self.p.play_info(self.item, resume=True, opts=opts)[0]

    def decisions(self):
        return [c for c in self.calls if c[0].endswith("/decision")]


class TestRules(Base):
    def test_direct_by_default_on_a_fast_remote(self):
        pi = self.play()
        self.assertEqual((pi.mode, pi.conn), ("direct", "remote"))
        self.assertFalse(self.decisions())

    def test_local_never_converts_on_auto(self):
        plex.CONN_RANK[self.BASE] = 1
        self.kbps = 100
        self.assertEqual(self.play().mode, "direct")

    def test_rule1_menu_and_retry(self):
        pi = self.play(mode="transcode", session="SESS")
        self.assertEqual((pi.mode, pi.reason, pi.session), ("transcode", "menu", "SESS"))
        pi = self.play(mode="transcode", retry=True)
        self.assertEqual(pi.reason, "retry")
        self.assertEqual(self.decisions()[-1][1]["directStream"], "0")

    def test_rule2_setting_480p(self):
        plex.CONN_RANK[self.BASE] = 1
        self.st["quality_home"] = "480p"
        self.assertEqual(self.play().reason, "setting")
        self.assertEqual(self.play(mode="direct").mode, "direct")       # the menu wins

    def test_rule3_relay(self):
        plex.CONN_RANK[self.BASE] = 4
        pi = self.play()
        self.assertEqual((pi.mode, pi.quality), ("transcode", "720 kbps"))
        q = self.decisions()[-1][1]
        self.assertEqual((q["maxVideoBitrate"], q["videoResolution"]), ("720", "576x320"))

    def test_rule4_bandwidth(self):
        self.kbps = 12000                         # 9000 > 0.7 x 12000
        self.assertEqual(self.play().reason, "bandwidth")
        self.kbps = 13000
        self.assertEqual(self.play().mode, "direct")
        self.kbps = None                          # the probe failed: play direct
        self.assertEqual(self.play().mode, "direct")
        self.st["quality_remote"] = "original"
        self.kbps = 100
        self.assertEqual(self.play().mode, "direct")


class TestQualities(Base):
    def q(self):
        return self.decisions()[-1][1]

    def test_settings_list_and_old_values(self):
        self.assertEqual([t for _k, t in plex.quality_choices()],
                         ["Automatic", "Original", "8 Mbps 480p", "4 Mbps 480p", "2 Mbps 480p",
                          "1.5 Mbps 480p", "720 kbps"])
        self.assertEqual((self.st["quality_home"], self.st["quality_remote"]), ("original", "auto"))
        self.st["quality_remote"] = "480p"                 # saved by version 1: 1.5 Mbps
        pi = self.play()
        self.assertEqual((pi.quality, self.q()["maxVideoBitrate"]), ("1.5 Mbps 480p", "1500"))

    def test_fixed_targets(self):
        for qid, kbps, res, label in (("8000", "8000", "720x480", "8 Mbps 480p"),
                                      ("2000", "2000", "720x480", "2 Mbps 480p"),
                                      ("720k", "720", "576x320", "720 kbps")):
            self.st["quality_remote"] = qid
            pi = self.play()
            self.assertEqual((pi.quality, pi.reason, self.q()["maxVideoBitrate"],
                              self.q()["videoResolution"]), (label, "setting", kbps, res))
            pi = self.play(mode="transcode", quality=pi.quality)   # a seek keeps the target
            self.assertEqual(self.q()["maxVideoBitrate"], kbps)

    def test_pal_labels_and_size(self):
        plex.PAL = True
        try:
            self.assertEqual(plex.quality_choices()[2], ("8000", "8 Mbps 576p"))
            self.st["quality_remote"] = "480p"
            pi = self.play()
            self.assertEqual((pi.quality, self.q()["videoResolution"]), ("1.5 Mbps 576p", "720x576"))
            self.assertEqual(plex.quality("720k")["res"], "576x320")
        finally:
            plex.PAL = False

    def test_automatic_picks_the_highest_that_fits(self):
        for kbps, label in ((12000, "8 Mbps 480p"), (6000, "4 Mbps 480p"), (2500, "1.5 Mbps 480p"),
                            (2900, "2 Mbps 480p"), (500, "720 kbps")):
            self.kbps = kbps
            self.assertEqual((self.play().quality, self.play().reason), (label, "bandwidth"), kbps)

    def test_home_automatic_picks_by_speed(self):
        plex.CONN_RANK[self.BASE] = 1
        self.st["quality_home"] = "auto"
        self.kbps = 12000                                  # 9000 > 0.7 x 12000
        self.assertEqual(self.play().quality, "8 Mbps 480p")
        self.kbps = 5000                                   # a slow Wi-Fi: not 8 Mbps
        self.assertEqual(self.play().quality, "2 Mbps 480p")
        self.kbps = 13000
        self.assertEqual(self.play().mode, "direct")
        self.st["quality_home"] = "original"
        self.kbps = 100
        self.assertEqual(self.play().mode, "direct")       # the default: never converts


class TestMeasure(Base):
    def test_failed_probe_is_kept_one_minute(self):
        del self.p.measure
        opened = []

        class Opener:
            def open(self, req, timeout):
                opened.append(timeout)
                raise OSError("timed out")
        real, plex._opener = plex._opener, Opener()
        try:
            self.assertIsNone(self.p.measure("/library/parts/90/1/file.mkv"))
            self.assertIsNone(self.p.measure("/library/parts/90/1/file.mkv"))
            self.assertEqual(opened, [plex.PROBE_S])       # one probe, connect at most 3 s
            kbps, t = plex._MEASURED[self.BASE]
            plex._MEASURED[self.BASE] = (kbps, t - plex.MEASURE_FAIL_TTL - 1)
            self.p.measure("/library/parts/90/1/file.mkv")
            self.assertEqual(len(opened), 2)               # tried again after 1 min
        finally:
            plex._opener = real


class TestRequest(Base):
    def test_exact_query_and_url(self):
        pi = self.play(mode="transcode", session="SESS")
        path, q, h = self.decisions()[-1]
        self.assertEqual(path, "/video/:/transcode/universal/decision")
        self.assertEqual(q, {
            "path": "/library/metadata/9", "mediaIndex": "0", "partIndex": "0", "protocol": "hls",
            "offset": "61", "fastSeek": "1", "directPlay": "0", "directStream": "1",
            "directStreamAudio": "1", "videoQuality": "99", "maxVideoBitrate": "1500",
            "videoResolution": "720x480", "subtitles": "none", "audioBoost": "100",
            "subtitleSize": "100", "location": "wan", "autoAdjustQuality": "0", "hasMDE": "1",
            "addDebugOverlay": "0", "session": "SESS", "X-Plex-Session-Identifier": "SESS",
            "X-Plex-Client-Identifier": "cid", "X-Plex-Product": "Batoplex",
            "X-Plex-Version": config.VERSION, "X-Plex-Platform": "Linux",
            "X-Plex-Device": "Batocera"})
        self.assertEqual(h["X-Plex-Client-Profile-Extra"], plex.PROFILE_EXTRA)
        self.assertEqual(pi.offset_s, 61.0)
        u = urllib.parse.urlparse(pi.url)
        self.assertEqual(u.path, "/video/:/transcode/universal/start.m3u8")
        self.assertNotIn("tok", pi.url)                           # the token is a header
        self.assertEqual(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(
            pi.url_at(300)).query))["offset"], "300")
        self.assertEqual(pi.headers["X-Plex-Token"], "tok")
        self.assertEqual(pi.headers["X-Plex-Session-Identifier"], "SESS")
        for v in pi.headers.values():
            self.assertNotIn(",", v)                              # mpv splits on commas

    def test_burned_subtitles(self):
        MD["Media"][0]["Part"][0]["Stream"] = [
            {"streamType": 3, "id": 5, "index": 2, "selected": True, "codec": "srt"}]
        try:
            self.play(mode="transcode")
        finally:
            MD["Media"][0]["Part"][0]["Stream"] = []
        self.assertEqual(self.decisions()[-1][1]["subtitles"], "burn")


class TestDecision(Base):
    def test_refused_by_choice_is_an_error(self):
        self.decision = {"generalDecisionCode": 2000, "generalDecisionText": "no"}
        with self.assertRaises(plex.TranscodeRefused) as cm:
            self.play(mode="transcode")
        self.assertEqual(str(cm.exception), "Den can't convert this video.")

    def test_refused_for_bandwidth_plays_original(self):
        self.kbps = 1000
        self.decision = {"generalDecisionCode": 1001, "transcodeDecisionCode": 3000}
        pi = self.play()
        self.assertEqual(pi.mode, "direct")
        self.assertIn("does not allow video conversion", pi.notice)

    def test_server_error_and_auth(self):
        self.decision = {"generalDecisionCode": 4005}
        with self.assertRaises(plex.PlexError) as cm:
            self.play(mode="transcode")
        self.assertNotIsInstance(cm.exception, plex.TranscodeRefused)
        self.decision = plex.PlexError("HTTP 403 for x")
        with self.assertRaises(plex.AuthError):
            self.play(mode="transcode")


class TestConn(unittest.TestCase):
    def test_kinds(self):
        plex.CONN_RANK.clear()
        self.assertEqual(plex.conn_kind("http://192.168.1.5:32400"), "local")
        self.assertEqual(plex.conn_kind("https://192-168-1-5.abc.plex.direct:32400"), "local")
        self.assertEqual(plex.conn_kind("https://8-8-4-4.abc.plex.direct:32400"), "remote")
        self.assertEqual(plex.conn_kind("https://example.com"), "remote")
        plex.CONN_RANK["https://r.plex.direct:8443"] = 4
        self.assertEqual(plex.conn_kind("https://r.plex.direct:8443/"), "relay")


if __name__ == "__main__":
    unittest.main()
