"""Search: an on-screen key grid, live results by type, recent searches.

The chain is anchored to the safe bottom: poster row, label line, keys. The field sits under
the top edge; the gap under it is the flexible one. No hint bar (stock shows none).
"""
import pygame

from .. import anim, draw, inputs, tokens
from ..search import SearchSession
from ..tokens import C
from . import Screen, ep_code
from .keys import KeyGrid, draw_field, draw_keys, keys_top
from .keys import key_rect as _key_rect

PW, PH = 112, 168
PITCH = 128
MAX_LEN = 40
RECENT_W = 320
SEARCHING_AFTER_S = 0.5


class SearchScreen(Screen):
    ambient = True                # The idle ribbon may show here
    name = "SEARCH"
    takes_text = True
    takes_backspace = True

    @property
    def ok_on_press(self):
        """The keys act at once; the results keep hold OK (their menus)."""
        return self.zone == "keys"

    def __init__(self, app):
        super().__init__(app)
        self.q = ""
        self.zone = "keys"           # keys | results | recent
        self.keys = KeyGrid(self._type, self._delete, lambda: self._set_query(""),
                            self._retry_after_error)
        self.g = 0                   # focused result group
        self.win = {}                # group key -> first poster in view
        self.rf = 0                  # focused recent row
        self.fade_img = None         # the old poster row while the new one fades in
        self.fade = anim.Anim(tokens.ROW_FADE, anim.LINEAR)      # its alpha, 255 to 0
        self._band = None
        self.dirty = True
        self._new_session()

    @property
    def kr(self):
        return self.keys.kr

    @property
    def kc(self):
        return self.keys.kc

    @property
    def typing(self):
        return self.keys.typing

    @typing.setter
    def typing(self, v):
        self.keys.typing = v

    # ------------------------------------------------------------ data
    def _new_session(self):
        app = self.app
        self.gen0 = app.src_gen
        self.session = SearchSession(
            lambda: app.sources.search_providers(app.settings) if app.sources else [],
            self._run, on_auth=app.auth_lost)
        if self.q:
            self.session.set_query(self.q)

    def _run(self, fn, done, fail):
        """Screen.run_bg, but a reply is dropped when this screen closed or the sources changed
        (sign-out, profile switch, reconnect)."""
        gen, sess = self.gen0, self.session

        def ok():
            return (self in self.app.stack and gen == self.app.src_gen
                    and sess is self.session)
        self.run_bg(fn, lambda r: ok() and done(r), lambda e: ok() and fail(e))

    def _target_ids(self):
        app = self.app
        try:
            return [sid for sid, _p in app.sources.search_providers(app.settings)]
        except Exception:
            return []

    def sources_changed(self):
        """A server joined or the pins changed: search again only when the servers to ask
        changed. The results on screen stay until the new ones land."""
        if self._target_ids() == self.session.order or not self.session.started:
            return
        old = self.session
        self._new_session()
        self.session.rows, self.session.state = old.rows, old.state
        self.session.retry()

    @property
    def rows(self):
        return self.session.rows

    @property
    def group(self):
        return self.rows[self.g] if self.rows and self.g < len(self.rows) else None

    @property
    def item(self):
        row = self.group
        return row.items[min(row.focus, len(row.items) - 1)] if row and row.items else None

    def recent(self):
        return self.app.settings.recent_searches()

    def _save_recent(self):
        q = self.q.strip()
        if not q:
            return
        st = self.app.settings
        st.add_recent_search(q)
        try:
            st.save()
        except OSError:
            pass

    def _clear_recent(self):
        self.app.settings.clear_recent_searches()
        try:
            self.app.saved(self.app.settings.save())
        except OSError:
            pass
        if self.zone == "recent":
            self.zone = "keys"
        self.dirty = True

    def _set_query(self, q):
        q = q[:MAX_LEN]
        if q == self.q:
            return
        self.q = q
        self.session.set_query(q)
        if self.zone == "recent" and q:
            self.zone = "keys"
        self.dirty = True

    def _type(self, c):
        if c == " " and (not self.q or self.q.endswith(" ")):
            return                         # no leading or double spaces
        self._set_query(self.q + c)

    def _delete(self):
        self._set_query(self.q[:-1])

    def _rows_changed(self, old_key, old_id):
        """Keep the focus on the same item (server + id) when late results land."""
        rows = self.rows
        if not rows:
            self.g = 0
            if self.zone == "results":
                self.zone = "keys"
            return
        keys = [r.key for r in rows]
        self.g = keys.index(old_key) if old_key in keys else 0
        row = rows[self.g]
        idx = next((i for i, it in enumerate(row.items) if (it.server_id, it.id) == old_id), None)
        row.focus = idx if idx is not None else 0
        self._clamp_window(row)

    def apply_files(self, files, exact=False, reload=True):
        for f in files:
            w = f["watched"] if exact else (f["watched"] or None)
            self.update_item(f["item_id"], f["position_ms"], w, f.get("server_id"))
        self.dirty = True

    def update_item(self, item_id, view_offset_ms=None, watched=None, server_id=None):
        for row in self.rows:
            for it in row.items:
                if it.id == item_id and server_id in (None, it.server_id):
                    if view_offset_ms is not None:
                        it.view_offset_ms = view_offset_ms
                    if watched is not None:
                        it.watched = watched

    # ------------------------------------------------------------ menu
    @property
    def menu_title(self):
        it = self.item if self.zone == "results" else None
        return it.hero_title if it else ""

    def menu_items(self):
        if self.zone == "recent":
            return [("Clear Recent Searches", self._clear_recent)]
        if self.zone != "results":
            return []
        it = self.item
        rows = []
        if it is not None and it.type in ("artist", "album", "track"):
            from .library import music_rows
            return music_rows(self, it)
        if it is not None and it.type in ("movie", "episode"):
            rows.append(("Mark as Unplayed" if it.watched else "Mark as Played",
                         lambda: self.app.mark_watched(it, not it.watched)))
        if it is not None and it.type == "episode" and it.show_id:
            rows.append(("Go to Show", lambda: self.app.go_to_show(it)))
        from .actions import item_rows
        return rows + item_rows(self, it)

    # ------------------------------------------------------------ input
    def below(self):
        """The zone under the keys: results, recent searches, or None."""
        if self.rows:
            return "results"
        if not self.q and self.recent():
            return "recent"
        return None

    def handle(self, a):
        self.dirty = True
        self._handle(a)
        self._remember()

    def _handle(self, a):
        if a.startswith(inputs.CHAR):
            self._type(a[len(inputs.CHAR):])
            self.zone, self.typing = "keys", True
            return
        if a in (inputs.BACKSPACE, inputs.X):
            self._delete()
            return
        if a == inputs.Y:
            self._type(" ")
            return
        if a == inputs.BACK:
            self.app.back()
            return
        if self.zone == "keys":
            self._keys(a)
        elif self.zone == "results":
            self._results(a)
        else:
            self._recent(a)

    def _keys(self, a):
        if self.keys.handle(a, self.below):
            self._enter_below()

    def _retry_after_error(self):
        if self.session.state == "error":
            self.session.retry()

    def _enter_below(self):
        self.typing = False
        z = self.below()
        if z == "results":
            self.zone, self.g = "results", 0
            row = self.group
            row.focus = min(row.focus, len(row.items) - 1)
            self._clamp_window(row)
        elif z == "recent":
            self.zone, self.rf = "recent", 0

    def _results(self, a):
        row = self.group
        if row is None:
            self.zone = "keys"
            return
        if a in (inputs.LEFT, inputs.RIGHT, inputs.LT, inputs.RT):
            step = {inputs.LEFT: -1, inputs.RIGHT: 1, inputs.LT: -4, inputs.RT: 4}[a]
            row.focus = max(0, min(len(row.items) - 1, row.focus + step))
            self._clamp_window(row)
        elif a in (inputs.UP, inputs.DOWN):
            n = self.g + (-1 if a == inputs.UP else 1)
            if n < 0:
                self.zone = "keys"
            elif n < len(self.rows):
                self._fade_row()
                self.g = n
                self._clamp_window(self.rows[n])
        elif a == inputs.OK:
            it = self.item
            if it is not None:
                self._save_recent()
                self.app.open_item(it)

    def _recent(self, a):
        lst = self.recent()
        if not lst:
            self.zone = "keys"
            return
        self.rf = min(self.rf, len(lst) - 1)
        if a == inputs.UP:
            if self.rf == 0:
                self.zone = "keys"
            else:
                self.rf -= 1
        elif a == inputs.DOWN and self.rf < len(lst) - 1:
            self.rf += 1
        elif a == inputs.OK:
            self.zone = "keys"
            self._set_query(lst[self.rf])

    def _clamp_window(self, row):
        w = self.win.get(row.key, 0)
        if row.focus < w:
            w = row.focus
        elif row.focus >= w + 4:
            w = row.focus - 3
        self.win[row.key] = max(0, w)

    def _fade_row(self):
        g = self.geo()
        r = pygame.Rect(0, g["label_y"], 641, 480 - g["label_y"])
        try:
            self.fade_img = self.app.canvas.subsurface(r).copy()
            self.fade.start(255, 0)
        except (ValueError, AttributeError):
            self.fade_img = None

    # ------------------------------------------------------------ update
    def _remember(self):
        """The focus identity, for when new results land (they land between frames)."""
        row, it = self.group, self.item
        self._seen = self.rows
        self._fkey = row.key if row else None
        self._fid = (it.server_id, it.id) if it else None

    def update(self):
        if self.app.src_gen != self.gen0:
            self._new_session()
        changed = self.session.tick()
        if self.rows is not getattr(self, "_seen", None):
            self._rows_changed(getattr(self, "_fkey", None), getattr(self, "_fid", None))
            changed = True
        self._remember()
        fading = self.fade_img is not None
        if fading and not self.fade.active:
            self.fade_img = None
        self._prefetch()
        # "Searching…" shows after a wait: one redraw when it comes (not one each field).
        show = (self.session.state == "searching" and not self.rows
                and self.session.elapsed() >= SEARCHING_AFTER_S)
        if show != getattr(self, "_searching", False):
            self._searching, changed = show, True
        d, self.dirty = self.dirty, False
        self._band = None
        if fading and not (d or changed):
            g = self.geo()                           # only the row fade: its rows
            self._band = pygame.Rect(0, g["label_y"], 641, 480 - g["label_y"])
        return d or changed or fading

    def band(self):
        return self._band

    def _prefetch(self):
        cache = self.app.images
        if cache is None:
            return
        gain = tokens.GAIN
        for gi, row in enumerate(self.rows[self.g:self.g + 2]):
            w = self.win.get(row.key, 0) if gi == 0 else 0
            for it in row.items[w:w + 5]:
                h = PW if it.type in ("artist", "album", "track") else PH
                cache.prefetch(it.poster, PW, h, gain["unfocused"])
                if gi == 0:
                    cache.prefetch(it.poster, PW, h, gain["art"])

    # ------------------------------------------------------------ geometry
    def geo(self):
        sf = self.safe
        x0, x1, T, y1 = sf["left"], 640 - sf["right"], sf["top"], 480 - sf["bottom"]
        # The keys 44 px under the safe top; with under 8 px left above the
        # label line they are 28 high on a 32 pitch.
        kh, kp = 32, 36
        top = keys_top(T)
        label_y = y1 - 212
        if label_y - (top + 2 * kp + kh) < 8:
            kh, kp = 28, 32
        pitch = 40 if x1 - x0 >= 548 else 36
        return dict(x0=x0, x1=x1, T=T, y1=y1, kh=kh, kp=kp, keys_top=top, pitch=pitch,
                    side_x=x0 + 12 * pitch, label_y=label_y, poster_y=y1 - 176)

    def key_rect(self, r, c, g=None):
        return _key_rect(g or self.geo(), r, c)

    # ------------------------------------------------------------ draw
    def draw(self, s):
        g = self.geo()
        draw.background(s, self)
        self._draw_field(s, g)
        self._draw_keys(s, g)
        if self.rows:
            self._draw_label(s, g)
            self._draw_posters(s, g)
            if self.fade_img is not None and self.fade.active:
                self.fade_img.set_alpha(int(self.fade.value))
                s.blit(self.fade_img, (0, g["label_y"]))
        elif not self.q:
            self._draw_recent(s, g)
        else:
            self._draw_state(s, g)

    def _draw_field(self, s, g):
        draw_field(s, g["x0"], g["x1"], g["T"], self.q, "Search")

    def _draw_keys(self, s, g):
        draw_keys(s, g, self.keys, self.zone == "keys" and not self.typing)

    def _focus_text(self, it):
        if it.type in ("album", "track"):
            parts = [it.title, it.artist]
        elif it.type == "episode":
            parts = [it.show_title or it.title, ep_code(it), it.title]
        else:
            parts = [it.title, str(it.year) if it.year else ""]
        src = self.app.sources
        if src is not None and it.server_id and it.server_id != src.home_id:
            parts.append(src.server_name(it.server_id))      # another server's item
        return " • ".join(p for p in parts if p)

    def _draw_label(self, s, g):
        row = self.group
        x0, x1, y = g["x0"], g["x1"], g["label_y"]
        lw = draw.text(s, row.title, "label", C["text"], x0, y, w=200)
        right = x1
        nxt = self.rows[self.g + 1] if self.g + 1 < len(self.rows) else None
        prev = self.rows[self.g - 1] if self.g > 0 else None
        other, up = (nxt, False) if nxt else (prev, True)
        fx = x0 + lw + 16
        if other is not None:
            ow = draw.width(other.title, "label")
            if right - 20 - ow - 16 - fx >= 160 or self.zone != "results":
                tw = draw.text(s, other.title, "label", C["dim"], right, y, w=160, align="right")
                draw.triangle(s, C["dim"], right - tw - 20, y + 6, up=up)
                right = right - tw - 20 - 16
            else:
                draw.triangle(s, C["dim"], right - 12, y + 6, up=up)
                right -= 12 + 16
        if self.zone == "results" and self.item is not None and right - fx > 40:
            draw.text(s, self._focus_text(self.item), "label", C["text2"], fx, y, w=right - fx)

    def _draw_posters(self, s, g):
        row = self.group
        cache = self.app.images
        w0 = self.win.get(row.key, 0)
        x0, x1, y = g["x0"], g["x1"], g["poster_y"]
        foc_i = row.focus if self.zone == "results" else -1
        clip = s.get_clip()
        s.set_clip(pygame.Rect(0, 0, x1, 480))
        try:
            from .views import MUSIC_TYPES, music_cell, poster
            for k in range(5):
                i = w0 + k
                if i >= len(row.items):
                    break
                x = x0 + k * PITCH
                if x1 - x < (PW if k < 4 else 40):
                    break
                it = row.items[i]
                foc = i == foc_i
                square = it.type in MUSIC_TYPES
                img = cache.get(it.poster, PW, PW if square else PH,
                                tokens.GAIN["art" if foc else "unfocused"]) if cache else None
                if square:
                    music_cell(s, it, pygame.Rect(x, y, PW, PH), img, False)   # frame below
                else:
                    poster(s, it, pygame.Rect(x, y, PW, PH), img)
        finally:
            s.set_clip(clip)
        if foc_i >= 0:
            sq = row.items[foc_i].type in ("artist", "album", "track")
            draw.focus_frame(s, (x0 + (foc_i - w0) * PITCH, y, PW, PW if sq else PH))

    def _draw_recent(self, s, g):
        lst = self.recent()
        x0 = g["x0"]
        if not lst:                      # Stock shows a line, not a blank
            draw.text(s, "Search your movies, shows and music.", "body", C["text2"], x0,
                      g["label_y"], w=g["x1"] - x0)
            return
        draw.text(s, "Recent Searches", "label", C["text"], x0, g["label_y"])
        for i, q in enumerate(lst):
            y = g["poster_y"] + i * tokens.ROW_COMPACT_PITCH
            draw.row_compact(s, (x0, y, RECENT_W), None, q, nw=0,
                             focused=self.zone == "recent" and i == self.rf)

    def _draw_state(self, s, g):
        st = self.session.state
        x0, y = g["x0"], g["poster_y"]
        if len(self.q.strip()) < self.session.min_len:
            return
        if st == "error":
            head, line = "Can't reach the server", ""
        elif self.session.due is not None or st == "idle":
            return                         # typing: the old state would flash
        elif st == "searching":
            if self.session.elapsed() < SEARCHING_AFTER_S:
                return
            head, line = "Searching…", ""
        else:
            head, line = "No results", 'Nothing found for "%s"' % self.q.strip()
        draw.state(s, x0, y, g["x1"] - x0, head, line or None)
