"""The Watchlist screen: one poster grid of the saved movies and shows that are
on the user's servers, newest added first. A title on no server shows nowhere.

A side panel source: on the root screen B and Left on the first column open the panel, LB/RB go
to the next source, Y opens Search. Opened from More when it is not pinned, it is a pushed
screen and B goes back. Sort chip: Date Added, Title, Release Date; filter chip: All, Movies,
TV Shows; both kept for each profile. Every tile comes from the watchlist cache (no wait).
"""
from .. import draw, inputs, log
from ..tokens import C
from . import Screen
from .library import release_key, title_key
from .views import PAGE, GridView

SORTS = ({"key": "added", "title": "Date Added", "dir": "desc"},
         {"key": "title", "title": "Title", "dir": "asc"},
         {"key": "release", "title": "Release Date", "dir": "desc"})
FILTERS = (("all", "All"), ("movie", "Movies"), ("show", "TV Shows"))
L = log.get("watchlist")


def watch_sort(items, sort, desc=None):
    """sort: "added" (newest first), "title" or "release" (the date, else the year; titles with
    no date last). Equal values by title. desc None: the sort's own direction."""
    if desc is None:
        desc = sort != "title"
    by_title = sorted(items, key=lambda i: title_key(i.title))
    if sort == "title":
        return list(reversed(by_title)) if desc else by_title
    if sort == "release":
        dated = [i for i in by_title if release_key(i)]
        dated.sort(key=release_key, reverse=desc)    # stable: equal dates keep title order
        return dated + [i for i in by_title if not release_key(i)]
    return sorted(by_title, key=lambda i: i.extra.get("added_at") or 0, reverse=desc)


def watch_filter(items, filt):
    return [i for i in items if filt in (None, "all") or i.type == filt]


def count_text(n):
    return "%d Title%s" % (n, "" if n == 1 else "s")


class WatchGrid(GridView):
    """The grid over a list in memory: pages fill at once, no worker."""

    def _load(self, page):
        if page in self.pages or page < 0:
            return
        state, items = self.scr.state(), self.scr.items
        if state == "loading":
            self.total = None
        elif state == "error":
            self.error, self.total = "error", None
        else:
            self.total = len(items)
            if page * PAGE < len(items) or page == 0:
                self.pages[page] = items[page * PAGE:(page + 1) * PAGE]
        self.dirty = True

    def query(self, focus=0):
        if getattr(self.scr, "grid", None) is self:
            self.scr._compute()                      # the sort or filter of this query
        super().query(focus)
        self.scr.view_changed()

    def open_filter_menu(self):
        def use(k, label):
            self.filters, self.filter_label = {"type": k}, label
            self.query()
        self.app.open_menu([(label, lambda k=k, label=label: use(k, label))
                            for k, label in FILTERS], title="FILTER", settings=False)

    def focus_line(self):
        """With more than one server in use, the focused title adds its
        server."""
        line = super().focus_line()
        it = self.item()
        src = self.app.sources
        if line and it is not None and src is not None and src.server_count() > 1:
            name = src.server_name(it.server_id)
            if name:
                line += " • " + name
        return line

    def draw(self, s):
        super().draw(s)
        if self.total and not self.error:            # "12 Titles": the titles shown
            g = self.geo()
            clip = s.get_clip()
            s.set_clip((0, g["clip_top"], 641, g["y1"] - g["clip_top"]))
            draw.text(s, count_text(self.total), "list", C["text2"], g["x1"],
                      self.top - self.scroll + 4, align="right")
            s.set_clip(clip)


