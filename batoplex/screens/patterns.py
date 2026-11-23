"""Test Patterns: a small 240p-test-suite style set for the tube.

Full raster (641x480), no safe area: the patterns show what the set does at its edges too.
The flicker filter is off here (Y turns it on to compare), so fine lines show the true
interlace. Left/Right: pattern. Up/Down: variant. A: show the name again. B: back.
"""
import time

import pygame

from .. import draw, inputs
from ..tokens import C
from . import Screen

W, H = 641, 480
NAME_S = 3.0

# SMPTE colour bars at 75 % (RGB). Black is 0: an RGB set has nothing below black, so the
# PLUGE strip uses black, +2 % and +4 %.
G75 = 191
BARS = [(G75, G75, G75), (G75, G75, 0), (0, G75, G75), (0, G75, 0), (G75, 0, G75), (G75, 0, 0),
        (0, 0, G75)]
CAST = [(0, 0, G75), (0, 0, 0), (G75, 0, G75), (0, 0, 0), (0, G75, G75), (0, 0, 0),
        (G75, G75, G75)]
WHITE, BLACK = (255, 255, 255), (0, 0, 0)


def _lv(p):
    v = int(round(255 * p / 100.0))
    return (v, v, v)


def smpte(s, _v):
    bw = W / 7.0
    top, mid = int(H * 0.67), int(H * 0.75)
    for i in range(7):
        x0, x1 = int(round(i * bw)), int(round((i + 1) * bw))
        s.fill(BARS[i], (x0, 0, x1 - x0, top))
        s.fill(CAST[i], (x0, top, x1 - x0, mid - top))
    # Bottom: -I, 100 % white, +Q, black, then the PLUGE (0, +2, +4 %), black.
    x = 0
    for col, n in (((0, 33, 76), 1.25), (WHITE, 1.25), ((50, 0, 106), 1.25), (BLACK, 1.25),
                   (BLACK, 1 / 3.0), (_lv(2), 1 / 3.0), (_lv(4), 1 / 3.0), (BLACK, 1)):
        w = int(round(n * bw))
        s.fill(col, (x, mid, max(0, min(w, W - x)), H - mid))
        x += w


def pluge(s, v):
    """Black field with bars at 0, 2, 4 and 8 %: set Brightness so the 2 % bar just shows and
    the black stays black. The variant adds a white patch for Contrast."""
    s.fill(BLACK)
    for i, p in enumerate((0, 2, 4, 8)):
        s.fill(_lv(p), (120 + i * 110, 120, 70, 240))
    if v % 2:
        s.fill(WHITE, (260, 380, 120, 60))


def ramp(s, v):
    """Gray: a smooth ramp on top, 11 steps (0-100 %) below. Variant: the ramp in red, green
    and blue, to see colour tracking."""
    s.fill(BLACK)
    chans = [(1, 1, 1)] if v % 2 == 0 else [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)]
    band = 240 // len(chans)
    for k, ch in enumerate(chans):
        for x in range(W):
            g = int(255 * x / (W - 1))
            s.fill((g * ch[0], g * ch[1], g * ch[2]), (x, k * band, 1, band))
    sw = W / 11.0
    for i in range(11):
        x0, x1 = int(round(i * sw)), int(round((i + 1) * sw))
        s.fill(_lv(i * 10), (x0, 260, x1 - x0, 220))


PURITY = [("White", WHITE), ("Red", (255, 0, 0)), ("Green", (0, 255, 0)), ("Blue", (0, 0, 255)),
          ("Black", BLACK)]


def purity(s, v):
    s.fill(PURITY[v % len(PURITY)][1])


def _grid(s, step, col=WHITE):
    cx, cy = W // 2, H // 2
    for x in range(cx % step, W, step):
        s.fill(col, (x, 0, 1 if x != cx else 2, H))
    for y in range(cy % step, H, step):
        s.fill(col, (0, y, W, 2))                      # 2 lines: one in each field


def grid240(s, v):
    """The 240p Test Suite grid at 480 lines (Artemio Urbina's grid-480): 40 x 30 cells of
    16 x 16 pixels over 640 x 480, each cell with a one-pixel outline (two pixels between
    cells) and a 2 x 2 dot at its centre. The outer ring of cells is red, the rest white:
    adjust size and centre until the red ring just shows at every edge. Variant: gray fill,
    to see the full active picture."""
    s.fill(_lv(50) if v % 2 else BLACK)
    red = (255, 0, 0)
    for cy in range(30):
        for cx in range(40):
            col = red if cx in (0, 39) or cy in (0, 29) else WHITE
            x, y = cx * 16, cy * 16
            s.fill(col, (x, y, 16, 1))
            s.fill(col, (x, y + 15, 16, 1))
            s.fill(col, (x, y, 1, 16))
            s.fill(col, (x + 15, y, 1, 16))
            s.fill(col, (x + 7, y + 7, 2, 2))


