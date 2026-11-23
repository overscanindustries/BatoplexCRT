"""Live TV lineup: the fetch skip by section stamp, the freeze timing, the tune offset rules,
channel up/down/last."""
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import channels as C  # noqa: E402
from batoplex import lineup as LU  # noqa: E402
from batoplex import schedule as S  # noqa: E402
from batoplex import themes as T  # noqa: E402
from batoplex.model import Item  # noqa: E402

NY = "America/New_York"


def loc(y, m, d, h, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=S.zone(NY)).timestamp())


class FakeProv:
    server_id = "srv"

    def __init__(self, stamp=1):
        self.stamp, self.calls = stamp, []

    def load_sections(self):
        return [{"id": 1, "type": "show", "title": "TV", "stamp": self.stamp}]

    def listing(self, path, params=None, cap=0, deadline=None):
        self.calls.append(path)
        return [Item(id=str(100 + i), type="episode", title="E%d" % i, duration_ms=1800000,
                     show_id="9", season_index=1, index=i, server_id="srv") for i in range(1, 4)]


class FakeSources:
    def __init__(self, p):
        self.p = p

    def strict(self, sid):
        return self.p


class TestFetch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_unchanged_section_skips_the_list(self):
        q = C.query_for({"type": "library", "server": "", "section": 1})
        p = FakeProv(stamp=7)
        f = LU.Fetcher(FakeSources(p), LU.Store(self.d))
        self.assertEqual(len(f.fetch(q, None)), 3)
        self.assertEqual(len(p.calls), 1)
        f2 = LU.Fetcher(FakeSources(p), LU.Store(self.d))      # the next day, same stamp
        out = f2.fetch(q, None)
        self.assertEqual((len(out), len(p.calls), f2.reused), (3, 1, 1))
        self.assertEqual(out[0].key, "srv/101")
        p.stamp = 8                                             # the library changed
        LU.Fetcher(FakeSources(p), LU.Store(self.d)).fetch(q, None)
        self.assertEqual(len(p.calls), 2)

    def test_reasons(self):
        q = C.query_for({"type": "library", "server": "", "section": 5})
        f = LU.Fetcher(FakeSources(FakeProv()), LU.Store(self.d))
        self.assertIsNone(f.fetch(q, None))
        self.assertEqual(f.reasons[q.key], LU.MISSING)
        f = LU.Fetcher(FakeSources(None), LU.Store(self.d))
        self.assertIsNone(f.fetch(q, None))
        self.assertEqual(f.reasons[q.key], LU.OFFLINE)


class ThemeProv:
    """One server with one section of invented titles; genre 7 Animation, 8 Comedy."""

    def __init__(self, sid, kind, rows, stamp=5, genre_fail=False, leaves=None):
        self.server_id, self.kind, self.rows, self.stamp = sid, kind, rows, stamp
        self.genre_fail, self.leaves, self.calls = genre_fail, leaves or {}, []
        self.max_page, self.no_total, self.lie, self.slow = None, False, 0, 0.0

    def load_sections(self):
        return [{"id": 3, "type": self.kind, "title": "Lib", "stamp": self.stamp}]

    def get(self, path, params=None, start=None, size=None, timeout=8):
        self.calls.append((path, dict(params or {})))
        if path.endswith("/genre"):
            if self.genre_fail:
                raise RuntimeError("down")
            return {"Directory": [{"key": "7", "title": "Animation"}, {"key": "8", "title": "Comedy"}]}
        md = self.rows
        g = (params or {}).get("genre")
        if g:
            md = [m for m in md if set(m["_g"]) & set(g.split(","))]
        elif self.slow:
            time.sleep(self.slow)
        st = start or 0
        n = min(size or len(md), self.max_page or len(md) or 1)
        mc = {"Metadata": md[st:st + n]}
        if not self.no_total:
            mc["totalSize"] = len(md) + self.lie
        return mc

    def listing(self, path, params=None, cap=0, deadline=None):
        self.calls.append((path, None))
        v = self.leaves[path.split("/")[3]]
        if isinstance(v, Exception):
            raise v
        return v


def film(rk, dur, genres=("8",), rating="PG-13"):
    return {"ratingKey": rk, "type": "movie", "title": "Film %s" % rk, "duration": dur,
            "contentRating": rating, "Genre": [{"tag": "Comedy"}], "_g": list(genres)}


