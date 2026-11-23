import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import channels as C  # noqa: E402
from batoplex import schedule as S  # noqa: E402

EXAMPLE = {
    "version": 1,
    "x-note": "kept",
    "channels": [
        {"id": "c7f3a1", "number": 4, "name": "SEINFELD", "order": "order", "mine": [1, 2],
         "source": {"type": "show", "server": "m1", "id": "1234", "title": "Seinfeld"}},
        {"id": "9be210", "number": 5, "name": "90S COMEDY", "order": "blocks", "block": 2,
         "source": {"type": "genre", "server": "m1", "section": 2, "genre": "Comedy"},
         "ratings": ["TV-G", "TV-PG"], "years": [1990, 1999], "max_minutes": 60},
        {"id": "44d0c2", "number": 9, "name": "WEEKEND", "blocks": [
            {"start": "06:00", "days": ["sat", "sun"], "source": {"type": "genre", "section": 2, "genre": "Animation"}},
            {"start": "20:00", "source": {"type": "library", "section": 1}}]},
    ],
}


def ep(show, e, dur=1320, rating="TV-PG", year=1995):
    return S.Entry("m1/%s%d" % (show, e), "%s%d" % (show, e), "m1", dur, "m1/" + show, "episode", 1, e,
                   "Ep", show, rating, year)


class TestParse(unittest.TestCase):
    def test_example(self):
        lu = C.parse(json.dumps(EXAMPLE))
        self.assertIsNone(lu.error)
        self.assertEqual([c.number for c in lu.channels], [4, 5, 9])
        a, b, w = lu.channels
        self.assertEqual((a.id, a.name, a.order, a.file_only), ("c7f3a1", "SEINFELD", "order", False))
        self.assertTrue(b.file_only)
        self.assertEqual(b.filters["years"], (1990, 1999))
        self.assertFalse(w.file_only)                 # days: Schedule edits them
        self.assertEqual(w.blocks[0]["start"], 360)
        self.assertEqual(w.blocks[0]["days"], frozenset({"sat", "sun"}))
        self.assertFalse(lu.needs_ids)

    def test_faults_numbers_ids_names(self):
        doc = {"channels": [
            {"id": "aaaaaa", "number": 3, "source": {"type": "show", "id": "1", "title": "Bob\x00 Show"}},
            {"id": "bbbbbb", "number": 3, "source": {"type": "show", "id": "2"}, "name": "Two"},
            {"id": "aaaaaa", "number": 7, "source": {"type": "show", "id": "3"}},      # id used before
            {"id": "cccccc", "order": "random", "source": {"type": "show", "id": "4"}},  # bad order
            {"id": "dddddd", "source": {"type": "genre", "section": 2}},               # no genre
            {"number": 8, "name": "No Id", "source": {"type": "library", "section": 1}},
            "text",
        ]}
        lu = C.parse(json.dumps(doc))
        self.assertEqual(lu.faults, 4)
        by = {c.number: c for c in lu.channels}
        self.assertEqual(by[3].id, "aaaaaa")
        self.assertEqual(by[3].name, "BOB SHOW")       # from the source title, no control character
        self.assertEqual(by[2].id, "bbbbbb")           # the later channel: the next free number
        self.assertTrue(lu.needs_ids)
        self.assertFalse(by[8].persistent)

    def test_errors_load_nothing(self):
        lu = C.parse('{"version": 1,\n "channels": [\n  {"id": oops}]}')
        self.assertEqual((lu.error, lu.line, lu.channels), ("parse", 3, []))
        self.assertEqual(C.parse('{"version": 2, "channels": []}').error, "version")
        self.assertEqual(C.parse('[]').error, "format")
        self.assertEqual(C.parse(" " * (C.MAX_BYTES + 1)).error, "size")

    def test_plan_revision_and_shared_slots(self):
        lu = C.parse(json.dumps(EXAMPLE))
        a = lu.channels[0]
        p = a.plan()
        self.assertEqual(p.slots[0].mode, "order")
        renamed = dict(EXAMPLE["channels"][0], name="OTHER", number=12)
        self.assertEqual(C.parse(json.dumps({"channels": [renamed]})).channels[0].plan().rev, p.rev)
        reordered = dict(EXAMPLE["channels"][0], order="shuffle")
        self.assertNotEqual(C.parse(json.dumps({"channels": [reordered]})).channels[0].plan().rev, p.rev)
        tb = {"id": "eeeeee", "blocks": [
            {"start": "06:00", "source": {"type": "show", "id": "1"}},
            {"start": "12:00", "source": {"type": "show", "id": "2"}},
            {"start": "18:00", "source": {"type": "show", "id": "1"}}]}
        p = C.parse(json.dumps({"channels": [tb]})).channels[0].plan()
        self.assertEqual(len(p.slots), 2)
        self.assertEqual(p.times[0][2], p.times[2][2])
        self.assertEqual(p.seed, "eeeeee")


