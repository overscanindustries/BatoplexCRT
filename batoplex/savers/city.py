"""Night City: a slow drive past a city at night. Four layers scroll at their
own speeds (far skyline, buildings, landmarks, the street), windows go on and off, stars
twinkle and the moon crosses the sky. Ten landmarks come by in an order that repeats. About
every 2 to 5 minutes one rare event: a UFO, a blimp, a train, or fireworks. Our own pixel art
(art/saver/, see art/NOTICE.md); the idea comes from the drive-by city screen savers of TV
boxes, with nothing taken from them.

Burn-in: only the sky is fixed, and it changes (twinkle, the moon).
"""
import math

import pygame

from . import Canvas240, sheet

GROUND = 206                   # the street line
SPEED = (2.0, 4.0, 8.0, 16.0)  # far, buildings, landmarks, street: canvas px a second
LANDMARK_GAP = (70, 130)
EVENT_S = (120, 300)
SKY_TOP, SKY_LOW = (8, 10, 34), (46, 30, 82)


def _sky():
    s = pygame.Surface((320, GROUND))
    for y in range(GROUND):
        k = (y / (GROUND - 1)) ** 1.6
        s.fill(tuple(int(a + (b - a) * k) for a, b in zip(SKY_TOP, SKY_LOW)), (0, y, 320, 1))
    return s


