import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import account, plex  # noqa: E402

RES = {"name": "Phalflix", "clientIdentifier": "srv1", "provides": "server", "accessToken": "TOK",
       "owned": False, "publicAddressMatches": False,
       "connections": [
           {"protocol": "https", "address": "10.0.0.5", "port": 32400, "local": True,
            "uri": "https://10-0-0-5.x.plex.direct:32400"},
           {"protocol": "https", "address": "1.2.3.4", "port": 32400, "local": False,
            "uri": "https://1-2-3-4.x.plex.direct:32400"},
           {"protocol": "http", "address": "1.2.3.4", "port": 32400, "local": False,
            "uri": "http://1.2.3.4:32400"},
           {"protocol": "https", "address": "9.9.9.9", "port": 443, "local": False, "relay": True,
            "uri": "https://9-9-9-9.x.plex.direct:8443"}]}


class TestCandidates(unittest.TestCase):
    def test_shared_server_private_address_skipped(self):
        self.assertEqual(plex._candidates(RES), [(3, "https://1-2-3-4.x.plex.direct:32400"),
                                                 (4, "https://9-9-9-9.x.plex.direct:8443")])

    def test_same_lan_gets_local_first(self):
        r = dict(RES, publicAddressMatches=True)
        c = plex._candidates(r)
        self.assertEqual(c[0], (1, "http://10.0.0.5:32400"))
        self.assertEqual(c[1], (2, "https://10-0-0-5.x.plex.direct:32400"))
        self.assertNotIn("http://1.2.3.4:32400", [u for _r, u in c])   # remote plain http


class FakeNet:
    def __init__(self, answers):
        self.answers = answers      # uri -> (delay, machine id or exception)
        self.headers = []

    def __call__(self, url, headers, timeout=8, **kw):
        self.headers.append(dict(headers))
        uri = url[:-len("/identity")]
        delay, mid = self.answers.get(uri, (0, plex.PlexError("refused")))
        time.sleep(delay)
        if isinstance(mid, Exception):
            raise mid
        return {"MediaContainer": {"machineIdentifier": mid}}


class TestConnect(unittest.TestCase):
    def srv(self, cands):
        return {"name": "S", "id": "srv1", "token": "TOK", "cands": cands}

    def test_no_token_and_identity_checked(self):
        net = FakeNet({"https://a": (0, "other"), "https://b": (0.05, "srv1")})
        uri = plex.connect_server("cid", self.srv([(3, "https://a"), (3, "https://b")]),
                                  settle=0.2, request=net)
        self.assertEqual(uri, "https://b")
        self.assertTrue(all("X-Plex-Token" not in h for h in net.headers))

    def test_local_wins_at_once(self):
        net = FakeNet({"http://lan": (0.05, "srv1"), "https://wan": (0, "srv1")})
        t0 = time.monotonic()
        uri = plex.connect_server("cid", self.srv([(1, "http://lan"), (3, "https://wan")]),
                                  settle=1.0, request=net)
        self.assertEqual(uri, "http://lan")
        self.assertLess(time.monotonic() - t0, 0.5)

    def test_better_rank_within_settle(self):
        net = FakeNet({"https://relay": (0, "srv1"), "https://wan": (0.1, "srv1")})
        uri = plex.connect_server("cid", self.srv([(3, "https://wan"), (4, "https://relay")]),
                                  settle=0.4, request=net)
        self.assertEqual(uri, "https://wan")

    def test_down_after_deadline(self):
        net = FakeNet({})
        with self.assertRaises(plex.PlexError):
            plex.connect_server("cid", self.srv([(3, "https://x")]), deadline=0.3, request=net)


