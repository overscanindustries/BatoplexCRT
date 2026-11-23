"""The visualizer screen. The engine window covers this screen; the app keeps
the pad. Left/Right: preset. LT/RT: track. A: play/pause. B or Y: back to Now Playing.
Started by the screensaver, any press closes it and the press is dropped (app._idle_tick)."""
import os

import pygame

from .. import config, draw, inputs
from ..tokens import C
from . import Screen

TITLE_S = 4.0
TITLE_H = 64
BAND_A = 150


class VisualizerScreen(Screen):
    name = "VISUALIZER"
    no_saver = True

    def __init__(self, app, saver=False):
        super().__init__(app)
        self.saver_mode = saver
        self._key = None
        self._shown = False
        self._started = False

    @property
    def vis(self):
        return self.app.visualizer

    def on_enter(self):
        self._started = self.vis.start()
        if not self._started:
            self.app.post(self.close)

    def close(self):
        self.vis.stop()
        try:
            os.remove(self.vis.overlay_path)
        except OSError:
            pass
        if self.app.top is self:
            self.app.pop()

    def handle(self, a):
        m = self.app.music
        if a in (inputs.LEFT, inputs.RIGHT):
            self.vis.send("prev" if a == inputs.LEFT else "next")
        elif a == inputs.LT:
            m.prev()
        elif a == inputs.RT:
            m.next()
        elif a == inputs.OK:
            m.toggle()
        elif a in (inputs.BACK, inputs.Y):
            self.close()
            return
        self.show_title()

    def track_key(self):
        m = self.app.music
        t = m.track
        return (m.index, t.id if t is not None else None, m.icy if m.live else None)

    def update(self):
        m = self.app.music
        if not m.active:
            if self.saver_mode:                 # the art screensaver takes over at once
                d = self.app.saver_delay_s()
                if d is not None:
                    self.app.idle_t -= d
            self.close()
            return False
        if self._started and not self.vis.active:
            self.close()                        # the engine stopped (B in its window, crash)
            return False
        running = self.vis.state == "running"
        if running:
            k = self.track_key()
            if k != self._key or not self._shown:     # a new track, or a new engine window
                self._key, self._shown = k, True
                self.show_title()
        else:
            self._shown = False
        return self.vis.state == "starting"         # draw the title until the engine covers it

    def draw(self, s):
        s.fill((0, 0, 0))
        if self.vis.state == "starting":        # until the engine window covers this screen
            title, line = self.lines()
            sf = self.safe
            x, w = sf["left"], 640 - sf["left"] - sf["right"]
            draw.text(s, title, "heading", C["text"], x, 206, w=w, align="center")
            draw.text(s, line, "label", C["text2"], x, 238, w=w, align="center")

    # ------------------------------------------------------------ title
    def lines(self):
        m = self.app.music
        t = m.track
        if t is None:
            return "", ""
        if m.live:
            from ..radio import split_title
            song, artist = split_title(m.icy)
            return (song, " • ".join(p for p in (artist, t.title) if p)) if song else (t.title, "")
        return t.title or "", " • ".join(p for p in (t.artist, t.album) if p)

    def title_image(self):
        """(RGBA bytes, w, h, x, y): the title and artist over a soft dark band, full width.
        Premultiplied alpha (the text is drawn on black), with the [1 2 1] vertical filter."""
        sf = self.safe
        w, fade = 640, 40
        h = draw.even(fade + TITLE_H + sf["bottom"])        # the band goes to the bottom edge
        title, line = self.lines()
        rgb = pygame.Surface((w, h))
        x0, tw = sf["left"], 640 - sf["left"] - sf["right"]
        if title:
            draw.text(rgb, title, "heading", C["text"], x0, fade + 4, w=tw, align="center")
        if line:
            draw.text(rgb, line, "label", C["text2"], x0, fade + 34, w=tw, align="center")
        rgb = draw.FlickerFilter((w, h)).apply(rgb, self.app.settings["flicker_filter"])
        alpha = pygame.Surface((w, h))
        for y in range(h):
            v = BAND_A * min(1.0, y / fade)
            alpha.fill((int(v),) * 3, (0, y, w, 1))
        alpha.blit(pygame.transform.grayscale(rgb), (0, 0), special_flags=pygame.BLEND_RGB_MAX)
        tob = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
        c, a = tob(rgb, "RGB"), tob(alpha, "RGB")
        out = bytearray(w * h * 4)
        out[0::4], out[1::4], out[2::4], out[3::4] = c[0::3], c[1::3], c[2::3], a[0::3]
        return bytes(out), w, h, 0, 480 - h

    def show_title(self):
        if self.vis.state != "running" or self.vis.engine != "projectm":
            return
        try:
            data, w, h, x, y = self.title_image()
            path = self.vis.overlay_path
            # /tmp is shared: mkstemp makes a new 0600 file (O_EXCL, never a planted link).
            config.atomic_write(path, data, mode=0o600, fsync=False)
        except (OSError, pygame.error):
            return
        self.vis.overlay(path, w, h, x, y, TITLE_S)