def crosshatch(s, v):
    s.fill(BLACK)
    _grid(s, (40, 32, 20)[v % 3])


def circles(s, v):
    """Linearity: a grid, a large centre circle and a circle in each corner. On a 4:3 set
    with the right geometry the circles are round."""
    s.fill(BLACK)
    _grid(s, 40, (90, 90, 90))
    cx, cy = W // 2, H // 2
    pygame.draw.circle(s, WHITE, (cx, cy), 220, 2)
    pygame.draw.circle(s, WHITE, (cx, cy), 110, 2)
    for x, y in ((80, 80), (W - 81, 80), (80, H - 81), (W - 81, H - 81)):
        pygame.draw.circle(s, WHITE, (x, y), 60, 2)
    if v % 2:
        s.fill(BLACK, (0, 0, W, 4))
        s.fill(BLACK, (0, H - 4, W, 4))


def convergence(s, v):
    """White dots, lines or crosses on black: the red, green and blue beams must meet."""
    s.fill(BLACK)
    step, kind = 40, v % 3
    for y in range(20, H, step):
        for x in range(20, W, step):
            if kind == 0:
                s.fill(WHITE, (x - 1, y - 1, 2, 2))
            elif kind == 2:
                s.fill(WHITE, (x - 6, y, 13, 2))
                s.fill(WHITE, (x, y - 6, 1, 13))
    if kind == 1:
        _grid(s, step)


def sharpness(s, v):
    """Top: bursts of vertical lines, coarse to fine (the finest is one pixel). Bottom: bursts
    of horizontal lines, 4, 2 and 1 lines high. Set Sharpness so the fine bursts are clean,
    with no ringing beside the edges."""
    s.fill(_lv(50) if v % 2 else BLACK)
    widths = (8, 6, 4, 3, 2, 1)
    bw = W // len(widths)
    for i, w in enumerate(widths):
        for x in range(i * bw + 6, (i + 1) * bw - 6, 2 * w):
            s.fill(WHITE, (x, 40, w, 180))
    hb = W // 3
    for i, h in enumerate((4, 2, 1)):
        for y in range(260, 440, 2 * h):
            s.fill(WHITE, (i * hb + 6, y, hb - 12, h))


def fine(s, v):
    """Interlace: one-pixel lines flicker at 30 Hz on a 480i set. Variants: horizontal lines,
    vertical lines, a checkerboard, two-pixel lines (they do not flicker)."""
    s.fill(BLACK)
    kind = v % 4
    if kind == 0:
        for y in range(0, H, 2):
            s.fill(WHITE, (0, y, W, 1))
    elif kind == 1:
        for x in range(0, W, 2):
            s.fill(WHITE, (x, 0, 1, H))
    elif kind == 2:
        for y in range(H):
            for x in range(y % 2, W, 2):
                s.set_at((x, y), WHITE)
    else:
        for y in range(0, H, 4):
            s.fill(WHITE, (0, y, W, 2))


def theme_colors(s, v):
    """The set test for the Stock colours. Variant 1: 128 x 64 blocks of the
    accent, its fallback and the Plex reference on the Stock background, with ink text: use
    the accent if the right edge of its block shows no smear and its text is sharp from the
    couch, else the fallback. Variant 2: the focus fill and its fallback as two 560 x 40 rows
    over a crosshatch: use the first if the lines beside it stay straight (no bloom, no change
    of picture size), else the fallback."""
    from .. import theme
    bg, ink = theme.rgb(theme.STOCK.palette["bg"]), theme.rgb(theme.STOCK.palette["ink"])
    if v % 2 == 0:
        s.fill(bg)
        cols = (theme.STOCK_ACCENT, theme.STOCK_ACCENT_FALLBACK, theme.PLEX_ACCENT)
        x = (W - (3 * 128 + 2 * 64)) // 2
        for h in cols:
            s.fill(theme.rgb(h), (x, 208, 128, 64))
            draw.text(s, h, "label", ink, x, 228, w=128, align="center")
            x += 128 + 64
    else:
        s.fill(BLACK)
        _grid(s, 40)
        for y, h in ((160, theme.STOCK_FOCUS), (280, theme.STOCK_FOCUS_FALLBACK)):
            s.fill(theme.rgb(h), (40, y, 560, 40))
            draw.text(s, h, "label", ink, 56, y + 8)


