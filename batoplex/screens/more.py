"""More: every library of every server, to pin, unpin and order.

Opens from the last row of the side panel, with the focus on the first library. One group
per server first: the server name, with "Shared" for a server the account does
not own. A row is a library, with "Pinned" on the right when it is in the side panel. A server
that does not answer shows one "Unavailable" row. Then, each a group with no heading and a gap
between: "Watchlist" (A opens it, X pins it) for a profile with a Watchlist;
Channels: "Set Up Channels" before setup, then "Channels" (A opens the guide, X pins it) and
"Manage Channels"; "Add Radio Stations": the way in while no station exists.
Menu: the pin menu. Same geometry and draw code as Settings (listlayout).
"""
from .. import draw, inputs
from ..config import LIVE_ENTRY, LIVE_PIN, WATCH_ENTRY, WATCH_PIN
from . import Screen, listlayout


class _LivePin:
    """The Live TV pin where More expects a library (server_id, section_id, title)."""
    server_id, section_id, title = LIVE_PIN, LIVE_ENTRY["section"], "Channels"


LIVE_ITEM = _LivePin()


class _WatchPin:
    """The Watchlist pin where More expects a library."""
    server_id, section_id, title = WATCH_PIN, WATCH_ENTRY["section"], "Watchlist"


WATCH_ITEM = _WatchPin()


