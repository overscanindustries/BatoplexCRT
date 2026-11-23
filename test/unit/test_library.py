import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import config, plex  # noqa: E402
from batoplex.model import Item  # noqa: E402


class TestLibraryApi(unittest.TestCase):
    def setUp(self):
        self.real = plex._request
        self.urls = []
        self.replies = {}
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.p = plex.PlexProvider("http://srv:32400", "tok", "cid", st, server_id="S")
        self.p.sections = [{"id": 6, "title": "Movies", "type": "movie"},
                           {"id": 5, "title": "TV", "type": "show"}]

        def req(url, h, **k):
            self.urls.append(url)
            u = urllib.parse.urlparse(url)
            r = self.replies.get(u.path, {})
            if isinstance(r, Exception):
                raise r
            return {"MediaContainer": r}
        plex._request = req
        self.movies = Item("6", "library", "Movies", section_id=6, server_id="S")
        self.tv = Item("5", "library", "TV", section_id=5, server_id="S")

    def tearDown(self):
        plex._request = self.real

    def q(self, i=-1):
        u = urllib.parse.urlparse(self.urls[i])
        return u.path, dict(urllib.parse.parse_qsl(u.query))

    def test_page_query(self):
        self.replies["/library/sections/5/all"] = {"totalSize": 120, "Metadata": [
            {"ratingKey": "1", "type": "show", "title": "A"}]}
        items, total = self.p.library_page(self.tv, "titleSort:desc", {"unwatchedLeaves": 1}, 48)
        path, q = self.q()
        self.assertEqual(path, "/library/sections/5/all")
        self.assertEqual(q, {"type": "2", "sort": "titleSort:desc", "unwatchedLeaves": "1",
                             "X-Plex-Container-Start": "48", "X-Plex-Container-Size": "48"})
        self.assertEqual((len(items), total), (1, 120))
        self.assertNotIn("tok", self.urls[-1])                  # the token is a header

    def test_search(self):
        self.replies["/hubs/search"] = {"Hub": [
            {"type": "movie", "title": "Movies", "Metadata": [
                {"ratingKey": "1", "type": "movie", "title": "Alien", "guid": "plex://movie/a",
                 "librarySectionID": 6},
                {"ratingKey": "2", "type": "movie", "title": "Home video", "librarySectionID": 9}]},
            {"type": "actor", "title": "People", "Metadata": [{"type": "actor", "tag": "x"}]},
            {"type": "show", "title": "Shows", "Metadata": []},
            {"type": "episode", "title": "Episodes", "Metadata": [
                {"ratingKey": "3", "type": "episode", "title": "Pilot", "grandparentTitle": "S"}]},
            {"type": "artist", "title": "Artists", "Metadata": [{"ratingKey": "4", "type": "artist"}]},
        ]}
        rows = self.p.search("al", limit=12)
        path, q = self.q()
        self.assertEqual(path, "/hubs/search")
        self.assertEqual(q, {"query": "al", "limit": "12", "includeCollections": "1"})
        self.assertNotIn("tok", self.urls[-1])
        self.assertEqual([(r.key, r.title, [i.id for i in r.items]) for r in rows],
                         [("movie", "Movies", ["1"]), ("episode", "Episodes", ["3"]),
                          ("artist", "Artists", ["4"])])                # music is kept
        self.assertEqual(rows[0].items[0].extra["guid"], "plex://movie/a")
        self.assertEqual(rows[0].items[0].server_id, "S")

    def test_demo_search(self):
        import sample_provider as demo
        d = demo.DemoProvider()
        rows = d.search("AIR")
        self.assertTrue(rows)
        self.assertTrue(all(q.lower() in i.title.lower() for r in rows for i in r.items
                            for q in ["air"]))
        self.assertEqual(d.search("zzzz"), [])

    def test_music_parse_and_calls(self):
        self.p.sections.append({"id": 9, "title": "Music", "type": "artist"})
        music = Item("9", "library", "Music", section_id=9, server_id="S")
        art = {"ratingKey": "a1", "type": "artist", "title": "Band", "thumb": "/t/a1"}
        alb = lambda rk, y: {"ratingKey": rk, "type": "album", "title": "LP" + rk, "year": y,
                             "parentTitle": "Band", "parentRatingKey": "a1", "thumb": "/t/" + rk,
                             "leafCount": 2}
        trk = {"ratingKey": "t1", "type": "track", "title": "Song", "index": 3, "parentIndex": 2,
               "duration": 200000, "parentTitle": "LP", "parentRatingKey": "b1",
               "grandparentTitle": "Band", "grandparentRatingKey": "a1", "originalTitle": "Guest",
               "parentThumb": "/t/b1", "Media": [{"Part": [{"key": "/library/parts/7/1/f.flac"}]}]}
        self.replies["/library/sections/9/all"] = {"totalSize": 1, "Metadata": [art]}
        items, total = self.p.library_page(music, "titleSort", {}, 0, kind="album")
        self.assertEqual(self.q()[1]["type"], "9")
        self.p.library_page(music, "titleSort", {}, 0)
        self.assertEqual(self.q()[1]["type"], "8")                 # an artist section: artists
        self.assertEqual((items[0].type, items[0].poster), ("artist", "S|/t/a1"))
        self.replies["/library/metadata/a1/children"] = {"Metadata": [alb("b0", 1980), alb("b1", 1990)]}
        albums = self.p.albums(Item("a1", "artist", "Band", server_id="S"))
        self.assertEqual([a.id for a in albums], ["b1", "b0"])     # newest first
        self.assertEqual((albums[0].artist, albums[0].artist_id), ("Band", "a1"))
        self.replies["/library/metadata/b1/children"] = {"Metadata": [trk]}
        t = self.p.tracks(albums[0])[0]
        self.assertEqual((t.artist, t.album, t.album_id, t.artist_id, t.index, t.disc),
                         ("Guest", "LP", "b1", "a1", 3, 2))
        self.assertEqual((t.poster, t.extra["album_artist"]), ("S|/t/b1", "Band"))
        n = len(self.urls)
        pi = self.p.track_info(t)
        self.assertEqual(len(self.urls), n + 1)              # one request: the loudness
        self.assertEqual(pi.url, "http://srv:32400/library/parts/7/1/f.flac")
        self.assertEqual(pi.headers, {"X-Plex-Token": "tok"})
        self.assertIsNone(pi.gain_db)                        # no metadata reply: no gain
        self.replies["/library/sections/9/all"] = {"Metadata": [trk]}
        self.assertEqual(len(self.p.shuffle_tracks(music)), 1)
        self.assertEqual({k: self.q()[1][k] for k in ("type", "sort")}, {"type": "10", "sort": "random"})
        self.replies["/library/metadata/a1/allLeaves"] = {"Metadata": [trk]}
        self.assertEqual(len(self.p.artist_tracks(Item("a1", "artist", "Band", server_id="S"))), 1)

    def test_music_home_rows(self):
        """Played then added, in section order; fallbacks; failures."""
        self.p.sections.append({"id": 9, "title": "Music", "type": "artist"})
        alb = lambda rk, **k: dict({"ratingKey": rk, "type": "album", "title": "LP" + rk}, **k)
        self.replies["/library/sections/6/recentlyAdded"] = {"Metadata": [
            {"ratingKey": "m1", "type": "movie", "title": "M"}]}
        self.replies["/hubs/sections/9"] = {"Hub": [
            {"hubIdentifier": "music.stations.9", "title": "Stations", "Metadata": [
                {"key": "/library/sections/9/stations/1", "title": "S"}]},
            {"hubIdentifier": "music.recent.added.9", "title": "Recently Added", "key": "/hubs/a",
             "size": 30, "more": True, "Metadata": [alb("b1")]},
            {"hubIdentifier": "music.recent.played.9", "title": "Recently Played",
             "Metadata": [alb("b2")]}]}
        rows = self.p.home_rows()
        self.assertEqual([r.title for r in rows if r.key != "continue"][:3],
                         ["Recently Added in Movies", "Recently Played in Music",
                          "Recently Added in Music"])
        played, added = rows[1], rows[2]
        self.assertEqual((played.key, played.items[0].id), ("music-played-S-9", "b2"))
        self.assertEqual((added.total, added.loader is not None), (30, True))
        # No played hub: the album list by last play, only played albums. An empty added hub:
        # nothing, and no fallback request.
        self.replies["/hubs/sections/9"] = {"Hub": [
            {"hubIdentifier": "music.recent.added.9", "Metadata": []}]}
        self.replies["/library/sections/9/all"] = {"Metadata": [
            alb("b3", lastViewedAt=1700000000), alb("b4")]}
        self.urls.clear()
        rows = self.p.home_rows()
        self.assertEqual([(r.title, [i.id for i in r.items]) for r in rows if "Music" in r.title],
                         [("Recently Played in Music", ["b3"])])
        self.assertEqual(self.q(next(i for i, u in enumerate(self.urls) if "/9/all" in u))[1]
                         ["sort"], "lastViewedAt:desc")
        self.assertFalse(any("/9/recentlyAdded" in u for u in self.urls))
        # A shared user without access to the music hubs: no music rows, video rows stay.
        self.replies["/hubs/sections/9"] = plex.AuthError("HTTP 403")
        rows = self.p.home_rows()
        self.assertEqual([r.title for r in rows], ["Recently Added in Movies"])

    def test_unplayed_filter_by_type(self):
        self.assertEqual(self.p.unplayed_filter(self.movies), {"unwatched": 1})
        self.assertEqual(self.p.unplayed_filter(self.tv), {"unwatchedLeaves": 1})

    def test_meta_and_fallback_sorts(self):
        self.replies["/library/sections/6/all"] = {"Meta": {"Type": [
            {"type": "show", "Sort": [{"key": "x", "title": "X"}]},
            {"type": "movie", "Sort": [{"key": "titleSort", "title": "Title", "defaultDirection": "asc"}],
             "Filter": [{"filter": "genre", "title": "Genre", "key": "/library/sections/6/genre"}]}]}}
        m = self.p.library_meta(self.movies)
        self.assertEqual(m["sorts"], [{"key": "titleSort", "title": "Title", "dir": "asc"}])
        self.assertEqual(m["filters"][0]["filter"], "genre")
        _p, q = self.q()
        self.assertEqual((q["includeMeta"], q["X-Plex-Container-Size"]), ("1", "0"))
        self.replies["/library/sections/6/all"] = plex.PlexError("HTTP 500")
        m = self.p.library_meta(self.movies)
        self.assertEqual([s["key"] for s in m["sorts"]][:2], ["titleSort", "addedAt"])
        self.assertEqual(m["filters"], [])
        self.assertTrue(m.get("failed"))

    def test_letter_offsets(self):
        chars = [("#", 2), ("A", 5), ("B", 3), ("D", 1)]
        self.assertEqual(plex.letter_offset(chars, "#"), 0)
        self.assertEqual(plex.letter_offset(chars, "B"), 7)
        self.assertEqual(plex.letter_offset(chars, "D", desc=True), 0)
        self.assertEqual(plex.letter_offset(chars, "A", desc=True), 4)
        self.assertIsNone(plex.letter_offset(chars, "Z"))

    def test_first_characters(self):
        self.replies["/library/sections/6/firstCharacter"] = {"Directory": [
            {"key": "A", "title": "A", "size": 4}, {"key": "#", "title": "#", "size": "2"}]}
        self.assertEqual(self.p.first_characters(self.movies, {"genre": "21"}), [("A", 4), ("#", 2)])
        _p, q = self.q()
        self.assertEqual(q, {"type": "1", "genre": "21"})

    def test_recommended_skips_empty_and_pages_more(self):
        self.replies["/hubs/sections/6"] = {"Hub": [
            {"hubIdentifier": "a", "title": "Recent", "key": "/hubs/x", "size": 12, "more": True,
             "Metadata": [{"ratingKey": "1", "type": "movie", "title": "M"}]},
            {"hubIdentifier": "b", "title": "Empty", "Metadata": []}]}
        rows = self.p.recommended(self.movies)
        self.assertEqual([r.title for r in rows], ["Recent"])
        self.assertIsNotNone(rows[0].loader)
        self.assertEqual(self.q()[1], {"count": "12"})

    def test_collection_children_fallback(self):
        coll = Item("77", "collection", "C", server_id="S")
        self.replies["/library/collections/77/children"] = plex.PlexError("HTTP 404")
        self.replies["/library/metadata/77/children"] = {"totalSize": 1, "Metadata": [
            {"ratingKey": "1", "type": "movie", "title": "M"}]}
        items, total = self.p.collection_items(coll, 0)
        self.assertEqual((len(items), total), (1, 1))
        self.assertEqual(self.q()[0], "/library/metadata/77/children")

    def test_filter_values(self):
        self.replies["/library/sections/6/genre"] = {"Directory": [{"key": 21, "title": "Comedy"}]}
        self.assertEqual(self.p.filter_values("/library/sections/6/genre"), [("21", "Comedy")])
        self.assertEqual(self.q()[1]["X-Plex-Container-Size"], "100")


class TestDemoLibrary(unittest.TestCase):
    def test_same_api(self):
        from sample_provider import DemoProvider
        d = DemoProvider()
        lib = d.libraries()[0]
        items, total = d.library_page(lib, "titleSort", {}, 0, 4)
        self.assertEqual(len(items), min(4, total))
        self.assertEqual(sum(n for _c, n in d.first_characters(lib)), total)
        g = d.filter_values("demo:genre")[0][0]
        _i, gt = d.library_page(lib, "titleSort", {"genre": g}, 0)
        self.assertLess(gt, total)
        self.assertTrue(d.recommended(lib))
        c, n = d.collections(lib, 0)
        self.assertEqual(n, 1)
        self.assertTrue(d.collection_items(c[0], 0)[0])


if __name__ == "__main__":
    unittest.main()
