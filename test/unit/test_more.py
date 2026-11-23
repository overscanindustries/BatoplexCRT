import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import config, inputs  # noqa: E402
from batoplex.app import App as RealApp  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.sources import Sources  # noqa: E402


class Prov:
    def __init__(self, sid, name, owned, titles):
        self.server_id, self.server_name, self.owned = sid, name, owned
        self.titles = titles

    def libraries(self, cached=False):
        return [Item(str(i), "library", t, section_id=i, server_id=self.server_id)
                for i, t in self.titles]


class Secrets:
    def __init__(self, servers):
        self.v = {"servers": servers}

    def get(self, k, default=None):
        return default


class App:
    def __init__(self, providers, servers=None):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.sources = Sources(providers, home_id=providers[0].server_id)
        self.secrets = Secrets(servers or {})
        self.opened, self.popped, self.stack, self.pushed = [], 0, [], []
        self.music = type("M", (), {"active": False, "live": False, "track": None, "version": 0,
                                    "drop_stations": lambda self, urls: None})()

    def open_item(self, it):
        self.opened.append(it)

    def saved(self, ok):
        return ok

    def push(self, scr):
        self.pushed.append(scr)

    def pop(self):
        self.popped += 1

    def post(self, fn):
        fn()

    def pins_changed(self):
        self.changed = getattr(self, "changed", 0) + 1

    def open_panel(self, focus=None):
        self.panel = focus

    top = property(lambda self: self.stack[-1])
    back = RealApp.back


class TestMore(unittest.TestCase):
    def make(self):
        from batoplex.screens.more import MoreScreen
        a = Prov("A", "Home Server", True, [(6, "Movies"), (5, "TV")])
        b = Prov("B", "Friend", False, [(2, "Anime")])
        app = App([a, b], servers={"C": {"name": "Cabin", "owned": True},
                                   "legacy": {"name": "Home Server", "owned": True}})
        app.settings.set_pins([{"server": "A", "section": 6, "title": "Movies"}])
        return app, MoreScreen(app)

    def test_groups_and_values(self):
        _app, m = self.make()
        self.assertEqual([(r[0], r[2], r[3]) for r in m.rows()], [
            ("head", "Home Server", ""), ("lib", "Movies", "Pinned"), ("lib", "TV", ""),
            ("head", "Friend • Shared", ""), ("lib", "Anime", ""),
            ("head", "Cabin", ""), ("info", "Unavailable", ""),
            ("gap", "", ""), ("live", "Set Up Channels", ""),
            ("gap", "", ""), ("add", "Add Radio Stations", "")])
        self.assertEqual(m._focused().title, "Movies")   # opens on the first library

    def test_pin_unpin_and_order(self):
        app, m = self.make()
        for _ in range(2):
            m.handle(inputs.DOWN)                    # from Movies: Anime (shared server)
        m.handle(inputs.X)
        self.assertEqual(app.settings.pin_list(), [("A", 6), ("B", 2)])
        labels = [r[0] for r in m.menu_items()]
        self.assertEqual(labels, ["Unpin", "Move Up"])
        dict(m.menu_items())["Move Up"]()
        self.assertEqual(app.settings.pin_list(), [("B", 2), ("A", 6)])
        m.handle(inputs.X)
        self.assertEqual(app.settings.pin_list(), [("A", 6)])
        m.handle(inputs.OK)
        self.assertEqual([i.title for i in app.opened], ["Anime"])

    def test_info_row_does_nothing(self):
        app, m = self.make()
        m.handle(inputs.RT)                          # the last row: Add Radio Stations
        m.handle(inputs.UP)
        m.handle(inputs.UP)                          # above Set Up Channels: Unavailable
        self.assertIsNone(m._focused())
        m.handle(inputs.OK)
        m.handle(inputs.X)
        self.assertEqual(app.opened, [])
        self.assertEqual(m.menu_items(), [])


class TestMoreLate(unittest.TestCase):
    def test_server_joins_while_open_and_back_opens_panel(self):
        from batoplex.screens.more import MoreScreen
        a = Prov("A", "Home Server", True, [(6, "Movies")])
        app = App([a], servers={"B": {"name": "Friend", "owned": False}})
        app.settings.set_pins([{"server": "A", "section": 6}])
        m = MoreScreen(app)
        self.assertIn(("info", "Unavailable"), [(r[0], r[2]) for r in m.rows()])
        app.sources.add(Prov("B", "Friend", False, [(2, "Anime")]))
        m.run_bg = lambda fn, done, fail=None: done(fn())
        m.sources_changed()
        self.assertIn(("lib", "Anime"), [(r[0], r[2]) for r in m.rows()])
        m.panel_origin = "more"                      # opened from the side panel
        app.stack.append(m)
        m.handle(inputs.BACK)
        self.assertEqual((app.popped, app.panel), (1, "more"))

    def test_back_without_panel_origin_only_pops(self):
        from batoplex.screens.more import MoreScreen
        app = App([Prov("A", "Home Server", True, [(6, "Movies")])])
        m = MoreScreen(app)
        app.stack.append(m)
        m.handle(inputs.BACK)
        self.assertEqual((app.popped, getattr(app, "panel", None)), (1, None))


class TestRadioEntries(unittest.TestCase):
    """Add Stations from More and Radio."""

    def setUp(self):
        from batoplex import radio, stationdir
        self.radio, self.p = radio, radio.path()
        self.real_get = stationdir.get
        stationdir.get = lambda: type("D", (), {"search": lambda self, q, limit=50: []})()
        if os.path.exists(self.p):
            os.remove(self.p)

    def tearDown(self):
        from batoplex import stationdir
        stationdir.get = self.real_get
        if os.path.exists(self.p):
            os.remove(self.p)

    def test_more_row_opens_add_stations_with_no_station(self):
        from batoplex.screens.more import MoreScreen
        from batoplex.screens.stations import StationsScreen
        app = App([Prov("A", "Home Server", True, [(6, "Movies")])])
        m = MoreScreen(app)
        m.handle(inputs.DOWN)
        m.handle(inputs.DOWN)                        # past Set Up Live TV and Movies
        self.assertEqual(m.menu_items(), [])
        m.handle(inputs.OK)
        self.assertIsInstance(app.pushed[-1], StationsScreen)

    def test_radio_add_row_reads_again_and_remove(self):
        from batoplex.screens.radio import RadioScreen
        from batoplex.screens.stations import StationsScreen
        app = App([Prov("A", "Home Server", True, [])])
        app.toast = lambda msg: None
        r = RadioScreen(app)
        self.assertEqual([x[2] for x in r.rows()], ["Add Stations"])   # "No stations" is state text
        r.handle(inputs.OK)
        self.assertIsInstance(app.pushed[-1], StationsScreen)
        self.radio.add("Test Jazz One", "https://one.example/s", "u-1")
        self.radio.add("Test Rock Two", "https://two.example/s")
        r.on_resume()
        self.assertEqual([x[2] for x in r.rows()], ["Test Jazz One", "Test Rock Two", "Add Stations"])
        r.handle(inputs.DOWN)
        self.assertEqual([t for t, _f in r.menu_items()], ["Remove from Radio"])
        r.menu_items()[0][1]()
        self.assertEqual([x[2] for x in r.rows()], ["Test Jazz One", "Add Stations"])
        with open(self.p) as f:
            self.assertEqual(f.read(), "#EXTM3U\n#RADIOBROWSER:u-1\n#EXTINF:-1,Test Jazz One\n"
                                       "https://one.example/s\n")
        r.handle(inputs.DOWN)
        self.assertEqual(r.menu_items(), [])


if __name__ == "__main__":
    unittest.main()
