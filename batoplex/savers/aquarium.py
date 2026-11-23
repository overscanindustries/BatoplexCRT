"""Aquarium: a tank two screens wide, seen through the glass. The view pans
slowly from end to end, so nothing stays in one place (burn-in).

Tank: Reef, Freshwater or Mixed. The tank is calm: each fish swims level at a steady pace and
keeps near its depth; it turns through its three-quarter view. Each kind has its own habit:
tangs cruise, tetras school (separation, alignment, cohesion), angelfish and the lionfish
hover, the clownfish nestle in their anemone, seahorses hold on to a plant, corys sift the
sand (nose down) and now and then go up for air, the betta gulps at the surface, the
pufferfish puffs. Frogs kick up for air and hang in the water; turtles cruise, surface to
breathe and rest. Feeding time, a shark or a manta far back, bubble bursts, a clam that snaps
shut, and now and then a dim moonlit spell.

Options (saveropts): the species of each tank, frogs, turtles, crabs and snails, Activity
(the pace of every animal), how often each event comes (each has its own timer; one event at a
time, a second one waits), Moonlight and Pan Speed. In the full-screen Preview, LB / RB show
the rare moments one by one (rare()); the view glides to the animal when none is in view.

Depth: a painted back wall, far fish (smaller, faded, less pan), shadows on the sand,
drifting motes. Our own pixel art (art/saver/, see art/NOTICE.md). All sprites are made once
at load.
"""
import math

import pygame

from .. import saveropts
from . import Canvas240, sheet

TANK_W = 640
SURF = 12                      # the water line
SAND_Y = 210                   # the top of the sand
WATER = (16, 54, 96)           # the mean water colour (the fade of far things)
PAN_S = 300.0                  # one pan to the far end and back
FAR_PAN = 0.55
NIGHT_EVERY, NIGHT_S, NIGHT_RAMP = 1200.0, 180.0, 20.0
# Each event's own timer (s): Feeding Time by its option; Sharks & Rays; Bubble Bursts.
FEED_S = {"rare": (600, 900), "normal": (240, 420), "often": (120, 180)}
BIG_S = BURST_S = (360, 600)
PACE = {"calm": 0.7, "normal": 1.0, "lively": 1.35}       # Activity
PAN = {"slow": 0.5, "normal": 1.0, "fast": 2.0}           # Pan Speed
MIX_ONE = ("parrot", "butterfly", "mandarin", "pleco")    # in Mixed only when picked: 1 each
GLIDE_S, LABEL_S, MOON_RAMP = 2.0, 2.0, 3.0
RARE = [("feed", "Feeding Time"), ("shark", "Shark Swims By"), ("manta", "Manta Ray Swims By"),
        ("bubbles", "Bubble Burst"), ("puff", "Pufferfish Puffs Up"), ("clam", "Clam Opens"),
        ("chest", "Treasure Chest Bubbles"), ("frog_air", "Frog Swims Up for Air"),
        ("frog_hang", "Frog Floats"), ("turtle", "Turtle Swims Up for Air"),
        ("air", "%s Dashes Up for Air"), ("seahorse", "Seahorse Grabs a Plant"),
        ("moon", "Moonlight %s")]

# name: (tank, speed px/s, band (top, bottom) as parts of the water, habit, stroke)
SPECIES = {
    "clown": ("reef", 12, (0.5, 0.8), "home", "steady"),
    "yellowtang": ("reef", 20, (0.12, 0.7), "cruise", "steady"),
    "idol": ("reef", 22, (0.1, 0.6), "cruise", "steady"),
    "lion": ("reef", 6, (0.3, 0.75), "hover", "hover"),
    "parrot": ("reef", 24, (0.3, 0.85), "cruise", "steady"),
    "puffer": ("reef", 12, (0.3, 0.8), "puffer", "hover"),
    "butterfly": ("reef", 20, (0.2, 0.75), "cruise", "steady"),
    "mandarin": ("reef", 7, (0.9, 0.97), "bottom", "steady"),
    "seahorse": ("reef", 4, (0.4, 0.8), "seahorse", "hover"),
    "jelly": ("reef", 0, (0.1, 0.55), "jelly", "hover"),
    "goldfish": ("fresh", 15, (0.2, 0.75), "cruise", "steady"),
    "angel": ("fresh", 9, (0.15, 0.65), "hover", "hover"),
    "discus": ("fresh", 10, (0.2, 0.7), "hover", "hover"),
    "betta": ("fresh", 9, (0.04, 0.3), "hover", "hover"),
    "tetra": ("fresh", 22, (0.25, 0.7), "school", "steady"),
    "guppy": ("fresh", 18, (0.1, 0.6), "cruise", "steady"),
    "pleco": ("fresh", 4, (0.97, 1.0), "bottom", "steady"),
    "cory": ("fresh", 11, (0.94, 1.0), "bottom", "steady"),
}
PITCH = {"angel": 10, "discus": 10, "idol": 12, "butterfly": 12, "lion": 12, "puffer": 12,
         "betta": 14, "seahorse": 0, "jelly": 0}          # degrees; others 14 (the climb)
# A kind that holds its head down: the lionfish always, a cory while it sifts the sand.
POSE = {"lion": -7, "cory": -20}
SLOW_TURN = ("angel", "discus", "lion")
# Who lives in each tank, and how many (Normal).
STOCK = {
    "reef": [("clown", 2), ("yellowtang", 2), ("idol", 1), ("lion", 1), ("parrot", 1),
             ("puffer", 1), ("butterfly", 2), ("mandarin", 1), ("seahorse", 2), ("jelly", 2)],
    "fresh": [("goldfish", 2), ("angel", 2), ("discus", 2), ("betta", 1), ("tetra", 7),
              ("guppy", 2), ("pleco", 1), ("cory", 3)],
    "mixed": [("clown", 2), ("yellowtang", 1), ("idol", 1), ("lion", 1), ("puffer", 1),
              ("seahorse", 1), ("jelly", 1), ("goldfish", 1), ("angel", 1), ("discus", 1),
              ("betta", 1), ("tetra", 7), ("guppy", 2), ("cory", 2)],
}
FAR = {"reef": ("yellowtang", "butterfly", "idol", "parrot"),
       "fresh": ("goldfish", "discus", "guppy", "angel"),
       "mixed": ("parrot", "goldfish", "butterfly", "discus")}
EATERS = ("cruise", "hover", "home", "puffer", "school", "bottom")

# Decor: (sheet, cell, x, front, sway px at the top, job). Sheets: decor (castle, open chest,
# seaweed, coral, rock), reefdecor (brain coral, anemone, helmet, chest, clam open, clam
# closed), plants (driftwood, sword plant, java fern, eel grass, stones).
LAYOUT = {
    "reef": [("decor", 3, 8, 0, 0, ""), ("reefdecor", 0, 58, 0, 0, ""), ("decor", 4, 96, 0, 0, ""),
             ("reefdecor", 1, 140, 0, 0, "anemone"), ("decor", 2, 196, 0, 2.5, ""),
             ("reefdecor", 2, 250, 0, 0, "helmet"), ("decor", 3, 318, 0, 0, ""),
             ("reefdecor", 0, 360, 0, 0, ""), ("reefdecor", 5, 410, 1, 0, "clam"),
             ("reefdecor", 3, 466, 0, 0, "chest"), ("decor", 4, 520, 0, 0, ""),
             ("decor", 2, 560, 1, 2.5, ""), ("reefdecor", 0, 596, 0, 0, "")],
    "fresh": [("plants", 3, 2, 0, 3.0, ""), ("plants", 0, 44, 0, 0, ""),
              ("plants", 1, 128, 0, 1.2, ""), ("decor", 0, 206, 0, 0, ""),
              ("plants", 2, 262, 1, 0.8, ""), ("plants", 4, 318, 0, 0, ""),
              ("plants", 3, 372, 0, 3.0, ""), ("reefdecor", 2, 440, 0, 0, "helmet"),
              ("plants", 1, 488, 0, 1.2, ""), ("reefdecor", 3, 556, 0, 0, "chest"),
              ("plants", 3, 590, 1, 3.0, "")],
    "mixed": [("plants", 3, 0, 0, 3.0, ""), ("decor", 3, 46, 0, 0, ""),
              ("reefdecor", 1, 100, 0, 0, "anemone"), ("plants", 0, 150, 0, 0, ""),
              ("reefdecor", 2, 230, 0, 0, "helmet"), ("plants", 2, 280, 1, 0.8, ""),
              ("reefdecor", 0, 330, 0, 0, ""), ("decor", 0, 370, 0, 0, ""),
              ("plants", 1, 428, 0, 1.2, ""), ("reefdecor", 5, 480, 1, 0, "clam"),
              ("reefdecor", 3, 520, 0, 0, "chest"), ("plants", 3, 588, 1, 3.0, "")],
}


def crop(frames):
    """Each cell cut to what it draws (sheets of different sprites)."""
    out = []
    for f in frames:
        r = f.get_bounding_rect()
        out.append(f.subsurface(r).copy() if r.w and r.h else f)
    return out


def faded(s, k, water=WATER):
    """s faded toward the water colour by k (0 to 1): far away."""
    t = s.copy()
    m = int(255 * (1 - k))
    t.fill((m, m, m), special_flags=pygame.BLEND_RGB_MULT)
    t.fill(tuple(int(v * k) for v in water), special_flags=pygame.BLEND_RGB_ADD)
    return t


def wag(s, tail=0.34, k=0.8):
    """A swim frame: the tail (the back third) narrowed, as if it swings toward us."""
    w, h = s.get_size()
    tw = max(2, int(w * tail))
    bw = w - tw
    f = pygame.Surface((w, h), pygame.SRCALPHA)
    f.blit(s.subsurface((0, 0, bw, h)), (0, 0))
    f.blit(pygame.transform.scale(s.subsurface((bw, 0, tw, h)), (max(1, int(round(tw * k))), h)), (bw, 0))
    return f


