"""Home / library screen: Layout A "Marquee".

One row on screen. The focus stays in the left slot and the row slides under it.
The backdrop follows the focused title after it settles, with a crossfade."""

import time

import pygame

from .. import anim, draw, inputs, tokens
from ..tokens import C, HOME
from . import Screen, meta_line

PW, PH = HOME["poster"]
SQUARE = ("artist", "album", "track", "station")   # square cells
FW, FH = HOME["poster_f"]
MW, MH = HOME["poster_mid"]
BACKDROP_WAIT = tokens.SETTLE     # ms on an item before its backdrop shows
BW, BH = tokens.BACKDROP
POSTER_CELL = {"pw": PW, "ph": PH, "fw": FW, "fh": FH, "pitch": HOME["pitch"], "round": False}
LIVE_ROW = "on_now"


def _thumb(e, kind):
    """The Plex image ref of a tape entry: the show's for an episode, else its own."""
    mid = e.group.split("/", 1)[1] if e.type == "episode" and "/" in e.group else e.id
    path = "/library/metadata/%s/%s" % (mid, kind)
    return "%s|%s" % (e.server, path) if e.server else path


def live_row(lineup, now):
    """(Row or None, due): On Right Now from the lineup tapes (memory only). due: the epoch of
    the first airing end, when the row changes, or None."""
    from .. import lineup as lu
    from ..model import Item, Row
    items, due = [], None
    for d in lineup.on_now(now):
        e = d["entry"]
        num = lu.num(d["number"])
        dur = max(1, d["end"] - d["start"])
        items.append(Item(
            id=d["cid"], type="channel", title=lu.prog_title(e), poster=_thumb(e, "thumb"),
            art=_thumb(e, "art"), duration_ms=int(dur * 1000),
            view_offset_ms=int(d["frac"] * dur * 1000),
            extra={"cid": d["cid"], "number": num, "offline": d["offline"],
                   "prefix": "%s %s" % (num, d["name"]), "end": d["end"]}))
        due = d["end"] if due is None else min(due, d["end"])
    return (Row(LIVE_ROW, "On Right Now", items) if items else None), due


