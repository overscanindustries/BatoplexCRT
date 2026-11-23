"""Polygons: two four-point shapes whose corners bounce off the edges, each
with a trail of older copies, the colour moving slowly round the hue circle. The 1990s
desktop look, drawn new: thin lines on the 240p canvas are 2 screen lines high.
"""
import pygame

from . import BLACK, Canvas240, hsv

TRAIL = 6            # copies behind the shape
GAP = 3              # steps between two copies
SPEED = (40.0, 75.0)  # canvas px a second, each corner axis


class Shape:
    def __init__(self, rng, w, h, hue):
        self.pts = [[rng.uniform(0, w - 1), rng.uniform(0, h - 1)] for _ in range(4)]
        self.vel = [[rng.choice((-1, 1)) * rng.uniform(*SPEED),
                     rng.choice((-1, 1)) * rng.uniform(*SPEED)] for _ in range(4)]
        self.hue = hue
        self.trail = []

    def step(self, dt, w, h):
        for p, v in zip(self.pts, self.vel):
            for k, lim in ((0, w - 1), (1, h - 1)):
                p[k] += v[k] * dt
                if p[k] < 0:
                    p[k], v[k] = -p[k], abs(v[k])
                elif p[k] > lim:
                    p[k], v[k] = 2 * lim - p[k], -abs(v[k])


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        h0 = self.rng.random()
        self.shapes = [Shape(self.rng, self.W, self.H, h0),
                       Shape(self.rng, self.W, self.H, h0 + 0.5)]
        self.n = 0

    def step(self, dt, t):
        self.n += 1
        for sh in self.shapes:
            sh.step(dt, self.W, self.H)
            sh.hue += dt / 40.0                       # once round the circle in 40 s
            if self.n % GAP == 0:
                sh.trail.insert(0, [tuple(p) for p in sh.pts])
                del sh.trail[TRAIL:]

    def render(self, c):
        c.fill(BLACK)
        for sh in self.shapes:
            for i in range(len(sh.trail) - 1, -1, -1):  # oldest first, dimmest
                v = 0.30 + 0.45 * (1 - (i + 1) / (TRAIL + 1))
                pygame.draw.aalines(c, hsv(sh.hue, 0.75, v), True, sh.trail[i])
            pygame.draw.aalines(c, hsv(sh.hue, 0.7, 1.0), True, [tuple(p) for p in sh.pts])
