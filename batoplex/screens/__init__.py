"""Screen base class. A screen handles actions, updates once per field, and draws onto the
641x480 canvas. `app` gives access to the provider, image cache, player and settings."""
import threading
import time

from .. import draw, lineup, log
from ..tokens import C

L = log.get("screen")
LOADING_AFTER_S = 0.5


class Screen:
    name = "SCREEN"
    panel_origin = None    # the side panel row that opened this page

    def __init__(self, app):
        self.app = app
        self.anim = False

    @property
    def safe(self):
        return self.app.settings["safe"]

    def on_enter(self):
        pass

    def on_resume(self, result=None):
        """Called when a screen above this one closes, or playback returns."""

    def handle(self, a):
        pass

    ok_on_press = False    # OK acts on press, no hold (key grids)

    def focus_identity(self):
        """What a pending OK acts on: the focused item of the screen or its grid,
        else the focus index. A refresh that puts another item there cancels the OK."""
        it = getattr(self, "item", None)
        if it is None and hasattr(self, "grid"):
            it = self.grid.item() if callable(getattr(self.grid, "item", None)) else None
        if callable(it):
            it = None
        if it is not None:
            return ("item", getattr(it, "id", None), getattr(it, "server_id", None))
        return ("focus", getattr(self, "zone", None), getattr(self, "focus", None))

    def open_options(self):
        """Menu and hold OK. Open the menu for the focused thing; True when a
        menu opened (else Menu reaches handle())."""
        return self.app.open_menu()

    def update(self):
        """Once per field. Return True if the screen needs a redraw. Input, worker results
        (app.post), a new image, the clock and the music redraw in the app:
        update() answers only for changes the app does not see (animations, timers)."""
        return self.anim

    def band(self):
        """After update() returned True, the rectangle that holds every
        change of this frame (the band pass filters only those rows); None: a full pass."""
        return None

    def image_band(self):
        """The rectangle in which a newly landed image can show; None: anywhere (a full
        pass)."""
        return None

    def press_band(self):
        """After handle() or a worker result (app.post): the rectangle that holds every change
        it made on screen, set in self._press; None: a full pass. Reading it clears it, and the
        app reads it once before the call too, so an old value never counts."""
        return self.__dict__.pop("_press", None)

    def late(self, busy, key="load"):
        """"Loading…" shows only after 500 ms. True when busy has been true
        for 500 ms (on this key); the screen keeps drawing while it loads."""
        marks = self.__dict__.setdefault("_busy_since", {})
        if not busy:
            marks.pop(key, None)
            self.__dict__.get("_late_shown", set()).discard(key)
            return False
        t0 = marks.setdefault(key, time.monotonic())
        return time.monotonic() - t0 >= LOADING_AFTER_S

    def late_due(self):
        """For update(): True once when a busy key passes 500 ms, so a screen that does not
        redraw while it waits still shows "Loading…"."""
        marks = self.__dict__.get("_busy_since")
        if not marks:
            return False
        shown = self.__dict__.setdefault("_late_shown", set())
        now = time.monotonic()
        for key, t0 in list(marks.items()):
            if key not in shown and now - t0 >= LOADING_AFTER_S:
                shown.add(key)
                return True
        shown.intersection_update(marks)
        return False

    def draw(self, s):
        pass

    # helpers
    def topbar(self, s, label, reserve=0, root=False, title=False, page=False):
        """The screen label left in small text2, Title Case; the clock right in
        small text2, at the safe top. reserve: px left of the clock kept free (a glyph).
        root: a side panel source (no mark).
        title: the label is the focused title of a grid (less chrome): the
        same slot and style, in text. page: page-title mode (Episodes): the label in heading,
        text, at the same y."""
        sf = self.safe
        cw = draw.width(lineup.clock_hm(), "small")
        x = sf["left"]
        reserve += self.music_mark(s, 640 - sf["right"] - cw - reserve - 16)
        lw = 640 - sf["right"] - cw - reserve - 24 - x
        if page:
            draw.text(s, label, "heading", C["text"], x, sf["top"], w=lw)
        else:
            draw.text(s, label, "small", C["text"] if title else C["text2"], x, sf["top"], w=lw)
        return draw.text(s, lineup.clock_hm(), "small", C["text2"], 640 - sf["right"],
                         sf["top"], align="right")

    def music_mark(self, s, right, measure=False):
        """While music plays: the play or pause mark and the track title,
        ending at `right`. Returns the width it takes, with its 16 px gap. measure: only the
        width (the toast keeps clear of it)."""
        music = getattr(self.app, "music", None)
        if music is None or not music.active or getattr(self, "is_now_playing", False):
            return 0
        t = music.track
        sf = self.safe
        T = sf["top"]
        maxw = 160 if 640 - sf["left"] - sf["right"] >= 548 else 112
        title = music.now_title if t is not None else ""
        tw = min(maxw, draw.width(title, "small"))
        if measure:
            return tw + 14 + 16
        x = right - tw
        draw.text(s, title, "small", C["text2"], x, T, w=tw)
        mx = x - 14
        if music.state == "paused":                  # 4 x 12 bars on even rows
            s.fill(C["text2"], (mx, T + 6, 4, 12))
            s.fill(C["text2"], (mx + 6, T + 6, 4, 12))
        else:
            import pygame
            pygame.draw.polygon(s, C["text2"], [(mx, T + 6), (mx, T + 18), (mx + 10, T + 12)])
        return tw + 14 + 16

    # Hint rule: hints show only on tool screens and edit modes (Channels setup
    # screens, Screen Margins, Test Patterns, Move mode, the Guide, Sign In, the Theme and Screen
    # Saver Type pickers, the playback start card). A hint names only a button that a TV remote
    # has: arrows ("dpad", "updown"), "A", "B", "menu". No X, Y, LB, RB, LT, RT. Four at most.
    def hintbar(self, s, items):
        if getattr(self, "hide_hints", False):    # under a dialog: the dialog has the focus
            return
        sf = self.safe
        draw.hints(s, items, sf["left"], 480 - sf["bottom"] - 24, 560)

    def run_bg(self, fn, done, fail=None):
        """Run fn() on a worker thread; deliver done(result) / fail(exc) on the UI thread."""
        def work():
            try:
                r = fn()
            except Exception as e:  # network
                L.info("background task failed: %s", e)
                if fail:
                    self.app.post(lambda e=e: fail(e))
                return
            self.app.post(lambda: done(r))
        threading.Thread(target=work, daemon=True).start()


