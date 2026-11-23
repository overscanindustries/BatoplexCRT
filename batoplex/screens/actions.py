"""Item actions of the Start and More menus: Play Next, Add to Queue and Add to
Playlist. Each menu adds item_rows() after its own rows.

Video: Play Next and Add to Queue fill the local video queue: movies and episodes of
one server, in memory; "Play Queue (n)" opens it. Music: Play Next and Add to Queue go to the
music queue while a music session plays (music_rows), and only for tracks of its servers.

Playlist names are user text: toasts that carry one are not logged (app.toast private).
"""
import dataclasses
import itertools

from .. import log
from ..plex import AuthError

L = log.get("actions")

VIDEO = ("movie", "episode")
VIDEO_CONTAINERS = ("show", "season")
MUSIC = ("artist", "album", "track")
_occ = itertools.count(1)


def kind_of(it):
    """"video" or "audio": the playlist type an item can join, or None."""
    if it.type in VIDEO + VIDEO_CONTAINERS:
        return "video"
    if it.type in MUSIC:
        return "audio"
    return None


def can_playlist(app, it):
    """Only a provider with playlists gets the rows."""
    src = app.sources
    if src is None or getattr(src, "local_images", False) or kind_of(it) is None:
        return False
    p = src.strict(it.server_id) if hasattr(src, "strict") else None
    return p is not None and hasattr(p, "playlists")


def can_watch(app, it):
    """The Watchlist row and button. A movie or show whose guid gives a
    Discover key, a profile with a Watchlist (a token, not refused), a known state."""
    wl = getattr(app, "watchlist", None)
    if it is None or wl is None or not getattr(app, "watch_visible", bool)():
        return False
    return wl.item_state(it) is not None


def watch_row(app, it):
    """The one owner of the Watchlist menu row: ("Add to Watchlist" | "Remove from Watchlist",
    action) or None."""
    if not can_watch(app, it):
        return None
    on = app.watchlist.item_state(it)
    return ("Remove from Watchlist" if on else "Add to Watchlist",
            lambda: app.watchlist.set(it, not on))


def item_rows(scr, it, watch=True):
    """The rows for one item, after the menu's own rows: the Watchlist row first (watch=False:
    the detail page, whose button has it), then Play Next, Add to Queue, Add to Playlist."""
    app = scr.app
    if it is None:
        return []
    wr = watch_row(app, it) if watch else None
    rows = [wr] if wr is not None else []
    if not can_playlist(app, it):
        return rows
    if it.type in VIDEO:
        rows += [("Play Next", lambda: queue_video(app, [it], first=True)),
                 ("Add to Queue", lambda: queue_video(app, [it]))]
    rows.append(("Add to Playlist", lambda: add_to_playlist(scr, it)))
    return rows


# ---------------------------------------------------------------- menu order
# The one row order of every item menu, by group. A label not listed is the screen's own row
# (Sort, Filter, the pin rows, Move, Remove…, Rename, Sleep Timer, Stop…): it goes last. The app
# rows (Now Playing, Play Queue, Settings) come after these, from app.open_menu.
ORDER = (
    ("play", ("Play", "Shuffle", "Play from Start", "Play Trailer")),
    ("mark", ("Mark as Played", "Mark as Unplayed", "Mark Season as Played")),
    ("watch", ("Add to Watchlist", "Remove from Watchlist")),
    ("queue", ("Play Next", "Add to Queue")),
    ("playlist", ("Add to Playlist",)),
    ("goto", ("Go to Show", "Go to Album", "Go to Artist", "Show Details", "Guide")),
    ("details", ("Details",)),
)
_RANK = {label: i for i, (_g, labels) in enumerate(ORDER) for label in labels}
OWN = len(ORDER)


def rank(label):
    """The index of a row's group in ORDER; OWN for a screen's own row."""
    return _RANK.get(label, OWN)


def ordered(rows):
    """Item rows in the one order: a stable sort by group, so rows of one group keep the
    order the screen gave (Mark as Played before Mark Season as Played; Unpin, Move Up)."""
    return sorted(rows, key=lambda r: rank(r[0]))


