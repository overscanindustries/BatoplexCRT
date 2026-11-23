"""Pong attract mode: two paddles play each other. Each paddle aims with a
small error and a speed limit, so points are scored; a game ends at 11 and a new one starts.
The 1970s arcade look, drawn new: square ball, block digits, a dashed net.

Burn-in: the net, the walls and the scores are fixed, so they are drawn at about half
brightness and the whole court moves on the Orbit loop; the court also moves 1 px after each
point.
"""
from . import BLACK, Canvas240, Orbit

W, H = 320, 240
TOP, BOTTOM = 20, 228          # the walls (inside edges)
WALL = 4
PAD_W, PAD_H = 4, 24
PAD_X = (16, W - 16 - PAD_W)
BALL = 4
PAD_SPEED = 150.0              # canvas px a second
SERVE_SPEED = 130.0
HIT_GAIN = 1.06
MAX_SPEED = 260.0
WIN = 11
PAUSE_S = 1.0                  # after a point
END_S = 3.0                    # after a game
FG = (235, 235, 235)
DIM = (120, 120, 120)

# 3 x 5 block digits (rows top to bottom, "#" = a block)
DIGITS = ["###,#.#,#.#,#.#,###", ".#.,##.,.#.,.#.,###", "###,..#,###,#..,###",
          "###,..#,###,..#,###", "#.#,#.#,###,..#,..#", "###,#..,###,..#,###",
          "###,#..,###,#.#,###", "###,..#,..#,..#,..#", "###,#.#,###,#.#,###",
          "###,#.#,###,..#,###"]
BLOCK = 4


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        self.orbit = Orbit()
        self.score = [0, 0]
        self.pads = [H / 2 - PAD_H / 2, H / 2 - PAD_H / 2]
        self.aim = [0.0, 0.0]          # each paddle's error, set when the ball comes its way
        self.wait = PAUSE_S
        self.shift = 0
        self.t = 0.0
        self._serve(self.rng.choice((0, 1)))

    # ------------------------------------------------------------ play
    def _serve(self, to):
        r = self.rng
        self.bx, self.by = W / 2 - BALL / 2, r.uniform(TOP + 20, BOTTOM - 20 - BALL)
        sp = SERVE_SPEED
        self.vx = sp * (1 if to == 1 else -1)
        self.vy = r.choice((-1, 1)) * r.uniform(0.3, 0.7) * sp
        self._new_aim(to)

    def _new_aim(self, side):
        """Where on its face the paddle tries to meet the ball; now and then a bad read."""
        r = self.rng
        bad = r.random() < 0.25
        self.aim[side] = r.uniform(-PAD_H * 1.2, PAD_H * 1.2) if bad else \
            r.uniform(-PAD_H * 0.35, PAD_H * 0.35)

    def step(self, dt, t):
        """Sub-steps of 1/60 s at most, so the ball never jumps a paddle (a weak box
        steps each 2nd slot, a stall up to 0.2 s)."""
        self.t = t
        while dt > 1e-9:
            h = min(dt, 1 / 60.0)
            self._tick(h)
            dt -= h

    def _tick(self, dt):
        if self.wait > 0:
            self.wait -= dt
            if self.wait <= 0 and max(self.score) >= WIN:
                self.score = [0, 0]
            return
        self.bx += self.vx * dt
        self.by += self.vy * dt
        if self.by < TOP:
            self.by, self.vy = 2 * TOP - self.by, abs(self.vy)
        elif self.by + BALL > BOTTOM:
            self.by, self.vy = 2 * (BOTTOM - BALL) - self.by, -abs(self.vy)
        for side in (0, 1):
            coming = (self.vx < 0) == (side == 0)
            target = (self.by + BALL / 2 + self.aim[side] if coming else H / 2) - PAD_H / 2
            d = target - self.pads[side]
            mx = PAD_SPEED * dt * (1.0 if coming else 0.4)
            self.pads[side] += max(-mx, min(mx, d))
            self.pads[side] = max(TOP, min(BOTTOM - PAD_H, self.pads[side]))
        self._hits()
        if self.bx + BALL < 0 or self.bx > W:
            win = 1 if self.bx < 0 else 0
            self.score[win] += 1
            self.shift = (self.shift + 1) % 3
            self.wait = END_S if self.score[win] >= WIN else PAUSE_S
            self._serve(1 - win)

    def _hits(self):
        for side in (0, 1):
            px, py = PAD_X[side], self.pads[side]
            toward = self.vx < 0 if side == 0 else self.vx > 0
            if not toward:
                continue
            if self.bx < px + PAD_W and self.bx + BALL > px and \
                    self.by + BALL > py and self.by < py + PAD_H:
                off = (self.by + BALL / 2 - (py + PAD_H / 2)) / (PAD_H / 2)   # -1 .. 1
                sp = min(MAX_SPEED, (self.vx ** 2 + self.vy ** 2) ** 0.5 * HIT_GAIN)
                self.vy = sp * 0.7 * max(-1.0, min(1.0, off))     # the edge: a steep return
                self.vx = (sp * sp - self.vy * self.vy) ** 0.5 * (1 if side == 0 else -1)
                self.bx = px + PAD_W if side == 0 else px - BALL
                self._new_aim(1 - side)

    # ------------------------------------------------------------ draw
    def _digit(self, c, n, x, y, col):
        for r, line in enumerate(DIGITS[n].split(",")):
            for k, ch in enumerate(line):
                if ch == "#":
                    c.fill(col, (x + k * BLOCK, y + r * BLOCK, BLOCK, BLOCK))

    def _number(self, c, n, cx, y, col):
        s = str(n)
        w = len(s) * 4 * BLOCK - BLOCK
        x = cx - w // 2
        for ch in s:
            self._digit(c, int(ch), x, y, col)
            x += 4 * BLOCK

    def render(self, c):
        c.fill(BLACK)
        ox, oy = self.orbit.at(self.t)
        ox += self.shift
        c.fill(DIM, (ox, TOP - WALL + oy, W, WALL))
        c.fill(DIM, (ox, BOTTOM + oy, W, WALL))
        for y in range(TOP + 2, BOTTOM - 4, 12):
            c.fill(DIM, (W // 2 - 2 + ox, y + oy, 4, 6))
        self._number(c, self.score[0], W // 4 + ox, TOP + 8 + oy, DIM)
        self._number(c, self.score[1], 3 * W // 4 + ox, TOP + 8 + oy, DIM)
        for side in (0, 1):
            c.fill(FG, (PAD_X[side] + ox, int(self.pads[side]) + oy, PAD_W, PAD_H))
        if self.wait <= 0:
            c.fill(FG, (int(self.bx) + ox, int(self.by) + oy, BALL, BALL))
