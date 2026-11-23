"""Sample-data provider for tests. No network. Artwork is drawn locally; playback uses
mpv's lavfi test sources, or real files from $BATOPLEX_DEMO_MEDIA (4x3.mkv, 16x9.mkv) when present."""
import colorsys
import hashlib
import os
import threading
import time

import pygame

from batoplex import fonts
from batoplex.model import Item, PlayInfo, Row

MOVIES = [
    ("Harbor Lights", 1987, 104, "PG", "A night-shift ferry pilot finds a message in a bottle that predicts each storm a day early."),
    ("The Long Signal", 1992, 118, "PG-13", "Two radio hams on opposite coasts race to decode a broadcast that should not exist."),
    ("Paper Comets", 1979, 96, "G", "A summer of homemade rockets, one stubborn grandmother, and a launch the whole town remembers."),
    ("Neon Orchard", 1995, 131, "R", "An orchard keeper in a flooded future city guards the last apple trees from a fruit syndicate."),
    ("Glass Mountain", 1983, 122, "PG", "Climbers trapped above a frozen lake trade stories while they wait for the ice to hold."),
    ("Midnight Arcade", 1989, 99, "PG-13", "The high score on a haunted cabinet belongs to someone who vanished in 1981."),
    ("Quiet Engines", 1998, 112, "PG", "A retired racer rebuilds a silent electric car for one last cross-desert rally."),
    ("Saltwater Waltz", 1974, 108, "PG", "A lighthouse orchestra plays every Sunday for ships that never come back."),
]
SHOW = ("Static Canyon", 1993, "TV-PG",
        "Teen rangers patrol a canyon where old televisions pick up messages from the future.")
EPISODE_NAMES = [
    ["Channel Zero", "Snow on the Dial", "The Rabbit Ears", "Night Test Pattern", "Vertical Hold",
     "Off the Air", "Color Burst", "Sign Off"],
    ["Rerun", "Ghost Image", "Horizontal Sync", "Prime Time", "Dead Air", "Sweeps Week"],
]


def _hue(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:4], 16) / 65535.0