# ---------------------------------------------------------------- pins
def _pin_state(app):
    """Returns (entries, hidden). A special pin whose row does not show now (no channel,
    no Watchlist for this profile) is taken out and keeps its index (it is hidden, not
    unpinned), so Move Up / Move Down step over the pins the user sees."""
    from ..config import LIVE_PIN, WATCH_PIN
    st = app.settings
    entries = list(st.pin_entries())
    lu = getattr(app, "lineup", None)
    vis = getattr(lu, "visible", None) or getattr(lu, "count", None)
    shown = {LIVE_PIN: vis is not None and bool(vis()),
             WATCH_PIN: bool(getattr(app, "watch_visible", bool)())}
    hidden = []
    for k in range(len(entries) - 1, -1, -1):
        pin = entries[k].get("server")
        if pin in shown and not shown[pin]:
            hidden.insert(0, (k, entries.pop(k)))
    return entries, hidden


def _save_pins(app, entries, hidden, after=None):
    new = list(entries)
    for k, e in hidden:                          # ascending: each goes back to its index
        new.insert(min(k, len(new)), e)
    st = app.settings
    st.set_pins(new)
    try:
        app.saved(st.save())
    except OSError:
        pass
    if after is not None:
        after()
    app.pins_changed()


def _entry(key, title):
    from ..config import LIVE_ENTRY, LIVE_PIN, WATCH_ENTRY, WATCH_PIN
    if key[0] == LIVE_PIN:
        return dict(LIVE_ENTRY)
    if key[0] == WATCH_PIN:
        return dict(WATCH_ENTRY)
    return {"server": key[0], "section": key[1], "title": title}


def toggle_pin(app, key, title, after=None):
    """Pin at the end, or unpin. key: (server, section); the special pins use their entry."""
    if getattr(app.sources, "local_images", False):
        return                                   # no server: nothing to pin
    entries, hidden = _pin_state(app)
    key = tuple(key)
    h = [x for x in hidden if (x[1]["server"], x[1]["section"]) == key]
    if h:                                        # pinned but hidden now (More shows it)
        hidden = [x for x in hidden if x not in h]
        _save_pins(app, entries, hidden, after)
        return
    i = next((k for k, e in enumerate(entries) if (e["server"], e["section"]) == key), None)
    if i is None:
        entries.append(_entry(key, title))
    else:
        entries.pop(i)
    _save_pins(app, entries, hidden, after)


def pin_rows(app, key, title, after=None):
    """The one pin menu (side panel, More, a library): Pin or Unpin, Move Up, Move Down.
    [] with no server."""
    if getattr(app.sources, "local_images", False):
        return []
    entries, hidden = _pin_state(app)
    key = tuple(key)
    i = next((k for k, e in enumerate(entries) if (e["server"], e["section"]) == key), None)
    if i is None and any((e["server"], e["section"]) == key for _k, e in hidden):
        return [("Unpin", lambda: toggle_pin(app, key, title, after))]   # no moves
    if i is None:
        return [("Pin", lambda: toggle_pin(app, key, title, after))]

    def move(d):
        new, hid = _pin_state(app)
        j = next((k for k, e in enumerate(new) if (e["server"], e["section"]) == key), None)
        if j is None or not 0 <= j + d < len(new):
            return
        new[j], new[j + d] = new[j + d], new[j]
        _save_pins(app, new, hid, after)
    rows = [("Unpin", lambda: toggle_pin(app, key, title, after))]
    if i > 0:
        rows.append(("Move Up", lambda: move(-1)))
    if i < len(entries) - 1:
        rows.append(("Move Down", lambda: move(1)))
    return rows


# ---------------------------------------------------------------- the local video queue
def queue_entry(it):
    """A copy of the item for the queue, with its own occurrence id (an item can be in the
    queue twice)."""
    c = dataclasses.replace(it, extra=dict(it.extra))
    c.extra.pop("pl_item", None)
    c.extra["occ"] = "q:%d" % next(_occ)
    return c


