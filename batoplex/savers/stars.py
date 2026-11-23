"""Starfield: stars fly out from the centre toward the viewer, faint and small
far away, bright and 2 x 2 near. The 1990s desktop look, drawn new on the 240p canvas.
"""
from . import BLACK, Canvas240

N = 220
SPEED = 0.6          # depth units a second (depth runs 1 -> 0.05)
NEAR = 0.05
FOCAL = 0.5


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        self.stars = [self._new(self.rng.uniform(NEAR, 1.0)) for _ in range(N)]

    def _new(self, z=1.0):
        r = self.rng
        return [r.uniform(-1.0, 1.0), r.uniform(-0.75, 0.75), z]

    def step(self, dt, t):
        for i, s in enumerate(self.stars):
            s[2] -= SPEED * dt * (0.6 + 0.4 * s[2])     # a little faster near: depth
            if s[2] <= NEAR or not self._on(s):
                self.stars[i] = self._new()

    def _xy(self, s):
        k = FOCAL / s[2]
        return (self.W / 2 + s[0] * k * self.W / 2, self.H / 2 + s[1] * k * self.W / 2)

    def _on(self, s):
        x, y = self._xy(s)
        return 0 <= x < self.W and 0 <= y < self.H

    def render(self, c):
        c.fill(BLACK)
        for s in self.stars:
            x, y = self._xy(s)
            near = 1.0 - s[2]
            v = int(50 + 185 * near ** 1.2)
            col = (v, v, min(235, v + 8))
            if near > 0.7:
                c.fill(col, (int(x), int(y), 2, 2))
            else:
                c.set_at((int(x), int(y)), col)
