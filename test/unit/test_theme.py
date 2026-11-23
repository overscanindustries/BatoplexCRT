import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(__file__))

from batoplex import theme, tokens  # noqa: E402


class TestPalettes(unittest.TestCase):
    def test_every_theme_passes_its_floors(self):
        """The five themes, Ribbon in all 12 months, each with and without High Contrast."""
        self.assertEqual([t.name for t in theme.THEMES.values()],
                         ["Stock", "Ribbon", "Midnight", "Hearth", "Console"])
        for key, th in theme.THEMES.items():
            for month in range(1, 13) if callable(th.palette) else (1,):
                for hc in (False, True):
                    p = theme.resolve(th, hc, month)
                    self.assertEqual(theme.failures(p, hc), [], (key, month, hc))

    def test_ribbon_follows_the_month(self):
        jan, jul = theme.ribbon_palette(1), theme.ribbon_palette(7)
        self.assertNotEqual(jan["accent"], jul["accent"])
        self.assertEqual(jan["ink"], jan["bg"])

    def test_set_test_fallbacks(self):
        """Open decision 1: the set test picks the accent and the focus fill. One fallback at
        a time passes; both fallbacks together put focus against accent at 1.40 (floor 1.5)."""
        A, AF = theme.STOCK_ACCENT, theme.STOCK_ACCENT_FALLBACK
        F, FF = theme.STOCK_FOCUS, theme.STOCK_FOCUS_FALLBACK
        for acc, foc in ((A, F), (AF, F), (A, FF)):
            p = dict(theme.STOCK.palette, accent=acc, focus=foc)
            for hc in (False, True):
                q = theme.resolve(theme.Theme("t", "T", p), hc)
                self.assertEqual(theme.failures(q, hc), [], (acc, foc, hc))
        q = theme.resolve(theme.Theme("t", "T", dict(theme.STOCK.palette, accent=AF, focus=FF)))
        self.assertEqual([f.split()[0] for f in theme.failures(q)], ["focus_accent"])

    def test_plex_reference_is_too_saturated(self):
        """#E5A00D is the Plex reference only."""
        self.assertGreater(theme.saturation(theme.PLEX_ACCENT), theme.MAX_SAT)

    def test_contrast_maths(self):
        self.assertAlmostEqual(theme.contrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(theme.contrast("#777777", "#777777"), 1.0)
        self.assertEqual(theme.mix("#000000", "#FFFFFF", 0.5), "#808080")

    def test_high_contrast_transform(self):
        p = theme.resolve(theme.STOCK, hc=True)
        self.assertEqual(p["text2"], p["text"])
        self.assertEqual(p["raised"], p["bg"])
        self.assertGreaterEqual(theme.checks(p)["dim"], 9.0)


class TestApply(unittest.TestCase):
    def tearDown(self):
        theme.apply(None)

    def test_apply_changes_tokens_in_place(self):
        c = tokens.C
        v = theme.version
        theme.apply({"theme": "stock", "high_contrast": True})
        self.assertIs(tokens.C, c)
        self.assertEqual(theme.version, v + 1)
        self.assertEqual(c["text2"], c["text"])
        self.assertEqual(tokens.SCRIM, tokens.SCRIM_HC)
        self.assertEqual(tokens.GAIN["unfocused"], 0.60)
        theme.apply({"theme": "stock"})
        self.assertEqual(c["bg"], theme.rgb(theme.STOCK.palette["bg"]))
        self.assertEqual(tokens.SCRIM, tokens.SCRIM_BASE)
        self.assertEqual(tokens.GAIN, tokens.GAIN_BASE)

    def test_unknown_theme_and_missing_keys_fall_back_to_stock(self):
        theme.apply({"theme": "gone"})
        self.assertIs(theme.cur, theme.STOCK)
        theme.apply(object())                      # no item access: defaults
        self.assertIs(theme.cur, theme.STOCK)

    def test_heading_rows_follow_the_theme(self):
        theme.apply(None)
        self.assertEqual(tokens.TYPE["heading"][0], "bold")
        self.assertFalse(tokens.TYPE["title"][3])
        theme.apply({"theme": "console"})
        self.assertEqual(tokens.TYPE["heading"], ("cond_bold", 24, 28, True))
        self.assertEqual(theme.cur.focus, "cut")
        self.assertEqual(tokens.TYPE["label"], tokens.TYPE_BASE["label"])

    def test_ribbon_month_change(self):
        import datetime
        theme.apply({"theme": "ribbon"}, today=datetime.date(2026, 1, 31))
        jan = dict(tokens.C)
        self.assertFalse(theme.month_changed(datetime.date(2026, 1, 1)))
        self.assertTrue(theme.month_changed(datetime.date(2026, 2, 1)))
        theme.apply({"theme": "ribbon"}, today=datetime.date(2026, 2, 1))
        self.assertNotEqual(jan["accent"], tokens.C["accent"])
        theme.apply({"theme": "stock"})
        self.assertFalse(theme.month_changed(datetime.date(2027, 5, 1)))


class TestProfileApply(unittest.TestCase):
    """Per-profile theme and the pickers."""

    def setUp(self):
        from batoplex.screens import profile as prof_mod
        self.prof_mod, self.real = prof_mod, prof_mod.ProfileScreen

        class ProfileScreen:
            def __init__(self, app, at_launch=False):
                self.at_launch = at_launch
        prof_mod.ProfileScreen = ProfileScreen

    def tearDown(self):
        self.prof_mod.ProfileScreen = self.real
        theme.apply(None)

    def app(self, profile=None):
        import tempfile
        from batoplex import config
        from batoplex.app import App
        app = App.__new__(App)
        app.stack = []
        app.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        app.settings["theme"] = "hearth"                       # the owner
        app.settings.profile = "uuid-kid"
        app.settings["theme"], app.settings["high_contrast"] = "console", True
        app.settings.profile = "default"
        app.settings.on_profile = app.apply_theme
        prof = {"uuid": "uuid-kid", "token": "t"} if profile else {}
        app.secrets = Secrets(profile=prof, user_token="u", server_token="s", server_url="x",
                              home_users_n=3)
        app.args = type("A", (), {})()
        app.replaced = []
        app.replace = app.replaced.append
        app._use_none = lambda: None
        return app

    def test_two_profiles_keep_two_themes(self):
        app = self.app()
        app.settings.profile = "uuid-kid"
        self.assertEqual((theme.cur.key, theme.high_contrast), ("console", True))
        app.settings.profile = "default"
        self.assertEqual((theme.cur.key, theme.high_contrast), ("hearth", False))

    def test_launch_picker_uses_the_owner_theme(self):
        app = self.app(profile=True)
        app.start()
        self.assertEqual(type(app.replaced[-1]).__name__, "ProfileScreen")
        self.assertEqual((theme.cur.key, theme.high_contrast), ("hearth", False))
        app.settings.profile = app.profile_key()               # the chosen profile enters
        self.assertEqual(theme.cur.key, "console")

    def test_auth_lost_picker_uses_the_owner_theme(self):
        app = self.app(profile=True)
        app.settings.profile = "uuid-kid"
        self.assertEqual(theme.cur.key, "console")
        app.auth_lost()
        self.assertEqual(type(app.replaced[-1]).__name__, "ProfileScreen")
        self.assertEqual(theme.cur.key, "hearth")

    def test_theme_change_tells_screens(self):
        app = self.app()
        seen = []
        app.stack = [type("S", (), {"theme_changed": lambda self: seen.append(1)})()]
        app.apply_theme()
        self.assertEqual(seen, [1])


class Secrets:
    def __init__(self, **v):
        self.v = v

    def get(self, k, default=None):
        return self.v.get(k, default)

    def set(self, **kw):
        self.v.update(kw)

    def test_colours_are_the_roles_only(self):
        """Landing 2: the old names (accent_dim, info) and the type aliases are gone."""
        self.assertEqual(set(tokens.C), set(theme.ROLES))
        for k in ("title_l", "title_m", "meta"):
            self.assertNotIn(k, tokens.TYPE)


if __name__ == "__main__":
    unittest.main()
