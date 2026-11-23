"""Digital Rain screen saver: glyphs fall down columns of
cells on black. Options: Glyph Size (the cell grid), Color, Speed, Density and Glyph Set.
The defaults are the first design: half-width katakana and digits in 40 columns of 16 x 24
cells, green.

Interlace safety: no glyph moves. A cell only changes state (a glyph at a brightness, or
empty), and every cell sits on the 24 px grid. A column step is one full cell.

The atlas (60 glyphs x 5 levels) is filtered once with the flicker mode. Glyphs stay in rows
2-21 of a cell, so filtered cells do not touch and the frame needs no filter pass: the saver
reports prefiltered and the app skips the frame filter.
"""
import colorsys
import random

import pygame

from . import fonts

CELL_W, CELL_H = 16, 24
COLS, ROWS = 40, 20
GLYPH_PX = 20
GLYPH_TOP = 2                       # glyph rows stay in y 2..21 of the cell
CHARS = ("ｦ" + "".join(chr(c) for c in range(0xFF71, 0xFF9E)) + "0123456789:.=+")
# head, then body levels 1-4 (saturation 62 % at most, highest channel 235)
LEVELS = ((0xD2, 0xEB, 0xD2), (0x50, 0xC8, 0x64), (0x3C, 0x9C, 0x4C), (0x2A, 0x6C, 0x34),
          (0x1A, 0x44, 0x20))
LENGTH = (6, 16)                    # cells lit in a column, head to tail
STEP_S = (0.067, 0.083, 0.100, 0.133)
WAIT_S = (0.2, 2.5)                 # after the tail leaves the screen
TWINKLE = 2                         # lit cells that get a new glyph each field
BLACK = (0, 0, 0)