def pulse(s):
    """Four jellyfish frames: the bell squeezes (shorter and wider), the top stays put."""
    w, h = s.get_size()
    return [pygame.transform.scale(s, (int(w * kw), int(h * kh)))
            for kw, kh in ((1.0, 1.0), (1.06, 0.93), (1.12, 0.86), (1.06, 0.93))]


def both(frames):
    """{-1: facing left (as drawn), 1: facing right}."""
    return {-1: frames, 1: [pygame.transform.flip(f, True, False) for f in frames]}


def lefts(frames):
    """Frames drawn facing right, turned to face left (the rule for every sheet here)."""
    return [pygame.transform.flip(f, True, False) for f in frames]


def scaled(s, k):
    if k == 1.0:
        return s
    return pygame.transform.scale(s, (max(1, int(round(s.get_width() * k))),
                                      max(1, int(round(s.get_height() * k)))))


class Body:
    """The art of one kind at one depth: swim frames for each facing and the three-quarter view
    for the turn. A kind in POSE also has its swim frames at that one angle. Made once."""

    def __init__(self, swim, q, pose=0, fade=0.0, water=WATER):
        if fade:
            swim = [faded(f, fade, water) for f in swim]
            q = faded(q, fade, water) if q else None
        r = swim[0].get_bounding_rect()
        self.w, self.h = max(2, r.w), max(2, r.h)
        self.swim, self.pose = {}, {}
        for face in (-1, 1):
            fr = swim if face < 0 else [pygame.transform.flip(f, True, False) for f in swim]
            self.swim[face] = fr
            if pose:
                self.pose[face] = [pygame.transform.rotate(f, pose * face) for f in fr]
        self.q = {-1: q, 1: pygame.transform.flip(q, True, False)} if q else None
        self.n = len(swim)


class Mover:
    """Something that swims to a target point. It only moves the way it faces: a target
    behind it brakes it, and if the target stays behind, it turns (on an arc, toward us)."""

    def __init__(self, x, y, speed, face, turn_s=0.8, pmax=22):
        self.x, self.y = float(x), float(y)
        self.vx = self.vy = 0.0
        self.v = 0.0                   # forward speed (never below 0)
        self.speed = speed
        self.face = self.f0 = face
        self.turn = None               # 0..1 while it turns
        self.turn_s = turn_s
        self.pmax = math.radians(pmax)
        self.pitch = 0.0               # nose up > 0
        self.lift = 3.0                # px/s up or down at a stop
        self.cool = 0.0
        self.behind = 0.0
        self.slack = 24.0
        self.arrived = False
        self.tx, self.ty = self.x, self.y
        self.timer = 0.0
        self.ph = 0.0
        self.posed = False             # it shows its POSE frames
        self.pose_t = 0.0              # time before the pose can change again
        self.turn_cool = 5.0           # time after a turn before the next one
        self.near = False              # near the target: a cruiser picks the next one first
        self.creep = 0.0               # the least forward speed (it keeps pace with the pan)

    def steer(self, dt, boost=1.0, accel=1.6, can_turn=True):
        dx, dy = self.tx - self.x, self.ty - self.y
        d = math.hypot(dx, dy)
        self.cool -= dt
        if self.turn is not None:
            self.turn += dt / self.turn_s
            u = min(1.0, self.turn)
            if u >= 0.5 and self.face == self.f0:
                self.face = -self.f0
            self.vx = self.f0 * max(self.v, self.speed * 0.4) * 0.5 * math.cos(math.pi * u)
            self.vy += (max(-6.0, min(6.0, dy * 0.5)) - self.vy) * min(1.0, dt * 3)
            self.pitch *= max(0.0, 1.0 - dt * 5)
            if self.turn >= 1.0:
                self.turn = None
                self.cool = self.turn_cool
                self.v = abs(self.vx)
        else:
            ahead = dx * self.face
            want = max(self.speed * boost * min(1.0, d / 24.0), self.creep)
            self.arrived = d < 5
            self.near = d < 28
            wp = math.atan2(-dy, max(abs(dx), 48.0))       # a shallow climb or dive
            if ahead < -self.slack:
                self.behind += dt
                want = max(want * 0.4, self.creep)
                wp = 0.0
                if can_turn and self.behind > 0.8 and self.cool <= 0:
                    self.turn, self.f0, self.behind = 0.0, self.face, 0.0
            else:
                self.behind = 0.0
                if ahead < 0:                       # a little behind: brake, do not back up
                    want = self.creep
                    self.arrived = abs(dy) < 6
                    self.near = True                # a cruiser picks a new target, no hang
            wp *= min(1.0, self.v / max(1.0, self.speed))  # slow: level, it rises with the fins
            wp = max(-self.pmax, min(self.pmax, wp))
            self.pitch += (wp - self.pitch) * min(1.0, dt * 1.2)
            self.v += (want - self.v) * min(1.0, dt * accel)
            self.v = max(0.0, self.v)
            self.vx = self.face * self.v * math.cos(self.pitch)
            lift = max(-self.lift, min(self.lift, dy * 0.4))
            vy = -self.v * math.sin(self.pitch) + lift     # eased: no up and down jitter
            self.vy += (vy - self.vy) * min(1.0, dt * 2.0)
        self.x += self.vx * dt
        self.y += self.vy * dt
        return d


class Fish(Mover):
    def __init__(self, aq, name, x, y, face, layer=0):
        sp = SPECIES[name]
        self.body = aq.art[name][layer]
        h = self.body.h
        turn_s = 1.3 if name in SLOW_TURN else (0.6 if h <= 16 else 0.85)
        super().__init__(x, y, sp[1] * (1.0, 0.8, 0.6)[layer] * aq.rng.uniform(0.85, 1.15), face,
                         turn_s, PITCH.get(name, 14))
        self.name, self.habit, self.band, self.style = name, sp[3], sp[2], sp[4]
        self.layer = layer
        self.lw = aq.layer_w[layer]
        self.puffed = self.puff_len = 0.0       # puffer: > 0 while puffed
        self.puff_at = aq.rng.uniform(30, 90)
        self.ph = aq.rng.uniform(0, 4)
        self.ph0 = aq.rng.uniform(0, 6.3)
        self.mode = None                        # "air": up for a gulp; "hold": a seahorse on a plant
        self.air_at = aq.rng.uniform(180, 420)
        self.anchor = None
        self.resting = False
        self.wait = 0.0                         # far fish: time off screen
        self.hurry = 0.0                        # Preview: a seahorse sent to a plant
        if self.habit == "hover":
            self.lift = 5.0
        if self.habit == "seahorse":
            self.lift = 6.0
        if name == "lion":
            self.posed = True                  # head a little down
        self.lift0 = self.lift
        if self.habit == "school":
            self.slack, self.turn_cool = 16.0, 2.5
        if self.habit == "hover":
            self.slack = 12.0
        if self.habit == "home":
            self.slack = 26.0

    def size(self):
        return self.body.w, self.body.h


class Frog:
    """An African dwarf frog: sit, kick up for air, sink back with the legs spread."""

    def __init__(self, x, rng):
        self.x, self.y = float(x), float(SAND_Y - 8)
        self.face = rng.choice((-1, 1))
        self.state, self.timer = "sit", rng.uniform(2, 12)
        self.vx = self.vy = 0.0
        self.kick = 0.0
        self.hang_at = 0.0
        self.want_hang = False                  # Preview: hang on the way down


class Turtle(Mover):
    """A sea turtle (reef) or a red-eared slider (fresh): cruise, surface to breathe, rest."""

    def __init__(self, kind, x, y, rng):
        super().__init__(x, y, 14 if kind == "seaturtle" else 18, rng.choice((-1, 1)),
                         turn_s=1.8, pmax=15)
        self.kind = kind
        self.state = "cruise"
        self.breathe_at = rng.uniform(40, 90)
        self.timer = 0.0
        self.slack = 30.0


class Walker:
    """A crab, a hermit crab or a snail on the sand."""

    def __init__(self, kind, x, speed, rng):
        self.kind, self.x, self.speed = kind, float(x), speed
        self.dir = rng.choice((-1, 1))
        self.state, self.timer = "walk", rng.uniform(4, 14)
        self.ph = 0.0


