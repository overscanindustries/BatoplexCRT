import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
sys.path.insert(0, os.path.dirname(__file__))

import pygame  # noqa: E402

from batoplex import draw, theme, tokens  # noqa: E402
from batoplex.overlay import Recorder  # noqa: E402
from batoplex.tokens import C  # noqa: E402

SAFES = [{"left": 16, "right": 16, "top": 16, "bottom": 16},
         {"left": 40, "right": 40, "top": 36, "bottom": 36},
         {"left": 72, "right": 72, "top": 56, "bottom": 56}]


def inside(r, box):
    return r.left >= box[0] and r.right <= box[0] + box[2] and r.top >= box[1] and \
        r.bottom <= box[1] + box[3]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        theme.apply(None)

    def setUp(self):
        self.s = pygame.Surface((641, 480))
        self.s.fill((1, 2, 3))


class TestControls(Base):
    def test_button_width_and_text_inside(self):
        self.assertEqual(draw.button_w("OK"), tokens.BTN_MIN_W)
        long = "Play from the Start"
        self.assertEqual(draw.button_w(long), draw.width(long, "label") + 32)
        rec = Recorder()
        r = (100, 200, draw.button_w(long), tokens.CTRL_H)
        draw.button(self.s, r, long, kind="primary", rec=rec)
        self.assertEqual(len(rec.rects), 1)
        self.assertTrue(inside(rec.rects[0], r))
        self.assertEqual(self.s.get_at((101, 201))[:3], C["accent"])

    def test_button_fills_by_kind_and_focus(self):
        draw.button(self.s, (0, 0, 96, 40), "A", surface="raised")
        self.assertEqual(self.s.get_at((1, 1))[:3], C["track"])
        draw.button(self.s, (0, 40, 96, 40), "A")
        self.assertEqual(self.s.get_at((1, 41))[:3], C["raised"])
        draw.button(self.s, (0, 80, 96, 40), "A", kind="primary", focused=True)
        self.assertEqual(self.s.get_at((1, 81))[:3], C["focus"])
        draw.button(self.s, (0, 120, 96, 40), "A", focused=True, enabled=False)
        self.assertEqual(self.s.get_at((1, 121))[:3], C["raised"])   # disabled takes no focus

    def test_toggle_bar_keeps_its_place_when_focused(self):
        draw.button(self.s, (0, 0, 48, 40), on=True)
        self.assertEqual(self.s.get_at((10, 37))[:3], C["accent"])
        draw.button(self.s, (0, 0, 48, 40), on=True, focused=True)
        self.assertEqual(self.s.get_at((10, 37))[:3], C["ink"])

    def test_row_text_and_value_inside_the_fill(self):
        rec = Recorder()
        r = (40, 100, 548)
        draw.row(self.s, r, "Auto Play Countdown Time", "During Final Credits", focused=True,
                 rec=rec)
        fill = (40, 100, 548, tokens.ROW_H)
        self.assertEqual(len(rec.rects), 2)
        for t in rec.rects:
            self.assertTrue(inside(t, fill), t)
        self.assertLessEqual(rec.rects[0].right, rec.rects[1].left)   # no overlap
        self.assertEqual(self.s.get_at((41, 101))[:3], C["focus"])
        self.assertEqual(self.s.get_at((41, 100 + tokens.ROW_H))[:3], (1, 2, 3))

    def test_current_row_has_a_bar_and_no_fill(self):
        draw.row(self.s, (40, 100, 240), "Home", current=True)
        self.assertEqual(self.s.get_at((41, 120))[:3], C["accent"])
        self.assertEqual(self.s.get_at((60, 101))[:3], (1, 2, 3))

    def test_compact_and_two_line_rows_inside(self):
        rec = Recorder()
        draw.row_compact(self.s, (40, 100, 400), 12, "A Long Track Title For Testing", "Artist",
                         "3:21", rec=rec)
        for t in rec.rects:
            self.assertTrue(t.left >= 40 and t.right <= 440, t)
        rec = Recorder()
        draw.row_two(self.s, (40, 200, 240), "CRT Movies", "Home Server", rec=rec)
        for t in rec.rects:
            self.assertTrue(inside(t, (40, 200, 240, tokens.ROW_TWO_H)), t)

    def test_tabs_scroll_so_the_selected_tab_shows(self):
        labels = ["Recommended", "Library", "Collections", "Categories", "Playlists"]
        rec = Recorder()
        off = draw.tabs(self.s, labels, 4, True, 40, 300, 80, 0, rec=rec)
        self.assertGreater(off, 0)
        ws = draw.tab_widths(labels)
        x4 = 40 + sum(w + 8 for w in ws[:4]) - off
        self.assertLessEqual(x4 + ws[4], 300)             # the last tab: no right cue
        self.assertGreaterEqual(x4, 40 + tokens.CUE)      # clear of the left cue
        off = draw.tabs(self.s, labels, 1, False, 40, 300, 80, 0)
        x1 = 40 + ws[0] + 8 - off
        self.assertLessEqual(x1 + ws[1], 300 - tokens.CUE)  # clear of the right cue
        self.assertEqual(draw.tabs(self.s, labels, 0, False, 40, 600, 80, off), 0)

    def test_chip_width(self):
        self.assertEqual(draw.chip(self.s, 40, 40, "Title"), draw.width("Title", "list") + 32)
        self.assertEqual(draw.chip(self.s, 40, 40, "Title", arrow="up"),
                         draw.width("Title", "list") + 48)