class TestThemeFetch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def q(self, kind="movie", sections=(("a", 3), ("b", 3))):
        return C.query_for(C.norm_source({"type": "theme", "theme": "comedy", "kind": kind,
                                          "sections": [list(s) for s in sections]}))

    def test_index_sets_and_complete_flags(self):
        p = ThemeProv("a", "movie", [film("1", 5400000), film("2", 6000000, ("7", "8"))])
        ix = LU.index(p, 3, "movie", time.monotonic() + 30, "Lib")
        self.assertTrue(ix["rows_complete"] and ix["dir_ok"])
        self.assertEqual(ix["sets"], {"animation": {"ids": frozenset({"2"}), "complete": True}})
        self.assertEqual(ix["action"], {"ids": frozenset(), "complete": True})   # no action genre
        self.assertEqual(ix["rows"][0].genres, ("Comedy",))
        light = [c[1] for c in p.calls if c[0].endswith("/all")]
        self.assertTrue(all(c["excludeFields"] == "summary,tagline" for c in light))
        late = LU.index(p, 3, "movie", time.monotonic() - 1, "Lib")
        self.assertFalse(late["rows_complete"])
        self.assertFalse(late["sets"]["animation"]["complete"])
        self.assertEqual(LU.summary_of(ix)["genres"], [{"genre": "Comedy", "count": 2, "minutes": 190}])

    def test_two_servers_one_theme_exact_durations_and_store(self):
        a = ThemeProv("a", "movie", [film("1", 5432109), film("2", 6000000, ("7", "8"))])
        b = ThemeProv("b", "movie", [film("1", 4000000)])
        src = FakeMap({"a": a, "b": b})
        f = LU.Fetcher(src, LU.Store(self.d))
        out = f.fetch(self.q(), time.monotonic() + 30)
        self.assertEqual(sorted((e.key, e.dur) for e in out), [("a/1", 5432), ("b/1", 4000)])
        it = LU.row_item(LU._row(film("1", 5432109)), "a")
        self.assertEqual((it.duration_ms, it.server_id), (5432109, "a"))
        n = len(a.calls)
        f2 = LU.Fetcher(src, LU.Store(self.d))                     # same stamps: no request
        self.assertEqual(len(f2.fetch(self.q(), time.monotonic() + 30)), 2)
        self.assertEqual((len(a.calls), f2.reused), (n, 1))
        b.stamp = 6
        LU.Fetcher(src, LU.Store(self.d)).fetch(self.q(), time.monotonic() + 30)
        self.assertGreater(len(a.calls), n)
        src.m.pop("b")                                             # a server that does not answer
        f3 = LU.Fetcher(src, LU.Store(self.d))
        self.assertIsNone(f3.fetch(self.q(), None))
        self.assertEqual(f3.reasons[self.q().key], LU.OFFLINE)
        f4 = LU.Fetcher(src, LU.Store(self.d))                     # a section that is gone
        self.assertIsNone(f4.fetch(self.q(sections=(("a", 4),)), None))
        self.assertEqual(f4.reasons[self.q(sections=(("a", 4),)).key], LU.MISSING)

    def test_incomplete_index_is_not_stored(self):
        a = ThemeProv("a", "movie", [film("1", 5400000)], genre_fail=True)
        store = LU.Store(self.d)
        f = LU.Fetcher(FakeMap({"a": a}), store)
        q = self.q(sections=(("a", 3),))
        self.assertIsNone(f.fetch(q, time.monotonic() + 30))
        self.assertEqual(f.reasons[q.key], LU.OFFLINE)
        self.assertIsNone(store.get(q.key))

    def test_show_theme_leaves_and_seen(self):
        eps = [Item(id=str(500 + i), type="episode", title="E%d" % i, duration_ms=1320000,
                    show_id="9", season_index=1, index=i, server_id="a", watched=i < 3)
               for i in range(1, 6)]
        show = {"ratingKey": "9", "type": "show", "title": "Show", "duration": 1320000,
                "leafCount": 5, "Genre": [{"tag": "Comedy"}], "_g": ["8"]}
        a = ThemeProv("a", "show", [show], leaves={"9": eps})
        f = LU.Fetcher(FakeMap({"a": a}), LU.Store(self.d))
        q = self.q("show", (("a", 3),))
        out = f.fetch(q, time.monotonic() + 30)
        self.assertEqual([e.key for e in out], ["a/%d" % (500 + i) for i in range(1, 6)])
        self.assertEqual(f.seen[q.key], ["a/501", "a/502"])
        f2 = LU.Fetcher(FakeMap({"a": a}), LU.Store(self.d))
        f2.fetch(q, None)
        self.assertEqual(f2.seen[q.key], ["a/501", "a/502"])      # kept beside the entries


