import json
import os
import tempfile
import unittest

from batoplex import config


def settings_with(disk):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "settings.json")
    with open(p, "w") as f:
        json.dump(disk, f)
    return config.Settings(p), p


def read(p):
    with open(p) as f:
        return json.load(f)


class TestPins(unittest.TestCase):
    def test_migrates_old_keys_and_removes_them(self):
        st, p = settings_with({"library_order": [6, 5], "hidden_libraries": [7],
                               "flicker_filter": "light"})
        self.assertEqual(st.pins("srv"), [])            # unknown server id: nothing yet
        self.assertTrue(st.migrate_pins("srv"))
        self.assertEqual(st.pins("srv"), [6, 5])
        self.assertEqual(st.hidden("srv"), {7})
        self.assertEqual(st.pins("other"), [])
        disk = read(p)
        self.assertNotIn("library_order", disk)
        self.assertNotIn("hidden_libraries", disk)
        self.assertEqual(disk["flicker_filter"], "light")   # other hand edits survive
        self.assertEqual(disk["profiles"]["default"]["pins"][0], {"server": "srv", "section": 6})
        st2 = config.Settings(p)
        self.assertFalse(st2.migrate_pins("srv"))      # nothing left to move
        self.assertEqual(st2.pins("srv"), [6, 5])

    def test_no_server_id_keeps_old_keys(self):
        st, p = settings_with({"library_order": [6]})
        self.assertFalse(st.migrate_pins(None))
        self.assertEqual(read(p)["library_order"], [6])

    def test_later_hand_edit_merges_without_duplicates(self):
        st, p = settings_with({"library_order": [6]})
        st.migrate_pins("srv")
        disk = read(p)
        disk["library_order"] = [6, "8", "bad"]
        with open(p, "w") as f:
            json.dump(disk, f)
        st2 = config.Settings(p)
        st2.migrate_pins("srv")
        self.assertEqual(st2.pins("srv"), [6, 8])

    def test_fresh_install_defaults(self):
        st, p = settings_with({})
        self.assertEqual(st["skip_intro"], "manual")
        self.assertEqual(st["autoplay_countdown_s"], 15)
        self.assertEqual(st["passout_protection_h"], 2)
        self.assertEqual(st["rewind_on_resume_s"], 0)
        self.assertTrue(st["theme_music"])
        st.save()
        self.assertEqual(read(p), {})                  # defaults are not written


class TestAuthV2(unittest.TestCase):
    def path(self, data):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "auth.json")
        with open(p, "w") as f:
            json.dump(data, f)
        return p

    def test_migrates_v1_with_server_id(self):
        p = self.path({"client_id": "c", "user_token": "U", "server_token": "S",
                       "server_url": "http://h:32400", "server_name": "Home", "server_id": "abc"})
        s = config.Secrets(p)
        disk = json.load(open(p))
        self.assertEqual(disk["account_token"], "U")
        self.assertEqual(disk["servers"]["abc"], {"token": "S", "url": "http://h:32400",
                                                  "name": "Home"})
        self.assertEqual(disk["home_server"], "abc")
        self.assertNotIn("server_token", disk)
        # version 1 names still read
        self.assertEqual((s.get("user_token"), s.get("server_token"), s.get("server_url"),
                          s.get("server_name"), s.get("server_id")),
                         ("U", "S", "http://h:32400", "Home", "abc"))
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")

    def test_migrates_e2e_file_without_server_id(self):
        p = self.path({"client_id": "c", "server_token": "S", "server_url": "http://x"})
        s = config.Secrets(p)
        self.assertEqual(s.get("server_token"), "S")
        self.assertIsNone(s.get("server_id"))
        s.set(server_id="mid")                     # learned from /identity later
        self.assertEqual(s.get("server_token"), "S")
        self.assertEqual(json.load(open(p))["servers"], {"mid": {"token": "S", "url": "http://x"}})

    def test_set_v1_names_after_sign_in(self):
        s = config.Secrets(self.path({"client_id": "c"}))
        s.set(user_token="U", server_token="S", server_url="u", server_name="n", server_id="m")
        self.assertEqual((s.get("server_token"), s.get("server_id")), ("S", "m"))
        self.assertEqual(s.v["servers"]["m"]["token"], "S")

    def test_sign_out_clears_v2(self):
        p = self.path({"client_id": "c", "user_token": "U", "server_token": "S", "server_id": "a"})
        s = config.Secrets(p)
        s.v["profile"] = {"uuid": "x", "token": "T"}
        s.clear_login()
        disk = json.load(open(p))
        self.assertEqual(set(disk), {"client_id", "auth_version"})


class TestProfiles(unittest.TestCase):
    def settings(self):
        d = tempfile.mkdtemp()
        return config.Settings(os.path.join(d, "settings.json"))

    def test_other_users_start_from_defaults(self):
        st = self.settings()
        st["skip_intro"] = "auto"                    # the owner's choice
        st.profile = "uuid-mom"
        self.assertEqual(st["skip_intro"], config.DEFAULTS["skip_intro"])
        st["skip_intro"] = "disabled"
        self.assertEqual(st["skip_intro"], "disabled")
        st["safe"] = {"left": 50, "right": 50, "top": 40, "bottom": 40}   # per device
        st.profile = "default"
        self.assertEqual(st["skip_intro"], "auto")
        self.assertEqual(st["safe"]["left"], 50)
        st.save()
        again = config.Settings(st.path)
        again.profile = "uuid-mom"
        self.assertEqual(again["skip_intro"], "disabled")

    def test_pin_list_per_profile(self):
        st = self.settings()
        st.set_pins([("srvA", 1), ("srvB", 7)])
        st.profile = "uuid-kid"
        self.assertEqual(st.pin_list(), [])
        st.set_pins([("srvA", 2)])
        st.profile = "default"
        self.assertEqual(st.pin_list(), [("srvA", 1), ("srvB", 7)])
        self.assertEqual(st.pins("srvB"), [7])


class TestPlexTvHook(unittest.TestCase):
    def test_only_loopback(self):
        from batoplex import plex
        for v, ok in (("http://127.0.0.1:32500", True), ("http://127.0.0.1:1234@evil.example", False),
                      ("http://localhost:32500", False), ("https://127.0.0.1:1", False),
                      ("http://127.0.0.1", False), ("http://u:p@127.0.0.1:5", False)):
            os.environ["BATOPLEX_PLEX_TV"] = v
            try:
                self.assertEqual(plex._plex_tv() != plex.PLEX_TV, ok, v)
            finally:
                del os.environ["BATOPLEX_PLEX_TV"]


class TestDamagedFiles(unittest.TestCase):
    def test_null_or_list_settings_use_defaults(self):
        for disk in (None, [1, 2]):
            with self.assertLogs("batoplex.config", "WARNING"):
                st, p = settings_with(disk)
            self.assertEqual(st["flicker_filter"], "normal")
            self.assertTrue(st.save())
            self.assertEqual(read(p), {})

    def test_box_default_keeps_a_saved_choice(self):
        st, p = settings_with({})
        st.set_default("flicker_filter", "off")             # a 240p screen
        self.assertEqual(st["flicker_filter"], "off")
        st["flicker_filter"] = "normal"                     # the user chose Normal
        st.save()
        self.assertEqual(read(p)["flicker_filter"], "normal")
        st2 = config.Settings(p)
        st2.set_default("flicker_filter", "off")
        self.assertEqual(st2["flicker_filter"], "normal")


if __name__ == "__main__":
    unittest.main()
