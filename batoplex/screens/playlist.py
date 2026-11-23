"""One playlist: the Album layout. Art, title, server and count with Play and
Shuffle on the left; the entries on the right, numbered by place, with no disc lines.

A music playlist plays through the music queue; a video playlist plays its items one after
another (section 4). Entries load in pages of 200, up to 5,000; Play and Shuffle work once all
pages are in. Each entry is one occurrence: the same item can be in a list twice, and its
playlistItemID (extra["occ"]) tells them apart.
"""
import threading

from .. import draw, inputs, log
from ..overlay import fmt_time
from ..plex import AuthError
from ..tokens import C
from . import fmt_dur, playlist_count
from .album import AlbumScreen

PAGE = 200
LIMIT = 5000
L = log.get("playlist")


def load_all(src, pl):
    """Every entry of a playlist (a worker), up to LIMIT."""
    out, start = [], 0
    while start < LIMIT:
        items, total, n = src.playlist_items(pl, start, PAGE)
        out += items
        start += n
        if not n or start >= total:
            break
    return out[:LIMIT]


def play_entries(app, pl, items, index=0, shuffle=False, resume=True, origin=None):
    """Play a playlist's entries: video items one after another, music through the queue.
    Entries whose server is not connected are left out (an Unavailable first entry
    stopped Play and Shuffle)."""
    start = items[index] if 0 <= index < len(items) else None
    items = [x for x in items if app.sources.strict(x.server_id) is not None]
    if not items:
        app.toast("Can't play %s" % pl.title)
        return
    index = next((k for k, x in enumerate(items) if x is start), 0)
    if pl.extra.get("kind") == "video":
        app.play_playlist(items, index, shuffle=shuffle, resume=resume, origin=origin,
                          local=bool(pl.extra.get("local")))
    else:
        app.play_music(items, index, shuffle=shuffle, origin=origin)


def playlist_rows(scr, pl):
    """Start on a playlist in a grid: Play, Shuffle (a smart playlist has no more)."""
    app, src = scr.app, scr.app.sources

    def start(shuffle):
        def done(items):
            if not items:
                app.toast("This playlist is empty")
                return
            play_entries(app, pl, items, 0, shuffle, origin=scr)

        def fail(e):
            if isinstance(e, AuthError):
                app.auth_lost()
            else:
                app.toast("Can't load this playlist")
        scr.run_bg(lambda: load_all(src, pl), done, fail)
    rows = [("Play", lambda: start(False)), ("Shuffle", lambda: start(True))]
    if not pl.extra.get("smart"):
        rows += [("Rename", lambda: rename_playlist(app, pl)),
                 ("Delete Playlist", lambda: delete_playlist(app, pl))]
    return rows


def _write_bg(app, pl, fn, done=None, fail=None):
    """A playlist write on a worker. A home server that refuses the token goes
    to the sign-in path; any other failure: "Can't change this playlist"."""
    gen = app.src_gen

    def ok(r):
        if gen == app.src_gen:
            (done or (lambda: None))()

    def bad(e):
        if gen != app.src_gen:
            return
        L.info("playlist write failed: %s", e)
        if isinstance(e, AuthError) and pl.server_id == getattr(app.sources, "home_id", None):
            app.auth_lost()
            return
        app.toast("Can't change this playlist")
        (fail or (lambda: None))()

    def work():
        try:
            fn()
        except Exception as e:
            app.post(lambda e=e: bad(e))
            return
        app.post(lambda: ok(None))
    threading.Thread(target=work, daemon=True).start()


def rename_playlist(app, pl, on_name=None):
    """The name screen with the current title; Save renames (the screen changes first)."""
    from .name import NameScreen
    old = pl.title

    def save(name):
        if name == old:
            return
        pl.title = name
        if on_name:
            on_name(name)

        def undo():
            pl.title = old
            app.playlist_changed(pl)
        _write_bg(app, pl, lambda: app.sources.playlist_rename(pl, name),
                  lambda: app.playlist_changed(pl), undo)
    app.push(NameScreen(app, old, "Save", save))


