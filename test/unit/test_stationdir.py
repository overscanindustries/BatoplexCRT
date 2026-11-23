import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import stationdir  # noqa: E402


def st(uuid, name, stream, **kw):
    d = {"stationuuid": uuid, "name": name, "url": stream, "url_resolved": stream, "tags": "jazz",
         "countrycode": "US", "codec": "MP3", "bitrate": 128, "hls": 0, "lastcheckok": 1,
         "ssl_error": 0}
    d.update(kw)
    return d


# A saved reply cut to a few rows, with invented names.
REPLY = [
    st("u-1", "Test Jazz One", "https://one.example/live",
       tags="jazz,smooth jazz,lounge,bebop", url="https://one.example/listen.pls"),
    st("u-2", "Broken", "https://two.example/live", lastcheckok=0),
    st("u-3", "Test Hls", "https://three.example/live.m3u8", hls=1),
    st("u-4", "File", "file:///etc/passwd"),
    st("u-5", "Bad Cert", "https://five.example/live", ssl_error=1),
    st("u-6", "Test Jazz One Copy", "https://one.example/live"),
    st("u-7", "Long " * 45, "http://seven.example:8000/s", codec="UNKNOWN", bitrate=0,
       countrycode=""),
    st("u-8", "\x01 Test\tRock \x7f  Two ", "https://eight.example/s"),
    "not an object",
]


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Opener:
    """Answers by host: a list of replies (bytes, an exception, or a JSON value)."""

    def __init__(self, by_host):
        self.by_host, self.reqs = by_host, []

    def open(self, req, timeout=None):
        self.reqs.append(req)
        host = req.full_url.split("/")[2]
        r = self.by_host[host]
        if isinstance(r, Exception):
            raise r
        return Resp(r if isinstance(r, bytes) else json.dumps(r).encode())


class NoShuffle:
    def shuffle(self, x):
        pass


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make(by_host, names=("de1.api.radio-browser.info",)):
    op = Opener(by_host)
    d = stationdir.Directory(opener=op, resolve=lambda: list(names), clock=Clock(),
                             rng=NoShuffle())
    d.base = None
    return d, op


class TestParse(unittest.TestCase):
    def test_filters_and_line2(self):
        items = stationdir.rows_from(REPLY)
        self.assertEqual([i.extra["uuid"] for i in items], ["u-1", "u-7", "u-8"])
        one, seven, eight = items
        self.assertEqual(one.extra["url"], "https://one.example/live")
        self.assertEqual(one.extra["alt"], "https://one.example/listen.pls")
        self.assertEqual(one.extra["line2"], "jazz • smooth jazz • lounge • US • MP3 128")
        self.assertEqual(seven.title, ("Long " * 16).strip())    # 80 characters at most
        self.assertEqual(seven.extra["line2"], "jazz")        # no bitrate 0, no UNKNOWN
        self.assertEqual(eight.title, "Test Rock Two")

    def test_lone_surrogate_and_line_separator(self):
        ok = {"lastcheckok": 1, "ssl_error": 0, "hls": 0, "stationuuid": "u1"}
        rows = stationdir.rows_from([
            dict(ok, name="Bad\ud800Name", url_resolved="https://a.example/s"),
            dict(ok, name="Cut", url_resolved="http://b.example/x\u2028y"),
            dict(ok, name="Next", url_resolved="http://c.example/x\x85y")])
        self.assertEqual([(r.title, r.id) for r in rows], [("Bad Name", "https://a.example/s")])
        from batoplex import radio
        os.makedirs(os.path.dirname(radio.path()), exist_ok=True)
        self.assertTrue(radio.add("Bad\ud800Name", rows[0].id, "u1"))   # no UnicodeEncodeError
        self.assertTrue(radio.has(radio.read_text(), [rows[0].id]))
        self.assertEqual(radio.remove([rows[0].id]), [rows[0].id])

    def test_not_a_list(self):
        with self.assertRaises(stationdir.BadReply):
            stationdir.rows_from({"a": 1})


