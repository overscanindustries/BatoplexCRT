import os
import tempfile
import unittest

from batoplex import tracks
from batoplex.model import Track


def T(id, kind, lang="eng", **kw):
    names = {"eng": "English", "jpn": "Japanese", "spa": "Spanish"}
    kw.setdefault("language", names.get(lang, ""))
    return Track(id=id, kind=kind, language_code=lang, **kw)


class TestParse(unittest.TestCase):
    def test_parse_part(self):
        part = {"Stream": [
            {"id": 1, "streamType": 1, "index": 0},
            {"id": 2, "streamType": 2, "index": 1, "languageCode": "jpn", "language": "Japanese",
             "codec": "aac", "channels": 2, "selected": True},
            {"id": 3, "streamType": 3, "index": 2, "languageCode": "eng", "language": "English",
             "codec": "subrip", "forced": True},
            {"id": 4, "streamType": 3, "key": "/library/streams/4", "languageCode": "eng",
             "codec": "srt", "hearingImpaired": True},
        ]}
        ts = tracks.parse(part)
        self.assertEqual([t.id for t in ts], [2, 3, 4])
        self.assertEqual(ts[0].kind, "audio")
        self.assertTrue(ts[1].forced)
        self.assertTrue(ts[2].external and ts[2].sdh)
        self.assertIsNone(ts[2].index)

    def test_markers(self):
        md = {"Marker": [{"type": "credits", "startTimeOffset": 50000, "endTimeOffset": 60000,
                          "final": True},
                         {"type": "intro", "startTimeOffset": 2000, "endTimeOffset": 12000},
                         {"type": "commercial", "startTimeOffset": 1, "endTimeOffset": 2}]}
        ms = tracks.parse_markers(md)
        self.assertEqual([m.type for m in ms], ["intro", "credits"])
        self.assertTrue(ms[1].final)
        self.assertEqual(tracks.parse_markers({}), [])


class TestLabels(unittest.TestCase):
    def test_sub_labels(self):
        self.assertEqual(tracks.label(None), "None")
        self.assertEqual(tracks.label(T(1, "sub", codec="subrip")), "English (SRT)")
        self.assertEqual(tracks.label(T(1, "sub", codec="pgs", forced=True)), "English • Forced (PGS)")
        self.assertEqual(tracks.label(T(1, "sub", codec="srt", sdh=True)), "English • SDH (SRT)")
        self.assertEqual(tracks.label(T(1, "sub", codec="srt", key="/x")), "English • External (SRT)")

    def test_audio_labels(self):
        self.assertEqual(tracks.label(T(1, "audio", codec="ac3", channels=6)), "English • 5.1 • AC3")
        self.assertEqual(tracks.label(T(1, "audio", "jpn", codec="aac", channels=2, title="Commentary")),
                         "Japanese • Stereo • AAC • Commentary")
        self.assertEqual(tracks.label(T(1, "audio", codec="aac", channels=2, title="English")),
                         "English • Stereo • AAC")

    def test_no_language(self):
        self.assertEqual(tracks.label(Track(1, "audio", display_title="Unknown (AAC)")), "Unknown (AAC)")
        self.assertEqual(tracks.label(Track(1, "audio", index=1)), "Track 2")
        self.assertEqual(tracks.label(Track(1, "sub", language_code="spa")), "Spanish")


class TestChoice(unittest.TestCase):
    def setUp(self):
        self.ja = T(10, "audio", "jpn", index=1)
        self.en_a = T(11, "audio", "eng", index=2)
        self.en_full = T(20, "sub", "eng", index=3)
        self.en_forced = T(21, "sub", "eng", index=4, forced=True)
        self.en_forced_sdh = T(22, "sub", "eng", index=5, forced=True, sdh=True)
        self.ts = [self.ja, self.en_a, self.en_full, self.en_forced_sdh, self.en_forced]

    def test_plex_selected_wins_without_memory(self):
        self.en_full.selected = True
        self.assertEqual(tracks.choose_sub(self.ts, self.en_a), (self.en_full, "plex"))

    def test_forced_in_audio_language_prefers_not_sdh(self):
        self.assertEqual(tracks.choose_sub(self.ts, self.en_a), (self.en_forced, "forced"))

    def test_forced_needs_same_language(self):
        self.assertEqual(tracks.choose_sub(self.ts, self.ja), (None, "off"))

    def test_plex_selection_beats_memory(self):
        self.en_full.selected = True
        self.assertEqual(tracks.choose_sub(self.ts, self.en_a, {"sub": {"off": True}}),
                         (self.en_full, "plex"))

    def test_memory_off_stops_forced(self):
        self.assertEqual(tracks.choose_sub(self.ts, self.en_a, {"sub": {"off": True}}), (None, "memory"))

    def test_memory_matches_on_new_episode(self):
        mem = {"sub": tracks.pref_of(self.en_full, "sub")}
        other = [T(30, "sub", "eng", forced=True), T(31, "sub", "eng"), T(32, "sub", "spa")]
        self.assertEqual(tracks.choose_sub(other, None, mem)[0].id, 31)

    def test_memory_language_missing_falls_through(self):
        mem = {"sub": {"off": False, "lang": "fre", "forced": False, "sdh": False}}
        self.assertEqual(tracks.choose_sub(self.ts, self.en_a, mem), (self.en_forced, "forced"))

    def test_audio_choice(self):
        self.ja.selected = True
        self.assertIs(tracks.choose_audio(self.ts), self.ja)
        self.assertIs(tracks.choose_audio(self.ts, {"audio": {"lang": "eng"}}), self.ja)
        self.ja.selected = False
        self.assertIs(tracks.choose_audio(self.ts, {"audio": {"lang": "eng"}}), self.en_a)
        self.assertIsNone(tracks.choose_audio([T(1, "sub")]))

    def test_mpv_id_by_source_order(self):
        self.assertEqual(tracks.mpv_id(self.en_a, self.ts), 2)
        self.assertEqual(tracks.mpv_id(self.en_forced, self.ts), 2)
        self.assertEqual(tracks.mpv_id(self.en_forced_sdh, self.ts), 3)
        self.assertIsNone(tracks.mpv_id(T(9, "sub", key="/x"), self.ts + [T(9, "sub", key="/x")]))


class TestMemory(unittest.TestCase):
    def test_round_trip(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "data", "track_prefs.json")
        m = tracks.Memory(p)
        k = m.key(None, "srv", "123")
        self.assertEqual(k, "default/srv/123")
        m.set(k, "sub", {"off": True})
        m.set(k, "audio", {"lang": "jpn", "title": ""})
        m2 = tracks.Memory(p)
        self.assertEqual(m2.get(k), {"sub": {"off": True}, "audio": {"lang": "jpn", "title": ""}})
        self.assertEqual(m2.get("nope"), {})

    def test_bad_file(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.json")
        with open(p, "w") as f:
            f.write("{bad")
        self.assertEqual(tracks.Memory(p).data, {})


if __name__ == "__main__":
    unittest.main()