def delete_playlist(app, pl, close=None):
    """Asks first. Yes: the playlist screen closes (close()), the delete runs,
    the grids load again."""
    from .system import ConfirmDialog

    def yes():
        if close:
            close()
        _write_bg(app, pl, lambda: app.sources.playlist_delete(pl),
                  lambda: app.playlist_changed(pl), lambda: app.playlist_changed(pl))
    app.overlay(ConfirmDialog(app, app.top, "Delete \u201c%s\u201d?" % pl.title, "Delete", yes))


class PlaylistScreen(AlbumScreen):
    name = "PLAYLIST"
    error_text = "Can't load this playlist"

    def __init__(self, app, pl):
        self.pl = pl
        self.complete = False
        self.lgen = 0
        self.local = bool(pl.extra.get("local"))     # the local video queue
        self.moving = None                           # Move: {"orig": [entries]}
        self.writes, self.writing = [], False        # one write at a time
        super().__init__(app, pl)

    @property
    def video(self):
        return self.pl.extra.get("kind") == "video"

    @property
    def is_queue(self):
        return self.local

    # ------------------------------------------------------------ data
    def load(self):
        self.moving = None                           # a reload ends Move mode
        if self.local:
            self.tracks = list(self.app.vqueue)      # no server call
            self.loading, self.error, self.complete, self.dirty = False, None, True, True
            self.pl.poster = self.tracks[0].poster if self.tracks else None
            self._clamp()
            return
        if self.loading:
            return
        self.loading, self.error, self.dirty, self.complete = True, None, True, False
        self.lgen += 1
        self._page(self.lgen, 0, [])

    def _page(self, gen, start, have):
        src, app = self.app.sources, self.app

        def stale():
            return self not in app.stack or gen != self.lgen

        def done(r):
            if stale():
                return
            items, total, n = r
            got = have + items
            nxt = start + n
            if n and nxt < min(total, LIMIT):
                if not self.tracks:
                    self.tracks = got               # the first page shows at once
                    self._focus_follow()
                self._page(gen, nxt, got)
                return
            self.loading, self.complete = False, True
            self.tracks = got[:LIMIT]
            self._focus_follow()
            self.dirty = True

        def fail(e):
            if stale():
                return
            self.loading = False
            if isinstance(e, AuthError):
                app.auth_lost()
                return
            if self.tracks and start:
                app.toast("Can't load all of this playlist")
                self.tracks, self.complete = have, True
            else:
                self.error = str(e)
            self.dirty = True
        self.run_bg(lambda: src.playlist_items(self.pl, start, PAGE), done, fail)

    def queue_changed(self):
        if self.local:
            self.load()

    def playlists_changed(self, pl):
        """Another screen wrote to this playlist (Add to Playlist over it): load it again."""
        if not self.local and pl is not None and (pl.id, pl.server_id) == (self.pl.id, self.pl.server_id) \
                and not getattr(self, "writing", False):
            self.load()

    def playlist_fresh(self, new):
        """The server's numbers and composite after a change."""
        if not self.local and (new.id, new.server_id) == (self.pl.id, self.pl.server_id):
            self.pl.poster, self.pl.leaf_count = new.poster, new.leaf_count
            self.pl.duration_ms, self.pl.title = new.duration_ms, new.title
            self.dirty = True

    def _focus_follow(self):
        if self.follow:
            i = next((k for k, t in enumerate(self.tracks) if t.extra.get("occ") == self.follow), None)
            if i is not None:
                self.zone, self.focus = "list", i
                self.follow = None
        self._clamp()
        self.dirty = True

    @property
    def rows(self):
        return [("track", i) for i in range(len(self.tracks))]

    def apply_files(self, files, exact=False, reload=True):
        """A video session ended: every row of that item (it can be in the list twice)."""
        for f in files:
            w = f["watched"] if exact else (f["watched"] or None)
            for t in self.tracks:
                if t.id == f["item_id"] and f.get("server_id") in (None, t.server_id):
                    t.view_offset_ms = f["position_ms"]
                    if w is not None:
                        t.watched = w
        self.dirty = True

    def on_resume(self, result=None):
        entry = (result or {}).get("entry")
        if entry:
            i = next((k for k, t in enumerate(self.tracks) if t.extra.get("occ") == entry), None)
            if i is not None:
                self.zone, self.focus = "list", i
                self._clamp()
        self.dirty = True

    # ------------------------------------------------------------ text
    def sub_lines(self):
        src = self.app.sources
        out = []
        if src.server_count() > 1:
            out.append((src.server_name(self.pl.server_id), C["text2"]))
        out.append((playlist_count(self.pl, self.tracks if self.complete else None), C["dim"]))
        return out

    def buttons_ready(self):
        return self.complete and bool(self.tracks)

    def available(self, t):
        return self.app.sources.strict(t.server_id) is not None

    # ------------------------------------------------------------ play
    def play(self, i, shuffle=False, resume=True):
        if not self.buttons_ready():
            return
        if not self.available(self.tracks[i]) and not shuffle:
            self.app.toast("Can't play %s" % self.tracks[i].title)
            return
        play_entries(self.app, self.pl, self.tracks, i, shuffle, resume, origin=self)

    # ------------------------------------------------------------ menu
    @property
    def menu_title(self):
        t = self._focused_track()
        return t.title if t is not None else self.pl.title

    def menu_items(self):
        t = self._focused_track()
        rows = []
        if self.moving is not None:
            return rows
        edit = self.local or (not self.pl.extra.get("smart") and self.complete)
        if t is None:
            if self.local and self.tracks:
                rows.append(("Clear Queue", self._queue_clear))
            elif not self.local and not self.pl.extra.get("smart"):
                rows += [("Rename", lambda: rename_playlist(self.app, self.pl, self._renamed)),
                         ("Delete Playlist", lambda: delete_playlist(self.app, self.pl, self._close))]
            return rows
        if t.type in ("movie", "episode") and t.view_offset_ms and not t.watched:
            rows.append(("Play from Start", lambda: self.play(self.focus, resume=False)))
        if edit and len(self.tracks) > 1:
            if not self.writing:                     # no Move while a write runs
                rows.append(("Move", self._move_start))
        if self.local:
            rows.append(("Remove from Queue", lambda: self._queue_remove(t)))
        elif edit:
            rows.append(("Remove from Playlist", lambda: self._remove(t)))
            from .actions import item_rows
            rows += [r for r in item_rows(self, t) if r[0] == "Add to Playlist"]
        if t.type == "track" and t.album_id:
            rows.append(("Go to Album", lambda: self.app.open_album_of(t)))
        elif t.type == "episode" and t.show_id:
            rows.append(("Go to Show", lambda: self.app.go_to_show(t)))
        return rows

    def _queue_remove(self, t):
        q = self.app.vqueue
        k = next((i for i, x in enumerate(q) if x is t), None)
        if k is not None:
            del q[k]
        self.app.queue_changed()
        if not q:
            self.app.pop()

    # ------------------------------------------------------------ editing
    def _write(self, fn, ok=None, undo=None):
        """Each write changes the screen first; writes run one at a time. A failure: the
        toast, the rest dropped, and the list loads again."""
        self.writes.append((fn, ok, undo))
        if not self.writing:
            self._next_write()

    def _next_write(self):
        if not self.writes:
            self.writing = False
            return
        self.writing = True
        fn, ok, undo = self.writes.pop(0)
        app = self.app

        def done():
            if ok:
                app.toast(ok, private=True)
            app.playlist_changed(self.pl)        # still writing: this screen does not reload
            self._next_write()

        def fail():
            self.writes, self.writing = [], False
            if undo:
                undo()
            if self in app.stack:
                self.load()
        _write_bg(app, self.pl, fn, done, fail)

    def _remove(self, t):
        k = next((i for i, x in enumerate(self.tracks) if x is t), None)
        if k is None:
            return
        del self.tracks[k]
        if not self.tracks:
            self.zone = "buttons"
        self._clamp()
        self.dirty = True
        self._write(lambda: self.app.sources.playlist_remove(self.pl, t),
                    "Removed from %s" % self.pl.title)

    def _renamed(self, name):
        self.dirty = True

    def _close(self):
        if self in self.app.stack:
            while self.app.top is not self:
                self.app.pop()
            self.app.pop()

    def _move_start(self):
        self.moving = {"orig": list(self.tracks)}
        self.dirty = True
        self._clamp()                    # Move shows one row less: keep the moving row on screen

    def _move_step(self, d):
        i = self.focus
        j = max(0, min(len(self.tracks) - 1, i + d))
        if j != i:
            t = self.tracks.pop(i)
            self.tracks.insert(j, t)
            self.focus = j
            self._clamp()

    def _move_end(self, keep):
        if not self.moving:
            return
        orig, self.moving = self.moving["orig"], None
        self.dirty = True
        self._clamp()                    # the hint bar row comes back: no blank row at the end
        i = self.focus
        t = self.tracks[i]
        if not keep:
            self.tracks = orig
            self.focus = next((k for k, x in enumerate(orig) if x is t), 0)
            self._clamp()
            return
        if [id(x) for x in orig] == [id(x) for x in self.tracks]:
            return                                   # the same place: no call
        if self.local:
            self.app.vqueue[:] = self.tracks
            return
        after = self.tracks[i - 1] if i > 0 else None
        self._write(lambda: self.app.sources.playlist_move(self.pl, t, after))

    def _queue_clear(self):
        self.app.vqueue.clear()
        self.app.queue_changed()
        self.app.pop()

    # ------------------------------------------------------------ input
    def handle(self, a):
        self.dirty = True
        if self.moving is not None:
            if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
                self._move_step({inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -5, inputs.RT: 5}[a])
            elif a == inputs.OK:
                self._move_end(True)
            elif a == inputs.BACK:
                self._move_end(False)
            return
        if a == inputs.OK and not self.error and self.tracks:
            if self.zone == "buttons":
                self.play(0, shuffle=self.btn == 1)
            else:
                self.play(self.focus)
            return
        super().handle(a)

    # ------------------------------------------------------------ draw
    def geo(self):
        """Move mode keeps the hint line under the list free: one row less."""
        g = super().geo()
        if self.moving is not None:
            g["n"] = max(1, g["n"] - 1)
        return g

    def topbar(self, s, label, **kw):
        return super().topbar(s, "Play Queue" if self.local else "Playlist", **kw)

    def draw(self, s):
        super().draw(s)
        if self.moving is not None:              # move has a hint bar
            g = self.geo()
            draw.hints(s, [("A", "Place"), ("B", "Cancel")], g["lx"], g["y1"] - 24, g["lw"])

    def _num_w(self):
        return max(20, draw.width(str(max(1, len(self.tracks))), "small"))

    def _row(self, s, g, v, y, foc):
        t = self.tracks[v]
        m = self.app.music
        now = m.track if (m.active and not self.video) else None
        playing = now is not None and (now is t or (now.id == t.id and now.server_id == t.server_id
                                                    and not any(x is now for x in self.tracks)))
        ok = self.available(t)
        if not ok:
            dur = "Unavailable"
        elif t.type == "track":
            dur = fmt_time(t.duration_ms / 1000) if t.duration_ms else ""
        elif t.view_offset_ms and t.duration_ms and not t.watched:
            dur = "%s left" % fmt_dur(t.duration_ms - t.view_offset_ms)
        else:
            dur = fmt_dur(t.duration_ms) if t.duration_ms else ""
        if t.type == "track":
            first, second = t.title, t.artist
        elif t.type == "episode":
            from . import ep_code
            first = t.show_title or t.title
            second = " • ".join(p for p in (ep_code(t), t.title) if p)
        else:
            first, second = t.title, str(t.year) if t.year else ""
        dim = not ok or (t.type != "track" and t.watched)
        draw.row_compact(s, (g["lx"], y, g["lw"]), v + 1, first, second=second or None,
                         right=dur or None, focused=foc, playing=playing, dimmed=dim,
                         nw=self._num_w(), moving=foc and self.moving is not None)
