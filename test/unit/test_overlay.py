import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(__file__))

import pygame  # noqa: E402

from batoplex import overlay  # noqa: E402
from batoplex.overlay import Recorder  # noqa: E402
from fake_mpv import FakeMpv  # noqa: E402
from test_playback import SETTINGS  # noqa: E402


class TestRenderer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def setUp(self):
        self.m = FakeMpv()
        self.r = overlay.OverlayRenderer(self.m.cmd, SETTINGS)

    def tearDown(self):
        self.r.cleanup()

    def adds(self):
        return [c for c in self.m.cmds if c[0] == "overlay-add"]

    def test_bar_bytes_and_ack(self):
        safe = SETTINGS["safe"]
        top = overlay.bar_top(safe)
        m = {"title": "T", "buttons": [("pause", "Pause")], "pos_s": 5, "dur_s": 60}
        self.r.show(overlay.BAR, 0, top, 641, 480 - top, lambda s: overlay.draw_bar(s, m, safe), sig=1)
        a = self.adds()[0]
        w, h, stride = a[7], a[8], a[9]
        data = open(a[4], "rb").read()
        self.assertEqual(len(data), stride * h)
        self.assertEqual(stride, w * 4)
        self.assertTrue(all(max(data[i:i + 3]) <= data[i + 3] for i in range(0, len(data), 4 * 97)))
        # a second change before the reply waits; the reply sends the latest
        self.r.show(overlay.BAR, 0, top, 641, 480 - top, lambda s: None, sig=2)
        self.r.show(overlay.BAR, 0, top, 641, 480 - top, lambda s: None, sig=3)
        self.assertEqual(len(self.adds()), 1)
        self.m.events()                          # runs the ack
        self.assertEqual(len(self.adds()), 2)
        self.assertEqual(self.r.comps[overlay.BAR].sig[1], 3)   # (theme.version, sig)

    def test_same_sig_not_sent(self):
        f = lambda s: None  # noqa: E731
        self.r.show(overlay.TOAST, 40, 36, 200, 40, f, sig="x")
        self.m.events()
        self.r.show(overlay.TOAST, 40, 36, 200, 40, f, sig="x")
        self.assertEqual(len(self.adds()), 1)
        self.r.hide(overlay.TOAST)
        self.assertEqual(self.m.cmds[-1], ["overlay-remove", overlay.TOAST])

    def test_same_sig_while_pending_is_not_resent(self):
        # A late ack must not start a loop: one tick queues, the ack sends, the next re-queues.
        f = lambda s: None  # noqa: E731
        self.r.show(overlay.BAR, 0, 300, 641, 180, f, sig="p")
        for _ in range(6):
            self.r.show(overlay.BAR, 0, 300, 641, 180, f, sig="p")   # the tick, before the ack
            self.m.events()                                           # the late ack
        self.assertEqual(len(self.adds()), 1)

    def test_error_reply_sets_text_mode(self):
        self.m.replies["overlay-add"] = ("invalid parameter", None)
        self.r.show(overlay.TOAST, 40, 36, 200, 40, lambda s: None, sig="x")
        self.m.events()
        self.assertTrue(self.r.text_mode)

    def test_card(self):
        x, y, w, h = overlay.card_rect(SETTINGS["safe"])
        self.r.show(overlay.PANEL, x, y, w, h, lambda s: overlay.draw_card(s, "Movie"), sig="c")
        self.assertEqual(len(self.adds()), 1)


SAFES = [{"left": 16, "right": 16, "top": 16, "bottom": 16},
         {"left": 40, "right": 40, "top": 36, "bottom": 36},
         {"left": 72, "right": 72, "top": 56, "bottom": 56}]
BAR = {"title": "A Very Long Show Title • S12 E104 • An Even Longer Episode Title Here",
       "buttons": [("pause", "Pause"), ("prev", "Previous"), ("next", "Next Episode"),
                   ("subs", "Audio & Subtitles"), ("stop", "Stop")],
       "focus_row": "buttons", "focus_btn": 3, "pos_s": 4000, "dur_s": 7300,
       "markers": [(90, 180)], "chapters": [300, 610]}
LONG = "English • Forced • Signs and Songs (PGS) with a very long track title"


