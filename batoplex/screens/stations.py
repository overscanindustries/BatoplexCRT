"""Add Stations: the field, the key grid and a list of stations from the
directory. An empty field (or one character) lists "Popular"; two or more characters search by
name ("Stations"). A on a row adds the station to radio.m3u or removes it; the row shows the
result. X on a row plays the station here. The query is user text: it is never logged.
"""
from .. import draw, inputs, radio, stationdir, tokens
from ..search import SearchSession
from ..tokens import C
from . import Screen
from .keys import KeyGrid, draw_field, draw_keys, keys_top
from .keys import key_rect as _key_rect

MAX_LEN = 40
ROW_H, PITCH = tokens.ROW_TWO_H, tokens.ROW_TWO_PITCH
LOADING_AFTER_S = 0.5


class StationsScreen(Screen):
    ambient = True                # the idle ribbon may show here
    name = "STATIONS"
    takes_text = True
    takes_backspace = True

    @property
    def ok_on_press(self):
        """The keys act at once; the list keeps hold OK (its menu)."""
        return self.zone == "keys"

    def focus_identity(self):
        """The focused station, not its index (a new search fills the list)."""
        if self.zone != "list" or not self.rows:
            return ("keys",)
        r = self.rows[min(self.focus, len(self.rows) - 1)]
        return ("station", getattr(r, "id", None), getattr(r, "title", None))

    def __init__(self, app):
        super().__init__(app)
        self.q = ""
        self.zone = "keys"           # keys | list
        self.keys = KeyGrid(self._type, self._delete, lambda: self._set_query(""), self._retry)
        self.focus, self.top = 0, 0  # focused row, first row in view
        self._fid = None             # the focused station's URL, kept when new rows land
        self._seen = None
        self._mv = None
        self.dirty = True
        self._read_file()
        self.session = SearchSession(lambda: [("dir", stationdir.get())], self._run, min_len=0,
                                     delay_s=0.5, limit=stationdir.LIMIT, cap=stationdir.LIMIT)
        self.session.retry()         # Popular now, through the same query check as search

    # ------------------------------------------------------------ data
    def _run(self, fn, done, fail):
        """Screen.run_bg; a reply is dropped when this screen closed."""
        sess = self.session

        def ok():
            return self in self.app.stack and sess is self.session
        self.run_bg(fn, lambda r: ok() and done(r), lambda e: ok() and fail(e))

    def _read_file(self):
        """The URLs and uuids in radio.m3u, for the "Added" mark."""
        found = radio.entries(radio.read_text())
        self.urls = {e[1] for e in found}
        self.uuids = {e[2] for e in found if e[2]}

    def added(self, it):
        x = it.extra
        return x["url"] in self.urls or (x.get("alt") or None) in self.urls or \
            (x.get("uuid") or None) in self.uuids

    def playing(self, it):
        m = self.app.music
        t = m.track if m.active and m.live else None
        return t is not None and t.extra.get("url") == it.extra["url"]

    @property
    def rows(self):
        return self.session.rows[0].items if self.session.rows else []

    @property
    def item(self):
        r = self.rows
        return r[min(self.focus, len(r) - 1)] if r and self.zone == "list" else None

    def toggle(self, it):
        """A on a row: add, or remove when added. A refusal is a toast; success
        shows only on the row."""
        x = it.extra
        try:
            if self.added(it):
                gone = radio.remove([x["url"], x.get("alt")], x.get("uuid", ""))
                self.app.music.drop_stations(set(gone))
            else:
                radio.add(it.title, x["url"], x.get("uuid", ""))
        except radio.Refused as e:
            self.app.toast(str(e))
        self._read_file()
        self.dirty = True

    def _query(self):
        q = self.q.strip()
        return q if len(q) >= 2 else ""

    def _set_query(self, q):
        q = q[:MAX_LEN]
        if q == self.q:
            return
        self.q = q
        self.session.set_query(self._query())
        self.dirty = True

    def _type(self, c):
        if c == " " and (not self.q or self.q.endswith(" ")):
            return
        self._set_query(self.q + c)
        self._retry()                # a new character tries again after an error

    def _delete(self):
        self._set_query(self.q[:-1])

    def _retry(self):
        if self.session.state == "error":
            self.session.retry()

    # ------------------------------------------------------------ menu
    @property
    def menu_title(self):
        it = self.item
        return it.title if it else ""

    def menu_items(self):
        it = self.item
        if it is None:
            return []
        return [("Remove from Radio" if self.added(it) else "Add to Radio",
                 lambda: self.toggle(it)), ("Play", lambda: self.preview(it))]

    def preview(self, it):
        """X: play the station with no change of screen; the music queue stops."""
        self.app.play_radio([it], 0, show=False)
        self.dirty = True

    # ------------------------------------------------------------ input
    def handle(self, a):
        self.dirty = True
        if a.startswith(inputs.CHAR):
            self._type(a[len(inputs.CHAR):])
            self.zone, self.keys.typing = "keys", True
        elif a == inputs.BACKSPACE or (a == inputs.X and self.zone == "keys"):
            self._delete()
        elif a == inputs.Y:
            self._type(" ")
        elif a == inputs.BACK:
            self.app.pop()
        elif self.zone == "keys":
            if self.keys.handle(a, lambda: bool(self.rows)):
                self.zone, self.focus, self.top = "list", 0, 0
        else:
            self._list(a)
        it = self.item
        self._fid = it.extra["url"] if it else None

    def _list(self, a):
        n = len(self.rows)
        if not n:
            self.zone = "keys"
            return
        if a == inputs.UP and self.focus == 0:
            self.zone = "keys"
        elif a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
            step = {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -5, inputs.RT: 5}[a]
            self.focus = max(0, min(n - 1, self.focus + step))
        elif a == inputs.OK:
            self.toggle(self.rows[min(self.focus, n - 1)])
        elif a == inputs.X:
            self.preview(self.rows[min(self.focus, n - 1)])

    # ------------------------------------------------------------ update
    def update(self):
        changed = self.session.tick()
        if self.session.rows is not self._seen:
            self._seen = self.session.rows
            rows = self.rows
            k = next((i for i, it in enumerate(rows) if it.extra["url"] == self._fid), None)
            self.focus = k if k is not None else 0
            if not rows and self.zone == "list":
                self.zone = "keys"
            changed = True
        m = self.app.music
        if m.version != self._mv:
            self._mv, changed = m.version, True
        # "Loading…" shows after a wait: one redraw when it comes (not one each field).
        show = (self.session.state == "searching" and not self.rows
                and self.session.elapsed() >= LOADING_AFTER_S)
        if show != getattr(self, "_loading_shown", False):
            self._loading_shown, changed = show, True
        d, self.dirty = self.dirty, False
        return d or changed

    def on_resume(self, result=None):
        self._read_file()            # a hand edit or a remove on the Radio screen
        self.dirty = True

    # ------------------------------------------------------------ geometry
    def geo(self):
        """Zone keys: the keys 12 px under the field, the label line 12 px under the keys, the
        rows 8 px under the label. Zone list: no keys; the label line 12 px under the field."""
        sf = self.safe
        x0, x1, T, y1 = sf["left"], 640 - sf["right"], sf["top"], 480 - sf["bottom"]
        kh, kp = 32, 36
        top = keys_top(T)
        pitch = 40 if x1 - x0 >= 548 else 36
        label_y = top + 2 * kp + kh + 12 if self.zone == "keys" else T + 32 + 12
        return dict(x0=x0, x1=x1, T=T, y1=y1, kh=kh, kp=kp, keys_top=top, pitch=pitch,
                    side_x=x0 + 12 * pitch, label_y=label_y, list_y=label_y + 24 + 8)

    def key_rect(self, r, c, g=None):
        return _key_rect(g or self.geo(), r, c)

    def row_rect(self, i, g=None):
        """Row i on screen (it can reach under the safe bottom: the list clips it)."""
        g = g or self.geo()
        return (g["x0"], g["list_y"] + (i - self.top) * PITCH, g["x1"] - g["x0"] - 12, ROW_H)

    def _scroll(self, g):
        view = max(1, (g["y1"] - g["list_y"] + PITCH - ROW_H) // PITCH)
        if self.focus < self.top:
            self.top = self.focus
        elif self.focus >= self.top + view:
            self.top = self.focus - view + 1
        self.top = max(0, min(self.top, max(0, len(self.rows) - view)))
        return view

    # ------------------------------------------------------------ draw
    def draw(self, s):
        g = self.geo()
        draw.background(s, self)
        draw_field(s, g["x0"], g["x1"], g["T"], self.q, "Search stations", scr=self)
        if self.zone == "keys":
            draw_keys(s, g, self.keys, not self.keys.typing)
        sr = self.session.rows
        label = sr[0].title if sr else ("Stations" if self._query() else "Popular")
        draw.text(s, label, "label", C["text"], g["x0"], g["label_y"], w=g["x1"] - g["x0"])
        if self.rows:
            self._draw_rows(s, g)
        else:
            self._draw_state(s, g)

    def _draw_rows(self, s, g):
        """Two-line rows (56 on a 60 pitch); line 2 in small text2, as given;
        "Added" / "Playing" right in small text2."""
        rows = self.rows
        view = self._scroll(g)
        x0, full, y0, y1 = g["x0"], g["x1"] - g["x0"], g["list_y"], g["y1"]
        foc_i = self.focus if self.zone == "list" else -1
        clip = s.get_clip()
        s.set_clip((0, y0, 641, y1 - y0))
        for i in range(self.top, min(len(rows), self.top + view + 1)):
            it = rows[i]
            x, y, w, h = self.row_rect(i, g)
            if y + h > y1:                               # a cut row shows no cut text
                continue
            mark = "Added" if self.added(it) else ("Playing" if self.playing(it) else "")
            draw.row_two(s, (x, y, w), it.title, it.extra.get("line2") or "",
                         right=mark or None, focused=i == foc_i)
        s.set_clip(clip)
        draw.scrollbar(s, x0 + full - tokens.SCROLLBAR_W, y0, y1 - y0, self.top, view,
                       len(rows))

    def _draw_state(self, s, g):
        st = self.session.state
        x0, y, w = g["x0"], g["list_y"], g["x1"] - g["x0"]
        if st == "error":
            head, line = "Can't load stations", "Can't reach the station directory"
        elif self.session.due is not None or st == "idle":
            return                         # typing: the old state would flash
        elif st == "searching":
            if self.session.elapsed() < LOADING_AFTER_S:
                return
            head, line = "Loading…", ""
        else:
            q = self._query()
            head, line = "No results", ('Nothing found for "%s"' % q) if q else ""
        draw.state(s, x0, y, w, head, line or None)
