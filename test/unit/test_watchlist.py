"""Watchlist: the Discover key, paging, the token, support, identity and the
cache, ordered writes, the guid lookup (found / absent / unavailable) and the shown titles."""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import config, plex  # noqa: E402
from batoplex import watchlist as W  # noqa: E402
from batoplex.app import App  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.sources import Sources  # noqa: E402


class SyncThread:
    """threading.Thread that runs its target at start() (workers in order, no races)."""

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def entry(key, guid=None, typ="movie", at=0):
    return {"ratingKey": key, "guid": guid or "plex://%s/%s" % (typ, key), "type": typ,
            "title": "T" + key, "watchlistedAt": at}


class Prov:
    def __init__(self, sid, settings, have=None, stamp=1):
        self.server_id, self.settings = sid, settings
        self.have = dict(have or {})             # guid -> (key, section)
        self.sections = [{"id": 1, "type": "movie", "stamp": stamp},
                         {"id": 2, "type": "show", "stamp": stamp}]
        self.calls, self.fail, self.down = [], False, False

    def load_sections(self):
        if self.down:
            raise plex.PlexError("network: down")
        return self.sections

    def by_guid(self, guids):
        self.calls.append(list(guids))
        if self.fail:
            raise plex.PlexError("network: timed out")
        out = {}
        for g in guids:
            if g in self.have:
                key, sec = self.have[g]
                out[g] = (key, sec, {"type": "movie", "title": "x", "poster": "/p/" + key})
        return out

    def search(self, q):
        return []


class Harness:
    """A fake app: post() queues, run() delivers in order."""

    def __init__(self, provs=(), pins=(), root=None):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.set_pins([{"server": s, "section": 1} for s in pins])
        self.posted, self.toasts, self.src_gen = [], [], 0
        self.sources = Sources(list(provs), home_id=provs[0].server_id) if provs else None
        self.root = root or tempfile.mkdtemp()

    def post(self, fn):
        self.posted.append(fn)

    def toast(self, msg, private=False):
        self.toasts.append(msg)

    def run(self):
        while self.posted:
            self.posted.pop(0)()


def make(provs=(), pins=(), wid="acct", token="tok", owner=True, root=None):
    app = Harness(provs, pins, root)
    wl = W.Watchlist(app, root=app.root)
    wl.reset(wid, token, "cid", owner=owner)
    return app, wl


def lists(*replies):
    """plex.watchlist replaced by these results in turn (a list, or an exception)."""
    it = iter(replies)

    def fn(cid, tok, *a, **k):
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return [{"key": e["ratingKey"], "guid": e["guid"], "type": e["type"],
                 "added_at": e["watchlistedAt"]} for e in r]
    return fn