class TestEdit(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["BATOPLEX_HOME"] = self.home
        self.n = 0

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def token(self):
        self.n += 1
        return "%06x" % (0xf00000 + self.n)

    def put(self, data):
        with open(C.path(), "w" if isinstance(data, str) else "wb") as f:
            f.write(data)
        return C.load().stamp

    def get(self):
        with open(C.path(), "rb") as f:
            return f.read()

    def test_round_trip_keeps_unknown_keys_and_invalid_entries(self):
        doc = json.loads(json.dumps(EXAMPLE))
        doc["channels"].insert(1, {"id": "bad", "number": "x", "note": "hand"})      # invalid: kept
        stamp = self.put(json.dumps(doc))
        C.save_channel({"id": "c7f3a1", "name": "JERRY", "order": None}, stamp, self.token)
        out = json.loads(self.get())
        self.assertEqual(out["x-note"], "kept")
        self.assertEqual(out["channels"][0]["mine"], [1, 2])
        self.assertEqual(out["channels"][0]["name"], "JERRY")
        self.assertNotIn("order", out["channels"][0])
        self.assertEqual(out["channels"][1]["note"], "hand")
        self.assertEqual(out["channels"][2], EXAMPLE["channels"][1])
        self.assertEqual(out["channels"][3], EXAMPLE["channels"][2])
        lu = C.load()
        self.assertEqual(lu.channels[0].name, "JERRY")

    def test_broken_file_is_never_written(self):
        bad = b'{"version": 1, "channels": [ {"id": "c7f3a1",, } ]}'
        stamp = self.put(bad)
        self.assertEqual(C.load().error, "parse")
        for call in (lambda: C.save_channel({"name": "X", "source": {"type": "library", "section": 1}}, stamp),
                     lambda: C.add_channels([{"number": 2}], stamp),
                     lambda: C.delete_channel("c7f3a1", stamp),
                     lambda: C.ensure_ids(stamp)):
            with self.assertRaises(C.Refused):
                call()
        self.assertEqual(self.get(), bad)
        stamp = self.put('{"version": 3, "channels": []}')
        with self.assertRaises(C.Refused):
            C.add_channels([{"number": 2}], stamp)

    def test_changed_file_refuses_a_save(self):
        stamp = self.put(json.dumps(EXAMPLE))
        os.utime(C.path(), ns=(1, 1))                  # a hand edit after the read
        with self.assertRaises(C.Refused):
            C.save_channel({"id": "c7f3a1", "name": "X"}, stamp)

    def test_new_file_ids_and_swap(self):
        ids = C.add_channels([{"number": 2, "name": "A", "source": {"type": "library", "section": 1}},
                              {"number": 3, "name": "B", "source": {"type": "show", "id": "9"}}],
                             None, self.token)
        self.assertEqual(ids, ["f00001", "f00002"])
        lu = C.load()
        C.swap_numbers("f00001", "f00002", lu.stamp)
        self.assertEqual({c.id: c.number for c in C.load().channels}, {"f00001": 3, "f00002": 2})
        C.delete_channel("f00001", C.load().stamp)
        self.assertEqual([c.id for c in C.load().channels], ["f00002"])

    def test_ensure_ids(self):
        doc = {"channels": [{"number": 2, "source": {"type": "library", "section": 1}},
                            {"id": "abcdef", "number": 3, "source": {"type": "library", "section": 2}},
                            {"id": "ABCDEF", "number": 4, "source": {"type": "library", "section": 3}}]}
        stamp = self.put(json.dumps(doc))
        self.assertTrue(C.load().needs_ids)
        self.assertTrue(C.ensure_ids(stamp, self.token))
        lu = C.load()
        self.assertFalse(lu.needs_ids)
        self.assertEqual(sorted(c.id for c in lu.channels), ["abcdef", "f00001", "f00002"])
        self.assertFalse(C.ensure_ids(lu.stamp, self.token))

    def test_symlink_and_mode_stay(self):
        real = os.path.join(self.home, "real.json")
        with open(real, "w") as f:
            f.write(json.dumps(EXAMPLE))
        os.chmod(real, 0o600)
        os.symlink(real, C.path())
        C.save_channel({"id": "c7f3a1", "name": "X"}, C.load().stamp)
        self.assertTrue(os.path.islink(C.path()))
        self.assertEqual(os.stat(real).st_mode & 0o777, 0o600)
        with open(real) as f:
            self.assertEqual(json.load(f)["channels"][0]["name"], "X")
        self.assertEqual([n for n in os.listdir(self.home) if n.startswith(".channels-")], [])


class TestTheme(unittest.TestCase):
    SRC = {"type": "theme", "theme": "scifi", "kind": "show", "title": "Sci-Fi",
           "sections": [["m2", "4"], ["m1", 2], ["m1", 2]]}

    def test_norm_key_and_order_choices(self):
        s = C.norm_source(self.SRC)
        self.assertEqual(s["sections"], [["m1", 2], ["m2", 4]])            # sorted, one each
        other = dict(self.SRC, sections=[["m1", 2], ["m2", 4]], title="Other")
        self.assertEqual(C.source_key(s), C.source_key(C.norm_source(other)))
        self.assertNotEqual(C.source_key(s), C.source_key(C.norm_source(dict(other, kind="movie"))))
        for bad in (dict(self.SRC, theme="Sci Fi"), dict(self.SRC, kind="music"),
                    dict(self.SRC, sections=[]), dict(self.SRC, sections=[["m1", 0]])):
            self.assertIsNone(C.norm_source(bad))
        self.assertEqual(C.source_lib_type(s), "show")
        self.assertEqual(C.source_lib_type(dict(s, kind="movie")), "movie")
        self.assertEqual(C.source_lib_type({k: v for k, v in s.items() if k != "kind"}), "show")
        self.assertEqual(C.source_lib_type({"type": "genre"}, "movie"), "movie")
        cat = C.catalog([], [], [{"source": dict(s, kind="movie"), "title": "Sci-Fi Movies",
                                  "count": 40, "what": "movies"}])
        e = cat["theme"][0]
        self.assertEqual(C.order_choices("theme", e["lib_type"]), ["shuffle"])
        self.assertEqual(C.draft(dict(e, source=s, lib_type=None), [])["order"], "blocks")
        q = C.query_for(s)
        self.assertEqual((q.theme, q.media, q.sections, q.canonical, q.show_cap),
                         ("scifi", "show", (("m1", 2), ("m2", 4)), True, 60))

    def test_unknown_source_drops_only_that_channel(self):
        doc = {"version": 1, "channels": [
            {"id": "aaaaaa", "number": 2, "source": {"type": "theme", "theme": "scifi",
                                                      "sections": [["m1", 2]]}},
            {"id": "bbbbbb", "number": 3, "source": {"type": "someday", "x": 1}},
            {"id": "cccccc", "number": 4, "blocks": [
                {"start": "04:00", "source": {"type": "library", "section": 1}},
                {"start": "20:00", "source": {"type": "library", "section": 2}, "once": True}]}]}
        lu = C.parse(json.dumps(doc))
        self.assertEqual((lu.error, lu.faults, [c.id for c in lu.channels]),
                         (None, 1, ["aaaaaa", "cccccc"]))
        tb = lu.channels[1]
        rule = tb.rule()
        self.assertNotIn("once", rule["blocks"][0])      # only when true: old revisions stay
        self.assertTrue(rule["blocks"][1]["once"])
        self.assertEqual([t[3] for t in tb.plan().times], [False, True])

    @staticmethod
    def doc():
        with open(C.path()) as f:
            return json.load(f)

    def test_add_channels_keeps_blocks_and_clear(self):
        home = tempfile.mkdtemp()
        old = os.environ["BATOPLEX_HOME"]
        os.environ["BATOPLEX_HOME"] = home
        try:
            bl = [C.block_raw(240, None, {"type": "library", "section": 1}, "blocks"),
                  C.block_raw(1200, ("fri",), {"type": "library", "section": 2}, "shuffle", True, 1)]
            C.add_channels([{"number": 2, "name": "A", "blocks": bl}], None)
            raw = self.doc()["channels"][0]
            self.assertEqual(raw["blocks"][1], {"start": "20:00", "days": ["fri"], "order": "shuffle",
                                                "block": 1, "once": True,
                                                "source": {"type": "library", "section": 2}})
            ch = C.load().channels[0]
            self.assertTrue(ch.blocks[1]["once"])
            C.save_block(ch.id, 1, dict(ch.blocks[1], once=False), C.load().stamp)
            self.assertNotIn("once", self.doc()["channels"][0]["blocks"][1])
            with open(C.path()) as f:
                d = json.load(f)
            d["x-note"] = "kept"
            with open(C.path(), "w") as f:
                json.dump(d, f)
            C.clear(C.load().stamp)
            d = self.doc()
            self.assertEqual((d["channels"], d["x-note"], d["version"]), ([], "kept", 1))
        finally:
            os.environ["BATOPLEX_HOME"] = old
            shutil.rmtree(home, ignore_errors=True)


class TestPools(unittest.TestCase):
    def lineup(self):
        doc = {"channels": [
            {"id": "aaaaaa", "number": 2, "source": {"type": "genre", "server": "m1", "section": 2, "genre": "Comedy"}},
            {"id": "bbbbbb", "number": 3, "source": {"type": "genre", "server": "m1", "section": 2, "genre": "comedy"},
             "max_minutes": 25},
            {"id": "cccccc", "number": 4, "blocks": [
                {"start": "06:00", "source": {"type": "show", "server": "m1", "id": "7"}},
                {"start": "20:00", "source": {"type": "genre", "server": "m1", "section": 2, "genre": "Comedy"}}]},
            {"id": "dddddd", "number": 5, "source": {"type": "playlist", "server": "m2", "id": "p1"}},
        ]}
        return C.parse(json.dumps(doc)).channels

    def test_shared_queries_once_sorted_unique(self):
        qs = C.plan_queries(self.lineup())
        self.assertEqual(len(qs), 3)                   # Comedy (3 users, any case), show 7, playlist
        self.assertEqual([q.key for q in qs], sorted(q.key for q in qs))
        self.assertEqual(len({q.key for q in qs}), 3)
        g = next(q for q in qs if q.kind == "genre")
        self.assertTrue(g.canonical)
        self.assertFalse(g.repeats)
        self.assertEqual((g.cap, g.read_cap), (S.MAX_POOL, 2 * S.MAX_POOL))
        self.assertTrue(next(q for q in qs if q.kind == "playlist").repeats)
        self.assertEqual(C.plan_queries(list(reversed(self.lineup()))), qs)

    def test_collect_keeps_previous_on_failure_and_deadline(self):
        qs = C.plan_queries(self.lineup())
        calls = []

        def fetch(q, deadline):
            calls.append(q.key)
            if q.kind == "show":
                raise RuntimeError("boom")
            return None if q.kind == "playlist" else [ep("a", 1)]
        prev = {qs[1].key: [ep("old", 1)]}
        res, failed = C.collect(qs, fetch, previous=prev)
        self.assertEqual(calls, [q.key for q in qs])
        self.assertEqual(sorted(failed), sorted(q.key for q in qs if q.kind != "genre"))
        for q in qs:
            if q.kind == "genre":
                self.assertEqual(res[q.key], [ep("a", 1)])
        self.assertEqual(res.get(qs[1].key), prev.get(qs[1].key))
        t = [0]
        skipped = []
        res, failed = C.collect(qs, fetch, deadline=5, clock=lambda: t[0] + 10, skipped=skipped)
        self.assertEqual((res, len(failed), skipped), ({}, 3, [q.key for q in qs]))
        skipped = []
        C.collect(qs, fetch, skipped=skipped)
        self.assertEqual(skipped, [])                  # a fetch error is not a skip

    def test_channel_pools_filters_and_missing_result(self):
        chans = self.lineup()
        qs = C.plan_queries(chans)
        g = next(q for q in qs if q.kind == "genre")
        es = [ep("a", 1, 1200), ep("a", 2, 1800), ep("b", 1, 1300)]
        res = {g.key: es}
        p = C.channel_pools(chans[1], res)             # max 25 minutes
        self.assertEqual([u.key for u in list(p.values())[0].units], ["m1/a1", "m1/b1"])
        self.assertIsNone(C.channel_pools(chans[2], res))   # the show source has no result
        f = C.apply_filters(es + [ep("c", 1, rating="R"), ep("d", 1, year=2010)],
                            {"ratings": frozenset({"tv-pg"}), "years": (1990, 1999)})
        self.assertEqual(len(f), 3)

    def test_cache_key_has_the_account(self):
        self.assertNotEqual(C.cache_key("acct1", "default"), C.cache_key("acct2", "default"))
        self.assertEqual(len(C.cache_key("a", "b")), 16)


def lib(section, kind, title, **kw):
    d = {"server": "m1", "section": section, "type": kind, "title": title, "episodes": 0, "movies": 0,
         "shows": [], "genres": [], "collections": []}
    d.update(kw)
    return d


def show(i, title, eps, net, mins=22):
    return {"id": str(i), "title": title, "episodes": eps, "network": net, "minutes": eps * mins}


class TestPropose(unittest.TestCase):
    def libs(self):
        shows = [show(1, "Seinfeld", 180, "NBC"), show(2, "Friends", 236, "NBC"),
                 show(3, "The Simpsons", 700, "FOX"), show(4, "Futurama", 140, "FOX"),
                 show(5, "Firefly", 14, "FOX", 44),
                 show(6, "Twin Peaks Extraordinarily Long", 48, "ABC", 50),
                 show(7, "Lost", 121, "ABC", 43)]
        tv = lib(1, "show", "TV Shows", episodes=sum(s["episodes"] for s in shows),
                 minutes=sum(s["minutes"] for s in shows), shows=shows,
                 genres=[{"genre": "Comedy", "count": 5}, {"genre": "Drama", "count": 3},
                         {"genre": "Anime", "count": 1}])
        mov = lib(2, "movie", "Movies", movies=300, minutes=300 * 110,
                  genres=[{"genre": "Action", "count": 120}, {"genre": "Drama", "count": 200},
                          {"genre": "Western", "count": 9}],
                  collections=[{"id": "c1", "title": "Star Wars", "count": 40},
                               {"id": "c2", "title": "Alien", "count": 4},
                               {"id": "c3", "title": "Back to the Future", "count": 3},
                               {"id": "c4", "title": "Rocky", "count": 30}])
        small = lib(3, "movie", "Home Videos", movies=4)
        return [tv, mov, small]

    PL = [{"server": "m1", "id": "p1", "title": "Sunday", "minutes": 3000, "video": True},
          {"server": "m1", "id": "p2", "title": "Short", "minutes": 200, "video": True},
          {"server": "m1", "id": "p3", "title": "Songs", "minutes": 9000, "video": False}]

    def test_every_qualifying_source_no_ceiling(self):
        out = C.propose(self.libs(), self.PL)
        names = {o["channel"]["name"]: o for o in out}
        # the 48 h floor: Twin Peaks (40 h), Alien (4 x 110 min), the short playlist are out;
        # Firefly (14 episodes) and Back to the Future (3) miss their kind's minimum
        for n in ("TWIN PEAKS", "ALIEN", "SHORT", "FIREFLY", "BACK TO THE", "HOME VIDEOS", "SONGS"):
            self.assertNotIn(n, names)
        self.assertEqual(sorted(o["kind"] for o in out).count("show"), 5)
        # Comedy 5 of 7 shows and Drama movies 200 of 300 are over 60 % of their library
        self.assertEqual(sorted((o["channel"]["source"]["genre"], o["what"]) for o in out
                                if o["kind"] == "genre"), [("Action", "movies"), ("Drama", "shows")])
        self.assertEqual([o["channel"]["number"] for o in out], list(range(2, 2 + len(out))))
        self.assertTrue(all(o["score"] == o["minutes"] >= C.POOL_MIN_MINUTES for o in out))
        self.assertTrue(all(len(n) <= 16 for n in names))
        self.assertEqual(names["TV SHOWS"]["channel"]["order"], "blocks")
        self.assertEqual(names["MOVIES"]["channel"]["order"], "shuffle")
        self.assertEqual(names["LOST"]["channel"]["order"], "order")
        many = lib(9, "show", "Big", episodes=60000, shows=[
            show(100 + i, "Show %d" % i, 200, "") for i in range(300)])
        self.assertEqual(len(C.propose([many])), 301)                 # no ceiling
        self.assertFalse(any(o["default"] for o in out))             # suggestions only

    def test_names(self):
        self.assertEqual(C.make_name("Twin Peaks Extraordinarily Long"), "TWIN PEAKS")
        self.assertEqual(C.make_name("Supercalifragilisticexpialidocious"), "SUPERCALIFRAGILI")
        # only weak words fit: a hard cut with no leading "THE"; a weak head gives its subtitle
        self.assertEqual(C.make_name("The Supercalifragilisticexpialidocious"), "SUPERCALIFRAGILI")
        self.assertEqual(C.make_name("The: A long subtitle here ok"), "A LONG SUBTITLE")
        self.assertEqual(C.make_name("Drama", ["drama"]), "DRAMA 2")
        self.assertEqual(C.make_name("Drama", ["DRAMA", "DRAMA 2"]), "DRAMA 3")
        self.assertEqual(C.make_name("Sixteen chars ok", ["SIXTEEN CHARS OK"]), "SIXTEEN CHARS 2")
        self.assertEqual(C.make_name("Star Trek: The Next Generation"), "STAR TREK: TNG")
        self.assertEqual(C.make_name("Star Trek: The Original Series"), "STAR TREK: TOS")
        self.assertEqual(C.make_name("BBC Best of the Eighties"), "BBC BEST")      # no "THE" at the end
        self.assertEqual(C.make_name("The Twilight Zone Remastered"), "TWILIGHT ZONE")
        self.assertEqual(C.make_name("Buffy the Vampire Slayer"), "BUFFY")
        self.assertEqual(C.make_name("The Hitchhiker's Guide to the Galaxy"), "THE HITCHHIKER'S")
        self.assertEqual(C.make_name("Spider-Man: Into the Spider-Verse"), "SPIDER-MAN")
        self.assertEqual(C.make_name("Star Wars: Episode IV A New Hope"), "STAR WARS")
        self.assertEqual(C.make_name("The 6:00 Something Long Show"), "6:00 SOMETHING")
        self.assertEqual(C.make_name(": Something very long title"), "SOMETHING VERY")
        self.assertEqual(C.make_name("-" * 22), "-" * 16)                # never empty
        for t in ("The Lord of the Rings: The Return of the King", "A B C D E F G H I J K L M N"):
            n = C.make_name(t)
            self.assertTrue(0 < len(n) <= C.NAME_MAX and not n.endswith((":", " ")), n)

    def test_existing_sources_numbers_and_names_are_kept(self):
        first = C.propose(self.libs())[:3]
        doc = {"channels": [dict(o["channel"], id="%06x" % i) for i, o in enumerate(first)]}
        doc["channels"][0]["number"] = 9
        existing = C.parse(json.dumps(doc)).channels
        again = C.propose(self.libs(), existing=existing)
        keys = {C.source_key(C.norm_source(o["channel"]["source"])) for o in again}
        self.assertFalse(keys & {C.source_key(c.source) for c in existing})
        self.assertEqual([o["channel"]["number"] for o in again][:3], [2, 5, 6])   # 3, 4, 9 are used
        # the proposal saves and loads back as valid channels
        doc["channels"] += [dict(o["channel"], id="%06x" % (100 + i)) for i, o in enumerate(again)]
        lu = C.parse(json.dumps(doc))
        self.assertEqual((lu.faults, len(lu.channels)), (0, len(doc["channels"])))


if __name__ == "__main__":
    unittest.main()
