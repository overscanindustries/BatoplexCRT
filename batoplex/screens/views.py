"""GridView: the poster grid of a library.

A wall of posters (five to a row; four square cells for music). On the first row the tabs
and the chip line show over the grid; from the second row on they slide up off the screen
and the focused row is the second row on screen. The topbar shows the focused title. With
the title sort, an A-Z rail on the right edge (stock Plex's jump bar) and LT/RT jump letters.

Data comes in pages of 48 from a worker. A page loads when the focus comes within 3 rows of
it; a sort or filter change starts a new generation and late pages from the old one are
dropped. At most MAX_PAGES pages stay in memory.
"""
import pygame

from .. import anim, draw, inputs, log, tokens
from ..plex import letter_offset
from ..tokens import C

# The wall of art you scroll through. Video: 5 posters a row;
# music keeps 4 square cells with two text lines (names matter there).
VIDEO = dict(cols=5, pw=96, ph=144, gap=12, pitch=156)
MUSIC = dict(cols=4, pw=112, ph=168, gap=16, pitch=180)
PAGE = 48
MAX_PAGES = 8
SETTLE = 12                             # fields on a rail letter before the grid follows
OVERLAY_FIELDS = 30                     # the big letter of a jump stays this long
RAIL_W, RAIL_PITCH = 20, 20
BUTTONS = ("play", "shuffle")            # toolbar items drawn as buttons
MUSIC_TYPES = ("artist", "album", "track")
SQUARE_TYPES = MUSIC_TYPES + ("playlist",)      # square art (playlist composites)
LOG = log.get("grid")
MOVES = (inputs.UP, inputs.DOWN, inputs.LEFT, inputs.RIGHT, inputs.LT, inputs.RT)