class MoreScreen(Screen):
    ambient = True                # The idle ribbon may show here
    name = "MORE"
    menu_title = "MORE"

    def __init__(self, app):
        super().__init__(app)
        self.focus = 0
        self.top = 0
        self.libs = {}            # server id -> [library Item], or None while it loads
        self.focus_key = None     # the focused library, kept when groups above it load
        self.moved = False        # the user moved the focus: no more first-library pick
        self._load()

    def sources_changed(self):
        """A server connected while More is open: load its libraries."""
        self._load()

    # ---------------------------------------------------------------- data
    def _servers(self):
        """[(server id, name, owned, provider or None)] in Sources order, then the servers of
        the account that are not connected."""
        src = self.app.sources
        out = []
        for sid in src.order:
            p = src.providers.get(sid)
            name = getattr(p, "server_name", "")
            if not name and sid == src.home_id:
                name = self.app.secrets.get("server_name") or ""
            name = name or "Server"
            owned = sid == src.home_id or bool(getattr(p, "owned", True))
            out.append((sid, name, owned, p))
        known = set(src.order) | {getattr(p, "server_id", None) for p in src.providers.values()}
        for sid, srv in sorted((self.app.secrets.v.get("servers") or {}).items(),
                               key=lambda kv: (kv[1] or {}).get("name") or ""):
            if sid not in known and sid != "legacy":   # "legacy": the home server, old file
                out.append((sid, (srv or {}).get("name") or "Server", bool((srv or {}).get("owned")),
                            None))
        return out

    def _load(self):
        """Loaded sections are used at once; a server without them loads on a worker. Servers
        already listed or loading are left alone."""
        for sid, _n, _o, p in self._servers():
            if p is None or not hasattr(p, "libraries") or sid in self.libs:
                continue
            try:
                libs = p.libraries(cached=True)
            except Exception:
                libs = None
            if libs is not None:
                self.libs[sid] = libs
                continue
            self.libs[sid] = None
            p.sections_busy = True                   # the panel does not load them again

            def done(libs, sid=sid, p=p):
                p.sections_busy = False
                self.libs[sid] = libs or []
                self._tell_others()

            def fail(_e, sid=sid, p=p):
                p.sections_busy = False
                self.libs.pop(sid, None)             # unavailable; the next connect tries again
            self.run_bg(p.libraries, done, fail)

    def _tell_others(self):
        """Sections arrived: a side panel on the stack shows those pins as available now."""
        for scr in list(self.app.stack):
            if scr is not self and hasattr(scr, "sources_changed"):
                scr.sources_changed()

    def rows(self):
        """(kind, key, label, value); kind: head | lib | info | gap | watchpin | livepin | live |
        add. The servers and their libraries first, then Watchlist, Channels
        and Radio, each a group with no heading, a gap between groups."""
        pins = {(e["server"], e["section"]) for e in self._entries()}
        local = self.app.sources.local_images
        out = []
        for sid, name, owned, _p in self._servers():
            out.append(("head", sid, name if owned else "%s • Shared" % name, ""))
            libs = self.libs.get(sid, "down")
            if libs == "down":
                out.append(("info", ("server", sid), "Unavailable", ""))
            elif libs is None:
                out.append(("info", ("server", sid), "Loading…", ""))
            elif not libs:
                out.append(("info", ("server", sid), "No libraries", ""))
            else:
                for l in libs:
                    on = local or (l.server_id, l.section_id) in pins
                    out.append(("lib", l, l.title, "Pinned" if on else ""))
        if local:                               # no server: no network request
            return out
        groups = []
        if getattr(self.app, "watch_visible", bool)():
            on = (WATCH_PIN, WATCH_ENTRY["section"]) in pins
            groups.append([("watchpin", "watchlist", "Watchlist", "Pinned" if on else "")])
        lu = getattr(self.app, "lineup", None)
        n = lu.count() if lu is not None else 0     # Channels, opt-in
        if n:
            on = (LIVE_PIN, LIVE_ENTRY["section"]) in pins
            groups.append([("livepin", "livetv", "Channels", "Pinned" if on else ""),
                           ("live", "channels", "Manage Channels", str(n))])
        else:
            groups.append([("live", "livetv_setup", "Set Up Channels", "")])
        groups.append([("add", "add_stations", "Add Radio Stations", "")])
        for g in groups:
            out += ([("gap", ("gap", len(out)), "", "")] if out else []) + g
        return out

    def _focused(self):
        r = self._focused_row()
        return r[1] if r is not None and r[0] == "lib" else None

    def _pin_target(self):
        """The focused row's pin as an object with server_id, section_id and title (a library,
        or the Live TV pin), or None."""
        r = self._focused_row()
        if r is None:
            return None
        if r[0] == "lib":
            return r[1]
        if r[0] == "livepin":
            return LIVE_ITEM
        if r[0] == "watchpin":
            return WATCH_ITEM
        return None

    def _focused_row(self):
        its = listlayout.items(self.rows())
        if not its:
            return None
        if not self.moved:              # Open on the first library, else an action
            first = next((r for r in its if r[0] == "lib"), None) or \
                next((r for r in its if r[0] in ("watchpin", "livepin", "live", "add")), None)
            if first is not None:
                self.focus_key = self._key(first)
        if self.focus_key is not None:               # rows above may have changed
            k = next((i for i, r in enumerate(its) if self._key(r) == self.focus_key), None)
            if k is not None:
                self.focus = k
        self.focus = max(0, min(self.focus, len(its) - 1))
        return its[self.focus]

    def _set_focus(self, i):
        self.moved = True
        its = listlayout.items(self.rows())
        self.focus = max(0, min(len(its) - 1, i))
        r = its[self.focus] if its else None
        self.focus_key = self._key(r) if r else None

    @staticmethod
    def _key(r):
        """A row's identity: (server, section) for a library, ("server", id) for a status row."""
        return (r[1].server_id, r[1].section_id) if r[0] == "lib" else r[1]

    # ---------------------------------------------------------------- pins
    def _entries(self):
        st = self.app.settings
        if not st.seeded():
            self.app.sources.pinned(st)              # seed the defaults first
        return st.pin_entries()

    def toggle_pin(self, lib):
        from .actions import toggle_pin
        toggle_pin(self.app, (lib.server_id, lib.section_id), lib.title)

    def menu_items(self):
        """The one pin menu order."""
        lib = self._pin_target()
        if lib is None:
            return []
        from .actions import pin_rows
        return pin_rows(self.app, (lib.server_id, lib.section_id), lib.title)

    # ---------------------------------------------------------------- input
    def handle(self, a):
        self._focused()
        if a == inputs.BACK:
            self.app.back()                          # from the side panel: back to it
        elif a in (inputs.UP, inputs.DOWN):
            self._set_focus(self.focus + (1 if a == inputs.DOWN else -1))
        elif a in (inputs.LT, inputs.RT):
            self._set_focus(self.focus + (6 if a == inputs.RT else -6))
        elif a == inputs.OK:
            r = self._focused_row()
            if r is not None and r[0] == "lib":
                self.app.open_item(r[1])
            elif r is not None and r[0] == "livepin":
                self.app.open_guide()
            elif r is not None and r[0] == "watchpin":
                from .watchlist import WatchlistScreen
                self.app.push(WatchlistScreen(self.app))    # not a root: B comes back here
            elif r is not None and r[0] == "live":
                lu = getattr(self.app, "lineup", None)
                if lu is not None and lu.count():
                    from .chsetup import ManageScreen
                    self.app.push(ManageScreen(self.app))   # Channels opens Manage
                else:
                    self.app.open_livetv_setup()
            elif r is not None and r[0] == "add":
                from .stations import StationsScreen
                self.app.push(StationsScreen(self.app))
        elif a == inputs.X:
            lib = self._pin_target()
            if lib is not None:
                self.toggle_pin(lib)
        elif a == inputs.QUIT:
            self.app.quit()

    def update(self):
        return self.late_due()        # input and worker results redraw in the app

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "More")
        self._focused()                              # resolves the focus by key (a side effect)
        rows = self.rows()
        listlayout.draw_list(self, s, rows, self.focus,
                             enabled=lambda r: r[0] in ("lib", "add", "live", "livepin",
                                                        "watchpin"))
