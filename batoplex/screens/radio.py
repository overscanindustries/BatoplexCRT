"""Radio: the user's stations from radio.m3u, in file order, with the
Settings list geometry. A starts a station and opens Now Playing. The last row, "Add Stations",
opens the station directory; Start on a station: "Remove from Radio"."""
from .. import draw, inputs, radio, tokens
from . import Screen, listlayout

WIDTH = 360                  # the list is 360 px wide, left-aligned


class RadioScreen(Screen):
    ambient = True                # the idle ribbon may show here
    name = "RADIO"

    def __init__(self, app):
        super().__init__(app)
        self.stations = radio.stations()
        m = app.music
        cur = m.track if m.active and m.live else None
        self.focus = next((i for i, s in enumerate(self.stations)
                           if cur is not None and s.id == cur.id), 0)
        self.top = 0
        self._v = None
        self.dirty = True

    def rows(self):
        m = self.app.music
        cur = m.track.id if m.active and m.live and m.track is not None else None
        out = [("station", s.id, s.title, "Playing" if s.id == cur else "")
               for s in self.stations]
        return out + [("add", None, "Add Stations", "")]

    @property
    def station(self):
        return self.stations[self.focus] if self.focus < len(self.stations) else None

    @property
    def menu_title(self):
        return self.station.title if self.station else "RADIO"

    def menu_items(self):
        st = self.station
        return [("Remove from Radio", lambda: self.remove(st))] if st else []

    def remove(self, st):
        try:
            gone = radio.remove([st.extra["url"]])
        except radio.Refused as e:
            self.app.toast(str(e))
            return
        self.app.music.drop_stations(set(gone))
        self._reload()

    def _reload(self):
        """Read the file again; the focus stays on the same station when it is still there."""
        old = self.station
        self.stations = radio.stations()
        k = next((i for i, s in enumerate(self.stations) if old is not None and s.id == old.id),
                 None)
        self.focus = k if k is not None else min(self.focus, len(self.stations))
        self.dirty = True

    def handle(self, a):
        self.dirty = True
        if a == inputs.BACK:
            self.app.back()
        elif a in (inputs.UP, inputs.DOWN):
            n = len(self.stations) + 1
            self.focus = max(0, min(n - 1, self.focus + (1 if a == inputs.DOWN else -1)))
        elif a == inputs.OK:
            if self.station is not None:
                self.app.play_radio(self.stations, self.focus)
            else:
                from .stations import StationsScreen
                self.app.push(StationsScreen(self.app))

    def update(self):
        m = self.app.music
        d = self.dirty or m.version != self._v
        self._v, self.dirty = m.version, False
        return d

    def on_resume(self, result=None):
        self._reload()

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Radio")
        skip = 0
        if not self.stations:                        # state text
            x, _w, y0, _y1 = listlayout.area(self)
            draw.state(s, x + tokens.INSET, y0, WIDTH - 2 * tokens.INSET, "No stations")
            skip = 44
        listlayout.draw_list(self, s, self.rows(), self.focus, width=WIDTH, skip=skip,
                             current=lambda r: bool(r[3]))
