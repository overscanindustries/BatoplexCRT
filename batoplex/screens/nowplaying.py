"""Now Playing: centred art, title, "artist • album", a progress line and
one row of buttons. The chain is anchored to the safe bottom; the art takes the room left
under the top bar (208 px at most). B closes the screen and the music goes on.

The queue view: the same screen, opened with Down on the buttons or
the Up Next button. A compact header (art, title, caption, progress) over the queue in
compact rows. B, or Up on the first row, goes back to the player view."""
import time

import pygame

from .. import draw, inputs, tokens
from ..overlay import fmt_time, icon
from ..tokens import C
from . import Screen

BTN_W, BTN_H, BTN_GAP = 48, 40, 8
BUTTONS = ("shuffle", "prev", "playpause", "next", "repeat", "queue", "visualizer")
LIVE_BUTTONS = ("prev", "playpause", "next", "stations", "visualizer")   # radio
ART_MAX = 208
SEEK_S = 10
Q_ART = 64                        # queue view: the header art
PITCH, ROW_H = tokens.ROW_COMPACT_PITCH, tokens.ROW_COMPACT_H


def resolve(music, session, item, hint):
    """The index of a queue row now. A menu captured the session, the
    row's Item and its index then; playback can move on while the menu is open. None when
    the session changed or the entry left the queue. The same Item twice: the one nearest
    to the old index."""
    if not music.active or music.session_id != session:
        return None
    hits = [i for i, t in enumerate(music.items) if t is item]
    return min(hits, key=lambda i: abs(i - hint)) if hits else None


