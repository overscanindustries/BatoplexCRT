"""OK on release and hold OK, the one menu order, the one pin menu, the side panel
wrap, Menu with no rows."""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import app as A  # noqa: E402
from batoplex import config, inputs  # noqa: E402
from batoplex.screens import actions  # noqa: E402
from batoplex.sources import Sources  # noqa: E402
from test_inputs import FakeEvent, drain  # noqa: E402
from test_live_pin import Prov, settings  # noqa: E402


def key(inp, k, down=True, inst=None):
    e = FakeEvent(pygame.KEYDOWN if down else pygame.KEYUP, key=k, mod=0, unicode="")
    if inst is not None:
        e.instance_id = inst
    inp.handle(e)
    return [a for a, _r in drain(inp)]


class TestOkEvents(unittest.TestCase):
    def test_down_and_up_once_per_press(self):
        inp = inputs.Input()
        self.assertEqual(key(inp, pygame.K_RETURN), [inputs.OK_DOWN])
        self.assertEqual(key(inp, pygame.K_RETURN), [])            # a repeat: one press
        self.assertEqual(key(inp, pygame.K_RETURN, down=False), [inputs.OK_UP])

    def test_release_from_another_device_is_ignored(self):
        inp = inputs.Input()
        inp._src = 7
        inp._press(inputs.OK)
        inp._src = "kbd"
        inp._release(inputs.OK)
        self.assertEqual([a for a, _r in drain(inp)], [inputs.OK_DOWN])
        inp._src = 7
        inp._release(inputs.OK)
        self.assertEqual([a for a, _r in drain(inp)], [inputs.OK_UP])

    def test_inject_ok_stays_a_finished_ok(self):
        inp = inputs.Input()
        inp.inject("ok")
        self.assertEqual([a for a, _r in drain(inp)], ["ok"])

    def test_flush_counts(self):
        inp = inputs.Input()
        n = inp.flushes
        inp.flush()
        self.assertEqual(inp.flushes, n + 1)


class Top:
    def __init__(self, ok_on_press=False):
        self.ok_on_press = ok_on_press
        self.focus = 0

    def focus_identity(self):
        return self.focus


class _App(A.App):
    playing = False


def ok_app(top):
    app = _App.__new__(_App)
    app.stack = [top]
    app.inputs = inputs.Input()
    return app


class TestHold(unittest.TestCase):
    def setUp(self):
        self.t = 100.0
        p = mock.patch.object(A.time, "monotonic", lambda: self.t)
        p.start()
        self.addCleanup(p.stop)

    def press(self, app):
        app.inputs.down.add("ok")
        return app._ok_event(inputs.OK_DOWN)

    def test_short_press_is_ok_on_release(self):
        app = ok_app(Top())
        self.assertIsNone(self.press(app))
        self.t += 0.2
        self.assertIsNone(app._ok_tick())
        self.assertEqual(app._ok_event(inputs.OK_UP), "ok")

    def test_hold_is_hold_once(self):
        app = ok_app(Top())
        self.press(app)
        self.t += 0.5
        self.assertEqual(app._ok_tick(), inputs.HOLD)
        self.assertIsNone(app._ok_event(inputs.OK_UP))           # no double fire

    def test_release_after_the_deadline_with_no_tick_is_hold(self):
        app = ok_app(Top())
        self.press(app)
        self.t += 0.6
        self.assertEqual(app._ok_event(inputs.OK_UP), inputs.HOLD)

    def test_ok_on_press_screen(self):
        app = ok_app(Top(ok_on_press=True))
        self.assertEqual(self.press(app), "ok")
        self.assertIsNone(app._ok_event(inputs.OK_UP))

    def test_focus_change_cancels(self):
        top = Top()
        app = ok_app(top)
        self.press(app)
        top.focus = 3
        self.assertIsNone(app._ok_event(inputs.OK_UP))

    def test_flush_cancels(self):
        app = ok_app(Top())
        self.press(app)
        app.inputs.flush()
        self.assertIsNone(app._ok_tick())
        self.assertIsNone(app._okp)

    def test_other_action_resolves_first(self):
        """_step calls _ok_finish before another action."""
        app = ok_app(Top())
        self.press(app)
        self.t += 0.1
        self.assertEqual(app._ok_finish(), "ok")


class TestOrder(unittest.TestCase):
    def test_groups_in_order_and_stable(self):
        rows = [("Sort", 1), ("Go to Show", 2), ("Add to Playlist", 3), ("Mark as Played", 4),
                ("Play from Start", 5), ("Add to Queue", 6), ("Play Next", 7),
                ("Mark Season as Played", 8), ("Add to Watchlist", 9), ("Details", 10)]
        self.assertEqual([r[0] for r in actions.ordered(rows)], [
            "Play from Start", "Mark as Played", "Mark Season as Played", "Add to Watchlist",
            "Add to Queue", "Play Next", "Add to Playlist", "Go to Show", "Details", "Sort"])

    def test_own_rows_keep_their_order(self):
        rows = [("Unpin", 0), ("Move Up", 0), ("Move Down", 0)]
        self.assertEqual(actions.ordered(rows), rows)