# (name, draw, [variant names])
PATTERNS = [
    ("SMPTE Color Bars", smpte, [""]),
    ("PLUGE", pluge, ["Black level", "Black level and white"]),
    ("Gray Ramp", ramp, ["Gray", "Red, green and blue"]),
    ("Color Purity", purity, [n for n, _c in PURITY]),
    ("Grid", grid240, ["240p Test Suite grid", "Gray fill"]),
    ("Crosshatch", crosshatch, ["Large", "Medium", "Small"]),
    ("Linearity", circles, ["Circles", "Circles, no edge lines"]),
    ("Convergence", convergence, ["Dots", "Lines", "Crosses"]),
    ("Sharpness", sharpness, ["On black", "On gray"]),
    ("Interlace", fine, ["Horizontal lines", "Vertical lines", "Checkerboard", "Two-pixel lines"]),
    ("Theme Colors", theme_colors, ["Accent", "Focus"]),
]


class PatternsScreen(Screen):
    name = "TEST PATTERNS"
    no_saver = True
    menu_title = "TEST PATTERNS"
    menu_app_rows = False          # a tool menu, no Settings row

    def __init__(self, app):
        super().__init__(app)
        self.i = 0
        self.v = 0
        self.filter_on = False
        self.name_until = 0.0
        self._sig = None
        self._cache = {}                 # (pattern, variant) -> Surface: the checkerboard is slow
        self._show_name()

    @property
    def raw_output(self):
        """The app skips the flicker filter while this is True."""
        return not self.filter_on

    def _show_name(self):
        self.name_until = time.monotonic() + NAME_S

    def toggle_filter(self):
        self.filter_on = not self.filter_on
        self._show_name()

    def menu_items(self):
        """The Y and A actions for a remote. The label shows the state."""
        return [("Flicker Filter: %s" % ("On" if self.filter_on else "Off"), self.toggle_filter),
                ("Show Name", self._show_name)]

    def handle(self, a):
        n = len(PATTERNS[self.i][2])
        if a in (inputs.LEFT, inputs.RIGHT):
            self.i = (self.i + (1 if a == inputs.RIGHT else -1)) % len(PATTERNS)
            self.v = 0
            self._show_name()
        elif a in (inputs.UP, inputs.DOWN):
            self.v = (self.v + (1 if a == inputs.DOWN else -1)) % n
            self._show_name()
        elif a == inputs.OK:
            self._show_name()
        elif a == inputs.Y:
            self.toggle_filter()
        elif a == inputs.BACK:
            self.app.pop()
        elif a == inputs.QUIT:
            self.app.quit()

    def update(self):
        sig = (self.i, self.v, self.filter_on, time.monotonic() < self.name_until)
        if sig != self._sig:
            self._sig = sig
            return True
        return False

    def draw(self, s):
        name, fn, variants = PATTERNS[self.i]
        key = (self.i, self.v % len(variants))
        surf = self._cache.get(key)
        if surf is None:
            surf = pygame.Surface((W, H))
            fn(surf, self.v)
            if len(self._cache) >= 4:              # about 1.2 MB each
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = surf
        s.blit(surf, (0, 0))
        if time.monotonic() < self.name_until:
            sf = self.safe
            var = variants[self.v % len(variants)]
            line1 = "%s   %d/%d" % (name, self.i + 1, len(PATTERNS))
            ff = self.app.settings["flicker_filter"] if self.filter_on else "off"
            line2 = " • ".join(t for t in (var, "Flicker Filter: %s" % ff.capitalize()) if t)
            x, y, w = sf["left"] + 16, 480 - sf["bottom"] - 16 - 96, 640 - sf["left"] - sf["right"] - 32
            draw.rect(s, C["raised"], (x, y, w, 96))
            draw.text(s, line1, "label", C["text"], x + 16, y + 10, w=w - 32)
            draw.text(s, line2, "small", C["text2"], x + 16, y + 40, w=w - 32)
            if not getattr(self, "hide_hints", False):   # not under its menu
                draw.hints(s, [("dpad", "Pattern"), ("A", "Name"), ("menu", "Options"),
                               ("B", "Back")], x + 16, y + 64, w - 32)