class TestAccount(unittest.TestCase):
    def setUp(self):
        self.real = plex._request

    def tearDown(self):
        plex._request = self.real

    def test_home_users_json(self):
        plex._request = lambda url, h, **k: {"users": [
            {"uuid": "u1", "id": 1, "title": "Alex", "protected": False, "admin": True},
            {"uuid": "u2", "id": 2, "title": "Kid", "protected": 1, "restricted": "1"}]}
        users = account.home_users("cid", "T")
        self.assertEqual([(u["title"], u["protected"], u["restricted"]) for u in users],
                         [("Alex", False, False), ("Kid", True, True)])

    def test_home_users_xml_fallback(self):
        def req(url, h, raw=False, **k):
            if url.endswith("/api/v2/home/users"):
                raise plex.PlexError("HTTP 404 for x")
            return (b'<MediaContainer><User id="7" uuid="u7" title="Mom" protected="1" '
                    b'admin="0" restricted="0"/></MediaContainer>')
        plex._request = req
        self.assertEqual(account.home_users("cid", "T"),
                         [{"uuid": "u7", "id": "7", "title": "Mom", "protected": True,
                           "admin": False, "restricted": False, "thumb": None}])

    def test_switch_errors_told_apart(self):
        def wrong_pin(url, h, **k):
            raise plex.PlexError("HTTP 403 for x")
        plex._request = wrong_pin
        with self.assertRaises(account.SwitchError) as c:
            account.switch_user("cid", "T", {"uuid": "u2", "protected": True}, pin="1111")
        self.assertEqual(c.exception.kind, "pin")

        def expired(url, h, **k):
            raise plex.AuthError("HTTP 401")
        plex._request = expired
        with self.assertRaises(account.SwitchError) as c:
            account.switch_user("cid", "T", {"uuid": "u1", "protected": False})
        self.assertEqual(c.exception.kind, "auth")

    def test_switch_returns_token_pin_only_in_query(self):
        seen = []

        def ok(url, h, method="GET", **k):
            seen.append((url, h))
            return {"authToken": "USER-TOK"}
        plex._request = ok
        tok = account.switch_user("cid", "ACC", {"uuid": "u2", "protected": True}, pin="1234")
        self.assertEqual(tok, "USER-TOK")
        self.assertTrue(seen[0][0].startswith("https://plex.tv/api/v2/home/users/u2/switch?pin="))
        self.assertNotIn("ACC", seen[0][0])


if __name__ == "__main__":
    unittest.main()


class TestQaT2(unittest.TestCase):
    def test_owned_server_keeps_lan(self):
        r = dict(RES, owned=True, publicAddressMatches=False)
        self.assertEqual(plex._candidates(r)[0], (1, "http://10.0.0.5:32400"))   # LAN kept

    def test_late_lan_answer_wins(self):
        net = FakeNet({"http://lan": (0.4, "srv1"), "https://wan": (0, "srv1")})
        srv = {"name": "S", "id": "srv1", "cands": [(1, "http://lan"), (3, "https://wan")]}
        self.assertEqual(plex.connect_server("cid", srv, settle=0.1, request=net), "http://lan")

    def test_bad_port_redirect_hook(self):
        os.environ["BATOPLEX_PLEX_TV"] = "http://127.0.0.1:99999"
        try:
            self.assertEqual(plex._plex_tv(), plex.PLEX_TV)
        finally:
            del os.environ["BATOPLEX_PLEX_TV"]


class TestRollbackMigration(unittest.TestCase):
    def test_v1_keys_after_v2_win(self):
        import json
        from batoplex import config
        d = tempfile.mkdtemp()
        p = os.path.join(d, "auth.json")
        json.dump({"client_id": "c", "auth_version": 2, "account_token": "OLD",
                   "servers": {"a": {"token": "OLDS"}}, "home_server": "a",
                   "user_token": "NEW", "server_token": "NEWS", "server_id": "b"}, open(p, "w"))
        s = config.Secrets(p)
        self.assertEqual((s.get("user_token"), s.get("server_token"), s.get("server_id")),
                         ("NEW", "NEWS", "b"))