class TestThemeFixes(unittest.TestCase):
    """Page ends, the order of the index reads, the classify memo, unknown themes,
    a show that is gone, request counts, the freeze deadline."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_capped_server_with_an_empty_window_mid_list(self):
        rows = [film(str(i), 5400000, ("7",)) for i in range(250)]

        class Capped(ThemeProv):
            """100-row pages and totalSize 250; the window at 100 is all restricted (empty)."""

            def get(self, path, params=None, start=None, size=None, timeout=8):
                mc = ThemeProv.get(self, path, params, start, size, timeout)
                if start == 100:
                    mc["Metadata"] = []
                return mc
        p = Capped("a", "movie", rows)
        p.max_page = 100
        conv = (lambda m: m["ratingKey"])
        got, ok = LU._pages(p, "/library/sections/3/all", {}, 1000, None, conv, pause=0)
        self.assertEqual((len(got), ok), (100, False))      # 100 of 500 is not short: not the end
        got, ok = LU._pages(p, "/library/sections/3/all", {"genre": "7"}, 1000, None, conv,
                            pause=0, early=False)
        self.assertEqual((len(got), ok), (100, False))      # an id set: never ends early
        p.no_total = True
        got, ok = LU._pages(p, "/library/sections/3/all", {"genre": "7"}, 1000, None, conv,
                            pause=0, early=False)
        self.assertFalse(ok)                                # no totalSize: only an empty list
        p.rows = []
        self.assertEqual(LU._pages(p, "/x", {"genre": "7"}, 10, None, conv, pause=0, early=False),
                         ([], True))

    def test_dropped_body_is_a_network_error(self):
        import http.client
        from batoplex import plex

        class Opener:
            def open(self, req, timeout=8):
                raise http.client.IncompleteRead(b"x")
        real, plex._opener = plex._opener, Opener()
        try:
            with self.assertRaises(plex.PlexError) as cm:
                plex._request("http://h/library/sections/1/all", {})
        finally:
            plex._opener = real
        self.assertEqual((str(cm.exception), cm.exception.code), ("network: IncompleteRead", None))

    def test_pages_end_at_total_size(self):
        rows = [film(str(i), 5400000, ("7",)) for i in range(5)]
        p = ThemeProv("a", "movie", rows)
        p.max_page = 2                                # a proxy caps the page under 500
        got, ok = LU._pages(p, "/library/sections/3/all", {}, 100, None, lambda m: m["ratingKey"],
                            pause=0)
        self.assertEqual((len(got), ok), (5, True))
        p.no_total = True                             # no totalSize: on to an empty page
        got, ok = LU._pages(p, "/library/sections/3/all", {}, 100, None, lambda m: m["ratingKey"],
                            pause=0)
        self.assertEqual((len(got), ok), (5, True))
        p.no_total, p.lie = False, 3                  # fewer rows than totalSize said: the
        got, ok = LU._pages(p, "/library/sections/3/all", {}, 100, None, lambda m: m["ratingKey"],
                            pause=0)                  # empty page after a short one ends it
        self.assertEqual((len(got), ok), (5, True))
        p.max_page = None                             # an empty page after a full one: not
        got, ok = LU._pages(p, "/library/sections/3/all", {}, 100, None, lambda m: m["ratingKey"],
                            page=5, pause=0)          # complete
        self.assertEqual((len(got), ok), (5, False))

    def test_sets_first_then_partial_rows_still_classify(self):
        rows = [film(str(i), 5400000, ("7",) if i % 2 else ("8",)) for i in range(6)]
        p = ThemeProv("a", "movie", rows)
        p.max_page, p.slow = 1, 0.15                  # row pages are slow: the deadline cuts them
        ix = LU.index(p, 3, "movie", time.monotonic() + 0.4, "Lib")
        self.assertTrue(ix["sets"]["animation"]["complete"])
        self.assertFalse(ix["rows_complete"])
        self.assertTrue(0 < len(ix["rows"]) < 6)
        self.assertEqual(len(T.classify_index(ix)), len(ix["rows"]))   # proposals still see them
        self.assertIsNone(T.members(ix, "comedy"))                       # never a stored pool
        self.assertEqual(ix["requests"], len(p.calls))

    def test_memo_unknown_theme_gone_show_and_counts(self):
        from batoplex.plex import PlexError
        shows = [{"ratingKey": str(9 + i), "type": "show", "title": "S", "duration": 1320000,
                  "leafCount": 2, "Genre": [{"tag": "Comedy"}], "_g": ["8"]} for i in range(2)]
        eps = [Item(id="500", type="episode", title="E", duration_ms=1320000, show_id="10",
                    season_index=1, index=1, server_id="a")]
        a = ThemeProv("a", "show", shows, leaves={"9": PlexError("HTTP 404 for x", code=404), "10": eps})
        calls = []
        real = T.classify_index
        T.classify_index = lambda ix: calls.append(1) or real(ix)
        try:
            f = LU.Fetcher(FakeMap({"a": a}), LU.Store(self.d))
            src = {"type": "theme", "theme": "comedy", "sections": [["a", 3]]}
            out = f.fetch(C.query_for(C.norm_source(src)), None)
            f.fetch(C.query_for(C.norm_source(dict(src, kind="show"))), None)
        finally:
            T.classify_index = real
        self.assertEqual([e.key for e in out], ["a/500"])           # show 9 is gone: passed over
        self.assertEqual(len(calls), 1)                             # one pass for both themes
        self.assertEqual(f.requests, len(a.calls) + 1)              # + the sections read
        q = C.query_for(C.norm_source(dict(src, theme="newer_theme")))
        self.assertIsNone(f.fetch(q, None))
        self.assertEqual(f.reasons[q.key], LU.MISSING)

    def test_server_error_is_not_a_gone_show(self):
        from batoplex.plex import PlexError
        show = {"ratingKey": "14041", "type": "show", "title": "S", "duration": 1320000,
                "leafCount": 2, "Genre": [{"tag": "Comedy"}], "_g": ["8"]}
        err = PlexError("HTTP 500 for /library/metadata/14041/allLeaves", code=500)
        a = ThemeProv("a", "show", [show], leaves={"14041": err})
        f = LU.Fetcher(FakeMap({"a": a}), LU.Store(self.d))
        q = C.query_for(C.norm_source({"type": "theme", "theme": "comedy",
                                       "sections": [["a", 3]]}))
        self.assertIsNone(f.fetch(q, None))
        self.assertEqual(f.reasons[q.key], LU.OFFLINE)
        self.assertIsNone(LU.Store(self.d).get(q.key))

    def test_a_fetch_error_is_not_retried_and_failed_queries_go_last(self):
        home = tempfile.mkdtemp()
        old = os.environ["BATOPLEX_HOME"]
        os.environ["BATOPLEX_HOME"] = home

        class Bad(FakeProv):
            fail = True

            def listing(self, path, params=None, cap=0, deadline=None):
                if self.fail and "sections" in path:
                    self.calls.append(path)
                    raise ValueError("bad data")          # not a server error, not the deadline
                return FakeProv.listing(self, path, params, cap, deadline)
        try:
            C.add_channels([{"number": 2, "name": "A", "order": "order",
                             "source": {"type": "library", "server": "srv", "section": 1}},
                            {"number": 3, "name": "B", "order": "order",
                             "source": {"type": "show", "server": "srv", "id": "9"}}], None)
            rules = C.load()
            a, b = rules.channels
            lu = LU.Lineup(lambda fn: None, clock=lambda: loc(2026, 5, 1, 12), zone=S.zone(NY))
            p = Bad()
            with self.assertLogs("batoplex.channels", "INFO"):
                res = lu.build(rules, "k", FakeSources(p))
            self.assertEqual((res["status"][a.id], res["status"][b.id], res["retry"]),
                             (LU.EMPTY, LU.ON, False))
            self.assertEqual(sorted(p.calls), ["/library/metadata/9/allLeaves",
                                               "/library/sections/1/all"])   # 2 at once
            p.calls[:] = []
            p.fail = False
            res = lu.build(rules, "k", FakeSources(p), mem={b.id: res["caches"][b.id]})
            self.assertEqual(p.calls, ["/library/sections/1/all"])   # the show is frozen today
            self.assertEqual(res["status"][a.id], LU.ON)
        finally:
            os.environ["BATOPLEX_HOME"] = old
            shutil.rmtree(home, ignore_errors=True)

    def test_failed_queries_are_fetched_last(self):
        root = tempfile.mkdtemp()
        try:
            LU._write_json(os.path.join(root, LU.FAILED_FILE), {"failed": ["x"]})
            self.assertEqual(LU.Lineup._failed(root), {"x"})
            qs = [types.SimpleNamespace(key=k) for k in ("w", "x", "y")]
            qs.sort(key=lambda q: q.key in LU.Lineup._failed(root))
            self.assertEqual([q.key for q in qs], ["w", "y", "x"])
            self.assertEqual(LU.Lineup._failed(os.path.join(root, "none")), set())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_queries_past_the_deadline_retry_soon(self):
        home = tempfile.mkdtemp()
        old, old_d = os.environ["BATOPLEX_HOME"], LU.DEADLINE_S
        os.environ["BATOPLEX_HOME"] = home
        try:
            C.add_channels([{"number": 2, "name": "A", "order": "order",
                             "source": {"type": "library", "server": "srv", "section": 1}}], None)
            rules = C.load()
            lu = LU.Lineup(lambda fn: None, clock=lambda: loc(2026, 5, 1, 12), zone=S.zone(NY))
            LU.DEADLINE_S = -1                          # every query is past the deadline
            res = lu.build(rules, "k", FakeSources(FakeProv()))
            self.assertEqual(res["status"][rules.channels[0].id], LU.OFFLINE)
            self.assertTrue(res["retry"])
            LU.DEADLINE_S = old_d
            res = lu.build(rules, "k", FakeSources(FakeProv()))
            self.assertEqual(res["status"][rules.channels[0].id], LU.ON)
        finally:
            LU.DEADLINE_S = old_d
            os.environ["BATOPLEX_HOME"] = old
            shutil.rmtree(home, ignore_errors=True)


class FakeMap:
    def __init__(self, m):
        self.m = m

    def strict(self, sid):
        return self.m.get(sid or "")


class TestTiming(unittest.TestCase):
    def test_next_freeze(self):
        tz = S.zone(NY)
        self.assertEqual(LU.next_freeze(loc(2026, 3, 7, 23), tz), loc(2026, 3, 8, 4) + 30)
        self.assertEqual(LU.next_freeze(loc(2026, 3, 8, 3, 59), tz), loc(2026, 3, 8, 4) + 30)
        self.assertEqual(LU.next_freeze(loc(2026, 3, 8, 4, 1), tz), loc(2026, 3, 9, 4) + 30)

    def test_needs_pools(self):
        tz = S.zone(NY)
        plan = S.Plan("abc123", "r1", (S.Slot("main", "order", None),), ())
        pool = S.build_pool([S.Entry("S/%d" % i, str(i), "S", 1800, "S/9", "episode", 1, i,
                                     "E", "Show", "", 1990) for i in range(1, 30)])
        now = loc(2026, 5, 1, 12)
        cache = S.freeze(plan, {"main": pool}, now, tz)
        self.assertTrue(LU.needs_pools(None, plan, now, tz))
        self.assertFalse(LU.needs_pools(cache, plan, now + 3600, tz))        # same day
        self.assertTrue(LU.needs_pools(cache, plan, loc(2026, 5, 2, 4, 1), tz))  # after 04:00
        self.assertTrue(LU.needs_pools(cache, plan._replace(rev="r2"), now, tz))
        self.assertTrue(LU.needs_pools(dict(cache, stale=True), plan, now, tz))

    def test_read_zone(self):
        d = tempfile.mkdtemp()
        conf, etc = os.path.join(d, "b.conf"), os.path.join(d, "tz")
        with open(conf, "w") as f:
            f.write("system.timezone=Europe/Paris\n")
        self.assertEqual(LU.read_zone((conf, etc, os.path.join(d, "none"))), "Europe/Paris")
        with open(conf, "w") as f:
            f.write("system.timezone=Not/AZone\n")
        with open(etc, "w") as f:
            f.write("America/Chicago\n")
        self.assertEqual(LU.read_zone((conf, etc, os.path.join(d, "none"))), "America/Chicago")
        shutil.rmtree(d)


class TestTune(unittest.TestCase):
    def tape(self):
        e = [S.Entry("S/%d" % i, str(i), "S", 600, "S/9", "episode", 1, i, "E%d" % i, "Show", "",
                     None) for i in range(3)]
        return S.Tape([S.Airing(1000 + 600 * i, 600, "main", S.Pool([x]).units[0])
                       for i, x in enumerate(e)])

    def test_start_snap_and_offset(self):
        t = self.tape()
        self.assertEqual(LU.tune_point(t, 1000 + 29), (0, 0, 0))   # under 30 s: from the start
        self.assertEqual(LU.tune_point(t, 1000 + 31), (0, 0, 31))
        self.assertEqual(LU.tune_point(t, 1000 + 595), (1, 0, 0))  # the tail: the next item
        self.assertIsNone(LU.tune_point(t, 999))

    def test_up_down_last(self):
        order = ["a", "b", "c"]
        self.assertEqual(LU.step(order, "a", 1), "b")              # Up: the higher number
        self.assertEqual(LU.step(order, "c", 1), "a")              # wrap
        self.assertEqual(LU.step(order, "a", -1), "c")
        self.assertEqual(LU.step(order, "gone", 1), "a")
        r = LU.remember([], "a")
        r = LU.remember(r, "b")
        self.assertEqual(r, ["b", "a"])
        self.assertEqual(LU.last_channel(r, "b", order), "a")
        r = LU.remember(r, "a")
        self.assertEqual(LU.last_channel(r, "a", order), "b")
        self.assertIsNone(LU.last_channel(["a"], "a", order))
        self.assertIsNone(LU.last_channel(["b", "x"], "b", order))  # a hidden channel


class TestPending(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.old = os.environ.get("BATOPLEX_HOME")
        os.environ["BATOPLEX_HOME"] = self.home

    def tearDown(self):
        os.environ["BATOPLEX_HOME"] = self.old
        shutil.rmtree(self.home, ignore_errors=True)

    def test_saved_channels_load_not_empty(self):
        t = [1_790_000_000.0]
        lu = LU.Lineup(lambda fn: None, clock=lambda: t[0], zone=S.zone(NY))
        lu.reset("k", None)
        t[0] += 5
        lu.tick()                                                  # no channels yet: loaded
        C.add_channels([{"number": 2, "name": "A", "order": "shuffle",
                         "source": {"type": "library", "server": "s", "section": 1}}],
                       C.load().stamp)
        lu.check_file()                                            # the guide opens
        self.assertEqual([c.name for c in lu.pending()], ["A"])    # "Loading…", not empty

    def test_one_bad_channel_does_not_stop_the_others(self):
        src = {"type": "library", "server": "srv", "section": 1}
        C.add_channels([{"number": n, "name": x, "order": "order", "source": src}
                        for n, x in ((2, "A"), (3, "B"), (4, "C"))], C.load().stamp)
        C.ensure_ids(C.load().stamp)
        rules = C.load()
        a, b, c = rules.channels
        b.plan = lambda: 1 / 0                                     # a damaged plan
        now = loc(2026, 5, 1, 12)
        empty_row = {"v": S.VERSION, "rev": c.plan().rev, "date": "2026-05-01",
                     "anchor": now - 60, "end": now + 9999, "pools": {}, "state": {}, "stale": True,
                     "rows": [[now - 60, "main", []]]}            # a damaged cache
        lu = LU.Lineup(lambda fn: None, clock=lambda: now, zone=S.zone(NY))
        with self.assertLogs("batoplex.lineup", "ERROR"):
            res = lu.build(rules, "k", FakeSources(FakeProv()), mem={c.id: empty_row})
        self.assertEqual(res["status"][b.id], LU.EMPTY)
        self.assertEqual(res["status"][a.id], LU.ON)
        self.assertEqual(res["status"][c.id], LU.ON)
        self.assertIn(a.id, res["tapes"])
        self.assertIn(c.id, res["tapes"])


class SlowProv(FakeProv):
    """FakeProv with a show library (1) and a movie library (2); a path in `slow` waits."""

    def __init__(self, slow=()):
        FakeProv.__init__(self)
        self.slow, self.lock = dict(slow), threading.Lock()

    def load_sections(self):
        return [{"id": 1, "type": "show", "title": "TV", "stamp": 1},
                {"id": 2, "type": "movie", "title": "Films", "stamp": 1}]

    def listing(self, path, params=None, cap=0, deadline=None):
        time.sleep(self.slow.get(path, 0))
        with self.lock:
            self.calls.append(path)
        if path == "/library/sections/2/all":
            return [Item(id=str(300 + i), type="movie", title="M%d" % i, duration_ms=5400000,
                         server_id="srv") for i in range(1, 4)]
        n = 200 if "metadata" in path else 100
        return [Item(id=str(n + i), type="episode", title="E%d" % i, duration_ms=1800000,
                     show_id="9", season_index=1, index=i, server_id="srv") for i in range(1, 4)]


class KeptProv(ThemeProv):
    """ThemeProv that also answers the section list (the scan reads the stamps there)."""

    def get(self, path, params=None, start=None, size=None, timeout=8):
        if path == "/library/sections":
            self.calls.append((path, {}))
            return {"Directory": [{"key": "3", "type": self.kind, "title": "Lib",
                                   "contentChangedAt": self.stamp}]}
        return ThemeProv.get(self, path, params, start, size, timeout)


def rows_of(res):
    return {k: [(a.start, a.end, [e.key for e in a.unit.children]) for a in t.rows]
            for k, t in res["tapes"].items()}


class TestFastFreeze(unittest.TestCase):
    """The first freeze after a save: parallel fetch, row-by-row publish, the scan indexes."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.old, self.old_w = os.environ["BATOPLEX_HOME"], LU.FETCH_WORKERS
        os.environ["BATOPLEX_HOME"] = self.home

    def tearDown(self):
        os.environ["BATOPLEX_HOME"] = self.old
        LU.FETCH_WORKERS = self.old_w
        shutil.rmtree(self.home, ignore_errors=True)

    def channels(self):
        srcs = [{"type": "show", "server": "srv", "id": "9"},          # 2: slow
                {"type": "library", "server": "srv", "section": 1},    # 3
                {"type": "library", "server": "srv", "section": 2}]    # 4
        C.add_channels([{"number": n, "name": "Ch%d" % n, "order": "order", "source": s}
                        for n, s in zip((2, 3, 4), srcs)], C.load().stamp)
        C.ensure_ids(C.load().stamp)
        return C.load()

    def lineup(self, posted=None):
        return LU.Lineup(posted.append if posted is not None else (lambda fn: None),
                         clock=lambda: loc(2026, 5, 1, 12), zone=S.zone(NY))

    def test_collect_parallel_is_the_sequential_result(self):
        qs = [types.SimpleNamespace(key="q%d" % i) for i in range(9)]

        def fetch(q, deadline):
            i = int(q.key[1:])
            time.sleep((9 - i) * 0.003)                 # later queries end first
            if i == 4:
                raise RuntimeError("boom")
            return None if i % 3 == 0 else [q.key]
        with self.assertLogs("batoplex.channels", "INFO"):
            seq = C.collect(qs, fetch)
            done, me = [], threading.get_ident()
            par = C.collect(qs, fetch, workers=3,
                            done=lambda q, r, skip: done.append((q.key, threading.get_ident())))
        self.assertEqual(par, seq)
        self.assertEqual(list(par[0]), [q.key for q in qs if q.key in par[0]])   # query order
        self.assertEqual(par[1], ["q0", "q3", "q4", "q6"])
        self.assertEqual(sorted(k for k, _t in done), sorted(q.key for q in qs))
        self.assertTrue(all(t == me for _k, t in done))  # done runs on the caller's thread
        skipped, ends = [], []
        res, failed = C.collect(qs, fetch, deadline=5, clock=lambda: 10, skipped=skipped,
                                workers=3, done=lambda q, r, skip: ends.append(skip))
        self.assertEqual((res, failed, skipped), ({}, [q.key for q in qs], [q.key for q in qs]))
        self.assertEqual(ends, [True] * 9)

    def test_parallel_build_gives_the_sequential_tapes(self):
        rules = self.channels()
        LU.FETCH_WORKERS = 1
        seq = self.lineup().build(rules, "seq", FakeSources(SlowProv()))
        LU.FETCH_WORKERS = 2
        slow = SlowProv({"/library/metadata/9/allLeaves": 0.2})
        par = self.lineup().build(rules, "par", FakeSources(slow))
        self.assertEqual(rows_of(par), rows_of(seq))
        self.assertEqual((par["status"], par["retry"]), (seq["status"], False))
        self.assertEqual(len(par["tapes"]), 3)

    def test_queries_of_the_lowest_numbers_go_first(self):
        rules = self.channels()                         # by key: library 1, library 2, show
        LU.FETCH_WORKERS = 1
        p = SlowProv()
        self.lineup().build(rules, "k", FakeSources(p))
        self.assertEqual(p.calls, ["/library/metadata/9/allLeaves", "/library/sections/1/all",
                                   "/library/sections/2/all"])

    def test_channels_show_row_by_row_and_the_end_result_rules(self):
        rules = self.channels()
        a, b, c = rules.channels
        posted = []
        lu = self.lineup(posted)
        lu.reset("k", FakeSources(SlowProv({"/library/metadata/9/allLeaves": 0.3})))
        lu.busy = True
        with self.assertLogs("batoplex.lineup", "INFO") as cm:
            lu._work(lu.gen, rules, "k", lu.sources, {})
        self.assertIn("lineup: freeze start, 3 channels need pools", cm.output[0])
        self.assertEqual(len(posted), 4)                # 3, 4, 2, the end
        shown = []
        for fn in posted[:-1]:
            fn()
            shown.append(sorted(lu.status))
        self.assertEqual(shown, [[b.id], sorted([b.id, c.id]),
                                 sorted([a.id, b.id, c.id])])   # the slow channel 2 last
        self.assertEqual([x.id for x in lu.visible()], [a.id, b.id, c.id])
        self.assertEqual(lu.pending(), [])
        part = dict(lu.tapes)
        posted[-1]()                                    # the end result
        self.assertFalse(lu.busy)
        self.assertEqual(set(lu.tapes), set(part))
        self.assertTrue(all(lu.tapes[k].rows == part[k].rows for k in part))
        lu.status = {}
        posted[0]()                                     # a late part: ignored
        self.assertEqual(lu.status, {})

    def test_a_part_waits_for_its_own_queries_and_the_generation(self):
        rules = self.channels()
        a, b, c = rules.channels
        posted = []
        lu = self.lineup(posted)
        lu.reset("k", FakeSources(SlowProv()))
        lu.busy = True
        gen = lu.gen
        old = lu.build(rules, "k", FakeSources(SlowProv()))
        posted.clear()
        lu._work(gen, rules, "k", lu.sources, dict(old["caches"]))   # all cached: one part
        self.assertEqual(len(posted), 2)                # before any fetch, then the end
        lu.reset("k", lu.sources)                       # another profile meanwhile
        lu.busy = True
        posted[0]()
        self.assertEqual((lu.status, lu.tapes), ({}, {}))
        self.assertEqual(len(lu.pending()), 3)
        lu.gen = gen
        lu.again = True                                 # saved meanwhile: old rules, ignored
        posted[0]()
        self.assertEqual((lu.status, lu.tapes), ({}, {}))
        lu.again = False
        posted[0]()
        self.assertEqual(sorted(lu.status), sorted([a.id, b.id, c.id]))
        self.assertTrue(all(v == LU.ON for v in lu.status.values()))

    def scan_lib(self):
        it = Item(id="3", type="library", title="Lib", section_id=3, server_id="a")
        it.extra["section_type"] = "movie"
        return it

    def theme_rules(self):
        srcs = [{"type": "theme", "theme": t, "kind": "movie", "sections": [["a", 3]]}
                for t in ("comedy", "cartoons")]
        C.add_channels([{"number": n, "name": "T%d" % n, "order": "shuffle", "source": s}
                        for n, s in zip((2, 3), srcs)], C.load().stamp)
        C.ensure_ids(C.load().stamp)
        return C.load()

    def films(self):
        return [film("1", 5400000), film("2", 6000000, ("7", "8"), "PG"), film("3", 5000000)]

    def test_kept_index_only_when_complete_fresh_and_same(self):
        t = [100.0]
        k = LU.Kept(clock=lambda: t[0])
        p = KeptProv("a", "movie", self.films())
        ix = LU.index(p, 3, "movie", time.monotonic() + 30, "Lib")
        k.put("a", 3, p, None, ix)                      # no stamp: not kept
        k.put("a", 3, p, 5, dict(ix, rows_complete=False))   # incomplete: never kept
        self.assertEqual(k.m, {})
        k.put("a", 3, p, 5, ix)
        self.assertIs(k.get(("a", 3), p, 5, "movie", "Lib"), ix)
        for args in ((("a", 3), p, 6, "movie", "Lib"), (("a", 3), object(), 5, "movie", "Lib"),
                     (("a", 3), p, 5, "show", "Lib"), (("a", 3), p, 5, "movie", "Renamed"),
                     (("a", 4), p, 5, "movie", "Lib"), (("a", 3), p, None, "movie", "Lib")):
            self.assertIsNone(k.get(*args))
        t[0] += LU.INDEX_KEEP_S + 1
        self.assertIsNone(k.get(("a", 3), p, 5, "movie", "Lib"))   # too old
        k.expire()
        self.assertEqual(k.m, {})

    def test_freeze_after_a_scan_reads_no_index(self):
        rules = self.theme_rules()
        ref_p = KeptProv("a", "movie", self.films())
        ref = self.lineup().build(rules, "ref", FakeMap({"a": ref_p}), kept=None)
        self.assertEqual(sum(1 for c in ref_p.calls if c[0].endswith("/genre")), 1)  # one index
        self.assertEqual(len(ref["tapes"]), 2)

        def stamps(pr):
            return LU._stamps(pr, {}, threading.Lock())
        kept = LU.Kept()
        p = KeptProv("a", "movie", self.films())
        LU.library_scan(p, self.scan_lib(), time.monotonic() + 30, stamps, kept)
        self.assertEqual(len(kept.m), 1)
        n = len(p.calls)
        res = self.lineup().build(rules, "k", FakeMap({"a": p}), kept=kept)
        self.assertEqual(len(p.calls), n)               # no index request at all
        self.assertEqual(rows_of(res), rows_of(ref))
        self.assertEqual(kept.m, {})                    # used: dropped
        p2 = KeptProv("a", "movie", self.films())       # the library changed after the scan
        LU.library_scan(p2, self.scan_lib(), time.monotonic() + 30, stamps, kept)
        p2.stamp = 6
        n = len(p2.calls)
        self.lineup().build(rules, "k2", FakeMap({"a": p2}), kept=kept)
        self.assertTrue(any(c[0].endswith("/genre") for c in p2.calls[n:]))


if __name__ == "__main__":
    unittest.main()
