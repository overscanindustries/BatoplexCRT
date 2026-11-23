"""Album: art, title, artist, meta and Play / Shuffle on the left; the
track list on the right. The list steps one row in one field (the Episodes rule)."""
import pygame

from .. import draw, inputs, tokens
from ..model import Item
from ..overlay import fmt_time
from ..tokens import C
from . import Screen

ROW_H, PITCH = tokens.ROW_COMPACT_H, tokens.ROW_COMPACT_PITCH
BTN_H = tokens.CTRL_H


def album_of(t):
    """An album Item for a track (its album id, title and artist)."""
    return Item(id=t.album_id or "", type="album", title=t.album, artist=t.extra.get(
        "album_artist") or t.artist, artist_id=t.artist_id, poster=t.poster,
        server_id=t.server_id, section_id=t.section_id, year=t.year)


class AlbumScreen(Screen):
    ambient = True                # the idle ribbon may show here
    name = "ALBUM"
    error_text = "Can't load this album"

    def __init__(self, app, album, follow=None, library=""):
        super().__init__(app)
        self.album = album
        self.library = library
        self.tracks = []
        self.zone = "buttons"           # buttons | list
        self.btn = 0                    # 0 Play, 1 Shuffle
        self.focus = 0                  # index into self.tracks
        self.top = 0                    # first row shown (index into self.rows)
        self.follow = follow            # a track id to focus once loaded
        self.loading, self.error = False, None
        self.dirty = True
        self._v = None
        self.load()

    # ------------------------------------------------------------ data
    def load(self):
        if self.loading:
            return
        self.loading, self.error, self.dirty = True, None, True
        src = self.app.sources

        def gone():
            return self not in self.app.stack

        def done(tracks):
            if gone():
                return
            self.loading, self.tracks, self.dirty = False, tracks, True
            if self.follow:
                i = next((k for k, t in enumerate(tracks) if t.id == self.follow), None)
                if i is not None:
                    self.zone, self.focus = "list", i
                self.follow = None
            self._clamp()

        def fail(e):
            if gone():
                return
            self.loading, self.error, self.dirty = False, str(e), True
        self.run_bg(lambda: src.tracks(self.album), done, fail)

    @property
    def rows(self):
        """[("disc", n) | ("track", i)]: a disc line only when there is more than one disc."""
        discs = {t.disc for t in self.tracks if t.disc is not None}
        out, last = [], object()
        for i, t in enumerate(self.tracks):
            if len(discs) > 1 and t.disc != last:
                out.append(("disc", t.disc))
                last = t.disc
            out.append(("track", i))
        return out

    # ------------------------------------------------------------ geometry
    def geo(self):
        sf = self.safe
        x0, x1, T, y1 = sf["left"], 640 - sf["right"], sf["top"], 480 - sf["bottom"]
        wide = x1 - x0 >= 548
        col = 176 if wide else 144
        gap = 20 if wide else 16
        first = T + 24 + (20 if wide else 16)
        n = max(1, (y1 - 8 - first + (PITCH - ROW_H)) // PITCH)
        top = y1 - 8 - (n * PITCH - (PITCH - ROW_H))
        lx = x0 + col + gap
        bw = col - tokens.BTN_GAP - tokens.ICON_BTN_W     # Play; Shuffle is an icon button
        btn_y = y1 - 8 - BTN_H
        # The text under the art must end 8 px above the buttons. With tall margins it does not
        # fit under a full-width square: the art gets smaller, then the title takes one line.
        lines = self._title_lines(col, 2)
        sub = 4 + self.SUB_LINES * 24
        art = col
        room = btn_y - 8 - top - 12 - sub
        if art + 28 * len(lines) > room:
            art = (room - 28 * len(lines)) // 4 * 4
            if art < 64 and len(lines) > 1:
                lines = self._title_lines(col, 1)
                art = (room - 28) // 4 * 4
            art = max(32, min(col, art))
        return dict(x0=x0, x1=x1, T=T, y1=y1, col=col, n=n, list_top=top, lx=lx, lw=x1 - lx,
                    art=pygame.Rect(x0, top, art, art), art_size=art, title_y=top + art + 12,
                    title_lines=lines, btn_y=btn_y, bw=bw)

    SUB_LINES = 2                       # small lines under the title (artist, meta)

    def _title_lines(self, w, n):
        key = (self.album.title, w, n)
        if getattr(self, "_tl_key", None) != key:
            self._tl_key, self._tl = key, draw.wrap(self.album.title, "heading", w, n)
        return self._tl

    def sub_lines(self):
        """[(text, color)]: the small lines under the title."""
        a = self.album
        out = [(a.artist, C["text2"])]
        if self.tracks:
            ms = sum(t.duration_ms or 0 for t in self.tracks)
            mins = max(1, int(round(ms / 60000.0)))
            dur = "%dh %02dm" % (mins // 60, mins % 60) if mins >= 60 else "%dm" % mins
            n = len(self.tracks)
            out.append((" • ".join(p for p in (str(a.year) if a.year else "",
                                               "%d track%s" % (n, "" if n == 1 else "s"),
                                               dur if ms else "") if p), C["dim"]))
        return out

    def buttons_ready(self):
        return bool(self.tracks)

    def btn_rect(self, i, g=None):
        """Play (primary, the column less the icon button), then the Shuffle icon button."""
        g = g or self.geo()
        if i == 0:
            return pygame.Rect(g["x0"], g["btn_y"], g["bw"], BTN_H)
        return pygame.Rect(g["x0"] + g["bw"] + tokens.BTN_GAP, g["btn_y"], tokens.ICON_BTN_W,
                           BTN_H)

    def _clamp(self):
        rows = self.rows
        if not rows:
            return
        self.focus = max(0, min(self.focus, len(self.tracks) - 1))
        r = next(k for k, x in enumerate(rows) if x == ("track", self.focus))
        n = self.geo()["n"]
        if r < self.top + 3:
            self.top = r - 3
        elif r > self.top + n - 4:
            self.top = r - n + 4
        self.top = max(0, min(self.top, max(0, len(rows) - n)))

    # ------------------------------------------------------------ menu
    @property
    def menu_title(self):
        t = self._focused_track()
        return t.title if t is not None else self.album.title

    def _focused_track(self):
        if self.zone == "list" and 0 <= self.focus < len(self.tracks):
            return self.tracks[self.focus]
        return None

    def menu_items(self):
        m = self.app.music
        t = self._focused_track()
        items = [t] if t is not None else list(self.tracks)
        rows = []
        if items and m.active and not m.live:
            sess = m.session_id

            def put(fn):
                if self.app.music_accepts(items, sess):
                    fn(items)
            rows += [("Play Next", lambda: put(m.play_next)), ("Add to Queue", lambda: put(m.add))]
        if self.album.artist_id and hasattr(self.app, "open_artist_of"):
            rows.append(("Go to Artist", lambda: self.app.open_artist_of(self.album)))
        from .actions import item_rows
        return rows + item_rows(self, t if t is not None else self.album)

    # ------------------------------------------------------------ input
    def handle(self, a):
        self.dirty = True
        if a == inputs.BACK:
            self.app.back()                          # the Play Queue from the side panel
            return
        if self.error and a == inputs.OK:
            self.load()
            return
        if not self.tracks:
            return
        if self.zone == "buttons":
            if a == inputs.LEFT:
                self.btn = 0
            elif a == inputs.RIGHT:
                if self.btn == 0:
                    self.btn = 1
                else:
                    self.zone = "list"
            elif a == inputs.OK:
                self.app.play_music(self.tracks, 0, shuffle=self.btn == 1)
        else:
            if a == inputs.LEFT:
                self.zone, self.btn = "buttons", 0
            elif a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
                d = {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -5, inputs.RT: 5}[a]
                self.focus = max(0, min(len(self.tracks) - 1, self.focus + d))
                self._clamp()
            elif a == inputs.OK:
                self.app.play_music(self.tracks, self.focus)

    # ------------------------------------------------------------ update
    def update(self):
        m = self.app.music
        d = self.dirty or m.version != self._v
        self._v, self.dirty = m.version, False
        if self.loading:
            d = True
        if self.app.images is not None:
            g = self.geo()
            self.app.images.prefetch(self.album.poster, g["art_size"], g["art_size"],
                                     tokens.GAIN["art"])
        return d

    def on_resume(self, result=None):
        self.dirty = True

    # ------------------------------------------------------------ draw
    def draw(self, s):
        draw.background(s, self)
        g = self.geo()
        a = self.album
        label = " • ".join(p for p in (self.library, a.artist or "") if p)
        self.topbar(s, label or "Album")
        x0 = g["x0"]
        art = g["art"]
        img = self.app.images.get(a.poster, g["art_size"], g["art_size"], tokens.GAIN["art"]) \
            if self.app.images is not None else None
        draw.art(s, a, art, img, lines=2)
        y = g["title_y"]
        for ln in g["title_lines"]:
            draw.text(s, ln, "heading", C["text"], x0, y, w=g["col"])
            y += 28
        y += 4
        for text, col in self.sub_lines()[:self.SUB_LINES]:
            if text:
                draw.text(s, text, "small", col, x0, y, w=g["col"])
            y += 24
        ready = self.buttons_ready()
        for i in range(2):
            foc = self.zone == "buttons" and self.btn == i and ready
            if i == 0:
                draw.button(s, self.btn_rect(0, g), "Play", kind="primary", focused=foc,
                            enabled=ready)
            else:
                draw.button(s, self.btn_rect(1, g), icon=_shuffle_icon, focused=foc,
                            enabled=ready)
        self._draw_list(s, g)

    def _draw_list(self, s, g):
        lx, lw, top = g["lx"], g["lw"], g["list_top"]
        if self.late(self.loading and not self.tracks):
            draw.state(s, lx, top, lw, "Loading…")
        if self.loading and not self.tracks:
            return
        if self.error and not self.tracks:
            draw.state(s, lx, top, lw, self.error_text, "Press A to try again.")
            return
        rows = self.rows
        n = g["n"]
        if len(rows) > n:                            # the scroll bar takes 12 px
            draw.scrollbar(s, lx + lw - tokens.SCROLLBAR_W, top, n * PITCH - (PITCH - ROW_H),
                           self.top, n, len(rows))
            g = dict(g, lw=lw - 12)
        for k in range(n):
            r = self.top + k
            if r >= len(rows):
                break
            kind, v = rows[r]
            y = top + k * PITCH
            if kind == "disc":
                draw.heading_row(s, lx, y, g["lw"], "Disc %s" % v)
                continue
            self._row(s, g, v, y, self.zone == "list" and v == self.focus)

    def _row(self, s, g, v, y, foc):
        m = self.app.music
        now = m.track if m.active else None
        t = self.tracks[v]
        playing = now is not None and now.id == t.id and now.server_id == t.server_id
        dur = fmt_time(t.duration_ms / 1000) if t.duration_ms else None
        second = t.artist if t.artist and t.artist != self.album.artist else None
        draw.row_compact(s, (g["lx"], y, g["lw"]), t.index, t.title, second=second, right=dur,
                         focused=foc, playing=playing)


def _shuffle_icon(surf, x, y, col):
    from ..overlay import icon
    icon(surf, "shuffle", x, y, col)