def _rgb(h, s, v):
    return tuple(int(c * 235) for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


class DemoProvider:
    name = "demo"
    server_id = "demo"
    local_images = True       # drawn with pygame, so the image cache draws on the UI thread

    def __init__(self, settings=None):
        self.settings = settings
        self.lock = threading.Lock()
        self.media = os.environ.get("BATOPLEX_DEMO_MEDIA", "")
        self.items = {}
        self.timeline_log = []
        self.movies = []
        for n, (t, y, mins, cr, summ) in enumerate(MOVIES):
            it = Item(id="m%d" % n, type="movie", title=t, year=y, duration_ms=mins * 60000,
                      content_rating=cr, summary=summ, poster="poster:" + t, art="art:" + t,
                      cast=["Ada Vale", "Rex Moreno", "June Okafor"][: 2 + n % 2],
                      media_note=("480P • 4:3 • Stereo" if n % 3 == 0 else
                                  "1080P • 16:9 • Stereo"))
            it.extra["aspect"] = "4:3" if n % 3 == 0 else "16:9"
            self.movies.append(it)
            self.items[it.id] = it
        self.movies[1].view_offset_ms = 41 * 60000
        self.movies[3].view_offset_ms = 95 * 60000
        self.movies[5].watched = True
        t, y, cr, summ = SHOW
        self.show = Item(id="s0", type="show", title=t, year=y, content_rating=cr, summary=summ,
                         poster="poster:" + t, art="art:" + t, child_count=2,
                         leaf_count=sum(len(x) for x in EPISODE_NAMES))
        self.items["s0"] = self.show
        self.season_items, self.episode_items = [], {}
        for si, names in enumerate(EPISODE_NAMES, 1):
            sid = "s0-%d" % si
            season = Item(id=sid, type="season", title="Season %d" % si, index=si,
                          show_title=t, show_id="s0", poster=self.show.poster, art=self.show.art,
                          leaf_count=len(names))
            self.season_items.append(season)
            self.items[sid] = season
            eps = []
            for ei, name in enumerate(names, 1):
                e = Item(id="%s-%d" % (sid, ei), type="episode", title=name, index=ei,
                         season_index=si, season_id=sid, show_id="s0", show_title=t, year=y,
                         duration_ms=(22 + ei % 3) * 60000, content_rating=cr,
                         summary="Episode %d of season %d. %s" % (ei, si, summ),
                         poster=self.show.poster, art=self.show.art, still="still:%s:%d:%d" % (t, si, ei),
                         media_note="480P • 4:3 • Stereo")
                e.extra["aspect"] = "4:3"
                eps.append(e)
                self.items[e.id] = e
            self.episode_items[sid] = eps
        for e in self.episode_items["s0-1"][:3]:
            e.watched = True
        self.episode_items["s0-1"][3].view_offset_ms = 9 * 60000
        for it in self.items.values():
            it.server_id = self.server_id

    # -- rows
    def _cw(self):
        out = [i for i in self.items.values()
               if i.type in ("movie", "episode") and i.view_offset_ms and not i.watched]
        out.sort(key=lambda i: -i.extra.get("touched", 0))
        return out

    def home_rows(self):
        rows = []
        cw = self._cw()
        if cw:
            rows.append(Row("continue", "Continue Watching", cw))
        rows.append(Row("recent-6", "Recently Added in CRT Movies", list(reversed(self.movies))))
        eps = self.episode_items["s0-2"][::-1] + self.episode_items["s0-1"][::-1]
        rows.append(Row("recent-5", "Recently Added in CRT TV", eps[:8]))
        rows.append(Row("hub-shows", "Saturday Morning", [self.show] + self.movies[::3]))
        return rows

    def libraries(self, cached=False):
        """Video libraries, in the server's order (the side panel pins them)."""
        return [Item(id="lib6", type="library", title="CRT Movies", section_id=6,
                     art="art:CRT Movies", server_id=self.server_id),
                Item(id="lib5", type="library", title="CRT TV", section_id=5, art="art:CRT TV",
                     server_id=self.server_id)]

    def library_rows(self, lib):
        if lib.section_id == 5:
            return [Row("lib-all", "All A–Z", [self.show])]
        allm = sorted(self.movies, key=lambda i: i.title)
        return [Row("lib-recent", "Recently Added", list(reversed(self.movies))),
                Row("lib-all", "All A–Z", allm)]

    def search(self, query, limit=12):
        q = query.strip().lower()
        hit = lambda it: q in it.title.lower()
        groups = (("movie", "Movies", [m for m in self.movies if hit(m)]),
                  ("show", "Shows", [self.show] if hit(self.show) else []),
                  ("episode", "Episodes", [e for s in self.season_items
                                           for e in self.episode_items[s.id] if hit(e)]))
        for _, _, items in groups:
            for it in items:
                it.extra.setdefault("guid", "demo://" + it.id)
        return [Row(k, t, items[:limit]) for k, t, items in groups if items]

    # -- library screen: the same calls as PlexProvider, on local data
    GENRES = ("Comedy", "Horror", "Sci-Fi")

    def _lib_items(self, lib):
        return [self.show] if int(lib.section_id) == 5 else list(self.movies)

    def _genre(self, it):
        return self.GENRES[int(it.id[1:]) % 3] if it.type == "movie" else "Comedy"

    def section_type(self, lib):
        return "show" if int(lib.section_id) == 5 else "movie"

    def unplayed_filter(self, lib):
        return {"unwatchedLeaves": 1} if self.section_type(lib) == "show" else {"unwatched": 1}

    def recommended(self, lib):
        if int(lib.section_id) == 5:
            return [Row("hub-recent", "Recently Added", [self.show])]
        cw = [i for i in self._cw() if i.type == "movie"]
        rows = [Row("hub-continue", "Continue Watching", cw)] if cw else []
        rows += [Row("hub-recent", "Recently Added", list(reversed(self.movies))),
                 Row("hub-unplayed", "Unplayed", [i for i in self.movies if not i.watched])]
        return rows

    def library_meta(self, lib):
        from batoplex.plex import DEFAULT_SORTS
        return {"sorts": [dict(x) for x in DEFAULT_SORTS[:3]],
                "filters": [{"filter": "genre", "title": "Genre", "key": "demo:genre"}]}

    def _filtered(self, lib, filters):
        items = self._lib_items(lib)
        f = filters or {}
        if f.get("unwatched") or f.get("unwatchedLeaves"):
            items = [i for i in items if not i.watched]
        if f.get("genre"):
            items = [i for i in items if self._genre(i) == f["genre"]]
        return items

    def library_page(self, lib, sort, filters, start, size=48):
        items = self._filtered(lib, filters)
        key, _, d = (sort or "titleSort").partition(":")
        fn = {"addedAt": lambda i: int(i.id[1:]) if i.id[1:].isdigit() else 0,
              "originallyAvailableAt": lambda i: i.year or 0}.get(key, lambda i: i.title.lower())
        items.sort(key=fn, reverse=d == "desc")
        return items[start:start + size], len(items)

    def first_characters(self, lib, filters=None):
        out = []
        for i in sorted(self._filtered(lib, filters), key=lambda i: i.title.lower()):
            c = i.title[:1].upper()
            c = c if c.isalpha() else "#"
            if out and out[-1][0] == c:
                out[-1] = (c, out[-1][1] + 1)
            else:
                out.append((c, 1))
        return out

    def collections(self, lib, start, size=48):
        if int(lib.section_id) == 5:
            return [], 0
        c = Item(id="c0", type="collection", title="Midnight Picks", poster="poster:Midnight Picks",
                 art="art:Midnight Picks", server_id=self.server_id, section_id=6)
        return [c][start:start + size], 1

    def collection_items(self, coll, start, size=48):
        items = self.movies[::2]
        return items[start:start + size], len(items)

    def filter_values(self, key):
        return [(g, g) for g in self.GENRES]

    def details(self, item):
        return self.items.get(item.id, item)

    def seasons(self, show_id):
        return list(self.season_items)

    def episodes(self, season_id):
        return list(self.episode_items.get(season_id, []))

    # -- playback
    def play_info(self, item, resume=True, opts=None):
        it = self.items[item.id]
        aspect = it.extra.get("aspect", "16:9")
        dur = 90
        f = os.path.join(self.media, "4x3.mkv" if aspect == "4:3" else "16x9.mkv") if self.media else ""
        if f and os.path.exists(f):
            url = f
        else:
            size = "640x480" if aspect == "4:3" else "1280x720"
            url = "av://lavfi:testsrc2=size=%s:rate=30000/1001:duration=%d" % (size, dur)
        start = min(it.view_offset_ms / 1000.0, dur - 15) if resume and it.view_offset_ms else 0.0
        pi = PlayInfo(url=url, headers={}, start_s=max(0.0, start), duration_ms=dur * 1000,
                      title=it.title if it.type != "episode" else "%s • S%d E%d" % (
                          it.show_title, it.season_index, it.index))
        if f and os.path.exists(f):
            pi.sub_pos = 1
        return pi, it

    def timeline(self, item, state, time_ms, duration_ms, session_id):
        self.timeline_log.append((item.id, state, int(time_ms)))
        it = self.items.get(item.id)
        if it is not None and time_ms:
            it.view_offset_ms = int(time_ms)
            it.extra["touched"] = time.time()

    def scrobble(self, item):
        it = self.items.get(item.id)
        if it is not None:
            it.watched, it.view_offset_ms = True, 0

    def unscrobble(self, item):
        it = self.items.get(item.id)
        if it is not None:
            it.watched = False

    # -- artwork
    def image(self, ref, w, h):
        if not ref:
            return None
        kind, _, name = ref.partition(":")
        with self.lock:
            if kind == "poster":
                return self._poster(name, w, h)
            if kind == "still":
                return self._backdrop(name, w, h, still=True)
            return self._backdrop(name, w, h)

    def _poster(self, title, w, h):
        hue = _hue(title)
        s = pygame.Surface((w, h))
        top, bot = _rgb(hue, 0.55, 0.62), _rgb(hue + 0.08, 0.75, 0.18)
        for y in range(h):
            t = y / (h - 1)
            s.fill(tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)), (0, y, w, 1))
        # motif: sun disc and horizon bands, sized to the poster
        cx, cy, r = w // 2, int(h * 0.40), int(w * 0.30)
        pygame.draw.circle(s, _rgb(hue + 0.5, 0.45, 0.92), (cx, cy), r)
        for k in range(4):
            y = cy + r // 3 + k * max(4, h // 28)
            pygame.draw.rect(s, bot, (0, y - (y % 2), w, max(2, h // 64) * 2))
        pygame.draw.rect(s, _rgb(hue, 0.4, 0.10), (0, int(h * 0.62), w, h))
        f = fonts.get("cond_bold", max(14, w // 6))
        words, lines, cur = title.upper().split(), [], ""
        for wd in words:
            if f.size((cur + " " + wd).strip())[0] <= w - w // 8:
                cur = (cur + " " + wd).strip()
            else:
                lines.append(cur)
                cur = wd
        lines.append(cur)
        y = int(h * 0.66)
        for ln in lines[:3]:
            img = f.render(ln, True, (235, 235, 230))
            s.blit(img, ((w - img.get_width()) // 2, y))
            y += f.get_linesize()
        return s

    def _backdrop(self, name, w, h, still=False):
        hue = _hue(name.split(":")[0]) + (0.03 * len(name) if still else 0)
        s = pygame.Surface((w, h))
        a, b = _rgb(hue + 0.05, 0.6, 0.55), _rgb(hue + 0.55, 0.7, 0.20)
        for x in range(0, w, 2):
            t = x / (w - 1)
            s.fill(tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3)), (x, 0, 2, h))
        for k in range(6):
            r = int(h * (0.18 + 0.09 * k))
            col = _rgb(hue + 0.1 * k, 0.5, 0.75 - 0.08 * k)
            pygame.draw.circle(s, col, (int(w * 0.72), int(h * 0.42)), r, max(2, h // 60) * 2)
        pygame.draw.rect(s, _rgb(hue, 0.4, 0.12), (0, int(h * 0.74), w, h))
        return s
