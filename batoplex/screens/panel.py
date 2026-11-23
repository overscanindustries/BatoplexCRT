"""The side panel: the sources of the Plex TV app.

Home, the pinned libraries (with the Channels and Watchlist pins at their places), Playlists,
More and Settings, over the dimmed root screen. It opens with Left on
the first item of a row or with B on a root screen. Right closes it; B in the panel
asks to exit. Text only: no Plex artwork.
"""
import pygame

from .. import draw, inputs, tokens
from ..config import LIVE_PIN, SPECIAL_PINS, WATCH_PIN
from ..tokens import C
from . import Overlay

W = tokens.PANEL_ROW_W  # row width
EDGE = tokens.PANEL_EDGE  # panel width from the safe left edge


class PanelScreen(Overlay):
    name = "PANEL"

    def __init__(self, app, under, focus=None):
        super().__init__(app, under)
        self.top = 0
        self.rows = self._rows()
        keys = [r[0] for r in self.rows]
        # an origin row that went away (last station removed, queue emptied): the source row
        cur = focus if focus in keys else app.source_key
        self.focus = keys.index(cur) if cur in keys else 0
        self._clamp()
        self._load_missing()

    def _load_missing(self):
        """A server whose sections are not loaded (Home not loaded yet, a failed load, a
        reconnect) loads them on a worker; its pins show when they arrive."""
        for p in list(self.app.sources.providers.values()):
            if not hasattr(p, "load_sections") or p.sections or getattr(p, "sections_busy", False):
                continue
            p.sections_busy = True

            def done(_r, p=p):
                p.sections_busy = False
                self.refresh()

            def fail(_e, p=p):
                p.sections_busy = False
            self.run_bg(p.load_sections, done, fail)

    def sources_changed(self):
        self.refresh()

    def refresh(self):
        """Sections or a server arrived while the panel is open: its pins become available."""
        key = self.rows[self.focus][0] if self.rows else None
        self.rows = self._rows()
        self.focus = next((i for i, r in enumerate(self.rows) if r[0] == key),
                          min(self.focus, len(self.rows) - 1))
        self._clamp()

    def _clamp(self):
        """Keep the focused row inside the nav window (the pitch changes with server tags)."""
        n, nav = self.geo()["n"], len(self.rows) - 1
        if self.focus < nav:
            if self.focus < self.top:
                self.top = self.focus
            elif self.focus >= self.top + n:
                self.top = self.focus - n + 1
        self.top = max(0, min(self.top, max(0, nav - n)))

    def _rows(self):
        """[(key, title, server tag or "", down)]; Settings is drawn at the bottom."""
        src = self.app.sources
        rows = [("home", "Home", "", False), ("search", "Search", "", False)]
        music = getattr(self.app, "music", None)
        self._music_on = bool(music is not None and music.active)
        if self._music_on:
            rows.append(("nowplaying", "Now Playing", "", False))
        st = self.app.settings
        many = src.server_count() > 1 or any(
            e["server"] not in (src.home_id,) + SPECIAL_PINS for e in st.pin_entries())
        libs = src.pinned(st)
        # The Live TV and Watchlist pins, at their places in the pin order; hidden (not
        # unpinned) with no channel, or with no Watchlist for this profile.
        special = [(st.special_index(pin), pin, key, title)
                   for pin, key, title in self._specials() if st.special_index(pin) is not None]
        for k in range(len(libs) + 1):
            for _i, _pin, key, title in [x for x in special if min(x[0], len(libs)) == k]:
                rows.append((key, title, "", False))
            if k == len(libs):
                break
            lib = libs[k]
            down = bool(lib.extra.get("down"))
            tag = ("Unavailable" if down else src.server_name(lib.server_id)) if many or down else ""
            rows.append(((lib.server_id, lib.section_id), lib.title, tag, down))
        if src.has_playlists():                      # not with no server signed in
            rows.append(("playlists", "Playlists", "", False))
        n = len(getattr(self.app, "vqueue", None) or [])
        if n:                                        # the local video queue
            rows.append(("vqueue", "Play Queue (%d)" % n, "", False))
        from .. import radio
        if radio.stations():                         # only with a station
            rows.append(("radio", "Radio", "", False))
        rows.append(("more", "More", "", False))
        rows.append(("settings", "Settings", "", False))
        return rows

    def _specials(self):
        """The special pins whose rows show now, in pin order: (pin, row key, title)."""
        src = self.app.sources
        if src.local_images:
            return []
        lu = getattr(self.app, "lineup", None)
        shown = {LIVE_PIN: lu is not None and bool(lu.visible()),
                 WATCH_PIN: bool(getattr(self.app, "watch_visible", bool)())}
        out = []
        for e in self.app.settings.pin_entries():
            pin = e.get("server")
            if shown.get(pin):
                out.append((pin, "livetv" if pin == LIVE_PIN else "watchlist",
                            "Channels" if pin == LIVE_PIN else "Watchlist"))
        return out

    def two_line(self):
        return any(r[2] for r in self.rows)

    def geo(self):
        sf = self.safe
        x0, top, y1 = sf["left"], sf["top"], 480 - sf["bottom"]
        pitch, h = ((tokens.ROW_TWO_PITCH, tokens.ROW_TWO_H) if self.two_line()
                    else (tokens.ROW_PITCH, tokens.ROW_H))
        settings_y = y1 - 48
        n = max(1, (settings_y - 20 - top) // pitch)
        return dict(x0=x0, top=top, settings_y=settings_y, pitch=pitch, h=h, n=n)

    def handle(self, a):
        if a in (inputs.UP, inputs.DOWN):
            n = self.focus + (1 if a == inputs.DOWN else -1)
            last = len(self.rows) - 1                # Settings
            if 0 <= n <= last:
                self.focus = n
            elif not getattr(self.app, "rep", False):   # a new press wraps
                self.focus = n % len(self.rows)
            self._clamp()
            self._press = pygame.Rect(0, 0, self.column_w() + 4, 480)   # only the column changes
        elif a == inputs.RIGHT:
            self.app.pop()
        elif a == inputs.BACK:
            key = self.rows[self.focus][0]
            self.app.pop()                           # cancel comes back here
            self.app.confirm_exit(on_no=lambda: self.app.open_panel(focus=key))
        elif a == inputs.OK:
            key, _t, _tag, down = self.rows[self.focus]
            if down:
                return
            self.app.pop()
            if key == self.app.source_key:
                return                               # the current source: just close
            # Each page gets its row (the panel has closed): B comes back here
            if key == "search":
                self.app.open_search(panel=key)
            elif key == "nowplaying":
                self.app.open_now_playing(panel=key)
            elif key == "radio":
                from .radio import RadioScreen
                self.app.push(RadioScreen(self.app), panel=key)
            elif key == "vqueue":
                from .actions import open_queue
                open_queue(self.app, panel=key)
            elif key == "playlists":
                from .playlists import PlaylistsScreen
                self.app.push(PlaylistsScreen(self.app), panel=key)
            elif key == "livetv":
                self.app.open_guide(panel=key)       # always the guide, no tune
            elif key == "more":
                from .more import MoreScreen
                self.app.push(MoreScreen(self.app), panel=key)
            elif key == "settings":
                self.app.open_settings(panel=key)
            else:
                self.app.go_source(key)
        elif a == inputs.QUIT:
            self.app.quit()

    def open_options(self):
        """Menu and hold OK: the pin menu on a pin row; elsewhere nothing."""
        return self._pin_menu()

    def _pin_menu(self):
        """Menu on a pin: Pin / Unpin, Move Up, Move Down. False on other
        rows."""
        key = self.rows[self.focus][0]
        if not (isinstance(key, tuple) or key in ("livetv", "watchlist")) \
                or self.app.sources.local_images:
            return False
        if key in ("livetv", "watchlist"):
            key = (LIVE_PIN if key == "livetv" else WATCH_PIN, 0)
        from .actions import pin_rows
        rows = pin_rows(self.app, key, self.rows[self.focus][1], after=self.refresh)
        if not rows or rows[0][0] != "Unpin":
            return False
        self.app.open_menu(rows, title=self.rows[self.focus][1].upper(), settings=False)
        return True

    def update(self):
        music = getattr(self.app, "music", None)
        if bool(music is not None and music.active) != getattr(self, "_music_on", False):
            key = self.rows[self.focus][0] if self.rows else None
            self.rows = self._rows()                 # the Now Playing row comes or goes
            self.focus = next((i for i, r in enumerate(self.rows) if r[0] == key), 0)
            self._clamp()
            self.dirty = True
        return super().update()

    def column_w(self):
        """The panel column, from the screen edge (it slides in)."""
        return self.safe["left"] + EDGE

    def scrim(self, s):
        x0 = self.safe["left"]
        draw.scrim(s, (x0 + EDGE, 0, 641 - x0 - EDGE, 480))

    def draw(self, s):
        self.draw_under(s)
        g = self.geo()
        x0 = g["x0"]
        draw.rect(s, C["raised"], (0, 0, x0 + EDGE, 480))
        s.fill(C["track"], (x0 + EDGE - 2, 0, 2, 480))
        nav = self.rows[:-1]
        y = g["top"]
        for i in range(self.top, min(len(nav), self.top + g["n"])):
            self._row(s, i, x0, y, g)
            y += g["pitch"]
        # Scroll cues in the 16 px between the rows and the panel edge, inside the safe area.
        cue = tokens.CUE
        if self.top > 0:
            draw.triangle(s, C["text2"], x0 + W + 1, g["top"] + 4, up=True, size=cue)
        if self.top + g["n"] < len(nav):
            draw.triangle(s, C["text2"], x0 + W + 1, g["settings_y"] - 32, up=False, size=cue)
        self._row(s, len(self.rows) - 1, x0, g["settings_y"], dict(g, h=tokens.ROW_H))

    def _row(self, s, i, x0, y, g):
        key, title, tag, down = self.rows[i]
        foc = i == self.focus
        cur = key == self.app.source_key
        if g["h"] == tokens.ROW_TWO_H:               # one height for all rows in the list
            draw.row_two(s, (x0, y, W), title, tag, focused=foc, current=cur, enabled=not down)
        else:
            draw.row(s, (x0, y, W), title, focused=foc, current=cur, enabled=not down)
