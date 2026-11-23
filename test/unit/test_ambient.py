import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import ambient, mpv, sounds, theme, tokens  # noqa: E402

F = tokens.FIELD_MS


class TestAmbient(unittest.TestCase):
    def test_starts_after_idle_moves_and_stops_at_a_press(self):
        a = ambient.Ambient()
        self.assertFalse(a.update(True, 7.9, 1000.0))
        self.assertTrue(a.update(True, 8.0, 1000.0))          # on, first fade frame
        self.assertTrue(a.on)
        off0, alpha0 = a.shown
        self.assertTrue(0 < alpha0 < 255)
        frames = [a.update(True, 9.0, 1000.0 + k * F) for k in range(1, 61)]
        self.assertTrue(all(frames[:35]))                      # the 600 ms fade: every field
        self.assertEqual(a.shown[1], 255)
        moves = frames[36:]
        self.assertIn(sum(moves), (11, 12, 13))                # then a step each 2nd field
        self.assertEqual(a.shown[0] % ambient.STEP_PX, 0)
        self.assertTrue(a.stop())                              # the press: a cut
        self.assertFalse(a.on)
        self.assertFalse(a.update(True, 0.0, 3000.0))          # idle starts again

    def test_off_when_not_allowed(self):
        a = ambient.Ambient()
        a.update(True, 9.0, 0.0)
        self.assertTrue(a.update(False, 9.0, F))               # saver, playback, other screen
        self.assertFalse(a.on)
        self.assertFalse(a.update(False, 99.0, 2 * F))

    def test_only_ribbon_without_high_contrast(self):
        theme.apply({"theme": "ribbon"})
        self.assertTrue(ambient.enabled())
        theme.apply({"theme": "ribbon", "high_contrast": True})
        self.assertFalse(ambient.enabled())
        theme.apply({"theme": "midnight"})
        self.assertFalse(ambient.enabled())
        theme.apply({})

    def test_strip_edges_are_bg(self):
        for x in range(0, ambient.W, 7):
            self.assertEqual(ambient.coverage(x, 0), 0.0)
            self.assertEqual(ambient.coverage(x, ambient.H - 1), 0.0)
        self.assertGreater(max(ambient.coverage(x, y) for x in range(0, 641, 40)
                               for y in range(ambient.H)), 0.9)


class TestSounds(unittest.TestCase):
    def test_gating(self):
        on, off = {"menu_sounds": True}, {"menu_sounds": False}
        self.assertTrue(sounds.wanted(on, "hearth", busy=False))
        self.assertFalse(sounds.wanted(off, "hearth", busy=False))   # default Off
        self.assertFalse(sounds.wanted(on, None, busy=False))        # theme without sounds
        self.assertFalse(sounds.wanted(on, "hearth", busy=True))     # video, music, theme music

    def test_mixer_closes_before_mpv_starts(self):
        pygame.init()
        self.assertTrue(sounds.play("hearth", "move"))       # SDL dummy audio in the tests
        self.assertTrue(sounds.is_open())
        seen = []

        def popen(*a, **k):
            seen.append(sounds.is_open())
            raise OSError("no mpv here")
        m = mpv.Mpv("mpv", "test")
        with mock.patch.object(mpv.subprocess, "Popen", popen):
            with self.assertRaises(OSError):
                m.start(["mpv"], os.path.join(tempfile.mkdtemp(), "s"))
        self.assertEqual(seen, [False])
        self.assertFalse(pygame.mixer.get_init())


if __name__ == "__main__":
    unittest.main()
