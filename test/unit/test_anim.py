import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import anim, draw  # noqa: E402
from batoplex.tokens import FAST, FIELD_MS  # noqa: E402


def run(a, t0=10.0, every=1):
    """Values of a from the press field on; every: fields between frames (a late frame)."""
    out, t = [], t0
    while a.active:
        out.append(a.value)
        t += every * FIELD_MS / 1000
        anim.tick(t)
    return out


def slide(frm, to, t0=10.0):
    anim.tick(t0)
    a = anim.Anim(FAST, anim.EASE_OUT, q=4, min_step=8)
    a.start(frm, to)
    return a


class TestAnim(unittest.TestCase):
    def test_home_slide_values(self):
        """Quad ease-out, 117 ms, q 4 on 132 px."""
        self.assertEqual(run(slide(0, 132)), [36, 64, 88, 108, 120, 132])
        self.assertEqual(run(slide(132, 0)), [96, 68, 44, 24, 12, 0])

    def test_steps_are_quantised_and_at_least_min_step(self):
        for d in (40, 52, 96, 132, 140, 152, 156, 176, 256, 296, -96, -152):
            vals = [0] + run(slide(0, d))
            steps = [b - a for a, b in zip(vals, vals[1:])]
            self.assertEqual(vals[-1], d)
            self.assertTrue(all(s % 4 == 0 and abs(s) >= 8 for s in steps), (d, steps))
            self.assertLessEqual(len(steps), 7, d)

    def test_first_field_shows_progress(self):
        a = slide(0, 132)
        self.assertNotEqual(a.value, 0)

    def test_late_frame_keeps_the_duration(self):
        vals = run(slide(0, 132), every=2)
        self.assertEqual(vals[-1], 132)
        self.assertEqual(len(vals), 4)                   # 117 ms at 2 fields a frame
        self.assertTrue(all(v % 4 == 0 for v in vals))

    def test_retarget_starts_from_the_shown_value(self):
        a = slide(0, 132)
        anim.tick(10.0 + 2 * FIELD_MS / 1000)
        shown = a.value
        a.retarget(264)
        self.assertEqual(a.frm, shown)
        self.assertGreater(a.value, shown)               # no jump back, progress at once
        vals = run(a, t0=10.0 + 2 * FIELD_MS / 1000)
        self.assertEqual(vals[-1], 264)

    def test_linear_alpha_and_end(self):
        anim.tick(5.0)
        a = anim.Anim(167, anim.LINEAR)
        a.start(0, 1)
        vals = run(a, t0=5.0)
        self.assertAlmostEqual(vals[0], FIELD_MS / 167, places=3)
        self.assertEqual(vals[-1], 1)
        self.assertEqual(len(vals), 11)               # 167 ms: the 11th field shows the end

    def test_set_is_a_cut(self):
        a = slide(0, 132)
        a.value
        a.set(40)
        self.assertEqual(a.value, 40)


