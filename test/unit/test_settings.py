"""Settings as a category list with pages: every old row on one page,
help copy for each focusable row, text rows the focus skips, and the side panel return."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import boot, draw, inputs, theme  # noqa: E402
from batoplex.app import App as RealApp  # noqa: E402
from batoplex.screens import system  # noqa: E402
from test_bands import Bench, fake_app  # noqa: E402

# The keys of the old single Settings list (Exit stays on the list).
OLD_KEYS = {"skip_intro", "skip_credits", "auto_play", "up_next", "autoplay_countdown_s",
            "passout_protection_h", "rewind_on_resume_s", "night_audio", "theme_music",
            "theme_volume", "quality_home", "quality_remote", "theme", "menu_sounds",
            "high_contrast", "margins", "patterns", "flicker_filter", "aspect", "saver", "boot",
            "server", "servers", "user", "auto_sign_in", "signout"}


class TwoServers:
    home_id, order = "A", ["A", "B"]

    def server_count(self):
        return 2

    def server_name(self, sid):
        return {"A": "Home", "B": "Friend"}[sid]


def full_app():
    """Every condition on: two Home users, two servers, Auto Play and Theme Music On."""
    app = fake_app()
    app.home_users_n = lambda: 2
    app.sources = TwoServers()
    app.settings["auto_play"] = True
    app.settings["theme_music"] = True
    return app


def all_rows(app):
    return {c: system.page_rows(app, c, boot_ok=True) for c, _t in system.CATEGORIES}


class TestPages(unittest.TestCase):
    def setUp(self):
        import dataclasses
        p = mock.patch.object(theme, "cur", dataclasses.replace(theme.cur, sounds="x"))
        p.start()
        self.addCleanup(p.stop)

    def test_every_old_key_on_exactly_one_page(self):
        pages = all_rows(full_app())
        keys = [r[1] for rows in pages.values() for r in rows]
        self.assertEqual(sorted(keys), sorted(OLD_KEYS))      # each once, none lost or added

    def test_every_focusable_row_has_help(self):
        for app in (full_app(),):
            with mock.patch.object(boot, "available", lambda: True):
                scr = system.SettingsScreen(app)
            rows = scr.rows() + [r for rows in all_rows(app).values() for r in rows]
            for r in rows:
                if draw.focusable(r):
                    self.assertIn(r[1], system.HELP, r)
                    limit = 40 if r[0] == "cat" or r[1] == "exit" else 52
                    self.assertLessEqual(len(system.HELP[r[1]]), limit, r)

    def test_text_rows_not_focusable(self):
        app = full_app()
        page = system.SettingsPage(app, "account", "Account")
        rows = page.rows()
        self.assertEqual([r[1] for r in rows if r[0] == "text"], ["server", "servers"])
        self.assertEqual([r[1] for r in page.items()], ["user", "signout"])
        for a in [inputs.UP] * 3 + [inputs.DOWN] * 3 + [inputs.UP] * 3:
            page.handle(a)
            self.assertIn(page.items()[page.focus][1], ("user", "signout"))
        # the renderer counts the same rows: focus 0 is Switch User, below the two text rows
        pos, _top, _total = draw.list_layout(rows, 0, 300, 0)
        self.assertEqual(len(pos), 4)

    def test_category_list_and_pages(self):
        app = full_app()
        app.pushed = []
        app.push = lambda scr, panel=None: app.pushed.append(scr)
        with mock.patch.object(boot, "available", lambda: False):
            scr = system.SettingsScreen(app)
        self.assertEqual([r[2] for r in scr.rows()],
                         ["Experience", "Appearance", "Player Experience", "Video & Audio",
                          "TV Setup", "Account", "Exit Batoplex"])
        self.assertTrue(all(r[3] == "" for r in scr.rows()))     # no values on the right
        scr.handle(inputs.DOWN)
        scr.handle(inputs.OK)
        page = app.pushed[-1]
        self.assertEqual((page.cat, page.title), ("appearance", "Appearance"))

    def test_page_choice_and_back(self):
        app = full_app()
        app.saved = lambda ok: ok
        app.popped = 0
        app.pop = lambda: setattr(app, "popped", app.popped + 1)
        page = system.SettingsPage(app, "player", "Player Experience")
        page.handle(inputs.DOWN)
        page.handle(inputs.DOWN)                      # Auto Play
        page.handle(inputs.OK)
        self.assertIs(app.settings["auto_play"], False)
        self.assertEqual(page.items()[page.focus][1], "auto_play")
        page.handle(inputs.MENU)                      # Menu is not Back
        self.assertEqual(app.popped, 0)
        page.handle(inputs.BACK)
        self.assertEqual(app.popped, 1)

    def test_sign_out_asks(self):
        app = full_app()
        app.stack = []
        app.overlay = lambda scr, kind="menu": app.stack.append(scr)
        app.pop = lambda result=None: app.stack.pop()
        app.sign_out = mock.Mock()
        page = system.SettingsPage(app, "account", "Account")
        page.handle(inputs.DOWN)                      # Sign Out
        page.handle(inputs.OK)
        dlg = app.stack[-1]
        self.assertEqual((dlg.title, dlg.labels, dlg.focus),
                         ("Sign out of Plex?", ("Sign Out", "Cancel"), 1))
        dlg.handle(inputs.OK)                         # Cancel
        app.sign_out.assert_not_called()
        page.handle(inputs.OK)
        app.stack[-1].handle(inputs.LEFT)
        app.stack[-1].handle(inputs.OK)
        app.sign_out.assert_called_once_with()

    def test_help_line_redraws_in_band(self):
        """Up/Down: the press band holds the list and the help line under it."""
        pygame.font.init()
        app = full_app()
        page = system.SettingsPage(app, "player", "Player Experience")
        b = Bench(self, page, app)
        for a in ["down"] * 9 + ["up"] * 9:
            b.press(a, settle=1)

    def test_help_line_hides_under_a_dialog(self):
        pygame.font.init()
        app = full_app()
        page = system.SettingsPage(app, "video", "Video & Audio")
        drawn = []
        real = draw.text
        with mock.patch.object(draw, "text", lambda s, t, *a, **k: drawn.append(t) or
                               real(s, t, *a, **k)):
            page.draw(app.canvas)
            self.assertIn(system.HELP["quality_home"], drawn)
            drawn.clear()
            page.hide_hints = True
            page.draw(app.canvas)
            self.assertNotIn(system.HELP["quality_home"], drawn)


class Stack:
    """The navigation part of App: push, pop, overlay, back (the real ones)."""

    def __init__(self):
        self.stack, self.panels = [], []
        self.pre = None

    top = RealApp.top
    push = RealApp.push
    pop = RealApp.pop
    back = RealApp.back
    overlay = RealApp.overlay
    _snapshot = RealApp._snapshot

    def open_panel(self, focus=None):
        self.panels.append(focus)


class Page(system.Screen):
    def handle(self, a):
        if a == inputs.BACK:
            self.app.back()


class TestPanelReturn(unittest.TestCase):
    def test_b_reopens_panel_only_with_origin(self):
        app = Stack()
        root = Page(app)
        app.stack.append(root)
        p = Page(app)
        app.push(p, panel="radio")
        p.handle(inputs.BACK)
        self.assertEqual((app.stack, app.panels), ([root], ["radio"]))
        q = Page(app)
        app.push(q)                                   # opened some other way
        q.handle(inputs.BACK)
        self.assertEqual((app.stack, app.panels), ([root], ["radio"]))

    def test_other_pops_keep_the_panel_shut(self):
        app = Stack()
        app.stack.append(Page(app))
        app.push(Page(app), panel="playlists")
        app.pop()                                     # playback, a menu action, auto close
        self.assertEqual(app.panels, [])

    def test_exit_cancel_reopens_panel(self):
        from batoplex.screens.panel import PanelScreen
        app = Stack()
        app.stack.append(Page(app))
        app.quit = mock.Mock()
        panel = PanelScreen.__new__(PanelScreen)
        panel.app, panel.rows, panel.focus = app, [("home",), ("more",)], 1
        panel.column_w = lambda: 0
        app.overlay(panel, "panel")
        app.confirm_exit = lambda on_no=None: RealApp.confirm_exit(app, on_no)
        app.overlay = lambda scr, kind="menu": app.stack.append(scr)
        PanelScreen.handle(panel, inputs.BACK)
        dlg = app.stack[-1]
        self.assertIsInstance(dlg, system.ExitDialog)
        dlg.handle(inputs.BACK)
        self.assertEqual(app.panels, ["more"])
        app.quit.assert_not_called()



class TestCalibrateVideo(unittest.TestCase):
    def test_a_moved_edge_turns_video_margins_on_and_reset_off(self):
        app = fake_app()
        app.saved = lambda r: None
        app.pop = lambda: None
        scr = system.CalibrateScreen(app)
        scr.handle(inputs.BACK)                           # open and leave: no measurement
        self.assertFalse(app.settings["video_margins"])
        app.settings["safe"] = dict(app.settings["safe"], left=8)
        scr.handle(inputs.LEFT)                           # at the limit: nothing moves
        self.assertFalse(app.settings["video_margins"])
        scr.handle(inputs.RIGHT)
        self.assertEqual(app.settings["safe"]["left"], 12)
        self.assertTrue(app.settings["video_margins"])
        scr.reset()
        self.assertFalse(app.settings["video_margins"])


if __name__ == "__main__":
    unittest.main()
