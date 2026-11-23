"""Playlists: the side panel's Playlists row. Video and Music tabs, each a
grid of square composites with the music cell (title, then "12 items • 48m", or the server
name when the list has more than one server). The playlists come from the home server and
each server with a pin, merged by title. A on a playlist opens it; Start: Play, Shuffle.
"""
from .. import inputs
from ..plex import AuthError, PlexError
from ..tokens import C
from . import Screen, playlist_count
from .library import tab_line
from .views import GridView

TABS = (("video", "Video"), ("audio", "Music"))


def guarded(app, gen, fn):
    """fn() on a worker. An expired token goes to the sign-in path (Screen.run_bg does not
    route AuthError); a late one, after a sign-out, is dropped."""
    try:
        return fn()
    except AuthError:
        app.post(lambda: gen == app.src_gen and app.auth_lost())
        raise


class PlaylistsScreen(Screen):
    name = "PLAYLISTS"

    def __init__(self, app):
        super().__init__(app)
        self.tab = getattr(app, "pl_tab", 0)        # the app keeps the last tab
        self.in_tabs = False
        self.views, self.lists = {}, {}
        self.gen = app.src_gen
        self.dirty = True
        self._view()

    # ---------------------------------------------------------------- views
    def _content_top(self):
        return self.safe["top"] + 76

    def _view(self):
        kind = TABS[self.tab][0]
        if kind in self.views:
            return self.views[kind]
        app, gen = self.app, self.gen

        def fetch(so, f, st, n):
            if st == 0 or kind not in self.lists:
                items, failed, asked = guarded(app, gen, lambda: app.sources.playlists(kind, app.settings))
                if not items and asked and failed == asked:
                    raise PlexError("no server answered")
                self.lists[kind] = items
            items = self.lists[kind]
            return items[st:st + n], len(items)
        v = GridView(self, fetch, self._content_top(), chips=(),
                     head=lambda n: "%d Playlist%s" % (n, "" if n == 1 else "s"),
                     empty="No playlists", music=True, error="Can't load playlists",
                         empty_line="Use Add to Playlist in a title's menu")
        v.cell_sub = self._cell_sub
        self.views[kind] = v
        return v

    def _cell_sub(self, it):
        items = self.lists.get(it.extra.get("kind")) or []
        if len({i.server_id for i in items}) > 1:
            return self.app.sources.server_name(it.server_id)
        return playlist_count(it)

    @property
    def view(self):
        return self._view()

    def reload(self):
        """A playlist changed: load every tab again, keep the focus."""
        self.lists = {}
        for v in self.views.values():
            v.query(v.focus)
        self.dirty = True

    def retry(self):
        self.view.query(self.view.focus)

    def playlists_changed(self, pl):
        self.reload()

    def on_resume(self, result=None):
        self.dirty = True

    def sources_changed(self):
        """A server joined: its playlists join the list."""
        self.reload()

    # ---------------------------------------------------------------- grid contract
    def open_grid_item(self, it):
        from .playlist import PlaylistScreen
        self.app.push(PlaylistScreen(self.app, it))

    def playlist_rows(self, pl):
        from .playlist import playlist_rows
        return playlist_rows(self, pl)

    def unplayed_filter(self):
        return {}

    @property
    def menu_title(self):
        it = self.view.item() if not self.in_tabs else None
        return it.title if it is not None else "PLAYLISTS"

    def menu_items(self):
        return [] if self.in_tabs else list(self.view.menu_items())

    # ---------------------------------------------------------------- input
    def handle(self, a):
        self.dirty = True
        if a == inputs.BACK:
            self.app.back()
            return
        if a in (inputs.LB, inputs.RB):
            self._set_tab(self.tab + (-1 if a == inputs.LB else 1))
            return
        if self.in_tabs:
            if a in (inputs.LEFT, inputs.RIGHT):
                self._set_tab(self.tab + (1 if a == inputs.RIGHT else -1))
            elif a in (inputs.DOWN, inputs.OK):
                self.in_tabs = False
                self.view.enter_from_top()
            elif a == inputs.QUIT:
                self.app.quit()
            return
        if self.view.handle(a) == "up":
            self.in_tabs = True

    def _set_tab(self, i):
        self.tab = max(0, min(len(TABS) - 1, i))
        self.app.pl_tab = self.tab
        self._view()

    # ---------------------------------------------------------------- update/draw
    def update(self):
        d = self.view.update()
        dd, self.dirty = self.dirty, False
        self._band = self.view.band() if d and not dd else None   # the grid scroll
        return d or dd

    def band(self):
        return self._band

    def image_band(self):
        return self.view.grid_rect()

    def draw(self, s):
        v = self.view
        s.fill(C["bg"])
        v.draw(s)
        line = v.focus_line() if not self.in_tabs else None
        self.topbar(s, line or "Playlists", title=bool(line))
        if v.scroll < 40:                            # the tabs slide up with the grid
            sf = self.safe
            clip = s.get_clip()
            s.set_clip((0, sf["top"] + 32, 641, 448))
            tab_line(s, [t for _k, t in TABS], self.tab, self.in_tabs, sf["left"],
                     640 - sf["right"], sf["top"] + 32 - v.scroll)
            s.set_clip(clip)