class HomeScreen(Screen):
    def __init__(self, app, title="Home", rows=None, loader=None, library=None, root=None,
                 top_extra=0, embedded=False, backdrop=True):
        super().__init__(app)
        # The rows view inside the library screen: the tab line takes
        # top_extra px under the top bar, and the library screen draws the bar and the tabs.
        self.top_extra = top_extra
        self.embedded = embedded
        # backdrop=False (the detail page rows): no backdrops of its
        # own and no background draws; the parent draws the page backdrop and its scrim.
        self.backdrop = backdrop
        self._round = {}              # (ref, d, gain) -> round cut of a person image
        self.root = (library is None) if root is None else root    # a source in the side panel
        self.name = title
        self.rows = rows or []
        self._base = list(self.rows)  # the loaded rows, without On Right Now
        self._live_sig = self._live_due = None
        self.loader = loader          # callable -> rows (runs on a worker)
        self.library = library
        self.r = 0
        # The row slides 117 ms (ease-out, 4 px steps, 8 px at least); the
        # value is the offset of the row from its place.
        self.slide = anim.Anim(tokens.FAST, anim.EASE_OUT, q=tokens.Q_X,
                               min_step=tokens.MIN_STEP_X)
        self.fade_img = None          # snapshot of the poster row for the row crossfade
        self.row_fade = anim.Anim(tokens.ROW_FADE, anim.LINEAR)     # its alpha, 255 to 0
        self.back_cur = None          # backdrop shown now
        self.back_old = None
        self.back_fade = anim.Anim(tokens.SLOW, anim.LINEAR)        # back_cur alpha, 0 to 255
        self.back_ref = None
        self._settle()
        self._band = None
        self.loading = loader is not None and not rows
        self.error = None
        self.scrim = draw.home_scrim() if backdrop else None   # the parent draws its own
        if self.loading:
            self.reload()

    # ------------------------------------------------------------ data
    def reload(self, keep_focus=False):
        # A generation: when loads overlap, only the newest one lands.
        self.load_gen = gen = getattr(self, "load_gen", 0) + 1

        def done(rows):
            if gen != self.load_gen:
                return
            self.loading = False
            self.error = None
            self.set_rows(rows, keep_focus)

        def fail(e):
            if gen != self.load_gen:
                return
            self.loading = False
            if not self.rows:
                self.error = str(e)              # the log has it (Screen.run_bg); not shown
        self.run_bg(self.loader, done, fail)

    def mark_stale(self):
        """Music played on the home server. Home loads again, keeping the
        focus, now if it shows (under a menu too), else when it shows again (on_resume)."""
        self.stale = True
        top = self.app.top if self.app.stack else None
        if top is self or getattr(top, "under", None) is self:
            self.on_resume()

    def on_resume(self, result=None):
        if getattr(self, "stale", False) and self.loader:
            self.stale = False
            self.reload(keep_focus=True)
        self._live_refresh()

    # -- On Right Now (Live TV): from the lineup tapes, on the UI thread
    def _live_on(self):
        app = self.app
        src = getattr(app, "sources", None)
        return (getattr(self, "root", False) and self.library is None and self.loader is not None
                and not self.embedded and src is not None and not src.local_images
                and getattr(app, "lineup", None) is not None and app.settings.live_home())

    def _with_live(self, rows):
        """rows with On Right Now below Continue Watching (else first), when it has a channel."""
        rows = [r for r in rows if r.key != LIVE_ROW]
        self._live_due = None
        if not self._live_on():
            return rows
        row, self._live_due = live_row(self.app.lineup, time.time())
        if row is None:
            return rows
        k = next((i + 1 for i, r in enumerate(rows) if r.key == "continue"), 0)
        return rows[:k] + [row] + rows[k:]

    def _live_sig_now(self):
        lu = getattr(self.app, "lineup", None)
        return (getattr(lu, "version", None), self._live_on())

    def _live_refresh(self):
        self._live_sig = self._live_sig_now()
        old = [r for r in getattr(self, "rows", []) if r.key == LIVE_ROW]
        if not old and not self._live_sig[1]:
            return                                   # no row before or after
        if self.loading and not self._base:
            return                                   # Home shows "Loading…" first
        rows = self._with_live(self._base)
        new = [r for r in rows if r.key == LIVE_ROW]
        if old or new:
            self._set(rows, True)

    def set_rows(self, rows, keep_focus=True):
        """Replace the loaded rows; On Right Now joins them."""
        self._base = list(rows)
        self._live_sig = self._live_sig_now()
        self._set(self._with_live(rows), keep_focus)

    def _set(self, rows, keep_focus=True):
        """Replace rows; keep the current row (by key) and the focused item (by server and id)."""
        if keep_focus and self.rows:
            cur_key = self.rows[self.r].key if self.r < len(self.rows) else None
            old = {row.key: row for row in self.rows}
            for row in rows:
                o = old.get(row.key)
                if not o or not o.items:
                    continue
                f = o.items[min(o.focus, len(o.items) - 1)]
                fid = (f.server_id, f.id)                # item ids repeat across servers
                idx = next((i for i, it in enumerate(row.items)
                            if (it.server_id, it.id) == fid), None)
                row.focus = idx if idx is not None else min(o.focus, max(0, len(row.items) - 1))
            keys = [row.key for row in rows]
            self.r = keys.index(cur_key) if cur_key in keys else min(self.r, max(0, len(rows) - 1))
        else:
            self.r = 0
        self.rows = [row for row in rows if row.items]
        self.r = min(self.r, max(0, len(self.rows) - 1))
        self._settle()
        self.anim = True

    def apply_files(self, files, exact=False, reload=True):
        for f in files:
            w = f["watched"] if exact else (f["watched"] or None)
            self.update_item(f["item_id"], f["position_ms"], w, f.get("server_id"))
        if reload and self.loader:
            self.reload(keep_focus=True)

    def update_item(self, item_id, view_offset_ms=None, watched=None, server_id=None):
        for row in self.rows:
            for it in row.items:
                if it.type in ("person", "clip", "channel"):
                    continue                  # a tag id can equal a media id
                if it.id == item_id and server_id in (None, it.server_id):
                    if view_offset_ms is not None:
                        it.view_offset_ms = view_offset_ms
                    if watched is not None:
                        it.watched = watched

    @property
    def menu_title(self):
        it = self.item
        return it.hero_title if it else ""

    def menu_items(self):
        """The focused item: Mark as Played / Unplayed, Go to Show (Plex long-press menu)."""
        it = self.item
        rows = []
        if it is not None and it.type == "channel":
            cid = it.extra["cid"]
            return [("Guide", lambda: self.app.open_guide(focus=cid))]
        if it is not None and it.type in ("movie", "episode"):
            rows.append(("Mark as Unplayed" if it.watched else "Mark as Played",
                         lambda: self.app.mark_watched(it, not it.watched)))
        if it is not None and it.type == "episode" and it.show_id:
            rows.append(("Go to Show", lambda: self.app.go_to_show(it)))
        if it is not None and it.type in ("artist", "album", "track"):
            from .library import music_rows         # Play, Shuffle, Play Next, Add to Queue
            rows += music_rows(self.scr_for_bg(), it)
        elif it is not None and it.type == "station":
            rows.append(("Play", lambda: self.app.open_item(it)))
        elif it is not None:
            from .actions import item_rows
            rows += item_rows(self.scr_for_bg(), it)
        return rows

    def scr_for_bg(self):
        """The screen that runs a worker for a menu row: an embedded view's parent library
        screen is the one in the stack."""
        return self.app.top if self.embedded and self.app.top is not None else self

    @property
    def row(self):
        return self.rows[self.r] if self.rows else None

    @property
    def item(self):
        row = self.row
        if not row or not row.items:
            return None
        return row.items[min(row.focus, len(row.items) - 1)]

    def _maybe_page(self, row):
        if row.loader and not row.loading and len(row.items) < row.total and \
                row.focus >= len(row.items) - 6:
            row.loading = True
            start = len(row.items)

            def done(items):
                row.loading = False
                have = {i.id for i in row.items}
                new = [i for i in items if i.id not in have]
                row.items.extend(new)
                if not new:
                    row.total = len(row.items)
                r = self._row_rect()                 # new cells show only in the row
                old = self.__dict__.get("_press")
                self._press = r if old is None else r.union(old)
            def fail(e):
                row.loading = False
            self.run_bg(lambda: row.loader(start, 48), done, fail)

    # ------------------------------------------------------------ input
    def handle(self, a):
        if self.error and a == inputs.OK:
            self.app.reconnect(self)
            return
        if a == inputs.BACK:
            if self.root:
                self.app.open_panel()                  # B opens the panel, B again exits
            else:
                self.app.pop()
            return
        if a in (inputs.LB, inputs.RB) and self.root:
            self.app.next_source(-1 if a == inputs.LB else 1)
            return
        if a == inputs.Y and self.root and not self.embedded:
            self.app.open_search()
            return
        row = self.row
        if not row:
            return
        if a in (inputs.LEFT, inputs.RIGHT):
            step = -1 if a == inputs.LEFT else 1
            n = row.focus + step
            if n < 0 and self.root:
                self.app.open_panel()                  # Left at the row start: the panel
                return
            if 0 <= n < len(row.items):
                row.focus = n
                # A new press while the row slides starts again from the offset shown.
                self.slide.start(self.slide.value + self.cell(row)["pitch"] * step, 0)
                self._settle()
                self._press = self._move_rect()
                self._maybe_page(row)
        elif a in (inputs.UP, inputs.DOWN):
            n = self.r + (-1 if a == inputs.UP else 1)
            if 0 <= n < len(self.rows):
                self.fade_img = self.app.canvas.subsurface(self._row_rect()).copy()
                self.row_fade.start(255, 0)
                self.r = n
                self.slide.set(0)
                self._settle()
                self._press = self._move_rect()
                self._maybe_page(self.rows[n])
        elif a == inputs.OK:
            it = self.item
            if it and it.type == "channel":
                if it.extra.get("offline"):
                    self.app.toast("Server not available")
                else:
                    self.app.tune(it.extra["cid"])
            elif it:
                self.app.open_item(it)

    # ------------------------------------------------------------ geometry
    def cell(self, row=None):
        """The cell of a row: a poster (Home), or a detail page cell by item type."""
        row = row or self.row
        t = row.items[0].type if row and row.items else None
        return tokens.DETAIL_ROWS.get(t) or POSTER_CELL

    def _geo(self):
        sf, H = self.safe, tokens.HOME
        bottom = 480 - sf["bottom"] - H["row_bottom_inset"]   # outline ends 4 px inside safe
        ftop = bottom - FH
        label_y = ftop - H["gap_label_row"] - 24
        meta_y = label_y - H["gap_hero_row"] - 24
        title_bottom = meta_y - H["gap_title_meta"]
        return dict(x=sf["left"], bottom=bottom, ftop=ftop, label_y=label_y, meta_y=meta_y,
                    title_bottom=title_bottom,
                    title_min_top=sf["top"] + 24 + H["hero_top_min"] + self.top_extra)

    def _row_rect(self):
        g = self._geo()
        return pygame.Rect(0, g["ftop"] - 6, 641, FH + 12)

    def _move_rect(self):
        """A focus move changes the title, the meta line, the row labels and the row: every
        row from the hero's top (16 px of room above the highest title line for glyphs that
        rise above their line box) to the row's bottom. The backdrop waits for the settle."""
        g = self._geo()
        top = min(g["title_min_top"], g["meta_y"]) - 16
        r = self._row_rect()
        return pygame.Rect(0, top, 641, r.bottom - top)

    # ------------------------------------------------------------ update/draw
    def _on_screen(self, row):
        """The cell offsets from the focus (k) whose cell shows on screen when the row rests,
        with the x of _draw_row and _draw_cells."""
        g = self._geo()
        spec = tokens.DETAIL_ROWS.get(row.items[0].type) if row.items else None
        cw, pw, pitch = (spec["fw"], spec["pw"], spec["pitch"]) if spec else (FW, PW,
                                                                                tokens.HOME["pitch"])
        x0 = g["x"]
        out = set()
        for k in range(-2, 6):
            if k == 0:
                x, w = x0, cw
            elif k > 0:
                x, w = x0 + cw + tokens.HOME["gap"] + (k - 1) * pitch, pw
            else:
                x, w = x0 + (cw - pw) // 2 + k * pitch - 8, pw
            if x + w >= 0 and x <= 641:
                out.add(k)
        return out

    def _settle(self):
        """The focus moved: the backdrop waits BACKDROP_WAIT ms for the new item."""
        self.settle_t = anim.now()
        self._near = False

    def _back_rect(self):
        return pygame.Rect(0, 0, 641, BH)

    def band(self):
        return self._band

    def image_band(self):
        """New images show only in the poster row (backdrops are taken in update())."""
        return self._row_rect()

    def update(self):
        if self.root and self.loader is not None and (
                self._live_sig != self._live_sig_now()
                or (self._live_due is not None and time.time() >= self._live_due)):
            self._live_refresh()                     # a new lineup, the setting, an airing end
        dirty, band = False, None

        def add(r):
            nonlocal band
            band = r if band is None else band.union(r)
        if self.slide.active:
            dirty = True
            add(self._row_rect())
        if self.fade_img is not None:
            dirty = True
            add(self._row_rect())
            if not self.row_fade.active:
                self.fade_img = None
        it = self.item
        cache = self.app.images
        if it:
            # preload posters for the drawn window and one beyond it; the focused size only
            # for cells on screen; backdrops for +-2
            row = self.row
            spec = tokens.DETAIL_ROWS.get(it.type)
            seen = self._on_screen(row)
            for k in range(max(0, row.focus - 2), min(len(row.items), row.focus + 7)):
                p = row.items[k]
                near = k - row.focus in seen
                if spec is not None:
                    cache.prefetch(p.poster, spec["pw"], spec["ph"], tokens.GAIN["unfocused"])
                    if near:
                        cache.prefetch(p.poster, spec["fw"], spec["fh"], tokens.GAIN["art"])
                    continue
                sq = p.type in SQUARE
                cache.prefetch(p.poster, PW, PW if sq else PH, tokens.GAIN["unfocused"])
                if near:
                    cache.prefetch(p.poster, FW, FW if sq else FH, tokens.GAIN["art"])
                if abs(k - row.focus) == 1:          # the middle size of the focus step
                    cache.prefetch(p.poster, MW, MW if sq else MH, tokens.GAIN["art"])
        if it and self.backdrop:
            row = self.row
            # Music: the item's own art as the backdrop, else none.
            ref = it.art if it.type in SQUARE else (it.art or it.poster)
            waited = anim.now() - self.settle_t
            if ref is None and self.back_ref is not None and waited >= BACKDROP_WAIT:
                # A music item with no art: no backdrop, not the last item's.
                self.back_old, self.back_cur, self.back_ref = None, None, None
                self.back_fade.set(255)
                dirty = True
                add(self._back_rect())
            elif ref != self.back_ref and waited >= BACKDROP_WAIT:
                img = cache.get(ref, 641, BH, tokens.GAIN["backdrop"])
                if img is not None:
                    self.back_old, self.back_cur, self.back_ref = self.back_cur, img, ref
                    if self.back_old is not None:
                        self.back_fade.start(0, 255)
                    else:
                        self.back_fade.set(255)
                    dirty = True
                    add(self._back_rect())
            if not self._near and waited >= BACKDROP_WAIT + 4 * tokens.FIELD_MS:
                self._near = True
                for k in (row.focus - 2, row.focus - 1, row.focus + 1, row.focus + 2):
                    if 0 <= k < len(row.items):
                        q = row.items[k]
                        qref = q.art if q.type in SQUARE else (q.art or q.poster)
                        if qref:
                            cache.prefetch(qref, 641, BH, tokens.GAIN["backdrop"])
        if self.back_fade.active:
            dirty = True
            add(self._back_rect())
        if self.late_due():
            dirty, band = True, None                 # "Loading…" after 500 ms: a full pass
        d, self.anim = self.anim, False              # set_rows, a page, a resume: full pass
        if d:
            dirty, band = True, None
        self._band = band if dirty else None
        return dirty

    def draw(self, s):
        g = self._geo()
        if self.backdrop:
            if self.back_cur is not None and self.back_fade.active and self.back_old is not None:
                s.fill(C["bg"])
                s.blit(self.back_old, (0, 0))
                self.back_cur.set_alpha(int(self.back_fade.value))
                s.blit(self.back_cur, (0, 0))
                self.back_cur.set_alpha(None)
                s.blit(self.scrim, (0, 0))
            else:
                s.fill(C["bg"])
                s.blit(self._back_plate(s), (0, 0))
            s.fill(C["bg"], (0, BH, 641, 480 - BH))
        if not self.embedded:
            cw = self.topbar(s, self.name, reserve=12 + 24, root=self.root)
            # Menu is the one Home action with no control on screen: its glyph sits by the clock.
            draw.glyph_draw(s, "menu", 640 - self.safe["right"] - cw - 12 - 24, self.safe["top"])
        sy = self.safe["top"] + 44 + self.top_extra     # state text: the content's top left
        sw = 640 - self.safe["right"] - g["x"]
        if self.late(self.loading and not self.rows):
            draw.state(s, g["x"], sy, sw, "Loading…")
        if self.loading and not self.rows:
            return
        if self.error:
            draw.state(s, g["x"], sy, sw, "Can't reach the server", "Press A to try again.")
            return
        it = self.item
        if not it:
            draw.state(s, g["x"], sy, sw, "Nothing here yet")
            return
        # Title steps: with less than 40 px of room, the meta line goes and
        # the title moves down into its place.
        show_meta = g["title_bottom"] - g["title_min_top"] >= 40
        if not show_meta:
            g = dict(g, title_bottom=g["meta_y"] + 28)
        self._draw_title(s, it, g)
        meta = meta_line(it) if show_meta else ""
        src = self.app.sources
        if show_meta and src is not None and it.server_id and it.server_id != src.home_id \
                and it.type != "channel":
            name = src.server_name(it.server_id)       # items from another server
            meta = meta + " • " + name if name and meta else (meta or name)
        draw.text(s, meta, "label", C["text2"], g["x"], g["meta_y"], w=360)
        self._draw_label(s, g)
        self._draw_row(s, g)
        if self.fade_img is not None and self.row_fade.active:
            self.fade_img.set_alpha(int(self.row_fade.value))
            s.blit(self.fade_img, self._row_rect().topleft)
        # No hint bar: stock Plex shows none.

    def _back_plate(self, s):
        """The resting backdrop with the scrim on it, in one surface of the canvas format, built
        again only when the backdrop, the scrim or the theme changes: the same pixels as
        drawing the two on the bg fill each frame (both start from that fill)."""
        from .. import theme
        key = (self.back_cur, self.scrim, theme.version, s.get_bitsize(), s.get_masks())
        plate = getattr(self, "_plate", None)
        if plate is None or plate[0] != key:
            surf = pygame.Surface((641, max(BH, self.scrim.get_height())), 0, s)
            surf.fill(C["bg"])
            if self.back_cur is not None:
                surf.blit(self.back_cur, (0, 0))
            surf.blit(self.scrim, (0, 0))
            plate = self._plate = (key, surf)
        return plate[1]

    def _draw_title(self, s, it, g):
        """The largest of: display one line, title two lines, title one line, heading one
        line, that fits the width and leaves hero_top_min under the top bar."""
        title = it.hero_title
        w = tokens.HOME["title_w"]
        room = g["title_bottom"] - g["title_min_top"]
        if draw.width(title, "display") <= w and room >= 52:
            draw.text(s, title, "display", C["text"], g["x"], g["title_bottom"] - 52)
            return
        if room < 40:                              # the last step: heading, one line
            draw.text(s, title, "heading", C["text"], g["x"], g["title_bottom"] - 28, w=w)
            return
        lines = draw.wrap(title, "title", w, 2 if room >= 80 else 1)
        y = g["title_bottom"] - 40 * len(lines)
        for ln in lines:
            draw.text(s, ln, "title", C["text"], g["x"], y, w=w)
            y += 40

    def _draw_label(self, s, g):
        # The row name only: no label of the next row (it cluttered Home).
        draw.text(s, self.row.title, "label", C["text"], g["x"], g["label_y"], w=300)

    def _draw_row(self, s, g):
        row = self.row
        spec = tokens.DETAIL_ROWS.get(row.items[0].type) if row.items else None
        if spec is not None:
            self._draw_cells(s, g, row, spec)
            return
        cache = self.app.images
        off = int(self.slide.value)
        sliding = off != 0
        x0 = g["x"]
        small_y = g["bottom"] - PH
        first_small_x = x0 + FW + tokens.HOME["gap"]
        for k in range(-2, 6):
            i = row.focus + k
            if not 0 <= i < len(row.items):
                continue
            it = row.items[i]
            sq = it.type in SQUARE                   # music: square art, two text lines under
            mid = None
            if k == 0 and sliding and self.slide.progress >= 0.5:
                # From half the slide the arriving poster shows at the middle
                # size, when that image is cached (it is not fetched here).
                mid = cache.get(it.poster, MW, MW if sq else MH, tokens.GAIN["art"],
                                request=False)
            if k == 0 and not sliding:
                r = pygame.Rect(x0, g["ftop"], FW, FW if sq else FH)
                img = cache.get(it.poster, r.w, r.h, tokens.GAIN["art"])
            elif mid is not None:
                r = pygame.Rect(x0 + (FW - MW) // 2 + off, g["ftop"] + FW - MW if sq else
                                g["bottom"] - MH, MW, MW if sq else MH)
                img = mid
            else:
                if k == 0:
                    x = x0 + (FW - PW) // 2
                elif k > 0:
                    x = first_small_x + (k - 1) * tokens.HOME["pitch"]
                else:
                    x = x0 + (FW - PW) // 2 + k * tokens.HOME["pitch"] - 8
                # Square art ends on the focused art's bottom line, so the text lines align.
                r = pygame.Rect(x + off, g["ftop"] + FW - PW if sq else small_y, PW, PW if sq else PH)
                img = cache.get(it.poster, r.w, r.h, tokens.GAIN["unfocused"])
            if r.right < 0 or r.left > 641:
                continue
            self._poster(s, it, r, img)
            if sq:
                foc = k == 0 and not sliding
                draw.text(s, it.title, "small", C["text"] if foc else C["text2"], r.x,
                          r.bottom + 4, w=r.w)
                sub = meta_line(it).split(" • ")[0] if it.type != "artist" else ""
                if sub:
                    draw.text(s, sub, "small", C["dim"], r.x, r.bottom + 28, w=r.w)
        cur = row.items[row.focus] if 0 <= row.focus < len(row.items) else None
        if cur is not None and cur.type in SQUARE:
            draw.focus_frame(s, (x0, g["ftop"], FW, FW))
        else:
            draw.focus_frame(s, (x0, g["ftop"], FW, FH))

    def _draw_cells(self, s, g, row, spec):
        """A detail page row: round people or 16:9 extras. The focused cell is
        in the left slot; art ends on one line (ftop + DETAIL_ART_END) so the name and the second
        line under each cell align."""
        cache = self.app.images
        off = int(self.slide.value)
        sliding = off != 0
        x0, pitch = g["x"], spec["pitch"]
        art_end = g["ftop"] + tokens.DETAIL_ART_END
        first_small_x = x0 + spec["fw"] + tokens.HOME["gap"]
        for k in range(-2, 6):
            i = row.focus + k
            if not 0 <= i < len(row.items):
                continue
            it = row.items[i]
            foc = k == 0 and not sliding
            if foc:
                w, h, x = spec["fw"], spec["fh"], x0
            else:
                w, h = spec["pw"], spec["ph"]
                if k == 0:
                    x = x0 + (spec["fw"] - w) // 2
                elif k > 0:
                    x = first_small_x + (k - 1) * pitch
                else:
                    x = x0 + (spec["fw"] - w) // 2 + k * pitch - 8
            r = pygame.Rect(x + (0 if foc else off), art_end - h, w, h)
            if r.right < 0 or r.left > 641:
                continue
            gain = tokens.GAIN["art" if foc else "unfocused"]
            img = cache.get(it.poster, w, h, gain)
            if spec["round"]:
                self._round_art(s, it, r, img, gain)
            else:
                draw.art(s, it, r, img, lines=2)
            line1 = it.title
            line2 = meta_line(it)
            draw.text(s, line1, "small", C["text"] if foc else C["text2"], r.x, art_end + 4,
                      w=max(w, pitch - tokens.HOME["gap"]))
            if line2:
                draw.text(s, line2, "small", C["dim"], r.x, art_end + 28,
                          w=max(w, pitch - tokens.HOME["gap"]))
        fr = pygame.Rect(x0, art_end - spec["fh"], spec["fw"], spec["fh"])
        if spec["round"]:
            draw.focus_ring(s, fr.center, spec["fw"] // 2)
        else:
            draw.focus_frame(s, fr)

    def _round_art(self, s, it, r, img, gain):
        """A round person photo, cut on the UI thread and kept per (ref, size, gain); no photo:
        a track disc with the initials."""
        d = r.w
        if img is not None:
            key = (it.poster, d, round(gain, 2))
            cut = self._round.get(key)
            if cut is None:
                from .. import avatars
                cut = self._round[key] = avatars.round_surface(img, d)
            s.blit(cut, r.topleft)
            return
        pygame.draw.circle(s, C["track"], r.center, d // 2)
        ini = "".join(w[0] for w in it.title.split()[:2]).upper()
        draw.text(s, ini, "heading", C["text2"], r.x, r.centery - 14, w=d, align="center")

    def _poster(self, s, it, r, img):
        draw.art(s, it, r, img)
        if it.type == "channel":
            dur = it.duration_ms or 1                # the bar moves with the clock
            it.view_offset_ms = int(max(0, min(dur, dur - (it.extra["end"] - time.time()) * 1000)))
            if it.extra.get("offline"):
                draw.scrim(s, r)                     # a channel whose server is offline: dim
            draw.art_marks(s, it, r)
            draw.channel_tag(s, r, it.extra["number"])
            return
        draw.art_marks(s, it, r)

    def theme_changed(self):
        """A theme or High Contrast change: the scrim is in bg, and the backdrop
        was cut at the old gain; it loads again at the new one."""
        self.scrim = draw.home_scrim() if self.backdrop else None
        self.back_old, self.back_cur, self.back_ref = None, None, None
        self.back_fade.set(255)