class Overlay(Screen):
    """A menu, a dialog or the side panel over another screen. The screen
    under it keeps its animations (its update() runs on its own) and is drawn into a
    cached surface, with the scrim, only when it changed; its band passes through."""
    overlay_kind = None

    def __init__(self, app, under):
        super().__init__(app)
        self.under = under
        self.dirty = True
        self._ucache = None
        self._ustale = True
        self._ugen = None
        self._own = True
        self._ub = None

    def open_options(self):
        """A menu, a dialog: no menu over it (Menu reaches handle(): MenuScreen closes)."""
        return False

    def update(self):
        u = bool(self.under.update())
        self._ub = self.under.band() if u else None
        if u:
            self._ustale = True
        d, self.dirty = self.dirty, False
        self._own = d
        return u or d or self.late_due()

    def band(self):
        return None if self._own else self._ub

    def image_band(self):
        return self.under.image_band()

    def scrim(self, s):
        draw.scrim(s)

    def draw_under(self, s):
        gen = getattr(self.app, "redraw_gen", None)
        if self._ucache is None or self._ucache.get_size() != s.get_size() or self._ustale \
                or gen is None or gen != self._ugen:
            import pygame
            if self._ucache is None or self._ucache.get_size() != s.get_size():
                self._ucache = pygame.Surface(s.get_size())
            self.under.hide_hints = True
            try:
                self.under.draw(self._ucache)
            finally:
                self.under.hide_hints = False
            self.scrim(self._ucache)
            self._ustale, self._ugen = False, gen
        s.blit(self._ucache, (0, 0))


def fmt_dur(ms):
    m = int(round(ms / 60000.0))
    return "%dh %02dm" % (m // 60, m % 60) if m >= 60 else "%dm" % m


def fmt_clock(ms):
    s = int(ms / 1000)
    return "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def ep_code(it, sep=" • "):
    """"S2 • E8"; Plex season 0 is "Specials"; an unknown part is left out."""
    parts = []
    if it.season_index == 0:
        parts.append("Specials")
    elif it.season_index is not None:
        parts.append("S%s" % it.season_index)
    if it.index is not None:
        parts.append("E%s" % it.index)
    return sep.join(parts)


def playlist_count(pl, items=None):
    """"12 items • 48m", or "Smart Playlist • 12 items". items: the
    loaded entries (their count and length win over the server's numbers)."""
    n = len(items) if items is not None else pl.leaf_count
    ms = sum(i.duration_ms or 0 for i in items) if items is not None else pl.duration_ms
    count = "%d item%s" % (n, "" if n == 1 else "s")
    if pl.extra.get("smart"):
        return "Smart Playlist • " + count
    return count + (" • " + fmt_dur(max(ms, 60000)) if ms else "")


def meta_line(it):
    sep = " • "
    if it.type == "playlist":
        return playlist_count(it)
    if it.type == "channel":                      # On Right Now: "02 NAME • 24 min left"
        import time
        from ..lineup import mins_left
        return "%s • %s" % (it.extra.get("prefix", ""),
                            mins_left(max(0, it.extra.get("end", 0) - time.time())))
    if it.type == "person":                       # the role
        return it.extra.get("role") or ""
    if it.type == "clip":                         # "Trailer • 2m"
        from ..plex import extra_label
        return sep.join(p for p in (extra_label(it.extra.get("subtype")),
                                    fmt_dur(it.duration_ms) if it.duration_ms else "") if p)
    if it.type == "episode":
        parts = [ep_code(it)] if ep_code(it) else []
        if it.view_offset_ms and it.duration_ms and not it.watched:
            parts.append("%s left" % fmt_dur(it.duration_ms - it.view_offset_ms))
        elif it.duration_ms:
            parts.append(fmt_dur(it.duration_ms))
        return sep.join(parts)
    if it.type in ("album", "track", "artist", "station"):
        lead = it.album if it.type == "track" else str(it.year or "")
        return sep.join(p for p in ((it.artist if it.type != "artist" else ""), lead) if p)
    parts = [str(it.year)] if it.year else []
    if it.type == "show":
        if it.child_count:
            parts.append("%d season%s" % (it.child_count, "" if it.child_count == 1 else "s"))
    elif it.type == "movie" and it.duration_ms:
        if it.view_offset_ms and not it.watched:
            parts.append("%s left" % fmt_dur(it.duration_ms - it.view_offset_ms))
        else:
            parts.append(fmt_dur(it.duration_ms))
    if it.content_rating:
        parts.append(it.content_rating)
    return sep.join(parts[:3])
