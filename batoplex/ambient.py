"""The Ribbon ambient: two soft horizontal bands in the theme's `raised`
colour move left behind the content of a solid-background screen, only while nobody presses
anything.

Starts after 8 s with no input on a screen with `ambient = True`, fades in over 600 ms, then
moves 2 px every 2 fields (on the frame clock). The first press removes it in that field (a
cut). Off under the screen saver, in playback, with High Contrast and in scripted runs (unless
BATOPLEX_AMBIENT=1). It changes only rows Y..Y+H, so a frame costs a band pass.
"""
import math

import pygame

from . import draw, theme, tokens
from .tokens import C

IDLE_S = 8.0
FADE_MS = 600.0
W = tokens.MODE_W                  # one period of the wave
Y, H = 228, 144                    # the strip on screen: centre line y = 300
STEP_PX = 2                        # px each 2 fields
BAND = (0, Y, tokens.MODE_W, H)
# (peak alpha, centre phase, thickness phase) of the two bands
BANDS = ((1.0, 0.0, 1.3), (0.5, 2.2, 3.6))


def coverage(x, y):
    """Alpha 0..1 of the ribbon at strip column x, strip row y: two bands over each other,
    each a raised cosine across its thickness (no hard edge)."""
    keep = 1.0
    for peak, pc, pt in BANDS:
        c = H / 2 + 28 * math.sin(2 * math.pi * x / W + pc)
        t = 56 + 16 * math.sin(4 * math.pi * x / W + pt)
        d = abs(y + 0.5 - c) / (t / 2)
        if d < 1.0:
            keep *= 1.0 - peak * 0.5 * (1.0 + math.cos(math.pi * d))
    return 1.0 - keep


def build_strip():
    """2 periods (1282 x 144) in bg and raised, filled as vertical runs of one colour."""
    bg, hi = C["bg"], C["raised"]
    s = pygame.Surface((2 * W, H))
    s.fill(bg)
    for x in range(W):
        y0, run = 0, None
        for y in range(H + 1):
            if y < H:
                a = coverage(x, y)
                col = tuple(int(round(bg[i] + (hi[i] - bg[i]) * a)) for i in range(3))
            else:
                col = None
            if col != run:
                if run is not None and run != bg:
                    s.fill(run, (x, y0, 1, y - y0))
                y0, run = y, col
    s.blit(s, (W, 0), (0, 0, W, H))
    return s


def enabled():
    """The theme has a moving background, and High Contrast is off."""
    return theme.cur.ambient == "ribbon" and not theme.high_contrast


class Ambient:
    def __init__(self):
        self.on = False
        self.t0 = 0.0              # frame-clock ms of the start
        self.shown = None          # (offset, alpha) of the last frame drawn

    def update(self, allowed, idle_s, now_ms):
        """Once a field. allowed: the screen and the app state permit it; idle_s: seconds
        since the last input. True when the ribbon changed (a band pass)."""
        if not allowed:
            return self.stop()
        if not self.on:
            if idle_s < IDLE_S:
                return False
            self.on, self.t0, self.shown = True, now_ms - tokens.FIELD_MS, None
        f = self.frame(now_ms)
        if f == self.shown:
            return False
        self.shown = f
        return True

    def stop(self):
        """A cut. True when it was showing."""
        was, self.on, self.shown = self.on, False, None
        return was

    def frame(self, now_ms):
        """(offset px, alpha 0-255) at now_ms."""
        dt = max(0.0, now_ms - self.t0)
        off = int(dt // (2 * tokens.FIELD_MS)) * STEP_PX % W
        alpha = min(255, int(255 * dt / FADE_MS))
        return off, alpha

    def draw(self, s):
        if not self.on or self.shown is None:
            return
        off, alpha = self.shown
        strip = draw.cached(("ribbon",), build_strip)
        strip.set_alpha(alpha if alpha < 255 else None)
        s.blit(strip, (0, Y), (off, 0, W, H))
        strip.set_alpha(None)