class TestRequests(unittest.TestCase):
    def test_headers_only_user_agent_and_accept(self):
        d, op = make({"de1.api.radio-browser.info": REPLY})
        rows = d.search("")
        self.assertEqual((rows[0].key, rows[0].title, len(rows[0].items)), ("popular", "Popular", 3))
        req = op.reqs[0]
        self.assertEqual(req.full_url,
                         "https://de1.api.radio-browser.info/json/stations/topclick/50?hidebroken=true")
        self.assertEqual(dict((k.lower(), v) for k, v in req.header_items()),
                         {"user-agent": "BatoplexCRT/%s" % stationdir.VERSION,
                          "accept": "application/json"})
        d.search("jazz one")
        self.assertIn("/json/stations/search?name=jazz+one&hidebroken=true&order=clickcount"
                      "&reverse=true&limit=50", op.reqs[1].full_url)
        self.assertFalse(any(k.lower().startswith("x-plex") for r in op.reqs
                             for k, _v in r.header_items()))

    def test_failover_and_answering_server_first(self):
        d, op = make({"a.api.radio-browser.info": urllib.error.URLError("down"),
                      "b.api.radio-browser.info": b"{bad json",
                      "c.api.radio-browser.info": REPLY},
                     names=("a.api.radio-browser.info", "b.api.radio-browser.info",
                            "c.api.radio-browser.info"))
        self.assertEqual(len(d.search("jazz")[0].items), 3)
        self.assertEqual(d.servers[0], "https://c.api.radio-browser.info")
        d.search("rock")
        self.assertEqual(op.reqs[-1].full_url.split("/")[2], "c.api.radio-browser.info")

    def test_all_fail_raises_and_looks_up_again(self):
        calls = []
        d, op = make({"a.api.radio-browser.info": socket.timeout("slow")})
        d.resolve = lambda: calls.append(1) or ["a.api.radio-browser.info"]
        for _ in range(2):
            with self.assertRaises(OSError):
                d.search("jazz")
        self.assertEqual(len(calls), 2)

    def test_bad_reverse_name_dropped_and_fallback(self):
        import batoplex.stationdir as sd
        real_gai, real_rev = socket.getaddrinfo, socket.gethostbyaddr
        socket.getaddrinfo = lambda *a, **k: [(0, 0, 0, "", ("10.0.0.1", 443)),
                                              (0, 0, 0, "", ("10.0.0.2", 443))]
        socket.gethostbyaddr = lambda ip: ({"10.0.0.1": "evil.example.com",
                                            "10.0.0.2": "DE1.api.radio-browser.info."}[ip], [], [])
        try:
            self.assertEqual(sd._resolve(), ["de1.api.radio-browser.info"])
        finally:
            socket.getaddrinfo, socket.gethostbyaddr = real_gai, real_rev
        d, _op = make({}, names=())
        self.assertEqual(d._servers(), ["https://all.api.radio-browser.info"])

    def test_reply_cap(self):
        d, _op = make({"de1.api.radio-browser.info": b"[" + b" " * stationdir.MAX_REPLY + b"]"})
        with self.assertRaises(stationdir.BadReply):
            d.search("jazz")

    def test_caches(self):
        d, op = make({"de1.api.radio-browser.info": REPLY})
        d.search("")
        d.search("")
        d.clock.t = stationdir.POPULAR_S + 1
        d.search("")
        self.assertEqual(len(op.reqs), 2)                   # Popular: 15 minutes
        for i in range(9):
            d.search("q%d" % i)
        d.search("q8")
        d.search("q0")                                      # the 9th query pushed q0 out
        self.assertEqual(len(op.reqs), 2 + 9 + 1)

    def test_test_base_only_localhost(self):
        for v, want in (("http://127.0.0.1:8123", "http://127.0.0.1:8123"),
                        ("http://evil.example:8123", None), ("https://127.0.0.1:1", None),
                        ("http://u@127.0.0.1:8123", None)):
            os.environ["BATOPLEX_RADIO_DIR"] = v
            self.assertEqual(stationdir._test_base(), want, v)
        del os.environ["BATOPLEX_RADIO_DIR"]


if __name__ == "__main__":
    unittest.main()
