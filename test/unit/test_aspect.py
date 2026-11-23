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

    def test_next_mode_wraps(self):
        self.assertEqual(aspect.next_mode("letterbox"), "14:9")
        self.assertEqual(aspect.next_mode("zoom"), "letterbox")
        self.assertEqual(aspect.next_mode("letterbox", -1), "zoom")

    def test_crop_letterboxed_4x3(self):
        self.assertEqual(aspect.crop_from_meta(meta(0, 639, 60, 419), 640, 480), (640, 360, 0, 60))

    def test_crop_needs_even_bars(self):
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 0, 419), 640, 480))   # bottom only
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 3, 476), 640, 480))   # too thin
        self.assertIsNone(aspect.crop_from_meta(meta(0, 639, 200, 279), 640, 480))  # over half

    def test_crop_pillarbox_and_odd(self):
        self.assertEqual(aspect.crop_from_meta(meta(81, 558, 0, 479), 640, 480), (480, 480, 80, 0))

    def test_blank_and_bad_meta(self):
        self.assertTrue(aspect.blank(meta(639, 0, 479, 0)))
        self.assertIsNone(aspect.crop_from_meta(meta(639, 0, 479, 0), 640, 480))
        self.assertFalse(aspect.blank({}))
        self.assertIsNone(aspect.crop_from_meta(None, 640, 480))
        self.assertIsNone(aspect.crop_from_meta({"lavfi.cropdetect.x1": "z"}, 640, 480))


if __name__ == "__main__":
    unittest.main()