@mock.patch.object(W.threading, "Thread", SyncThread)
class TestKeyAndPaging(unittest.TestCase):
    def test_watch_key(self):
        self.assertEqual(plex.watch_key("plex://movie/5d776b59ad5437001f79c6f8"),
                         "5d776b59ad5437001f79c6f8")
        self.assertEqual(plex.watch_key("plex://show/abc"), "abc")
        for g in ("plex://episode/abc", "local://12", "com.plexapp.agents.imdb://tt1?lang=en",
                  "plex://movie/", "", None):
            self.assertIsNone(plex.watch_key(g))

    def pages(self, replies, size=2):
        """plex._request answers from replies (by the Start header); records the headers."""
        seen = []

        def req(url, headers, method="GET", timeout=8, raw=False):
            seen.append((url, dict(headers)))
            r = replies(int(headers["X-Plex-Container-Start"]))
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.object(plex, "_request", req):
            try:
                return plex.watchlist("cid", "tok", size=size), seen
            except plex.PlexError as e:
                return e, seen

    @staticmethod
    def mc(keys, total, start):
        return {"MediaContainer": {"size": len(keys), "totalSize": total, "offset": start,
                                   "Metadata": [entry(k) for k in keys]}}

    def test_three_pages_by_headers(self):
        allk = ["a", "b", "c", "d", "e"]
        out, seen = self.pages(lambda s: self.mc(allk[s:s + 2], 5, s))
        self.assertEqual([x["key"] for x in out], allk)
        self.assertEqual([h["X-Plex-Container-Start"] for _u, h in seen], ["0", "2", "4"])
        self.assertTrue(all("X-Plex-Container" not in u and "tok" not in u for u, _h in seen))
        self.assertTrue(all(h["X-Plex-Token"] == "tok" for _u, h in seen))

    def test_repeated_and_empty_pages_and_bad_replies(self):
        e, _ = self.pages(lambda s: self.mc(["a", "b"], 5, 0))             # the same page again
        self.assertIsInstance(e, plex.PlexError)
        e, _ = self.pages(lambda s: self.mc(["a", "b"] if s == 0 else [], 5, s))
        self.assertIsInstance(e, plex.PlexError)                          # empty before the total
        e, _ = self.pages(lambda s: {})                                   # bad JSON
        self.assertIsInstance(e, plex.PlexError)
        e, _ = self.pages(lambda s: {"MediaContainer": {"size": 2}})      # a count, no Metadata
        self.assertIsInstance(e, plex.PlexError)
        e, _ = self.pages(lambda s: {"MediaContainer": {}})               # no count, no Metadata
        self.assertIsInstance(e, plex.PlexError)
        out, _ = self.pages(lambda s: {"MediaContainer": {"size": 0, "totalSize": 0}})
        self.assertEqual(out, [])                                         # an empty Watchlist

    def test_no_limit_and_membership_past_500(self):
        n = 620
        keys = ["k%d" % i for i in range(n)]
        out, seen = self.pages(lambda s: self.mc(keys[s:s + 100], n, s), size=100)
        self.assertEqual((len(out), len(seen)), (n, 7))
        app, wl = make()
        with mock.patch.object(plex, "watchlist", lambda *a, **k: out):
            self.assertIsNone(wl.state("k501"))                           # no complete list yet
            wl.load()
            app.run()
        self.assertTrue(wl.state("k501"))
        self.assertFalse(wl.state("other"))

    def test_failed_later_load_keeps_the_list(self):
        app, wl = make()
        with mock.patch.object(plex, "watchlist", lists([entry("a")], plex.PlexError("HTTP 500"))):
            wl.load()
            app.run()
            wl.load()
            app.run()
        self.assertEqual([x["key"] for x in wl.titles], ["a"])
        self.assertTrue(wl.complete and wl.error)
        self.assertTrue(wl.state("a"))


class TestToken(unittest.TestCase):
    def app(self, prof, token="acct-tok"):
        sec = types.SimpleNamespace(v={"profile": prof, "account_token": token})
        sec.get = lambda k, d=None: (sec.v.get("account_token") if k == "user_token"
                                     else sec.v.get(k, d))
        return types.SimpleNamespace(secrets=sec)

    def test_owner_profile_and_no_fallback(self):
        self.assertEqual(App.watchlist_token(self.app(None)), "acct-tok")
        self.assertEqual(App.watchlist_token(self.app({"uuid": "o", "admin": True})), "acct-tok")
        self.assertEqual(App.watchlist_token(self.app({"uuid": "m", "token": "mom"})), "mom")
        self.assertIsNone(App.watchlist_token(self.app({"uuid": "m", "token": None})))


