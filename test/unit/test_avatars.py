import io
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import avatars, plex  # noqa: E402


def png_header(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr +
            struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))


def jpeg(w, h):
    buf = io.BytesIO()
    pygame.image.save(pygame.Surface((w, h)), buf, "a.jpg")
    return buf.getvalue()


class TestAvatars(unittest.TestCase):
    def test_sizes_from_headers(self):
        self.assertEqual(avatars.image_size(png_header(160, 120)), (160, 120))
        self.assertEqual(avatars.image_size(jpeg(90, 60)), (90, 60))
        self.assertIsNone(avatars.image_size(b"<html>portal</html>"))

    def test_bomb_and_non_image_refused(self):
        self.assertRaises(plex.PlexError, avatars._check, png_header(20000, 20000))
        self.assertRaises(plex.PlexError, avatars._check, b"<html>portal</html>")
        self.assertIsNone(avatars.round_image(png_header(20000, 20000), 112))

    def test_round_cut(self):
        img = avatars.round_image(jpeg(200, 100), 112)
        self.assertEqual(img.get_size(), (112, 112))
        self.assertEqual(img.get_at((0, 0)).a, 0)          # a corner is clear
        self.assertEqual(img.get_at((56, 56)).a, 255)


if __name__ == "__main__":
    unittest.main()