class GridView:
    def __init__(self, scr, fetch, top, chips=("sort", "filter"), letters=None, meta=None,
                 head=None, sort=None, empty="Nothing here yet", actions=None, music=False,
                 error="Can't load this library", empty_line=None):
        """fetch(sort, filters, start, size) -> (items, total), on a worker.
        top: y of the chip line. chips: which chips show (() for none).
        letters(filters) -> [(letter, count)] or None, on a worker (title sort only).
        meta() -> {"sorts", "filters"} on a worker, for the menus.
        head(total) -> text for the chip line when there are no chips.
        actions: {chip: (label(), run())} for chips other than sort and filter (music:
        the Artists/Albums kind, Shuffle, Play)."""
        self.actions = dict(actions or {})
        self.L = MUSIC if music else VIDEO
        self.scroll, self.overlay = 0, 0
        # A one-row scroll moves 117 ms (ease-out, 4 px steps, 8 px at least);
        # a new press starts again from the place shown. Longer jumps are cuts.
        self.sanim = anim.Anim(tokens.FAST, anim.EASE_OUT, q=tokens.Q_Y,
                               min_step=tokens.MIN_STEP_Y)
        self._band = None
        self.scr, self.app = scr, scr.app
        self.fetch, self.top, self.chips = fetch, top, tuple(chips)
        self.letters_fn, self.meta_fn, self.head = letters, meta, head
        self.empty, self.error_text = empty, error
        self.empty_line = empty_line    # a second line, a string or a callable
        self.sort = sort or {"key": "titleSort", "title": "Title", "dir": "asc"}
        self.filters, self.filter_label = {}, "All"
        self.meta = None
        self.zone = "grid"
        self.chip = 0
        self.focus = 0
        self.letter_i = 0
        self.settle = 0
        self.gen = 0
        self.pages, self.busy, self.total = {}, set(), None
        self.chars = None
        self.error = None
        self.dirty = True
        self._ov_end = False
        self.meta_busy = False
        self.query()

    # ---------------------------------------------------------------- data
    @property
    def by_title(self):
        return self.sort["key"] == "titleSort" and self.letters_fn is not None

    def sort_param(self):
        return self.sort["key"] + (":desc" if self.sort["dir"] == "desc" else "")

    def query(self, focus=0):
        """Start again: a sort or filter change, or a retry."""
        self.gen += 1
        self.pages, self.busy, self.total = {}, set(), None
        self.chars, self.error = None, None
        self.focus = focus
        self.letter_i, self.settle = 0, 0
        if self.zone == "rail":                      # the rail is gone until the letters return
            self.zone = "chips" if self.chips else "grid"
        self._ensure()
        if self.meta is None and self.meta_fn and "sort" in self.chips and not self.meta_busy:
            self.meta_busy = True                    # again on each query until it loads

            def got(meta):
                self.meta_busy = False
                if meta.get("failed"):
                    return                           # default sorts meanwhile; try again later
                self._got_meta(meta)

            def lost(_e):
                self.meta_busy = False
            self.scr.run_bg(self.meta_fn, got, lost)
        if self.by_title:
            gen, f = self.gen, dict(self.filters)

            def done(chars):
                if gen == self.gen:
                    self.chars = chars or []
                    self.dirty = True
            self.scr.run_bg(lambda: self.letters_fn(f), done, lambda e: None)

    def _got_meta(self, meta):
        self.meta = meta
        cur = next((s for s in meta.get("sorts") or [] if s["key"] == self.sort["key"]), None)
        if cur:
            self.sort["title"] = cur["title"]
        self.dirty = True

    def _load(self, page):
        if page in self.pages or page in self.busy or page < 0:
            return
        if self.total is not None and page * PAGE >= self.total:
            return
        self.busy.add(page)
        gen, sort, f = self.gen, self.sort_param(), dict(self.filters)

        def done(res):
            if gen != self.gen:
                return                               # an old sort or filter
            self.busy.discard(page)
            items, total = res
            self.pages[page] = items
            self.total = total
            if self.focus > max(0, total - 1):
                self.focus = max(0, total - 1)
                self._ensure()
            self._trim()
            self.dirty = True

        def fail(e):
            if gen != self.gen:
                return
            self.busy.discard(page)
            LOG.info("grid page %d failed: %s", page, e)
            if not self.pages:
                self.error = str(e)
            else:
                self.app.toast("Can't load more of this library")
            self.dirty = True
        self.scr.run_bg(lambda: self.fetch(sort, f, page * PAGE, PAGE), done, fail)

    def _ensure(self):
        """Load the focused page, and the next or previous one near its edges."""
        p, cols = self.focus // PAGE, self.L["cols"]
        self._load(p)
        within = self.focus % PAGE
        if within >= PAGE - 4 * cols:
            self._load(p + 1)
        if within < 4 * cols:
            self._load(p - 1)

    def _trim(self):
        if len(self.pages) <= MAX_PAGES:
            return
        p = self.focus // PAGE
        for k in sorted(self.pages, key=lambda k: -abs(k - p))[:len(self.pages) - MAX_PAGES]:
            del self.pages[k]

    def item(self, i=None):
        i = self.focus if i is None else i
        page = self.pages.get(i // PAGE)
        return page[i % PAGE] if page and i % PAGE < len(page) else None

    def update_item(self, item_id, view_offset_ms=None, watched=None, server_id=None):
        for page in self.pages.values():
            for it in page:
                if it.id == item_id and server_id in (None, it.server_id):
                    if view_offset_ms is not None:
                        it.view_offset_ms = view_offset_ms
                    if watched is not None:
                        it.watched = watched
                    self.dirty = True

    # ---------------------------------------------------------------- letters
    def _offsets(self):
        """[(letter, first index)] in grid order."""
        desc = self.sort["dir"] == "desc"
        chars = self.chars or []
        seq = list(reversed(chars)) if desc else list(chars)
        out, n = [], 0
        for c, size in seq:
            out.append((c, n))
            n += size
        return out

    def current_letter(self):
        cur = None
        for c, start in self._offsets():
            if start <= self.focus:
                cur = c
        return cur

    def _jump_letter(self, c):
        off = letter_offset(self.chars or [], c, desc=self.sort["dir"] == "desc")
        if off is not None and self.total:
            self.focus = min(off, self.total - 1)
            self._ensure()
            self.dirty = True

    # ---------------------------------------------------------------- menus
    def open_sort_menu(self):
        sorts = (self.meta or {}).get("sorts") or [
            {"key": "titleSort", "title": "Title", "dir": "asc"},
            {"key": "addedAt", "title": "Date Added", "dir": "desc"}]

        def pick(s):
            if s["key"] == self.sort["key"]:          # the same sort again: other direction
                self.sort = dict(self.sort, dir="asc" if self.sort["dir"] == "desc" else "desc")
            else:
                self.sort = {"key": s["key"], "title": s["title"], "dir": s.get("dir") or "asc"}
            self.query()
        rows = []
        for s in sorts:
            label = s["title"]
            if s["key"] == self.sort["key"]:
                label += "  (%s)" % ("Descending" if self.sort["dir"] == "desc" else "Ascending")
            rows.append((label, lambda s=s: pick(s)))
        self.app.open_menu(rows, title="SORT BY", settings=False)

    def open_filter_menu(self):
        unplayed = self.scr.unplayed_filter()

        def use(filters, label):
            self.filters, self.filter_label = filters, label
            self.query()
        rows = [("All", lambda: use({}, "All")),
                ("Unplayed", lambda: use(dict(unplayed), "Unplayed"))]
        for f in (self.meta or {}).get("filters") or []:
            rows.append((f["title"] + "…", lambda f=f: self._field_menu(f, use)))
        self.app.open_menu(rows, title="FILTER", settings=False)

    def _field_menu(self, f, use):
        def done(values):
            if self.app.top is not self.scr:
                return                               # the user moved on meanwhile
            if not values:
                self.app.toast("No %s values" % f["title"].lower())
                return
            rows = [(t, lambda k=k, t=t: use({f["filter"]: k}, "%s: %s" % (f["title"], t)))
                    for k, t in values]
            self.app.open_menu(rows, title=f["title"].upper(), settings=False)
        self.scr.run_bg(lambda: self.scr.filter_values(f["key"]), done,
                        lambda e: self.app.toast("Can't load %s" % f["title"].lower()))

    def menu_items(self):
        it = self.item() if self.zone == "grid" else None
        rows = []
        if it is not None and it.type in ("movie", "episode"):
            rows.append(("Mark as Unplayed" if it.watched else "Mark as Played",
                         lambda: self.app.mark_watched(it, not it.watched)))
        if it is not None and it.type in MUSIC_TYPES and hasattr(self.scr, "music_rows"):
            rows += self.scr.music_rows(it)
        if it is not None and it.type == "playlist" and hasattr(self.scr, "playlist_rows"):
            rows += self.scr.playlist_rows(it)
        elif it is not None and it.type not in MUSIC_TYPES and hasattr(self.scr, "item_rows"):
            rows += self.scr.item_rows(it)
        if "sort" in self.chips:
            rows.append(("Sort", self.open_sort_menu))
        if "filter" in self.chips:
            rows.append(("Filter", self.open_filter_menu))
        return rows

    # ---------------------------------------------------------------- input
    def handle(self, a):
        """Returns "up" when Up leaves the top of the view, "left" for Left at the start of a
        row, None otherwise."""
        d0, self.dirty = self.dirty, True
        self.press = None
        before = self._target()
        if self.error and a == inputs.OK and self.zone == "grid":
            self.scr.retry()
            return None
        if a == inputs.X and "sort" in self.chips:
            self.open_sort_menu()
            return None
        if a == inputs.Y and "filter" in self.chips:
            self.open_filter_menu()
            return None
        if a in MOVES:
            # A move changes only what the view draws: its rows and the topbar line (the
            # focused title), so the content rows are the band.
            self.press, self.dirty = self.content_rect(), d0
        if self.zone == "chips":
            return self._chips(a)
        if self.zone == "rail":
            return self._rail(a)
        r = self._grid(a)
        tgt = self._target()
        if a in (inputs.UP, inputs.DOWN) and 0 < abs(tgt - before) <= self.geo()["pitch"]:
            self.sanim.start(self.scroll, tgt)
        return r

    def press_band(self):
        """The band of the last handle() (see Screen.press_band); reading it clears it."""
        b, self.press = getattr(self, "press", None), None
        return b

    def content_rect(self):
        """Every row the view and the topbar can draw in: from 8 px above the safe top (the
        topbar line) to the view's bottom."""
        top = max(0, self.scr.safe["top"] - 8)
        return pygame.Rect(0, top, 641, self.geo()["y1"] - top)

    def enter_from_top(self):
        """Down from the tab line: the first line of the view."""
        self.zone = "chips" if self.chips else "grid"
        self.dirty = True

    @property
    def has_rail(self):
        return self.by_title and bool(self.chars)

    def _grid(self, a):
        n, cols = self.total or 0, self.L["cols"]
        if a == inputs.LEFT:
            if self.focus % cols == 0:
                return "left"
            self.focus -= 1
        elif a == inputs.RIGHT:
            if self.focus % cols < cols - 1 and self.focus + 1 < n:
                self.focus += 1
            elif self.has_rail:
                self.zone = "rail"
                self.letter_i = self._letter_index()
        elif a == inputs.UP:
            if self.focus < cols:
                if self.chips:
                    self.zone = "chips"
                    return None
                return "up"
            self.focus -= cols
        elif a == inputs.DOWN:
            if self.focus // cols < (n - 1) // cols:
                self.focus = min(n - 1, self.focus + cols)
        elif a in (inputs.LT, inputs.RT):
            d = -1 if a == inputs.LT else 1
            if self.has_rail:
                offs = self._offsets()
                k = self._letter_index()
                if d < 0 and offs[k][1] < self.focus:
                    k += 1                           # back to the start of this letter first
                k = max(0, min(len(offs) - 1, k + d))
                self.focus = min(n - 1, offs[k][1]) if n else 0
                self.overlay = OVERLAY_FIELDS
            else:
                self.focus = max(0, min(n - 1, self.focus + d * 2 * cols))
        elif a == inputs.OK:
            it = self.item()
            if it is not None:
                self.scr.open_grid_item(it)
        self._ensure()
        return None

    def _letter_index(self):
        cur = self.current_letter()
        return max(0, next((i for i, (c, _s) in enumerate(self._offsets()) if c == cur), 0))

    def _rail(self, a):
        """The A-Z rail on the right (stock Plex's jump bar): Up/Down pick a letter and the
        grid follows after SETTLE fields; Left or A goes back to the grid."""
        offs = self._offsets()
        if not offs:
            self.zone = "grid"
            return None
        self.letter_i = max(0, min(len(offs) - 1, self.letter_i))
        if a in (inputs.UP, inputs.DOWN):
            k = max(0, min(len(offs) - 1, self.letter_i + (1 if a == inputs.DOWN else -1)))
            if k != self.letter_i:
                self.letter_i, self.settle, self.overlay = k, SETTLE, OVERLAY_FIELDS
        elif a in (inputs.LEFT, inputs.OK):
            # Back on the letter of the focused item keeps the focus where it was.
            if offs[self.letter_i][0] != self.current_letter():
                self._jump_letter(offs[self.letter_i][0])
            self.settle = 0
            self.zone = "grid"
        return None

    def _chips(self, a):
        if a in (inputs.LEFT, inputs.RIGHT):
            self.chip = max(0, min(len(self.chips) - 1, self.chip + (1 if a == inputs.RIGHT else -1)))
        elif a == inputs.OK:
            c = self.chips[self.chip]
            if c in self.actions:
                self.actions[c][1]()
            elif c == "sort":
                self.open_sort_menu()
            elif c == "filter":
                self.open_filter_menu()
        elif a == inputs.DOWN:
            if self.total:
                self.zone = "grid"
        elif a == inputs.UP:
            return "up"
        return None

    def update(self):
        if self.settle:
            self.settle -= 1
            if not self.settle:
                offs = self._offsets()
                if offs and self.zone == "rail" and not getattr(self.scr, "in_tabs", False):
                    self._jump_letter(offs[min(self.letter_i, len(offs) - 1)][0])
                self.dirty = True
        if self.overlay:
            self.overlay -= 1
            if not self.overlay:
                self._ov_end = True                  # the jump letter goes
        tgt = self._target()
        moved = False
        if self.sanim.active and self.sanim.to != tgt:
            self.sanim.set(tgt)                      # the target moved (tab line, chips): a cut
        elif not self.sanim.active and self.scroll != tgt:
            self.sanim.set(tgt)                      # a jump, a page load, a new query: a cut
        if self.sanim.active or self.sanim.value != self.scroll:
            self.scroll, moved = self.sanim.value, True
        d, self.dirty = self.dirty, False             # input and worker results set dirty
        if getattr(self.scr, "late_due", lambda: False)():
            d = True                                 # "Loading…" shows after 500 ms
        # Only the scroll (and the jump letter) changed: the grid rows are the band.
        self._band = None if d else (self.grid_rect() if moved or self._ov_end else None)
        d = d or moved or self._ov_end
        self._ov_end = False
        it = self.item()
        if it is not None:                           # the row above, this row and the next two
            g, cols = self.geo(), self.L["cols"]
            row0 = max(0, self.focus // cols - 1) * cols
            for i in range(row0, row0 + 4 * cols):
                p = self.item(i)
                if p is not None:
                    h = g["pw"] if p.type in SQUARE_TYPES else g["ph"]
                    self.app.images.prefetch(p.poster, g["pw"], h,
                                             tokens.GAIN["art" if i == self.focus else "unfocused"])
        return d

    def band(self):
        return self._band

    def grid_rect(self):
        """Everything under the top bar line: the grid, the toolbar, the rail and the tab line
        that slides with them."""
        g = self.geo()
        return pygame.Rect(0, g["clip_top"], 641, g["y1"] - g["clip_top"])

    def focus_line(self):
        """"Title • Year" of the focused item while the focus is in the grid or the rail (the
        topbar shows it in place of the library name), else None."""
        it = self.item()
        if it is None or self.zone == "chips":
            return None
        return it.title + (" • %s" % it.year if it.year and it.type not in ("collection", "artist")
                           else "")

    # ---------------------------------------------------------------- draw
    def geo(self):
        sf = self.scr.safe
        L = self.L
        x0, x1, y1 = sf["left"], 640 - sf["right"], 480 - sf["bottom"]
        clip_top = sf["top"] + 32                   # under the topbar line
        room = x1 - x0 - RAIL_W - 12
        cols = L["cols"]
        pw, ph, pitch = L["pw"], L["ph"], L["pitch"]
        gap = (room - cols * pw) // (cols - 1)
        if gap < 8:                                 # a wide calibrated margin: smaller art
            gap = 8
            pw = (room - (cols - 1) * gap) // cols // 2 * 2
            ph = ph * pw // L["pw"] // 2 * 2
            pitch = L["pitch"] - (L["ph"] - ph)
            pitch = (pitch + 3) // 4 * 4             # scroll steps stay on 4 px
        gap = min(L["gap"], gap)
        top_a = self.top + (60 if self.has_button else 52)   # a 40 px toolbar with a button
        lift = max(0, top_a - clip_top)
        return dict(x0=x0, x1=x1, y1=y1, gap=gap, clip_top=clip_top, top_a=top_a, lift=lift,
                    pw=pw, ph=ph, pitch=pitch, grid_w=cols * pw + (cols - 1) * gap)

    @property
    def has_button(self):
        return any(c in BUTTONS for c in self.chips)

    def _target(self):
        """The scroll in px for the focus: 0 on the first row (the tabs and chips show); then
        the chrome is gone and the focused row is the second row on screen."""
        fr = self.focus // self.L["cols"]
        if fr == 0 or getattr(self.scr, "in_tabs", False) or self.zone == "chips":
            return 0                                 # the chrome shows
        g = self.geo()
        return g["lift"] + (fr - 1) * g["pitch"]

    def focused_grid(self):
        """The focus is on a poster (not on the chips, nor on the screen's tab line)."""
        return self.zone == "grid" and not getattr(self.scr, "in_tabs", False)

    def draw(self, s):
        g = self.geo()
        x0 = g["x0"]
        clip = s.get_clip()
        s.set_clip((0, g["clip_top"], 641, g["y1"] - g["clip_top"]))
        ty = self.top - self.scroll
        # The toolbar (or a count): chips, and Play / Shuffle as buttons.
        # The focused title is in the topbar.
        cx = x0
        if self.chips:
            cy = ty + (4 if self.has_button else 0)
            for k, c in enumerate(self.chips):
                label = (self.actions[c][0]() if c in self.actions else
                         self.sort["title"] if c == "sort" else self.filter_label)
                foc = self.zone == "chips" and k == self.chip
                if c in BUTTONS:
                    w = draw.button_w(label)
                    draw.button(s, (cx, ty, w, tokens.CTRL_H), label, focused=foc,
                                kind="primary" if c == "play" else "secondary")
                else:
                    arrow = None
                    if c == "sort" and c not in self.actions:   # the direction triangle
                        arrow = "down" if self.sort["dir"] == "desc" else "up"
                    w = draw.chip(s, cx, cy, label, foc, arrow=arrow, maxw=180)
                cx += w + tokens.BTN_GAP
        elif self.head and self.total is not None:
            draw.text(s, self.head(self.total), "list", C["text2"], x0, ty + 4)
        top = g["top_a"] - self.scroll
        w = g["x1"] - x0
        if self.error and not self.pages:
            draw.state(s, x0, top, w, self.error_text, "Press A to try again.")
        elif self.total == 0:
            line = self.empty_line() if callable(self.empty_line) else self.empty_line
            draw.state(s, x0, top, w, self.empty, line)
        elif self.total is None:
            if self.scr.late(True, ("grid", id(self))):
                draw.state(s, x0, top, w, "Loading…")
        else:
            self.scr.late(False, ("grid", id(self)))
            self._draw_grid(s, g)
            if self.has_rail:
                self._draw_rail(s, g)
            if self.overlay and self.has_rail:
                self._draw_overlay(s, g)
        s.set_clip(clip)

    def _rail_top(self, g):
        return g["top_a"] - min(self.scroll, g["lift"])

    def _draw_rail(self, s, g):
        offs = self._offsets()
        n = len(offs)
        if not n:
            return
        y0 = self._rail_top(g)
        slots = max(1, (g["y1"] - 8 - y0) // RAIL_PITCH)
        cur = self._letter_index()
        ci = self.letter_i if self.zone == "rail" else cur
        start = max(0, min(n - slots, ci - slots // 2))
        x = g["x1"] - RAIL_W
        for k in range(start, min(n, start + slots)):
            y = y0 + (k - start) * RAIL_PITCH
            foc = self.zone == "rail" and k == self.letter_i
            if foc:
                draw.focus_fill(s, (x, y, RAIL_W, RAIL_PITCH))
            col = C["ink"] if foc else (C["text"] if k == cur else C["dim"])
            draw.text(s, offs[k][0], "tag", col, x, y, w=RAIL_W, align="center")

    def _draw_overlay(self, s, g):
        """The letter of a jump, large, over the middle of the grid."""
        offs = self._offsets()
        k = self.letter_i if self.zone == "rail" else self._letter_index()
        if not 0 <= k < len(offs):
            return
        y0 = self._rail_top(g)
        draw.letter_box(s, (g["x0"] + g["grid_w"] // 2, (y0 + g["y1"]) // 2), offs[k][0])

    def _draw_grid(self, s, g):
        cols, pw, ph, pitch = self.L["cols"], g["pw"], g["ph"], g["pitch"]
        x0, gap, y1 = g["x0"], g["gap"], g["y1"]
        top = g["top_a"] - self.scroll
        first = max(0, (g["clip_top"] - top) // pitch)
        r = first
        while True:
            y = top + r * pitch
            if y >= y1 - 8:
                break
            for c in range(cols):
                i = r * cols + c
                if i >= (self.total or 0):
                    return
                rect = pygame.Rect(x0 + c * (pw + gap), y, pw, ph)
                it = self.item(i)
                if it is None:
                    s.fill(C["track"], rect)
                    continue
                foc = i == self.focus and self.focused_grid()
                gain = tokens.GAIN["art" if foc else "unfocused"]
                if it.type in SQUARE_TYPES:
                    img = self.app.images.get(it.poster, pw, pw, gain)
                    if rect.bottom > y1:                 # a peek row: the art only (2.5)
                        rect.h = pw
                    music_cell(s, it, rect, img, foc, getattr(self, "cell_sub", None))
                    continue
                img = self.app.images.get(it.poster, pw, ph, gain)
                poster(s, it, rect, img)
                if foc:
                    draw.focus_frame(s, rect)
            r += 1


def poster(s, it, r, img):
    """A poster with the title as a stand-in until the image arrives, and its marks."""
    draw.art(s, it, r, img)
    draw.art_marks(s, it, r)


def music_cell(s, it, r, img, foc, sub=None):
    """Square art at the top of the cell and two text lines under it."""
    if sub:
        line2 = sub(it)
    elif it.type == "playlist":
        from . import playlist_count
        line2 = playlist_count(it)
    else:
        line2 = it.artist if it.type in ("album", "track") else ""
    draw.music_cell(s, it, r, img, foc, line2)
