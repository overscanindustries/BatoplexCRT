"""Screensaver. Not a screen: the app draws it over an
unchanged stack, so loaders, theme music and server joins go on under it.

The host: it reads the type and its options (saveropts), loads library items when the type
needs them, owns the During Music rule (the album art bounces while a track plays) and the
server-name fallback of Entertainment, and hands the rest to one saver object:

- Bounce (savers/bounce.py): posters or DVD Video logos.
- Digital Rain (rain.py).
- Entertainment (here): pinned-library art with the clock and the title; changes each 20 s.

Black background, no frame; nothing stays in one place for long (CRT burn-in). Motion runs on
the frame clock (anim.now), so a late field does not slow it.
"""
import random
import threading
import time

import pygame

from . import anim, draw, lineup, log, saveropts, tokens
from .savers import PAD, Bounce, Sprites
from .tokens import C

L = log.get("saver")

BLACK = (0, 0, 0)
MUSIC = (168, 168)        # album art while a track plays
ART = (641, 360)
CHANGE_S = 20.0           # Entertainment: each change
FADE_FIELDS = 18
FADE_MS = FADE_FIELDS * tokens.FIELD_MS
RELOAD_S = 30 * 60
BLANK_S = 0.5             # Shuffle: 30 fields of black between two savers
NAME_MAX_W = 360
SAVERS = saveropts.SAVERS
TYPES = tuple(SAVERS)


