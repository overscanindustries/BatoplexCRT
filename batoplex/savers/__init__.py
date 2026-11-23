"""Shared parts of the screen savers.

Path B (filtered sprites): a saver on black draws sprites that were filtered one time, so the
frame needs no filter pass and the saver reports `prefiltered`. A sprite is opaque: its
picture on black with one black row above and below, filtered with the flicker mode (the
filter is vertical, so its spill fills those rows). Drawn at (x, y - 1) on black it equals
the frame filter of the picture at (x, y). No alpha: the frame filter makes every pixel
opaque, and on black none is needed. Two sprites that overlap: posters cover
(a plain blit), light sprites (a logo, text) take the brighter value (BLEND_RGB_MAX).
"""
import pygame

BLACK = (0, 0, 0)
PAD = 1


def prefilter(img, mode, flt_cache=None):
    """A filtered copy of img (opaque) with PAD black rows above and below. Blit it at
    (x, y - PAD)."""
    from ..draw import FlickerFilter, opaque_surface
    w, h = img.get_size()
    s = opaque_surface((w, h + 2 * PAD))
    s.fill(BLACK)
    s.blit(img, (0, PAD))
    if mode == "off":
        return s
    key = (w, h + 2 * PAD)
    cache = flt_cache if flt_cache is not None else {}
    f = cache.get(key)
    if f is None:
        f = cache[key] = FlickerFilter(key)
    return f.apply(s, mode).copy()


class Sprites:
    """Filtered sprites by key and flicker mode. A new mode drops them all. At most `cap`
    are kept (the oldest goes first)."""

    def __init__(self, cap=48):
        self.cap = cap
        self.mode = None
        self.d = {}
        self.filters = {}

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.d.clear()

    def get(self, key, build):
        """The sprite for key; build() gives the picture (None: not ready, nothing kept)."""
        s = self.d.get(key)
        if s is None:
            img = build()
            if img is None:
                return None
            s = prefilter(img, self.mode or "off", self.filters)
            if len(self.d) >= self.cap:
                self.d.pop(next(iter(self.d)))
            self.d[key] = s
        return s


def tint(img, rgb):
    """A white-on-black picture in the colour rgb (multiply)."""
    s = img.copy()
    s.fill(rgb, special_flags=pygame.BLEND_RGB_MULT)
    return s


STEP_PX = 2                   # a move every 2 fields, 2 px on each axis: 60 px/s at 60 fields/s
# Speed -> (px, slots): px on each axis, once each `slots` 2-field slots.
SPEEDS = {"slow": (2, 2), "normal": (2, 1), "fast": (4, 1)}


class Bounce:
    """A box that moves px on each axis once each `every` 2-field slots and turns back at the
    area edges. y stays even, so both fields of a 480i frame show the same position."""

    def __init__(self, area, size, rng, px=STEP_PX, every=1):
        self.area = pygame.Rect(area)
        self.w, self.h = size
        self.px, self.every = px, every
        self.x = rng.randint(self.area.x, max(self.area.x, self.area.right - self.w))
        self.y = rng.randint(self.area.y, max(self.area.y, self.area.bottom - self.h)) & ~1
        self.dx = rng.choice((-px, px))
        self.dy = rng.choice((-px, px))
        self.field = 0
        self.slot = None                       # the 2-field slot of the frame clock
        self.hit = False                       # True for the step that turned at a wall

    def resize(self, size):
        self.w, self.h = size
        self.x = max(self.area.x, min(self.x, self.area.right - self.w))
        self.y = max(self.area.y, min(self.y, self.area.bottom - self.h)) & ~1

    def step(self, ms=None):
        """Once per field. ms: the frame clock; the box moves when a new 2-field slot starts
        (without ms: every 2nd call), once each `every` slots. True when the box moved."""
        from .. import tokens
        self.hit = False
        if ms is None:
            self.field += 1
            if self.field % (2 * self.every):
                return False
        else:
            slot = int(ms / (2 * tokens.FIELD_MS) + 1e-6)
            if self.slot is None or slot == self.slot:
                self.slot = slot
                return False
            self.slot = slot
            if slot % self.every:
                return False
        a = self.area
        nx, ny = self.x + self.dx, self.y + self.dy
        if nx < a.x or nx + self.w > a.right:
            self.dx = -self.dx
            nx = max(a.x, min(self.x + self.dx, a.right - self.w))
            self.hit = True
        if ny < a.y or ny + self.h > a.bottom:
            self.dy = -self.dy
            ny = max(a.y, min(self.y + self.dy, a.bottom - self.h))
            self.hit = True
        self.x, self.y = nx, ny & ~1
        return True