class TestPanels(Base):
    def test_dialog_inside_the_safe_area(self):
        for sf in SAFES:
            for n in (1, 3):
                x, y, w, h = draw.dialog_geo(sf, n)
                self.assertTrue(inside(pygame.Rect(x, y, w, h), (sf["left"], sf["top"],
                                640 - sf["left"] - sf["right"], 480 - sf["top"] - sf["bottom"])))
                self.assertEqual(y % 4, 0)
                self.assertEqual(h % 4, 0)

    def test_dialog_buttons_left_aligned_with_the_title(self):
        sf = SAFES[1]
        rec = Recorder()
        rects = draw.dialog_box(self.s, sf, ["Exit Batoplex?"], ("Exit", "Cancel"), 0, rec=rec)
        x, y, w, h = draw.dialog_geo(sf, 1)
        self.assertEqual(rects[0][0], x + tokens.DIALOG_PAD)
        self.assertEqual(rec.rects[0].left, x + tokens.DIALOG_PAD)
        for t in rec.rects:
            self.assertTrue(inside(t, (x, y, w, h)), t)
        self.assertLessEqual(rects[-1][1] + rects[-1][3] + tokens.DIALOG_PAD, y + h)

    def test_menu_inside_the_safe_area_and_rows_in_the_panel(self):
        for sf in SAFES:
            fit = draw.menu_fit(sf)
            r = draw.menu_rect(sf, fit)
            box = (sf["left"], sf["top"], 640 - sf["left"] - sf["right"],
                   480 - sf["top"] - sf["bottom"])
            self.assertTrue(inside(pygame.Rect(r), box), sf)
            rec = Recorder()
            labels = ["Row %d" % i for i in range(10)]
            draw.menu_panel(self.s, r, "Menu", labels, 2, top=1, fit=fit, rec=rec)
            for t in rec.rects:
                self.assertTrue(inside(t, r), t)

    def test_toast_leaves_room_for_the_clock(self):
        sf = SAFES[2]
        msg = "A very long message " * 6
        x, y, w, h = draw.toast_rect(msg, sf, 50)
        self.assertEqual((x, y, h), (72, 56, tokens.CTRL_H))
        self.assertEqual(x + w, 640 - 72 - 50 - tokens.TOAST_GAP)
        self.assertEqual(draw.toast_rect("Hi", sf, 50)[2], draw.width("Hi", "label") + 32)

    def test_list_shows_no_cut_row(self):
        rows = [("head", "", "Group", "")] + [("choice", i, "Row %d" % i, "Value")
                                              for i in range(12)]
        s = self.s
        top = draw.list_rows(s, rows, 11, 40, 80, 396, 560, 0)
        _pos, top2, total = draw.list_layout(rows, 11, 316, 0)
        self.assertEqual(top, top2)
        self.assertGreater(total, 316)
        whole, cut = [], []
        for r, ry, h in _pos:
            yy = 80 + ry - top
            fill = h if r[0] == "head" else tokens.ROW_H
            if 80 <= yy and yy + fill <= 396:
                whole.append(yy)
            elif yy < 396 and yy + fill > 80:
                cut.append(yy)
        self.assertEqual(s.get_at((60, whole[-1] + 2))[:3], C["focus"])   # focused, whole
        self.assertTrue(cut)
        for yy in cut:                           # a cut row draws nothing in the list area
            for y in range(max(80, yy), min(396, yy + 40)):
                for x in range(40, 560, 7):
                    self.assertEqual(s.get_at((x, y))[:3], (1, 2, 3), (x, y))


