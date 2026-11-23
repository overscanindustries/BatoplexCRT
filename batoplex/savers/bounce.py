"""Bounce: 1 to 5 posters or DVD Video logos on black, each on its own path.

Facts behind the logo: the DVD logo bounced slowly on black, some players changed its colour
at an edge hit, and the Philips DVD727 started it after about 5 minutes stopped. The colours
are our pick (CRT safe: saturation 80 % at most, no channel above 235); a new colour never
repeats the old one. Posters: each object changes its poster at a wall hit when 20 s passed;
the server name bounces until the first poster is in, or when the libraries give none.

All sprites are filtered one time (savers.Sprites), so the saver is prefiltered.
"""
import os

import pygame

from .. import config, tokens
from . import BLACK, PAD, SPEEDS, Bounce, Sprites, tint

POSTER = {"small": (80, 120), "medium": (112, 168), "large": (144, 216)}
LOGO_W = {"small": 96, "medium": 128, "large": 176}
# Logo -> file: the DVD logo forms seen on idle players (art/NOTICE.md).
LOGO_FILES = {"classic": "dvd-logo.png", "mark": "dvd-logo-mark.png", "oval": "dvd-logo-oval.png"}
WHITE = (235, 235, 235)
COLORS = [(235, 72, 72), (235, 148, 48), (232, 220, 64), (88, 220, 96), (64, 208, 232),
          (88, 116, 235), (176, 96, 235), (235, 104, 196)]
CHANGE_S = 20.0


class Obj:
    __slots__ = ("b", "k", "shown_at", "color")

    def __init__(self, b, k, color):
        self.b, self.k, self.shown_at, self.color = b, k, None, color


class BounceSaver:
    prefiltered = True

    def __init__(self, host, opts):
        self.host = host
        self.rng = host.rng
        self.art = opts["art"]
        self.count = opts["count"]
        self.colors = opts["colors"]
        self.px, self.every = SPEEDS[opts["speed"]]
        self.size = opts["size"]
        self.logo_style = opts.get("logo", "classic")
        self.sprites = Sprites()
        self.items = []
        self.next_k = 0
        self.objs = []
        self.name = None                       # the name box until a poster is in
        self.logo = None
        if self.art == "dvd":
            self._logo_objs()
        else:
            self.name = self._bounce(host.name_surf.get_size())

    # ------------------------------------------------------------ setup
    def _bounce(self, size):
        return Bounce(self.host.area, size, self.rng, self.px, self.every)

    def _logo_objs(self):
        path = os.path.join(config.APP_DIR, "art", LOGO_FILES.get(self.logo_style, "dvd-logo.png"))
        try:
            img = pygame.image.load(path).convert() if pygame.display.get_surface() \
                else pygame.image.load(path)
            w = LOGO_W[self.size]
            h = (w * img.get_height() // img.get_width()) // 2 * 2   # its own shape, even
            self.logo = pygame.transform.smoothscale(img, (w, h))
        except (pygame.error, FileNotFoundError):
            self.logo = None                    # a missing file (an older install): the name
            self.name = self._bounce(self.host.name_surf.get_size())
            return
        for i in range(self.count):
            c = self.rng.randrange(len(COLORS)) if self.colors else None
            self.objs.append(Obj(self._bounce((w, h)), i, c))

    def set_items(self, items):
        """Posters from the pinned libraries (the host loads them)."""
        if self.art != "posters":
            return
        if not items and self.items:
            return                              # a failed reload keeps what shows
        self.items = items
        self.next_k = 0
        if not items:
            self.objs = []
            if self.name is None:
                self.name = self._bounce(self.host.name_surf.get_size())
            return
        size = POSTER[self.size]
        keep = [o.b for o in self.objs] or ([self.name] if self.name is not None else [])
        self.objs = []
        for i in range(min(self.count, len(items))):
            b = keep[i] if i < len(keep) else self._bounce(size)
            b.resize(size)
            self.objs.append(Obj(b, i, None))
        self.next_k = len(self.objs)

    # ------------------------------------------------------------ images
    def _poster(self, k, request=True):
        h = self.host
        if h.app.images is None or not self.items:
            return None
        it = self.items[k % len(self.items)]
        w, hh = POSTER[self.size]
        return h.app.images.get(it.poster, w, hh, tokens.GAIN["unfocused"], request=request)

    def _sprite(self, o):
        if self.art == "dvd":
            c = WHITE if o.color is None else COLORS[o.color]
            return self.sprites.get(("logo", c), lambda: tint(self.logo, c))
        it = self.items[o.k % len(self.items)]
        return self.sprites.get(("poster", it.poster, self.size),
                                lambda: self._poster(o.k, request=False))

    # ------------------------------------------------------------ time
    def update(self, t, now):
        """Once per field. t: frame clock ms; now: seconds. True when the frame changed."""
        self.sprites.set_mode(self.host.filter_mode())
        moved = False
        if self.art == "posters" and self.objs:
            if self.name is not None:
                if any(self._poster(o.k) is not None for o in self.objs):
                    self.name = None            # the first poster is in
                    for o in self.objs:
                        o.shown_at = now
                    moved = True
                else:
                    return self.name.step(t)
            for o in self.objs:
                self._poster(self.next_k)        # prefetch the next one
                if o.shown_at is None and self._poster(o.k) is not None:
                    o.shown_at = now
                    moved = True
        elif self.name is not None:
            return self.name.step(t)
        for o in self.objs:
            if not o.b.step(t):
                continue
            moved = True
            if not o.b.hit:
                continue
            if self.art == "dvd" and self.colors:
                o.color = (o.color + self.rng.randrange(1, len(COLORS))) % len(COLORS)
            elif self.art == "posters" and o.shown_at is not None and \
                    now - o.shown_at >= CHANGE_S and self._poster(self.next_k) is not None:
                o.k, o.shown_at = self.next_k, now
                self.next_k += 1
        return moved

    def first_box(self):
        """(x, y) of the first object, for the album art to start there (no jump)."""
        b = self.name or (self.objs[0].b if self.objs else None)
        return (b.x, b.y) if b is not None else None

    # ------------------------------------------------------------ draw
    def draw(self, s):
        s.fill(BLACK)
        if self.name is not None:
            spr = self.sprites.get(("name",), lambda: self.host.name_surf)
            s.blit(spr, (self.name.x, self.name.y - PAD), special_flags=pygame.BLEND_RGB_MAX)
            return
        for o in self.objs:
            if self.art == "posters" and o.shown_at is None:
                continue                          # its poster is not in yet
            spr = self._sprite(o)
            if spr is None:
                continue
            flags = pygame.BLEND_RGB_MAX if self.art == "dvd" else 0
            s.blit(spr, (o.b.x, o.b.y - PAD), special_flags=flags)
