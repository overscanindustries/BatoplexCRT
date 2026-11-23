"""Flying Objects: winged toasters, slices of toast and winged CRT TVs fly from
the top right to the bottom left over black, at a few speeds; the faster ones are nearer and
draw on top. Our own pixel art (art/saver/, see art/NOTICE.md): a homage to the 1990s
desktop savers, with no sprite, frame or name taken from them.
"""
import pygame

from . import BLACK, Canvas240, sheet

N = 11
SPEEDS = (14.0, 20.0, 27.0)    # canvas px a second along x
SLOPE = 0.5                    # y px for each x px
FLAP_S = 0.12                  # a wing frame
WINGS = (0, 1, 2, 1, 0, 1, 2, 3)   # the beat: up, level, down, level (frame 3 is the up-stroke)


class Obj:
    __slots__ = ("kind", "x", "y", "v", "t")


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        self.sheets = {"toaster": sheet("toaster", 4), "tv": sheet("tv", 4),
                       "toast": sheet("toast", 1)}
        mode = opts.get("objects", "toasters")
        self.kinds = {"toasters": ["toaster", "toaster", "toaster", "toast"],
                      "tvs": ["tv"], "mixed": ["toaster", "tv", "toaster", "tv", "toast"]}[mode]
        self.objs = []
        for i in range(N):
            o = Obj()
            self._spawn(o, first=True)
            self.objs.append(o)

    def _size(self, kind):
        frames = self.sheets[kind]
        return frames[0].get_size() if frames else (8, 8)

    def _spawn(self, o, first=False):
        r = self.rng
        o.kind = r.choice(self.kinds)
        o.v = r.choice(SPEEDS)
        o.t = r.uniform(0, 1)
        w, h = self._size(o.kind)
        for _ in range(12):                       # a place clear of the others
            if first:
                o.x, o.y = r.uniform(-w, self.W), r.uniform(-h, self.H)
            elif r.random() < 0.6:
                o.x, o.y = r.uniform(self.W * 0.2, self.W + w), -h - r.uniform(0, 20)
            else:
                o.x, o.y = self.W + r.uniform(0, 20), r.uniform(-h, self.H * 0.6)
            box = pygame.Rect(int(o.x), int(o.y), w, h).inflate(8, 8)
            if not any(p is not o and getattr(p, "kind", None) and
                       box.colliderect(pygame.Rect(int(p.x), int(p.y), *self._size(p.kind)))
                       for p in getattr(self, "objs", [])):
                break

    def step(self, dt, t):
        for o in self.objs:
            o.x -= o.v * dt
            o.y += o.v * SLOPE * dt
            o.t += dt
            w, h = self._size(o.kind)
            if o.x < -w - 4 or o.y > self.H + 4:
                self._spawn(o)

    def render(self, c):
        c.fill(BLACK)
        for o in sorted(self.objs, key=lambda o: o.v):
            frames = self.sheets[o.kind]
            if not frames:
                continue
            f = frames[WINGS[int(o.t / FLAP_S) % len(WINGS)] % len(frames)]
            c.blit(f, (int(o.x), int(o.y)))