def text_y(area, band_top, band_bottom, h=40):
    """The text line centred in the part of the band inside the safe area, down to 4 px."""
    top, bottom = max(area.y, band_top), min(area.bottom, band_bottom)
    return ((top + bottom) // 2 - h // 2) // 4 * 4


class RainSaver:
    """Digital Rain (rain.py) behind the saver interface."""
    prefiltered = True

    def __init__(self, host, opts):
        from .rain import Rain
        self.host = host
        self.rain = Rain(host.filter_mode(), host.rng, size=opts["size"], color=opts["color"],
                         speed=opts["speed"], density=opts["density"], glyphs=opts["glyphs"])

    def set_items(self, items):
        pass

    def first_box(self):
        return None

    def update(self, t, now):
        self.rain.set_mode(self.host.filter_mode())
        return self.rain.step(t / 1000.0)

    def draw(self, s):
        s.blit(self.rain.surface, (0, 0))


class Screensaver:
    def __init__(self, app, clock=time.monotonic, rng=random, ms=anim.now, kind=None,
                 music=True, inset=False, preview=False):
        """kind: the type to show (Preview); else the saved one. music: False: no album art
        takes over. inset: a preview box in Settings (scaled down: no sprite filter).
        preview: the full-screen Preview (a saver can then answer LB / RB: rare()).
        Shuffle: the host shows one saver of the marked set and changes it
        each Change Every minutes; self.setting is "shuffle", self.type the one shown."""
        self.app, self.clock, self.rng, self.ms = app, clock, rng, ms
        self.inset = inset
        self.preview = preview
        self.music_ok = music
        st = app.settings
        kind = kind or st["screensaver_type"]
        self.setting = kind if kind in SAVERS else "bounce"
        sf = st["safe"]
        self.area = pygame.Rect(sf["left"], sf["top"], 640 - sf["left"] - sf["right"],
                                480 - sf["top"] - sf["bottom"])
        self.gen = app.src_gen
        self.t = 0.0
        self.name_surf = self._name_surface()
        self.msprites = Sprites(cap=4)
        self.mbounce = None                    # the album art
        self.drawn = None                      # the mode of the last update
        self.impl = None
        if self.setting == "shuffle":
            self.every_s = 60.0 * saveropts.get(st, "shuffle", "every")
            self.pool = saveropts.shuffle_set(st)
            self._begin(self.rng.choice(self.pool))
        else:
            self._begin(self.setting)

    def _begin(self, kind):
        """Show the saver of one type (at the start, and at each Shuffle change)."""
        self.close()
        st = self.app.settings
        self.type = kind
        self.opts = saveropts.all_of(st, kind)
        # Visualizer: the app opens it at the start when its engine runs; here it is the
        # album art (the engine is absent, or the music started later).
        self.music_on = self.music_ok and self.opts.get("music", "keep") != "keep"
        self.items = []
        self.k = 0                             # Entertainment: index of the item shown
        self.shown_at = self.clock()
        self.begun_at = self.shown_at
        self.loaded_at = None
        self.cur = None                        # Entertainment: the image shown
        self.old = None                        # the one that fades out
        self.fade = None                       # Entertainment: frame-clock ms of the fade start
        self.bounce = Bounce(self.area, self.name_surf.get_size(), self.rng)
        self.mode = "name"                     # name until art is in
        self.impl = None
        self._shown = None
        if kind == "bounce":
            from .savers.bounce import BounceSaver
            self.impl = BounceSaver(self, self.opts)
        elif kind == "rain":
            self.impl = RainSaver(self, self.opts)
        elif kind in saveropts.MODULES:
            import importlib
            mod = importlib.import_module(".savers." + saveropts.MODULES[kind], __package__)
            self.impl = mod.Saver(self, self.opts)
        if self.impl is not None:
            self.mode = kind
            if getattr(self, "_light", False) and hasattr(self.impl, "lighten"):
                self.impl.lighten()              # a weak box stays light after a change
        if self.needs_items():
            self.load()

    def _shuffle(self, now):
        """Shuffle: the next saver (never the same one twice in a row). True when changed."""
        if self.setting != "shuffle" or now - self.begun_at < self.every_s:
            return False
        others = [k for k in self.pool if k != self.type] or self.pool
        self._begin(self.rng.choice(others))
        self.blank_until = now + BLANK_S      # black between two savers
        self.drawn = None                      # a full pass
        L.info("screensaver shuffle: %s", self.type)
        return True

    def filter_mode(self):
        return "off" if self.inset else self.app.settings["flicker_filter"]

    _filter_mode = filter_mode

    def needs_items(self):
        return self.type == "art" or (self.type == "bounce" and self.opts["art"] == "posters")

    @property
    def rain(self):
        return self.impl.rain if isinstance(self.impl, RainSaver) else None

    @property
    def prefiltered(self):
        """True when the frame is already filtered (sprites and the rain atlas are filtered
        one time); the app then skips its frame filter. Entertainment takes the filter."""
        return self.mode == "music" or (self.impl is not None and self.mode == self.type
                                        and self.impl.prefiltered)

    # ------------------------------------------------------------ data
    def _name_surface(self):
        app = self.app
        home = getattr(getattr(app, "sources", None), "home", None)
        name = (getattr(home, "server_name", "") or app.secrets.get("server_name") or
                "Batoplex")
        w = min(NAME_MAX_W, draw.width(name, "title"))
        surf = pygame.Surface((w, 40))
        surf.fill(BLACK)
        draw.text(surf, name, "title", C["text2"], 0, 0, w=w)
        return surf

    def load(self):
        """Items from the pinned libraries, on a worker; the reply lands through app.post."""
        app, gen, kind = self.app, self.gen, self.type
        src = getattr(app, "sources", None)
        if src is None:
            return
        self.loaded_at = self.clock()

        def work():
            try:
                items = src.saver_items(app.settings)
            except Exception as e:                  # not fatal: the name shows
                L.info("screensaver items failed: %s", e)
                items = []
            app.post(lambda: self._loaded(gen, items, kind))
        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, gen, items, kind=None):
        if not self.alive() or gen != self.app.src_gen or (kind and kind != self.type):
            return
        if self.impl is not None:
            items = [i for i in items if getattr(i, "poster", None)]
            self.rng.shuffle(items)
            self.impl.set_items(items)
            L.info("screensaver: %d items", len(items))
            return
        items = [i for i in items if getattr(i, "art", None) or i.poster]
        if not items and self.items:
            return                             # a failed reload keeps what shows
        self.rng.shuffle(items)
        self.items = items
        self.k = 0
        self.cur = self.old = None             # Entertainment: title and picture stay paired
        self.fade = None
        if self.mode != "name":
            self.mode = "name"                 # back to the name until the new art is in
            self.bounce.resize(self.name_surf.get_size())
        L.info("screensaver: %d items", len(items))

    LIGHT_MS = 10.0                 # A mean field above this lightens the saver
    LIGHT_N = 120

    def note_field(self, ms):
        """The app's cost of a drawn field. After LIGHT_N fields with a mean above LIGHT_MS
        (a weak box), the saver lowers its work once (lighten()); a capable box never gets
        there, so it keeps full quality."""
        if getattr(self, "_light", False) or self.inset:
            return
        n, tot = getattr(self, "_cost", (0, 0.0))
        n, tot = n + 1, tot + ms
        self._cost = (n, tot)
        if n >= self.LIGHT_N:
            self._cost = (0, 0.0)
            if tot / n > self.LIGHT_MS:
                self._light = True
                fn = getattr(self.impl, "lighten", None)
                if fn is not None:
                    fn()
                L.info("screensaver: mean field %.1f ms, lighter mode", tot / n)

    def rare(self, step):
        """Preview: LB (-1) or RB (1) shows the previous or next rare moment of the saver
        shown. False when it has none (the press then wakes it)."""
        fn = getattr(self.impl, "rare", None) if self.mode == self.type else None
        return bool(fn and fn(step))

    def close(self):
        """The saver ends (a wake, playback, a sign-out)."""
        fn = getattr(self.impl, "close", None)
        if fn is not None:
            fn()

    def alive(self):
        """True while this saver shows (the app's saver, or a preview that holds it)."""
        return self.app.saver is self or getattr(self, "held", False)

    def sources_changed(self):
        if self.needs_items():
            self.load()

    def _image(self, k, request=True):
        cache = self.app.images
        if cache is None or not self.items:
            return None
        it = self.items[k % len(self.items)]
        return cache.get(it.art or it.poster, ART[0], ART[1], tokens.GAIN["backdrop_detail"],
                         request=request)

    # ------------------------------------------------------------ update
    def update(self):
        """Once per field. True when the frame changed. band() then says where."""
        prev = getattr(self, "_shown", None)        # the name box last drawn
        switched = self.drawn != self.mode
        changed = self._update()
        if getattr(self, "blank_until", None) is not None:
            self._band = None                      # Shuffle's black gap: one full black pass,
            return changed                         # then nothing until the next saver
        if self.prefiltered:
            self._shown, self._band = None, None    # a copy of the whole frame, no filter
            return changed
        if self.impl is not None and self.mode == self.type:     # a filtered saver (Weather)
            self._shown = None
            self._band = None if switched else self.impl.band()
            return changed or switched
        b = self.bounce
        look = (self.mode, b.w, b.h) if self.mode == "name" else None
        box = pygame.Rect(b.x, b.y, b.w, b.h)
        same = prev is not None and look is not None and prev[0] == look
        if not changed and (look is not None or prev is not None and prev[0] is not None) \
                and not (same and prev[1] == box):
            # A mode change on a field the box did not move (music ends): the screen still
            # shows the old look, so redraw now.
            changed = True
        self._band = prev[1].union(box) if changed and same else None   # old and new place
        if changed or prev is None:
            self._shown = (look, box)
        return changed

    def band(self):
        """After update() returned True: the rectangle of every change (the name at its old
        and new place); None: a full pass."""
        return getattr(self, "_band", None)

    def _update(self):
        now = self.clock()
        self.t = t = self.ms()
        if self._shuffle(now):
            return True
        blank = getattr(self, "blank_until", None)
        if blank is not None and now < blank:
            return False
        if blank is not None:
            self.blank_until = None
            self.drawn = None                  # the first field of the new saver: full
        was, self.drawn = self.drawn, self.mode
        if self.music_on and self._update_music():
            self.drawn = self.mode
            return self.mbounce.step(t) or was != self.mode
        self.drawn = self.mode
        if self.loaded_at is not None and now - self.loaded_at >= RELOAD_S:
            self.load()
        if self.impl is not None:
            return self.impl.update(t, now) or was != self.mode
        return self._update_art(now)

    def _update_music(self):
        """While a track plays, its album art bounces whatever the type is. True in that mode."""
        fn = getattr(self.app, "now_playing_art", None)
        ref = fn() if fn else None
        img = None
        if ref and self.app.images is not None:
            img = self.app.images.get(ref, MUSIC[0], MUSIC[1], tokens.GAIN["unfocused"])
        if img is not None:
            if self.mode != "music":
                start = self.impl.first_box() if self.impl is not None else None
                b = self.mbounce = Bounce(self.area, MUSIC, self.rng)
                b.x, b.y = start or (self.bounce.x, self.bounce.y)
                b.resize(MUSIC)                 # the box keeps its place: no jump
                self.mode = "music"
            self.music_ref = ref
            return True
        if self.mode == "music" and not ref:
            self.mode, self.cur, self.old, self.fade = "name", None, None, None
            if self.impl is not None:
                self.mode = self.type
                return False
            self.bounce.resize(self.name_surf.get_size())
        return self.mode == "music"             # a new track's art is still loading

    def _update_art(self, now):
        if not self.items:
            self.mode = "name"
            return self.bounce.step(self.t)
        changed = False
        if self.cur is None:
            img = self._image(self.k)
            if img is None:
                return self.bounce.step(self.t) if self.mode == "name" else False
            self.mode, self.cur, self.shown_at, changed = "art", img, now, True
        self._image(self.k + 1)                   # prefetch
        if now - self.shown_at >= CHANGE_S:
            nxt = self._image(self.k + 1)
            if nxt is not None:
                self.old, self.cur = (self.k, self.cur), nxt
                self.k += 1
                # the first frame already shows progress (anim.py timing rule)
                self.shown_at, self.fade, changed = now, self.t - tokens.FIELD_MS, True
        if self.fade is not None:
            if self.t - self.fade >= FADE_MS - 0.5:
                self.fade, self.old = None, None      # the last fade frame: the new art only
            changed = True
        return changed

    # ------------------------------------------------------------ draw
    def draw(self, s):
        if getattr(self, "blank_until", None) is not None:
            s.fill(BLACK)
            return
        if self.mode == "music":
            s.fill(BLACK)
            self.msprites.set_mode(self.filter_mode())
            spr = self.msprites.get(self.music_ref, lambda: self.app.images.get(
                self.music_ref, MUSIC[0], MUSIC[1], tokens.GAIN["unfocused"], request=False))
            if spr is not None:
                s.blit(spr, (self.mbounce.x, self.mbounce.y - PAD))
            return
        if self.impl is not None and self.mode == self.type:
            self.impl.draw(s)
            return
        s.fill(BLACK)
        if self.mode == "name":
            s.blit(self.name_surf, (self.bounce.x, self.bounce.y))
        else:
            self._draw_art(s)

    def _art_frame(self, s, k, img):
        it = self.items[k % len(self.items)]
        below = k % 2 == 0                       # the text band changes side with each item
        iy = 0 if below else 480 - ART[1]
        s.blit(img, (0, iy))
        y = text_y(self.area, ART[1], 480) if below else text_y(self.area, 0, 480 - ART[1])
        x0, x1 = self.area.x, self.area.right
        cw = draw.text(s, lineup.clock_hm(), "title", C["text2"], x0, y)
        title = it.show_title if it.type == "episode" and it.show_title else it.title
        line = " • ".join(p for p in (title, str(it.year) if it.year else "") if p)
        draw.text(s, line, "label", C["dim"], x1, y + 8, w=x1 - x0 - cw - 24, align="right")

    def _draw_art(self, s):
        if self.cur is None:
            return
        self._art_frame(s, self.k, self.cur)
        if self.fade is not None and self.old is not None:
            k, img = self.old
            layer = getattr(self, "_layer", None)       # opaque: text writes no stale alpha
            if layer is None:
                from .draw import opaque_surface
                layer = self._layer = opaque_surface((641, 480))
            layer.fill(BLACK)
            self._art_frame(layer, k, img)
            left = 1.0 - (self.t - self.fade) / FADE_MS
            layer.set_alpha(max(0, min(255, int(255 * left))))
            s.blit(layer, (0, 0))