def pin_app(st):
    src = Sources([Prov("A", [(1, "Movies"), (2, "TV"), (3, "Music")])], home_id="A")
    return types.SimpleNamespace(settings=st, sources=src, lineup=None,
                                 saved=lambda ok: None, pins_changed=lambda: None)


class TestPinRows(unittest.TestCase):
    def test_one_order(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, {"server": "A", "section": 2},
                     {"server": "A", "section": 3}])
        app = pin_app(st)
        self.assertEqual([r[0] for r in actions.pin_rows(app, ("A", 2), "TV")],
                         ["Unpin", "Move Up", "Move Down"])
        self.assertEqual([r[0] for r in actions.pin_rows(app, ("A", 1), "Movies")],
                         ["Unpin", "Move Down"])
        dict(actions.pin_rows(app, ("A", 3), "Music"))["Move Up"]()
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 3), ("A", 2)])

    def test_pin_alone_when_not_pinned(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}])
        app = pin_app(st)
        rows = actions.pin_rows(app, ("A", 2), "TV")
        self.assertEqual([r[0] for r in rows], ["Pin"])
        rows[0][1]()
        self.assertEqual(st.pin_list(), [("A", 1), ("A", 2)])

    def test_hidden_special_pin_keeps_its_place(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, dict(config.LIVE_ENTRY),
                     {"server": "A", "section": 2}])
        app = pin_app(st)                            # no lineup: the Channels pin is hidden
        dict(actions.pin_rows(app, ("A", 2), "TV"))["Move Up"]()
        self.assertEqual(st.pin_list(), [("A", 2), ("livetv", 0), ("A", 1)])


class TestPanelWrap(unittest.TestCase):
    def panel(self):
        from batoplex.screens.panel import PanelScreen
        p = PanelScreen.__new__(PanelScreen)
        p.rows = [("home", "Home", "", False), ("search", "Search", "", False),
                  ("more", "More", "", False), ("settings", "Settings", "", False)]
        p.top = 0
        p.focus = 0
        p.app = types.SimpleNamespace(rep=False, settings={"safe": dict(
            left=16, top=16, right=16, bottom=16)})
        return p

    def test_wraps_on_a_new_press_only(self):
        from batoplex import inputs as I
        p = self.panel()
        p.handle(I.UP)
        self.assertEqual(p.focus, 3)                 # Settings
        p.handle(I.DOWN)
        self.assertEqual((p.focus, p.top), (0, 0))
        p.app.rep = True
        p.handle(I.UP)
        self.assertEqual(p.focus, 0)                 # a held Up stops at the top


class TestMenuNoRows(unittest.TestCase):
    def app(self, top):
        app = A.App.__new__(A.App)
        app.stack = [top]
        app.music = types.SimpleNamespace(active=False)
        app.vqueue = []
        app.opened = []
        app.overlay = lambda scr, kind="menu": app.opened.append(scr)
        return app

    def test_no_own_rows_gives_settings_alone(self):
        top = types.SimpleNamespace(menu_items=lambda: [], menu_title="X")
        app = self.app(top)
        self.assertTrue(app.open_menu())
        m = app.opened[0]
        self.assertEqual(([r[0] for r in m.rows], m.title), (["Settings"], ""))

    def test_no_menu_items_opens_nothing(self):
        app = self.app(types.SimpleNamespace())
        self.assertFalse(app.open_menu())
        self.assertEqual(app.opened, [])

    def test_tool_menu_has_no_app_rows(self):
        top = types.SimpleNamespace(menu_items=lambda: [("Reset", None)], menu_app_rows=False,
                                    menu_title="SCREEN MARGINS")
        app = self.app(top)
        app.open_menu()
        self.assertEqual([r[0] for r in app.opened[0].rows], ["Reset"])

    def test_move_mode_opens_nothing(self):
        top = types.SimpleNamespace(menu_items=lambda: [("A", None)], moving=1)
        self.assertFalse(self.app(top).open_menu())


if __name__ == "__main__":
    unittest.main()


class TestHiddenPinFromMore(unittest.TestCase):
    """A pinned Channels row that the panel hides (all channels off) is Unpin in More,
    never a second pin."""

    def test_unpin_not_duplicate(self):
        st = settings()
        st.set_pins([{"server": "A", "section": 1}, dict(config.LIVE_ENTRY)])
        app = pin_app(st)
        app.lineup = types.SimpleNamespace(count=lambda: 2, visible=lambda: [])
        key = (config.LIVE_PIN, 0)
        rows = actions.pin_rows(app, key, "Channels")
        self.assertEqual([r[0] for r in rows], ["Unpin"])
        actions.toggle_pin(app, key, "Channels")
        self.assertEqual(st.pin_list(), [("A", 1)])


class TestHoldNoOwnRows(unittest.TestCase):
    """A hold on a row with no options of its own selects."""

    def test_hold_gives_no_settings_only_menu(self):
        app = A.App.__new__(A.App)
        app.stack = [types.SimpleNamespace(menu_items=lambda: [])]
        app.music = types.SimpleNamespace(active=False)
        app.vqueue = []
        app.opened = []
        app.overlay = lambda scr, kind="menu": app.opened.append(scr)
        app._holding = True
        self.assertFalse(app.open_menu())
        app._holding = False
        self.assertTrue(app.open_menu())             # Menu still gives the Settings row
