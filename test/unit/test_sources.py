import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex.model import Item  # noqa: E402
from batoplex.sources import Sources  # noqa: E402


class P:
    def __init__(self, sid):
        self.server_id = sid
        self.name = "p"
        self.calls = []

    def image(self, ref, w, h):
        self.calls.append(ref)
        return ref

    def theme_url(self, ref):
        return ("http://%s%s" % (self.server_id, ref), {})


class TestSources(unittest.TestCase):
    def test_routes_by_server(self):
        a, b = P("A"), P("B")
        s = Sources([a, b], home_id="A")
        self.assertIs(s.provider_for(Item("1", "movie", "x", server_id="B")), b)
        self.assertIs(s.provider_for(Item("1", "movie", "x")), a)
        self.assertEqual(s.image("B|/thumb/1", 10, 10), "/thumb/1")
        self.assertEqual(b.calls, ["/thumb/1"])
        self.assertEqual(s.image("/demo/art", 10, 10), "/demo/art")   # untagged: home
        self.assertEqual(s.theme_url("B|/t"), ("http://B/t", {}))

    def test_local_images_passes_through(self):
        d = P("demo")
        d.local_images = True
        self.assertTrue(Sources([d]).local_images)
        self.assertFalse(Sources([P("A")]).local_images)

    def test_provider_that_learned_its_id_late(self):
        p = P(None)
        s = Sources([p])
        p.server_id = "late"
        self.assertIs(s.provider("late"), p)
        self.assertEqual(s.image("late|/x", 1, 1), "/x")

    def test_plex_items_are_tagged(self):
        from batoplex.plex import PlexProvider
        pp = PlexProvider.__new__(PlexProvider)
        pp.server_id = "srv"
        it = pp._item({"ratingKey": "5", "type": "episode", "thumb": "/t", "grandparentThumb": "/g",
                       "art": "/a", "grandparentTheme": "/th"})
        self.assertEqual((it.poster, it.still, it.theme, it.server_id),
                         ("srv|/g", "srv|/t", "srv|/th", "srv"))


class TestSectionType(unittest.TestCase):
    def test_pass_through_and_fallback(self):
        from batoplex.model import Item
        from batoplex.sources import Sources

        class P:
            server_id = "H"

            def section_type(self, lib):
                return "artist"
        lib = Item("9", "library", "Music", section_id=9, server_id="H")
        self.assertEqual(Sources([P()], home_id="H").section_type(lib), "artist")
        q = type("Q", (), {"server_id": "H"})()
        lib.extra["section_type"] = "show"
        self.assertEqual(Sources([q], home_id="H").section_type(lib), "show")


if __name__ == "__main__":
    unittest.main()


class TestMergedContinueWatching(unittest.TestCase):
    def test_merge_newest_first_pinned_only(self):
        from batoplex.model import Row

        class St:
            def __init__(self, pins):
                self.p = pins

            def pins(self, sid):
                return self.p.get(sid, [])

        class Prov(P):
            def __init__(self, sid, items, st):
                super().__init__(sid)
                self.items, self.settings = items, st

            def continue_watching(self):
                return self.items

            def home_rows(self):
                return [Row("continue", "Continue Watching", list(self.items)), Row("x", "X", [])]

        def it(i, sid, sec, lv):
            x = Item(i, "movie", i, server_id=sid, section_id=sec)
            x.extra["last_viewed"] = lv
            return x
        st = St({"B": [6]})
        a = Prov("A", [it("1", "A", 6, 100), it("2", "A", 6, 300)], st)
        b = Prov("B", [it("1", "B", 6, 200), it("9", "B", 7, 999)], st)   # section 7 not pinned
        rows = Sources([a, b], home_id="A").home_rows()
        self.assertEqual([(i.server_id, i.id) for i in rows[0].items], [("A", "2"), ("B", "1"), ("A", "1")])
        self.assertEqual([r.key for r in rows], ["continue", "x"])
        # An item without lastViewedAt keeps its place after the item before it.
        a = Prov("A", [it("1", "A", 6, 300), it("3", "A", 6, None), it("2", "A", 6, 100)], st)
        b = Prov("B", [it("1", "B", 6, 200)], st)
        rows = Sources([a, b], home_id="A").home_rows()
        self.assertEqual([(i.server_id, i.id) for i in rows[0].items],
                         [("A", "1"), ("A", "3"), ("B", "1"), ("A", "2")])
        a = Prov("A", [it("up", "A", 6, None), it("2", "A", 6, 300)], st)   # Up Next first
        rows = Sources([a, b], home_id="A").home_rows()
        self.assertEqual([(i.server_id, i.id) for i in rows[0].items],
                         [("A", "up"), ("A", "2"), ("B", "1")])


class TestPinned(unittest.TestCase):
    class Lib(P):
        loaded = True

        def libraries(self, cached=False):
            if cached and not self.loaded:
                return None
            if not self.loaded:
                raise AssertionError("pinned() made a request")
            return [Item("6", "library", "Movies", section_id=6, server_id=self.server_id),
                    Item("5", "library", "TV", section_id=5, server_id=self.server_id)]

    def test_no_request_and_no_seed_before_sections_load(self):
        st = self.settings()
        a = self.Lib("A")
        a.loaded = False
        src = Sources([a], home_id="A")
        self.assertEqual(src.pinned(st), [])
        self.assertFalse(st.seeded())
        a.loaded = True
        self.assertEqual(len(src.pinned(st)), 2)
        self.assertTrue(st.seeded())

    def test_server_without_sections_is_down_not_fetched(self):
        st = self.settings()
        b = self.Lib("B")
        b.loaded = False
        st.set_pins([{"server": "B", "section": 6, "title": "Shared"}])
        libs = Sources([self.Lib("A"), b], home_id="A").pinned(st)
        self.assertEqual([(l.title, bool(l.extra.get("down"))) for l in libs], [("Shared", True)])

    def test_local_pins_not_saved(self):
        st = self.settings()
        d = self.Lib("local")
        d.local_images = True
        self.assertEqual(len(Sources([d], home_id="local").pinned(st)), 2)
        self.assertFalse(st.seeded())                       # nothing written without a server

    def test_old_sample_pins_dropped_and_reseeded(self):
        from batoplex import config
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        with open(path, "w") as f:
            json.dump({"profiles": {"default": {"seeded": True, "pins": [
                {"server": "demo", "section": 6}, {"server": "livetv", "section": 0}]}}}, f)
        st = config.Settings(path)
        self.assertEqual([e["server"] for e in st.pin_entries()], ["livetv"])
        libs = Sources([self.Lib("A")], home_id="A").pinned(st)
        self.assertEqual([(l.server_id, bool(l.extra.get("down"))) for l in libs],
                         [("A", False), ("A", False)])

    def settings(self):
        from batoplex import config
        return config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))

    def test_seed_once_then_down_server_kept(self):
        Lib = self.Lib
        st = self.settings()
        src = Sources([Lib("A")], home_id="A")
        libs = src.pinned(st)
        self.assertEqual([(l.server_id, l.section_id, l.title) for l in libs],
                         [("A", 6, "Movies"), ("A", 5, "TV")])
        self.assertTrue(st.seeded())
        st.set_pins(st.pin_entries() + [{"server": "B", "section": 2, "title": "Shared"}])
        libs = Sources([Lib("A")], home_id="A").pinned(st)
        self.assertEqual(libs[-1].title, "Shared")
        self.assertTrue(libs[-1].extra.get("down"))          # server B not connected
