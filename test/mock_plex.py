"""Mock Plex Media Server for desktop tests. Serves JSON like PMS, the two test files with
HTTP Range support, an external subtitle, and photo transcodes (generated with pygame).
It records /:/timeline and /:/scrobble calls, and flags any request that puts the token in
the URL (the client must send it only as a header)."""
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
import pygame  # noqa: E402

TOKEN = os.environ.get("MOCK_TOKEN", "mock-token-123456")
MOCK_ID = os.environ.get("MOCK_ID", "mock")             # machineIdentifier
MOCK_NAME = os.environ.get("MOCK_NAME", "Mock")
MOVIES_TITLE = os.environ.get("MOCK_MOVIES_TITLE", "CRT Movies")
# A Plex Home user's token for this server is TOKEN + "-" + user (see plex.tv below).
HOME_USERS = [{"uuid": "u-owner", "id": 1, "title": "Owner", "protected": False, "admin": True},
              {"uuid": "u-mom", "id": 2, "title": "Mom", "protected": False, "admin": False},
              {"uuid": "u-kid", "id": 3, "title": "Kid", "protected": True, "admin": False,
               "restricted": True}]
ACCOUNT_TOKEN = "mock-account-token"
PIN = "1234"
PEERS = [p for p in os.environ.get("MOCK_PEERS", "").split(",") if p]   # id:name:port:token
SERVER_DOWN = os.environ.get("MOCK_SERVER_DOWN") == "1"   # the server part does not answer


def server_token_ok(tok):
    return tok == TOKEN or (tok or "").startswith(TOKEN + "-u-")
MEDIA = os.environ.get("MOCK_MEDIA", os.path.join(os.path.dirname(__file__), "media"))
LOG = os.environ.get("MOCK_LOG", "/tmp/mock_plex.log")
pygame.font.init()
LOCK = threading.Lock()
STATE = {"viewOffset": {"101": 20_000, "2001": 20_000}, "viewCount": {}}


def dir_station(uuid, name, n, tags):
    url = "http://127.0.0.1:32400/radio/loop.wav?s=%d" % n
    return {"stationuuid": uuid, "name": name, "url": url, "url_resolved": url, "tags": tags,
            "countrycode": "US", "codec": "WAV", "bitrate": 352, "hls": 0, "lastcheckok": 1,
            "ssl_error": 0}


# Invented stations for Add Stations.
DIR_STATIONS = [dir_station("9a1c0000-0000-4000-8000-000000000001", "Test Jazz One", 1,
                            "jazz,smooth"),
                dir_station("9a1c0000-0000-4000-8000-000000000002", "Test Rock Two", 2,
                            "rock,classic rock")]


def log(kind, **kw):
    with LOCK, open(LOG, "a") as f:
        f.write(json.dumps({"kind": kind, **kw}) + "\n")


# Watchlist: each movie and show has a guid. One guid is on the two mocks with
# different local keys (113 here, 125 on mock2); the others are unique to their server.
BOTH_GUID = "plex://movie/wlboth"


def guid_of(sid, rk, typ="movie"):
    if (sid, rk) in (("mock", "113"), ("mock2", "125")):
        return BOTH_GUID
    return "plex://%s/%s-%s" % (typ, sid, rk)


def movie(rk, title, year, dur, ar, part, thumb):
    m = {"ratingKey": rk, "key": "/library/metadata/%s" % rk, "type": "movie", "title": title,
         "guid": guid_of(MOCK_ID, rk),
         "year": year, "duration": dur, "contentRating": "PG", "summary": "Test movie %s." % title,
         "thumb": "/library/metadata/%s/thumb/1" % thumb, "art": "/library/metadata/%s/art/1" % thumb,
         "librarySectionID": 6, "Role": [dict(r) for r in ROLES],
         "originallyAvailableAt": "%d-0%d-15" % (year, 1 + int(rk) % 9),
         "Media": [{"videoResolution": "720" if ar > 1.5 else "480", "aspectRatio": ar,
                    "audioChannels": 2, "Part": [{"id": int(rk), "key": "/library/parts/%s/1/file.mkv" % rk,
                                                  "duration": dur, "Stream": part(int(rk))}]}]}
    return m


# People with tag ids; their thumbs are absolute URLs on a metadata host, which the
# client must fetch only through the server's photo transcoder.
ROLES = [{"id": 501, "tag": "Test Actor", "role": "Captain Test",
          "thumb": "https://metadata.invalid/people/501.jpg"},
         {"id": 502, "tag": "Second Actor", "role": "First Mate"}]
DIRECTOR = [{"id": 601, "tag": "Test Director", "thumb": "https://metadata.invalid/people/601.jpg"}]


def has_person(m, q):
    """The actor= / director= / writer= filters of a section listing."""
    for key, field in (("actor", "Role"), ("director", "Director"), ("writer", "Writer")):
        if q.get(key) and not any(str(r.get("id")) == q[key] for r in m.get(field) or []):
            return False
    return True


# Each part gets its own stream list with real ids (part id * 10 + n), so a PUT to one part
# never changes another; the external subtitle keeps the fixed id 900 (its URL).
def WIDE_STREAMS(pid):
    return [{"streamType": 1, "index": 0, "id": pid * 10},
            {"streamType": 2, "index": 1, "id": pid * 10 + 1, "selected": True, "codec": "aac",
             "channels": 2, "language": "English", "languageCode": "eng", "displayTitle": "English (AAC Stereo)"},
            {"streamType": 3, "id": 900, "key": "/library/streams/900", "codec": "srt",
             "language": "English", "languageCode": "eng", "displayTitle": "English (SRT External)",
             "selected": True}]


def EP_STREAMS(pid):
    return [{"streamType": 1, "index": 0, "id": pid * 10},
            {"streamType": 2, "index": 1, "id": pid * 10 + 1, "selected": True, "codec": "aac",
             "channels": 2, "language": "English", "languageCode": "eng", "displayTitle": "English (AAC Stereo)"},
            {"streamType": 3, "index": 2, "id": pid * 10 + 2, "codec": "srt", "selected": True,
             "language": "English", "languageCode": "eng", "displayTitle": "English (SRT)"}]
