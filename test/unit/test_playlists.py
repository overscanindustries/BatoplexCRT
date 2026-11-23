import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import config, plex  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.sources import Router, Sources  # noqa: E402


def settings():
    return config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))


class TestPlaylistApi(unittest.TestCase):
    """Each call's method, path and query; the token only as a header."""

    def setUp(self):
        self.real = plex._request
        self.calls, self.replies = [], {}
        self.p = plex.PlexProvider("http://srv:32400", "tok", "cid", settings(), server_id="S")

        def req(url, h, method="GET", **k):
            self.calls.append((method, url, h.get("X-Plex-Token")))
            r = self.replies.get(urllib.parse.urlparse(url).path, {})
            if isinstance(r, Exception):
                raise r
            return {"MediaContainer": r}
        plex._request = req
        self.pl = Item("77", "playlist", "Road Trip", server_id="S")
        self.e1 = Item("101", "movie", "A", server_id="S", extra={"pl_item": "9001"})
        self.e2 = Item("102", "movie", "B", server_id="S", extra={"pl_item": "9002"})

    def tearDown(self):
        plex._request = self.real

    def last(self):
        method, url, tok = self.calls[-1]
        u = urllib.parse.urlparse(url)
        self.assertNotIn("tok", url)
        self.assertEqual(tok, "tok")
        return method, u.path, dict(urllib.parse.parse_qsl(u.query))

    def test_list_and_parse(self):
        self.replies["/playlists"] = {"Metadata": [
            {"ratingKey": "77", "type": "playlist", "title": "Road Trip", "playlistType": "video",
             "smart": 0, "leafCount": 3, "duration": 9000000, "composite": "/playlists/77/composite/5"},
            {"ratingKey": "78", "type": "playlist", "title": "Hits", "playlistType": "audio", "smart": 1}]}
        items = self.p.playlists("video", section_id=None)
        self.assertEqual(self.last(), ("GET", "/playlists", {
            "playlistType": "video", "X-Plex-Container-Start": "0", "X-Plex-Container-Size": "1000"}))
        self.assertEqual([i.id for i in items], ["77"])         # another type is left out
        pl = items[0]
        self.assertEqual((pl.type, pl.leaf_count, pl.extra["smart"], pl.extra["kind"]),
                         ("playlist", 3, False, "video"))
        self.assertEqual(pl.poster, "S|/playlists/77/composite/5")
        self.p.playlists("audio", section_id=9)
        self.assertEqual(self.last()[2]["sectionID"], "9")

    def test_items_keep_each_entry_server(self):
        self.replies["/playlists/77/items"] = {"totalSize": 450, "Metadata": [
            {"ratingKey": "101", "type": "movie", "title": "A", "playlistItemID": 9001,
             "thumb": "/library/metadata/101/thumb/1"},
            {"ratingKey": "101", "type": "movie", "title": "Other A", "playlistItemID": 9002,
             "thumb": "/library/metadata/101/thumb/1",
             "sourceURI": "server://T/com.plexapp.plugins.library/library/metadata/101"},
            {"ratingKey": "5", "type": "clip", "title": "c", "playlistItemID": 9003}]}
        items, total, n = self.p.playlist_items(self.pl, 200)
        self.assertEqual(self.last()[2], {"X-Plex-Container-Start": "200", "X-Plex-Container-Size": "200"})
        self.assertEqual((len(items), total, n), (2, 450, 3))
        a, b = items
        self.assertEqual((a.server_id, a.poster, a.extra["pl_item"], a.extra["occ"]),
                         ("S", "S|/library/metadata/101/thumb/1", "9001", "pl:9001"))
        # the same rating key on another server: its own server for media, art and reports
        self.assertEqual((b.server_id, b.poster), ("T", "T|/library/metadata/101/thumb/1"))

    def test_writes(self):
        self.p.playlist_add(self.pl, [self.e1, self.e2])
        self.assertEqual(self.last(), ("PUT", "/playlists/77/items", {
            "uri": "server://S/com.plexapp.plugins.library/library/metadata/101,102"}))
        self.p.playlist_remove(self.pl, self.e2)
        self.assertEqual(self.last(), ("DELETE", "/playlists/77/items/9002", {}))
        self.p.playlist_move(self.pl, self.e2, self.e1)
        self.assertEqual(self.last(), ("PUT", "/playlists/77/items/9002/move", {"after": "9001"}))
        self.p.playlist_move(self.pl, self.e2, None)              # to the first place
        self.assertEqual(self.last(), ("PUT", "/playlists/77/items/9002/move", {}))
        self.p.playlist_rename(self.pl, "Road Trip 2")
        self.assertEqual(self.last(), ("PUT", "/playlists/77", {"title": "Road Trip 2"}))
        self.p.playlist_delete(self.pl)
        self.assertEqual(self.last(), ("DELETE", "/playlists/77", {}))

    def test_create(self):
        self.replies["/playlists"] = {"Metadata": [{"ratingKey": "80", "type": "playlist",
                                                    "title": "Mix", "leafCount": 1}]}
        album = Item("3100", "album", "Warm Up", server_id="S")
        pl = self.p.playlist_create("Mix", "audio", [album])
        self.assertEqual(self.last(), ("POST", "/playlists", {
            "type": "audio", "title": "Mix", "smart": "0",
            "uri": "server://S/com.plexapp.plugins.library/library/metadata/3100"}))
        self.assertEqual((pl.id, pl.extra["kind"], pl.server_id), ("80", "audio", "S"))

    def test_items_of_another_server_are_refused(self):
        other = Item("101", "movie", "A", server_id="T")
        with self.assertRaises(plex.PlexError):
            self.p.playlist_add(self.pl, [other])
        self.assertEqual(self.calls, [])


