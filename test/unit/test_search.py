import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import config, plex, search  # noqa: E402
from batoplex.model import Item, Row  # noqa: E402


def it(i, sid, guid=None):
    x = Item(str(i), "movie", "T%s" % i, server_id=sid)
    if guid:
        x.extra["guid"] = guid
    return x


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


class Prov:
    def __init__(self, rows=None, err=None):
        self.rows, self.err, self.queries = rows or [], err, []

    def search(self, q, limit=12):
        self.queries.append(q)
        if self.err:
            raise self.err
        return self.rows


class Runner:
    """Holds each request until release(); then calls done or fail, as app.post would."""
    def __init__(self):
        self.held = []

    def __call__(self, fn, done, fail):
        self.held.append((fn, done, fail))

    def release(self, n=None):
        batch, self.held = (self.held, []) if n is None else (self.held[:n], self.held[n:])
        for fn, done, fail in batch:
            try:
                r = fn()
            except Exception as e:
                fail(e)
                continue
            done(r)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.clk, self.run = Clock(), Runner()
        self.home = Prov([Row("movie", "Movies", [it(1, "H", "plex://movie/1"), it(2, "H", "plex://movie/2")])])
        self.other = Prov([Row("movie", "Movies", [it(9, "O", "plex://movie/1"), it(8, "O", "plex://movie/8")]),
                           Row("show", "Shows", [it(7, "O")])])
        self.targets = [("H", self.home), ("O", self.other)]
        self.auth = []
        self.s = search.SearchSession(lambda: self.targets, self.run, clock=self.clk,
                                      on_auth=lambda: self.auth.append(1))

    def type(self, q):
        self.s.set_query(q)
        self.s.tick()

    def test_delay_and_min_length(self):
        self.type("a")
        self.clk.t = 1
        self.s.tick()
        self.assertEqual(self.run.held, [])            # 1 character: no request
        self.type("al")
        self.clk.t = 1.29
        self.s.tick()
        self.assertEqual(self.run.held, [])            # 300 ms not over
        self.clk.t = 1.31
        self.assertTrue(self.s.tick())
        self.assertEqual(len(self.run.held), 2)
        self.assertEqual(self.s.state, "searching")

    def test_merge_as_replies_land(self):
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.run.release(1)                            # home only
        self.assertTrue(self.s.tick())
        self.assertEqual([i.id for i in self.s.rows[0].items], ["1", "2"])
        self.run.release()
        self.s.tick()
        self.assertEqual(self.s.state, "done")
        self.assertEqual([(r.key, [i.id for i in r.items]) for r in self.s.rows],
                         [("movie", ["1", "2", "8"]), ("show", ["7"])])   # copy g1 dropped

    def test_one_request_per_server_newest_runs_after(self):
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.type("ali")
        self.clk.t = 2
        self.s.tick()
        self.assertEqual(len(self.run.held), 2)       # still the first two
        self.assertEqual(self.s.rows, [])
        self.run.release()                            # old replies: dropped, newest starts
        self.assertEqual(self.s.rows, [])
        self.assertEqual(self.home.queries, ["al"])
        self.assertEqual(len(self.run.held), 2)
        self.run.release()
        self.assertEqual(self.home.queries, ["al", "ali"])
        self.assertEqual(self.s.state, "done")

    def test_old_results_stay_until_new_ones_land(self):
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.run.release()
        old = self.s.rows
        self.type("alx")
        self.clk.t = 2
        self.s.tick()
        self.assertIs(self.s.rows, old)
        self.home.rows = []
        self.other.rows = []
        self.run.release()
        self.assertEqual((self.s.rows, self.s.state), ([], "done"))   # "No results"

    def test_all_failed_is_error_one_failed_is_not(self):
        self.other.err = plex.PlexError("network: down")
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.run.release()
        self.assertEqual(self.s.state, "done")
        self.home.err = plex.PlexError("network: down")
        self.s.retry()
        self.s.tick()
        self.run.release()
        self.assertEqual(self.s.state, "error")
        self.assertEqual(self.auth, [])

    def test_home_auth_error(self):
        self.home.err = plex.AuthError("HTTP 401")
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.run.release()
        self.assertEqual(self.auth, [1])

    def test_short_query_clears(self):
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.run.release()
        self.type("a")
        self.assertEqual((self.s.rows, self.s.state), ([], "idle"))
        self.run.release()

    def test_no_servers_is_error(self):
        self.targets = []
        self.type("al")
        self.clk.t = 1
        self.s.tick()
        self.assertEqual(self.s.state, "error")


class TestMerge(unittest.TestCase):
    def test_interleave_order_and_cap(self):
        parts = {"O": [Row("show", "Shows", [it(5, "O")]),
                       Row("movie", "Movies", [it(i, "O") for i in range(100, 130)])],
                 "H": [Row("movie", "Movies", [it(1, "H"), it(2, "H")]),
                       Row("collection", "Collections", [it(3, "H")])]}
        rows = search.merge(parts, ["H", "O"])
        self.assertEqual([r.key for r in rows], ["movie", "collection", "show"])
        self.assertEqual([i.id for i in rows[0].items][:5], ["1", "100", "2", "101", "102"])
        self.assertEqual(len(rows[0].items), search.GROUP_MAX)

    def test_music_groups_come_after_video(self):
        parts = {"H": [Row("artist", "Artists", [it(1, "H")]), Row("movie", "Movies", [it(2, "H")]),
                       Row("track", "Tracks", [it(3, "H")]), Row("album", "Albums", [it(4, "H")])]}
        self.assertEqual([r.key for r in search.merge(parts, ["H"])],
                         ["movie", "artist", "album", "track"])

    def test_local_guids_do_not_merge(self):
        parts = {"H": [Row("movie", "Movies", [it(1, "H", "local://5")])],
                 "O": [Row("movie", "Movies", [it(9, "O", "local://5")])]}
        self.assertEqual(len(search.merge(parts, ["H", "O"])[0].items), 2)


class TestRecent(unittest.TestCase):
    def test_order_max_case_and_profile(self):
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        for q in ("alien", "Brazil", "ALIEN", "cars", "dune", "elf"):
            st.add_recent_search(q)
        self.assertEqual(st.recent_searches(), ["elf", "dune", "cars", "ALIEN"])
        st.add_recent_search("  ")
        self.assertEqual(len(st.recent_searches()), 4)
        st.profile = "kid"
        self.assertEqual(st.recent_searches(), [])
        st.add_recent_search("bluey")
        st.profile = "default"
        self.assertEqual(st.recent_searches()[0], "elf")
        st.save()
        st2 = config.Settings(st.path)
        self.assertEqual(st2.recent_searches(), ["elf", "dune", "cars", "ALIEN"])
        st2.clear_recent_searches()
        self.assertEqual(st2.recent_searches(), [])


class TestSources(unittest.TestCase):
    def test_search_providers(self):
        from batoplex.sources import Sources
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))

        class P:
            def __init__(self, sid):
                self.server_id, self.settings = sid, st

            def search(self, q, limit=12):
                return []
        s = Sources([P("H"), P("A"), P("B")], home_id="H")
        st.set_pins([("B", 1), ("H", 2)])
        self.assertEqual([k for k, _ in s.search_providers(st)], ["H", "B"])


if __name__ == "__main__":
    unittest.main()
