"""Live TV guide.

Rows are channels, columns are time: 4 px for each minute, the window from the last half
hour. The focused programme shows on top (title, times, summary). It reads memory only (the
lineup tapes), so it opens with no request; the summary of the focused programme comes from a
worker after 300 ms on a cell. It never closes on a timer. A channel with no tape for this
profile has no row (never an empty channel).

Buttons: Up/Down one channel, LT/RT one page of rows, Left/Right one programme, A tunes a
programme that is on (a later one says when it is on), B goes back where the guide came from:
the picture after RB, else the side panel (or the screen under it).
"""
import time

from .. import draw, inputs, lineup, log
from ..tokens import C
from . import Screen

L = log.get("guide")

PX_MIN = 4                   # px for a minute: a half hour is 120 px
SUMMARY_AFTER_S = 0.3
HALF = 1800


def floor_half(t):
    return int(t) - int(t) % HALF


def geo(safe):
    """The rows end above the hint line of this safe area."""
    left, top = safe["left"], safe["top"]
    w = 640 - left - safe["right"]
    foot = 480 - safe["bottom"] - 24
    rows_y = top + 172
    n = max(1, (foot - 8 - rows_y + 4) // 40)
    gx = left + 140
    gw = w - 140
    return {"left": left, "top": top, "w": w, "title_y": top + 28, "meta_y": top + 56,
            "sum_y": top + 84, "ruler_y": top + 144, "rows_y": rows_y, "n": n, "foot": foot,
            "gx": gx, "gw": gw, "span": gw // PX_MIN * 60}


class GuideScreen(Screen):
    name = "GUIDE"
    menu_title = "CHANNELS"

    def __init__(self, app, focus=None):
        super().__init__(app)
        self.t0 = time.perf_counter()
        self.lu = app.lineup
        self.lu.check_file()
        self.picture = None                   # the channel that played before RB (B tunes it)
        rec = app.settings.live_recent()
        self.cid = focus or (rec[0] if rec else None)
        now = time.time()
        self.ft = now                         # the focus time (the programme on at it)
        self.win = floor_half(now)
        self.top = 0
        self._v = None
        self.summ, self.summ_key, self.summ_t, self.summ_busy = {}, None, 0.0, False
        self.logged = False

    # -- data
    def rows(self):
        """[(Channel, Tape or None)]: the visible channels, then the ones still loading."""
        out = [(c, self.lu.tape(c.id)) for c in self.lu.visible()]
        return out + [(c, None) for c in self.lu.pending()]

    def _index(self, rows):
        i = next((k for k, (c, _t) in enumerate(rows) if c.id == self.cid), None)
        if i is None:
            i = 0
            self.cid = rows[0][0].id if rows else None
        return i

    def focused(self):
        """(Channel, Tape, airing) of the focus, or Nones."""
        rows = self.rows()
        if not rows:
            return None, None, None
        c, tape = rows[self._index(rows)]
        if tape is None:
            return c, None, None
        t = max(self.ft, time.time())
        i = tape.at(t)
        return c, tape, tape.rows[i] if i is not None else None

    # -- input
    def handle(self, a):
        rows = self.rows()
        now = time.time()
        if a == inputs.BACK:
            self._back()
            return
        if a == inputs.QUIT:
            self.app.quit()
            return
        if not rows:
            return
        i = self._index(rows)
        g = self._geo()
        if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
            d = {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -g["n"], inputs.RT: g["n"]}[a]
            i = max(0, min(len(rows) - 1, i + d))
            self.cid = rows[i][0].id
            if a in (inputs.LT, inputs.RT):
                self.top = max(0, min(i, len(rows) - g["n"]))
        elif a in (inputs.LEFT, inputs.RIGHT):
            c, tape, air = self.focused()
            if air is None:
                return
            if a == inputs.RIGHT:
                if air.end < now + 24 * 3600:
                    self.ft = air.end
            else:
                self.ft = max(now, air.start - 1)
            c, tape, air = self.focused()
            if air is not None:
                start = max(air.start, now)
                if start >= self.win + g["span"] - 15 * 60:
                    while start >= self.win + g["span"] - 15 * 60:
                        self.win += HALF
                elif start < self.win:
                    self.win = max(floor_half(now), floor_half(start))
        elif a == inputs.OK:
            c, tape, air = self.focused()
            if air is None:
                return
            if air.start <= now < air.end:
                self.app.tune(c.id)
            else:
                self.app.toast("On at %s" % lineup.hhmm(air.start, self.lu._tz()))
        self._clamp(len(rows), g["n"])

    def _geo(self):
        return geo(self.safe)

    def menu_items(self):
        """Start: the ways in to the channel setup."""
        from .chsetup import AddScreen, ManageScreen
        return [("Add a Channel", lambda: self.app.push(AddScreen(self.app, done=self._added))),
                ("Manage Channels", lambda: self.app.push(ManageScreen(self.app, focus=self.cid)))]

    def _added(self, cid):
        if cid:
            self.cid = cid                        # the new channel ("Loading…" until frozen)

    def _back(self):
        if self.picture is not None and self.lu.channel(self.picture) is not None:
            cid, self.picture = self.picture, None
            self.app.tune(cid)                    # back to the picture
            return
        self.app.back()                           # from the side panel: back to it

    def _clamp(self, n_rows, n):
        i = next((k for k, (c, _t) in enumerate(self.rows()) if c.id == self.cid), 0)
        if i < self.top:
            self.top = i
        elif i >= self.top + n:
            self.top = i - n + 1
        self.top = max(0, min(self.top, max(0, n_rows - n)))

    def on_resume(self, result=None):
        if isinstance(result, dict) and result.get("channel"):
            self.cid = result["channel"]         # the channel that played
            self.picture = result["channel"] if result.get("guide") else None
            self.ft = time.time()
            self.win = floor_half(self.ft)
            self._clamp(len(self.rows()), self._geo()["n"])
        self.lu.check_file()

    # -- frame
    def update(self):
        d = self.lu.version != self._v
        self._v = self.lu.version
        c, tape, air = self.focused()
        key = air.unit.children[0].key if air is not None else None
        now = time.monotonic()
        if key != self.summ_key:
            self.summ_key, self.summ_t = key, now
        elif key and key not in self.summ and not self.summ_busy and now - self.summ_t >= SUMMARY_AFTER_S:
            self._fetch_summary(air.unit.children[0])
        if time.time() >= self.win + HALF and floor_half(time.time()) > self.win \
                and self.ft <= time.time():
            self.win = floor_half(time.time())   # the window follows the clock
            d = True
        return d or self.late_due()

    def _fetch_summary(self, e):
        p = self.app.sources.strict(e.server or None) if self.app.sources else None
        if p is None or not hasattr(p, "details"):
            self.summ[e.key] = ""
            return
        self.summ_busy = True
        it = lineup.entry_item(e)

        def done(full):
            self.summ_busy = False
            self.summ[e.key] = getattr(full, "summary", "") or ""

        def fail(_e):
            self.summ_busy = False
            self.summ[e.key] = ""
        self.run_bg(lambda: p.details(it), done, fail)

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Channels")
        g = geo(self.safe)
        rows = self.rows()
        now = time.time()
        L0, W = g["left"], g["w"]
        if not rows:
            if self.lu.loaded and lineup.OFFLINE in self.lu.status.values():
                draw.state(s, L0, g["title_y"], W, "Can't reach the server", "Retrying…")
            elif self.lu.loaded and self.lu.rules.channels and \
                    all(c.off for c in self.lu.rules.channels):
                draw.state(s, L0, g["title_y"], W, "All channels are off",
                           "Turn one on in Manage Channels.")
            elif self.lu.loaded:
                draw.state(s, L0, g["title_y"], W, "No channels to show",
                           "Their libraries have no items for this profile.")
            elif self.late(True, "lineup"):
                draw.state(s, L0, g["title_y"], W, "Loading…")
            self.hintbar(s, [("menu", "Options"), ("B", "Back")])
            self._log()
            return
        self.late(False, "lineup")
        self._clamp(len(rows), g["n"])
        c, tape, air = self.focused()
        if air is not None:
            e = air.unit.children[0]
            draw.text(s, lineup.prog_title(e), "heading", C["text"], L0, g["title_y"], w=W)
            tz = self.lu._tz()
            parts = ["%s – %s" % (lineup.hhmm(air.start, tz), lineup.hhmm(air.end, tz))]
            if air.start <= now < air.end:
                parts.append(lineup.mins_left(air.end - now))
            meta = lineup.prog_meta(e)
            parts += [meta] if meta and e.type == "episode" else []
            if e.rating and e.type == "episode":
                parts.append(e.rating)
            elif meta and e.type != "episode":
                parts.append(meta)
            draw.text(s, " • ".join(parts), "small", C["text2"], L0, g["meta_y"], w=W)
            summ = self.summ.get(e.key)
            if summ:
                draw.paragraph(s, summ, "body", C["text"], L0, g["sum_y"], W, 2)
        elif c is not None:
            draw.text(s, c.name, "heading", C["text"], L0, g["title_y"], w=W)
            draw.text(s, "Loading…", "small", C["text2"], L0, g["meta_y"], w=W)
        self._ruler(s, g, now)
        last = (self.app.settings.live_recent() or [None])[0]
        for k in range(self.top, min(len(rows), self.top + g["n"])):
            ch, tp = rows[k]
            self._row(s, g, g["rows_y"] + (k - self.top) * 40, ch, tp, ch.id == self.cid,
                      air if ch.id == self.cid else None, ch.id == last, now)
        x = g["gx"] + int(now - self.win) // (60 // PX_MIN)
        if g["gx"] <= x < g["gx"] + g["gw"]:
            y0 = g["ruler_y"] + 24
            y1 = g["rows_y"] + min(g["n"], len(rows) - self.top) * 40 - 4
            draw.rect(s, C["accent"], (x, y0, 2, y1 - y0))
        # scroll cues in the ruler row, over the channel column
        if self.top > 0:
            draw.triangle(s, C["text2"], L0 + 100, g["ruler_y"] + 6, up=True, size=12)
        if self.top + g["n"] < len(rows):
            draw.triangle(s, C["text2"], L0 + 120, g["ruler_y"] + 6, up=False, size=12)
        self.hintbar(s, [("A", "Watch"), ("menu", "Options"), ("B", "Back")])
        self._log()

    def _log(self):
        if not self.logged:
            self.logged = True
            L.info("guide open %d ms", (time.perf_counter() - self.t0) * 1000)

    def _ruler(self, s, g, now):
        tz = self.lu._tz()
        t = self.win
        while t < self.win + g["span"]:
            x = g["gx"] + (t - self.win) // (60 // PX_MIN)
            if g["gx"] + g["gw"] - x >= 48:
                draw.text(s, lineup.hhmm(t, tz), "small", C["text2"], x, g["ruler_y"])
            t += HALF

    def _row(self, s, g, y, ch, tape, focus, fair, last, now):
        L0 = g["left"]
        if focus:
            draw.rect(s, C["raised"], (L0, y, 136, 36))
            draw.current_bar(s, (L0, y, 136, 36))
        offline = self.lu.status.get(ch.id) == lineup.OFFLINE
        n = lineup.num(ch.number)
        nw = draw.text(s, n, "list", C["text"] if last else C["text2"], L0 + 8, y + 6)
        nx = L0 + 8 + max(32, nw + 8)
        draw.text(s, ch.name, "list", C["dim"] if offline else C["text"], nx, y + 6,
                  w=L0 + 136 - 4 - nx)
        if tape is None:
            draw.text(s, "Loading…", "small", C["text2"], g["gx"] + 8, y + 6)
            return
        w0, w1 = self.win, self.win + g["span"]
        step = 60 // PX_MIN                     # s for a pixel
        for a in tape.window(w0, w1):
            x0 = g["gx"] + (max(a.start, w0) - w0) // step
            x1 = g["gx"] + (min(a.end, w1) - w0) // step
            w = min(x1, g["gx"] + g["gw"]) - x0
            w -= 4 if w >= 12 else 1              # a very short item stays a sliver
            if w <= 0:
                continue
            foc = fair is not None and a.start == fair.start
            if foc:
                draw.focus_fill(s, (x0, y, w, 36))
                col = C["ink"]
            else:
                draw.rect(s, C["raised"], (x0, y, w, 36))
                col = C["dim"] if offline else (C["text"] if a.start <= now < a.end else C["text2"])
            if w >= 40:
                draw.text(s, lineup.prog_title(a.unit.children[0]), "list", col, x0 + 8, y + 6,
                          w=w - 16)


# ---------------------------------------------------------------- the guide over the picture

PIC_ROWS = 4                 # rows of the guide over the picture
PIC_H = 292                  # its height above the bottom safe margin


class PictureGuide(GuideScreen):
    """The guide over the live picture (RB in a channel session): on the mpv
    overlay, the picture plays above it. The same navigation as the guide screen. handle()
    returns what the player does: ("tune", channel id), ("close",), ("toast", text) or None.
    It makes no request (no summary)."""
    name = "GUIDE_OVER"

    def __init__(self, app, cid):
        super().__init__(app, focus=cid)

    def top_y(self):
        return (480 - self.safe["bottom"] - PIC_H) & ~1

    def _geo(self):
        """Geometry on the overlay surface (y from top_y())."""
        sf = self.safe
        w = 640 - sf["left"] - sf["right"]
        gx, gw = sf["left"] + 140, w - 140
        return {"left": sf["left"], "w": w, "title_y": 12, "meta_y": 44, "ruler_y": 76,
                "rows_y": 104, "n": PIC_ROWS, "foot": PIC_H - 24, "gx": gx, "gw": gw,
                "span": gw // PX_MIN * 60}

    def handle(self, a):
        if a in (inputs.BACK, inputs.RB):
            return ("close",)
        if a == inputs.OK:
            c, _tape, air = self.focused()
            now = time.time()
            if air is None:
                return None
            if air.start <= now < air.end:
                return ("tune", c.id)
            return ("toast", "On at %s" % lineup.hhmm(air.start, self.lu._tz()))
        if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT, inputs.LEFT, inputs.RIGHT):
            GuideScreen.handle(self, a)
        return None

    def update(self):
        if time.time() >= self.win + HALF and floor_half(time.time()) > self.win \
                and self.ft <= time.time():
            self.win = floor_half(time.time())   # the window follows the clock

    def sig(self):
        """Changes when the drawing changes: the focus, the window, the minute, the tapes."""
        return ("guide", self.cid, self.ft if self.ft > time.time() else 0, self.win, self.top,
                int(time.time() // 60), self.lu.version, tuple(self.safe.values()))

    def draw_over(self, s):
        """Draw on the overlay surface (641 wide, from top_y() to the screen bottom)."""
        g = self._geo()
        rows = self.rows()
        now = time.time()
        L0, W = g["left"], g["w"]
        cw = draw.text(s, lineup.clock_hm(), "small", C["text2"], 640 - self.safe["right"],
                       g["title_y"] + 4, align="right")
        if not rows:
            draw.text(s, "Loading…", "heading", C["text"], L0, g["title_y"], w=W - cw - 16)
            return
        self._clamp(len(rows), g["n"])
        c, tape, air = self.focused()
        if air is not None:
            e = air.unit.children[0]
            draw.text(s, lineup.prog_title(e), "heading", C["text"], L0, g["title_y"],
                      w=W - cw - 16)
            tz = self.lu._tz()
            parts = ["%s – %s" % (lineup.hhmm(air.start, tz), lineup.hhmm(air.end, tz))]
            if air.start <= now < air.end:
                parts.append(lineup.mins_left(air.end - now))
            meta = lineup.prog_meta(e)
            if meta:
                parts.append(meta)
            draw.text(s, " • ".join(parts), "small", C["text2"], L0, g["meta_y"], w=W)
        elif c is not None:
            draw.text(s, c.name, "heading", C["text"], L0, g["title_y"], w=W - cw - 16)
        self._ruler(s, g, now)
        last = (self.app.settings.live_recent() or [None])[0]
        for k in range(self.top, min(len(rows), self.top + g["n"])):
            ch, tp = rows[k]
            self._row(s, g, g["rows_y"] + (k - self.top) * 40, ch, tp, ch.id == self.cid,
                      air if ch.id == self.cid else None, ch.id == last, now)
        x = g["gx"] + int(now - self.win) // (60 // PX_MIN)
        if g["gx"] <= x < g["gx"] + g["gw"]:
            y0 = g["ruler_y"] + 24
            y1 = g["rows_y"] + min(g["n"], len(rows) - self.top) * 40 - 4
            draw.rect(s, C["accent"], (x, y0, 2, y1 - y0))
        if self.top > 0:
            draw.triangle(s, C["text2"], L0 + 100, g["ruler_y"] + 6, up=True, size=12)
        if self.top + g["n"] < len(rows):
            draw.triangle(s, C["text2"], L0 + 120, g["ruler_y"] + 6, up=False, size=12)
        draw.hints(s, [("A", "Watch"), ("B", "Back")], L0, g["foot"], W)