class Strip:
    """A layer: a long picture made once, drawn with wrap-around at an offset."""

    def __init__(self, surf, speed, y):
        self.surf, self.speed, self.y, self.x = surf, speed, y, 0.0

    def draw(self, c):
        w = self.surf.get_width()
        x = -int(self.x) % w - w
        while x < 320:
            c.blit(self.surf, (x, self.y))
            x += w


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        r = self.rng
        self.sky = _sky()
        self.stars = [(r.randrange(320), r.randrange(GROUND - 70), r.random()) for _ in range(90)]
        self.moon = r.uniform(0, 1)
        self.time = 0.0
        self.lands = sheet("land1", 5) + sheet("land2", 5)
        self.veh = sheet("vehicles", 5)
        self.far = Strip(self._far(), SPEED[0], GROUND - 70)
        self.mid, self.windows = self._mid()
        self.mid = Strip(self.mid, SPEED[1], GROUND - 96)
        self.front = Strip(self._landmarks(), SPEED[2], 0)
        self.street = Strip(self._street(), SPEED[3], GROUND)
        self.cars = []
        self.event = None
        self.next_event = r.uniform(*EVENT_S) * 0.25        # the first one comes sooner
        self.sparks = []

    # ------------------------------------------------------------ layers
    def _far(self):
        r = self.rng
        s = pygame.Surface((640, 70), pygame.SRCALPHA)
        x = 0
        while x < 640:
            w, h = r.randint(14, 34), r.randint(18, 64)
            s.fill((34, 28, 66), (x, 70 - h, w, h))
            for _ in range(w * h // 90):
                s.fill((110, 90, 70), (x + r.randrange(1, w - 1), 70 - h + r.randrange(2, h), 1, 1))
            x += w + r.randint(-4, 3)
        return s

    def _mid(self):
        r = self.rng
        s = pygame.Surface((960, 96), pygame.SRCALPHA)
        wins = []
        x = 0
        while x < 960:
            w, h = r.randint(24, 52), r.randint(26, 66)
            body = r.choice(((40, 40, 78), (48, 36, 70), (30, 46, 70)))
            s.fill(body, (x, 96 - h, w, h))
            s.fill(tuple(min(235, v + 14) for v in body), (x, 96 - h, w, 2))
            for wy in range(96 - h + 6, 92, 7):
                for wx in range(x + 4, x + w - 4, 6):
                    on = r.random() < 0.35
                    wins.append([wx, wy, on])
                    s.fill((232, 196, 110) if on else (24, 24, 48), (wx, wy, 3, 4))
            x += w + r.randint(2, 10)
        return s, wins

    def _landmarks(self):
        r = self.rng
        n = len(self.lands)
        order = list(range(n))
        r.shuffle(order)
        xs, x = [], 40
        for k in order:
            xs.append((x, k))
            x += (self.lands[k].get_width() if n else 30) + r.randint(*LANDMARK_GAP)
        s = pygame.Surface((max(320, x), GROUND), pygame.SRCALPHA)
        s.fill((0, 0, 0, 0))
        for x0, k in xs:
            img = self.lands[k]
            s.blit(img, (x0, GROUND - img.get_height() + 1))
        return s

    def _street(self):
        s = pygame.Surface((320, 240 - GROUND))
        s.fill((22, 20, 36))
        s.fill((48, 44, 66), (0, 0, 320, 3))                  # the curb
        for x in range(0, 320, 24):
            s.fill((150, 140, 90), (x, 18, 12, 2))           # lane marks
        for x in range(8, 320, 80):                          # lamp posts with a glow
            s.fill((70, 66, 90), (x, 0, 2, 3))
        return s

    # ------------------------------------------------------------ time
    def step(self, dt, t):
        r = self.rng
        self.time += dt
        for layer in (self.far, self.mid, self.front, self.street):
            layer.x += layer.speed * dt
        if r.random() < dt * 3:                               # a window goes on or off
            w = r.choice(self.windows)
            w[2] = not w[2]
            self.mid.surf.fill((232, 196, 110) if w[2] else (24, 24, 48), (w[0], w[1], 3, 4))
        if r.random() < dt * 0.25 and len(self.cars) < 3:
            d = r.choice((-1, 1))
            self.cars.append([340 if d < 0 else -40, d, r.uniform(26, 40)])
        for c in self.cars:
            c[0] += c[1] * c[2] * dt - SPEED[3] * dt
        self.cars = [c for c in self.cars if -60 < c[0] < 380]
        self.moon = (self.moon + dt / 1800.0) % 1.0
        self._events(dt)

    def _events(self, dt):
        r = self.rng
        if self.event is None:
            self.next_event -= dt
            if self.next_event <= 0:
                kind = r.choice(("ufo", "blimp", "train", "fireworks"))
                self.start_event(kind)
            return
        e = self.event
        e["t"] += dt
        if e["kind"] in ("ufo", "blimp"):
            e["x"] += e["v"] * dt
            if e["x"] < -80:
                self.event = None
        elif e["kind"] == "train":
            e["x"] -= 60 * dt
            if e["x"] < -60 * e["cars"] - 80:
                self.event = None
        elif e["kind"] == "fireworks":
            if e["t"] < 14 and r.random() < dt * 1.6:
                cx, cy = r.uniform(40, 280), r.uniform(30, 90)
                col = r.choice(((235, 90, 90), (235, 210, 90), (110, 200, 235), (180, 120, 235),
                                (120, 235, 140)))
                for i in range(28):
                    a = i * math.pi * 2 / 28
                    sp = r.uniform(20, 34)
                    self.sparks.append([cx, cy, math.cos(a) * sp, math.sin(a) * sp, 1.0, col])
            for s in self.sparks:
                s[0] += s[2] * dt
                s[1] += s[3] * dt
                s[3] += 14 * dt
                s[4] -= dt * 0.55
            self.sparks = [s for s in self.sparks if s[4] > 0]
            if e["t"] > 14 and not self.sparks:
                self.event = None
        if self.event is None:
            self.next_event = r.uniform(*EVENT_S)

    def start_event(self, kind):
        """One rare event (tests can force one)."""
        r = self.rng
        if kind == "ufo":
            self.event = {"kind": kind, "t": 0.0, "x": 340.0, "y": r.uniform(30, 70), "v": -22.0}
        elif kind == "blimp":
            self.event = {"kind": kind, "t": 0.0, "x": 340.0, "y": r.uniform(26, 60), "v": -7.0}
        elif kind == "train":
            self.event = {"kind": kind, "t": 0.0, "x": 340.0, "cars": r.randint(3, 5)}
        else:
            self.event = {"kind": kind, "t": 0.0}

    # ------------------------------------------------------------ draw
    def render(self, c):
        c.blit(self.sky, (0, 0))
        c.fill((10, 10, 20), (0, GROUND, 320, 240 - GROUND))
        for x, y, ph in self.stars:
            v = int(90 + 120 * (0.5 + 0.5 * math.sin(self.time * (0.7 + ph) + ph * 9)))
            c.set_at((x, y), (v, v, min(235, v + 20)))
        mx = int(-20 + self.moon * 360)
        my = int(40 - 24 * math.sin(self.moon * math.pi))
        pygame.draw.circle(c, (226, 222, 196), (mx, my), 9)
        pygame.draw.circle(c, (200, 196, 172), (mx + 3, my - 2), 2)
        e = self.event
        if e and e["kind"] in ("ufo", "blimp"):
            k = 0 if e["kind"] == "ufo" else 1
            if k < len(self.veh):
                img = self.veh[k]
                y = e["y"] + (2 * math.sin(e["t"] * 3) if k == 0 else 0)
                c.blit(img, (int(e["x"]), int(y)))
                if k == 0 and int(e["t"] * 4) % 2:
                    c.fill((140, 235, 160), (int(e["x"]) + img.get_width() // 2, int(y) + img.get_height(), 1, 1))
        for s in self.sparks:
            v = s[4]
            c.set_at((int(s[0]), int(s[1])), tuple(int(ch * v) for ch in s[5]))
        self.far.draw(c)
        self.mid.draw(c)
        self.front.draw(c)
        self.street.draw(c)
        if e and e["kind"] == "train" and len(self.veh) > 3:
            x = int(e["x"])
            loco, car = self.veh[2], self.veh[3]
            c.blit(loco, (x, GROUND - loco.get_height() + 6))
            x += loco.get_width() + 1
            for _ in range(e["cars"]):
                c.blit(car, (x, GROUND - car.get_height() + 6))
                x += car.get_width() + 1
        if len(self.veh) > 4:
            if not hasattr(self, "_car_r"):
                self._car_r = pygame.transform.flip(self.veh[4], True, False)
            for cx, d, _v in self.cars:
                img = self.veh[4] if d < 0 else self._car_r
                c.blit(img, (int(cx), 240 - img.get_height() - (2 if d < 0 else 10)))
