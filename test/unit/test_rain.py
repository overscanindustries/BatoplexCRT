import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import rain  # noqa: E402


class TestRain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_levels_by_distance(self):
        self.assertEqual([rain.level(d, 9) for d in range(-1, 10)],
                         [None, 0, 1, 1, 2, 2, 3, 3, 4, 4, None])
        for n in range(*rain.LENGTH):
            self.assertEqual(rain.level(n - 1, n), 4)

    def test_a_step_moves_the_head_one_cell(self):
        r = rain.Rain("off", random.Random(1))
        col = r.cols[5]
        r._start(col, 0.0)
        col.length = 6
        heads = []
        for _ in range(30):
            if col.head is None:
                break
            r.step_column(5, 0.0)
            if col.head is not None:
                heads.append(col.head)
                self.assertEqual(r.cells[col.head][5][1] if col.head < rain.ROWS else 0, 0)
        self.assertEqual(heads, list(range(len(heads))))      # one full cell a step
        self.assertIsNone(col.head)                           # the tail left: it waits
        self.assertEqual(r.lit, set())
        self.assertEqual(len(rain.CHARS), 60)

    def test_cells_on_the_grid_and_steps_on_time(self):
        r = rain.Rain("normal", random.Random(2))
        t = 0.0
        for _ in range(600):                                 # 10 s at 60 fields/s
            t += 1 / 60.0
            r.step(t)
        self.assertTrue(r.lit)
        for (row, c) in r.lit:
            self.assertTrue(0 <= row < rain.ROWS and 0 <= c < rain.COLS)
        # nothing outside a lit cell: every pixel off the lit cells is black
        mask = pygame.Surface(r.surface.get_size())
        mask.blit(r.surface, (0, 0))
        mask.set_colorkey((0, 0, 0))
        for (row, c) in r.lit:
            mask.fill((0, 0, 0), (c * 16, row * 24, 16, 24))
        self.assertEqual(mask.get_bounding_rect().w, 0)

    def test_filtered_atlas_keeps_cell_edges_black(self):
        for mode in ("normal", "light"):
            atlas = rain.build_atlas(mode)
            self.assertEqual((len(atlas), len(atlas[0])), (5, 60))
            for lv in atlas:
                for cell in lv:
                    self.assertEqual(cell.get_size(), (16, 24))
                    for x in range(16):
                        self.assertEqual(cell.get_at((x, 0))[:3], (0, 0, 0))
                        self.assertEqual(cell.get_at((x, 23))[:3], (0, 0, 0))
            head = atlas[0][0].copy()
            head.set_colorkey((0, 0, 0))
            self.assertGreater(head.get_bounding_rect().h, 8)          # a glyph drew


if __name__ == "__main__":
    unittest.main()
