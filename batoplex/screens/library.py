"""The library screen: Recommended, Library, Collections and Categories tabs
(a music library also has Playlists),
and the pushed grid of a collection or category.

Recommended is the Home rows view under the tab line. Library and Collections are poster
grids. Categories lists the filter fields left and the values of the focused field right;
A on a value pushes a grid of that value.
"""
from .. import draw, inputs, tokens
from ..tokens import C
from . import Screen
from .home import HomeScreen
from .views import GridView

TABS = ("Recommended", "Library", "Collections", "Categories")
MUSIC_TABS = ("Recommended", "Library", "Collections", "Playlists", "Categories")
KINDS = (("artist", "Artists"), ("album", "Albums"))
LIST_PITCH = tokens.ROW_PITCH
FIELD_W = 200                 # the Categories field column; values from x0 + 220
VALUES_SETTLE = 15            # fields on a category before its values load


def music_rows(scr, it):
    """Start menu rows for an artist, album or track."""
    app, src = scr.app, scr.app.sources

    def tracks():
        if it.type == "artist":
            return src.artist_tracks(it)
        if it.type == "album":
            return src.tracks(it)
        return [it]

    def start(shuffle):
        scr.run_bg(tracks, lambda ts: app.play_music(ts, 0, shuffle=shuffle, origin=scr),
                   lambda e: app.toast("Can't load this music"))

    def queue(fn):
        sess = app.music.session_id                 # the session the menu saw
        scr.run_bg(tracks, lambda ts: fn(ts) if scr in app.stack and app.music_accepts(ts, sess)
                   else None, lambda e: app.toast("Can't load this music"))
    rows = [("Play", lambda: start(False)), ("Shuffle", lambda: start(True))]
    if app.music.active and not app.music.live:     # no Plex tracks in a radio queue
        rows += [("Play Next", lambda: queue(app.music.play_next)),
                 ("Add to Queue", lambda: queue(app.music.add))]
    from .actions import item_rows
    return rows + item_rows(scr, it)


def _tab_memory(app):
    if not hasattr(app, "lib_tabs"):
        app.lib_tabs = {}
    return app.lib_tabs


class _LibraryBase(Screen):
    """What the library screen and the pushed grid share."""

    def unplayed_filter(self):
        return self.app.sources.unplayed_filter(self.lib)

    def filter_values(self, key):
        return self.app.sources.filter_values(self.lib, key)

    def open_grid_item(self, it):
        if it.type == "artist":
            self.app.push(GridScreen(self.app, self.lib, "%s • %s" % (self.lib.title, it.title),
                                     artist=it))
        elif it.type == "album":
            from .album import AlbumScreen
            self.app.push(AlbumScreen(self.app, it, library=self.lib.title))
        elif it.type == "collection":
            self.app.push(GridScreen(self.app, self.lib, "%s • %s" % (self.lib.title, it.title),
                                     collection=it))
        elif it.type == "playlist":
            from .playlist import PlaylistScreen
            self.app.push(PlaylistScreen(self.app, it))
        else:
            self.app.open_item(it)

    def music_rows(self, it):
        return music_rows(self, it)

    def playlist_rows(self, pl):
        from .playlist import playlist_rows
        return playlist_rows(self, pl)

    def item_rows(self, it):
        from .actions import item_rows
        return item_rows(self, it)

    def label(self, text):
        src = self.app.sources
        name = src.server_name(self.lib.server_id) if src.server_count() > 1 else ""
        return text + " • " + name if name else text