class NowPlayingScreen(Screen):
    ambient = True                # the idle ribbon may show here
    name = "NOW PLAYING"
    is_now_playing = True

    def __init__(self, app):
        super().__init__(app)
        self.row = 1                  # 0 = progress bar, 1 = buttons
        self.btn = self.buttons.index("playpause")
        self.dirty = True
        self._sec = None
        self._v = None
        self.view = "player"          # or "queue"
        self.qfocus = 0               # queue view: the focused row
        self.qtop = 0                 # queue view: the first row shown
        self._qitem = None            # the focused Item (a shuffle keeps it)
        self._qcur = None             # the current Item when the focus was last placed

    @property
    def music(self):
        return self.app.music

    @property
    def buttons(self):
        return LIVE_BUTTONS if self.app.music.live else BUTTONS

    # ------------------------------------------------------------ geometry
    def geo(self):
        sf = self.safe
        x0, x1, T, y1 = sf["left"], 640 - sf["right"], sf["top"], 480 - sf["bottom"]
        btn_y = y1 - 8 - BTN_H
        prog_y = btn_y - 12 - 36                  # draw.seekbar: the bar, the times 12 below
        artist_y = prog_y - 20 - 24               # 12 px over the focused knob (8 px up)
        title_y = artist_y - 4 - 28
        art_bottom = title_y - 12
        size = max(0, min(ART_MAX, art_bottom - (T + 36))) // 4 * 4
        nb = len(self.buttons)
        total = nb * BTN_W + (nb - 1) * BTN_GAP
        return dict(x0=x0, x1=x1, T=T, y1=y1, btn_y=btn_y, btn_x=320 - total // 2,
                    prog_y=prog_y, artist_y=artist_y, title_y=title_y, art=size,
                    art_x=320 - size // 2, art_y=art_bottom - size,
                    bar_x0=320 - total // 2, bar_x1=320 - total // 2 + total)

    def qgeo(self):
        """Queue view: the header under the top bar (art 64; title, caption and the progress
        bar beside it, the bar on the art's bottom line), the list 16 px under it, ending
        8 px above the safe bottom."""
        sf = self.safe
        x0, x1, T, y1 = sf["left"], 640 - sf["right"], sf["top"], 480 - sf["bottom"]
        hy = T + 36
        top = hy + Q_ART + 16
        n = max(1, (y1 - 8 - top + (PITCH - ROW_H)) // PITCH)
        return dict(x0=x0, x1=x1, T=T, hy=hy, tx=x0 + Q_ART + 16, top=top, n=n)

    def btn_rect(self, i, g=None):
        g = g or self.geo()
        return pygame.Rect(g["btn_x"] + i * (BTN_W + BTN_GAP), g["btn_y"], BTN_W, BTN_H)

    # ------------------------------------------------------------ input
    def menu_items(self):
        if self.view == "queue":
            return self._queue_menu()
        t = self.music.track
        rows = []
        sleep = getattr(self.app, "sleep", None)
        if sleep is not None:
            rows.append(("Sleep Timer: %s" % sleep.label("End of Track"), self.app.open_sleep_menu))
        if self.music.live:
            return rows + [("Stop", self.music.stop)]
        if t is not None and t.album_id and hasattr(self.app, "open_album_of"):
            rows.append(("Go to Album", lambda: self.app.open_album_of(t)))
        if t is not None and t.artist_id and hasattr(self.app, "open_artist_of"):
            rows.append(("Go to Artist", lambda: self.app.open_artist_of(t)))
        if t is not None:
            from .actions import item_rows             # add to Playlist
            rows += item_rows(self, t)
        rows.append(("Stop", self.music.stop))
        return rows

    def _queue_menu(self):
        """The focused row: Remove from Queue (not the current track), Go
        to Album, Go to Artist, Add to Playlist. Remove finds the row again when it runs."""
        m = self.music
        if not 0 <= self.qfocus < len(m.items):
            return []
        t, i, sess = m.items[self.qfocus], self.qfocus, m.session_id
        rows = []
        if i != m.index:
            rows.append(("Remove from Queue", lambda: self.remove_row(sess, t, i)))
        if t.album_id and hasattr(self.app, "open_album_of"):
            rows.append(("Go to Album", lambda: self.app.open_album_of(t)))
        if t.artist_id and hasattr(self.app, "open_artist_of"):
            rows.append(("Go to Artist", lambda: self.app.open_artist_of(t)))
        from .actions import item_rows                 # Add to Playlist
        return rows + item_rows(self, t)

    def remove_row(self, session, item, hint):
        m = self.music
        i = resolve(m, session, item, hint)
        if i is None or i == m.index:                  # it left, or it plays now
            return False
        m.remove(i)
        return True

    @property
    def menu_title(self):
        if self.view == "queue":
            items = self.music.items
            return items[self.qfocus].title if 0 <= self.qfocus < len(items) else ""
        t = self.music.track
        return t.title if t is not None else ""

    # ------------------------------------------------------------ queue view
    def set_view(self, v):
        if v == self.view or (v == "queue" and self.music.live):
            return
        snap = getattr(self.app, "_snapshot", None)
        if snap is not None:
            snap()                                     # the standard crossfade
        self.view = v
        if v == "queue":
            m = self.music
            self.qfocus, self._qitem, self._qcur = m.index, m.track, m.track
            self.qtop = 0
            self._qclamp()
        self.dirty = True

    def _qsync(self):
        """After a queue change: the focus stays on its Item (a shuffle, a removal); on the
        current row it follows a track change."""
        m = self.music
        cur, fi = m.track, self._qitem
        if fi is not None and fi is self._qcur and cur is not self._qcur:
            fi = cur
        hits = [i for i, t in enumerate(m.items) if t is fi] if fi is not None else []
        if hits:
            self.qfocus = min(hits, key=lambda i: abs(i - self.qfocus))
        self._qcur = cur
        self._qclamp()

    def _qclamp(self):
        m = self.music
        n = self.qgeo()["n"]
        self.qfocus = max(0, min(self.qfocus, len(m.items) - 1))
        if self.qfocus < self.qtop + 2:
            self.qtop = self.qfocus - 2
        elif self.qfocus > self.qtop + n - 3:
            self.qtop = self.qfocus - n + 3
        self.qtop = max(0, min(self.qtop, max(0, len(m.items) - n)))
        self._qitem = m.items[self.qfocus] if m.items else None

    def _handle_queue(self, a):
        m = self.music
        if a == inputs.BACK or (a == inputs.UP and self.qfocus == 0):
            self.set_view("player")
        elif a in (inputs.UP, inputs.DOWN):
            self.qfocus += -1 if a == inputs.UP else 1
            self._qclamp()
        elif a == inputs.OK:
            if self.qfocus == m.index:
                m.toggle()
            else:
                m.jump(self.qfocus)
        elif a == inputs.LT:
            m.prev()
        elif a == inputs.RT:
            m.next()
        elif a == inputs.Y:
            self.app.open_visualizer()

    def handle(self, a):
        self.dirty = True
        m = self.music
        if self.view == "queue":
            self._handle_queue(a)
            return
        if a == inputs.BACK:
            self.app.back()                          # the queue view went back above
        elif a == inputs.Y:
            self.app.open_visualizer()
        elif a == inputs.LT:
            m.prev()
        elif a == inputs.RT:
            m.next()
        elif a == inputs.DOWN and self.row == 1 and not m.live:
            self.set_view("queue")
        elif a in (inputs.UP, inputs.DOWN):
            self.row = 0 if a == inputs.UP and not m.live else 1     # radio: no seek line
        elif a in (inputs.LEFT, inputs.RIGHT):
            d = -1 if a == inputs.LEFT else 1
            if self.row == 0:
                m.seek(d * SEEK_S)
            else:
                self.btn = max(0, min(len(self.buttons) - 1, self.btn + d))
        elif a == inputs.OK and self.row == 1:
            self.press(self.buttons[min(self.btn, len(self.buttons) - 1)])
        elif a == inputs.OK and self.row == 0:
            m.toggle()

    def press(self, b):
        m = self.music
        if b == "playpause":
            m.toggle()
        elif b == "prev":
            m.prev()
        elif b == "next":
            m.next()
        elif b == "shuffle":
            m.set_shuffle(not m.shuffle)
            self.app.toast("Shuffle On" if m.shuffle else "Shuffle Off")
        elif b == "repeat":
            r = m.cycle_repeat()
            self.app.toast({"off": "Repeat Off", "all": "Repeat All", "one": "Repeat One"}[r])
        elif b == "queue":
            self.set_view("queue")
        elif b == "stations":
            from .radio import RadioScreen
            self.app.push(RadioScreen(self.app))
        elif b == "visualizer":
            self.app.open_visualizer()

    # ------------------------------------------------------------ update
    def update(self):
        if not self.music.active:
            if self.app.top is self:
                self.app.pop()              # the session ended (queue end, Stop, error)
            return False
        self.btn = min(self.btn, len(self.buttons) - 1)   # radio and music differ in buttons
        if self.music.live:
            self.row = 1
            self.view = "player"                     # radio has no queue view
        d = self.dirty or self.music.version != self._v
        if self.view == "queue" and self.music.version != self._v:
            self._qsync()
        sec = int(time.time())
        if sec != self._sec:
            self._sec, d = sec, True
        self._v, self.dirty = self.music.version, False
        t = self.music.track
        if t is not None and self.app.images is not None:
            size = Q_ART if self.view == "queue" else self.geo()["art"]
            if size:
                self.app.images.prefetch(t.poster, size, size, tokens.GAIN["art"])
        return d

    def on_resume(self, result=None):
        self.dirty = True
        if not self.music.active and self.app.top is self:
            self.app.pop()

    # ------------------------------------------------------------ draw
    def draw(self, s):
        draw.background(s, self)
        m = self.music
        t = m.track
        n = "%d of %d" % (m.index + 1, len(m.items)) if t is not None else ""
        if m.live:
            n = "Radio"
        self.topbar(s, "Now Playing" + (" • " + n if n else ""))
        if t is None:
            return
        if self.view == "queue":
            self._draw_queue(s, m, t)
            return
        g = self.geo()
        x0, x1 = g["x0"], g["x1"]
        if g["art"]:
            r = pygame.Rect(g["art_x"], g["art_y"], g["art"], g["art"])
            img = self.app.images.get(t.poster, g["art"], g["art"], tokens.GAIN["art"]) \
                if self.app.images is not None and t.poster else None
            if img is None and m.live:               # no art for a station: its initial
                draw.rect(s, C["track"], r)
                draw.text(s, (t.title or "?")[:1].upper(), "display", C["text"], r.x,
                          r.y + (r.h - 52) // 2, w=r.w, align="center")
            else:
                draw.art(s, t, r, img, lines=3)
        title, line = t.title, " • ".join(p for p in (t.artist, t.album) if p)
        if m.live:
            from ..radio import split_title
            song, artist = split_title(m.icy)
            title, line = (song, " • ".join(p for p in (artist, t.title) if p)) if song \
                else (t.title, "")
        draw.text(s, title, "heading", C["text"], x0, g["title_y"], w=x1 - x0, align="center")
        draw.text(s, line, "label", C["text2"], x0, g["artist_y"], w=x1 - x0, align="center")
        py = g["prog_y"]
        wait = m.state == "loading" or m.cache_wait
        if m.live:                                   # a live stream has no length
            if wait:
                draw.text(s, "…", "tag", C["text2"], x0, py + 8, w=x1 - x0, align="center")
            else:                                    # tag text2, accent mark
                draw.live_tag(s, x0, py + 8, w=x1 - x0)
            self._draw_buttons(s, g, m)
            return
        bx0, bx1 = g["bar_x0"], g["bar_x1"]
        draw.seekbar(s, bx0, py, bx1 - bx0, None if wait else m.pos_s, m.dur_s,
                     focused=self.row == 0)
        self._draw_buttons(s, g, m)

    def _draw_buttons(self, s, g, m):
        for i, b in enumerate(self.buttons):
            on = (b == "shuffle" and m.shuffle) or (b == "repeat" and m.repeat != "off")
            name = b
            if b == "playpause":
                name = "pause" if m.state in ("playing", "loading") else "play"
            elif b == "repeat" and m.repeat == "one":
                name = "repeat_one"
            elif b == "stations":
                name = "queue"
            draw.button(s, self.btn_rect(i, g), icon=lambda sf, x, y, col, name=name: icon(
                sf, name, x, y, col), focused=self.row == 1 and i == self.btn, on=on)

    def _draw_queue(self, s, m, t):
        g = self.qgeo()
        x0, x1, hy, tx = g["x0"], g["x1"], g["hy"], g["tx"]
        r = pygame.Rect(x0, hy, Q_ART, Q_ART)
        img = self.app.images.get(t.poster, Q_ART, Q_ART, tokens.GAIN["art"]) \
            if self.app.images is not None and t.poster else None
        draw.art(s, t, r, img, lines=2)
        draw.text(s, t.title, "heading", C["text"], tx, hy, w=x1 - tx)
        line = " • ".join(p for p in (t.artist, t.album) if p)
        draw.text(s, line, "small", C["text2"], tx, hy + 30, w=x1 - tx)
        wait = m.state == "loading" or m.cache_wait
        frac = 0.0 if wait or not m.dur_s else max(0.0, min(1.0, m.pos_s / m.dur_s))
        draw.progress(s, tx, hy + Q_ART - tokens.PROGRESS_H, x1 - tx, frac)
        n, w = g["n"], x1 - x0
        if len(m.items) > n:                         # the scroll bar takes 12 px
            draw.scrollbar(s, x1 - tokens.SCROLLBAR_W, g["top"], n * PITCH - (PITCH - ROW_H),
                           self.qtop, n, len(m.items))
            w -= 12
        for k in range(n):
            i = self.qtop + k
            if i >= len(m.items):
                break
            it = m.items[i]
            dur = fmt_time(it.duration_ms / 1000) if it.duration_ms else None
            draw.row_compact(s, (x0, g["top"] + k * PITCH, w), None, it.title, second=it.artist,
                             right=dur, focused=i == self.qfocus, playing=i == m.index,
                             dimmed=i < m.index)
