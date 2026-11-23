"""Fireplace: a brick fireplace at night with live pixel flames, two logs,
glowing embers and sparks. The flames are the classic "Doom fire" spread (each cell takes a
cooler copy of the cell below, pushed a little sideways by chance), run live on a 56 x 40 grid
and scaled 4 x; so the fire never loops. Drawn new; no art from any other fire saver.

Burn-in: the bricks, the mantel and the hearth are fixed. They stay dark (60 % at most), take
the fire's light so they flicker, and the whole scene moves on the Orbit loop.
"""
import math

import pygame

from . import BLACK, Canvas240, Orbit

W, H = 320, 240
FW, FH = 56, 40              # fire cells; each is 4 x 4 canvas px
SCALE = 4
OPEN = pygame.Rect(48, 60, FW * SCALE, FH * SCALE)        # the fire opening
LEVELS = 37
FIRE_STEP_S = 1 / 20.0       # the fire runs at 20 steps a second
SHIFT = (-1, 0, 0, 1)


def palette():
    """37 levels: black, deep red, orange, yellow, a pale yellow top. No channel above 235."""
    stops = [(0, (0, 0, 0)), (5, (40, 6, 2)), (11, (110, 20, 4)), (17, (170, 46, 6)),
             (23, (212, 92, 12)), (29, (232, 150, 30)), (33, (235, 200, 70)),
             (36, (235, 225, 150))]
    out = []
    for i in range(LEVELS):
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= i <= b:
                k = (i - a) / (b - a)
                out.append(tuple(int(x + (y - x) * k) for x, y in zip(ca, cb)))
                break
    return out


