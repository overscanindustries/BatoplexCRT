import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import account, config, inputs, plex  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import profile as prof_mod  # noqa: E402


def sync_run_bg(self, fn, done, fail=None):
    try:
        r = fn()
    except Exception as e:
        if fail:
            fail(e)
        return
    done(r)


class TestSwitchUser(unittest.TestCase):
    def setUp(self):
        self.real = plex._request
        self.urls = []
        self.who = None                 # None: whoami works; else the exception it raises
        self.switch = None

        def req(url, h, method="GET", **k):
            self.urls.append(url)
            if "/switch" in url:
                if self.switch:
                    raise self.switch
                return {"authToken": "user-tok"}
            if url.endswith("/api/v2/user"):
                if self.who:
                    raise self.who
                return {"uuid": "o"}
            return {}
        plex._request = req

    def tearDown(self):
        plex._request = self.real

    def kind(self, user, pin):
        with self.assertRaises(account.SwitchError) as cm:
            account.switch_user("cid", "acct", user, pin)
        return cm.exception.kind

    def test_refused_pin_is_pin_when_the_account_is_fine(self):
        u = {"uuid": "k", "protected": True}
        self.switch = plex.AuthError("HTTP 401")
        self.assertEqual(self.kind(u, "1111"), "pin")
        self.switch = plex.PlexError("HTTP 403 for x")
        self.assertEqual(self.kind(u, "1111"), "pin")

    def test_refused_with_an_expired_account_is_auth(self):
        self.switch = plex.AuthError("HTTP 401")
        self.who = plex.AuthError("HTTP 401")
        self.assertEqual(self.kind({"uuid": "k", "protected": True}, "1111"), "auth")
        self.assertEqual(self.kind({"uuid": "k"}, None), "auth")

    def test_network_is_other(self):
        self.switch = plex.PlexError("network: down")
        self.assertEqual(self.kind({"uuid": "k"}, "1111"), "other")

    def test_id_when_no_uuid(self):
        self.assertEqual(account.switch_user("cid", "acct", {"id": 42}), "user-tok")
        self.assertIn("/home/users/42/switch", self.urls[-1])


class Secrets:
    def __init__(self, **v):
        self.v = v

    def get(self, k, default=None):
        return self.v.get(k, default)

    def set(self, **kw):
        self.v.update(kw)


class App:
    def __init__(self, **secrets):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.secrets = Secrets(client_id="cid", user_token="acct", **secrets)
        self.stack, self.entered, self.switched = [], 0, []

    @property
    def top(self):
        return self.stack[-1]

    def push(self, s):
        self.stack.append(s)

    def pop(self):
        self.stack.pop()

    def enter_stored(self):
        self.entered += 1

    def switch_profile(self, user, pin, screen):
        self.switched.append((user, pin))


class TestPicker(unittest.TestCase):
    def setUp(self):
        self.real_bg, self.real_users = Screen.run_bg, account.home_users
        self.held = []
        held = self.held

        def later(scr, fn, done, fail=None):       # results arrive after the push, as posts do
            held.append((fn, done, fail))
            while scr in getattr(scr.app, "stack", []) and held:
                sync_run_bg(scr, *held.pop(0))
        Screen.run_bg = later

        def down(cid, tok):
            raise plex.PlexError("network: down")
        account.home_users = down

    def tearDown(self):
        Screen.run_bg, account.home_users = self.real_bg, self.real_users

    def picker(self, app):
        p = prof_mod.ProfileScreen(app, at_launch=True)
        app.push(p)
        while self.held:
            sync_run_bg(p, *self.held.pop(0))
        return p

    def test_offline_owner_with_no_profile_continues(self):
        app = App(server_token="st")
        p = self.picker(app)
        self.assertEqual(len(p.users), 1)
        p.handle(inputs.OK)
        self.assertEqual(app.entered, 1)

    def test_offline_protected_profile_has_no_entry(self):
        for prof in ({"uuid": "k", "title": "Kid", "protected": True, "token": "t"},
                     {"uuid": "o", "title": "Owner", "admin": True, "protected": True}):
            app = App(server_token="st", profile=prof)
            p = self.picker(app)
            self.assertEqual(p.users, [])
            p.handle(inputs.OK)
            self.assertEqual(app.entered, 0)

    def test_refused_account_signs_out(self):
        def refused(cid, tok):
            raise plex.AuthError("HTTP 401")
        account.home_users = refused
        app = App(server_token="st")
        app.signed_out = 0

        def so():
            app.signed_out += 1
        app.sign_out = so
        self.picker(app)
        self.assertEqual(app.signed_out, 1)

    def test_offline_without_server_token_has_no_entry(self):
        self.assertEqual(self.picker(App()).users, [])

    def test_errors_reach_the_pin_pad(self):
        app = App(server_token="st")
        p = self.picker(app)
        p.users = [{"uuid": "k", "title": "Kid", "protected": True}]
        p.handle(inputs.OK)
        pad = app.top
        for d in "1234":
            pad.handle("d" + d)
        self.assertEqual(app.switched[-1][1], "1234")
        p.switch_failed("other", "Can't switch user: network")
        self.assertEqual((pad.digits, pad.msg), ("", "Can't switch user: network"))
        self.assertFalse(p.busy)
        pad.handle("d5")
        self.assertEqual(pad.digits, "5")
        pad.handle(inputs.BACKSPACE)
        self.assertEqual(pad.digits, "")
        app.pop()                                 # the pad closed while it checked
        p.busy = True
        p.switch_failed("pin", "Incorrect PIN")
        self.assertEqual((p.busy, p.error), (False, "Incorrect PIN"))


class TestSwitchProfile(unittest.TestCase):
    def run_switch(self, user, pin):
        from batoplex.app import App as RealApp
        calls = []
        real = account.switch_user

        def sw(cid, tok, u, p=None):
            calls.append(p)
            raise account.SwitchError("pin", "HTTP 401")
        account.switch_user = sw
        try:
            fake = types.SimpleNamespace(secrets=Secrets(client_id="cid", user_token="acct"),
                                         src_gen=1)
            scr = types.SimpleNamespace(run_bg=lambda w, d, f: sync_run_bg(None, w, d, f),
                                        failed=[])
            scr.switch_failed = lambda k, m: scr.failed.append(k)
            RealApp.switch_profile(fake, user, pin, scr)
        finally:
            account.switch_user = real
        return calls, scr.failed

    def test_protected_owner_pin_is_checked(self):
        calls, failed = self.run_switch({"uuid": "o", "admin": True, "protected": True}, "9999")
        self.assertEqual((calls, failed), (["9999"], ["pin"]))


if __name__ == "__main__":
    unittest.main()