def queue_video(app, items, first=False):
    """Add to Queue (at the end) or Play Next (at the start). The queue holds one server."""
    q = app.vqueue
    items = [i for i in items if i.type in VIDEO]
    if not items:
        return
    sid = q[0].server_id if q else items[0].server_id
    if any(i.server_id != sid for i in items):
        app.toast("This queue plays from %s" % (app.sources.server_name(sid) or "another server"))
        return
    new = [queue_entry(i) for i in items]
    if first:
        q[0:0] = new
    else:
        q.extend(new)
    n = len(q)
    app.toast("Added to Queue • %d item%s" % (n, "" if n == 1 else "s"))
    app.queue_changed()


def open_queue(app, panel=None):
    from ..model import Item
    from .playlist import PlaylistScreen
    q = app.vqueue
    pl = Item("queue", "playlist", "Play Queue", server_id=q[0].server_id if q else None,
              poster=q[0].poster if q else None, extra={"kind": "video", "local": True})
    app.push(PlaylistScreen(app, pl), panel=panel)


def queue_row(app):
    """"Play Queue (3)" while the video queue has items (Start menus, side panel)."""
    n = len(getattr(app, "vqueue", None) or [])
    return ("Play Queue (%d)" % n, lambda: open_queue(app)) if n else None


# ---------------------------------------------------------------- Add to Playlist
def add_to_playlist(scr, it):
    """1. A worker lists the playlists of that type on the item's server (not smart). 2. A menu:
    New Playlist, then the playlists by title. 3. Add, or name a new one."""
    app, kind, sid = scr.app, kind_of(it), it.server_id
    gen = app.src_gen

    def done(pls):
        if gen != app.src_gen:
            return
        pls = sorted((p for p in pls if not p.extra.get("smart")), key=lambda p: p.title.lower())
        rows = [("New Playlist", lambda: new_playlist(app, it, kind, pls))]
        rows += [(p.title, lambda p=p: write(app, it, p)) for p in pls]
        app.open_menu(rows, title="ADD TO PLAYLIST", settings=False)

    def fail(e):
        if gen != app.src_gen:
            return
        if isinstance(e, AuthError) and sid == getattr(app.sources, "home_id", None):
            app.auth_lost()
            return
        app.toast("Can't load playlists")
    scr.run_bg(lambda: app.sources.server_playlists(kind, sid), done, fail)


def new_playlist(app, it, kind, pls):
    from .name import NameScreen, suggest

    def create(name):
        run(app, lambda: app.sources.playlist_create(name, kind, [it], it.server_id),
            "Added to %s" % name, sid=it.server_id)
    app.push(NameScreen(app, suggest(it, [p.title for p in pls]), "Create", create))


def write(app, it, pl):
    run(app, lambda: app.sources.playlist_add(pl, [it]), "Added to %s" % pl.title, pl)


def run(app, fn, ok_msg, pl=None, sid=None):
    """A playlist write on a worker: a private toast, and the playlist views load again. A
    home server that refuses the token goes to the sign-in path."""
    sid = pl.server_id if pl is not None else sid
    import threading
    gen = app.src_gen

    def work():
        try:
            r = fn()
        except Exception as e:
            app.post(lambda e=e: fail(e))
            return
        app.post(lambda: done(r))

    def done(r):
        if gen != app.src_gen:
            return
        app.toast(ok_msg, private=True)
        app.playlist_changed(pl if pl is not None else r)

    def fail(e):
        if gen != app.src_gen:
            return
        L.info("playlist write failed: %s", e)
        if isinstance(e, AuthError) and sid == getattr(app.sources, "home_id", None):
            app.auth_lost()
            return
        app.toast("Can't change this playlist")
    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- music queue checks
def music_accepts(app, items, session=None):
    """Play Next / Add to Queue on the music queue: checked when
    the tracks are in, just before they go in. The session must be the one the menu saw and
    every track must come from its servers. True, or a toast and False."""
    m = app.music
    if not m.active or m.live or (session is not None and m.session_id != session):
        return False
    servers = m.servers
    if any(i.server_id not in servers for i in items):
        sid = next(iter(servers), None)
        name = app.sources.server_name(sid) if app.sources is not None else ""
        app.toast("This queue plays from %s" % (name or "another server"))
        return False
    return True