def components(safe):
    """(name, box, draw_fn(surf) -> Recorder) for every overlay at one safe area."""
    out = []
    top = overlay.bar_top(safe)
    box = (0, top, 641, 480 - top)
    out.append(("bar", box, lambda s: overlay.draw_bar(s, BAR, safe, oy=top)))
    out.append(("bar-seek", box, lambda s: overlay.draw_bar(s, dict(BAR, focus_row="seek"),
                                                            safe, oy=top)))
    mt = overlay.mini_top(safe)
    out.append(("mini", (0, mt, 641, 480 - mt),
                lambda s: overlay.draw_mini(s, dict(BAR, scrub_s=5000), safe, oy=mt)))
    rows = [(LONG, i == 2) for i in range(6)]
    pm = {"tab": "sub", "rows": rows, "focus": 5, "more_up": True, "more_down": True}
    b = overlay.panel_rect(len(rows), safe)
    out.append(("panel", b, lambda s, b=b: overlay.draw_panel(s, pm, ox=b[0], oy=b[1])))
    labels = ["Subtitles: " + LONG, "Audio: Japanese • 5.1", "Quality: Original",
              "Display Mode: Letterbox", "Sleep Timer: End of Episode", "Play from Start",
              "Next Episode", "Previous Episode", "Stop"]
    fit = overlay.menu_fit(safe)
    b = overlay.menu_rect(len(labels), safe)
    out.append(("menu", b, lambda s, b=b: overlay.draw_menu(s, labels, fit - 1, ox=b[0],
                                                            oy=b[1], top=0, fit=fit)))
    for lbl in ("Skip Intro", "Skip Credits"):
        for bar_open in (False, True):
            b = overlay.skip_rect(lbl, safe, bar_open)
            out.append(("skip", b, lambda s, b=b, lbl=lbl: overlay.draw_skip(s, lbl, ox=b[0],
                                                                             oy=b[1])))
    b = overlay.toast_rect(LONG + LONG, safe)
    out.append(("toast", b, lambda s, b=b: overlay.draw_toast(s, LONG + LONG, ox=b[0],
                                                              oy=b[1])))
    b = overlay.card_rect(safe)
    out.append(("card", b, lambda s, b=b: overlay.draw_card(s, LONG, ox=b[0], oy=b[1])))
    un = {"show": LONG, "line": "S12 E104", "title": LONG, "count": 12, "focus": "next"}
    b = overlay.upnext_card_rect(safe)
    out.append(("upnext", b, lambda s, b=b: overlay.draw_upnext(s, un, ox=b[0], oy=b[1])))
    st = dict(un, still=True, focus="stop")
    b = overlay.upnext_card_rect(safe, still=True)
    out.append(("still", b, lambda s, b=b: overlay.draw_upnext(s, st, ox=b[0], oy=b[1])))
    return out


class TestGeometry(unittest.TestCase):
    """Text inside its component and inside the safe area at every
    margin, no dim text, boxes on screen."""

    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_text_inside_component_and_safe_area(self):
        from batoplex.tokens import C
        for safe in SAFES:
            sr = pygame.Rect(safe["left"], safe["top"], 640 - safe["left"] - safe["right"],
                             480 - safe["top"] - safe["bottom"])
            for name, box, fn in components(safe):
                x, y, w, h = box
                with self.subTest(name=name, safe=safe["left"]):
                    self.assertEqual(y % 2, 0)
                    self.assertEqual(h % 2, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(y + h, 480)
                    surf = pygame.Surface((w, h))
                    rec = fn(surf)
                    self.assertTrue(rec.rects)
                    for r, comp in zip(rec.rects, rec.boxes):
                        self.assertTrue(comp.contains(r), "%s outside %s" % (r, comp))
                        self.assertTrue(sr.contains(r), "%s outside safe %s" % (r, sr))
                    self.assertNotIn(tuple(C["dim"]), rec.colors)

    def test_upnext_card_height_follows_its_content(self):
        hs = []
        for safe in SAFES:
            x, y, w, h = overlay.upnext_card_rect(safe)
            iw, th = overlay.upnext_thumb_size(safe)
            self.assertEqual(iw, w - 32)
            self.assertEqual(h, 40 + th + 12 + 92 + 24 + 16)      # 16 px under "Starts in"
            self.assertLessEqual(y + h, 480 - safe["bottom"])
            hs.append(h)
        self.assertGreater(hs[0], hs[2])       # a wider card has a taller still

    def test_recorder_uses_the_line_box_of_each_style(self):
        rec = Recorder()
        surf = pygame.Surface((200, 100))
        for style, line in (("heading", 28), ("label", 24), ("tag", 20), ("body", 26)):
            rec.text(surf, "Ag", style, (255, 255, 255), 0, 10)
            self.assertEqual(rec.rects[-1].h, line)

    def test_bar_scrub_and_unknown_duration(self):
        safe = SAFES[1]
        surf = pygame.Surface((641, 132))
        rec = overlay.draw_bar(surf, dict(BAR, focus_row="seek", pos_s=60, dur_s=600,
                                          scrub_s=300), safe)
        self.assertIn("5:00", rec.texts)                     # the scrub target, not the position
        self.assertIn("-5:00", rec.texts)
        rec = overlay.draw_bar(surf, dict(BAR, focus_row="seek", pos_s=75, dur_s=0,
                                          markers=[], chapters=[]), safe)
        self.assertIn("1:15", rec.texts)
        self.assertFalse(any(t.startswith("-") for t in rec.texts))   # no remaining time


if __name__ == "__main__":
    unittest.main()