# Glyph Size -> (cell w, cell h, glyph px). Cell heights are multiples of 4 and fill 480 rows;
# the columns are centred in 641 px.
GRIDS = {"small": (12, 20, 16), "medium": (CELL_W, CELL_H, GLYPH_PX), "large": (24, 32, 28)}
SPEED = {"slow": 1.5, "normal": 1.0, "fast": 0.67}         # step times x
DENSITY = {"low": 2.0, "normal": 1.0, "high": 0.4}          # waits between runs x
# Glyph Set -> (font key, glyphs). ASCII is in Barlow Condensed Bold (shipped, OFL).
GLYPHS = {"kana": ("rain", CHARS), "binary": ("rain", "01"),
          "ascii": ("cond_bold", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$#%&*+=<>?/@")}
# Color -> hue (degrees) and body saturation; Green keeps the LEVELS table. Head, then levels
# 1-4 as (saturation x, value): saturation 62 % at most, highest channel 235.
HUES = {"amber": (36, 1.0), "white": (190, 0.12)}
SHAPE = ((0.18, 235), (0.97, 200), (1.0, 156), (0.98, 108), (0.85, 68))


def levels(color, accent=None):
    """The 5 colours (head, levels 1-4) of a Color value. accent: the theme accent RGB."""
    if color == "green" or (color == "accent" and accent is None):
        return LEVELS
    if color == "accent":
        h, s, _v = colorsys.rgb_to_hsv(*(c / 255.0 for c in accent))
        hue, sat = h * 360, min(1.0, s / 0.62)
    else:
        hue, sat = HUES[color]
    out = []
    for sx, v in SHAPE:
        r, g, b = colorsys.hsv_to_rgb(hue / 360.0, min(0.62, 0.62 * sat * sx / 0.97), v / 255.0)
        out.append((round(r * 255), round(g * 255), round(b * 255)))
    return tuple(out)


class Grid:
    """The cell grid of a Glyph Size."""

    def __init__(self, size="medium", width=641, height=480):
        self.cw, self.ch, self.px = GRIDS[size]
        self.cols, self.rows = width // self.cw, height // self.ch
        self.x0 = (width - 1 - self.cols * self.cw) // 2 if width % 2 else \
            (width - self.cols * self.cw) // 2


def level(d, length):
    """The brightness level of a cell d cells behind the head; None behind the tail."""
    if d < 0 or d >= length:
        return None
    if d == 0:
        return 0
    if d <= 2:
        return 1
    return 2 + min(2, (d - 3) * 3 // max(1, length - 3))


class Column:
    __slots__ = ("head", "length", "step_s", "next_t", "wait_until")

    def __init__(self):
        self.head = None            # the head row; None while the column waits
        self.length = LENGTH[0]
        self.step_s = STEP_S[0]
        self.next_t = 0.0
        self.wait_until = 0.0


def build_atlas(mode, flt=None, grid=None, colors=LEVELS, glyphs="kana"):
    """[level][glyph] -> cell surface, black, glyph centred 2 rows clear of the top and the
    bottom, filtered with the flicker mode. A glyph larger than its box is scaled down to fit
    (never cut)."""
    grid = grid or Grid()
    cw, chh = grid.cw, grid.ch
    key, chars = GLYPHS[glyphs]
    f = fonts.get(key, grid.px)
    if flt is None:
        from .draw import FlickerFilter
        flt = FlickerFilter((cw, chh))
    bw, bh = cw, chh - 2 * GLYPH_TOP
    atlas = []
    for col in colors:
        row = []
        for ch in chars:
            g = f.render(ch, True, col)               # per-pixel alpha: a tight box
            box = g.get_bounding_rect()
            g = g.subsurface(box)
            if box.w > bw or box.h > bh:
                k = min(bw / box.w, bh / box.h)
                g = pygame.transform.smoothscale(g, (max(1, int(box.w * k)),
                                                     max(1, int(box.h * k))))
            cell = pygame.Surface((cw, chh))
            cell.fill(BLACK)
            w, h = g.get_size()
            x = (cw - w) // 2
            y = GLYPH_TOP + (bh - h) // 2
            cell.blit(g, (x, y))
            if mode != "off":
                cell = flt.apply(cell, mode).copy()
            row.append(cell)
        atlas.append(row)
    return atlas


class Rain:
    def __init__(self, mode, rng=random, frame=(641, 480), size="medium", color="green",
                 speed="normal", density="normal", glyphs="kana", accent=None):
        self.rng = rng
        self.grid = g = Grid(size, *frame)
        self.ROWS, self.COLS = g.rows, g.cols
        self.colors = levels(color, accent if accent is not None else _accent())
        self.glyphs = glyphs
        self.nchars = len(GLYPHS[glyphs][1])
        self.step_s = tuple(s * SPEED[speed] for s in STEP_S)
        self.wait_s = tuple(w * DENSITY[density] for w in WAIT_S)
        self.surface = pygame.Surface(frame)
        self.surface.fill(BLACK)
        self.cells = [[None] * g.cols for _ in range(g.rows)]   # (glyph, level) or None
        self.lit = set()
        self.cols = [Column() for _ in range(g.cols)]
        self.mode = None
        self.atlas = None
        self.set_mode(mode)
        self.t0 = None

    def set_mode(self, mode):
        """A new flicker mode: filter the atlas again and redraw every lit cell."""
        if mode == self.mode:
            return
        self.mode = mode
        self.atlas = build_atlas(mode, grid=self.grid, colors=self.colors, glyphs=self.glyphs)
        for r, c in self.lit:
            self._blit(r, c)

    # ------------------------------------------------------------ cells
    def _blit(self, r, c):
        g = self.grid
        v = self.cells[r][c]
        x, y = g.x0 + c * g.cw, r * g.ch
        if v is None:
            self.surface.fill(BLACK, (x, y, g.cw, g.ch))
        else:
            self.surface.blit(self.atlas[v[1]][v[0]], (x, y))

    def _set(self, r, c, v):
        if self.cells[r][c] == v:
            return False
        self.cells[r][c] = v
        if v is None:
            self.lit.discard((r, c))
        else:
            self.lit.add((r, c))
        self._blit(r, c)
        return True

    def _glyph(self):
        return self.rng.randrange(self.nchars)

    # ------------------------------------------------------------ time
    def _start(self, col, now):
        col.head = -1
        col.length = self.rng.randint(*LENGTH)
        col.step_s = self.rng.choice(self.step_s)
        col.next_t = now

    def step_column(self, c, now):
        """One step of column c: the head goes down one cell with a new glyph, the cells
        behind it take their level, the cell behind the tail clears. True when a cell
        changed."""
        col = self.cols[c]
        col.head += 1
        changed = False
        h = col.head
        for r in range(max(0, h - col.length), min(self.ROWS, h + 1)):
            lv = level(h - r, col.length)
            if lv is None:
                changed |= self._set(r, c, None)
            elif r == h:
                changed |= self._set(r, c, (self._glyph(), 0))
            else:
                cur = self.cells[r][c]
                changed |= self._set(r, c, (cur[0] if cur else self._glyph(), lv))
        if h - col.length + 1 >= self.ROWS:         # the tail left the screen
            col.head = None
            col.wait_until = now + self.rng.uniform(*self.wait_s)
        return changed

    def step(self, now):
        """Once a field; now in seconds. True when the frame changed."""
        if self.t0 is None:
            self.t0 = now
            for col in self.cols:
                col.wait_until = now + self.rng.uniform(0.0, self.wait_s[1])
        changed = False
        for c, col in enumerate(self.cols):
            if col.head is None:
                if now < col.wait_until:
                    continue
                self._start(col, now)
            if now - col.next_t > 0.5:              # a long stall: no burst of steps
                col.next_t = now
            while col.head is not None and now >= col.next_t:
                col.next_t += col.step_s
                changed |= self.step_column(c, now)
        if self.lit:
            lit = list(self.lit)
            for r, c in self.rng.sample(lit, min(TWINKLE, len(lit))):
                g, lv = self.cells[r][c]
                changed |= self._set(r, c, (self._glyph(), lv))
        return changed


def _accent():
    from .tokens import C
    return C.get("accent")