class WatchlistScreen(Screen):
    name = "WATCHLIST"
    is_watchlist = True

    def __init__(self, app, root=False):
        super().__init__(app)
        self.root = root
        self.wl = app.watchlist
        self.loading, self.error = False, None        # for the app's root screen checks
        view = app.settings.watch_view()
        sort = dict(next(x for x in SORTS if x["key"] == view["sort"]))
        sort["dir"] = "desc" if view["desc"] else "asc"
        self.items = []
        self._ver = None
        self._compute(sort, view["filter"])
        self.grid = WatchGrid(self, None, self.safe["top"] + 32, chips=("sort", "filter"),
                              sort=sort, empty="No titles", error="Can't load your Watchlist",
                              empty_line=self._empty_line)
        self.grid.meta = {"sorts": [dict(x) for x in SORTS], "filters": []}
        self.grid.filters = {"type": view["filter"]}
        self.grid.filter_label = dict(FILTERS)[view["filter"]]
        self._ver = self.wl.version
        self.grid.query()
        self._ready = True                           # later queries are the user's: kept
        self.wl.opened()

    # ---------------------------------------------------------------- data
    def _compute(self, sort=None, filt=None):
        g = getattr(self, "grid", None)
        sort = sort or g.sort
        filt = filt or (g.filters.get("type") if g is not None else "all")
        self.items = watch_filter(watch_sort(self.wl.shown(), sort["key"],
                                             sort["dir"] == "desc"), filt)
        if len(self.items) != getattr(self, "_n", None):
            self._n = len(self.items)
            L.info("watchlist screen: %d titles", self._n)     # a count only, never a title

    def state(self):
        wl = self.wl
        if self.items:
            return "ok"
        if wl.first_load() or (wl.resolving and not wl.found):
            return "loading"
        if wl.error and not wl.complete and not wl.titles:
            return "error"
        return "ok"

    def view_changed(self):
        """A sort or filter change: kept for the profile."""
        if not getattr(self, "_ready", False):
            return
        g, st = self.grid, self.app.settings
        st.set_watch_view(g.sort["key"], g.sort["dir"] == "desc", g.filters.get("type") or "all")
        try:
            self.app.saved(st.save())
        except OSError:
            pass

    def refresh(self):
        """The list or the lookups changed: the grid again, the focus on the same slot."""
        self._compute()
        g = self.grid
        n = len(self.items)
        self._ready = False
        g.query(focus=max(0, min(g.focus, n - 1)))
        self._ready = True

    # ---------------------------------------------------------------- contracts
    def retry(self):
        self.wl.load()

    def unplayed_filter(self):
        return {}

    def open_grid_item(self, it):
        self.app.open_item(it)

    def item_rows(self, it):
        from .actions import item_rows
        return item_rows(self, it)

    def apply_files(self, files, exact=False, reload=True):
        self.wl.apply_files(files, exact)

    def theme_ref(self):
        return None

    @property
    def menu_title(self):
        return "WATCHLIST"

    def _empty_line(self):
        """The how-to line only when the Watchlist itself has no
        title after its load (not when a filter or a missing server copy hides them)."""
        if self.wl.loaded_at is None or self.wl.view_titles():
            return None
        return "Use Add to Watchlist on a movie or show"

    def menu_items(self):
        """Mark as Played / Unplayed, then item_rows (it holds the one Watchlist row), then
        Sort and Filter."""
        g = self.grid
        it = g.item() if g.zone == "grid" and not g.error else None
        rows = []
        if it is not None:
            rows.append(("Mark as Unplayed" if it.watched else "Mark as Played",
                         lambda: self.app.mark_watched(it, not it.watched)))
            rows += self.item_rows(it)
        rows += [("Sort", g.open_sort_menu), ("Filter", g.open_filter_menu)]
        return rows

    # ---------------------------------------------------------------- input
    def handle(self, a):
        g = self.grid
        if a == inputs.BACK:
            if self.root:
                self.app.open_panel()                # B opens the panel, B again exits
            else:
                self.app.pop()
            return
        if a in (inputs.LB, inputs.RB):
            if self.root:
                self.app.next_source(-1 if a == inputs.LB else 1)
            return
        if a == inputs.QUIT:
            self.app.quit()
            return
        g.press_band()
        r = g.handle(a)
        if r == "left" and self.root:
            self.app.open_panel()
            return
        pb = g.press_band()
        if pb is not None:
            self._press = pb

    # ---------------------------------------------------------------- update/draw
    def update(self):
        if self.wl.version != self._ver:
            self._ver = self.wl.version
            self.refresh()
        return self.grid.update()

    def band(self):
        return self.grid.band()

    def image_band(self):
        return self.grid.grid_rect()

    def draw(self, s):
        s.fill(C["bg"])
        self.grid.draw(s)
        line = self.grid.focus_line()
        self.topbar(s, line or "Watchlist", root=self.root, title=bool(line))
