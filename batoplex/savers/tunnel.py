"""Tunnel: a flight down a wire tunnel. Rings of 12 sides come at the viewer;
the far end swings on a slow curve, so the tunnel bends. The rings are joined by lines along
the walls, the colour runs down the tunnel, and far rings fade into the dark. Drawn new on
the 240p canvas.
"""
import math

import pygame

from . import BLACK, Canvas240, hsv

RINGS = 18
SIDES = 12
NEAR, FAR = 0.35, 6.0          # ring depths
SPEED = 1.6                    # depth units a second
RADIUS = 1.0
FOCAL = 150.0


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        gap = (FAR - NEAR) / RINGS
        self.z = [NEAR + gap * i for i in range(RINGS)]
        self.time = self.rng.uniform(0, 100)
        self.spin = 0.0

    def _centre(self, z):
        """The bend: the tunnel centre at depth z, in world units."""
        a = self.time - z * 0.35
        return (0.9 * math.sin(a * 0.45) + 0.3 * math.sin(a * 1.1),
                0.6 * math.cos(a * 0.38))

    def step(self, dt, t):
        self.time += dt
        self.spin += dt * 0.25
        gap = (FAR - NEAR) / RINGS
        self.z = [z - SPEED * dt for z in self.z]
        while self.z and self.z[0] < NEAR:
            self.z.pop(0)
            self.z.append(self.z[-1] + gap)

    def _ring(self, z):
        cx0, cy0 = self._centre(0.0)
        cx, cy = self._centre(z)
        k = FOCAL / z
        pts = []
        for i in range(SIDES):
            a = self.spin + i * 2 * math.pi / SIDES
            x = cx - cx0 + RADIUS * math.cos(a)
            y = cy - cy0 + RADIUS * math.sin(a)
            pts.append((self.W / 2 + x * k, self.H / 2 + y * k))
        return pts

    def render(self, c):
        c.fill(BLACK)
        rings = [(z, self._ring(z)) for z in self.z]
        for n in range(len(rings) - 1, -1, -1):            # far first, near on top
            z, pts = rings[n]
            fade = max(0.0, min(1.0, (FAR - z) / (FAR - NEAR)))
            col = hsv(self.time * 0.05 + z * 0.06, 0.65, 0.15 + 0.85 * fade ** 1.6)
            pygame.draw.aalines(c, col, True, pts)
            if n + 1 < len(rings):
                nxt = rings[n + 1][1]
                for i in range(0, SIDES, 2):              # lines along the walls
                    pygame.draw.aaline(c, col, pts[i], nxt[i])