MOVIES = [movie("101", "Widescreen Test Movie", 2001, 60000, 1.78, WIDE_STREAMS, "101")] + \
    [movie(str(110 + i), "Filler Movie %d" % i, 1980 + i, 60000, 1.33, WIDE_STREAMS, str(110 + i))
     for i in range(1, 60)]
# Ratings: the old fields on movie 101, a Rating array on 111, none on others.
MOVIES[0].update(rating=8.7, ratingImage="rottentomatoes://image.rating.ripe",
                 audienceRating=9.1, audienceRatingImage="rottentomatoes://image.rating.upright",
                 Director=[dict(d) for d in DIRECTOR],
                 summary="A test movie with a long summary so the page shows how many lines it "
                         "keeps under the rating badges: a ferry pilot, a lighthouse, a storm "
                         "that comes a day early, and a radio that will not stop talking.")
MOVIES[1]["Rating"] = [{"image": "imdb://image.rating", "value": 7.4, "type": "audience"},
                       {"image": "themoviedb://image.rating", "value": 6.8, "type": "audience"}]
# Extras: a trailer clip; its part id starts with 1, so it plays 16x9.mkv.
EXTRAS = {"101": [{"ratingKey": "5101", "key": "/library/metadata/5101", "type": "clip",
                   "subtype": "trailer", "extraType": 1, "title": "Theatrical Trailer",
                   "duration": 60000, "thumb": "/library/metadata/5101/thumb/1",
                   "Media": [{"Part": [{"id": 1501, "duration": 60000,
                                        "key": "/library/parts/1501/1/file.mkv"}]}]},
                  {"ratingKey": "5102", "type": "clip", "subtype": "behindTheScenes",
                   "title": "No Media Here", "Media": []},
                  {"ratingKey": "5103", "type": "clip", "extraType": 5, "title": "Making Of",
                   "duration": 180000, "thumb": "/library/metadata/5103/thumb/1",
                   "Media": [{"Part": [{"id": 1503, "duration": 60000,
                                        "key": "/library/parts/1503/1/file.mkv"}]}]}]}
SHOW = {"ratingKey": "2000", "key": "/library/metadata/2000/children", "type": "show",
        "title": "Four Three Show", "year": 1994, "childCount": 1, "leafCount": 3, "viewedLeafCount": 0,
        "contentRating": "TV-PG", "duration": 60000, "Genre": [{"tag": "Comedy"}],
        "thumb": "/library/metadata/2000/thumb/1", "art": "/library/metadata/2000/art/1",
        "librarySectionID": 5, "summary": "A 4:3 show.", "Role": [dict(ROLES[0])],
        "originallyAvailableAt": "1994-09-10", "guid": guid_of(MOCK_ID, "2000", "show")}
SEASON = {"ratingKey": "2010", "type": "season", "title": "Season 1", "index": 1, "parentTitle": SHOW["title"],
          "parentRatingKey": "2000", "leafCount": 3, "viewedLeafCount": 0,
          "thumb": "/library/metadata/2000/thumb/1", "librarySectionID": 5}


def episode(n):
    rk = str(2000 + n)
    return {"ratingKey": rk, "key": "/library/metadata/%s" % rk, "type": "episode",
            "title": "Episode %d" % n, "index": n, "parentIndex": 1, "parentRatingKey": "2010",
            "grandparentRatingKey": "2000", "grandparentTitle": SHOW["title"], "duration": 60000,
            "summary": "Episode %d summary." % n, "thumb": "/library/metadata/%s/thumb/1" % rk,
            "grandparentThumb": "/library/metadata/2000/thumb/1", "librarySectionID": 5,
            "grandparentTheme": "/library/metadata/2000/theme/1",
            # An episode has guest stars, a director, a writer, one rating.
            "Role": [{"id": 503, "tag": "Guest Star", "role": "Visitor %d" % n}],
            "Director": [dict(d) for d in DIRECTOR], "Writer": [{"id": 701, "tag": "Test Writer"}],
            "audienceRating": 7.9, "audienceRatingImage": "themoviedb://image.rating",
            "Media": [{"videoResolution": "480", "aspectRatio": 1.33, "audioChannels": 2,
                       "Part": [{"id": int(rk), "key": "/library/parts/%s/1/file.mkv" % rk,
                                 "duration": 60000, "Stream": EP_STREAMS(int(rk))}]}],
            # Episode 2 has an intro and final credits (markers exist only with Plex Pass).
            "Marker": ([{"type": "intro", "startTimeOffset": 2000, "endTimeOffset": 12000},
                        {"type": "credits", "startTimeOffset": 50000, "endTimeOffset": 60000,
                         "final": True}] if n == 2 else [])}


EPISODES = [episode(n) for n in (1, 2, 3)]

# Music: section 9, 2 artists, 3 albums, 6 s FLAC tones (tone1, tone2).
def _artist(rk, title):
    return {"ratingKey": rk, "key": "/library/metadata/%s/children" % rk, "type": "artist",
            "title": title, "thumb": "/library/metadata/%s/thumb/1" % rk, "librarySectionID": 9,
            "childCount": 0}


def _album(rk, title, year, artist):
    return {"ratingKey": rk, "key": "/library/metadata/%s/children" % rk, "type": "album",
            "title": title, "year": year, "parentTitle": artist["title"],
            "parentRatingKey": artist["ratingKey"], "thumb": "/library/metadata/%s/thumb/1" % rk,
            "librarySectionID": 9, "leafCount": 0}


def _track(rk, n, title, album, artist, original=None):
    t = {"ratingKey": rk, "key": "/library/metadata/%s" % rk, "type": "track", "title": title,
         "index": n, "parentIndex": 1, "duration": 6000, "parentTitle": album["title"],
         "parentRatingKey": album["ratingKey"], "grandparentTitle": artist["title"],
         "grandparentRatingKey": artist["ratingKey"], "parentThumb": album["thumb"],
         "parentYear": album["year"], "librarySectionID": 9,
         "Media": [{"Part": [{"id": int(rk), "key": "/library/parts/%s/1/file.flac" % rk}]}]}
    if original:
        t["originalTitle"] = original
    return t