class LibraryScreen(_LibraryBase):
    name = "LIBRARY"

    def __init__(self, app, lib, root=False):
        super().__init__(app)
        self.lib, self.root = lib, root
        key = (lib.server_id, lib.section_id)
        self.music = app.sources.section_type(lib) == "artist"
        self.tabs = MUSIC_TABS if self.music else TABS
        self.kind = _tab_memory(app).get(key + ("kind",), "artist")
        self.tab = min(_tab_memory(app).get(key, 0), len(self.tabs) - 1)
        self.in_tabs = False
        self.views = {}
        # Categories
        self.fields, self.fi, self.vals, self.vi, self.cat_side = None, 0, {}, 0, 0
        self.fields_busy = self.fields_failed = False
        self.loading, self.error = False, None        # for app.reconnect
        self.cat_settle = 0
        self.cat_top, self.val_top = 0, 0
        self.dirty = True
        self._view()

    # ---------------------------------------------------------------- views
    def _content_top(self):
        return self.safe["top"] + 76

    def _view(self):
        """The view of the current tab, made on first use."""
        t = self.tabs[self.tab]
        if t in self.views:
            return self.views[t]
        app, lib = self.app, self.lib          # app.sources is read at call time (reconnect)
        if t == "Recommended":
            v = HomeScreen(self.app, title=lib.title, loader=lambda: app.sources.recommended(lib),
                           library=lib, root=False, top_extra=40, embedded=True)
        elif t == "Library" and self.music:
            v = GridView(self, lambda so, f, st, n: app.sources.library_page(
                             lib, so, f, st, n, kind=self.kind),
                         self._content_top(), chips=("kind", "sort", "shuffle"),
                         letters=lambda f: app.sources.first_characters(lib, f, kind=self.kind),
                         meta=lambda: app.sources.library_meta(lib, kind=self.kind),
                         actions={"kind": (lambda: dict(KINDS)[self.kind], self._kind_menu),
                                  "shuffle": (lambda: "Shuffle", self._shuffle_library)},
                         music=True)
        elif t == "Library":
            v = GridView(self, lambda so, f, st, n: app.sources.library_page(lib, so, f, st, n),
                         self._content_top(), letters=lambda f: app.sources.first_characters(lib, f),
                         meta=lambda: app.sources.library_meta(lib))
        elif t == "Collections":
            v = GridView(self, lambda so, f, st, n: app.sources.collections(lib, st, n),
                         self._content_top(), chips=(),
                         head=lambda n: "%d Collection%s" % (n, "" if n == 1 else "s"),
                         empty="No collections", music=self.music)
        elif t == "Playlists":
            from .playlists import guarded
            gen = app.src_gen

            def pls(so, f, st, n):
                items = guarded(app, gen, lambda: app.sources.lib_playlists(lib, "audio"))
                return items[st:st + n], len(items)
            v = GridView(self, pls, self._content_top(), chips=(),
                         head=lambda n: "%d Playlist%s" % (n, "" if n == 1 else "s"),
                         empty="No playlists", music=True, error="Can't load playlists",
                         empty_line="Use Add to Playlist in a title's menu")
        else:
            v = None
            self._load_fields()
        self.views[t] = v
        return v

    def _kind_menu(self):
        def use(k):
            if k != self.kind:
                self.kind = k
                _tab_memory(self.app)[(self.lib.server_id, self.lib.section_id, "kind")] = k
                v = self.views.get("Library")
                if v is not None:
                    v.meta = None                     # each kind has its own sorts
                    v.sort = {"key": "titleSort", "title": "Title", "dir": "asc"}
                    v.query()
        self.app.open_menu([(t, lambda k=k: use(k)) for k, t in KINDS], title="SHOW",
                           settings=False)

    def _shuffle_library(self):
        """Shuffle on a music library: 200 random tracks."""
        app, lib = self.app, self.lib
        v = self.views.get("Library")
        f = dict(v.filters) if v is not None else {}
        self.run_bg(lambda: app.sources.shuffle_tracks(lib, f),
                    lambda ts: app.play_music(ts, 0, shuffle=True, origin=self) if ts
                    else app.toast("No tracks"),
                    lambda e: app.toast("Can't load this music"))

    def _load_fields(self):
        if self.fields is not None or self.fields_busy:
            return
        self.fields_busy = True

        def done(meta):
            self.fields_busy = False
            if meta.get("failed"):
                fail(None)
                return
            self.fields = meta.get("filters") or []
            self.cat_settle = 1
            self.dirty = True

        def fail(_e):
            self.fields_busy = False                 # fields stays None: A tries again
            self.fields_failed = True
            self.dirty = True
        self.run_bg(lambda: self.app.sources.library_meta(self.lib), done, fail)


    @property
    def view(self):
        return self._view()

    def _set_tab(self, i):
        self.tab = i % len(self.tabs)
        self._view()

    def _keep_tab(self):
        """The tab is remembered once the user works in it (not when LB/RB pass through)."""
        _tab_memory(self.app)[(self.lib.server_id, self.lib.section_id)] = self.tab

    # ---------------------------------------------------------------- contracts
    def theme_changed(self):
        for v in self.views.values():
            hook = getattr(v, "theme_changed", None)
            if hook is not None:
                hook()

    def apply_files(self, files, exact=False, reload=True):
        for v in self.views.values():
            if isinstance(v, HomeScreen):
                v.apply_files(files, exact, reload)
            elif isinstance(v, GridView):
                for f in files:
                    w = f["watched"] if exact else (f["watched"] or None)
                    v.update_item(f["item_id"], f["position_ms"], w, f.get("server_id"))

    def playlists_changed(self, pl):
        """A playlist write: the Playlists tab loads again."""
        v = self.views.get("Playlists")
        if v is not None:
            v.query(v.focus)

    @property
    def menu_title(self):
        return self.lib.title.upper()

    def menu_items(self):
        v = self.view
        rows = []
        if not self.in_tabs and v is not None:
            rows = list(v.menu_items())
        from .actions import pin_rows                # The one pin menu
        return rows + pin_rows(self.app, (self.lib.server_id, self.lib.section_id),
                               self.lib.title)

    def theme_ref(self):
        return None

    @property
    def library(self):
        return self.lib

    def retry(self):
        """A on an error: a root library finds its server again (its address can change),
        as Home does; a pushed one loads again."""
        if self.loading:
            return                                   # a reconnect is under way
        if self.root:
            self.app.toast("Connecting…")
            self.app.reconnect(self)
        else:
            self.reload()

    def reload(self, keep_focus=False):
        """Load every view again (after a reconnect: the new provider)."""
        self.loading, self.error = False, None
        for t, v in list(self.views.items()):
            if isinstance(v, HomeScreen):
                v.loading, v.error = True, None
                v.reload()
            elif isinstance(v, GridView):
                v.query(v.focus)
        if self.fields is None and not self.fields_busy and self.tabs[self.tab] == "Categories":
            self._load_fields()
        self.dirty = True

    # ---------------------------------------------------------------- input
    def handle(self, a):
        """A move inside the same view on the same tab passes the view's band through (the
        tab line and the topbar are inside a grid's band; a rows view's moves leave them)."""
        v, tab, d0 = self.view, self.tab, self.dirty
        pass_band = isinstance(v, (HomeScreen, GridView))
        if pass_band:
            v.press_band()                           # an old value never counts
        self._handle(a)
        pb = v.press_band() if pass_band and v is self.view and self.tab == tab else None
        if pb is not None:
            self._press, self.dirty = pb, d0

    def _handle(self, a):
        self.dirty = True
        if a == inputs.BACK:
            if self.root:
                self.app.open_panel()
            else:
                self.app.pop()
            return
        if a in (inputs.LB, inputs.RB):
            d = -1 if a == inputs.LB else 1
            if self.root and (self.tab + d) not in range(len(self.tabs)):
                self.app.next_source(d)                # past the last tab, the next source
            else:
                self._set_tab(self.tab + d)
            return
        if self.in_tabs:
            if a in (inputs.LEFT, inputs.RIGHT):
                self._set_tab(max(0, min(len(self.tabs) - 1, self.tab + (1 if a == inputs.RIGHT else -1))))
            elif a in (inputs.DOWN, inputs.OK):
                self.in_tabs = False
                if isinstance(self.view, GridView):
                    self.view.enter_from_top()
            elif a == inputs.QUIT:
                self.app.quit()
            return
        v = self.view
        self._keep_tab()
        if isinstance(v, HomeScreen):
            if v.error and a == inputs.OK:
                self.retry()
                return
            if a == inputs.UP and v.r == 0:
                self.in_tabs = True
                return
            if a == inputs.LEFT and v.row is not None and v.row.focus == 0 and self.root:
                self.app.open_panel()
                return
            v.handle(a)
        elif isinstance(v, GridView):
            r = v.handle(a)
            if r == "up":
                self.in_tabs = True
            elif r == "left" and self.root:
                self.app.open_panel()
        else:
            self._cat_handle(a)

    def _cat_handle(self, a):
        fields = self.fields or []
        if not fields:
            if a == inputs.UP:
                self.in_tabs = True
            elif a == inputs.OK and self.fields is None and not self.fields_busy:
                self.fields_failed = False
                self._load_fields()
            return
        f = fields[self.fi]
        vals = self.vals.get(f["key"])
        if self.cat_side == 0:
            if a in (inputs.UP, inputs.DOWN):
                n = self.fi + (1 if a == inputs.DOWN else -1)
                if n < 0:
                    self.in_tabs = True
                    return
                self.fi = min(len(fields) - 1, n)
                self.vi = 0
                self.cat_settle = VALUES_SETTLE
            elif a in (inputs.RIGHT, inputs.OK) and vals:
                self.cat_side = 1
            elif a == inputs.LEFT and self.root:
                self.app.open_panel()
        else:
            if a == inputs.UP and self.vi == 0:
                self.in_tabs = True
            elif a in (inputs.UP, inputs.DOWN):
                self.vi = max(0, min(len(vals or []) - 1, self.vi + (1 if a == inputs.DOWN else -1)))
            elif a == inputs.LEFT:
                self.cat_side = 0
            elif a == inputs.OK and vals:
                k, t = vals[self.vi]
                self.app.push(GridScreen(self.app, self.lib, "%s • %s" % (self.lib.title, t),
                                         filters={f["filter"]: k}))

    # ---------------------------------------------------------------- update/draw
    def update(self):
        if self.error:                               # a reconnect that failed
            self.app.toast("Can't reach the server")
            self.error = None
        v = self.view
        dirty = v.update() if v is not None else False
        band = v.band() if dirty else None           # the view's band passes through
        if self.cat_settle:
            self.cat_settle -= 1
            if not self.cat_settle:
                if self.fields:
                    self._load_values(self.fields[self.fi])
                self.dirty = True
        d, self.dirty = self.dirty, False
        late = self.late_due()
        self._band = band if not (d or late) else None
        return dirty or d or late

    def band(self):
        return self._band

    def image_band(self):
        v = self.view
        if isinstance(v, HomeScreen):
            return v.image_band()
        if isinstance(v, GridView):
            return v.grid_rect()
        return None

    def _load_values(self, f):
        if f["key"] in self.vals:
            return
        self.vals[f["key"]] = None

        def done(vals):
            self.vals[f["key"]] = vals
            self.dirty = True

        def fail(_e):
            self.vals.pop(f["key"], None)
            self.dirty = True
        self.run_bg(lambda: self.filter_values(f["key"]), done, fail)

    def draw(self, s):
        v = self.view
        if isinstance(v, HomeScreen):
            v.hide_hints = getattr(self, "hide_hints", False)   # a menu or panel is over us
            v.draw(s)                                 # backdrop and rows, under the tabs
        else:
            s.fill(C["bg"])
            if isinstance(v, GridView):
                v.draw(s)
            else:
                self._cat_draw(s)
        line = v.focus_line() if isinstance(v, GridView) and not self.in_tabs else None
        self.topbar(s, line or self.label(self.lib.title), root=self.root, title=bool(line))
        lift = v.scroll if isinstance(v, GridView) else 0
        if lift < 40:                                 # the tabs slide up with the grid
            clip = s.get_clip()
            s.set_clip((0, self.safe["top"] + 32, 641, 448))
            self._tabs(s, -lift)
            s.set_clip(clip)

    def _tabs(self, s, dy=0):
        sf = self.safe
        self.tab_off = tab_line(s, self.tabs, self.tab, self.in_tabs, sf["left"],
                                640 - sf["right"], sf["top"] + 32 + dy, getattr(self, "tab_off", 0))

    def _cat_draw(self, s):
        """The fields as rows (200 wide) left; the values of the focused field
        from x0 + 220; a scroll bar on each column that scrolls."""
        sf = self.safe
        x0, x1, y1 = sf["left"], 640 - sf["right"], 480 - sf["bottom"]
        top = self._content_top()
        w = x1 - x0
        if self.fields is None and self.fields_failed:
            draw.state(s, x0, top, w, "Can't load categories", "Press A to try again.")
            return
        if self.fields is None:
            if self.late(True, "fields"):
                draw.state(s, x0, top, w, "Loading…")
            return
        self.late(False, "fields")
        if not self.fields:
            draw.state(s, x0, top, w, "No categories")
            return
        rows = max(1, (y1 - 8 - top + LIST_PITCH - tokens.ROW_H) // LIST_PITCH)
        self.cat_top = _window(self.fi, self.cat_top, rows)
        n = len(self.fields)
        fw = FIELD_W - (12 if n > rows else 0)
        for k in range(self.cat_top, min(n, self.cat_top + rows)):
            y = top + (k - self.cat_top) * LIST_PITCH
            foc = k == self.fi and self.cat_side == 0 and not self.in_tabs
            # The field whose values show, with the focus on the values: the current bar.
            draw.row(s, (x0, y, fw), self.fields[k]["title"], focused=foc,
                     current=k == self.fi and not foc)
        view_h = rows * LIST_PITCH - (LIST_PITCH - tokens.ROW_H)
        draw.scrollbar(s, x0 + FIELD_W - tokens.SCROLLBAR_W, top, view_h, self.cat_top, rows, n)
        vx = x0 + FIELD_W + 20
        vals = self.vals.get(self.fields[self.fi]["key"])
        if vals is None:
            if self.late(self.fields[self.fi]["key"] in self.vals or bool(self.cat_settle),
                         "values"):
                draw.text(s, "Loading…", "label", C["text2"], vx + tokens.INSET, top + 8)
            return
        self.late(False, "values")
        if not vals:
            draw.text(s, "Nothing here", "label", C["text2"], vx + tokens.INSET, top + 8)
            return
        self.val_top = _window(self.vi, self.val_top, rows)
        vw = x1 - vx - (12 if len(vals) > rows else 0)
        for k in range(self.val_top, min(len(vals), self.val_top + rows)):
            y = top + (k - self.val_top) * LIST_PITCH
            foc = k == self.vi and self.cat_side == 1 and not self.in_tabs
            draw.row(s, (vx, y, vw), vals[k][1], focused=foc)
        draw.scrollbar(s, x1 - tokens.SCROLLBAR_W, top, view_h, self.val_top, rows, len(vals))


class GridScreen(_LibraryBase):
    """A pushed grid: one collection, or one category value of a library."""
    name = "GRID"

    def __init__(self, app, lib, title, collection=None, filters=None, artist=None):
        super().__init__(app)
        self.lib, self.title = lib, title
        top = self.safe["top"] + 32
        music = app.sources.section_type(lib) == "artist"   # square cells for music items
        if artist is not None:
            # The artist's albums, newest first, with Play and Shuffle chips.
            self.artist = artist

            def albums(so, f, st, n):
                items = app.sources.albums(artist)
                return items[st:st + n], len(items)

            def play(shuffle):
                self.run_bg(lambda: app.sources.artist_tracks(artist),
                            lambda ts: app.play_music(ts, 0, shuffle=shuffle, origin=self)
                            if ts else None,
                            lambda e: app.toast("Can't load this music"))
            self.grid = GridView(self, albums, top, chips=("play", "shuffle"), music=True,
                                 actions={"play": (lambda: "Play", lambda: play(False)),
                                          "shuffle": (lambda: "Shuffle", lambda: play(True))})
            self.grid.cell_sub = lambda it: str(it.year) if it.year else ""   # not the artist again
        elif collection is not None:
            self.grid = GridView(self, lambda so, f, st, n: app.sources.collection_items(collection, st, n),
                                 top, chips=(), head=lambda n: "%d item%s" % (n, "" if n == 1 else "s"),
                                 music=music)
        else:
            base = dict(filters or {})
            self.grid = GridView(self, lambda so, f, st, n: app.sources.library_page(lib, so, dict(base, **f), st, n),
                                 top, chips=("sort",), meta=lambda: app.sources.library_meta(lib),
                                 music=music,
                                 letters=lambda f: app.sources.first_characters(lib, dict(base, **f)))

    def apply_files(self, files, exact=False, reload=True):
        for f in files:
            w = f["watched"] if exact else (f["watched"] or None)
            self.grid.update_item(f["item_id"], f["position_ms"], w, f.get("server_id"))

    @property
    def menu_title(self):
        return self.title.upper()

    def menu_items(self):
        return self.grid.menu_items()

    def retry(self):
        self.grid.query(self.grid.focus)

    def handle(self, a):
        if a == inputs.BACK:
            self.app.pop()
            return
        self.grid.press_band()
        self.grid.handle(a)
        pb = self.grid.press_band()
        if pb is not None:
            self._press = pb

    def update(self):
        return self.grid.update()

    def band(self):
        return self.grid.band()

    def image_band(self):
        return self.grid.grid_rect()

    def draw(self, s):
        s.fill(C["bg"])
        self.grid.draw(s)
        line = self.grid.focus_line()
        self.topbar(s, line or self.label(self.title), title=bool(line))


PERSON_HEAD = 72                 # The header over the chip line
PERSON_PHOTO = 64
PERSON_SORTS = ({"key": "originallyAvailableAt", "title": "Release Date", "dir": "desc"},
                {"key": "titleSort", "title": "Title", "dir": "asc"})
KIND_LABELS = {"actor": "Actor", "director": "Director", "writer": "Writer"}


def title_key(title):
    """Title order: case ignored, a leading The / A / An ignored."""
    t = (title or "").strip().lower()
    for art in ("the ", "a ", "an "):
        if t.startswith(art):
            return t[len(art):]
    return t


def release_key(it):
    return it.extra.get("date") or ("%04d" % it.year if it.year else "")


def sort_titles(items, sort):
    """sort: "originallyAvailableAt[:desc]" or "titleSort[:desc]". Release Date: the full
    date (else the year); equal dates by title; titles with no date at the end."""
    key, _, d = (sort or "").partition(":")
    desc = d == "desc"
    if key == "titleSort":
        return sorted(items, key=lambda i: title_key(i.title), reverse=desc)
    dated = sorted((i for i in items if release_key(i)), key=lambda i: title_key(i.title))
    dated.sort(key=release_key, reverse=desc)        # stable: equal dates keep title order
    undated = sorted((i for i in items if not release_key(i)), key=lambda i: title_key(i.title))
    return dated + undated


class PersonScreen(_LibraryBase):
    """One person's movies and shows on the person's server, in one poster grid
    with a Release Date / Title sort chip. The list is small: it loads once and sorts here."""
    name = "PERSON"

    def __init__(self, app, person):
        super().__init__(app)
        import threading
        from ..model import Item
        self.person = person
        self.lib = Item(id="", type="library", title=person.title, server_id=person.server_id)
        src = app.sources
        self.prov = src.strict(person.server_id) if src is not None else None
        self._all = None
        self._lock = threading.Lock()
        self._round = None
        top = self.safe["top"] + 32 + PERSON_HEAD
        self.grid = GridView(self, self._fetch, top, chips=("sort",),
                             meta=lambda: {"sorts": [dict(x) for x in PERSON_SORTS],
                                           "filters": []},
                             sort=dict(PERSON_SORTS[0]), error="Can't load these titles")

    def _fetch(self, sort, filters, start, size):
        """On a worker: the merged list (once), sorted, one page of it."""
        with self._lock:
            if self._all is None:
                if self.prov is None or not hasattr(self.prov, "person_titles"):
                    from ..plex import PlexError
                    raise PlexError("the server of this person is not connected")
                self._all = self.prov.person_titles(self.person)
            items = sort_titles(self._all, sort)
        return items[start:start + size], len(items)

    def apply_files(self, files, exact=False, reload=True):
        for f in files:
            w = f["watched"] if exact else (f["watched"] or None)
            self.grid.update_item(f["item_id"], f["position_ms"], w, f.get("server_id"))

    @property
    def menu_title(self):
        return self.person.title

    def menu_items(self):
        return self.grid.menu_items()

    def retry(self):
        with self._lock:
            self._all = None
        self.grid.query(self.grid.focus)

    def handle(self, a):
        if a == inputs.BACK:
            self.app.pop()
            return
        self.grid.handle(a)

    def update(self):
        d = self.grid.update()
        p = self.person
        if p.poster and self._round is None:
            img = self.app.images.get(p.poster, PERSON_PHOTO, PERSON_PHOTO, tokens.GAIN["art"])
            if img is not None:
                from .. import avatars
                self._round = avatars.round_surface(img, PERSON_PHOTO)
                return True
        return d

    def band(self):
        return self.grid.band()

    def image_band(self):
        return None if self._round is None and self.person.poster else self.grid.grid_rect()

    def draw(self, s):
        s.fill(C["bg"])
        self.grid.draw(s)
        g = self.grid.geo()
        clip = s.get_clip()
        s.set_clip((0, g["clip_top"], 641, g["y1"] - g["clip_top"]))
        self._header(s, g["x0"], self.safe["top"] + 32 - self.grid.scroll, g["x1"] - g["x0"])
        s.set_clip(clip)
        line = self.grid.focus_line()
        self.topbar(s, line or self.person.title, title=bool(line))

    def _header(self, s, x, y, w):
        """The round photo, the name, "Actor • 12 titles"; it scrolls away with the chips."""
        d = PERSON_PHOTO
        if self._round is not None:
            s.blit(self._round, (x, y))
        else:
            import pygame
            pygame.draw.circle(s, C["track"], (x + d // 2, y + d // 2), d // 2)
            ini = "".join(p[0] for p in self.person.title.split()[:2]).upper()
            draw.text(s, ini, "heading", C["text2"], x, y + d // 2 - 14, w=d, align="center")
        tx = x + d + 12
        draw.text(s, self.person.title, "heading", C["text"], tx, y + 2, w=w - d - 12)
        sub = KIND_LABELS.get(self.person.extra.get("kind"), "Actor")
        n = self.grid.total
        if n:
            sub += " • %d title%s" % (n, "" if n == 1 else "s")
        draw.text(s, sub, "small", C["text2"], tx, y + 32, w=w - d - 12)


def tab_line(s, tabs, sel_i, focused, x0, x1, y, off=0):
    """The tab line (draw.tabs). When it is wider than the safe width (five
    music tabs at wide margins) it scrolls so the selected tab shows
    whole, with a triangle at a cut edge. Returns the new scroll offset."""
    return draw.tabs(s, list(tabs), sel_i, focused, x0, x1, y, off)


def _window(i, top, n):
    if i < top:
        return i
    if i >= top + n:
        return i - n + 1
    return top