@mock.patch.object(W.threading, "Thread", SyncThread)
class TestSupportAndIdentity(unittest.TestCase):
    def test_refused_home_profile_is_no_owner_is_error(self):
        app, wl = make(owner=False)
        with mock.patch.object(plex, "watchlist", lists(plex.PlexError("HTTP 403", code=403))):
            wl.load()
            app.run()
        self.assertEqual(wl.support, W.NO)
        self.assertFalse(wl.visible())
        app, wl = make(owner=True)
        with mock.patch.object(plex, "watchlist", lists(plex.AuthError("HTTP 401", code=401))):
            wl.load()
            app.run()
        self.assertEqual(wl.support, W.UNKNOWN)
        self.assertTrue(wl.error and wl.visible())

    def test_two_owners_two_files_and_no_uuid_no_file(self):
        from batoplex import channels
        root = tempfile.mkdtemp()
        for acct in ("owner-a", "owner-b"):
            app, wl = make(wid=channels.cache_key(acct, acct), root=root)
            with mock.patch.object(plex, "watchlist", lists([entry(acct)])):
                wl.load()
                app.run()
        self.assertEqual(len(os.listdir(root)), 2)
        app, wl = make(wid=None, root=tempfile.mkdtemp())
        with mock.patch.object(plex, "watchlist", lists([entry("a")])):
            wl.load()
            app.run()
        self.assertEqual(os.listdir(app.root), [])
        self.assertTrue(wl.state("a"))                                    # in memory

    def test_sign_out_removes_files_and_a_late_load_makes_none(self):
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"BATOPLEX_HOME": home}):
            sec = config.Secrets(os.path.join(home, "auth.json"))
            app = Harness()
            wl = W.Watchlist(app)                                         # <home>/cache/watchlist
            wl.reset("id1", "tok", "cid")
            with mock.patch.object(plex, "watchlist", lists([entry("a")], [entry("b")])):
                wl.load()
                app.run()
                folder = os.path.join(home, "cache", "watchlist")
                self.assertEqual(os.listdir(folder), ["id1.json"])
                wl.load()                                                 # its reply comes late
                sec.clear_login()
                wl.reset(None, None, None)                                # App._use after sign-out
                app.run()
            self.assertFalse(os.path.exists(folder))


