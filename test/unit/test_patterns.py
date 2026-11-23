import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex.screens import patterns  # noqa: E402


class TestPatterns(unittest.TestCase):
    def test_every_pattern_and_variant_draws_full_raster(self):
        s = pygame.Surface((patterns.W, patterns.H))
        for name, fn, variants in patterns.PATTERNS:
            for v in range(len(variants)):
                s.fill((1, 2, 3))
                fn(s, v)
                self.assertNotEqual(s.get_at((patterns.W - 1, patterns.H - 1))[:3], (1, 2, 3), name)

    def test_pluge_steps(self):
        s = pygame.Surface((patterns.W, patterns.H))
        patterns.pluge(s, 0)
        self.assertEqual(s.get_at((150, 200))[:3], (0, 0, 0))
        self.assertEqual(s.get_at((260, 200))[:3], (5, 5, 5))


if __name__ == "__main__":
    unittest.main()
