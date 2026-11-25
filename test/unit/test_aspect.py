import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import aspect  # noqa: E402


def meta(x1, x2, y1, y2):
    return {"lavfi.cropdetect.x1": str(x1), "lavfi.cropdetect.x2": str(x2),
            "lavfi.cropdetect.y1": str(y1), "lavfi.cropdetect.y2": str(y2)}


class TestAspect(unittest.TestCase):
    def test_panscan(self):
        self.assertEqual(aspect.panscan("letterbox", 16 / 9), 0.0)
        self.assertEqual(aspect.panscan("zoom", 16 / 9), 1.0)
        self.assertAlmostEqual(aspect.panscan("14:9", 16 / 9), 0.4286, places=3)
        self.assertEqual(aspect.panscan("zoom", 4 / 3), 0.0)          # nothing to fill
        self.assertEqual(aspect.panscan("14:9", 1.5), 0.0)            # narrower than 14:9
        self.assertEqual(aspect.panscan("bogus", 16 / 9), 0.0)
        self.assertEqual(aspect.panscan("zoom", 0), 0.0)               # no video params yet

    def test_4x3_shapes_are_not_wide(self):
        for a in (4 / 3, 720 * 10 / 11 / 480.0, 1.37, 1.40):    # 4:3, SD 10:11, Academy
            for mode in ("14:9", "zoom"):
                self.assertEqual(aspect.panscan(mode, a), 0.0, (mode, a))
        self.assertEqual(aspect.panscan("zoom", 1.43), 1.0)
        self.assertEqual(aspect.panscan("zoom", 4 / 3, screen=1.2), 1.0)   # a narrow area
        self.assertEqual(aspect.panscan("zoom", 4 / 3, screen=1.48), 1.0)  # a wide area
        self.assertEqual(aspect.panscan("14:9", 4 / 3, screen=1.48), 0.0)
        self.assertEqual(aspect.panscan("zoom", 1.43, screen=1.48), 0.0)   # near its shape

    def test_thin_top_and_bottom_lines_are_not_bars(self):
        self.assertIsNone(aspect.crop_from_meta(meta(0, 719, 12, 467), 720, 480, 4 / 3))
        self.assertEqual(aspect.crop_from_meta(meta(0, 719, 60, 419), 720, 480, 4 / 3),
                         (720, 360, 0, 60))                       # a 16:9 film in 4:3
        self.assertEqual(aspect.crop_from_meta(meta(81, 558, 0, 479), 640, 480, 4 / 3),
                         (480, 480, 80, 0))                       # side bars: no rule

    def test_crop_letterboxed_4x3(self):
        self.assertEqual(aspect.crop_from_meta(meta(0, 639, 60, 419), 640, 480), (640, 360, 0, 60))

    def test_crop_needs_even_bars(self):
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 0, 419), 640, 480))   # bottom only
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 3, 476), 640, 480))   # too thin
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 200, 279), 640, 480))  # over half

    def test_crop_pillarbox_and_odd(self):
        self.assertEqual(aspect.crop_from_meta(meta(81, 558, 0, 479), 640, 480), (480, 480, 80, 0))

    def test_crop_one_direction_only(self):
        # A wide film in a 16:9 transcode, in a scene that is dark at the sides.
        self.assertEqual(aspect.crop_from_meta(meta(31, 681, 52, 352), 718, 404), (718, 304, 0, 50))
        self.assertEqual(aspect.crop_from_meta(meta(81, 558, 20, 459), 640, 480), (480, 480, 80, 0))

    def test_frac_round_trip(self):
        f = aspect.to_frac((3840, 1608, 0, 276), 3840, 2160)
        self.assertEqual(aspect.from_frac(f, 718, 404), (718, 300, 0, 52))   # 51.6 px of bar
        self.assertIsNone(aspect.from_frac((0.0, 0.0), 718, 404))
        self.assertIsNone(aspect.from_frac(None, 718, 404))
        self.assertIsNone(aspect.from_frac(f, 0, 0))
        for w in (360, 404, 720, 1080):                    # no 2 px lost to float error
            for cut in range(2, w // 4, 2):
                self.assertEqual(aspect.from_frac(aspect.to_frac((w, w - 2 * cut, 0, cut), w, w), w, w)[3], cut)

    def test_blank_and_bad_meta(self):
        self.assertTrue(aspect.blank(meta(639, 0, 479, 0)))
        self.assertIsNone(aspect.crop_from_meta(meta(639, 0, 479, 0), 640, 480))
        self.assertFalse(aspect.blank({}))
        self.assertIsNone(aspect.crop_from_meta(None, 640, 480))
        self.assertIsNone(aspect.crop_from_meta({"lavfi.cropdetect.x1": "z"}, 640, 480))


if __name__ == "__main__":
    unittest.main()