class TestText(Base):
    def test_seekbar_unknown_duration_and_knob_inside(self):
        rec = Recorder()
        draw.seekbar(self.s, 40, 100, 560, 30, 0, rec=rec)
        self.assertEqual(len(rec.rects), 1)                # no remaining time
        self.assertEqual(self.s.get_at((41, 101))[:3], C["track"])
        self.s.fill((1, 2, 3))
        draw.seekbar(self.s, 40, 100, 560, 100, 100, focused=True)
        self.assertEqual(self.s.get_at((599, 100))[:3], C["focus"])
        self.assertEqual(self.s.get_at((600, 100))[:3], (1, 2, 3))   # knob stays on the track
        rec = Recorder()
        draw.seekbar(self.s, 40, 100, 560, 10, 100, scrub_s=50, rec=rec)
        self.assertEqual(len(rec.rects), 2)

    def test_hints_record_text(self):
        rec = Recorder()
        draw.hints(self.s, [("A", "Place"), ("B", "Cancel")], 40, 420, 560, rec=rec)
        self.assertEqual(len(rec.rects), 2)

    def test_cached_rebuilds_after_a_theme_change(self):
        n = []
        build = lambda: n.append(1) or pygame.Surface((2, 2))   # noqa: E731
        a = draw.cached("t", build)
        self.assertIs(draw.cached("t", build), a)
        theme.apply(None)
        self.assertIsNot(draw.cached("t", build), a)
        self.assertEqual(len(n), 2)

class TestLanding2(Base):
    def item(self, **kw):
        from batoplex.model import Item
        return Item("e1", "episode", "Ep", **kw)

    def test_row_marks(self):
        draw.row_marks(self.s, self.item(), 300, 0, 40)            # unwatched: the square
        self.assertEqual(self.s.get_at((300, 16))[:3], C["accent"])
        it = self.item(duration_ms=1000, view_offset_ms=500)
        draw.row_marks(self.s, it, 300, 100, 40)                   # in progress: the bar
        self.assertEqual(self.s.get_at((40, 132))[:3], C["accent"])
        self.assertNotEqual(self.s.get_at((300, 116))[:3], C["accent"])
        self.s.fill((1, 2, 3))
        draw.row_marks(self.s, self.item(watched=True), 300, 200, 40)
        self.assertEqual(self.s.get_at((300, 216))[:3], (1, 2, 3))   # watched: no mark

    def test_move_row_is_a_focus_fill_with_ink_arrows(self):
        draw.row_compact(self.s, (40, 100, 400), 3, "Song", moving=True)
        self.assertEqual(self.s.get_at((41, 101))[:3], C["focus"])
        self.assertEqual(self.s.get_at((40 + 16 + 20 - 5, 100 + 12))[:3], C["ink"])

    def test_peek_music_cell_shows_art_only(self):
        from batoplex.model import Item
        rec = []
        real = draw.text
        draw.text = lambda *a, **k: rec.append(a[1]) or 0
        try:
            it = Item("a", "album", "Album", artist="Band")
            draw.music_cell(self.s, it, pygame.Rect(0, 0, 112, 168), None, False, "Band")
            n = len(rec)
            draw.music_cell(self.s, it, pygame.Rect(0, 200, 112, 112), None, False, "Band")
            self.assertEqual(rec.count("Band"), 1)
            self.assertEqual(rec[n:].count("Album"), 1)        # the stand-in title only
        finally:
            draw.text = real

    def test_detail_tint(self):
        import colorsys
        from batoplex.screens import detail
        img = pygame.Surface((64, 36))
        img.fill((200, 40, 40))
        t = detail.tint_of(img)
        h, s_, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in t))
        self.assertAlmostEqual(v, 0.14, delta=0.01)
        self.assertLessEqual(s_, 0.46)
        self.assertLess(abs(h - 0.0), 0.02)
        self.assertEqual(detail.lift((250, 0, 10), 16), (255, 16, 26))

    def test_loading_shows_after_500_ms(self):
        from batoplex.screens import Screen
        import batoplex.screens as scr_mod
        sc = Screen(None)
        t = [100.0]
        real = scr_mod.time.monotonic
        scr_mod.time.monotonic = lambda: t[0]
        try:
            self.assertFalse(sc.late(True))
            t[0] += 0.4
            self.assertFalse(sc.late(True))
            t[0] += 0.2
            self.assertTrue(sc.late(True))
            self.assertFalse(sc.late(False))
            self.assertFalse(sc.late(True))                    # a new load starts again
            self.assertFalse(sc.late_due())
            t[0] += 0.6
            self.assertTrue(sc.late_due())                     # one redraw for "Loading…"
            self.assertFalse(sc.late_due())
        finally:
            scr_mod.time.monotonic = real


class TestTextCache(Base):
    def test_bounded_by_bytes_oldest_first(self):
        real = draw.TEXT_CACHE_BYTES
        draw.TEXT_CACHE_BYTES = 200000
        try:
            for i in range(200):
                draw.text(self.s, "A long line of cached text %d" % i, "label", C["text"], 0, 0)
            sizes = sum(draw._nbytes(v) for v in draw._texts.values())
            self.assertEqual(sizes, draw._texts_bytes[0])
            self.assertLessEqual(sizes, 200000)
            self.assertLess(len(draw._texts), 200)
            self.assertEqual(next(reversed(draw._texts))[2], "A long line of cached text 199")
        finally:
            draw.TEXT_CACHE_BYTES = real


if __name__ == "__main__":
    unittest.main()