@mock.patch.object(W.threading, "Thread", SyncThread)
class TestWorkersAndWrites(unittest.TestCase):
    def test_late_reply_after_switch_or_reconnect_is_dropped(self):
        settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        a = Prov("A", settings, {"plex://movie/a": ("11", 1)})
        app, wl = make([a])
        with mock.patch.object(plex, "watchlist", lists([entry("a")], [entry("b")])):
            wl.load()
            wl.reset("other", "tok2", "cid")                              # a profile switch
            app.run()
        self.assertEqual(wl.titles, [])
        wl.titles, wl.complete = [{"key": "a", "guid": "plex://movie/a", "type": "movie"}], True
        wl.resolve()                                                      # lands after a reconnect
        app.sources.add(Prov("A", settings, {"plex://movie/a": ("99", 1)}))
        app.run()
        self.assertEqual(wl.found, {})

    def item(self, key):
        it = Item(key, "movie", "M", server_id=None)
        it.extra["guid"] = "plex://movie/" + key
        return it

    def test_failed_first_write_after_good_second_write(self):
        app, wl = make()
        wl.complete = True
        ops = []
        wl._send = ops.append
        wl.set(self.item("k"), True)                                      # add, then remove
        wl.set(self.item("k"), False)
        self.assertFalse(wl.state("k"))
        calls = iter([None, plex.PlexError("HTTP 500")])

        def write(*a):
            e = next(calls)
            if e:
                raise e
        with mock.patch.object(plex, "watchlist_add", write), \
                mock.patch.object(plex, "watchlist_remove", write):
            wl._write_one(ops[1])                                         # the good one lands first
            wl._write_one(ops[0])
            app.run()
        self.assertFalse(wl.state("k"))
        self.assertEqual(wl.pending, {})
        self.assertEqual(app.toasts, ["Removed from Watchlist", "Can't change your Watchlist"])

    def test_failed_write_puts_back_only_that_title(self):
        app, wl = make()
        with mock.patch.object(plex, "watchlist", lists([entry("x")])):
            wl.load()
            app.run()
        ops = []
        wl._send = ops.append
        wl.set(self.item("x"), False)
        wl.set(self.item("y"), True)
        with mock.patch.object(plex, "watchlist_remove", mock.Mock(side_effect=plex.PlexError())), \
                mock.patch.object(plex, "watchlist_add", mock.Mock()):
            for op in ops:
                wl._write_one(op)
            app.run()
        self.assertEqual((wl.state("x"), wl.state("y")), (True, True))

    def test_list_load_during_a_pending_write(self):
        app, wl = make()
        with mock.patch.object(plex, "watchlist", lists([entry("a")], [entry("a")])):
            wl.load()
            app.run()
            wl._send = lambda op: None
            wl.set(self.item("a"), False)
            wl.load()
            app.run()
        self.assertFalse(wl.state("a"))                                   # the pending remove wins
        self.assertEqual(wl.view_titles(), [])

    def test_a_load_older_than_a_confirmed_write_does_not_undo_it(self):
        for want, before in ((True, []), (False, [entry("k")])):
            app, wl = make()
            with mock.patch.object(plex, "watchlist", lists(before, before)):
                wl.load()
                app.run()
                ops = []
                wl._send = ops.append
                wl.load()                                                 # T0: the load starts
                loaded = app.posted.pop()                                 # ... its reply waits
                wl.set(self.item("k"), want)                              # T1: Add or Remove
                with mock.patch.object(plex, "watchlist_add", mock.Mock()), \
                        mock.patch.object(plex, "watchlist_remove", mock.Mock()):
                    wl._write_one(ops[0])
                    app.run()                                             # T2: confirmed
                self.assertEqual((wl.pending, wl.state("k")), ({}, want))
                loaded()                                                  # T3: the T0 list lands
            self.assertEqual(wl.state("k"), want)
            with open(wl._path()) as f:
                disk = json.load(f)
            self.assertEqual(any(x["key"] == "k" for x in disk["titles"]), want)
            with mock.patch.object(plex, "watchlist", lists(before)):
                wl.load()                                                 # a later load: no replay
                app.run()
            self.assertEqual(wl.state("k"), bool(before))

    def test_reconnect_keeps_queued_writes_switch_drops_them(self):
        app, wl = make()
        wl.complete = True
        ops = []
        wl._send = ops.append
        wl.set(self.item("k"), True)
        wl.reset("acct", "tok", "cid")                                    # a reconnect, same profile
        self.assertTrue(wl.state("k"))
        with mock.patch.object(plex, "watchlist_add", mock.Mock()) as add:
            wl._write_one(ops[0])
            app.run()
        self.assertEqual(add.call_count, 1)
        self.assertEqual((wl.pending, [x["key"] for x in wl.titles]), ({}, ["k"]))
        wl.set(self.item("j"), True)
        wl.reset("other", "tok2", "cid")                                  # another profile
        self.assertEqual(wl.pending, {})
        with mock.patch.object(plex, "watchlist_add", mock.Mock()) as add:
            wl._write_one(ops[1])
            app.run()
        self.assertEqual(add.call_count, 0)