class P:
    def __init__(self, sid, lists=None, fail=None):
        self.server_id, self.server_name = sid, sid
        self.lists, self.fail = lists or {}, fail
        self.calls = []
        self.settings = None

    def playlists(self, kind, section_id=None):
        self.calls.append((kind, section_id))
        if self.fail:
            raise self.fail
        return [Item(t, "playlist", t, server_id=self.server_id, extra={"kind": kind})
                for t in self.lists.get(kind, [])]

    def search(self, *a):
        return []

    def image(self, ref, w, h):
        return ref

    def track_info(self, t):
        return ("info", self.server_id, t.id)


class Pins:
    def __init__(self, pinned):
        self.pinned = pinned

    def pins(self, sid):
        return [6] if sid in self.pinned else []


class TestSourcesPlaylists(unittest.TestCase):
    def test_merge_with_one_server_down(self):
        a = P("A", {"video": ["Zed", "alpha"]})
        b = P("B", fail=plex.PlexError("down"))
        c = P("C", {"video": ["Mid"]})
        src = Sources([a, b, c], home_id="A")
        items, failed, asked = src.playlists("video", Pins({"B", "C"}))
        self.assertEqual([i.title for i in items], ["alpha", "Mid", "Zed"])
        self.assertEqual((failed, asked), (1, 3))
        # all down: the screen tells "Can't load" from "No playlists"
        src = Sources([P("A", fail=plex.PlexError("x"))], home_id="A")
        self.assertEqual(src.playlists("video", Pins(set())), ([], 1, 1))
        self.assertEqual(Sources([P("A")], home_id="A").playlists("audio", Pins(set())), ([], 0, 1))

    def test_expired_home_token_raises(self):
        src = Sources([P("A", fail=plex.AuthError("HTTP 401")), P("B", {"video": ["x"]})], home_id="A")
        with self.assertRaises(plex.AuthError):
            src.playlists("video", Pins({"B"}))
        # a shared server that refuses is only a failed server
        src = Sources([P("A", {"video": ["x"]}), P("B", fail=plex.AuthError("HTTP 401"))], home_id="A")
        self.assertEqual(src.playlists("video", Pins({"B"}))[1:], (1, 2))

    def test_library_tab_asks_only_its_server(self):
        a, b = P("A", {"audio": ["a"]}), P("B", {"audio": ["b"]})
        src = Sources([a, b], home_id="A")
        lib = Item("9", "library", "Music", section_id=9, server_id="B")   # 9 is on both
        self.assertEqual([i.title for i in src.lib_playlists(lib, "audio")], ["b"])
        self.assertEqual((a.calls, b.calls), ([], [("audio", 9)]))

    def test_no_home_fallback(self):
        a = P("A")
        src = Sources([a], home_id="A")
        self.assertIsNone(src.strict("T"))
        self.assertIs(src.strict(None), a)
        self.assertIsNone(src.image("T|/library/metadata/101/thumb/1", 10, 10))
        pl = Item("1", "playlist", "x", server_id="T")
        with self.assertRaises(plex.PlexError):
            src.playlist_items(pl, 0)
        r = Router(src)
        self.assertEqual(r.track_info(Item("7", "track", "t", server_id="A")), ("info", "A", "7"))
        with self.assertRaises(plex.PlexError):
            r.track_info(Item("7", "track", "t", server_id="T"))


if __name__ == "__main__":
    unittest.main()
