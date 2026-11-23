import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from batoplex import config, inputs  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens.episodes import EpisodeScreen  # noqa: E402


class Prov:
    def __init__(self):
        self.fail_seasons, self.fail_eps = True, True

    def seasons(self, show_id):
        if self.fail_seasons:
            raise Exception("network: timed out")
        return [Item("s1", "season", "Season 1", index=1)]

    def episodes(self, sid):
        if self.fail_eps:
            raise Exception("network: timed out")
        return [Item("e1", "episode", "Pilot", index=1)]


class Sources:
    def __init__(self, p):
        self.p = p

    def provider(self, sid):
        return self.p


class App:
    def __init__(self, p):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.sources = Sources(p)

    def pop(self):
        pass


def sync(self, fn, done, fail=None):
    try:
        r = fn()
    except Exception as e:
        fail(e)
        return
    done(r)


class TestEpisodes(unittest.TestCase):
    def setUp(self):
        self.real = Screen.run_bg
        Screen.run_bg = sync

    def tearDown(self):
        Screen.run_bg = self.real

    def test_failed_load_input_and_retry(self):
        p = Prov()
        scr = EpisodeScreen(App(p), "show", "Show")
        for a in (inputs.DOWN, inputs.UP, inputs.LB, inputs.RB, inputs.X, inputs.Y):
            scr.handle(a)                             # no season: nothing may raise
        p.fail_seasons = False
        scr.handle(inputs.OK)                         # retry: seasons load, episodes fail
        self.assertEqual(len(scr.seasons), 1)
        self.assertTrue(scr.error)
        scr.handle(inputs.DOWN)
        p.fail_eps = False
        scr.handle(inputs.OK)
        self.assertEqual(scr.episode.title, "Pilot")
        self.assertIsNone(scr.error)


if __name__ == "__main__":
    unittest.main()