def bricks():
    """The fixed scene (bricks, mantel, hearth, the dark opening) at full scene brightness."""
    s = pygame.Surface((W + 8, H + 8))
    s.fill((14, 8, 6))
    for row in range(0, H + 8, 10):
        off = 0 if (row // 10) % 2 == 0 else 12
        for x in range(-24 + off, W + 8, 24):
            shade = 52 + ((x * 7 + row * 13) % 17)
            s.fill((shade + 18, shade // 2 + 6, shade // 3 + 4), (x + 1, row + 1, 22, 8))
    s.fill((46, 28, 16), (20, 40, W - 32, 12))               # mantel
    s.fill((30, 18, 10), (20, 52, W - 32, 2))
    s.fill((58, 54, 50), (36, 222, W - 64, 10))              # hearth stone
    s.fill((40, 38, 36), (36, 232, W - 64, 2))
    pygame.draw.rect(s, (8, 5, 4), OPEN.inflate(8, 8), border_radius=6)
    s.fill(BLACK, OPEN)
    return s


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        self.orbit = Orbit()
        self.pal = palette()
        self.cells = bytearray(FW * FH)
        self.surf = pygame.Surface((FW, FH), depth=8)
        self.surf.set_palette(self.pal)
        self.scene = bricks()
        self.lit = self.scene.copy()
        self.acc = 0.0
        self.sparks = []
        self.glow = 1.0
        self.t = 0.0
        self.rand = [self.rng.randrange(16) for _ in range(4093)]
        self.ri = 0
        self._feed()
        for _ in range(60):                                   # start with a lit fire
            self._spread()

    def _feed(self):
        """The bottom row: tongues of heat over the logs (they wander), cold at the sides."""
        base = (FH - 1) * FW
        r = self.rng
        k = self.t / 1000.0
        for x in range(FW):
            d = abs(x - FW / 2) / (FW / 2)
            tongue = 0.5 + 0.5 * math.sin(x * 0.55 + k * 2.3) * math.sin(x * 0.21 - k * 1.3)
            hot = (LEVELS - 1) * min(1.0, max(0.0, 1.2 - d * 1.5)) * (0.78 + 0.22 * tongue)
            self.cells[base + x] = max(0, int(hot) - r.randrange(5))

    def _spread(self):
        """Each cell takes the cell below, cooler by 0 to 2 levels (0.75 on average), moved
        one cell sideways now and then (no side wins)."""
        c, rnd, n = self.cells, self.rand, len(self.rand)
        i = self.ri
        for x in range(FW):
            for src in range(FW + x, FW * FH, FW):
                p = c[src]
                if p == 0:
                    c[src - FW] = 0
                    continue
                r = rnd[i]
                i = i + 1 if i + 1 < n else 0
                dst = src - FW + SHIFT[r & 3]
                if 0 <= dst < FW * FH:
                    q = p - (r >> 2 & 1) - (1 if r >> 2 == 3 else 0)
                    c[dst] = q if q > 0 else 0
        self.ri = (i + 7) % n

    def step(self, dt, t):
        self.t = t
        self.acc += dt
        while self.acc >= FIRE_STEP_S:
            self.acc -= FIRE_STEP_S
            self._feed()
            self._spread()
            if self.rng.random() < 0.12:
                self.sparks.append([OPEN.x + self.rng.uniform(60, 160), OPEN.bottom - 30,
                                    self.rng.uniform(-8, 8), self.rng.uniform(-55, -30), 1.0])
        for s in self.sparks:
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            s[4] -= dt * 0.7
        self.sparks = [s for s in self.sparks if s[4] > 0 and s[1] > OPEN.y]
        heat = sum(self.cells[(FH - 12) * FW:(FH - 11) * FW]) / (FW * (LEVELS - 1))
        self.glow = 0.78 + 0.22 * min(1.0, heat * 1.3)

    def render(self, c):
        ox, oy = self.orbit.at(self.t)
        g = int(150 * self.glow)                              # bricks at 60 % at most
        self.lit.blit(self.scene, (0, 0))
        self.lit.fill((g, int(g * 0.86), int(g * 0.8)), special_flags=pygame.BLEND_RGB_MULT)
        c.blit(self.lit, (ox - 4, oy - 4))
        self.surf.get_buffer().write(bytes(self.cells))
        fire = pygame.transform.scale(self.surf, OPEN.size)
        c.blit(fire, (OPEN.x + ox, OPEN.y + oy))
        self._logs(c, ox, oy)
        for s in self.sparks:
            v = int(235 * s[4])
            c.fill((v, int(v * 0.7), int(v * 0.25)), (int(s[0]) + ox, int(s[1]) + oy, 1, 1))

    def _logs(self, c, ox, oy):
        """Two round logs: bark shaded top to bottom, a pale cut end, cracks that glow with
        the fire."""
        x0, yb = OPEN.x + ox, OPEN.bottom + oy
        ember = min(1.0, max(0.0, (self.glow - 0.78) / 0.22))
        for lx, ly, lw in ((x0 + 34, yb - 24, 112), (x0 + 82, yb - 15, 116)):
            for k in range(12):                               # bark: light top, dark base
                v = 1.0 - abs(k - 3.5) / 9.0
                c.fill((int(70 * v), int(40 * v), int(22 * v)), (lx + 4, ly + k, lw - 8, 1))
            pygame.draw.ellipse(c, (96, 70, 44), (lx + lw - 10, ly, 10, 12))   # the cut end
            pygame.draw.ellipse(c, (70, 48, 28), (lx + lw - 8, ly + 3, 6, 6))
            for k in range(4):
                cx = lx + 14 + k * (lw - 30) // 4
                hot = (int(150 + 85 * ember), int(50 + 60 * ember), 10)
                c.fill(hot, (cx, ly + 10, 8, 1))              # glowing cracks underneath
                c.fill((30, 16, 8), (cx + 4, ly + 4, 6, 1))   # bark lines
        c.fill((34, 30, 28), (x0 + 20, yb - 4, OPEN.w - 40, 4))   # the grate
        for k in range(8):
            c.fill((44, 40, 38), (x0 + 28 + k * 22, yb - 8, 3, 4))