ARTISTS = [_artist("3000", "Cathode Choir"), _artist("3001", "Vertical Hold")]
ALBUMS = [_album("3100", "Warm Up", 1984, ARTISTS[0]), _album("3101", "Test Card", 1989, ARTISTS[0]),
          _album("3102", "Sync Pulse", 1991, ARTISTS[1])]
TRACKS = [_track(str(3200 + i), i % 4 + 1, "Track %d" % (i + 1), ALBUMS[i // 4],
                 ARTISTS[0] if i < 8 else ARTISTS[1], original="Guest Star" if i == 2 else None)
          for i in range(12)]
for _a in ALBUMS:
    _a["leafCount"] = sum(1 for t in TRACKS if t["parentRatingKey"] == _a["ratingKey"])
for _a in ARTISTS:
    _a["childCount"] = sum(1 for a in ALBUMS if a["parentRatingKey"] == _a["ratingKey"])
ALL = {m["ratingKey"]: m for m in MOVIES + EPISODES + [SHOW, SEASON] + ARTISTS + ALBUMS + TRACKS}
PARTS = {p["id"]: p for m in MOVIES + EPISODES for md in m["Media"] for p in md["Part"]}

# Live TV (MOCK_LIVE=1): section 7, one show of 3,000 one-minute episodes (50 h, so Make
# Channels for Me proposes it). Off by default: the other runs keep their libraries.
LIVE = os.environ.get("MOCK_LIVE") == "1"
LSHOW = {"ratingKey": "7000", "key": "/library/metadata/7000/children", "type": "show",
         "title": "Test Pattern Hour", "year": 1990, "childCount": 1, "leafCount": 3000,
         "duration": 60000, "studio": "Test Net", "librarySectionID": 7, "summary": "Test cards."}


def live_episode(n):
    rk = str(70000 + n)
    return {"ratingKey": rk, "key": "/library/metadata/%s" % rk, "type": "episode",
            "title": "Pattern %d" % n, "index": n, "parentIndex": 1, "parentRatingKey": "7001",
            "grandparentRatingKey": "7000", "grandparentTitle": LSHOW["title"], "duration": 60000,
            "summary": "Test card number %d." % n, "librarySectionID": 7,
            "Media": [{"videoResolution": "480", "aspectRatio": 1.33, "audioChannels": 2,
                       "Part": [{"id": int(rk), "key": "/library/parts/%s/1/file.mkv" % rk,
                                 "duration": 60000, "Stream": EP_STREAMS(int(rk))}]}]}


LEPS = [live_episode(n) for n in range(1, 3001)] if LIVE else []
if LIVE:
    ALL.update({m["ratingKey"]: m for m in LEPS + [LSHOW]})


# Playlists: one store per Plex Home user on each mock server, so a user never
# sees or changes another user's lists. The two mock servers use the same playlist
# ids. Mock "mock"'s video list has an entry of the shared server (sourceURI), whose rating key
# also exists here: its media and reports must go to mock2.
PL_WRITES = os.environ.get("MOCK_PLAYLIST_WRITES", "1") == "1"
PL_SEQ = [9000]


def _pl_entry(rk, source=None):
    PL_SEQ[0] += 1
    return {"rk": rk, "pid": PL_SEQ[0], "source": source}


def _pl_seed():
    if MOCK_ID == "mock":
        video = [_pl_entry("101"), _pl_entry("2001"), _pl_entry("111", "mock2")]
    else:
        video = [_pl_entry("112"), _pl_entry("113")]
    return {
        "801": {"title": "Road Trip", "type": "video", "smart": 0, "section": None, "v": 1,
                "entries": video},
        "802": {"title": "Drive Mix", "type": "audio", "smart": 0, "section": 9, "v": 1,
                "entries": [_pl_entry(str(rk)) for rk in (3204, 3205, 3206, 3207)]},
        "803": {"title": "Smart Mix", "type": "audio", "smart": 1, "section": 9, "v": 1,
                "entries": [_pl_entry(str(rk)) for rk in (3200, 3201, 3202)]},
    }


PLAYLISTS = {}                      # user -> {playlist id: playlist}
PL_NEXT = [900]


def user_playlists(user):
    with LOCK:
        if user not in PLAYLISTS:
            PLAYLISTS[user] = _pl_seed() if user == "owner" else {}
        return PLAYLISTS[user]


def pl_meta(rk, pl):
    items = [ALL[e["rk"]] for e in pl["entries"] if e["rk"] in ALL]
    return {"ratingKey": rk, "key": "/playlists/%s/items" % rk, "type": "playlist",
            "title": pl["title"], "playlistType": pl["type"], "smart": pl["smart"],
            "leafCount": len(items), "duration": sum(int(m.get("duration") or 0) for m in items),
            "composite": "/playlists/%s/composite/%d" % (rk, pl["v"])}


def pl_entries(pl):
    out = []
    for e in pl["entries"]:
        if e["rk"] not in ALL:
            continue
        m = dict(live(ALL[e["rk"]]), playlistItemID=e["pid"])
        if e["source"]:
            m["sourceURI"] = "server://%s/com.plexapp.plugins.library/library/metadata/%s" % (
                e["source"], e["rk"])
        out.append(m)
    return out


def uri_ids(uri):
    m = re.match(r"server://([^/]+)/com\.plexapp\.plugins\.library/library/metadata/([\d,]+)$", uri or "")
    if not m or m.group(1) != MOCK_ID:
        return None
    ids = []
    for rk in m.group(2).split(","):
        # A container adds its leaves.
        if rk in ALL and ALL[rk]["type"] in ("show", "season"):
            ids += [e["ratingKey"] for e in EPISODES]
        elif rk in ALL and ALL[rk]["type"] == "album":
            ids += [t["ratingKey"] for t in TRACKS if t["parentRatingKey"] == rk]
        elif rk in ALL and ALL[rk]["type"] == "artist":
            ids += [t["ratingKey"] for t in TRACKS if t["grandparentRatingKey"] == rk]
        elif rk in ALL:
            ids.append(rk)
    return ids


# The Discover host, served by the plex.tv mock: one list per Plex Home user.
# The owner's list: the title on two servers, two movies and the show of this mock, and one
# title on no server ("Nowhere Movie"). Only the account and user tokens are accepted.
WATCH_KNOWN = {}
for _sid in ("mock", "mock2"):
    for _m in MOVIES:
        _g = guid_of(_sid, _m["ratingKey"])
        WATCH_KNOWN[_g.rsplit("/", 1)[1]] = (_g, "movie", _m["title"])
    _g = guid_of(_sid, "2000", "show")
    WATCH_KNOWN[_g.rsplit("/", 1)[1]] = (_g, "show", SHOW["title"])
WATCH_KNOWN["wlnowhere"] = ("plex://movie/wlnowhere", "movie", "Nowhere Movie")
WATCH = {}


def watch_entry(key, at):
    g, t, title = WATCH_KNOWN[key]
    return {"ratingKey": key, "key": "/library/metadata/%s" % key, "guid": g, "type": t,
            "title": title, "watchlistedAt": at}


def user_watchlist(user):
    with LOCK:
        if user not in WATCH:
            WATCH[user] = ([watch_entry(k, 1700000500 - 100 * i) for i, k in enumerate(
                ("wlboth", "mock-101", "mock-2000", "wlnowhere", "mock-112"))]
                if user == "u-owner" and MOCK_ID == "mock" else [])
        return WATCH[user]


def genre_id(m):
    return 21 + int(m["ratingKey"]) % 2          # 21 Comedy, 22 Horror


def live(m):
    m = dict(m)
    rk = m["ratingKey"]
    if STATE["viewOffset"].get(rk):
        m["viewOffset"] = STATE["viewOffset"][rk]
    if STATE["viewCount"].get(rk):
        m["viewCount"] = STATE["viewCount"][rk]
    return m


def container(items, start=0, size=None, total=None):
    sl = items[start:start + size] if size else items[start:]
    return {"MediaContainer": {"size": len(sl), "totalSize": total or len(items), "offset": start,
                               "Metadata": [live(m) for m in sl]}}


def photo(path, w, h):
    s = pygame.Surface((w, h))
    hue = sum(map(ord, path)) % 360
    c = pygame.Color(0)
    c.hsva = (hue, 60, 70, 100)
    s.fill(c)
    pygame.draw.circle(s, (230, 230, 220), (w // 2, h // 2), min(w, h) // 3, 4)
    f = pygame.font.Font(None, max(16, w // 6))
    t = f.render(path.split("/")[3] if path.count("/") > 3 else "?", True, (16, 16, 16))
    s.blit(t, ((w - t.get_width()) // 2, (h - t.get_height()) // 2))
    buf = io.BytesIO()
    pygame.image.save(s, buf, "x.jpg")
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        p = u.path
        if TOKEN in self.path:
            log("TOKEN_IN_URL", path=p)
        if SERVER_DOWN and not (p.startswith(("/api/", "/users/", "/cdn/"))
                                or p == "/library/sections/watchlist/all"):
            log("server_down", path=p)            # plex.tv and Discover still answer
            return self.send_json({}, 503)
        if p == "/identity":                      # real servers answer this without a token
            log("identity", token=bool(self.headers.get("X-Plex-Token")))
            return self.send_json({"MediaContainer": {"machineIdentifier": MOCK_ID}})
        if p.startswith("/json/"):                # the station directory
            log("radiodir", path=p, name=q.get("name"), ua=self.headers.get("User-Agent", ""),
                accept=self.headers.get("Accept", ""), token=bool(self.headers.get("X-Plex-Token")),
                client=bool(self.headers.get("X-Plex-Client-Identifier")))
            if p == "/json/stations/topclick/50" or p == "/json/stations/search":
                name = (q.get("name") or "").lower()
                return self.send_json([s for s in DIR_STATIONS if name in s["name"].lower()])
            return self.send_json({}, 404)
        if p == "/radio/loop.wav":                # a continuous stream, no Content-Length
            log("radio_loop", token=bool(self.headers.get("X-Plex-Token")),
                client=bool(self.headers.get("X-Plex-Client-Identifier")))
            return self.serve_loop()
        if p == "/radio/live.flac":               # a radio stream: no Plex token
            log("radio", token=bool(self.headers.get("X-Plex-Token")),
                client=bool(self.headers.get("X-Plex-Client-Identifier")))
            return self.serve_file(os.path.join(MEDIA, "tone1.flac"), "audio/flac")
        m = re.match(r"/users/([\w-]+)/avatar$", p)
        if m:                                     # plex.tv sends avatars on to an image host
            self.send_response(302)
            self.send_header("Location", "http://localhost:%d/cdn/%s.jpg" % (
                self.server.server_port, m.group(1)))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        m = re.match(r"/cdn/([\w-]+)\.jpg$", p)
        if m:                                     # the image host: public, no token
            log("avatar", user=m.group(1), token=bool(self.headers.get("X-Plex-Token")))
            data = photo("/users/%s/avatar/A" % m.group(1), 160, 160)
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if p.startswith("/api/"):
            return self.plex_tv("GET", p, q)
        if p == "/library/sections/watchlist/all":
            return self.discover("GET", p, q)
        if not server_token_ok(self.headers.get("X-Plex-Token")):
            log("unauthorized", path=p)
            return self.send_json({}, 401)
        start = int(self.headers.get("X-Plex-Container-Start") or q.get("X-Plex-Container-Start") or 0)
        size = int(self.headers.get("X-Plex-Container-Size") or q.get("X-Plex-Container-Size") or 0) or None
        log("get", path=p, user=self.token_user(), start=start, size=size)
        if p == "/identity":
            return self.send_json({"MediaContainer": {"machineIdentifier": "mock"}})
        if p == "/library/sections":
            dirs = [{"key": "6", "title": MOVIES_TITLE, "type": "movie", "contentChangedAt": 1001},
                    {"key": "5", "title": "CRT TV", "type": "show", "contentChangedAt": 1002},
                    {"key": "9", "title": "Music", "type": "artist", "contentChangedAt": 1003}]
            if LIVE:
                dirs.insert(2, {"key": "7", "title": "Test Channels", "type": "show",
                                "contentChangedAt": 1004})
            return self.send_json({"MediaContainer": {"Directory": dirs}})
        if LIVE and p == "/library/sections/7/all":
            return self.send_json(container(LEPS if q.get("type") == "4" else [LSHOW], start, size))
        if LIVE and p == "/library/metadata/7000/allLeaves":
            return self.send_json(container(LEPS, start, size))
        if LIVE and p in ("/library/sections/7/genre", "/library/sections/7/network",
                          "/library/sections/7/collections"):
            return self.send_json({"MediaContainer": {"size": 0, "Directory": [], "Metadata": []}})
        if p == "/hubs/continueWatching/items":
            lv = int(os.environ.get("MOCK_LAST_VIEWED", "1700000000"))
            items = [dict(ALL[rk], lastViewedAt=lv + int(rk)) for rk, v in STATE["viewOffset"].items()
                     if v and not STATE["viewCount"].get(rk)]
            return self.send_json(container(items))
        if p == "/library/sections/6/recentlyAdded":
            return self.send_json(container(MOVIES[:20], start, size))
        if p == "/library/sections/5/recentlyAdded":
            return self.send_json(container(EPISODES[::-1], start, size))
        if p == "/hubs/sections/9":
            return self.send_json({"MediaContainer": {"Hub": [
                {"hubIdentifier": "music.recent.added.9", "title": "Recently Added",
                 "size": len(ALBUMS), "Metadata": ALBUMS},
                {"hubIdentifier": "music.stations.9", "title": "Stations", "type": "station",
                 "Metadata": [{"key": "/library/sections/9/stations/1", "title": "Library Radio",
                               "type": "playlist", "radio": 1}]},
                {"hubIdentifier": "music.artists.9", "title": "Artists", "size": len(ARTISTS),
                 "Metadata": ARTISTS},
                {"hubIdentifier": "music.recent.played.9", "title": "Recently Played",
                 "Metadata": ALBUMS[:1]}]}})
        if p == "/hubs/sections/6":
            return self.send_json({"MediaContainer": {"Hub": [
                {"hubIdentifier": "movie.recentlyadded.6", "title": "Recently Added Movies",
                 "key": "/library/sections/6/recentlyAdded", "size": 12, "more": True,
                 "Metadata": [live(m) for m in MOVIES[:12]]},
                {"hubIdentifier": "movie.empty", "title": "Empty Hub", "Metadata": []}]}})
        if p == "/library/sections/6/all" and q.get("includeMeta"):
            return self.send_json({"MediaContainer": {"size": 0, "totalSize": len(MOVIES), "Meta": {"Type": [
                {"type": "movie", "Sort": [
                    {"key": "titleSort", "title": "Title", "defaultDirection": "asc"},
                    {"key": "addedAt", "title": "Date Added", "defaultDirection": "desc"}],
                 "Filter": [{"filter": "genre", "title": "Genre",
                             "key": "/library/sections/6/genre"}]}]}}})
        if p == "/library/sections/6/genre":
            return self.send_json({"MediaContainer": {"Directory": [
                {"key": "21", "title": "Comedy"}, {"key": "22", "title": "Horror"}]}})
        if p in ("/library/sections/6/all", "/library/sections/6/firstCharacter"):
            items = MOVIES
            if q.get("genre"):
                items = [m for m in items if str(genre_id(m)) == q["genre"]]
            if q.get("unwatched"):
                items = [m for m in items if not STATE["viewCount"].get(m["ratingKey"])]
            items = [m for m in items if has_person(m, q)]
            key, _, d = (q.get("sort") or "titleSort").partition(":")
            items = sorted(items, key=lambda m: m["title"] if key == "titleSort" else int(m["ratingKey"]),
                           reverse=d == "desc")
            if p.endswith("firstCharacter"):
                counts = {}
                for m in sorted(items, key=lambda m: m["title"]):
                    counts[m["title"][0].upper()] = counts.get(m["title"][0].upper(), 0) + 1
                return self.send_json({"MediaContainer": {"Directory": [
                    {"key": c, "title": c, "size": n} for c, n in counts.items()]}})
            return self.send_json(container(items, start, size or 50))
        if p == "/library/sections/6/collections":
            return self.send_json({"MediaContainer": {"size": 1, "totalSize": 1, "Metadata": [
                {"ratingKey": "77", "type": "collection", "title": "Test Collection",
                 "librarySectionID": 6, "childCount": 5, "thumb": "/library/metadata/77/thumb/1"}]}})
        if p == "/library/collections/77/children":
            return self.send_json(container(MOVIES[::12], start, size))
        if p == "/library/sections/5/genre":       # 51 Comedy, 52 Animation
            return self.send_json({"MediaContainer": {"Directory": [
                {"key": "51", "title": "Comedy"}, {"key": "52", "title": "Animation"}]}})
        if p == "/library/sections/5/all":
            items = [m for m in [SHOW] if has_person(m, q)]
            if q.get("genre"):                    # K1,K2 is OR, as on the box
                items = [m for m in items if "51" in q["genre"].split(",")]
            return self.send_json(container(items, start, size))
        if p == "/library/metadata/2000/allLeaves":
            return self.send_json(container(EPISODES, start, size))
        if p == "/hubs":
            return self.send_json({"MediaContainer": {"Hub": [
                {"hubIdentifier": "home.continue", "title": "Continue", "type": "mixed", "Metadata": []},
                {"hubIdentifier": "collection.11", "title": "Promoted Collection", "type": "movie",
                 "key": "/hubs/promoted/11", "size": 3, "more": False,
                 "Metadata": [live(MOVIES[5]), live(SHOW), live(MOVIES[7])]}]}})
        if p == "/playlists" or p.startswith("/playlists/"):
            return self.playlists_get(p, q, start, size)
        if p == "/hubs/search":
            delay = int(os.environ.get("MOCK_SEARCH_DELAY_MS", "0"))
            if delay:
                time.sleep(delay / 1000.0)
            needle = q.get("query", "").lower()
            lim = int(q.get("limit") or 10)
            log("search", query=q.get("query", ""), limit=lim, user=self.token_user())
            hubs = []
            for t, title, pool in (("artist", "Artists", ARTISTS), ("movie", "Movies", MOVIES),
                                   ("show", "Shows", [SHOW]), ("episode", "Episodes", EPISODES),
                                   ("album", "Albums", ALBUMS), ("track", "Tracks", TRACKS)):
                hits = [dict(live(m), guid="plex://%s/%s" % (t, m["ratingKey"]))
                        for m in pool if needle in m["title"].lower()][:lim]
                hubs.append({"type": t, "title": title, "hubIdentifier": t, "size": len(hits),
                             "Metadata": hits})
            hubs.append({"type": "actor", "title": "People", "size": 0})
            return self.send_json({"MediaContainer": {"size": len(hubs), "Hub": hubs}})
        if p == "/library/all" and q.get("guid"):     # the Watchlist lookup
            want = set(q["guid"].split(","))
            log("guid_lookup", n=len(want), user=self.token_user())
            hits = [m for m in MOVIES + [SHOW] if m.get("guid") in want]
            return self.send_json(container(hits))
        if p == "/library/sections/9/all":
            pool = {"8": ARTISTS, "9": ALBUMS, "10": TRACKS}.get(q.get("type", "8"), ARTISTS)
            if q.get("sort") == "random":
                log("music_random", type=q.get("type"))
                pool = list(reversed(pool))
            return self.send_json(container(pool, start, size))
        m = re.match(r"/library/metadata/(30\d\d|31\d\d)/(children|allLeaves)$", p)
        if m:
            rk, kind = m.groups()
            if kind == "allLeaves":
                items = [t for t in TRACKS if t["grandparentRatingKey"] == rk]
            elif rk in {a["ratingKey"] for a in ARTISTS}:
                items = [a for a in ALBUMS if a["parentRatingKey"] == rk]
            else:
                items = [t for t in TRACKS if t["parentRatingKey"] == rk]
            return self.send_json(container(items))
        if p == "/library/metadata/2000/children":
            return self.send_json(container([SEASON]))
        if p == "/library/metadata/2010/children":
            return self.send_json(container(EPISODES))
        m = re.match(r"/library/metadata/(\d+)$", p)
        if m and m.group(1) == SHOW["ratingKey"] and q.get("includeOnDeck"):
            nxt = [e for e in EPISODES if not STATE["viewCount"].get(e["ratingKey"])]
            show = dict(SHOW, OnDeck={"Metadata": [live(nxt[0])]} if nxt else {})
            return self.send_json(container([show]))
        if m and m.group(1) in ALL:
            return self.send_json(container([ALL[m.group(1)]]))
        m = re.match(r"/library/metadata/(\d+)/extras$", p)
        if m:                                     # one trailer on movie 101
            return self.send_json({"MediaContainer": {"Metadata": EXTRAS.get(m.group(1), [])}})
        m = re.match(r"/(?:library|hubs)/metadata/(\d+)/related$", p)
        if m:                                     # 112 has no related hubs
            if m.group(1) == "112":
                return self.send_json({}, 404)
            return self.send_json({"MediaContainer": {"Hub": [
                {"hubIdentifier": "movie.related.director", "title": "More Directed by Test",
                 "Metadata": [live(MOVIES[9])]},
                {"hubIdentifier": "movie.similar", "title": "More Like This",
                 "Metadata": [live(x) for x in MOVIES[1:4]]}]}})
        if p == "/photo/:/transcode":
            data = photo(q.get("url", ""), int(q["width"]), int(q["height"]))
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if p == "/library/streams/900":
            data = open(os.path.join(MEDIA, "ext.srt"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if p == "/library/metadata/2000/theme/1":
            log("theme", token=bool(self.headers.get("X-Plex-Token")))
            return self.serve_file(os.path.join(MEDIA, "4x3.mkv"))
        if p.startswith("/video/:/transcode/universal/"):
            return self.transcode(p, q)
        m = re.match(r"/library/parts/(\d+)/1/file.flac", p)
        if m:
            name = "tone1.flac" if int(m.group(1)) % 2 == 0 else "tone2.flac"
            log("play", part=m.group(1), file=name, token=bool(self.headers.get("X-Plex-Token")))
            return self.serve_file(os.path.join(MEDIA, name), "audio/flac")
        m = re.match(r"/library/parts/(\d+)/1/file.mkv", p)
        if m:
            name = "16x9.mkv" if m.group(1).startswith("1") else "4x3.mkv"
            log("play", part=m.group(1), file=name)
            return self.serve_file(os.path.join(MEDIA, name))
        if p == "/:/timeline":
            log("timeline", ratingKey=q.get("ratingKey"), state=q.get("state"), user=self.token_user(), time=int(q.get("time", 0)),
                session=bool(self.headers.get("X-Plex-Session-Identifier")))
            rk = q.get("ratingKey")
            if rk and int(q.get("time", 0)) > 0:
                STATE["viewOffset"][rk] = int(q["time"])
            return self.send_json({})
        if p == "/:/scrobble":
            log("scrobble", key=q.get("key"))
            STATE["viewCount"][q.get("key")] = 1
            STATE["viewOffset"].pop(q.get("key"), None)
            return self.send_json({})
        if p == "/:/unscrobble":
            STATE["viewCount"].pop(q.get("key"), None)
            return self.send_json({})
        self.send_json({}, 404)

    # ---------------------------------------------------------------- playlists
    def playlists_get(self, p, q, start, size):
        user = self.token_user()
        pls = user_playlists(user)
        if p == "/playlists":
            sec = q.get("sectionID")
            log("playlist_list", type=q.get("playlistType"), section=sec, user=user)
            rows = [pl_meta(rk, pl) for rk, pl in sorted(pls.items())
                    if pl["type"] == q.get("playlistType")
                    and (sec is None or str(pl["section"]) == sec)]
            return self.send_json({"MediaContainer": {"size": len(rows), "Metadata": rows}})
        m = re.match(r"/playlists/(\d+)(/items)?$", p)
        if not m or m.group(1) not in pls:
            return self.send_json({}, 404)
        pl = pls[m.group(1)]
        if not m.group(2):
            return self.send_json({"MediaContainer": {"size": 1, "Metadata": [pl_meta(m.group(1), pl)]}})
        log("playlist_items", id=m.group(1), start=start, size=size, user=user)
        items = pl_entries(pl)
        sl = items[start:start + size] if size else items[start:]
        return self.send_json({"MediaContainer": {"size": len(sl), "totalSize": len(items),
                                                  "Metadata": sl}})

    def playlist_write(self, method, p, q):
        """POST, PUT and DELETE on /playlists. One log line per call; the title is user text
        and is not logged."""
        if TOKEN in self.path:
            log("TOKEN_IN_URL", path=p)
        if not server_token_ok(self.headers.get("X-Plex-Token")):
            return self.send_json({}, 401)
        user = self.token_user()
        pls = user_playlists(user)
        if not PL_WRITES:
            log("playlist_refused", method=method, path=p, user=user)
            return self.send_json({}, 403)
        if method == "POST" and p == "/playlists":
            ids = uri_ids(q.get("uri"))
            if ids is None or q.get("type") not in ("audio", "video") or not q.get("title"):
                return self.send_json({}, 400)
            with LOCK:
                PL_NEXT[0] += 1
                rk = str(PL_NEXT[0])
                pls[rk] = {"title": q["title"], "type": q["type"], "smart": 0, "v": 1,
                           "section": 9 if q["type"] == "audio" else None,
                           "entries": [_pl_entry(i) for i in ids]}
            log("playlist_create", id=rk, type=q["type"], n=len(ids), user=user)
            return self.send_json({"MediaContainer": {"size": 1, "Metadata": [pl_meta(rk, pls[rk])]}})
        m = re.match(r"/playlists/(\d+)(?:/items(?:/(\d+)(/move)?)?)?$", p)
        if not m or m.group(1) not in pls:
            log("playlist_missing", method=method, path=p, user=user)
            return self.send_json({}, 404)
        rk, pid, move = m.group(1), m.group(2), m.group(3)
        pl = pls[rk]
        if pl["smart"]:
            return self.send_json({}, 400)
        items_path = p.endswith("/items")
        with LOCK:
            if method == "PUT" and items_path:
                ids = uri_ids(q.get("uri"))
                if ids is None:
                    return self.send_json({}, 400)
                pl["entries"] += [_pl_entry(i) for i in ids]
                kind = "playlist_add"
            elif method == "PUT" and move:
                ent = next((e for e in pl["entries"] if str(e["pid"]) == pid), None)
                if ent is None:
                    return self.send_json({}, 404)
                pl["entries"].remove(ent)
                after = q.get("after")
                at = 0 if after is None else next(
                    (i + 1 for i, e in enumerate(pl["entries"]) if str(e["pid"]) == after), None)
                if at is None:
                    pl["entries"].append(ent)
                    return self.send_json({}, 400)
                pl["entries"].insert(at, ent)
                kind = "playlist_move"
            elif method == "PUT" and not pid and not items_path:
                if not q.get("title"):
                    return self.send_json({}, 400)
                pl["title"] = q["title"]
                kind = "playlist_rename"
            elif method == "DELETE" and pid:
                ent = next((e for e in pl["entries"] if str(e["pid"]) == pid), None)
                if ent is None:
                    return self.send_json({}, 404)
                pl["entries"].remove(ent)
                kind = "playlist_remove"
            elif method == "DELETE" and not pid and not items_path:
                del pls[rk]
                kind = "playlist_delete"
            else:
                return self.send_json({}, 400)
            pl["v"] += 1
        log(kind, id=rk, item=pid, after=q.get("after"), user=user,
            order=[e["pid"] for e in pl["entries"]] if kind == "playlist_move" else None)
        return self.send_json({"MediaContainer": {"size": 1, "Metadata": [pl_meta(rk, pl)]}}
                              if kind != "playlist_delete" else {})

    def token_user(self):
        tok = self.headers.get("X-Plex-Token") or ""
        return tok[len(TOKEN) + 1:] if tok.startswith(TOKEN + "-") else ("owner" if tok == TOKEN else "")

    # ---------------------------------------------------------------- Discover (Watchlist)
    def discover(self, method, p, q):
        """The three Discover calls. A server token is refused; paging is by
        headers only. Titles are not logged."""
        tok = self.headers.get("X-Plex-Token") or ""
        if ACCOUNT_TOKEN in self.path or TOKEN in self.path:
            log("TOKEN_IN_URL", path=p)
        user = "u-owner" if tok == ACCOUNT_TOKEN else (tok[5:] if tok.startswith("user-") else None)
        if user is None:
            log("discover_refused", path=p, server_token=bool(tok))
            return self.send_json({}, 401)
        lst = user_watchlist(user)
        if method == "GET":
            if "X-Plex-Container-Start" in q or "X-Plex-Container-Size" in q:
                log("discover_query_paging", path=p)
            start = int(self.headers.get("X-Plex-Container-Start") or 0)
            size = int(self.headers.get("X-Plex-Container-Size") or 0) or None
            log("discover_list", user=user, start=start, size=size, sort=q.get("sort"))
            with LOCK:
                items = sorted(lst, key=lambda e: -e["watchlistedAt"])
            sl = items[start:start + size] if size else items[start:]
            return self.send_json({"MediaContainer": {
                "librarySectionID": "watchlist", "identifier": "tv.plex.provider.discover",
                "offset": start, "totalSize": len(items), "size": len(sl), "Metadata": sl}})
        key = q.get("ratingKey") or ""
        add = p.endswith("addToWatchlist")
        log("watchlist_add" if add else "watchlist_remove", user=user, key=key)
        if key not in WATCH_KNOWN:
            return self.send_json({}, 404)
        with LOCK:
            lst[:] = [e for e in lst if e["ratingKey"] != key]
            if add:
                lst.append(watch_entry(key, int(time.time())))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------------------------------------------------------- plex.tv (account)
    def plex_tv(self, method, p, q):
        """plex.tv paths, served by the first mock (BATOPLEX_PLEX_TV points here)."""
        tok = self.headers.get("X-Plex-Token") or ""
        if ACCOUNT_TOKEN in self.path or TOKEN in self.path:
            log("TOKEN_IN_URL", path=p)
        users = {u["uuid"]: u for u in HOME_USERS}
        user = "u-owner" if tok == ACCOUNT_TOKEN else (tok[5:] if tok.startswith("user-") else None)
        if user is None or user not in users:
            log("plextv_unauthorized", path=p)
            return self.send_json({}, 401)
        log("plextv", method=method, path=p, user=user)
        if method == "GET" and p == "/api/v2/user":
            u = users[user]
            return self.send_json({"uuid": u["uuid"], "title": u["title"], "username": u["title"]})
        if method == "GET" and p == "/api/v2/home/users":
            port = self.server.server_port               # Kid has no avatar: the initial shows
            return self.send_json({"users": [
                dict(u, thumb="http://127.0.0.1:%d/users/%s/avatar" % (port, u["uuid"]))
                if u["uuid"] != "u-kid" else u for u in HOME_USERS]})
        if method == "GET" and p == "/api/v2/resources":
            servers = [(MOCK_ID, MOCK_NAME, self.server.server_port, TOKEN)]
            for peer in PEERS:
                sid, name, port, ptok = peer.split(":")
                servers.append((sid, name, int(port), ptok))
            suffix = "" if user == "u-owner" else "-" + user
            return self.send_json([{
                "name": name, "clientIdentifier": sid, "provides": "server", "owned": i == 0,
                "accessToken": ptok + suffix, "publicAddressMatches": True,
                "connections": [{"protocol": "http", "address": "127.0.0.1", "port": port,
                                 "local": True, "uri": "http://127.0.0.1:%d" % port}]}
                for i, (sid, name, port, ptok) in enumerate(servers)])
        m = re.match(r"/api/v2/home/users/([\w-]+)/switch$", p)
        if method == "POST" and m and m.group(1) in users:
            u = users[m.group(1)]
            if u["protected"] and q.get("pin") != PIN:
                log("switch_denied", user=u["uuid"])
                return self.send_json({"errors": [{"code": 1041, "message": "Invalid PIN"}]}, 403)
            log("switch", user=u["uuid"])
            return self.send_json({"authToken": "user-" + u["uuid"]}, 201)
        return self.send_json({}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.startswith("/api/"):
            return self.plex_tv("POST", u.path, q)
        if u.path == "/playlists":
            return self.playlist_write("POST", u.path, q)
        if u.path == "/playQueues":               # a library station
            if TOKEN in self.path:
                log("TOKEN_IN_URL", path=u.path)
            if not server_token_ok(self.headers.get("X-Plex-Token")):
                return self.send_json({}, 401)
            log("playqueue", type=q.get("type"), uri=q.get("uri"))
            return self.send_json(container(TRACKS[:6]))
        self.send_json({}, 404)

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.startswith("/playlists/"):
            return self.playlist_write("DELETE", u.path, q)
        self.send_json({}, 404)

    def do_PUT(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.startswith("/playlists/"):
            return self.playlist_write("PUT", u.path, q)
        if u.path in ("/actions/addToWatchlist", "/actions/removeFromWatchlist"):
            return self.discover("PUT", u.path, q)
        if not server_token_ok(self.headers.get("X-Plex-Token")):
            return self.send_json({}, 401)
        m = re.match(r"/library/parts/(\d+)$", u.path)
        if not m or int(m.group(1)) not in PARTS:
            return self.send_json({}, 404)
        part = PARTS[int(m.group(1))]
        log("streams", part=int(m.group(1)), audio=q.get("audioStreamID"),
            sub=q.get("subtitleStreamID"), allParts=q.get("allParts"))
        for key, st in (("audioStreamID", 2), ("subtitleStreamID", 3)):
            if key in q:
                want = int(q[key])
                for s in part["Stream"]:
                    if s.get("streamType") == st:
                        s["selected"] = s.get("id") == want
                        if not s["selected"]:
                            s.pop("selected")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def transcode(self, p, q):
        """Plex's universal transcoder, played from pre-made HLS (test/media/hls): the decision,
        a playlist without the segments before the offset, segments, stop and ping."""
        sess = q.get("session") or ""
        name = p.rsplit("/", 1)[-1]
        if name == "decision":
            log("tc_decision", offset=int(q.get("offset", 0)), session=bool(sess),
                target=bool(self.headers.get("X-Plex-Client-Profile-Extra")),
                quality=q.get("maxVideoBitrate"))
            if os.environ.get("MOCK_NO_TRANSCODE"):
                return self.send_json({"MediaContainer": {"generalDecisionCode": 1001,
                                                          "transcodeDecisionCode": 2000,
                                                          "transcodeDecisionText": "refused"}})
            return self.send_json({"MediaContainer": {"generalDecisionCode": 1001,
                                                      "transcodeDecisionCode": 1001}})
        if name == "start.m3u8":
            off = int(q.get("offset", 0))
            log("tc_start", offset=off, session=bool(sess),
                same=sess == self.headers.get("X-Plex-Session-Identifier"))
            out, t, dur = [], 0.0, None
            for line in open(os.path.join(MEDIA, "hls", "index.m3u8")).read().splitlines():
                if line.startswith("#EXTINF:"):
                    dur = float(line[8:].split(",")[0])
                    continue
                if line and not line.startswith("#"):
                    if t + dur > off:
                        out += ["#EXTINF:%.3f," % dur, "session/%s/%s" % (sess, line)]
                    t += dur
                    continue
                if not line.startswith("#EXT-X-ENDLIST"):
                    out.append(line)
            out.append("#EXT-X-ENDLIST")
            b = ("\n".join(out) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if "/session/" in p:
            log("tc_segment", name=name, token=bool(self.headers.get("X-Plex-Token")))
            return self.serve_file(os.path.join(MEDIA, "hls", os.path.basename(name)), "video/mp2t")
        if name in ("stop", "ping"):
            log("tc_" + name, session=bool(sess))
            return self.send_json({})
        return self.send_json({}, 404)

    def serve_loop(self):
        """A 440 Hz WAV with no end and no Content-Length (HTTP/1.0: the close ends it)."""
        import math
        import struct
        rate = 22050
        sec = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
                       for i in range(rate))
        hdr = (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt " +
               struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) +
               b"data" + struct.pack("<I", 0xFFFFFFFF))
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        try:
            self.wfile.write(hdr)
            while True:
                self.wfile.write(sec)
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_file(self, path, ctype="video/x-matroska"):
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = int(m.group(2))
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 32400
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
