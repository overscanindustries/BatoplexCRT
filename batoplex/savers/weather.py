"""Weather: the local forecast in the look of the early-1990s cable weather
channel: a deep purple sky band, an orange title tab with a slant, a navy panel in a bright
blue bevel, and a blue crawl band. The frame, the icons and the logo are drawn new in code
(colours and layout measured from the open-source WeatherStar 4000+ recreation, MIT; none of
its images or fonts are used). Text: the shipped Barlow fonts with drop shadows.

Pages, 10 s each: Current Conditions, Local Forecast, Extended Forecast, Precipitation (a
forecast grid, not radar). No data: "No Report Available"; the crawl and the clock still run.

Path C: the app's frame filter runs. A page change is one full pass; each field redraws only
the crawl band and, each second, the clock (band passes). Burn-in: the frame is fixed, so the
whole picture moves on the Orbit loop (2 screen px each 30 s).
"""
import math
import threading
import time

import pygame

from .. import fonts, log, weather
from . import Orbit

L = log.get("weather")

W, H = 641, 480
PAGE_S = 10.0
CRAWL_PX = 2                   # each 2-field slot
TOP = (28, 10, 87)
TAB_TOP, TAB_BOT = (188, 89, 4), (74, 34, 62)
SIDE_TOP, SIDE_BOT = (49, 20, 80), (192, 92, 3)
PANEL = (33, 40, 90)
BEVEL_OUT = (38, 82, 178)
CRAWL = (35, 50, 112)
YELLOW = (232, 222, 48)
WHITE = (230, 230, 230)
SHADOW = (8, 8, 16)
PANEL_R = pygame.Rect(52, 90, 532, 309)
CRAWL_R = pygame.Rect(0, 403, W, 77)
CLOCK_R = pygame.Rect(440, 30, 190, 60)
ICON_CUR = pygame.Rect(112, 260, 112, 100)
ICON_EXT = pygame.Rect(88, 140, 464, 92)