class TestBand(unittest.TestCase):
    def test_band_and_union_equal_a_full_pass(self):
        pygame.display.init()
        rnd = random.Random(4)
        src = pygame.Surface((641, 480))
        for y in range(0, 480, 3):
            for x in range(0, 641, 7):
                src.fill((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)), (x, y, 7, 3))
        f = draw.FlickerFilter((641, 480))
        for mode in ("normal", "light", "off"):
            f.full(src, mode)
            for _ in range(40):
                rs = [pygame.Rect(rnd.randrange(641), rnd.randrange(480), rnd.randrange(1, 200),
                                  rnd.randrange(1, 200)) for _ in range(rnd.randrange(1, 3))]
                for r in rs:
                    src.fill((rnd.randrange(256),) * 3, r)
                f.apply_rect(src, rs[0].unionall(rs[1:]), mode)
                ref = f.apply(src, mode)
                self.assertEqual(pygame.image.tobytes(ref, "RGB"),
                                 pygame.image.tobytes(f.out, "RGB"), (mode, rs))


    def test_band_on_a_canvas_with_stale_alpha_bytes(self):
        """A 32-bit display with an alpha channel (a macOS window) gives the canvas an alpha
        mask; text blits leave alpha 0 in it. A subsurface or copy of it blends by those
        bytes, so a band pass showed old text through new rows (ghost text, gray blocks)."""
        pygame.display.init()
        rnd = random.Random(7)
        argb = (0xFF0000, 0xFF00, 0xFF, 0xFF000000)
        src = pygame.Surface((641, 480), 0, 32, argb)
        hole = pygame.Surface((60, 14), pygame.SRCALPHA)
        hole.fill((255, 255, 255, 0))            # leaves the colour, writes alpha 0

        def paint(r):
            src.fill((rnd.randrange(256),) * 3, r)
            for _ in range(6):
                src.blit(hole, (r.x + rnd.randrange(max(1, r.w)), r.y + rnd.randrange(max(1, r.h))))
        paint(pygame.Rect(0, 0, 641, 480))
        f = draw.FlickerFilter((641, 480))
        for mode in ("normal", "light", "off"):
            f.full(src, mode)
            self.assertEqual(f.out.get_masks()[3], 0, mode)
            for _ in range(20):
                r = pygame.Rect(rnd.randrange(600), rnd.randrange(440), rnd.randrange(1, 300),
                                rnd.randrange(1, 120))
                paint(r)
                f.apply_rect(src, r, mode)
                ref = f.apply(src, mode)
                self.assertEqual(pygame.image.tobytes(ref, "RGB"),
                                 pygame.image.tobytes(f.out, "RGB"), (mode, r))

    def test_opaque_surface_drops_the_alpha_channel(self):
        argb = pygame.Surface((8, 8), 0, 32, (0xFF0000, 0xFF00, 0xFF, 0xFF000000))
        s = draw.opaque_surface((8, 8), argb)
        self.assertEqual(s.get_masks(), (0xFF0000, 0xFF00, 0xFF, 0))
        self.assertFalse(s.copy().get_flags() & pygame.SRCALPHA)
        self.assertFalse(s.subsurface((0, 0, 4, 4)).get_flags() & pygame.SRCALPHA)


class _FreshFilter(draw.FlickerFilter):
    """The reference: every average is a new surface (no kept work surfaces)."""

    def _run(self, src, up, dn, mode, dest=None):
        w, h = src.get_size()
        up.blit(src, (0, -1))
        up.blit(src, (0, h - 1), (0, h - 1, w, 1))
        dn.blit(src, (0, 1))
        dn.blit(src, (0, 0), (0, 0, w, 1))
        avg = pygame.transform.average_surfaces
        if mode == "light":
            pair = [src, avg([src, avg([up, dn])])]
        else:
            pair = [avg([src, up]), avg([src, dn])]
        return avg(pair) if dest is None else avg(pair, dest)


class TestScratch(unittest.TestCase):
    SIZE = (64, 48)

    def src(self, depth, seed):
        rnd = random.Random(seed)
        s = pygame.Surface(self.SIZE, 0, depth)
        for y in range(0, self.SIZE[1], 2):
            for x in range(0, self.SIZE[0], 3):
                s.fill((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)), (x, y, 3, 2))
        return s

    def same(self, a, b, msg):
        self.assertEqual((a.get_bitsize(), a.get_masks()), (b.get_bitsize(), b.get_masks()), msg)
        self.assertEqual(pygame.image.tobytes(a, "RGBA"), pygame.image.tobytes(b, "RGBA"), msg)

    def test_equals_fresh_allocation(self):
        for depth in (32, 24, 16):
            for mode in ("normal", "light"):
                src = self.src(depth, depth)
                f, ref = draw.FlickerFilter(self.SIZE), _FreshFilter(self.SIZE)
                self.same(f.apply(src, mode), ref.apply(src, mode), (depth, mode, "apply"))
                self.same(f.full(src, mode), ref.full(src, mode), (depth, mode, "full"))
                rnd = random.Random(depth + len(mode))
                for bh in (1, 2, 5, 17, 48):
                    r = pygame.Rect(rnd.randrange(40), rnd.randrange(48 - bh + 1), 20, bh)
                    src.fill((rnd.randrange(256),) * 3, r)
                    self.same(f.apply_rect(src, r, mode), ref.apply_rect(src, r, mode),
                              (depth, mode, bh))

    def test_scratch_stays_bounded(self):
        f = draw.FlickerFilter(self.SIZE)
        src = self.src(32, 1)
        f.full(src, "normal")
        for mode in ("normal", "light"):
            for bh in range(1, 49):
                f.apply_rect(src, (0, 0, 10 + bh % 30, bh), mode)
        self.assertLessEqual(len(f._scratch), 3)


if __name__ == "__main__":
    unittest.main()
