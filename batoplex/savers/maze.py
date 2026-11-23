"""3D Maze: a walk through a brick maze, first person, in the look of the
early-1990s desktop maze. A raycaster on the 240p canvas: 160 rays, each a 2 px column of a
brick texture made in code, shaded with distance; a plain ceiling and floor. The walker keeps
its right hand on the wall and turns smoothly; at the exit a new maze is made.
"""
import math

import pygame

from . import Canvas240

SIZE = 9                       # cells a side; the grid is 2 * SIZE + 1
RAYS = 160
FOV = math.radians(66)
WALK = 1.7                     # cells a second
TURN = math.radians(150)       # a second
CEIL, FLOOR = (24, 26, 52), (54, 50, 46)
TEX = 64


def make_maze(rng, n=SIZE):
    """A perfect maze (each cell can be reached) by depth-first carving: 1 = wall."""
    g = 2 * n + 1
    m = [[1] * g for _ in range(g)]
    stack = [(0, 0)]
    m[1][1] = 0
    seen = {(0, 0)}
    while stack:
        x, y = stack[-1]
        nb = [(x + dx, y + dy, dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
              if 0 <= x + dx < n and 0 <= y + dy < n and (x + dx, y + dy) not in seen]
        if not nb:
            stack.pop()
            continue
        nx, ny, dx, dy = rng.choice(nb)
        m[2 * y + 1 + dy][2 * x + 1 + dx] = 0
        m[2 * ny + 1][2 * nx + 1] = 0
        seen.add((nx, ny))
        stack.append((nx, ny))
    return m


def bricks():
    """A 64 x 64 red brick texture with mortar, and a darker copy for the other wall side."""
    s = pygame.Surface((TEX, TEX))
    s.fill((92, 84, 76))
    rnd = __import__("random").Random(7)
    for row in range(0, TEX, 16):
        off = 0 if (row // 16) % 2 == 0 else 16
        for x in range(-32 + off, TEX, 32):
            base = rnd.randint(130, 168)
            s.fill((base, base // 3 + 14, base // 4 + 8), (x + 1, row + 1, 30, 14))
            for _ in range(10):
                px, py = x + rnd.randint(2, 29), row + rnd.randint(2, 13)
                if 0 <= px < TEX:
                    v = base - rnd.randint(10, 30)
                    s.set_at((px, py), (v, v // 3 + 10, v // 4 + 6))
    dark = s.copy()
    dark.fill((178, 178, 178), special_flags=pygame.BLEND_RGB_MULT)
    return s, dark


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        self.tex = bricks()
        self.rays = RAYS
        self._new()

    def _new(self):
        self.m = make_maze(self.rng)
        self.x, self.y = 1.5, 1.5
        self.cell = (1, 1)
        self.dir = 0 if self.m[1][2] == 0 else 1          # 0 E, 1 S, 2 W, 3 N
        self.a = self.dir * math.pi / 2
        self.target = None
        self.exit = (2 * SIZE - 1, 2 * SIZE - 1)
        self._plan()

    def lighten(self):
        super().lighten()
        self.rays = RAYS // 2

    # ------------------------------------------------------------ walking
    def _open(self, cx, cy):
        return self.m[cy][cx] == 0

    def _plan(self):
        """The next cell by the right-hand rule: right, ahead, left, back."""
        cx, cy = self.cell
        for turn in (1, 0, -1, 2):
            d = (self.dir + turn) % 4
            dx, dy = ((1, 0), (0, 1), (-1, 0), (0, -1))[d]
            if self._open(cx + dx, cy + dy):
                self.dir = d
                self.target = (cx + dx, cy + dy)
                return

    def step(self, dt, t):
        want = self.dir * math.pi / 2
        diff = (want - self.a + math.pi) % (2 * math.pi) - math.pi
        if abs(diff) > 0.01:                               # turn first, then walk
            self.a += max(-TURN * dt, min(TURN * dt, diff))
            return
        self.a = want
        tx, ty = self.target[0] + 0.5, self.target[1] + 0.5
        dx, dy = tx - self.x, ty - self.y
        d = math.hypot(dx, dy)
        s = WALK * dt
        if d <= s:
            self.x, self.y = tx, ty
            self.cell = self.target
            if self.cell == self.exit:
                self._new()
                return
            self._plan()
        else:
            self.x += dx / d * s
            self.y += dy / d * s

    # ------------------------------------------------------------ drawing
    def cast(self, ang):
        """(distance, wall side 0/1, texture x 0-1) of one ray (DDA)."""
        ca, sa = math.cos(ang), math.sin(ang)
        mx, my = int(self.x), int(self.y)
        ddx = abs(1 / ca) if ca else 1e9
        ddy = abs(1 / sa) if sa else 1e9
        if ca < 0:
            sx, side_x = -1, (self.x - mx) * ddx
        else:
            sx, side_x = 1, (mx + 1 - self.x) * ddx
        if sa < 0:
            sy, side_y = -1, (self.y - my) * ddy
        else:
            sy, side_y = 1, (my + 1 - self.y) * ddy
        m = self.m
        for _ in range(64):
            if side_x < side_y:
                side_x += ddx
                mx += sx
                side = 0
            else:
                side_y += ddy
                my += sy
                side = 1
            if m[my][mx]:
                break
        if side == 0:
            dist = side_x - ddx
            wx = self.y + dist * sa
        else:
            dist = side_y - ddy
            wx = self.x + dist * ca
        return dist, side, wx - math.floor(wx)

    def render(self, c):
        W, H = self.W, self.H
        c.fill(CEIL, (0, 0, W, H // 2))
        for k in range(8):                                  # the floor darkens far away
            v = 0.55 + 0.45 * k / 7
            c.fill(tuple(int(ch * v) for ch in FLOOR), (0, H // 2 + k * H // 16, W, H // 16 + 1))
        n = self.rays
        cw = W // n
        for i in range(n):
            off = (i + 0.5) / n - 0.5
            ray = self.a + math.atan(off * 2 * math.tan(FOV / 2))
            dist, side, u = self.cast(ray)
            dist *= math.cos(ray - self.a)                 # no fish-eye
            h = int(H / max(0.05, dist))
            tex = self.tex[side]
            tx = min(TEX - 1, int(u * TEX))
            top = (H - h) // 2
            if h > 4 * H:                                   # very near: only the part on screen
                y0 = int((-top) / h * TEX)
                y1 = int((H - top) / h * TEX) + 1
                strip = tex.subsurface((tx, max(0, y0), 1, max(1, min(TEX, y1) - max(0, y0))))
                col = pygame.transform.scale(strip, (cw, H))
                top = 0
            else:
                col = pygame.transform.scale(tex.subsurface((tx, 0, 1, TEX)), (cw, max(1, h)))
            shade = max(0.25, min(1.0, 1.6 / (dist + 0.6)))
            if shade < 0.99:
                v = int(255 * shade)
                col.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
            c.blit(col, (i * cw, top))