def _mix(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


FIXED = 0.75                   # the fixed frame at 75 %: no channel above about 141 (burn-in)


def chrome():
    """The fixed frame of every page, dimmed to FIXED (it never changes; the panel text and
    the crawl do), with 8 px more on each side (its edges copied out), so an Orbit move
    never shows a bare strip. Blit it at (ox - 8, oy - 8)."""
    s = _chrome()
    big = pygame.Surface((W + 16, H + 16))
    big.blit(s, (8, 8))
    for x in range(8):                                        # left and right edges
        big.blit(s, (x, 8), (0, 0, 1, H + 8))
        big.blit(s, (W + 8 + 8 + x - 8, 8), (W + 7, 0, 1, H + 8))
    for y in range(8):                                        # top and bottom rows
        big.blit(big, (0, y), (0, 8, W + 16, 1))
        big.blit(big, (0, H + 8 + y), (0, H + 7, W + 16, 1))
    return big


def _chrome():
    s = pygame.Surface((W + 8, H + 8))
    s.fill(TOP)
    for y in range(30, 90):                                   # the title tab, slanted
        k = (y - 30) / 59.0
        right = int(500 - 50 * k)
        s.fill(_mix(TAB_TOP, TAB_BOT, k), (0, y, right, 1))
    for y in range(90, 399):                                  # the sides
        c = _mix(SIDE_TOP, SIDE_BOT, (y - 90) / 308.0)
        s.fill(c, (0, y, 52, 1))
        s.fill(c, (584, y, W + 8 - 584, 1))
    for i in range(16):                                       # the bevel
        c = _mix(BEVEL_OUT, PANEL, i / 16.0)
        pygame.draw.rect(s, c, PANEL_R.inflate(-2 * i, -2 * i), 1)
    s.fill(PANEL, PANEL_R.inflate(-32, -32))
    s.fill((20, 20, 20), (0, 399, W + 8, 2))
    s.fill((175, 175, 175), (0, 401, W + 8, 2))
    s.fill(CRAWL, (0, 403, W + 8, H + 8 - 403))
    _logo(s, 58, 36)
    dim = int(255 * FIXED)
    s.fill((dim, dim, dim), (0, 0, W + 8, 403), special_flags=pygame.BLEND_RGB_MULT)
    s.fill(PANEL, PANEL_R.inflate(-32, -32))                  # the panel keeps its colour
    return s


def _logo(s, x, y):
    """The channel mark: white words on a blue rounded square (our drawing)."""
    r = pygame.Rect(x, y, 56, 48)
    pygame.draw.rect(s, (12, 72, 188), r, border_radius=8)
    pygame.draw.rect(s, (184, 184, 190), r, 2, border_radius=8)
    f = fonts.get("cond_bold", 13)
    for i, word in enumerate(("The", "Weather", "Channel")):
        t = f.render(word, True, (188, 188, 188))     # x FIXED: about 141 on screen
        s.blit(t, (r.centerx - t.get_width() // 2, r.y + 3 + i * 14))


def text(s, txt, size, col, x, y, align="left", font="cond_bold"):
    """Text with the drop shadow of the period. Returns its width."""
    f = fonts.get(font, size)
    t = f.render(txt, True, col)
    sh = f.render(txt, True, SHADOW)
    if align == "right":
        x -= t.get_width()
    elif align == "center":
        x -= t.get_width() // 2
    s.blit(sh, (x + 2, y + 2))
    s.blit(t, (x, y))
    return t.get_width()


# ------------------------------------------------------------ icons
def icon(s, kind, cx, cy, size=1.0, phase=0.0):
    """A weather icon drawn in code, centred at (cx, cy); size 1 = about 96 x 72."""
    k = size

    def sun(x, y, r):
        for i in range(8):
            a = i * math.pi / 4 + phase * 0.3
            p1 = (x + math.cos(a) * r * 1.35, y + math.sin(a) * r * 1.35)
            p2 = (x + math.cos(a) * r * 1.85, y + math.sin(a) * r * 1.85)
            pygame.draw.line(s, (232, 160, 20), p1, p2, max(2, int(4 * k)))
        pygame.draw.circle(s, (232, 150, 16), (int(x), int(y)), int(r + 2 * k))
        pygame.draw.circle(s, (235, 214, 60), (int(x), int(y)), int(r))

    def moon(x, y, r):
        pygame.draw.circle(s, (220, 220, 190), (int(x), int(y)), int(r))
        pygame.draw.circle(s, PANEL, (int(x + r * 0.45), int(y - r * 0.3)), int(r * 0.85))

    def cloud(x, y, w, dark=False):
        top, bot = ((150, 150, 160), (100, 100, 112)) if dark else ((235, 235, 235), (180, 182, 196))
        h = w * 0.42
        for (dx, dy, rr) in ((-0.28, 0.1, 0.22), (0.0, -0.08, 0.3), (0.28, 0.08, 0.22)):
            pygame.draw.circle(s, bot, (int(x + dx * w), int(y + dy * w + 3 * k)), int(rr * w))
        pygame.draw.rect(s, bot, (x - w * 0.45, y, w * 0.9, h * 0.55 + 3 * k), border_radius=int(8 * k))
        for (dx, dy, rr) in ((-0.28, 0.1, 0.2), (0.0, -0.08, 0.28), (0.28, 0.08, 0.2)):
            pygame.draw.circle(s, top, (int(x + dx * w), int(y + dy * w)), int(rr * w))
        pygame.draw.rect(s, top, (x - w * 0.45, y, w * 0.9, h * 0.55), border_radius=int(8 * k))

    def drops(x, y, n, col, slant=6, length=12, gap=14):
        for i in range(n):
            ox = x + (i - (n - 1) / 2) * gap * k
            off = ((phase * 20 + i * 7) % 10) * k
            p1 = (ox + slant * k * 0.3, y + off)
            p2 = (ox - slant * k * 0.3, y + off + length * k)
            pygame.draw.line(s, col, p1, p2, max(2, int(3 * k)))

    def flakes(x, y, n):
        for i in range(n):
            ox = x + (i - (n - 1) / 2) * 16 * k
            oy = y + ((phase * 12 + i * 9) % 12) * k
            for a in (0, math.pi / 3, 2 * math.pi / 3):
                dx, dy = math.cos(a) * 5 * k, math.sin(a) * 5 * k
                pygame.draw.line(s, (235, 235, 235), (ox - dx, oy - dy), (ox + dx, oy + dy), 2)

    if kind == "sunny":
        sun(cx, cy, 20 * k)
    elif kind == "clear":
        moon(cx, cy, 22 * k)
    elif kind in ("partly", "partly_night"):
        (sun if kind == "partly" else moon)(cx - 16 * k, cy - 12 * k, 16 * k)
        cloud(cx + 10 * k, cy + 6 * k, 64 * k)
    elif kind == "cloudy":
        cloud(cx - 14 * k, cy - 10 * k, 54 * k, dark=True)
        cloud(cx + 8 * k, cy + 4 * k, 66 * k)
    elif kind == "fog":
        cloud(cx, cy - 10 * k, 64 * k, dark=True)
        for i in range(3):
            s.fill((170, 170, 180), (cx - 34 * k + (i % 2) * 8 * k, cy + 12 * k + i * 8 * k,
                                     60 * k, 4 * k))
    elif kind in ("drizzle", "rain", "heavy", "showers"):
        if kind == "showers":
            sun(cx - 18 * k, cy - 18 * k, 13 * k)
        cloud(cx, cy - 10 * k, 66 * k, dark=kind == "heavy")
        n = {"drizzle": 3, "rain": 4, "heavy": 5, "showers": 3}[kind]
        drops(cx, cy + 14 * k, n, (90, 150, 235), length=8 if kind == "drizzle" else 14)
    elif kind == "snow":
        cloud(cx, cy - 10 * k, 66 * k)
        flakes(cx, cy + 18 * k, 4)
    elif kind == "sleet":
        cloud(cx, cy - 10 * k, 66 * k)
        drops(cx - 10 * k, cy + 14 * k, 2, (90, 150, 235), gap=28)
        flakes(cx + 14 * k, cy + 20 * k, 2)
    elif kind == "storm":
        cloud(cx, cy - 10 * k, 66 * k, dark=True)
        bolt = [(0, 0), (-10, 18), (-1, 18), (-8, 36), (12, 12), (2, 12), (8, 0)]
        pygame.draw.polygon(s, (235, 220, 40), [(cx + x * k, cy + 6 * k + y * k) for x, y in bolt])


# ------------------------------------------------------------ the saver
class Saver:
    prefiltered = False

    def __init__(self, host, opts):
        self.host, self.opts, self.rng = host, opts, host.rng
        self.app = host.app
        self.orbit = Orbit()
        self.frame = chrome()
        self.reply, self.grid, self.place, self.units = None, None, None, "f"
        self.fails, self.next_try, self.loading = 0, 0.0, False
        self.pages = ["none"]
        self.page = 0
        self.page_at = None
        self.cache = {}                 # page key -> rendered surface (frame + page)
        self.crawl_x = 0
        self.crawl = None
        self.slot = None
        self.sec = None
        self.off = None
        self.full = True
        self.band_r = None
        self.anim_k = 0
        self.music_sid = None
        self._load_cached()
        if not host.inset and host.setting != "shuffle":     # Shuffle: silent
            self._start_music()

    def _start_music(self):
        """The chosen playlist, shuffled, when no music plays."""
        pl = self.opts.get("playlist")
        app = self.app
        mus = getattr(app, "music", None)
        src = getattr(app, "sources", None)
        if not pl or mus is None or src is None or mus.active:
            return
        from ..model import Item
        ref = Item(id=str(pl["id"]), type="playlist", title=pl.get("title", ""),
                   server_id=pl.get("server_id"))

        def work():
            try:
                items, _total, _n = src.playlist_items(ref, 0, 200)
            except Exception as e:
                L.info("weather playlist failed: %s", e)
                return
            tracks = [i for i in items if i.type == "track"]

            def go():
                if self.host.alive() and not self.closed:
                    self.music_sid = app.play_quiet(tracks)
            app.post(go)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ data
    def _units(self, country):
        u = self.opts.get("units", "auto")
        return weather.units_for(country) if u == "auto" else u

    def _load_cached(self):
        c = weather.load_cache()
        rep = c.get("reply")
        if rep and time.time() - c.get("time", 0) < weather.SHOW_S and \
                c.get("key") == self._key(c.get("place")):
            self.reply, self.grid, self.place = rep, c.get("grid"), c.get("place")
            self.units = c.get("units", "f")
            self.fetched = c.get("time", 0)
            self._pages()
        else:
            self.fetched = 0

    def _key(self, place):
        """The options a cached reply was made for: another place or units fetch again."""
        mode = self.opts.get("place", "auto")
        where = (self.opts.get("where") or {}) if mode == "custom" else {}
        return "%s|%s|%s,%s" % (mode, self.opts.get("units", "auto"), where.get("lat"),
                                where.get("lon")) if place else None

    def _want(self, now):
        if self.host.inset:
            return False                  # a preview box: the cache only, no requests
        if self.loading or now < self.next_try:
            return False
        return time.time() - getattr(self, "fetched", 0) >= weather.FRESH_S

    def _start_fetch(self):
        self.loading = True
        opts = dict(self.opts)
        app = self.app

        def work():
            res = None
            try:
                place = opts.get("where") if opts.get("place") == "custom" else None
                if not place:
                    c = weather.load_cache()
                    g = c.get("geoip")
                    if g and time.time() - c.get("geoip_t", 0) < weather.GEOIP_S:
                        place = g
                    else:
                        place = weather.locate()
                        if place:
                            c["geoip"], c["geoip_t"] = place, time.time()
                            weather.save_cache(c)
                if not place:
                    raise ValueError("no place")
                units = self._units(place.get("country"))
                rep = weather.fetch(place, units)
                grid = None
                try:
                    grid = weather.fetch_grid(place, units)
                    m = Saver.MAP
                    grid["levels"] = weather.grid_frames(grid, units, m.w // Saver.CELL,
                                                         m.h // Saver.CELL)
                except Exception as e:                  # the map page only
                    L.info("weather grid failed: %s", e)
                res = (place, units, rep, grid)
            except Exception as e:
                L.info("weather failed: %s", e)
            post = getattr(app, "post", None)
            if post:
                post(lambda: self._fetched(res))
        threading.Thread(target=work, daemon=True).start()

    def _fetched(self, res):
        self.loading = False
        if res is None:
            self.fails += 1
            self.next_try = time.monotonic() + weather.retry_s(self.fails)
            return
        self.fails = 0
        self.place, self.units, self.reply, self.grid = res
        self.fetched = time.time()
        c = weather.load_cache()
        grid = {k: v for k, v in (self.grid or {}).items() if k != "levels"} or None
        c.update(reply=self.reply, grid=grid, place=self.place, units=self.units,
                 time=self.fetched, key=self._key(self.place))
        weather.save_cache(c)
        self._pages()
        self.cache.clear()
        self.full = True
        L.info("weather: %s", self.place.get("name"))

    def _pages(self):
        if not self.reply:
            self.pages, self.page = ["none"], 0
            return
        lf = weather.local_forecast(self.reply)
        wet = any(v for row in (self.grid or {}).get("cells") or [] for c in row
                  for v in (c or []))
        self.pages = ["current"] + (["local"] if lf else []) + ["extended"] + \
            (["precip"] if wet else [])
        self.page = min(self.page, len(self.pages) - 1)

    # ------------------------------------------------------------ interface
    def set_items(self, items):
        pass

    def first_box(self):
        return None

    closed = False

    def close(self):
        """A wake: stop the playlist only when Weather started it and it still plays."""
        self.closed = True
        mus = getattr(self.app, "music", None)
        if self.music_sid is not None and mus is not None and mus.session_id == self.music_sid \
                and mus.active:
            mus.stop()

    def band(self):
        return self.band_r

    def update(self, t, now):
        from .. import tokens
        if self._want(time.monotonic()):
            self._start_fetch()
        slot = int(t / (2 * tokens.FIELD_MS) + 1e-6)
        if slot == self.slot and not self.full:
            return self._between(now)
        self.slot = slot
        changed_full = self.full
        self.full = False
        if self.page_at is None:
            self.page_at = now
        if now - self.page_at >= PAGE_S:
            self.page = (self.page + 1) % len(self.pages)
            self.page_at = now
            changed_full = True
        ox, oy = self.orbit.at(t)
        off = (2 * ox, 2 * oy)                                # 2 screen px steps, 8 px loop
        if off != self.off:
            self.off = off
            changed_full = True
        self.crawl_x -= CRAWL_PX
        self.band_r = None if changed_full else CRAWL_R.move(self.off).inflate(6, 6)
        if changed_full:
            self.sec = int(time.time())
        return True

    def _between(self, now):
        """The field between two crawl steps: the clock (each second) or the moving icons
        and map, each in its own small band."""
        sec = int(time.time())
        page = self.pages[self.page]
        r = None
        if sec != self.sec:
            self.sec = sec
            r = CLOCK_R
        elif page == "precip" and int(now * 2) != self.anim_k:
            self.anim_k = int(now * 2)
            r = self.MAP
        elif page in ("current", "extended") and int(now * 5) != getattr(self, "_icon_k", None):
            self._icon_k = int(now * 5)
            self.anim_k += 1
            r = ICON_CUR if page == "current" else ICON_EXT
        if r is None:
            return False
        self.band_r = r.move(self.off).inflate(6, 6)
        return True

    # ------------------------------------------------------------ draw
    def draw(self, s):
        """The cached page (frame, title, text), then what moves: icons or the map frame,
        the clock and the crawl."""
        ox, oy = self.off or (0, 0)
        page = self.pages[self.page]
        key = (page, ox, oy, id(self.reply), id(self.grid), self.units)
        if self.cache.get("key") != key:
            surf = pygame.Surface((W, H))
            surf.fill(TOP)
            surf.blit(self.frame, (ox - 8, oy - 8))
            self._title(surf, page, ox, oy)
            getattr(self, "_p_" + page)(surf, ox, oy)
            self.cache = {"key": key, "surf": surf}
        s.blit(self.cache["surf"], (0, 0))
        live = getattr(self, "_live_" + page, None)
        if live is not None:
            live(s, ox, oy)
        self._clock(s, ox, oy)
        self._crawl(s, ox, oy)

    def _title(self, s, page, ox, oy):
        title = {"current": ("Current", "Conditions"), "local": ("Local", "Forecast"),
                 "extended": ("Extended", "Forecast"), "precip": ("Precipitation", "Forecast"),
                 "none": ("Local", "Forecast")}[page]
        text(s, title[0], 26, YELLOW, 132 + ox, 32 + oy)
        text(s, title[1], 26, YELLOW, 132 + ox, 58 + oy)

    def _clock(self, s, ox, oy):
        now = time.localtime()
        clock = time.strftime("%I:%M:%S %p", now).lstrip("0")
        date = time.strftime("%a %b ", now).upper() + str(now.tm_mday)
        text(s, clock, 24, WHITE, 618 + ox, 34 + oy, align="right")
        text(s, date, 24, WHITE, 618 + ox, 60 + oy, align="right")

    def _deg(self):
        return "°F" if self.units == "f" else "°C"

    def _p_none(self, s, ox, oy):
        name = (self.place or {}).get("name") or ""
        text(s, "No Report Available", 34, WHITE, 318 + ox, 200 + oy, align="center")
        if name:
            text(s, name, 28, YELLOW, 318 + ox, 150 + oy, align="center")

    def _p_current(self, s, ox, oy):
        cur = self.reply.get("current") or {}
        name = (self.place or {}).get("name") or ""
        text(s, name, 30, YELLOW, 318 + ox, 106 + oy, align="center")
        temp = cur.get("temperature_2m")
        text(s, "%d°" % round(temp) if temp is not None else "--", 72, WHITE, 168 + ox,
             142 + oy, align="center")
        sky, ic = weather.sky(cur.get("weather_code"), bool(cur.get("is_day", 1)))
        text(s, sky, 28, WHITE, 168 + ox, 226 + oy, align="center")
        x0, x1, y = 316 + ox, 560 + ox, 156 + oy
        p = cur.get("pressure_msl")
        if p is not None:
            p = ("%.2f" % (p * 0.02953)) if self.units == "f" else ("%d" % round(p))
        rows = [("Humidity:", "%d%%" % round(cur["relative_humidity_2m"])
                 if cur.get("relative_humidity_2m") is not None else "--"),
                ("Dewpoint:", "%d°" % round(cur["dew_point_2m"])
                 if cur.get("dew_point_2m") is not None else "--"),
                ("Pressure:", p or "--"),
                ("Feels Like:", "%d°" % round(cur["apparent_temperature"])
                 if cur.get("apparent_temperature") is not None else "--"),
                ("Wind:", weather.wind_text(cur.get("wind_direction_10m"),
                                            cur.get("wind_speed_10m"), self.units)),
                ("Gusts:", "%d" % round(cur["wind_gusts_10m"])
                 if cur.get("wind_gusts_10m") else "None")]
        for label, val in rows:
            text(s, label, 26, WHITE, x0, y)
            text(s, val, 26, WHITE, x1, y, align="right")
            y += 36

    def _live_current(self, s, ox, oy):
        cur = self.reply.get("current") or {}
        _sky, ic = weather.sky(cur.get("weather_code"), bool(cur.get("is_day", 1)))
        s.fill(PANEL, (168 + ox - 56, 310 + oy - 50, 112, 100))
        icon(s, ic, 168 + ox, 310 + oy, 1.0, self.anim_k)

    def _live_extended(self, s, ox, oy):
        for i, (_d, _s, ic, _h, _l) in enumerate(weather.extended(self.reply)):
            cx = 140 + i * 180 + ox
            s.fill(PANEL, (cx - 52, 186 + oy - 46, 104, 92))
            icon(s, ic, cx, 186 + oy, 0.9, self.anim_k + i * 3)

    def _p_local(self, s, ox, oy):
        y = 108 + oy
        f = fonts.get("cond_bold", 25)
        for title, line in weather.local_forecast(self.reply):
            words = ("%s...%s" % (title, line)).upper().split()
            cur = ""
            for w in words:
                nxt = (cur + " " + w).strip()
                if f.size(nxt)[0] > 480:
                    text(s, cur, 25, WHITE, 78 + ox, y)
                    y += 30
                    cur = w
                else:
                    cur = nxt
            if cur:
                text(s, cur, 25, WHITE, 78 + ox, y)
                y += 30
            y += 12

    def _p_extended(self, s, ox, oy):
        days = weather.extended(self.reply)
        for i, (day, sky, ic, hi, lo) in enumerate(days):
            cx = 140 + i * 180 + ox
            text(s, day, 30, YELLOW, cx, 104 + oy, align="center")
            for k, word in enumerate(_wrap(sky, 2)):
                text(s, word, 24, WHITE, cx, 244 + oy + k * 26, align="center")
            text(s, "Lo", 24, YELLOW, cx - 34, 312 + oy, align="center")
            text(s, "Hi", 24, YELLOW, cx + 34, 312 + oy, align="center")
            text(s, str(lo), 34, WHITE, cx - 34, 336 + oy, align="center")
            text(s, str(hi), 34, WHITE, cx + 34, 336 + oy, align="center")

    MAP = pygame.Rect(92, 108, 452, 232)
    CELL = 8

    def _p_precip(self, s, ox, oy):
        g = self.grid or {}
        if not g.get("cells") or not g.get("hours"):
            return self._p_none(s, ox, oy)
        m = self.MAP.move(ox, oy)
        s.fill((14, 44, 30), m)
        for k in range(1, 4):                                  # a faint lat / long grid
            s.fill((22, 60, 42), (m.x, m.y + k * m.h // 4, m.w, 2))
            s.fill((22, 60, 42), (m.x + k * m.w // 4, m.y, 2, m.h))
        lx, ly = m.x + 8, m.bottom + 6
        text(s, "Light", 20, WHITE, lx, ly - 2)
        x = lx + 52
        for col in RADAR:
            s.fill(col, (x, ly + 4, 34, 14))
            x += 36
        text(s, "Heavy", 20, WHITE, x + 6, ly - 2)

    def _frames(self):
        """The map frames from the worker's levels (grid_frames): an 8-bit picture each,
        scaled to 8 px cells."""
        g = self.grid
        if getattr(self, "_fr_for", None) is g:
            return self._fr
        nx, ny = self.MAP.w // self.CELL, self.MAP.h // self.CELL
        levels = g.get("levels") or weather.grid_frames(g, self.units, nx, ny)
        frames = []
        for buf in levels:
            surf = pygame.Surface((nx, ny), depth=8)
            surf.set_palette([(0, 0, 0)] + list(RADAR) + [(0, 0, 0)] * 250)
            surf.get_buffer().write(buf)
            surf.set_colorkey((0, 0, 0))
            frames.append(pygame.transform.scale(surf, (nx * self.CELL, ny * self.CELL)))
        self._fr_for, self._fr = g, frames
        return frames

    def _live_precip(self, s, ox, oy):
        g = self.grid or {}
        if not g.get("cells") or not g.get("hours"):
            return
        frames = self._frames()
        k = self.anim_k % len(frames)
        m = self.MAP.move(ox, oy)
        s.blit(self.cache["surf"], m, m)
        s.blit(frames[k], m.topleft)
        cx, cy = m.centerx, m.centery
        pygame.draw.circle(s, WHITE, (cx, cy), 5)
        text(s, (self.place or {}).get("name", ""), 22, WHITE, cx + 10, cy - 12)
        text(s, g["hours"][k], 26, YELLOW, m.right - 8, m.y + 6, align="right")

    def _crawl(self, s, ox, oy):
        if self.crawl is None or self.crawl[0] != self._crawl_text():
            f = fonts.get("cond_semi", 28)
            msg = self._crawl_text()
            surf = pygame.Surface(f.size(msg + "     "))
            surf.fill(CRAWL)
            sh = f.render(msg, True, SHADOW)
            surf.blit(sh, (2, 2))
            surf.blit(f.render(msg, True, WHITE), (0, 0))
            self.crawl = (msg, surf)
        surf = self.crawl[1]
        w = surf.get_width()
        x = self.crawl_x % w - w
        clip = s.get_clip()
        s.set_clip(pygame.Rect(0, CRAWL_R.y + oy, W, CRAWL_R.h))
        while x < W:
            s.blit(surf, (x, 424 + oy))
            x += w
        s.set_clip(clip)

    def _crawl_text(self):
        parts = []
        if self.place:
            parts.append("Conditions at %s" % self.place.get("name", ""))
        if getattr(self, "fetched", 0):
            parts.append("Updated %s" % time.strftime("%I:%M %p",
                                                      time.localtime(self.fetched)).lstrip("0"))
        parts.append("Weather data by Open-Meteo.com")
        return "   •   ".join(parts)


def _wrap(t, n):
    words = t.split()
    if len(words) <= 1 or len(t) <= 12:
        return [t]
    half = (len(words) + 1) // 2
    return [" ".join(words[:half]), " ".join(words[half:])][:n]


RADAR = ((40, 140, 60), (60, 190, 70), (220, 210, 50), (230, 130, 30), (220, 50, 40))