class Canvas240:
    """Path A: the saver draws on a 320 x 240 picture; draw() doubles it to
    640 x 480, so each picture row fills one even and one odd line and both fields are equal.
    No filter: the saver is prefiltered. A step runs only when a new 2-field slot of the frame
    clock starts, at most 30 a second."""
    prefiltered = True
    W, H = 320, 240
    every = 1                       # steps once each `every` slots (2 on a weak box)

    def __init__(self, host, opts):
        self.host, self.opts, self.rng = host, opts, host.rng
        self.canvas = pygame.Surface((self.W, self.H))
        self.canvas.fill(BLACK)
        self.slot = None
        self.started = None

    def set_items(self, items):
        pass

    def first_box(self):
        return None

    def update(self, t, now):
        from .. import tokens
        slot = int(t / (2 * tokens.FIELD_MS) + 1e-6)   # 1e-6: float sums of FIELD_MS
        if self.slot is None:
            self.slot, self.started = slot, t
            self.step(0.0, t)
            self.render(self.canvas)
            return True
        if slot == self.slot or slot % self.every:
            return False
        dt = min(0.2, (slot - self.slot) * 2 * tokens.FIELD_MS / 1000.0)   # a stall: no leap
        self.slot = slot
        self.step(dt, t)
        self.render(self.canvas)
        return True

    def lighten(self):
        """A weak box: a step each 2nd slot (15 a second); each step costs the same."""
        self.every = 2

    def step(self, dt, t):
        """Advance the scene by dt seconds (t: the frame clock, ms)."""

    def render(self, c):
        """Draw the scene on the 320 x 240 canvas c."""

    def draw(self, s):
        if s.get_width() >= 640 and s.get_height() >= 480:
            pygame.transform.scale(self.canvas, (640, 480), s.subsurface((0, 0, 640, 480)))
            if s.get_width() > 640:
                s.fill(BLACK, (640, 0, s.get_width() - 640, s.get_height()))
        else:
            s.blit(pygame.transform.scale(self.canvas, s.get_size()), (0, 0))


class Orbit:
    """Burn-in guard for savers with fixed parts: an offset that moves 1 canvas px (2 screen
    lines) each 30 s around a loop of 8 px."""
    PATH = [(0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2), (4, 3), (4, 4), (3, 4),
            (2, 4), (1, 4), (0, 4), (0, 3), (0, 2), (0, 1)]

    def __init__(self, period_s=30.0):
        self.period = period_s

    def at(self, t_ms):
        return self.PATH[int(t_ms / 1000.0 / self.period) % len(self.PATH)]


def hsv(h, s, v):
    """An RGB tuple from hue (0-1), saturation and value; no channel above 235."""
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 235), int(g * 235), int(b * 235))


ART_DIR = None
_sheets = {}


def sheet(name, frames):
    """The frames of art/saver/<name>.png: a row of equal cells. [] when the file is missing
    (an older install): the saver draws what it can."""
    import os
    from .. import config
    k = (name, frames)
    if k not in _sheets:
        path = os.path.join(ART_DIR or os.path.join(config.APP_DIR, "art", "saver"), name + ".png")
        try:
            s = pygame.image.load(path)
            if pygame.display.get_surface() is not None:
                s = s.convert_alpha()
            w, h = s.get_size()
            cw = w // frames
            _sheets[k] = [s.subsurface((i * cw, 0, cw, h)).copy() for i in range(frames)]
        except (pygame.error, FileNotFoundError):
            _sheets[k] = []
    return _sheets[k]
