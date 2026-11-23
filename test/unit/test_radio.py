import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import radio  # noqa: E402


class TestParse(unittest.TestCase):
    def test_names_host_fallback_and_schemes(self):
        text = ("﻿#EXTM3U\r\n#EXTINF:-1,Groove Room\r\nhttps://a.example/x.aac\r\n"
                "http://b.example:8000/live\r\n#EXTINF:-1,Bad\r\nfile:///etc/passwd\r\n"
                "#EXTINF:-1,After bad\r\nrtmp://c.example/s\r\nhttps://d.example/s.pls\r\n")
        self.assertEqual(radio.parse(text), [
            ("Groove Room", "https://a.example/x.aac"),
            ("b.example", "http://b.example:8000/live"),
            ("d.example", "https://d.example/s.pls")])     # a name never moves past a URL

    def test_control_chars_and_bad_line(self):
        text = "#EXTINF:-1,Bad\x00Name\nhttp://[x\nhttps://ok.example/s\n"
        self.assertEqual(radio.parse(text), [("ok.example", "https://ok.example/s")])
        self.assertEqual(radio.parse("#EXTINF:-1,A\x00B\nhttps://z.example/\n"),
                         [("AB", "https://z.example/")])

    def test_station_limit(self):
        text = "\n".join("https://h%d.example/s" % i for i in range(radio.MAX_STATIONS + 5))
        self.assertEqual(len(radio.parse(text)), radio.MAX_STATIONS)

    def test_file_read_again_on_change(self):
        p = radio.path()
        with open(p, "w") as f:
            f.write("#EXTINF:-1,One\nhttps://one.example/s\n")
        self.assertEqual([s.title for s in radio.stations()], ["One"])
        with open(p, "w") as f:
            f.write("#EXTINF:-1,One\nhttps://one.example/s\n#EXTINF:-1,Two\nhttps://two.example/s\n")
        os.utime(p, ns=(1, 2_000_000_000_000_000_000))
        self.assertEqual([s.title for s in radio.stations()], ["One", "Two"])
        os.remove(p)
        self.assertEqual(radio.stations(), [])

    def test_split_title(self):
        self.assertEqual(radio.split_title("Boards - Roygbiv"), ("Roygbiv", "Boards"))
        self.assertEqual(radio.split_title("A - B - C"), ("B - C", "A"))
        self.assertEqual(radio.split_title("Station ID"), ("Station ID", ""))
        self.assertEqual(radio.split_title(None), ("", ""))


class TestEdit(unittest.TestCase):
    """Add Stations writes."""

    def setUp(self):
        self.p = radio.path()
        if os.path.exists(self.p):
            os.remove(self.p)

    def tearDown(self):
        if os.path.exists(self.p):
            os.remove(self.p)

    def put(self, data):
        with open(self.p, "wb") as f:
            f.write(data)

    def get(self):
        with open(self.p, "rb") as f:
            return f.read()

    def test_append_to_no_file(self):
        self.assertTrue(radio.add("Test\x01 Jazz   One", "https://a.example/s", "u-1"))
        self.assertEqual(self.get(), b"#EXTM3U\n#RADIOBROWSER:u-1\n#EXTINF:-1,Test Jazz One\n"
                                     b"https://a.example/s\n")
        self.assertEqual(radio.parse(self.get().decode()), [("Test Jazz One", "https://a.example/s")])

    def test_append_keeps_bytes_crlf_bom_no_last_line_end(self):
        old = b"\xef\xbb\xbf#EXTM3U\r\n# my list\r\n#EXTINF:-1,Mine\r\nhttp://m.example/\xff"
        self.put(old)
        radio.add("", "https://b.example/s")                    # empty name: the host
        self.assertEqual(self.get(), old + b"\r\n#EXTINF:-1,b.example\r\nhttps://b.example/s\r\n")

    def test_remove_leaves_all_other_bytes(self):
        old = (b"#EXTM3U\n# keep\n\n#EXTINF:-1,Mine\nhttp://m.example/\n"
               b"#RADIOBROWSER:u-2\n#EXTINF:-1,Test Jazz Two\n# note\nhttps://t.example/s\n"
               b"#EXTINF:-1,Last\nhttp://l.example/\n")
        self.put(old)
        self.assertTrue(radio.remove(["https://t.example/s"]))
        self.assertEqual(self.get(), b"#EXTM3U\n# keep\n\n#EXTINF:-1,Mine\nhttp://m.example/\n"
                                     b"# note\n#EXTINF:-1,Last\nhttp://l.example/\n")
        self.assertEqual([n for n, _u in radio.parse(self.get().decode())], ["Mine", "Last"])
        self.assertFalse(radio.remove(["https://t.example/s"]))

    def test_dedupe_by_both_urls_and_uuid(self):
        self.put(b"#EXTINF:-1,Hand\nhttps://r.example/s\n#RADIOBROWSER:u-3\nhttps://x.example/\n")
        text = radio.read_text()
        self.assertTrue(radio.has(text, ["https://new.example/", "https://r.example/s"]))
        self.assertTrue(radio.has(text, ["https://other.example/"], "u-3"))
        self.assertFalse(radio.has(text, ["https://other.example/"], "u-9"))
        before = self.get()
        self.assertFalse(radio.add("Hand", "https://r.example/s", "u-4"))
        self.assertFalse(radio.add("Same", "https://y.example/", "u-3"))
        self.assertEqual(self.get(), before)
        radio.remove(["https://none.example/"], "u-3")
        self.assertEqual(self.get(), b"#EXTINF:-1,Hand\nhttps://r.example/s\n")

    def test_full_and_size_limits(self):
        self.put("".join("https://h%d.example/s\n" % i
                         for i in range(radio.MAX_STATIONS)).encode())
        with self.assertRaisesRegex(radio.Refused, "Radio is full"):
            radio.add("X", "https://new.example/")
        self.put(b"# " + b"x" * (radio.MAX_BYTES - 40) + b"\n")
        with self.assertRaisesRegex(radio.Refused, "Radio is full"):
            radio.add("X", "https://new.example/long")
        self.put(b"#" * (radio.MAX_BYTES + 1))
        with self.assertRaisesRegex(radio.Refused, "Can't change radio.m3u"):
            radio.add("X", "https://new.example/")
        with self.assertRaisesRegex(radio.Refused, "Can't change radio.m3u"):
            radio.remove(["https://new.example/"])

    def test_write_error_changes_nothing(self):
        self.put(b"https://a.example/\n")
        real = os.replace

        def fail(*a):
            raise PermissionError("read-only")
        os.replace = fail                      # the test image runs as root
        try:
            with self.assertRaisesRegex(radio.Refused, "Can't save station"):
                radio.add("X", "https://b.example/")
        finally:
            os.replace = real
        self.assertEqual(self.get(), b"https://a.example/\n")
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.p)) if n.startswith(".radio-")], [])

    def test_symlink_and_mode_stay(self):
        target = os.path.join(tempfile.mkdtemp(), "list.m3u")
        with open(target, "wb") as f:
            f.write(b"#EXTM3U\n")
        os.chmod(target, 0o640)
        os.symlink(target, self.p)
        radio.add("Test Rock", "https://c.example/")
        self.assertTrue(os.path.islink(self.p))
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o640)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"#EXTM3U\n#EXTINF:-1,Test Rock\nhttps://c.example/\n")


if __name__ == "__main__":
    unittest.main()
