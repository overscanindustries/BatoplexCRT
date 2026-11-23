"""Sources: the one object the UI asks for content.

It holds one provider per server, keyed by the server's machine id. Items carry server_id, so
item calls go to the server they came from (app.provider_for). Image and theme refs from a
PlexProvider are tagged "<server id>|<path>"; an untagged ref (a server whose id was not known
yet) goes to the home server.
"""
import time

from . import log

L = log.get("sources")


class Sources:
    name = "sources"            # the image cache folder

    def __init__(self, providers, home_id=None):
        self.providers = {}
        self.order = []
        for p in providers:
            self.add(p)
        self.home_id = home_id if home_id in self.providers else (self.order[0] if self.order else None)

    def add(self, p):
        key = getattr(p, "server_id", None) or "home"
        if key not in self.providers:
            self.order = self.order + [key]
        # A new dict, not a change in place: worker threads iterate the old one meanwhile.
        self.providers = dict(self.providers, **{key: p})

    @property
    def local_images(self):
        """No provider fetches images (no server is signed in); network art is fetched as bytes."""
        provs = list(self.providers.values())
        return bool(provs) and all(getattr(p, "local_images", False) for p in provs)

    @property
    def home(self):
        return self.providers.get(self.home_id)

    def provider(self, server_id):
        return self.providers.get(server_id) or self._by_late_id(server_id) or self.home

    def _by_late_id(self, server_id):
        # A provider added before /identity told it its id: find it by its current id.
        for p in list(self.providers.values()):
            if server_id and getattr(p, "server_id", None) == server_id:
                return p
        return None

    def provider_for(self, item):
        return self.provider(getattr(item, "server_id", None))

    def strict(self, server_id):
        """The provider of that server, or None when it is not connected: no home fallback, so
        an item id is never sent to a server it does not belong to. An
        item with no server id (a server whose id came late) goes home."""
        if server_id is None:
            return self.home
        return self.providers.get(server_id) or self._by_late_id(server_id)

    def strict_for(self, item):
        p = self.strict(getattr(item, "server_id", None))
        if p is None:
            from .plex import PlexError
            raise PlexError("the server of this item is not connected")
        return p

    # ---------------------------------------------------------------- UI lists
    def home_rows(self):
        """The home server's rows, with Continue Watching merged from every other server that
        has a pinned library: pinned sections only, newest first."""
        rows = self.home.home_rows()
        others = [p for k, p in list(self.providers.items()) if k != self.home_id
                  and hasattr(p, "continue_watching") and p.settings.pins(p.server_id)]
        if not others:
            return rows
        import threading
        got, lock = [], threading.Lock()

        def one(p):
            try:
                pins = set(p.settings.pins(p.server_id))
                items = [i for i in p.continue_watching() if i.section_id in pins]
            except Exception as e:
                L.info("continue watching on %s failed: %s", getattr(p, "server_name", "?"), e)
                return
            with lock:
                got.append(items)
        ts = [threading.Thread(target=one, args=(p,), daemon=True) for p in others]
        for t in ts:
            t.start()
        end = time.monotonic() + 4.0                  # one deadline for all servers
        for t in ts:
            t.join(max(0.0, end - time.monotonic()))  # a slow server is left out of this load
        with lock:
            extra = list(got)
        if not any(extra):
            return rows
        cw = next((r for r in rows if r.key == "continue"), None)
        lists = [list(cw.items) if cw else []] + extra
        merged = []
        for lst in lists:
            # An item without lastViewedAt (an Up Next episode) keeps its place in its own
            # server's list: it takes the time of the item before it, or at the head of the
            # list the time of the first timed item after it.
            times = [i.extra.get("last_viewed") or 0 for i in lst]
            last = next((t for t in times if t), 0)
            for t, i in zip(times, lst):
                if t:
                    last = t
                merged.append((t or last, i))
        merged.sort(key=lambda e: -e[0])               # stable: equal times keep their order
        merged = [i for _t, i in merged]
        from .model import Row
        new = Row("continue", "Continue Watching", merged[:40])
        return [new] + [r for r in rows if r.key != "continue"]

    # ---------------------------------------------------------------- pins (side panel)
    def pinned(self, settings):
        """Pinned libraries as library Items, in panel order. It never makes a request: it runs
        on the UI thread and uses only sections already loaded. The first call for a profile,
        once the home server's sections are loaded, pins its video libraries in today's order.
        A pin whose server is not connected or not loaded yet stays, marked
        unavailable (extra["down"])."""
        from .model import Item
        home = self.home
        if home is None:
            return []
        if self.local_images:                         # no server: its libraries, never saved
            return list(home.libraries(cached=True) or [])
        from .config import SPECIAL_PINS
        every = settings.pin_entries()
        entries = [e for e in every if e.get("server") not in SPECIAL_PINS]
        if not settings.seeded():
            libs = self._cached_libs(home)
            if not libs:
                return []                             # seed later, when the sections are in
            # the Live TV and Watchlist pins stay, after the libraries
            special = [e for e in every if e.get("server") in SPECIAL_PINS]
            settings.set_pins([{"server": l.server_id, "section": l.section_id, "title": l.title}
                               for l in libs] + special)
            try:
                settings.save()
            except OSError:
                pass
            entries = [e for e in settings.pin_entries() if e.get("server") not in SPECIAL_PINS]
        out = []
        for e in entries:
            p = self.providers.get(e["server"]) or self._by_late_id(e["server"])
            libs = self._cached_libs(p) if p is not None else None
            lib = next((l for l in libs or [] if l.section_id == e["section"]), None)
            if lib is None:
                lib = Item(id=str(e["section"]), type="library", title=e.get("title") or "Library",
                           section_id=e["section"], server_id=e["server"])
                lib.extra["down"] = True
            out.append(lib)
        return out

    @staticmethod
    def _cached_libs(p):
        if not hasattr(p, "libraries"):
            return None
        try:
            return p.libraries(cached=True)
        except Exception as ex:                       # settings or data trouble: not fatal
            L.info("libraries of %s failed: %s", getattr(p, "server_id", "?"), ex)
            return None

    # ---------------------------------------------------------------- search
    def search_providers(self, settings):
        """[(server_id, provider)]: the home server first, then each connected server with a
        pinned library (the Continue Watching rule)."""
        out = [(self.home_id, self.home)] if self.home is not None else []
        for k, p in list(self.providers.items()):
            if k != self.home_id and hasattr(p, "search") and settings.pins(
                    getattr(p, "server_id", None) or k):
                out.append((getattr(p, "server_id", None) or k, p))
        return out

    def saver_items(self, settings, libs=4, per=12):
        """Screensaver art: random items from the first pinned libraries that
        are up. Runs on a worker. A server that fails is left out."""
        out = []
        libs_up = [l for l in self.pinned(settings)
                   if not l.extra.get("down") and self.section_type(l) != "artist"]
        for lib in libs_up[:libs]:
            for sort in ("random", "addedAt:desc"):
                try:
                    items, _total = self.library_page(lib, sort, {}, 0, per)
                except Exception as e:
                    L.info("screensaver items from %s (%s) failed: %s", lib.title, sort, e)
                    continue
                out += [i for i in items if i.poster or i.art]
                break
        return out

    def server_count(self):
        return len(self.providers)

    def server_name(self, server_id):
        p = self.providers.get(server_id)
        return getattr(p, "server_name", "") if p is not None else ""

    # Library screen calls go to the library's own server.
    def recommended(self, lib):
        return self.provider_for(lib).recommended(lib)

    def library_meta(self, lib, kind=None):
        p = self.provider_for(lib)
        return p.library_meta(lib, kind) if kind else p.library_meta(lib)

    def library_page(self, lib, sort, filters, start, size=48, kind=None):
        p = self.provider_for(lib)
        if kind:
            return p.library_page(lib, sort, filters, start, size, kind=kind)
        return p.library_page(lib, sort, filters, start, size)

    def first_characters(self, lib, filters=None, kind=None):
        p = self.provider_for(lib)
        return p.first_characters(lib, filters, kind=kind) if kind else p.first_characters(lib, filters)

    # Music calls go to the item's own server.
    def albums(self, artist):
        return self.provider_for(artist).albums(artist)

    def tracks(self, album):
        return self.provider_for(album).tracks(album)

    def station_tracks(self, station):
        return self.provider_for(station).station_tracks(station)

    def artist_tracks(self, artist):
        return self.provider_for(artist).artist_tracks(artist)

    def shuffle_tracks(self, lib, filters=None, n=200):
        return self.provider_for(lib).shuffle_tracks(lib, filters, n)

    def track_info(self, track):
        return self.provider_for(track).track_info(track)

    def collections(self, lib, start, size=48):
        return self.provider_for(lib).collections(lib, start, size)

    def collection_items(self, coll, start, size=48):
        return self.provider_for(coll).collection_items(coll, start, size)

    def filter_values(self, lib, key):
        return self.provider_for(lib).filter_values(key)

    def section_type(self, lib):
        """"movie", "show" or "artist" (music) for a library Item."""
        p = self.provider_for(lib)
        fn = getattr(p, "section_type", None)
        return fn(lib) if fn else (lib.extra.get("section_type") or "movie")

    def unplayed_filter(self, lib):
        return self.provider_for(lib).unplayed_filter(lib)

    def library_rows(self, item):
        return self.provider_for(item).library_rows(item)

    def children_rows(self, item):
        return self.provider_for(item).children_rows(item)

    # ---------------------------------------------------------------- playlists
    def has_playlists(self):
        """A Plex server is in use."""
        return not self.local_images and any(hasattr(p, "playlists")
                                             for p in list(self.providers.values()))

    def playlists(self, kind, settings, deadline=4.0):
        """The Playlists screen: (items, failed, asked) from the home server and each server
        with a pin, merged and sorted by title. failed: how many servers did not answer within
        one deadline. The home server refusing the token raises AuthError (sign-in path)."""
        import threading
        from .plex import AuthError
        provs = [(k, p) for k, p in self.search_providers(settings) if hasattr(p, "playlists")]
        got, errs, lock = [], [], threading.Lock()

        def one(k, p):
            try:
                items = p.playlists(kind)
            except Exception as e:
                L.info("playlists on %s failed: %s", getattr(p, "server_name", "?"), e)
                with lock:
                    errs.append((k, e))
                return
            with lock:
                got.append(items)
        ts = [threading.Thread(target=one, args=kp, daemon=True) for kp in provs]
        for t in ts:
            t.start()
        end = time.monotonic() + deadline
        for t in ts:
            t.join(max(0.0, end - time.monotonic()))
        with lock:
            lists, bad = list(got), list(errs)
        if any(k == self.home_id and isinstance(e, AuthError) for k, e in bad):
            raise AuthError("HTTP 401")
        items = sorted((i for lst in lists for i in lst), key=lambda i: (i.title.lower(), i.server_id or ""))
        return items, len(provs) - len(lists), len(provs)

    def lib_playlists(self, lib, kind):
        """The Playlists tab of one music library: its own server only, with its section id
        (section ids repeat across servers)."""
        return self.strict_for(lib).playlists(kind, section_id=lib.section_id)

    def playlist_items(self, pl, start, size=200):
        return self.strict_for(pl).playlist_items(pl, start, size)

    def playlist_create(self, title, kind, items, server_id):
        p = self.strict(server_id)
        if p is None:
            from .plex import PlexError
            raise PlexError("the server is not connected")
        return p.playlist_create(title, kind, items)

    def playlist_add(self, pl, items):
        return self.strict_for(pl).playlist_add(pl, items)

    def playlist_remove(self, pl, entry):
        return self.strict_for(pl).playlist_remove(pl, entry)

    def playlist_move(self, pl, entry, after):
        return self.strict_for(pl).playlist_move(pl, entry, after)

    def playlist_rename(self, pl, title):
        return self.strict_for(pl).playlist_rename(pl, title)

    def playlist_delete(self, pl):
        return self.strict_for(pl).playlist_delete(pl)

    def playlist(self, pl):
        return self.strict_for(pl).playlist(pl)

    def server_playlists(self, kind, server_id):
        """The pickers of Add to Playlist: the playlists of one type on one server."""
        p = self.strict(server_id)
        if p is None or not hasattr(p, "playlists"):
            from .plex import PlexError
            raise PlexError("the server is not connected")
        return p.playlists(kind)

    # ---------------------------------------------------------------- refs
    def _split(self, ref):
        """(provider, path). A ref tagged with a server that is not connected gives no
        provider: its path means nothing on another server."""
        sid, sep, path = (ref or "").partition("|")
        if sep and sid in self.providers:
            return self.providers[sid], path
        if sep:
            return self._by_late_id(sid), path
        return self.home, ref

    def image(self, ref, w, h):
        p, path = self._split(ref)
        return p.image(path, w, h) if p is not None else None

    def can_theme(self):
        return any(hasattr(p, "theme_url") for p in self.providers.values())

    def theme_url(self, ref):
        p, path = self._split(ref)
        if p is None:
            from .plex import PlexError
            raise PlexError("the server of this theme is not connected")
        return p.theme_url(path)


class Router:
    """The media calls of a music queue whose tracks come from more than one server (a
    playlist with entries of another server): each call goes to the track's own server, and a
    track whose server is not connected fails."""

    def __init__(self, sources):
        self.sources = sources

    def track_info(self, t):
        return self.sources.strict_for(t).track_info(t)

    def timeline(self, t, *a):
        return self.sources.strict_for(t).timeline(t, *a)

    def scrobble(self, t):
        return self.sources.strict_for(t).scrobble(t)
