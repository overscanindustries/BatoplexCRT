import http.server
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import plex  # noqa: E402


def serve(handler):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestRedirects(unittest.TestCase):
    def setUp(self):
        seen = self.seen = []

        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("X-Plex-Token"))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass
        self.other = serve(Other)
        target = "http://127.0.0.1:%d/x" % self.other.server_port

        class Redir(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/same":
                    self.send_response(302)
                    self.send_header("Location", "/ok")
                elif self.path == "/ok":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok": 1}')
                    return
                else:
                    self.send_response(302)
                    self.send_header("Location", target)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.redir = serve(Redir)
        self.base = "http://127.0.0.1:%d" % self.redir.server_port

    def tearDown(self):
        self.other.shutdown()
        self.redir.shutdown()

    def test_cross_host_redirect_refused_token_not_sent(self):
        with self.assertRaises(plex.PlexError):
            plex._request(self.base + "/away", {"X-Plex-Token": "secret"})
        self.assertEqual(self.seen, [])

    def test_same_origin_redirect_followed(self):
        self.assertEqual(plex._request(self.base + "/same", {"X-Plex-Token": "t"}), {"ok": 1})


class TestMenuFits(unittest.TestCase):
    def test_menu_inside_safe_area_at_wide_margins(self):
        from batoplex.screens.menu import MenuScreen
        safe = {"left": 72, "right": 72, "top": 56, "bottom": 56}
        app = type("A", (), {"settings": {"safe": safe}})()
        m = MenuScreen(app, None, "T", [("Row %d" % i, None) for i in range(9)])
        x, y, w, h = m.rect()
        self.assertGreaterEqual(y, 56)
        self.assertLessEqual(y + h, 480 - 56)


if __name__ == "__main__":
    unittest.main()