class Saver(Canvas240):
    def __init__(self, host, opts):
        super().__init__(host, opts)
        r = self.rng
        self.tank = opts.get("tank", "mixed") if opts.get("tank") in STOCK else "mixed"
        self.many = {"few": 0.5, "many": 1.5}.get(opts.get("fish"), 1.0)
        self.pace = PACE.get(opts.get("activity"), 1.0)
        self.pan_s = PAN_S / PAN.get(opts.get("pan"), 1.0)
        self.moon = opts.get("moon") if opts.get("moon") in ("off", "always") else "20"
        self._species(opts)
        self.time = 0.0
        self.phase = r.uniform(0, 1)
        self.view = 0.0
        self.view_v = 0.0                               # px/s, + when the view moves right
        self.fade_to = (22, 62, 66) if self.tank == "fresh" else WATER
        self.layer_w = {0: TANK_W, 2: int(self.W + (TANK_W - self.W) * FAR_PAN)}
        self.far_w = self.layer_w[2]
        self._load_art()
        self.water = self._water()
        self.wall = self._wall()
        self.rocks = None if self.wall else self._rocks()
        self.back = None
        if self.wall:                                   # water and wall in one opaque picture
            self.back = pygame.Surface((self.far_w, self.H))
            for x in range(0, self.far_w, self.W):
                self.back.blit(self.water, (x, 0))
            self.back.blit(self.wall, (0, SAND_Y + 4 - self.wall.get_height()))
        self.sand = self._sand()
        self.caustic = self._caustic()
        self.decor = self._decor()
        self.rays = self._make_rays()
        self.vignette = self._vignette()
        self.shadows = {}
        self.night = 0.0
        self.fish, self.far, self.lead = [], [], None
        self._stock()
        nfrog = opts.get("frogs", 2) if opts.get("frogs", 2) in (0, 1, 2, 3) else 2
        self.frogs = [Frog(r.uniform(40, TANK_W - 40), r) for _ in range(nfrog)] \
            if self.tank in ("fresh", "mixed") and self.frog_art else []
        self.turtles = []
        for kind in {"reef": ("seaturtle",), "fresh": ("slider",),
                     "mixed": ("seaturtle", "slider")}[self.tank]:
            if self.turtle_art.get(kind) and opts.get("turtles", True) is not False:
                self.turtles.append(Turtle(kind, r.uniform(60, TANK_W - 60), r.uniform(60, 150), r))
        self.walkers = []
        for kind, sp in {"reef": (("crab", 10), ("hermit", 4)), "fresh": (("snail", 1.5),),
                         "mixed": (("crab", 10), ("snail", 1.5), ("hermit", 4))}[self.tank]:
            if self.critter.get(kind) and opts.get("crabs", True) is not False:
                self.walkers.append(Walker(kind, r.uniform(20, TANK_W - 40), sp, r))
        self.bubbles, self.flakes, self.ripples, self.puffs = [], [], [], []
        self.motes = [[r.uniform(0, TANK_W), r.uniform(SURF + 6, SAND_Y), r.choice((0, 2)),
                       r.uniform(0, 6.3)] for _ in range(40)]
        self.event = None
        self.ev_every = {}                              # event -> (lo, hi) s between two
        if opts.get("feed", "normal") in FEED_S:
            self.ev_every["feed"] = FEED_S[opts.get("feed", "normal")]
        if opts.get("big", True) is not False and self.tank != "fresh" and self.big:
            self.ev_every["big"] = BIG_S
        if opts.get("bursts", True) is not False:
            self.ev_every["bubbles"] = BURST_S
        self.due = {k: r.uniform(*v) * r.uniform(0.2, 1.0) for k, v in self.ev_every.items()}
        self.glide = None                               # Preview: the view glides to an animal
        self.night_force = None                         # Preview: moonlight on (1) or off (0)
        self.rare_key = None
        self.label = None                               # (text, until self.time)
        self.chest_at = r.uniform(8, 20)
        self.clam_open = 0.0
        self.clam_at = r.uniform(20, 50)
        self.night_t = r.uniform(0.3, 0.7) * NIGHT_EVERY      # time until the next dim spell

    def _species(self, opts):
        """Who lives here (Species). The default list: the tank as it was, with its far
        fish. A list of the user's: those kinds only; in the back, only the chosen kinds
        that cruise or hover (none: no fish far back)."""
        t = self.tank
        names, default = saveropts.aq_from(t, opts.get("sp_" + t))
        if default:
            self.stock, self.far_names = STOCK[t], FAR[t]
            return
        n = dict(STOCK[t])
        if t == "mixed":
            for k in MIX_ONE:
                n.setdefault(k, 1)
        self.stock = [(k, n[k]) for k in names if k in n]
        back = [k for k in names if SPECIES[k][3] in ("cruise", "hover")]
        self.far_names = self.rng.sample(back, min(4, len(back)))

    # ------------------------------------------------------------ art
    def _load_art(self):
        names = {n for n, _k in self.stock} | set(self.far_names)
        self.art = {}
        for name in names:
            if name == "jelly":
                cells = crop(sheet("jelly", 1))
                if cells:
                    near = pulse(cells[0])
                    self.art[name] = {0: Body(near, None)}
                continue
            cells = sheet("fish_" + name, 4)
            if len(cells) < 4:
                continue
            side, stroke, q, _front = cells
            if name == "puffer":                  # its third cell is half puffed, not a turn view
                self.puff_half, q = both([q]), None
            sets = {}
            for layer, k, fade in ((0, 1.0, 0.0), (2, 0.6, 0.55)):
                s, st = scaled(side, k), scaled(stroke, k)
                swim = [s, st, s, wag(s)] if name != "seahorse" else [s, s, s, s]
                sets[layer] = Body(swim, scaled(q, k) if q else None,
                                   POSE.get(name, 0) if layer == 0 else 0, fade, self.fade_to)
            self.art[name] = sets
        puff = crop(sheet("puffball", 1))
        self.puff_art = None
        if puff:                               # after the half-puffed fish: the ball
            self.puff_art = both([puff[0]])
        frog = crop(sheet("frog", 4))
        if len(frog) == 4:                     # the sitting frog is drawn larger: the same size
            frog[3] = scaled(frog[3], 0.65)
        self.frog_art = both(lefts(frog)) if len(frog) == 4 else None
        if self.frog_art:                                 # nose up for the kick to the air
            self.frog_up = {d: [pygame.transform.rotate(f, 55 * d) for f in self.frog_art[d][:2]]
                            for d in (-1, 1)}
        self.turtle_art = {}
        for kind in ("seaturtle", "slider"):
            fr = sheet(kind, 4)
            if len(fr) == 4:
                fr = lefts(fr)
                turn = sheet("turn_" + kind, 2)          # (the slider's is a different turtle)
                q = turn[0] if len(turn) == 2 and kind == "seaturtle" else None
                self.turtle_art[kind] = Body([fr[0], fr[1], fr[2], fr[3], fr[2], fr[1]], q)
        c = crop(sheet("critters", 5))
        self.critter = {}
        if len(c) == 5:
            self.critter = {"crab": both([c[0]]), "hermit": both(lefts([c[2]])),
                            "snail": both(lefts([c[4]])), "star": c[3]}
        big = crop(sheet("big", 2))
        self.big = {k: both(lefts([faded(big[i], 0.6, self.fade_to)]))
                    for i, k in enumerate(("shark", "manta")) if i < len(big)}

    def _water(self):
        s = pygame.Surface((self.W, self.H))
        top, bot = (34, 86, 122), (8, 28, 52)
        if self.tank == "fresh":
            top, bot = (58, 98, 86), (14, 36, 32)
        s.fill((8, 14, 22), (0, 0, self.W, SURF))          # the air under the lid
        for y in range(SURF, self.H):
            k = (y - SURF) / float(self.H - SURF)
            s.fill(tuple(int(a + (b - a) * k) for a, b in zip(top, bot)), (0, y, self.W, 1))
        return s

    def _wall(self):
        """The painted back wall of the tank (far layer), faded a little more toward the water."""
        w = sheet("wall_fresh" if self.tank == "fresh" else "wall_reef", 1)
        if not w:
            return None
        img = w[0]
        if img.get_width() != self.far_w:
            img = pygame.transform.scale(img, (self.far_w, img.get_height()))
        return faded(img, 0.15, self.fade_to)

    def _rocks(self):
        """No wall art: a low line of rock, faded (parallax layer)."""
        w = self.far_w + 2
        s = pygame.Surface((w, 100), pygame.SRCALPHA)
        rnd = __import__("random").Random(11)
        x = -10
        body = (24, 48, 70) if self.tank != "fresh" else (26, 48, 44)
        while x < w:
            rw, rh = rnd.randint(30, 70), rnd.randint(24, 70)
            pygame.draw.ellipse(s, body, (x, 100 - rh, rw, rh * 2))
            pygame.draw.ellipse(s, tuple(min(235, v + 10) for v in body),
                                (x + rw // 4, 100 - rh + 2, rw // 3, rh // 3))
            x += rw - rnd.randint(6, 16)
        return s

    def _sand(self):
        s = pygame.Surface((TANK_W, self.H - SAND_Y + 6), pygame.SRCALPHA)
        rnd = __import__("random").Random(4)
        if self.tank == "fresh":
            base, top, grains = (92, 78, 60), (120, 104, 80), ((70, 60, 48), (130, 116, 92), (84, 84, 80))
        else:
            base, top, grains = (162, 140, 96), (190, 168, 118), ((130, 110, 72), (200, 180, 132), (148, 126, 86))
        for x in range(TANK_W):
            h = 6 + int(2 * math.sin(x * 0.045) + 1.5 * math.sin(x * 0.13))
            s.fill(base, (x, h, 1, s.get_height() - h))
            s.fill(top, (x, h, 1, 2))
        for _ in range(1600):
            x, y = rnd.randrange(TANK_W), rnd.randrange(10, s.get_height())
            s.set_at((x, y), rnd.choice(grains))
        return s

    def _caustic(self):
        """Soft light patches for the sand, drawn with add (two copies move apart)."""
        s = pygame.Surface((128, self.H - SAND_Y))
        rnd = __import__("random").Random(9)
        for _ in range(18):
            x, y = rnd.randrange(128), rnd.randrange(s.get_height())
            pygame.draw.ellipse(s, (14, 14, 10), (x - 8, y - 2, rnd.randint(8, 18), rnd.randint(3, 5)), 1)
        return s

    def _sway(self, img, sway, n=12):
        h = img.get_height()
        frames = []
        for p in range(n):
            f = pygame.Surface((img.get_width() + 8, h), pygame.SRCALPHA)
            for row in range(h):
                a = (h - row) / float(h)
                dx = int(round(sway * a * a * math.sin(p * 2 * math.pi / n + row * 0.07)))
                f.blit(img, (4 + dx, row), (0, row, img.get_width(), 1))
            frames.append(f)
        return frames

    def _decor(self):
        sheets = {"decor": crop(sheet("decor", 5)), "reefdecor": crop(sheet("reefdecor", 6)),
                  "plants": crop(sheet("plants", 5))}
        out = []
        for name, k, x, front, sway, job in LAYOUT[self.tank]:
            cells = sheets.get(name) or []
            if k >= len(cells):
                continue
            img = cells[k]
            frames = self._sway(img, sway) if sway else [img]
            d = {"frames": frames, "x": x - (4 if sway else 0), "front": front, "job": job,
                 "y": SAND_Y + 6 - img.get_height(), "w": img.get_width(), "h": img.get_height(),
                 "ph": (x * 0.37) % 12, "sway": sway}
            if job == "clam":
                d["open"] = cells[4] if len(cells) > 4 else img
            if job == "anemone":                       # its top again, in front of the clownfish
                d["over"] = img.subsurface((0, 0, img.get_width(), int(img.get_height() * 0.6))).copy()
            out.append(d)
        return out

    def _vignette(self):
        """The corners of the glass a little darker (one multiply layer)."""
        s = pygame.Surface((32, 24))
        for y in range(24):
            for x in range(32):
                dx, dy = (x - 15.5) / 15.5, (y - 11.5) / 11.5
                v = int(255 * (1.0 - 0.3 * min(1.0, (dx * dx + dy * dy) / 2.0) ** 1.3))
                s.set_at((x, y), (v, v, v))
        return pygame.transform.smoothscale(s, (self.W, self.H))

    def _home(self):
        for d in self.decor:
            if d["job"] == "anemone":
                return d["x"] + d["w"] / 2.0, d["y"] + 4
        return TANK_W / 2.0, SAND_Y - 40

    def _anchors(self):
        """Points where a seahorse can hold on: plants that sway, corals."""
        out = []
        for d in self.decor:
            if d["sway"] or d["job"] in ("anemone",) or d["h"] > 30:
                out.append((d["x"] + (4 if d["sway"] else 0) + d["w"] / 2.0, d["y"] + d["h"] * 0.35, d))
        return out

    def _stock(self):
        r = self.rng
        for name, n in self.stock:
            if name not in self.art:
                continue
            school = SPECIES[name][3] == "school"
            n = max(4 if school else 1, int(round(n * self.many)))
            if self.many < 1 and n == 1 and r.random() < 0.35 and name not in ("clown", "tetra"):
                continue
            if school:
                lead = Mover(r.uniform(60, TANK_W - 60), r.uniform(70, 150), SPECIES[name][1], -1,
                             turn_s=1.2)
                lead.turn_cool = 2.5
                self.lead = lead
                for _ in range(n):
                    f = Fish(self, name, lead.x + r.uniform(-26, 26), lead.y + r.uniform(-12, 12), -1)
                    f.speed *= 1.3
                    self.fish.append(f)
                continue
            for _ in range(n):
                y0, y1 = self._band(SPECIES[name][2])
                x = r.uniform(30, TANK_W - 30)
                if SPECIES[name][3] == "home":
                    hx, hy = self._home()
                    x, y0, y1 = hx + r.uniform(-20, 20), hy - 22, hy
                f = Fish(self, name, x, r.uniform(y0, y1), r.choice((-1, 1)))
                self._aim(f)
                self.fish.append(f)
        for name in self.far_names:
            if name in self.art:
                f = Fish(self, name, r.uniform(20, self.far_w - 20), r.uniform(40, 170),
                         r.choice((-1, 1)), 2)
                self._aim(f)
                self.far.append(f)

    def _band(self, band):
        top, bot = SURF + 14, SAND_Y - 6
        return top + band[0] * (bot - top), top + band[1] * (bot - top)

    # ------------------------------------------------------------ habits
    def _ahead(self, f, lo, hi, lw):
        """A distance (lo..hi) mostly the way the fish faces, kept inside the tank. With no
        room ahead (at the end of the tank) it goes back, so it does not wait at the glass."""
        r = self.rng
        w = f.size()[0]
        a, b = w / 2 + 8, lw - w / 2 - 8
        room = b - f.x if f.face > 0 else f.x - a
        way = f.face if (r.random() < 0.92 and room >= lo) else -f.face
        return max(a, min(b, f.x + r.uniform(lo, hi) * way))

    def _aim(self, f):
        """A new target point for a fish, by its habit."""
        r = self.rng
        w, h = f.size()
        if f.mode == "air":                           # a new aim ends an air trip
            f.mode, f.lift = None, 16.0                  # (it dives back fast; see _fish_step)
            f.air_at = r.uniform(180, 420)
        y0, y1 = self._band(f.band)
        y0, y1 = max(y0, SURF + 4 + h / 2), min(y1, SAND_Y + 2 - h / 2)
        if f.layer == 2:                              # far: across and out, never a turn
            f.tx = -60 if f.face < 0 else self.far_w + 60
            f.ty = max(40, min(170, f.y + r.uniform(-30, 30)))
            f.timer = 60
            return
        if f.habit == "home":
            hx, hy = self._home()
            ahead = f.x + f.face * r.uniform(12, 36)
            f.tx = ahead if abs(ahead - hx) < 48 and r.random() < 0.93 else hx + r.uniform(-30, 30)
            f.ty = max(hy - 26, min(hy - 4, f.y + r.uniform(-8, 8)))
            f.timer = r.uniform(5, 10)
        elif f.habit == "hover":
            f.tx = self._ahead(f, 30, 100, TANK_W)
            f.ty = self._depth(f, y0, y1, 16)
            f.timer = r.uniform(6, 14)
        elif f.habit == "seahorse":
            pts = self._anchors()
            if pts:
                self._hold_on(f, r.choice(pts))
            else:
                f.tx, f.ty = self._ahead(f, 20, 70, TANK_W), r.uniform(y0, y1)
            f.timer = 90
        elif f.habit == "bottom":
            f.tx = self._ahead(f, 20, 120, TANK_W)
            f.ty = r.uniform(y0, y1)
            f.timer = r.uniform(3, 10)
        elif f.habit == "jelly":
            f.tx, f.ty = f.x + r.uniform(-30, 30), r.uniform(y0, y1)
            f.timer = r.uniform(10, 20)
        else:
            f.tx = self._ahead(f, 90, 360, TANK_W)
            f.ty = self._depth(f, y0, y1, 28)
            f.timer = r.uniform(10, 24)

    def _depth(self, f, y0, y1, d):
        """A new depth near the one it swims at (most of the time), so it does not climb and
        dive all the time. Now and then a depth anywhere in its band."""
        r = self.rng
        if r.random() < 0.2:
            return r.uniform(y0, y1)
        return max(y0, min(y1, f.y + r.uniform(-d, d)))

    def _hold_on(self, f, pt):
        """A seahorse swims to a plant or coral to hold on to."""
        ax, ay, d = pt
        h = f.size()[1]
        y0 = max(self._band(f.band)[0], SURF + 4 + h / 2)
        f.anchor = (ax + self.rng.uniform(-4, 4), min(SAND_Y + 4 - h / 2, max(y0 - 30, ay)), d)
        f.tx, f.ty = f.anchor[0], f.anchor[1]

    def _stroke(self, f, dt):
        """The boost and tail beat (Hz): steady, a little faster when it swims faster."""
        k = min(1.0, f.v / max(1.0, f.speed))
        if f.style == "hover":
            return 1.0, 0.8 + 0.8 * k
        return 1.0 + 0.15 * math.sin(self.time * 0.3 + f.ph0), 1.0 + 1.0 * k

    def _fish_step(self, f, dt):
        r = self.rng
        f.timer -= dt
        if f.habit == "jelly":
            self._jelly(f, dt)
            return
        boost, beat = self._stroke(f, dt)
        boost *= 1.0 - 0.4 * self.night
        if f.hurry > 0:
            f.hurry -= dt
            boost, beat, f.lift = 3.5, 4.0, 18.0
        can_turn = True
        if f.habit == "school":
            self._school_aim(f, dt)
        food = self._food_for(f) if f.mode is None else None
        if food is not None:
            f.tx, f.ty = food[0], food[1]
            f.resting = False                               # a pause ends for food
            f.lift = max(f.lift, 14.0)                      # up or down to a flake at once
            boost = max(boost, 1.7)
            beat = max(beat, 2.0)
        elif f.mode == "air":
            if f.y <= SURF + 6 + f.size()[1] / 2:
                self.ripples.append([f.x + f.face * f.size()[0] / 2, 0.0])
                self.bubbles.append([f.x, f.y + 2, 0.0, 1])
                self._aim(f)
            elif f.timer <= 0:                              # it could not get there: give up
                self._aim(f)
            boost = 2.0
        elif f.mode == "hold":
            d = f.anchor[2]
            p = int(self.time * 3 + d["ph"]) % 12
            k = min(1.0, dt * 2.0)                          # eases on (it stops a little off)
            f.x += (f.anchor[0] + d["sway"] * 0.5 * math.sin(p * math.pi / 6) - f.x) * k
            f.y += (f.anchor[1] + 0.6 * math.sin(self.time * 0.9 + f.ph0) - f.y) * k
            if f.timer <= 0:
                f.mode = None
                self._aim(f)
            f.ph += dt * 0.5
            return
        elif f.habit != "school" and (f.timer <= 0 or (f.arrived and not f.resting) or (
                f.near and not f.resting and f.habit in ("cruise", "hover", "bottom", "puffer"))):
            f.resting = False
            if f.habit == "seahorse" and f.anchor and math.hypot(f.anchor[0] - f.x, f.anchor[1] - f.y) < 14:
                f.mode, f.timer, f.hurry = "hold", r.uniform(20, 60), 0.0
                return
            if f.timer > 0 and f.habit in ("bottom", "hover", "home") and r.random() < 0.35:
                f.timer = r.uniform(3, 8) if f.habit == "bottom" else r.uniform(1.5, 5)
                f.tx, f.ty = f.x, f.y                           # a pause (on the sand: sift)
                f.resting = True
            else:
                self._aim(f)
        if f.name in ("cory", "betta") and f.mode is None and food is None:
            f.air_at -= dt
            if f.air_at <= 0 and f.turn is None:
                f.mode, f.lift, f.timer, f.resting = "air", 24.0, 15.0, False
                f.tx, f.ty = max(20, min(TANK_W - 20, f.x + f.face * 60)), SURF + 8
        if f.habit == "bottom":                            # resting on the sand: nose down, sift
            want = (f.name == "cory" and f.resting and f.v < 1.5 and f.y > SAND_Y - 14
                    and f.mode is None)                    # a pause: nose down in the sand
            f.pose_t -= dt
            if want != f.posed and f.pose_t <= 0:
                f.posed, f.pose_t = want, 1.0              # then it holds for 1 s
            if f.posed and r.random() < dt * 2.5:
                w = f.size()[0]
                self.puffs.append([f.x + f.face * w / 2 + r.uniform(-1, 1), SAND_Y + 3, 0.0])
        # A fish that stops in open water, with the view panning its way, slides back tail
        # first on the screen. So it keeps pace with the pan (a pause moves along with it);
        # on the sand it can sit, and at the glass it waits.
        w = f.size()[0]
        sits = f.habit == "bottom" and f.y > SAND_Y - 14
        nose = f.x + f.face * w / 2
        glass = nose > TANK_W - 20 if f.face > 0 else nose < 20     # the glass it faces
        f.creep = 0.0
        if self.view_v * f.face > 0 and not sits and not glass:
            f.creep = 1.1 * abs(self.view_v) / self.pace
            if f.resting:
                f.tx = f.x + f.face                         # the pause point goes with it
        if f.habit == "puffer":
            self._puff(f, dt)
            if f.puffed > 0:
                boost, can_turn, f.creep = 0.3, False, 0.0
        f.steer(dt, boost, accel=2.4 if f.habit == "school" else 1.6, can_turn=can_turn)
        f.ph += dt * beat
        if f.mode is None and f.hurry <= 0 and f.lift != f.lift0 \
                and f.y >= self._band(f.band)[0]:
            f.lift = f.lift0                                # back in its band after the air
        w, h = f.size()
        f.x = max(w / 2, min(TANK_W - w / 2, f.x))
        f.y = max(SURF + 4 + h / 2, min(SAND_Y + 4 - h / 2, f.y))

    def _school_aim(self, f, dt):
        """Boids: keep apart (9 px), match the others' heading (40 px), keep together, follow
        the lead point, stay in the band. The sum is a point to swim to."""
        sx = sy = ax = ay = cx = cy = 0.0
        n = 0
        for g in self.fish:
            if g is f or g.habit != "school":
                continue
            dx, dy = f.x - g.x, f.y - g.y
            d2 = dx * dx + dy * dy
            if d2 < 81:
                d = math.sqrt(d2) + 0.1
                sx += dx / d * (9 - d) / 9
                sy += dy / d * (9 - d) / 9
            if d2 < 1600:
                ax += g.vx
                ay += g.vy
                cx += g.x
                cy += g.y
                n += 1
        lead = self.lead
        sp = f.speed
        vx = 1.6 * sp * sx
        vy = 1.6 * sp * sy
        if n:
            vx += 1.0 * (ax / n) + 0.6 * (cx / n - f.x) / 40.0 * sp
            vy += 1.0 * (ay / n) + 0.6 * (cy / n - f.y) / 40.0 * sp
        vx += 0.4 * (lead.x - f.x) / 30.0 * sp
        vy += 0.4 * (lead.y - f.y) / 30.0 * sp
        y0, y1 = self._band(f.band)
        if f.y < y0 + 10:
            vy += 2.0 * sp * 0.3
        if f.y > y1 - 10:
            vy -= 2.0 * sp * 0.3
        k = min(1.0, dt * 2.5)                            # eased, so the school does not jitter
        f.tx += (f.x + vx * 0.8 - f.tx) * k
        f.ty += (f.y + vy * 0.4 - f.ty) * k

    def _jelly(self, f, dt):
        """A pulse pushes it up; between pulses it sinks slowly and drifts."""
        r = self.rng
        f.ph += dt * 1.6
        ph = f.ph % (2 * math.pi)
        push = 22 if ph < 0.9 else 0
        y0, y1 = self._band(f.band)
        f.vy += (-push - (f.vy - 4)) * min(1.0, dt * 2.0)
        if f.y < y0:
            f.vy = max(f.vy, 3.0)
        if f.y > y1:
            f.vy = min(f.vy, -6.0)
        if f.timer <= 0:
            f.vx = r.uniform(-4, 4)
            f.timer = r.uniform(8, 16)
        hw = f.size()[0] / 2
        f.x = max(hw, min(TANK_W - hw, f.x + f.vx * dt))
        f.y += f.vy * dt

    def _puff(self, f, dt):
        r = self.rng
        if f.puffed > 0:
            f.puffed -= dt
            if f.puffed <= 0:
                f.puff_at = r.uniform(40, 110)
        else:
            f.puff_at -= dt
            if f.puff_at <= 0 and self.puff_art and f.turn is None:
                f.puffed = f.puff_len = r.uniform(4, 7)

    def _food_for(self, f):
        if not self.flakes or f.layer or f.habit not in EATERS:
            return None
        if f.habit == "bottom":
            cands = [fl for fl in self.flakes if fl[1] >= SAND_Y - 6]
        else:
            cands = [fl for fl in self.flakes if fl[1] < SAND_Y - 6]
        best, bd = None, 170.0
        for fl in cands:
            d = math.hypot(fl[0] - f.x, fl[1] - f.y)
            if d < bd:
                best, bd = fl, d
        return best

    def _deep_step(self, f, dt):
        """A far fish: it swims across and out, waits, and comes back in."""
        r = self.rng
        if f.wait > 0:
            f.wait -= dt
            if f.wait <= 0:
                f.face = f.f0 = r.choice((-1, 1))
                f.x = -30.0 if f.face > 0 else self.far_w + 30.0
                f.y = r.uniform(40, 170)
                f.v = f.speed
                self._aim(f)
            return
        if f.x < -50 or f.x > self.far_w + 50:
            f.wait = r.uniform(4, 30)
            f.vx = f.vy = f.v = 0.0
            return
        f.steer(dt, can_turn=False)
        f.ph += dt * 2

    def _frog_step(self, g, dt):
        r = self.rng
        g.timer -= dt
        if g.state == "sit":
            if g.timer <= 0:
                if r.random() < 0.6:
                    g.state, g.vy = "rise", -10.0
                    g.vx = g.face * r.uniform(0, 8)
                else:
                    g.state, g.timer = "hop", r.uniform(1.5, 3.5)
                    if g.x < 60 or g.x > TANK_W - 60:
                        g.face = 1 if g.x < 60 else -1
        elif g.state == "rise":
            g.kick += dt * 3.2
            if g.kick % 1.0 < dt * 3.2:                       # a kick: a push up
                g.vy = -46.0
            g.vy += (-10 - g.vy) * min(1.0, dt * 2.5)
            g.x += g.vx * dt
            g.y += g.vy * dt
            if g.y <= SURF + 13:
                g.y, g.state, g.timer = SURF + 13, "air", r.uniform(0.8, 1.6)
                self.ripples.append([g.x, 0.0])
        elif g.state == "air":
            if g.timer <= 0:
                g.state, g.vy, g.vx = "sink", 0.0, g.face * r.uniform(0, 6)
                if g.want_hang:
                    g.hang_at, g.want_hang = r.uniform(0.6, 1.2), False
                elif r.random() < 0.35:
                    g.hang_at = r.uniform(1.5, 4)
        elif g.state == "sink":
            if g.hang_at > 0:
                g.hang_at -= dt
                if g.hang_at <= 0:                          # hang in the water, legs spread
                    g.state, g.timer = "hang", r.uniform(5, 10)
            g.vy += (9 - g.vy) * min(1.0, dt * 1.5)
            g.y += g.vy * dt
            g.x += (g.vx + g.face * 1.5 * (1 + math.sin(self.time * 0.8))) * dt
            if g.y >= SAND_Y - 8:
                g.y, g.state, g.timer = SAND_Y - 8, "sit", r.uniform(6, 22)
        elif g.state == "hang":
            g.vy *= max(0.0, 1.0 - dt * 2)
            g.y += (g.vy + 0.8 * math.sin(self.time * 0.7)) * dt
            g.x += 1.5 * math.sin(self.time * 0.4) * dt
            if g.timer <= 0:
                g.state = "sink"
        elif g.state == "hop":
            g.kick += dt * 3.0
            if g.kick % 1.0 < dt * 3.0:
                g.vx = 36.0 * g.face
            g.vx *= max(0.0, 1.0 - dt * 2.5)
            g.x += g.vx * dt
            if g.timer <= 0:
                g.state, g.timer = "sit", r.uniform(5, 16)
        food = self._food_near(g.x, g.y, 14)
        if food is not None:
            self.flakes.remove(food)
        g.x = max(16, min(TANK_W - 16, g.x))
        g.y = max(SURF + 13, min(SAND_Y - 8, g.y))

    def _turtle_step(self, t, dt):
        r = self.rng
        t.breathe_at -= dt
        t.timer -= dt
        if t.state == "cruise":
            if t.breathe_at <= 0:
                t.state = "up"
                t.tx = max(50, min(TANK_W - 50, t.x + t.face * r.uniform(30, 90)))
                t.ty = self._air_y(t)
            elif t.timer <= 0 or t.arrived:
                if t.kind == "slider" and r.random() < 0.3:
                    t.state, t.timer = "rest", r.uniform(8, 20)
                    t.tx, t.ty = t.x + t.face * r.uniform(10, 30), SAND_Y - 6
                else:
                    d = r.uniform(100, 300) * (t.face if r.random() < 0.7 else -t.face)
                    t.tx = max(50, min(TANK_W - 50, t.x + d))
                    t.ty = r.uniform(50, 160)
                    t.timer = r.uniform(12, 30)
        elif t.state == "up":
            if t.y <= self._air_y(t) + 2:
                t.state, t.timer = "air", r.uniform(1.5, 3.0)
                t.tx, t.ty = t.x, t.y
                self.ripples.append([t.x + t.face * 18, 0.0])
        elif t.state == "air":
            if t.timer <= 0:
                t.state, t.breathe_at, t.timer = "cruise", r.uniform(50, 110), 0.0
                t.tx = max(50, min(TANK_W - 50, t.x + t.face * 120))
                t.ty = r.uniform(90, 170)
                for k in range(3):                           # a few bubbles as it dives
                    self.bubbles.append([t.x + t.face * 16 + r.uniform(-2, 2), t.y - 2 + k * 3,
                                         r.uniform(0, 6.3), 2])
        elif t.state == "rest":
            if t.timer <= 0:
                t.state, t.timer = "cruise", 0.0
        moving = t.state in ("cruise", "up") or (t.state == "rest" and abs(t.ty - t.y) > 2)
        t.lift = 10.0 if t.state == "up" else 3.0
        t.steer(dt, (1.0 - 0.4 * self.night) if moving else 0.0, accel=0.8)
        if moving or t.turn is not None:
            t.ph += dt * (1.6 if t.kind == "slider" else 1.0)
        t.x = max(40, min(TANK_W - 40, t.x))
        t.y = max(self._air_y(t), min(SAND_Y - 6, t.y))

    def _air_y(self, t):
        """The centre height of a turtle whose head is at the surface."""
        return SURF + 1 + self.turtle_art[t.kind].h * 0.3

    def _walker_step(self, w, dt):
        r = self.rng
        w.timer -= dt
        if w.state == "walk":
            w.x += w.dir * w.speed * dt
            w.ph += dt * w.speed * 0.6
            if w.x < 10 or w.x > TANK_W - 30:
                w.dir = -w.dir
                w.x = max(10, min(TANK_W - 30, w.x))
            if w.timer <= 0:
                w.state, w.timer = "stop", r.uniform(2, 6)
        elif w.timer <= 0:
            w.state, w.timer = "walk", r.uniform(5, 16)
            if w.kind == "crab" and r.random() < 0.4:
                w.dir = -w.dir

    def _food_near(self, x, y, d):
        for fl in self.flakes:
            if abs(fl[0] - x) < d and abs(fl[1] - y) < d:
                return fl
        return None

    # ------------------------------------------------------------ time
    def step(self, dt, t):
        r = self.rng
        self.time += dt
        self._pan(dt)
        self._night(dt)
        dt *= self.pace                                 # Activity: the animals only
        lead = self.lead
        if lead is not None:
            lead.timer -= dt
            if lead.timer <= 0 or lead.arrived or lead.near:
                room = TANK_W - 50 - lead.x if lead.face > 0 else lead.x - 50
                way = lead.face if (r.random() < 0.85 and room >= 120) else -lead.face
                d = r.uniform(120, 360) * way
                lead.tx = max(50, min(TANK_W - 50, lead.x + d))
                lead.ty = max(60, min(160, lead.y + r.uniform(-35, 35)))
                lead.timer = r.uniform(10, 24)
            if self.flakes:
                fl = min(self.flakes, key=lambda fl: abs(fl[0] - lead.x))
                lead.tx, lead.ty = fl[0], fl[1]
                lead.timer = 0.0                           # a new target when the food is gone
            lead.steer(dt, 1.0 - 0.4 * self.night, accel=1.0)
        for f in self.fish:
            self._fish_step(f, dt)
            if self.flakes and f.habit in EATERS:
                w, _h = f.size()
                fl = self._food_near(f.x + f.face * w / 2.0, f.y, 7)
                if fl is not None:
                    self.flakes.remove(fl)
        for f in self.far:
            self._deep_step(f, dt)
        for g in self.frogs:
            self._frog_step(g, dt)
        for tt in self.turtles:
            self._turtle_step(tt, dt)
        for w in self.walkers:
            self._walker_step(w, dt)
        dt /= self.pace
        self._decor_step(dt)
        self._bubbles(dt)
        self._flakes(dt)
        self._motes(dt)
        self.puffs = [[x, y - 5 * dt, a + dt] for x, y, a in self.puffs if a + dt < 0.6][-60:]
        self.ripples = [[x, a + dt] for x, a in self.ripples if a + dt < 1.6]
        self._events(dt)

    def _pan(self, dt):
        """The view pans end to end and back (Pan Speed). A glide (Preview) moves it to an
        animal in GLIDE_S; then the pan goes on from there, the same way as before."""
        v0, glide = self.view, self.glide
        self._pan_to(dt)
        self.view_v = (self.view - v0) / dt if dt > 0 and glide is None else 0.0

    def _pan_to(self, dt):
        span = TANK_W - self.W
        g = self.glide
        if g is None:
            self.phase = (self.phase + dt / self.pan_s) % 1.0
            self.view = (0.5 - 0.5 * math.cos(self.phase * 2 * math.pi)) * span
            return
        g["t"] += dt
        u = min(1.0, g["t"] / GLIDE_S)
        want = max(0.0, min(span, g["x"]() - self.W / 2.0))
        self.view = g["from"] + (want - g["from"]) * u * u * (3 - 2 * u)
        if u >= 1.0:
            self.glide = None
            ph = math.acos(max(-1.0, min(1.0, 1 - 2 * self.view / span))) / (2 * math.pi)
            self.phase = ph if self.phase < 0.5 else 1.0 - ph

    def _night(self, dt):
        """Each 20 minutes about 3 minutes of dim moonlight (it eases in and out), or by
        Moonlight: never or always. Preview can turn it on or off (night_force)."""
        if self.night_force is not None:
            d = self.night_force - self.night
            self.night += max(-dt / MOON_RAMP, min(dt / MOON_RAMP, d))
            return
        if self.moon != "20":
            self.night = 1.0 if self.moon == "always" else 0.0
            return
        self.night_t -= dt
        if self.night_t > 0:
            self.night = 0.0
            return
        into = -self.night_t
        if into >= NIGHT_S:
            self.night_t = NIGHT_EVERY
            self.night = 0.0
            return
        self.night = min(1.0, into / NIGHT_RAMP, (NIGHT_S - into) / NIGHT_RAMP)

    def _decor_step(self, dt):
        r = self.rng
        self.chest_at -= dt
        if self.chest_at <= 0:
            self.chest_at = r.uniform(14, 30)
            for d in self.decor:
                if d["job"] == "chest":
                    cx = d["x"] + d["w"] / 2
                    for _ in range(14):
                        self.bubbles.append([cx + r.uniform(-6, 6), d["y"] + 4 + r.uniform(0, 6),
                                             r.uniform(0, 6.3), r.choice((1, 2, 2, 3))])
                    d["shake"] = 0.6
        for d in self.decor:
            if d.get("shake"):
                d["shake"] = max(0.0, d["shake"] - dt)
            if d["job"] == "helmet" and r.random() < dt * 2.2:
                self.bubbles.append([d["x"] + d["w"] * 0.55 + r.uniform(-1, 1), d["y"] + 2,
                                     r.uniform(0, 6.3), r.choice((1, 1, 2))])
        if self.clam_open > 0:
            self.clam_open -= dt
            for d in self.decor:
                if d["job"] != "clam":
                    continue
                cx, cy = d["x"] + d["w"] / 2, d["y"] + d["h"] / 2
                if any(abs(f.x - cx) < 14 and abs(f.y - cy) < 14 for f in self.fish):
                    self.clam_open, self.clam_at = 0.0, r.uniform(25, 50)   # snap!
                    self.bubbles.append([cx, d["y"], 0.0, 3])
                    d["shake"] = 0.3
        else:
            self.clam_at -= dt
            if self.clam_at <= 0:
                self.clam_open, self.clam_at = r.uniform(8, 14), r.uniform(30, 70)

    def _bubbles(self, dt):
        for b in self.bubbles:
            b[1] -= (12 + 5 * b[3]) * dt
            b[2] += dt * 3
        out = [b for b in self.bubbles if b[1] > SURF + 1]
        if len(out) < len(self.bubbles) and self.rng.random() < 0.3:
            gone = [b for b in self.bubbles if b[1] <= SURF + 1]
            self.ripples.append([gone[0][0], 0.6])
        self.bubbles = out[-400:]

    def _flakes(self, dt):
        keep = []
        for fl in self.flakes:
            fl[2] += dt
            if fl[1] < SAND_Y - 2:
                fl[1] += 8 * dt
                fl[0] += math.sin(fl[2] * 2 + fl[3]) * 6 * dt
            if fl[2] < 40:
                keep.append(fl)
        self.flakes = keep

    def _motes(self, dt):
        """Specks drifting in the water, near and far (they move with the current)."""
        for m in self.motes:
            m[0] += (2.5 if m[2] == 0 else 1.2) * dt
            m[1] += (0.6 + 1.5 * math.sin(self.time * 0.5 + m[3])) * dt
            lw = self.layer_w[m[2]]
            if m[0] > lw:
                m[0] -= lw
            if m[1] > SAND_Y:
                m[1] = SURF + 6

    def _events(self, dt):
        r = self.rng
        for k in self.due:
            self.due[k] -= dt
        if self.event is None:
            ready = [k for k in self.due if self.due[k] <= 0]  # one at a time: the others wait
            if ready:
                k = min(ready, key=lambda k: self.due[k])
                self.due[k] = r.uniform(*self.ev_every[k])
                self.start_event(r.choice(sorted(self.big)) if k == "big" else k)
            return
        e = self.event
        e["t"] += dt
        if e["kind"] in ("shark", "manta"):
            e["x"] += e["v"] * dt
            e["y"] += math.sin(e["t"] * 0.5) * 3 * dt
            if e["x"] < -200 or e["x"] > self.far_w + 60:
                self.event = None
        elif e["kind"] == "feed":
            if e["t"] < 3 and r.random() < dt * 8:
                self.flakes.append([e["x"] + r.uniform(-40, 40), SURF + 2, 0.0, r.uniform(0, 6.3)])
            if e["t"] > 3 and not self.flakes:
                self.event = None
            elif e["t"] > 55:
                self.event = None
        elif e["kind"] == "bubbles":
            if e["t"] < 6:
                for _ in range(int(dt * 40) + (1 if r.random() < (dt * 40) % 1 else 0)):
                    self.bubbles.append([e["x"] + r.uniform(-60, 60), SAND_Y - 2, r.uniform(0, 6.3),
                                         r.choice((1, 2, 3))])
            else:
                self.event = None

    def start_event(self, kind, near=False):
        """One event (tests and Preview can force one). Feed and bubbles happen in view; near:
        a shark or manta starts just outside the view, not at the far end."""
        r = self.rng
        mid = self.view + self.W / 2.0
        if kind in ("shark", "manta"):
            d = r.choice((-1, 1))
            x = -160.0 if d > 0 else self.far_w + 40.0
            if near:
                fx = self.view * FAR_PAN
                x = fx - 150.0 if d > 0 else fx + self.W + 10.0
            self.event = {"kind": kind, "t": 0.0, "dir": d, "v": d * (18 if kind == "shark" else 11),
                          "x": x, "y": r.uniform(50, 110)}
        else:
            self.event = {"kind": kind, "t": 0.0, "x": mid + r.uniform(-60, 60)}

    # ------------------------------------------------------------ Preview: rare moments
    def rare_list(self):
        """The rare moments this tank and its options have, in order."""
        fish = self.fish
        jobs = {d["job"] for d in self.decor}
        have = {
            "feed": "feed" in self.ev_every,
            "shark": "big" in self.ev_every and "shark" in self.big,
            "manta": "big" in self.ev_every and "manta" in self.big,
            "bubbles": "bubbles" in self.ev_every,
            "puff": bool(self.puff_art) and any(f.habit == "puffer" for f in fish),
            "clam": "clam" in jobs,
            "chest": "chest" in jobs,
            "frog_air": bool(self.frogs),
            "frog_hang": bool(self.frogs),
            "turtle": bool(self.turtles),
            "air": any(f.name in ("cory", "betta") for f in fish),
            "seahorse": bool(self._anchors()) and any(f.habit == "seahorse" for f in fish),
            "moon": self.moon != "off",
        }
        return [k for k, _l in RARE if have[k]]

    def rare(self, step):
        """LB (-1) / RB (1) in Preview: show the previous or next rare moment now, with its
        name at the bottom for LABEL_S. False when there is none."""
        ks = self.rare_list()
        if not ks:
            return False
        i = ks.index(self.rare_key) if self.rare_key in ks else (-1 if step > 0 else 0)
        self.rare_key = k = ks[(i + step) % len(ks)]
        self.label = (self._show(k), self.time + LABEL_S)
        return True

    def _pick(self, cands, at=lambda o: o.x):
        """One of cands, in view if it can be; else the nearest, and the view glides to it."""
        seen = [o for o in cands if 24 <= at(o) - self.view <= self.W - 24]
        if seen:
            return self.rng.choice(seen)
        mid = self.view + self.W / 2.0
        o = min(cands, key=lambda o: abs(at(o) - mid))
        self.glide = {"t": 0.0, "from": self.view, "x": lambda: at(o)}
        return o

    def _show(self, k):
        """Start one rare moment; its name."""
        r = self.rng
        name = dict(RARE)[k]
        mid = lambda d: d["x"] + d["w"] / 2.0      # noqa: E731
        if k in ("feed", "bubbles", "shark", "manta"):
            self.event = None
            self.start_event(k, near=True)
        elif k == "moon":
            on = self.night < 0.5
            self.night_force = 1.0 if on else 0.0
            return name % ("On" if on else "Off")
        elif k == "puff":
            f = self._pick([f for f in self.fish if f.habit == "puffer"])
            if f.puffed <= 0:
                f.puff_at = 0.0                 # it puffs when it is not in a turn
        elif k == "clam":
            self._pick([d for d in self.decor if d["job"] == "clam"], mid)
            self.clam_open, self.clam_at = r.uniform(8, 14), r.uniform(30, 70)
        elif k == "chest":
            self._pick([d for d in self.decor if d["job"] == "chest"], mid)
            self.chest_at = 0.0
        elif k == "frog_air":
            sit = [g for g in self.frogs if g.state in ("sit", "hop")]
            g = self._pick(sit or self.frogs)
            g.state, g.vy, g.vx, g.kick = "rise", -10.0, g.face * r.uniform(0, 8), 0.0
            g.hang_at, g.want_hang = 0.0, False
        elif k == "frog_hang":
            wet = [g for g in self.frogs if g.state in ("rise", "air", "sink", "hang")]
            g = self._pick(wet or self.frogs)
            if g in wet:
                g.state, g.timer, g.vy = "hang", r.uniform(5, 10), g.vy * 0.3
            else:                               # from the sand: up for air, hang coming down
                g.state, g.vy, g.vx, g.kick = "rise", -10.0, g.face * r.uniform(0, 8), 0.0
                g.want_hang = True
        elif k == "turtle":
            t = self._pick(self.turtles)
            if t.state in ("cruise", "rest"):
                t.state, t.timer, t.breathe_at = "cruise", 0.0, 0.0
        elif k == "air":
            cands = [f for f in self.fish if f.name in ("cory", "betta")]
            free = [f for f in cands if f.mode is None]
            f = self._pick(free or cands)
            f.air_at, f.resting = 0.0, False
            return name % ("Cory Catfish" if f.name == "cory" else "Betta")
        elif k == "seahorse":
            hs = [f for f in self.fish if f.habit == "seahorse"]
            holding = [f for f in hs if f.mode == "hold"]
            if holding:
                self._pick(holding)
                return name
            pts = self._anchors()

            def plant(f):                       # ahead, or far enough back to turn
                ok = [p for p in pts if not -f.slack - 16 < (p[0] - f.x) * f.face < 0] or pts
                p = min(ok, key=lambda p: abs(p[0] - f.x) + abs(p[1] - f.y))
                return p, abs(p[0] - f.x) + abs(p[1] - f.y)
            seen = [f for f in hs if 24 <= f.x - self.view <= self.W - 24]
            f = self._pick([min(seen or hs, key=lambda f: plant(f)[1])])
            self._hold_on(f, plant(f)[0])
            f.timer, f.resting, f.hurry = 90.0, False, 30.0
        return name

    def draw(self, s):
        super().draw(s)
        if not getattr(self.host, "preview", False):
            return
        # No button hint on the saver (the Preview row's help says it);
        # the name of a rare moment still shows.
        if self.label and self.time < self.label[1]:
            from .. import draw as ui
            from ..tokens import C
            text = self.label[0]
            w = ui.width(text, "label")
            box = pygame.Rect(0, 0, w + 40, 40)
            box.midbottom = (320, (self.host.area.bottom - 16) // 2 * 2)
            ui.rect(s, (0, 0, 0), box, alpha=170)
            ui.text(s, text, "label", C["text"], box.x, box.y + 8, w=box.w, align="center")

    # ------------------------------------------------------------ draw
    def render(self, c):
        vx = int(self.view)
        fx = int(self.view * FAR_PAN)
        if self.back:
            c.blit(self.back, (-fx, 0))
        else:
            c.blit(self.water, (0, 0))
            c.blit(self.rocks, (-fx, SAND_Y - 96))
        if self.night < 0.99:
            self._rays(c, vx)
        e = self.event
        if e and e["kind"] in ("shark", "manta") and e["kind"] in self.big:
            img = self.big[e["kind"]][e["dir"]][0]
            c.blit(img, (int(e["x"]) - fx, int(e["y"])))
        for f in self.far:
            if f.wait <= 0:
                self._draw_fish(c, f, fx)
        self._draw_motes(c, 2, fx)
        c.blit(self.sand, (-vx, SAND_Y - 6))
        for k in range(0, self.W + 256, 128):
            c.blit(self.caustic, ((-(vx + int(self.time * 5)) % 128) - 128 + k, SAND_Y),
                   special_flags=pygame.BLEND_RGB_ADD)
            c.blit(self.caustic, ((-(vx - int(self.time * 3)) % 128) - 128 + k, SAND_Y + 6),
                   special_flags=pygame.BLEND_RGB_ADD)
        for d in self.decor:
            if not d["front"]:
                self._draw_decor(c, d, vx)
        self._draw_shadows(c, vx)
        for p in self.puffs:
            px = int(p[0]) - vx
            if 0 <= px < self.W:
                c.set_at((px, int(p[1])), (150, 132, 100) if self.tank != "fresh" else (110, 96, 76))
        if "star" in self.critter and self.tank != "fresh":
            c.blit(self.critter["star"], (300 - vx, SAND_Y + 8))
        for w in self.walkers:
            self._draw_walker(c, w, vx)
        for tt in self.turtles:
            if tt.state == "rest":
                self._draw_turtle(c, tt, vx)
        for g in self.frogs:
            self._draw_frog(c, g, vx)
        for f in self.fish:
            if f.habit == "home":
                self._draw_fish(c, f, vx)
        for d in self.decor:                               # the clownfish sit in the anemone
            if d.get("over") is not None and -d["w"] < d["x"] - vx < self.W:
                c.blit(d["over"], (d["x"] - vx, d["y"]))
        for f in self.fish:
            if f.habit != "home":
                self._draw_fish(c, f, vx)
        for tt in self.turtles:
            if tt.state != "rest":
                self._draw_turtle(c, tt, vx)
        for b in self.bubbles:
            x = int(b[0] + 1.5 * math.sin(b[2])) - vx
            if 0 <= x < self.W:
                if b[3] == 1:
                    c.set_at((x, int(b[1])), (150, 205, 232))
                else:
                    pygame.draw.circle(c, (140, 196, 226), (x, int(b[1])), b[3], 1)
                    c.set_at((x - 1, int(b[1]) - 1), (210, 230, 235))
        for fl in self.flakes:
            x = int(fl[0]) - vx
            if 0 <= x < self.W:
                c.fill((214, 150, 70) if int(fl[3] * 10) % 2 else (196, 90, 60), (x, int(fl[1]), 2, 1))
        self._draw_motes(c, 0, vx)
        for d in self.decor:
            if d["front"]:
                self._draw_decor(c, d, vx)
        self._surface(c, vx)
        if self.night > 0:
            k = self.night
            c.fill((int(255 - 140 * k), int(255 - 118 * k), int(255 - 60 * k)),
                   special_flags=pygame.BLEND_RGB_MULT)
        c.blit(self.vignette, (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    def _make_rays(self):
        out = []
        for i in range(6):
            w = 18 + 8 * (i % 3)
            ray = pygame.Surface((w + 80, SAND_Y - SURF))
            ray.fill((0, 0, 0))
            pygame.draw.polygon(ray, (10, 18, 22), [(0, 0), (w, 0), (w + 50, SAND_Y - SURF),
                                                    (30, SAND_Y - SURF)])
            out.append(ray)
        return out

    def _rays(self, c, vx):
        """Soft light from the surface: bands that slide slowly (drawn with add). Each one
        comes and goes (only some draw at a time)."""
        for i, ray in enumerate(self.rays):
            if math.sin(self.time * 0.1 * (1 + i * 0.3) + i * 1.7) < -0.4:
                continue
            x0 = (i * 120 + self.time * 4 * (1 + i % 3)) % (TANK_W + 160) - 80 - vx * 0.8
            if -ray.get_width() < x0 < self.W:
                c.blit(ray, (int(x0), SURF), special_flags=pygame.BLEND_RGB_ADD)

    def _surface(self, c, vx):
        """The surface from below: a dim line and a moving shimmer under it. Burn-in: its
        height drifts 3 px over a few minutes and nothing in it is bright."""
        y = SURF + 1.5 + 1.5 * math.sin(self.time * 2 * math.pi / 200.0)
        t = self.time
        pts = [(x, y + 1.2 * math.sin((x + vx) * 0.07 + t * 1.6)) for x in range(0, self.W + 8, 8)]
        pygame.draw.lines(c, (62, 104, 128), False, pts, 1)
        iy = int(y)
        for x in range(0, self.W, 3):
            wx = x + vx
            b = math.sin(wx * 0.21 + t * 2.3) + math.sin(wx * 0.067 - t * 1.1)
            if b > 1.15:
                c.fill((88, 128, 140), (x, iy + 2 + int(1.2 * math.sin(wx * 0.07 + t * 1.6)), 2, 1))
            elif b < -1.4:
                c.fill((64, 104, 124), (x, iy + 4, 2, 1))
        for x, a in self.ripples:
            sx = int(x) - vx
            if -30 < sx < self.W + 30:
                rw = int(4 + a * 16)
                v = max(0.0, 1 - a / 1.6)
                pygame.draw.ellipse(c, tuple(int(ch * v) for ch in (150, 200, 220)),
                                    (sx - rw, SURF - 1, rw * 2, 4), 1)

    def _draw_motes(self, c, layer, ox):
        col = tuple(min(235, v + (26 if layer == 0 else 14)) for v in self.fade_to)
        for m in self.motes:
            if m[2] == layer:
                x = int(m[0]) - ox
                if 0 <= x < self.W:
                    c.set_at((x, int(m[1])), col)

    def _shadow(self, w, k):
        key = (max(8, (int(w) + 7) // 8 * 8), k)
        s = self.shadows.get(key)
        if s is None:
            s = pygame.Surface((key[0], 4))
            s.fill((255, 255, 255))
            v = (150, 185, 215)[k]
            pygame.draw.ellipse(s, (v, v, v), (0, 0, key[0], 4))
            self.shadows[key] = s
        return s

    def _draw_shadows(self, c, vx):
        """Soft shadows on the sand under what swims near it."""
        items = [(f.x, f.y + f.size()[1] / 2, f.size()[0]) for f in self.fish if f.habit != "jelly"]
        items += [(t.x, t.y + self.turtle_art[t.kind].h / 2, self.turtle_art[t.kind].w) for t in self.turtles]
        items += [(g.x, g.y + 6, 14) for g in self.frogs]
        for x, bottom, w in items:
            gap = SAND_Y + 4 - bottom
            if gap < 40:
                s = self._shadow(w * 0.8, 0 if gap < 10 else (1 if gap < 24 else 2))
                sx = int(x - s.get_width() / 2) - vx
                if -s.get_width() < sx < self.W:
                    c.blit(s, (sx, SAND_Y + 4), special_flags=pygame.BLEND_RGB_MULT)

    def _turn_img(self, b, m, frame=1):
        """The view while it turns: side, three-quarter, the other three-quarter, side. With no
        three-quarter art it narrows and comes back the other way."""
        u = min(1.0, m.turn)
        f0 = m.f0
        art = b.pose if m.posed and b.pose else b.swim    # (the lion keeps its pose)
        side = art[f0 if u < 0.5 else -f0][frame]
        if b.q is None:
            k = max(0.3, abs(math.cos(math.pi * u)))
            return pygame.transform.scale(side, (max(1, int(side.get_width() * k)), side.get_height()))
        if 0.25 <= u < 0.75:
            return b.q[f0 if u < 0.5 else -f0]
        return side

    def _draw_fish(self, c, f, vx):
        b = f.body
        if f.habit == "puffer" and f.puffed > 0 and self.puff_art:
            k = min(f.puff_len - f.puffed, f.puffed)   # half puffed for 0.3 s, then round
            img = (self.puff_half if k < 0.3 else self.puff_art)[f.face][0]
        elif f.turn is not None:
            img = self._turn_img(b, f)
        else:
            fr = b.pose[f.face] if f.posed and b.pose else b.swim[f.face]
            img = fr[int(f.ph * b.n) % b.n] if f.habit != "jelly" else fr[int(f.ph) % b.n]
        x = int(f.x - img.get_width() / 2) - vx
        if -img.get_width() < x < self.W:
            c.blit(img, (x, int(f.y - img.get_height() / 2)))

    def _draw_decor(self, c, d, vx):
        x = d["x"] - vx
        if x > self.W or x + d["w"] + 8 < 0:
            return
        if d["job"] == "clam" and self.clam_open > 0:
            img = d["open"]
            c.blit(img, (x + (d["w"] - img.get_width()) // 2, SAND_Y + 6 - img.get_height()))
            return
        frames = d["frames"]
        img = frames[int(self.time * 3 + d["ph"]) % len(frames)] if len(frames) > 1 else frames[0]
        dx = 1 if d.get("shake") and int(self.time * 20) % 2 else 0
        c.blit(img, (x + dx, d["y"]))

    def _draw_frog(self, c, g, vx):
        a = self.frog_art
        if g.state == "rise":
            img = self.frog_up[g.face][0 if g.kick % 1.0 < 0.45 else 1]
        elif g.state == "air":
            img = self.frog_up[g.face][1]
        elif g.state == "hop":
            img = a[g.face][0 if g.kick % 1.0 < 0.45 else 1]
        elif g.state in ("sink", "hang"):
            img = a[g.face][2]
        else:
            img = a[g.face][3]
        x = int(g.x - img.get_width() / 2) - vx
        if -img.get_width() < x < self.W:
            y = g.y - img.get_height() / 2 if g.state != "sit" else SAND_Y + 6 - img.get_height()
            if g.state == "hop":
                y = SAND_Y + 4 - img.get_height()
            c.blit(img, (x, int(y)))

    def _draw_turtle(self, c, t, vx):
        b = self.turtle_art[t.kind]
        if t.turn is not None:
            img = self._turn_img(b, t, 2)
        else:
            img = b.swim[t.face][int(t.ph * 3) % b.n if t.state != "rest" else 0]
        x = int(t.x - img.get_width() / 2) - vx
        if -img.get_width() < x < self.W:
            c.blit(img, (x, int(t.y - img.get_height() / 2)))

    def _draw_walker(self, c, w, vx):
        frames = self.critter[w.kind][w.dir if w.kind != "crab" else -1]
        if w.kind == "crab":
            img = frames[0]
            bob = int(w.ph * 2) % 2 if w.state == "walk" else 0
        else:
            img, bob = frames[0], (int(w.ph) % 2 if w.kind == "hermit" and w.state == "walk" else 0)
        x = int(w.x) - vx
        if -img.get_width() < x < self.W:
            c.blit(img, (x, SAND_Y + 10 - img.get_height() - bob))