@mock.patch.object(W.threading, "Thread", SyncThread)
class TestResolve(unittest.TestCase):
    def setup(self, have_a, have_b=None, pins=("B",)):
        settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        a = Prov("A", settings, have_a)
        b = Prov("B", settings, have_b or {})
        app, wl = make([a, b], pins=pins)
        app.settings = settings
        settings.set_pins([{"server": s, "section": 1} for s in pins])
        a.settings = b.settings = settings
        return app, wl, a, b

    def load(self, app, wl, entries):
        with mock.patch.object(plex, "watchlist", lists(entries)):
            wl.load()
            app.run()

    def keys(self, wl):
        return [(i.server_id, i.id) for i in wl.shown()]

    def test_found_absent_and_title_on_no_server(self):
        app, wl, a, b = self.setup({"plex://movie/m1": ("11", 1)})
        self.load(app, wl, [entry("m1"), entry("gone")])
        self.assertEqual(self.keys(wl), [("A", "11")])
        self.assertTrue(wl.found[("A", "plex://movie/gone")]["absent"])
        self.assertEqual(len(wl.shown()), 1)                              # the count: 1, not 2

    def test_timeout_keeps_found_and_makes_no_absent_then_recovers(self):
        app, wl, a, b = self.setup({"plex://movie/m1": ("11", 1)})
        self.load(app, wl, [entry("m1")])
        a.sections[0]["stamp"] = 2                                        # a scan: look again
        a.fail = True
        wl.resolve()
        app.run()
        self.assertEqual(self.keys(wl), [("A", "11")])                    # the mapping stays
        app2, wl2, a2, _b = self.setup({"plex://movie/m2": ("12", 1)})
        a2.fail = True
        self.load(app2, wl2, [entry("m2")])
        self.assertNotIn(("A", "plex://movie/m2"), wl2.found)             # unavailable: no absent
        a2.fail = False
        wl2.resolve()
        app2.run()
        self.assertEqual(self.keys(wl2), [("A", "12")])

    def test_stamps_decide_reuse(self):
        app, wl, a, b = self.setup({})
        self.load(app, wl, [entry("late")])
        n = len(a.calls)
        wl.resolve()
        app.run()
        self.assertEqual(len(a.calls), n)                                 # same stamps: no call
        a.have["plex://movie/late"] = ("13", 1)                           # added to a library
        for s in a.sections:
            s["stamp"] = 5
        wl.resolve()
        app.run()
        self.assertEqual(self.keys(wl), [("A", "13")])
        for s in a.sections:
            s["stamp"] = None                                             # no stamp: no reuse
        n = len(a.calls)
        wl.resolve()
        app.run()
        self.assertEqual(len(a.calls), n + 1)

    def test_home_wins_and_failed_sections_keep_titles_with_one_retry(self):
        both = "plex://movie/both"
        app, wl, a, b = self.setup({both: ("11", 1)}, {both: ("77", 1)})
        now = [0.0]
        wl.clock = lambda: now[0]
        self.load(app, wl, [entry("both")])
        self.assertEqual(self.keys(wl), [("A", "11")])
        a.down = True                                                     # sections fail, A connected
        wl.resolve()
        app.run()
        self.assertEqual(self.keys(wl), [("B", "77")])                    # B's copy plays
        self.assertEqual(wl.retry, {"A": 20.0})
        b.have.clear()
        b.sections[0]["stamp"] = 2
        wl.resolve(only="B")
        app.run()
        self.assertEqual(self.keys(wl), [("A", "11")])                    # only on A: still shown
        a.have[both] = ("12", 1)
        a.sections[0]["stamp"] = 2
        n = len(a.calls)
        wl.tick(False)
        self.assertEqual(len(a.calls), n)                                 # not yet
        now[0] = 20.0
        wl.tick(False)                                                    # the retry: still down
        app.run()
        self.assertEqual((wl.retry, len(a.calls)), ({}, n))
        wl.resolve(only="A")
        app.run()
        self.assertEqual(wl.retry, {})                                    # bounded: no second retry
        a.down = False
        wl.resolve(only="A")
        app.run()
        self.assertEqual(self.keys(wl), [("A", "12")])
        a.down = True                                                     # after good sections:
        wl.resolve(only="A")                                              # a retry again
        app.run()
        self.assertEqual(wl.retry, {"A": 40.0})
        now[0] = 40.0
        a.down = False
        wl.tick(False)
        app.run()
        self.assertEqual((wl.retry, wl.retried), ({}, set()))

    def test_hidden_section_and_unpinned_server_are_out(self):
        app, wl, a, b = self.setup({}, {"plex://movie/b1": ("21", 1)})
        self.load(app, wl, [entry("b1")])
        self.assertEqual(self.keys(wl), [("B", "21")])
        app.settings._prof("default")["hidden"] = [{"server": "B", "section": 1}]
        self.assertEqual(self.keys(wl), [])
        app.settings._prof("default")["hidden"] = []
        app.settings.set_pins([])                                         # B has no pin now
        self.assertEqual(self.keys(wl), [])

    def test_hidden_section_hit_is_found_and_shows_after_unhide(self):
        app, wl, a, b = self.setup({}, {"plex://movie/b1": ("21", 1)})
        app.settings._prof("default")["hidden"] = [{"server": "B", "section": 1}]
        self.load(app, wl, [entry("b1")])
        self.assertEqual(wl.found[("B", "plex://movie/b1")]["key"], "21")
        self.assertEqual(self.keys(wl), [])
        app.settings._prof("default")["hidden"] = []
        self.assertEqual(self.keys(wl), [("B", "21")])

    def test_by_guid_keeps_hidden_sections_and_needs_a_container(self):
        st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        p = plex.PlexProvider("http://srv:32400", "tok", "cid", st, server_id="S")
        st._prof("default")["hidden"] = [{"server": "S", "section": 3}]
        h = {"guid": "plex://movie/h", "type": "movie", "ratingKey": 9, "librarySectionID": 3}
        v = dict(h, ratingKey=7, librarySectionID=4)
        v2 = dict(h, ratingKey=8, librarySectionID=5)
        replies = iter([{"MediaContainer": {"size": 1, "Metadata": [h]}},
                        {"MediaContainer": {"size": 3, "Metadata": [h, v, v2]}},
                        {"MediaContainer": {}}])
        with mock.patch.object(plex, "_request", lambda *a, **k: next(replies)):
            got = p.by_guid(["plex://movie/h", "plex://movie/none"])
            self.assertEqual(list(got), ["plex://movie/h"])
            self.assertEqual(got["plex://movie/h"][:2], ("9", 3))         # only copy: hidden kept
            got = p.by_guid(["plex://movie/h"])
            self.assertEqual(got["plex://movie/h"][:2], ("7", 4))         # the first visible wins
            self.assertRaises(plex.PlexError, p.by_guid, ["plex://movie/h"])

    def test_batches_publish_in_parts_and_epoch_stops_the_job(self):
        have = {"plex://movie/k%d" % i: (str(100 + i), 1) for i in range(45)}
        app, wl, a, b = self.setup(have, pins=())
        with mock.patch.object(plex, "watchlist", lists([entry("k%d" % i) for i in range(45)])):
            wl.load()
            app.posted.pop(0)()                                           # the list lands
        self.assertEqual([len(c) for c in a.calls], [20, 20, 5])
        app.posted.pop(0)()                                               # server up
        app.posted.pop(0)()                                               # the first batch
        self.assertEqual(len(wl.shown()), 20)
        app.run()
        self.assertEqual(len(wl.shown()), 45)
        app, wl, a, b = self.setup(have, pins=())
        orig = a.by_guid

        def switch(g):
            wl.epoch += 1                                                 # a profile switch
            return orig(g)
        a.by_guid = switch
        with mock.patch.object(plex, "watchlist", lists([entry("k%d" % i) for i in range(45)])):
            wl.load()
            app.posted.pop(0)()
        self.assertEqual(len(a.calls), 1)

    def test_server_connect_and_cold_start(self):
        settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        settings.set_pins([{"server": "B", "section": 1}])
        a = Prov("A", settings, {})
        b = Prov("B", settings, {"plex://movie/b1": ("21", 1)})
        root = tempfile.mkdtemp()
        app, wl = make([a, b], pins=("B",), root=root)
        app.settings = settings
        self.load(app, wl, [entry("b1"), entry("a0")])
        self.assertEqual(self.keys(wl), [("B", "21")])
        # A cold start: the cache, only the home server connected, no Discover reply yet.
        app2 = Harness([a], root=root)
        app2.settings = settings
        wl2 = W.Watchlist(app2, root=root)
        wl2.reset("acct", "tok", "cid")
        self.assertEqual(self.keys(wl2), [])                              # B is not connected
        app2.sources.add(b)
        wl2.server_added("B")
        self.assertEqual(self.keys(wl2), [("B", "21")])                   # at once, from the cache
        self.assertTrue(wl2.state("b1"))


if __name__ == "__main__":
    unittest.main()
