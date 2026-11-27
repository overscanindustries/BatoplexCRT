"""Plex provider: PIN sign-in, server discovery, home rows, metadata, playback, reporting.

Standard library only. The token goes in the X-Plex-Token header, never in a URL that
is logged or passed on a command line.
"""
import collections
import http.client
import ipaddress
import json
import os
import platform
import socket
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

from . import log, loudness, tracks
from .config import VERSION
from .model import Item, PlayInfo, Row

L = log.get("plex")
PLEX_TV = "https://plex.tv"


def _plex_tv():
    """plex.tv base. Tests may point it at a local mock, and only at 127.0.0.1."""
    v = os.environ.get("BATOPLEX_PLEX_TV", "")
    if not v:
        return PLEX_TV
    u = urllib.parse.urlsplit(v)
    try:
        port = u.port
    except ValueError:
        return PLEX_TV
    if u.scheme == "http" and u.hostname == "127.0.0.1" and port and not u.username \
            and not u.password and u.netloc == "127.0.0.1:%d" % port:
        return "http://127.0.0.1:%d" % port
    return PLEX_TV
IDENT = "com.plexapp.plugins.library"
VIDEO_SECTION_TYPES = ("movie", "show")
MUSIC_SECTION_TYPES = ("artist",)
LIB_TYPE = {"movie": 1, "show": 2, "artist": 8}
KIND_TYPE = {"artist": 8, "album": 9, "track": 10}     # music grid kinds
DEFAULT_SORTS = ({"key": "titleSort", "title": "Title", "dir": "asc"},
                 {"key": "addedAt", "title": "Date Added", "dir": "desc"},
                 {"key": "originallyAvailableAt", "title": "Release Date", "dir": "desc"},
                 {"key": "rating", "title": "Critic Rating", "dir": "desc"})


def letter_offset(chars, letter, desc=False):
    """Grid index of the first title that starts with letter. chars: [(letter, count)] in
    ascending title order (firstCharacter). With a descending sort the letters run backward."""
    seq = list(reversed(chars)) if desc else list(chars)
    n = 0
    for c, size in seq:
        if c == letter:
            return n
        n += size
    return None


class PlexError(Exception):
    """code: the HTTP status when the server answered with an error, else None."""

    def __init__(self, msg="", code=None):
        super().__init__(msg)
        self.code = code


class AuthError(PlexError):
    pass


class TranscodeRefused(PlexError):
    """The server will not convert this video (decision code 2xxx or 3xxx)."""

    def __init__(self, msg, code=None, text=""):
        super().__init__(msg)
        self.code, self.text = code, text


def _headers(client_id, token=None):
    h = {
        "Accept": "application/json",
        "X-Plex-Product": "Batoplex",
        "X-Plex-Version": VERSION,
        "X-Plex-Client-Identifier": client_id,
        "X-Plex-Platform": "Linux",
        "X-Plex-Platform-Version": platform.release(),
        "X-Plex-Device": "Batocera",
        "X-Plex-Device-Name": socket.gethostname() or "Batoplex",
        "X-Plex-Provides": "player",
    }
    if token:
        h["X-Plex-Token"] = token
    return h


class _SameOrigin(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the same scheme, host and port: the request carries the
    X-Plex-Token header, and urllib would send it to any host a server redirects to."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a = urllib.parse.urlsplit(req.full_url)
        b = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        try:
            same = (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
        except ValueError:                       # a bad port in the Location
            same = False
        if not same:
            raise urllib.error.HTTPError(req.full_url, code, "redirect to another host refused",
                                         headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SameOrigin)


def _request(url, headers, method="GET", timeout=8, raw=False):
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 498):
            raise AuthError("HTTP %d" % e.code, code=e.code)
        raise PlexError("HTTP %d for %s" % (e.code, url.split("?")[0]), code=e.code)
    except (urllib.error.URLError, OSError) as e:
        raise PlexError("network: %s" % e)
    except http.client.HTTPException as e:       # a dropped body (IncompleteRead), a bad line
        raise PlexError("network: %s" % e.__class__.__name__)
    except ValueError as e:                      # a malformed URL or header
        raise PlexError("bad request: %s" % e)
    if raw:
        return data
    if not data:
        return {}
    try:
        return json.loads(data)
    except ValueError:
        return {}


# ---------------------------------------------------------------- sign-in

class PinLogin:
    """plex.tv/link flow. start() -> code; poll() -> token or None."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.pin_id = None
        self.code = None

    def start(self):
        d = _request(_plex_tv() + "/api/v2/pins?strong=false", _headers(self.client_id), "POST")
        self.pin_id, self.code = d["id"], d["code"]
        L.info("PIN requested")
        return self.code

    def poll(self):
        d = _request("%s/api/v2/pins/%s" % (_plex_tv(), self.pin_id), _headers(self.client_id))
        tok = d.get("authToken")
        if tok:
            log.add_secret(tok)
        return tok


def _candidates(r):
    """Connection candidates of one resource as [(rank, uri)]:
    1 local http, 2 local https (both only when publicAddressMatches: a shared server lists
    its private addresses, and on this LAN they can be another device), 3 remote https,
    4 relay. Remote plain http is skipped (the token would cross the internet in clear)."""
    # The owner's own server keeps its LAN addresses even when the public addresses differ
    # (VPN, CGNAT, dual WAN): the probe sends no token and checks the server id.
    same_lan = bool(r.get("publicAddressMatches")) or bool(r.get("owned"))
    out = []
    for c in r.get("connections") or []:
        uri = (c.get("uri") or "").rstrip("/")
        if c.get("relay"):
            if uri.startswith("https://"):
                out.append((4, uri))
        elif c.get("local"):
            if not same_lan:
                continue
            if c.get("address") and c.get("port"):
                out.append((1, "http://%s:%s" % (c["address"], c["port"])))
            if uri.startswith("https://"):
                out.append((2, uri))
        elif uri.startswith("https://"):
            out.append((3, uri))
    seen, uniq = set(), []
    for rank, uri in sorted(out):
        if uri not in seen:
            seen.add(uri)
            uniq.append((rank, uri))
    return uniq


def discover_servers(client_id, user_token):
    url = _plex_tv() + "/api/v2/resources?includeHttps=1&includeRelay=1"
    res = _request(url, _headers(client_id, user_token))
    out = []
    for r in res if isinstance(res, list) else []:
        if "server" not in (r.get("provides") or ""):
            continue
        tok = r.get("accessToken") or user_token
        log.add_secret(tok)
        cands = _candidates(r)
        out.append({"name": r.get("name"), "id": r.get("clientIdentifier"),
                    "token": tok, "uris": [u for _r, u in cands], "cands": cands,
                    "owned": r.get("owned")})
    return out


# ---------------------------------------------------------------- Watchlist

DISCOVER = "https://discover.provider.plex.tv"
WATCH_PAGE = 100
WATCH_MAX_PAGES = 100
WATCH_TYPES = ("movie", "show")


def _discover():
    """The Discover host. It gets three calls only: the list, add and remove. A test points
    it at the local mock with BATOPLEX_PLEX_TV, under the same 127.0.0.1 rule."""
    base = _plex_tv()
    return DISCOVER if base == PLEX_TV else base


def watch_key(guid):
    """The Discover key of a library item: the last part of plex://movie/{key} or
    plex://show/{key}. Other guids (local://, com.plexapp.agents.*) have none: None."""
    g = guid or ""
    for pre in ("plex://movie/", "plex://show/"):
        if g.startswith(pre):
            k = g[len(pre):]
            return k if k and "/" not in k and "?" not in k else None
    return None


def _count(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def watchlist(client_id, token, size=WATCH_PAGE, timeout=10):
    """The whole Watchlist, newest first: [{key, guid, type, added_at}]. Pages go by the
    X-Plex-Container-Start and -Size headers until the count reaches the total. Raises
    PlexError for a bad reply (not a MediaContainer, a count with no Metadata, neither a count
    nor Metadata), a page that
    repeats its first key, an empty page before the total, or more than 100 pages: the
    caller keeps its last complete list. Titles are not returned (they are never logged)."""
    url = _discover() + "/library/sections/watchlist/all?" + urllib.parse.urlencode(
        {"includeCollections": 1, "includeExternalMedia": 1, "sort": "watchlistedAt:desc"})
    out, keys, firsts, got = [], set(), set(), 0
    for _n in range(WATCH_MAX_PAGES):
        h = _headers(client_id, token)
        h["X-Plex-Container-Start"], h["X-Plex-Container-Size"] = str(got), str(size)
        d = _request(url, h, timeout=timeout)
        mc = d.get("MediaContainer") if isinstance(d, dict) else None
        if not isinstance(mc, dict):
            raise PlexError("watchlist: not a MediaContainer")
        md, n = mc.get("Metadata"), _count(mc.get("size"))
        total = _count(mc.get("totalSize"))
        if md is None and n is None and total is None:
            raise PlexError("watchlist: no count and no Metadata")   # an empty list has size 0
        if md is None:
            if n:
                raise PlexError("watchlist: a count with no Metadata")
            md = []
        if not isinstance(md, list):
            raise PlexError("watchlist: Metadata is not a list")
        if md:
            first = str(md[0].get("ratingKey") or "")
            if first in firsts or first in keys:
                raise PlexError("watchlist: a page repeats its first key")
            firsts.add(first)
        elif total is not None and got < total:
            raise PlexError("watchlist: an empty page before the total")
        for m in md:
            key, guid = str(m.get("ratingKey") or ""), m.get("guid") or ""
            got += 1
            if not key or key in keys:
                continue
            keys.add(key)
            out.append({"key": key, "guid": guid, "type": m.get("type") or "",
                        "added_at": _count(m.get("watchlistedAt")) or 0})
        if total is not None and got >= total:
            return out
        if total is None and len(md) < size:
            return out                   # no totalSize: a short page is the last one
    raise PlexError("watchlist: more than %d pages" % WATCH_MAX_PAGES)


def _watch_write(client_id, token, key, action, timeout=10):
    _request(_discover() + "/actions/%s?%s" % (action, urllib.parse.urlencode({"ratingKey": key})),
             _headers(client_id, token), method="PUT", timeout=timeout)


def watchlist_add(client_id, token, key):
    _watch_write(client_id, token, key, "addToWatchlist")


def watchlist_remove(client_id, token, key):
    _watch_write(client_id, token, key, "removeFromWatchlist")


def _is_loopback(uri):
    return urllib.parse.urlsplit(uri).hostname in ("127.0.0.1", "localhost", "::1")


# ---------------------------------------------------------------- transcode

CONN_RANK = {}             # uri -> candidate rank of the connection picked for it
_MEASURED = {}             # server base -> (kbps or None, monotonic time) (rule 4)
MEASURE_TTL = 600          # a good result is kept 10 min
MEASURE_FAIL_TTL = 60      # a failed probe is kept 1 min: no wait again on each start
PROBE_BYTES = 4194304
PROBE_S = 3.0
QRELAY = {"bitrate": 720, "res": "576x320", "label": "720 kbps", "id": "720k"}
# Video Quality targets (Plex names), best first: stable setting id -> kbps. "480p" is the id
# of 1.5 Mbps since version 1 (saved settings keep it). On a PAL screen (a 576-line desktop)
# the 480p targets convert to 720x576 and their labels say 576p.
QUALITY_KBPS = {"8000": 8000, "4000": 4000, "2000": 2000, "480p": 1500, "720k": 720}
PAL = False                # the app sets it when the display opens
AUTO_SHARE = 0.7           # a stream fits when it needs at most 0.7 x the measured throughput


def quality(qid):
    """The transcode target of a setting id: {"bitrate", "res", "label", "id"}."""
    if qid == "720k":
        return dict(QRELAY)
    kbps, lines = QUALITY_KBPS[qid], 576 if PAL else 480
    return {"bitrate": kbps, "res": "720x%d" % lines, "id": qid,
            "label": "%g Mbps %dp" % (kbps / 1000.0, lines)}


def quality_of(label):
    """The target whose label this is (a transcode that restarts keeps its target); None."""
    return next((quality(k) for k in QUALITY_KBPS if quality(k)["label"] == label), None)


def quality_choices():
    """The Settings values of Home Streaming and Remote Streaming: (id, label)."""
    return ([("auto", "Automatic"), ("original", "Original")]
            + [(k, quality(k)["label"]) for k in QUALITY_KBPS])


def auto_quality(kbps):
    """Automatic: the highest target that fits under 0.7 x the measured throughput; the
    lowest when none fits."""
    fits = [k for k, v in QUALITY_KBPS.items() if v <= AUTO_SHARE * kbps]
    return quality(fits[0] if fits else "720k")
# The target the server converts to (always sent). No comma: mpv's
# http-header-fields splits on commas.
PROFILE_EXTRA = ("add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
                 "&container=mpegts&videoCodec=h264&audioCodec=aac)")


def source_aspect(media):
    """Display aspect of the first video stream: width x pixel aspect / height, else the
    media's rounded aspectRatio, else 0. A transcode keeps the source's pixel aspect on a
    picture it already scaled to square pixels (720x480 16:9 comes back 718x404 at
    853:720), so the player sets this shape itself."""
    media = media or {}
    part = (media.get("Part") or [{}])[0]
    v = next((s for s in part.get("Stream") or [] if s.get("streamType") == 1), None) or {}
    try:
        w, h = int(v.get("width") or 0), int(v.get("height") or 0)
        num, _, den = str(v.get("pixelAspectRatio") or "1:1").partition(":")
        par = float(num) / float(den or 1)
        if w > 0 and h > 0 and par > 0:
            return w * par / h
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        return max(0.0, float(media.get("aspectRatio") or 0))
    except (TypeError, ValueError):
        return 0.0


def _host_kind(uri):
    """local or remote from the host alone: a private address, or a plex.direct name that
    spells one (192-168-1-5.<hash>.plex.direct)."""
    host = urllib.parse.urlsplit(uri).hostname or ""
    if host.endswith(".plex.direct"):
        host = host.split(".")[0].replace("-", ".")
    if host == "localhost":
        return "local"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "remote"
    return "local" if (ip.is_private or ip.is_loopback or ip.is_link_local) else "remote"


def conn_kind(uri):
    """"local", "remote" or "relay" for a server base URL."""
    rank = CONN_RANK.get((uri or "").rstrip("/"))
    if rank in (1, 2):
        return "local"
    if rank == 3:
        return "remote"
    if rank == 4:
        return "relay"
    return _host_kind(uri or "")


def connect_server(client_id, server, override="", probe_timeout=4.0, settle=1.5, deadline=5.0,
                   request=None):
    """Probe every candidate at once, without the token, and keep one whose /identity names
    this server. A rank 0-1 (override, local http) answer wins at once; else the best answer
    1.5 s after the first one; nothing within 5 s: the server is down (PlexError)."""
    import threading
    import time as _time
    request = request or _request
    cands = list(server.get("cands") or [(3, u) for u in server.get("uris") or []])
    if override:
        cands.insert(0, (0, override.rstrip("/")))
    want = server.get("id")
    lock, hits, done = threading.Lock(), [], threading.Event()
    pending_local = [sum(1 for r, _u in cands if r <= 1)]

    def probe(rank, uri):
        try:
            d = request(uri + "/identity", _headers(client_id), timeout=probe_timeout)
        except Exception as e:
            L.info("probe failed %s: %s", uri, e)
            if rank <= 1:
                with lock:
                    pending_local[0] -= 1
            return
        mid = (d.get("MediaContainer") or {}).get("machineIdentifier") if isinstance(d, dict) else None
        if want and mid != want and not (rank == 0 and _is_loopback(uri)):
            L.info("probe %s answered as another server; skipped", uri)
            if rank <= 1:
                with lock:
                    pending_local[0] -= 1
            return
        with lock:
            hits.append((rank, uri, _time.monotonic()))
        if rank <= 1:
            done.set()

    for rank, uri in cands:
        threading.Thread(target=probe, args=(rank, uri), daemon=True).start()
    t0 = _time.monotonic()
    while _time.monotonic() - t0 < deadline:
        if done.wait(0.05):
            break
        with lock:
            # A LAN probe still out can beat a remote answer: wait for it (to the deadline).
            if hits and pending_local[0] <= 0 and _time.monotonic() - min(h[2] for h in hits) >= settle:
                break
    with lock:
        best = sorted(hits)[:1]
    if not best:
        raise PlexError("no reachable connection for %s" % server.get("name"))
    CONN_RANK[best[0][1]] = best[0][0]
    L.info("server %s reachable (rank %d)", server.get("name"), best[0][0])
    return best[0][1]


def pick_connection(client_id, server, override=""):
    return connect_server(client_id, server, override)


def _ep_code(it):
    if it.season_index == 0:
        return ("Special %s" % it.index) if it.index is not None else "Special"
    bits = []
    if it.season_index is not None:
        bits.append("S%s" % it.season_index)
    if it.index is not None:
        bits.append("E%s" % it.index)
    return " ".join(bits)


# ---------------------------------------------------------------- detail page
Rating = collections.namedtuple("Rating", "source kind value state")
RATING_ORDER = {"critic": 0, "audience": 1}
MAX_PEOPLE = 20
EXTRA_LABELS = {"trailer": "Trailer", "behindTheScenes": "Behind the Scenes",
                "featurette": "Featurette", "deletedScene": "Deleted Scene",
                "interview": "Interview", "sceneOrSample": "Scene", "short": "Short"}
# Plex extraType numbers, for a reply with no subtype.
EXTRA_TYPES = {1: "trailer", 2: "deletedScene", 3: "interview", 5: "behindTheScenes",
               6: "sceneOrSample", 10: "featurette", 11: "short"}
PERSON_PAGE = 200


def _rating(image, value, kind):
    """One badge from a ratingImage-style URI and a 0-10 value, or None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    scheme, _, rest = (image or "").partition("://")
    if scheme == "rottentomatoes":
        pct = "%d%%" % round(v * 10)
        if kind == "audience":
            return Rating("rt", kind, pct, "down" if "spilled" in rest else "up")
        if "rotten" in rest:
            state = "rotten"
        elif "ripe" in rest or "certified" in rest:
            state = "fresh"
        else:
            state = "fresh" if v >= 6.0 else "rotten"
        return Rating("rt", kind, pct, state)
    if scheme == "imdb":
        return Rating("imdb", kind, "%.1f" % v, None)
    if scheme == "themoviedb":
        return Rating("tmdb", kind, "%d%%" % round(v * 10), None)
    return Rating("plain", kind, "%.1f" % v, None)


def ratings(md):
    """The Rating array of newer servers, else rating/ratingImage and
    audienceRating/audienceRatingImage. Critic first, then audience, then the others."""
    out = []
    arr = md.get("Rating")
    if isinstance(arr, list) and arr:
        for r in arr:
            b = _rating(r.get("image"), r.get("value"), r.get("type") or "")
            if b is not None:
                out.append(b)
    else:
        for val, img, kind in (("rating", "ratingImage", "critic"),
                               ("audienceRating", "audienceRatingImage", "audience")):
            if md.get(val) is not None:
                b = _rating(md.get(img), md.get(val), kind)
                if b is not None:
                    out.append(b)
    out.sort(key=lambda r: RATING_ORDER.get(r.kind, 2))      # stable: server order otherwise
    return out


def people(md, server_id=None):
    """Cast & Crew: Role in server order, then Director, then Writer, at most
    20. Items of type person: id = the tag id, poster = the thumb (an absolute URL; it goes
    through the server's photo transcoder as all art does), extra kind and role."""
    out = []
    for key, kind in (("Role", "actor"), ("Director", "director"), ("Writer", "writer")):
        for r in md.get(key) or []:
            name = r.get("tag") or ""
            if not name:
                continue
            role = (r.get("role") or "") if kind == "actor" else kind.title()
            it = Item(id=str(r.get("id") or ""), type="person", title=name,
                      poster=r.get("thumb") or None, server_id=server_id)
            it.extra.update(kind=kind, role=role)
            out.append(it)
    return out[:MAX_PEOPLE]


def similar_hub(mc):
    """The first related hub whose hubIdentifier names "similar" (More Like This), or None."""
    for h in mc.get("Hub") or []:
        if "similar" in str(h.get("hubIdentifier") or "").lower():
            return h
    return None


def extra_label(subtype):
    return EXTRA_LABELS.get(subtype or "", "Extra")


# ---------------------------------------------------------------- provider

class PlexProvider:
    name = "plex"

    def __init__(self, base, token, client_id, settings, server_id=None):
        self.base = base.rstrip("/")
        self.server_id = server_id      # machineIdentifier; read from /identity if not known
        self.token = token
        import hashlib
        self.name = "plex-" + hashlib.sha1(self.base.encode()).hexdigest()[:10]  # cache per server
        self.client_id = client_id
        self.settings = settings
        log.add_secret(token)
        self.sections = []
        self._tmeta = {}                # (server, ratingKey) -> (part key, loud, duration_ms)

    # -- http
    def h(self):
        return _headers(self.client_id, self.token)

    def get(self, path, params=None, start=None, size=None, timeout=8):
        q = dict(params or {})
        if start is not None:
            q["X-Plex-Container-Start"] = start
            q["X-Plex-Container-Size"] = size
        url = self.base + path
        if q:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(q)
        d = _request(url, self.h(), timeout=timeout)
        return d.get("MediaContainer", {})

    def theme_url(self, ref):
        """URL and headers for a show theme (Item.theme)."""
        return self.base + ref, {"X-Plex-Token": self.token}

    def image(self, ref, w, h):
        if not ref:
            return None
        q = urllib.parse.urlencode({"width": w, "height": h, "minSize": 1, "upscale": 1,
                                    "format": "jpg", "url": ref})
        return _request(self.base + "/photo/:/transcode?" + q, self.h(), timeout=10, raw=True)

    # -- parsing
    def _item(self, m, sid=None):
        """sid: the server the item lives on when it is not this one (a playlist entry with a
        sourceURI)."""
        t = m.get("type", "")
        it = Item(
            id=str(m.get("ratingKey", "")), type=t, title=m.get("title", ""),
            year=m.get("year") or m.get("parentYear"),
            duration_ms=int(m.get("duration") or 0),
            view_offset_ms=int(m.get("viewOffset") or 0),
            content_rating=m.get("contentRating", ""),
            summary=m.get("summary", ""),
            poster=m.get("thumb"), art=m.get("art"),
            section_id=m.get("librarySectionID"), server_id=sid or self.server_id,
            theme=m.get("theme") or m.get("grandparentTheme") or m.get("parentTheme"),
        )
        if t == "episode":
            it.show_title = m.get("grandparentTitle", "")
            it.show_id = str(m.get("grandparentRatingKey", "")) or None
            it.season_id = str(m.get("parentRatingKey", "")) or None
            it.season_index = m.get("parentIndex")
            it.index = m.get("index")
            it.still = m.get("thumb")
            it.poster = m.get("grandparentThumb") or m.get("parentThumb") or m.get("thumb")
            it.art = m.get("grandparentArt") or m.get("art")
            it.watched = bool(m.get("viewCount"))
        elif t == "season":
            it.show_title = m.get("parentTitle", "")
            it.show_id = str(m.get("parentRatingKey", "")) or None
            it.index = m.get("index")
            it.poster = m.get("thumb") or m.get("parentThumb")
            it.art = m.get("art") or m.get("parentArt")
            it.leaf_count = int(m.get("leafCount") or 0)
            it.viewed_leaf_count = int(m.get("viewedLeafCount") or 0)
            it.watched = bool(it.leaf_count) and it.viewed_leaf_count >= it.leaf_count
        elif t == "artist":
            it.poster = m.get("thumb")
            it.child_count = int(m.get("childCount") or 0)
        elif t == "album":
            it.artist = m.get("parentTitle", "")
            it.artist_id = str(m.get("parentRatingKey", "")) or None
            it.leaf_count = int(m.get("leafCount") or 0)
            it.poster = m.get("thumb") or m.get("parentThumb")
        elif t == "track":
            it.artist = m.get("originalTitle") or m.get("grandparentTitle", "")
            it.album = m.get("parentTitle", "")
            it.album_id = str(m.get("parentRatingKey", "")) or None
            it.artist_id = str(m.get("grandparentRatingKey", "")) or None
            it.index = m.get("index")
            it.disc = m.get("parentIndex")
            it.poster = m.get("thumb") or m.get("parentThumb") or m.get("grandparentThumb")
            it.year = m.get("parentYear") or m.get("year")
            it.extra["album_artist"] = m.get("grandparentTitle", "")
            part = (((m.get("Media") or [{}])[0].get("Part")) or [{}])[0]
            if part.get("key"):
                it.extra["part_key"] = part["key"]
            loud = self._loud(part)
            if loud is not None:
                it.extra["loud"] = loud
        elif t == "show":
            it.child_count = int(m.get("childCount") or 0)
            it.leaf_count = int(m.get("leafCount") or 0)
            it.viewed_leaf_count = int(m.get("viewedLeafCount") or 0)
            it.watched = bool(it.leaf_count) and it.viewed_leaf_count >= it.leaf_count
        else:
            it.watched = bool(m.get("viewCount"))
        self._tag(it, sid)
        if m.get("lastViewedAt"):
            it.extra["last_viewed"] = int(m["lastViewedAt"])
        it.cast = [r.get("tag", "") for r in (m.get("Role") or [])[:3]]
        media = (m.get("Media") or [None])[0]
        if media:
            res = media.get("videoResolution", "")
            res = (res.upper() + "P") if res.isdigit() else res.upper()
            ar = media.get("aspectRatio")
            ar = "4:3" if ar and float(ar) < 1.5 else ("16:9" if ar and float(ar) < 1.9 else
                                                      ("%.2f:1" % float(ar) if ar else ""))
            ch = media.get("audioChannels")
            chs = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(ch, "%s ch" % ch if ch else "")
            it.media_note = " • ".join(x for x in (res, ar, chs) if x)
        it.extra["raw_key"] = m.get("key")
        if t in WATCH_TYPES and m.get("guid"):
            it.extra["guid"] = m["guid"]          # the Watchlist key
        it.ratings = ratings(m)
        if m.get("originallyAvailableAt"):
            it.extra["date"] = str(m["originallyAvailableAt"])     # the person page sort
        return it

    @staticmethod
    def _loud(part):
        """The loudness fields of the part's first audio stream, as floats or
        None. None when the reply has no audio stream with any of them: a list reply can leave
        Stream out, so the absence alone does not mean "no data"."""
        for st in part.get("Stream") or []:
            if st.get("streamType") == 2:
                loud = {k: loudness._num(st.get(k)) for k in loudness.FIELDS}
                return loud if any(v is not None for v in loud.values()) else None
        return None

    def _tag(self, it, sid=None):
        """Artwork and theme refs name their server, so Sources can route them."""
        sid = sid or self.server_id
        if not sid:
            return it
        for a in ("poster", "art", "still", "theme"):
            v = getattr(it, a, None)
            if v and "|" not in v:
                setattr(it, a, "%s|%s" % (sid, v))
        return it

    def _items(self, mc):
        return [self._item(m) for m in (mc.get("Metadata") or [])
                if m.get("type") in ("movie", "show", "season", "episode", "collection",
                                     "artist", "album", "track")]

    SEARCH_TYPES = ("movie", "show", "episode", "collection", "artist", "album", "track")

    def search(self, query, limit=12):
        """Hub search: one Row per result type (movie, show, episode, collection), in the
        server's order. Items carry extra["guid"] so copies on two servers can merge. The
        query is user text: it goes only in the URL, never in a log line."""
        mc = self.get("/hubs/search", {"query": query, "limit": limit, "includeCollections": 1},
                      timeout=6)
        video = ({int(s["id"]) for s in self.sections
                  if s["type"] in VIDEO_SECTION_TYPES + MUSIC_SECTION_TYPES}
                 if self.sections else None)
        rows = []
        for hub in mc.get("Hub") or []:
            t = hub.get("type")
            if t not in self.SEARCH_TYPES:
                continue
            items = []
            for m in hub.get("Metadata") or []:
                sid = m.get("librarySectionID")
                if m.get("type") != t or (video is not None and sid is not None
                                          and str(sid).isdigit() and int(sid) not in video):
                    continue
                it = self._item(m)
                it.extra["guid"] = m.get("guid") or ""
                items.append(it)
            if items:
                rows.append(Row(t, hub.get("title") or t.title() + "s", items))
        return rows

    def by_guid(self, guids):
        """Watchlist titles on this server: {guid: (key, section, tile)} for
        the movies and shows of all video sections with these guids (20 at most, one call
        with the guids joined by commas). Hidden sections are in: shown() leaves them out, so
        a title shows again at once when its section is no longer hidden. A copy in a visible
        section wins over one in a hidden section. A guid that is not in the answer is absent.
        Raises PlexError when the answer is not a good one (an absent result needs a good,
        complete answer). tile: what a poster tile needs, kept in the watchlist cache."""
        want = set(guids)
        try:
            mcs = [self.get("/library/all", {"guid": ",".join(guids)}, timeout=8)]
        except PlexError as e:
            if e.code not in (400, 404) or not self.sections:
                raise
            # An older server: each video section with the same filter.
            mcs = [self.get("/library/sections/%d/all" % s["id"], {"guid": ",".join(guids)},
                            timeout=8)
                   for s in self.sections if s["type"] in VIDEO_SECTION_TYPES]
        hidden = self.settings.hidden(self.server_id)
        out = {}
        for mc in mcs:
            if not isinstance(mc, dict) or ("size" not in mc and "Metadata" not in mc):
                raise PlexError("guid lookup: not a MediaContainer")
            for m in mc.get("Metadata") or []:
                g, sec = m.get("guid"), _count(m.get("librarySectionID"))
                if g not in want or m.get("type") not in WATCH_TYPES or not m.get("ratingKey") \
                        or (g in out and (sec in hidden or out[g][1] not in hidden)):
                    continue                 # a visible copy wins over a hidden one
                out[g] = (str(m["ratingKey"]), sec, {
                    "type": m.get("type"), "title": m.get("title") or "", "year": m.get("year"),
                    "date": m.get("originallyAvailableAt"), "poster": m.get("thumb"),
                    "art": m.get("art"), "dur": int(m.get("duration") or 0),
                    "offset": int(m.get("viewOffset") or 0), "views": int(m.get("viewCount") or 0),
                    "leaf": int(m.get("leafCount") or 0), "viewed": int(m.get("viewedLeafCount") or 0),
                    "child": int(m.get("childCount") or 0), "rating": m.get("contentRating") or ""})
        return out

    def _visible_sections(self):
        hidden = self.settings.hidden(self.server_id)
        order = self.settings.pins(self.server_id)
        secs = [s for s in self.sections
                if s["type"] in VIDEO_SECTION_TYPES + MUSIC_SECTION_TYPES and s["id"] not in hidden]
        secs.sort(key=lambda s: (order.index(s["id"]) if s["id"] in order else 99, s["title"]))
        return secs

    # -- rows
    def load_sections(self):
        if not self.server_id:
            try:
                self.server_id = self.get("/identity").get("machineIdentifier") or None
            except Exception as e:
                L.info("identity failed: %s", e)
        if self.server_id and getattr(self, "migrate", True):   # version 1 pins: home only
            self.settings.migrate_pins(self.server_id)
        mc = self.get("/library/sections")
        self.sections = [{"id": int(d["key"]), "title": d.get("title", ""), "type": d.get("type"),
                          "art": d.get("art"), "thumb": d.get("thumb") or d.get("composite"),
                          # Live TV: a library that did not change keeps its
                          # pool. Box check: which of the two fields the server sends.
                          "stamp": d.get("contentChangedAt") or d.get("updatedAt")}
                         for d in mc.get("Directory") or []]
        return self.sections

    def continue_watching(self, size=24):
        for path in ("/hubs/continueWatching/items", "/library/onDeck"):
            try:
                return self._items(self.get(path, start=0, size=size))
            except PlexError as e:
                L.info("continue watching via %s failed: %s", path, e)
        return []

    def home_rows(self):
        if not self.sections:
            self.load_sections()
        hidden = self.settings.hidden(self.server_id)
        rows = []
        cw = [i for i in self.continue_watching() if i.section_id not in hidden]
        if cw:
            rows.append(Row("continue", "Continue Watching", cw))
        for s in self._visible_sections():
            if s["type"] in MUSIC_SECTION_TYPES:
                rows += self.music_home_rows(s)
                continue
            if s["type"] not in VIDEO_SECTION_TYPES:
                continue
            sid = s["id"]
            path = "/library/sections/%d/recentlyAdded" % sid
            try:
                mc = self.get(path, start=0, size=24)
            except PlexError:
                continue
            items = self._items(mc)
            if items:
                rows.append(Row("recent-%d" % sid, "Recently Added in %s" % s["title"], items))
        try:
            for hub in self.get("/hubs", {"count": 12}).get("Hub") or []:
                ident = hub.get("hubIdentifier", "")
                if any(k in ident for k in ("continue", "ondeck", "recent", "inprogress")):
                    continue
                items = [i for i in self._items(hub) if i.section_id not in hidden]
                if items and hub.get("type") in ("movie", "show", "episode", "mixed", "collection"):
                    key = hub.get("key")
                    total = int(hub.get("size") or len(items))
                    if hub.get("more") and key:
                        total = max(total, len(items) + 1)
                    rows.append(Row("hub-" + ident, hub.get("title", ""), items, total=total,
                                    loader=self._pager(key) if hub.get("more") and key else None))
        except PlexError as e:
            L.info("hubs failed: %s", e)
        return rows

    def music_home_rows(self, s):
        """"Recently Played in {Library}", then "Recently Added in
        {Library}", from the section hubs. A hub the server does not send has a list
        fallback; a hub that is there but empty means nothing to show. A failed hub call
        (network, a shared user without access) adds no rows for this library. Row keys
        carry the server id: two servers can both have a section 9."""
        sid, lib = s["id"], s["title"]
        try:
            hubs = self.get("/hubs/sections/%d" % sid, {"count": 12}).get("Hub") or []
        except PlexError as e:
            L.info("music hubs of section %d failed: %s", sid, e)
            return []
        found = {}
        for h in hubs:
            ident = str(h.get("hubIdentifier") or "")
            for kind in ("played", "added"):
                if kind not in found and ident.startswith("music.recent.%s" % kind):
                    found[kind] = h
        rows = []
        for kind, label in (("played", "Recently Played in %s"), ("added", "Recently Added in %s")):
            key = "music-%s-%s-%d" % (kind, self.server_id, sid)
            h = found.get(kind)
            if h is not None:
                items = [i for i in self._items({"Metadata": h.get("Metadata") or []})
                         if i.type in ("album", "artist", "track")]
                row = Row(key, label % lib, items, total=int(h.get("size") or len(items)))
                if h.get("more") and h.get("key"):
                    row.total = max(row.total, len(items) + 1)
                    row.loader = self._pager(h["key"])
            else:
                try:
                    if kind == "played":
                        mc = self.get("/library/sections/%d/all" % sid,
                                      {"type": 9, "sort": "lastViewedAt:desc"}, start=0, size=24)
                        items = [i for i in self._items(mc) if i.extra.get("last_viewed")]
                    else:
                        items = self._items(self.get("/library/sections/%d/recentlyAdded" % sid,
                                                     start=0, size=24))
                except PlexError as e:
                    L.info("music %s list of section %d failed: %s", kind, sid, e)
                    continue
                row = Row(key, label % lib, items)
            if row.items:
                rows.append(row)
        return rows

    def libraries(self, cached=False):
        """Video and music libraries of this server, not hidden, in today's order. cached: never make a
        request (the UI thread); None when the sections are not loaded yet."""
        if not self.sections:
            if cached:
                return None
            self.load_sections()
        out = []
        for s in self._visible_sections():
            it = self._tag(Item(id=str(s["id"]), type="library", title=s["title"], art=s.get("art"),
                                section_id=s["id"], server_id=self.server_id))
            it.extra["section_type"] = s["type"]
            out.append(it)
        return out

    def _pager(self, key):
        def load(start, size):
            return self._items(self.get(key, start=start, size=size))
        return load

    def library_rows(self, lib):
        sid = int(lib.section_id)
        rows = []
        try:
            items = self._items(self.get("/library/sections/%d/recentlyAdded" % sid, start=0, size=24))
            if items:
                rows.append(Row("lib-recent", "Recently Added", items))
        except PlexError:
            pass
        path = "/library/sections/%d/all" % sid
        mc = self.get(path, {"sort": "titleSort"}, start=0, size=48)
        total = int(mc.get("totalSize") or mc.get("size") or 0)

        def load(start, size):
            return self._items(self.get(path, {"sort": "titleSort"}, start=start, size=size))
        rows.append(Row("lib-all", "All A–Z", self._items(mc), total=total, loader=load))
        return rows

    # -- library screen. All of these run on workers.
    def section_type(self, lib):
        """"movie" or "show" for a library Item."""
        sid = int(lib.section_id)
        return next((s["type"] for s in self.sections if s["id"] == sid), None) or \
            lib.extra.get("section_type") or "movie"

    def _lib_params(self, lib, filters=None, kind=None):
        q = {"type": KIND_TYPE.get(kind) or LIB_TYPE.get(self.section_type(lib), 1)}
        q.update(filters or {})
        return q

    def unplayed_filter(self, lib):
        return {"unwatchedLeaves": 1} if self.section_type(lib) == "show" else {"unwatched": 1}

    def recommended(self, lib):
        """The library's hubs, as rows (empty hubs left out). A hub with more items pages by
        its key."""
        mc = self.get("/hubs/sections/%d" % int(lib.section_id), {"count": 12})
        rows = []
        for h in mc.get("Hub") or []:
            if str(h.get("hubIdentifier") or "").startswith("music.stations"):
                items = self._stations(h, lib)
            else:
                items = self._items({"Metadata": h.get("Metadata") or []})
            if not items:
                continue
            row = Row("hub-%s" % (h.get("hubIdentifier") or h.get("title")), h.get("title") or "",
                      items, total=int(h.get("size") or len(items)))
            if h.get("more") and h.get("key"):
                row.total = max(row.total, len(items) + 1)
                row.loader = self._pager(h["key"])
            rows.append(row)
        return rows

    def _stations(self, hub, lib):
        """Library stations (Library Radio, Deep Cuts, ...): Items of type "station" whose id
        is the station key (/library/sections/N/stations/K)."""
        out = []
        for m in hub.get("Metadata") or []:
            key = m.get("key") or ""
            if not key.startswith("/library/sections/"):
                continue
            out.append(self._tag(Item(id=key, type="station", title=m.get("title") or "Radio",
                                      poster=m.get("thumb") or m.get("composite"),
                                      art=m.get("art"), section_id=lib.section_id,
                                      server_id=self.server_id)))
        return out

    def station_tracks(self, station):
        """A station's tracks: the server makes a play queue for it.
        A new call gives the next tracks (the refill)."""
        if not self.server_id:
            self.server_id = self.get("/identity").get("machineIdentifier") or None
        uri = "server://%s/com.plexapp.plugins.library%s" % (self.server_id, station.id)
        q = urllib.parse.urlencode({"type": "audio", "uri": uri, "repeat": 0, "own": 1})
        d = _request(self.base + "/playQueues?" + q, self.h(), method="POST", timeout=10)
        mc = d.get("MediaContainer", {}) if isinstance(d, dict) else {}
        return [i for i in self._items(mc) if i.type == "track"]

    def library_meta(self, lib, kind=None):
        """{"sorts": [{key, title, dir}], "filters": [{filter, title, key}]} for the section's
        type, with the common sorts when the server does not say."""
        try:
            mc = self.get("/library/sections/%d/all" % int(lib.section_id),
                          {"includeMeta": 1, "includeAdvanced": 1}, start=0, size=0)
        except PlexError as e:
            L.info("library meta failed: %s", e)
            return {"sorts": [dict(s) for s in DEFAULT_SORTS], "filters": [], "failed": True}
        want = kind or self.section_type(lib)
        types = (mc.get("Meta") or {}).get("Type") or []
        t = next((t for t in types if t.get("type") == want), types[0] if types else {})
        sorts = [{"key": x.get("key"), "title": x.get("title") or x.get("key"),
                  "dir": x.get("defaultDirection") or "asc"} for x in t.get("Sort") or [] if x.get("key")]
        filters = [{"filter": x.get("filter"), "title": x.get("title") or x.get("filter"),
                    "key": x.get("key")} for x in t.get("Filter") or []
                   if x.get("filter") and x.get("key")
                   and x.get("filterType") != "boolean"]   # not the On/Off ones
        return {"sorts": sorts or [dict(s) for s in DEFAULT_SORTS], "filters": filters}

    def library_page(self, lib, sort, filters, start, size=48, kind=None):
        """(items, total) of one grid page. sort: "titleSort" or "addedAt:desc". kind: a music
        grid kind ("artist" | "album" | "track")."""
        q = self._lib_params(lib, filters, kind)
        if sort:
            q["sort"] = sort
        mc = self.get("/library/sections/%d/all" % int(lib.section_id), q, start=start, size=size)
        return self._items(mc), int(mc.get("totalSize") or mc.get("size") or 0)

    def first_characters(self, lib, filters=None, kind=None):
        """[(letter, count)] in title order, for the A-Z bar."""
        mc = self.get("/library/sections/%d/firstCharacter" % int(lib.section_id),
                      self._lib_params(lib, filters, kind))
        return [(d.get("title") or d.get("key") or "#", int(d.get("size") or 0))
                for d in mc.get("Directory") or []]

    def collections(self, lib, start, size=48):
        mc = self.get("/library/sections/%d/collections" % int(lib.section_id), start=start, size=size)
        return self._items(mc), int(mc.get("totalSize") or mc.get("size") or 0)

    def collection_items(self, coll, start, size=48):
        try:
            mc = self.get("/library/collections/%s/children" % coll.id, start=start, size=size)
        except PlexError as e:
            L.info("collection children failed, trying metadata: %s", e)
            mc = self.get("/library/metadata/%s/children" % coll.id, start=start, size=size)
        return self._items(mc), int(mc.get("totalSize") or mc.get("size") or 0)

    def filter_values(self, key):
        """[(value key, title)] of one filter field (Filter.key), first 100."""
        mc = self.get(key, start=0, size=100)
        return [(str(d.get("key")), d.get("title") or "") for d in mc.get("Directory") or []
                if d.get("key") is not None]

    def details(self, item):
        q = {"includeMarkers": 1, "includeChapters": 1}
        if item.type == "show":
            q["includeOnDeck"] = 1
        mc = self.get("/library/metadata/%s" % item.id, q)
        md = (mc.get("Metadata") or [None])[0]
        if not md:
            return item
        full = self._item(md)
        full.extra["raw"] = md
        full.extra["people"] = [self._tag(p) for p in people(md, self.server_id)]
        if full.type == "show":
            od = (md.get("OnDeck") or {}).get("Metadata")
            od = od[0] if isinstance(od, list) and od else od
            if isinstance(od, dict) and od.get("type") == "episode":
                full.extra["on_deck"] = self._item(od)
        return full

    def extras(self, item):
        """Trailers and other extras, as clip Items. A clip keeps its raw
        metadata (play_info reads its part from it) and plays with no watch state."""
        mc = self.get("/library/metadata/%s/extras" % item.id)
        out = []
        for m in mc.get("Metadata") or []:
            part = (((m.get("Media") or [{}])[0].get("Part")) or [{}])[0]
            if not part.get("key"):
                continue                         # nothing to play
            try:
                et = int(m.get("extraType") or 0)
            except (TypeError, ValueError):
                et = 0
            it = Item(id=str(m.get("ratingKey") or ""), type="clip", title=m.get("title") or "",
                      duration_ms=int(m.get("duration") or part.get("duration") or 0),
                      poster=m.get("thumb"), still=m.get("thumb"), server_id=self.server_id)
            it.extra.update(subtype=m.get("subtype") or EXTRA_TYPES.get(et), raw=m,
                            no_state=True)
            out.append(self._tag(it))
        return out

    def related(self, item):
        """More Like This, the first "similar" hub of the related hubs."""
        try:
            mc = self.get("/library/metadata/%s/related" % item.id, {"count": 12})
        except AuthError:
            raise
        except PlexError as e:
            L.info("related failed, trying the hubs path: %s", e)
            mc = self.get("/hubs/metadata/%s/related" % item.id, {"count": 12})
        h = similar_hub(mc)
        if h is None:
            return []
        return [i for i in self._items({"Metadata": h.get("Metadata") or []})
                if i.type in VIDEO_SECTION_TYPES]

    def _clip_info(self, item):
        """An extra plays direct, from the start, from the part in its extras reply (no second
        metadata call). An absolute part URL on another host gets no token."""
        m = item.extra.get("raw") or {}
        part = (((m.get("Media") or [{}])[0].get("Part")) or [{}])[0]
        key = part.get("key") or ""
        if not key:
            raise PlexError("no media for %s" % item.title)
        if key.startswith(("http://", "https://")):
            url = key
            headers = {"X-Plex-Token": self.token} if key.startswith(self.base + "/") else {}
        else:
            url, headers = self.base + key, {"X-Plex-Token": self.token}
        pi = PlayInfo(url=url, headers=headers, duration_ms=int(part.get("duration") or
                                                                 item.duration_ms or 0),
                      title=item.title, base_url=self.base)
        pi.conn = conn_kind(self.base)
        return pi

    def person_titles(self, person):
        """The person's movies and shows in every visible video library of this
        server, by the actor, director or writer filter. Each section pages by 200 until
        totalSize. A section that fails is left out; all failing raises."""
        if not self.sections:
            self.load_sections()
        field = {"director": "director", "writer": "writer"}.get(person.extra.get("kind"), "actor")
        secs = [s for s in self._visible_sections() if s["type"] in VIDEO_SECTION_TYPES]
        out, seen, errors = [], set(), []
        for sec in secs:
            try:
                start, total, ids = 0, None, set()
                while total is None or start < total:
                    mc = self.get("/library/sections/%d/all" % sec["id"],
                                  {"type": LIB_TYPE[sec["type"]], field: person.id},
                                  start=start, size=PERSON_PAGE)
                    page = self._items(mc)
                    total = int(mc.get("totalSize") or mc.get("size") or 0)
                    new = [i for i in page if i.id not in ids]
                    if not new:
                        break                    # an empty or repeated page ends the section
                    ids.update(i.id for i in new)
                    start += len(page)
                    for i in new:
                        if i.type in VIDEO_SECTION_TYPES and (i.server_id, i.id) not in seen:
                            seen.add((i.server_id, i.id))
                            out.append(i)
            except PlexError as e:
                L.info("person titles in section %s failed: %s", sec["id"], e)
                errors.append(e)
        if secs and len(errors) == len(secs):
            raise errors[0]
        return out

    # -- Live TV pools. Workers only.
    def listing(self, path, params=None, cap=10000, deadline=None, page=200, pause=0.02):
        """Items of a list path in pages of `page`, at most `cap`. A short pause between pages
        lets the UI thread run. Raises PlexError past the deadline."""
        out, start = [], 0
        while len(out) < cap:
            if deadline is not None and time.monotonic() > deadline:
                raise PlexError("deadline passed")
            mc = self.get(path, params, start=start, size=page, timeout=10)
            md = mc.get("Metadata") or []
            out += self._items(mc)
            total = int(mc.get("totalSize") or 0)
            start += len(md)
            if not md or len(md) < page or (total and start >= total):
                break
            time.sleep(pause)
        return out[:cap]

    def leaves(self, show_id, cap=10000, deadline=None):
        return self.listing("/library/metadata/%s/allLeaves" % show_id, cap=cap, deadline=deadline)

    def seasons(self, show_id):
        return self._items(self.get("/library/metadata/%s/children" % show_id,
                                    {"excludeAllLeaves": 1}))

    def episodes(self, season_id):
        return self._items(self.get("/library/metadata/%s/children" % season_id))

    # -- music. All on workers.
    def albums(self, artist):
        """The artist's albums, newest first."""
        items = [i for i in self._items(self.get("/library/metadata/%s/children" % artist.id))
                 if i.type == "album"]
        return sorted(items, key=lambda i: -(i.year or 0))

    def tracks(self, album):
        return [i for i in self._items(self.get("/library/metadata/%s/children" % album.id))
                if i.type == "track"]

    def artist_tracks(self, artist):
        return [i for i in self._items(self.get("/library/metadata/%s/allLeaves" % artist.id))
                if i.type == "track"]

    def shuffle_tracks(self, lib, filters=None, n=200):
        q = self._lib_params(lib, filters, "track")
        q["sort"] = "random"
        mc = self.get("/library/sections/%d/all" % int(lib.section_id), q, start=0, size=n)
        return [i for i in self._items(mc) if i.type == "track"]

    def track_info(self, track):
        """PlayInfo for direct play of a track. The token goes in a header only. One metadata
        request per track gives the part and the loudness; the result is
        cached by (server, ratingKey, part). When the part key is known, a failed request only
        costs the gain: the track plays with no fallback option."""
        key = track.extra.get("part_key")
        loud = track.extra.get("loud")
        dur = track.duration_ms
        if loud is None or not key:
            c = self._tmeta.get((self.server_id, track.id))
            if c is not None and (not key or c[0] == key):
                key, loud, dur = c[0], c[1], dur or c[2]
            else:
                try:
                    mc = self.get("/library/metadata/%s" % track.id, timeout=3 if key else 8)
                except PlexError as e:
                    if not key:
                        raise
                    L.info("track loudness request failed: %s", e)
                    mc = None
                md = ((mc or {}).get("Metadata") or [None])[0]
                if md:
                    full = self._item(md)
                    key = key or full.extra.get("part_key")
                    loud = full.extra.get("loud") or {}      # fetched: {} is "no data"
                    dur = dur or full.duration_ms
                    if key:
                        if len(self._tmeta) > 5000:
                            self._tmeta.clear()
                        self._tmeta[(self.server_id, track.id)] = (key, loud, full.duration_ms)
                if not key:
                    raise PlexError("no media part for this track")
        return PlayInfo(url=self.base + key, headers={"X-Plex-Token": self.token},
                        duration_ms=dur, gain_db=loudness.gain_db(loud),
                        gain_max_db=loudness.limit_db(loud))

    # -- playlists. All on workers; the token goes in the header only.
    # Playlist names are user text: they go in the URL and never in a log line.
    PLAYLIST_ENTRY_TYPES = ("movie", "episode", "track")

    def _playlist(self, m):
        kind = m.get("playlistType") or ""
        it = Item(id=str(m.get("ratingKey", "")), type="playlist", title=m.get("title", ""),
                  duration_ms=int(m.get("duration") or 0), leaf_count=int(m.get("leafCount") or 0),
                  poster=m.get("composite") or m.get("thumb"), summary=m.get("summary", ""),
                  server_id=self.server_id)
        it.extra["smart"] = bool(int(m.get("smart") or 0))
        it.extra["kind"] = kind
        return self._tag(it)

    def _entry(self, m):
        """One playlist entry: a normal movie, episode or track Item with extra["pl_item"].
        An entry from another server names it in sourceURI (server://<id>/...); the Item then
        carries that server's id, so media calls, images and reports go there."""
        src = m.get("sourceURI") or ""
        sid = None
        if src.startswith("server://"):
            sid = src.split("/")[2] or None
        it = self._item(m, sid if sid and sid != self.server_id else None)
        it.extra["pl_item"] = str(m.get("playlistItemID", ""))
        it.extra["occ"] = "pl:%s" % it.extra["pl_item"]
        return it

    def playlists(self, kind, section_id=None):
        """Playlists of one type ("audio" | "video") on this server; section_id: one library."""
        q = {"playlistType": kind}
        if section_id is not None:
            q["sectionID"] = int(section_id)
        mc = self.get("/playlists", q, start=0, size=1000)
        return [self._playlist(m) for m in mc.get("Metadata") or []
                if m.get("type") == "playlist" and (m.get("playlistType") or "") == kind]

    def playlist_items(self, pl, start, size=200):
        """(entries, total, n): the movie, episode and track entries of one page, the total
        number of entries, and how many entries this page had (other types left out)."""
        mc = self.get("/playlists/%s/items" % pl.id, start=start, size=size)
        md = mc.get("Metadata") or []
        items = [self._entry(m) for m in md if m.get("type") in self.PLAYLIST_ENTRY_TYPES]
        return items, int(mc.get("totalSize") or mc.get("size") or len(md)), len(md)

    def items_uri(self, items):
        """server://<machine id>/com.plexapp.plugins.library/library/metadata/<id,id>. A show,
        season, album or artist id adds all its leaves."""
        if not self.server_id:
            self.server_id = self.get("/identity").get("machineIdentifier") or None
        bad = [i for i in items if i.server_id not in (None, self.server_id)]
        if bad or not items:
            raise PlexError("items from another server")
        return "server://%s/%s/library/metadata/%s" % (self.server_id, IDENT,
                                                       ",".join(i.id for i in items))

    def _write(self, method, path, q=None):
        url = self.base + path
        if q:
            url += "?" + urllib.parse.urlencode(q)
        return _request(url, self.h(), method=method, timeout=8)

    def playlist_create(self, title, kind, items):
        d = self._write("POST", "/playlists", {"type": kind, "title": title, "smart": 0,
                                                "uri": self.items_uri(items)})
        md = ((d.get("MediaContainer") or {}).get("Metadata") or [None])[0] if isinstance(d, dict) else None
        if not md:
            raise PlexError("no playlist in the reply")
        md.setdefault("playlistType", kind)
        md.setdefault("type", "playlist")
        return self._playlist(md)

    def playlist_add(self, pl, items):
        self._write("PUT", "/playlists/%s/items" % pl.id, {"uri": self.items_uri(items)})

    def playlist_remove(self, pl, entry):
        self._write("DELETE", "/playlists/%s/items/%s" % (pl.id, entry.extra["pl_item"]))

    def playlist_move(self, pl, entry, after):
        """after: the entry it goes after, or None for the first place."""
        q = {"after": after.extra["pl_item"]} if after is not None else None
        self._write("PUT", "/playlists/%s/items/%s/move" % (pl.id, entry.extra["pl_item"]), q)

    def playlist_rename(self, pl, title):
        self._write("PUT", "/playlists/%s" % pl.id, {"title": title})

    def playlist_delete(self, pl):
        self._write("DELETE", "/playlists/%s" % pl.id)

    def playlist(self, pl):
        """The playlist again (count, length, composite) after a change."""
        md = (self.get("/playlists/%s" % pl.id).get("Metadata") or [None])[0]
        return self._playlist(md) if md else None

    # -- playback
    def play_info(self, item, resume=True, opts=None):
        """(PlayInfo, full item). Direct play first; transcode only by these rules:
        the playback menu (opts mode) > the quality setting > the automatic rules.
        opts: mode ("direct" | "transcode"), session (the timeline session id), retry
        (direct play failed to load)."""
        opts = opts or {}
        if item.extra.get("no_state"):
            return self._clip_info(item), item       # an extra: direct, no conversion
        pi, full, media = self._direct_info(item, resume)
        if opts.get("at_s") is not None:      # Live TV: the tune offset
            pi.start_s = max(0.0, float(opts["at_s"]))
        pi.conn = conn_kind(self.base)
        pi.src_kbps = int((media or {}).get("bitrate") or 0)   # rule 6 never steps above it
        pi.src_aspect = source_aspect(media)
        q, reason = self.transcode_choice(media, part_key=pi.url[len(self.base):], opts=opts)
        if q is None:
            return pi, full
        try:
            tpi = self._transcode_info(pi, full, q, reason, opts)
            tpi.src_kbps = pi.src_kbps
            return tpi, full
        except TranscodeRefused as e:
            L.info("transcode refused: %s %s", e.code, e.text)
            if reason == "bandwidth":
                # Only the speed asked for it: play the original, and say why.
                pi.notice = "This server does not allow video conversion. Playing original quality."
                return pi, full
            err = TranscodeRefused("%s can't convert this video." % (
                getattr(self, "server_name", "") or "The server"), e.code, e.text)
            err.screen = True                # this text is written for the screen
            raise err

    def transcode_choice(self, media, part_key="", opts=None):
        """(quality dict, reason) to transcode, or (None, "") to play direct."""
        opts = opts or {}
        mode = opts.get("mode")
        if mode == "direct":
            return None, ""
        if mode == "transcode":
            q = quality_of(opts.get("quality")) or quality("480p")
            return q, opts.get("reason") or ("retry" if opts.get("retry") else "menu")
        kind = conn_kind(self.base)
        st = self.settings
        want = (st["quality_home"] if kind == "local" else st["quality_remote"]) or "original"
        if want in QUALITY_KBPS:
            return quality(want), "setting"
        if want != "auto":
            return None, ""
        if kind == "relay":
            return QRELAY, "bandwidth"
        rate = int((media or {}).get("bitrate") or 0)
        # A file at or under the lowest target gains nothing from a conversion: no probe.
        if rate > QRELAY["bitrate"] and part_key:
            kbps = self.measure(part_key)
            if kbps and rate > AUTO_SHARE * kbps:
                q = auto_quality(kbps)
                L.info("bitrate %d over 0.7 x %.0f kbps: transcode at %s", rate, kbps, q["label"])
                return q, "bandwidth"
        return None, ""

    def measure(self, part_key):
        """Throughput in kbps: a Range GET of the first 4 MiB of a part, cut at 3 s. One
        result per server for 10 min. None when the probe fails; a failure is kept 1 min."""
        now = time.monotonic()
        got = _MEASURED.get(self.base)
        if got and now - got[1] < (MEASURE_TTL if got[0] is not None else MEASURE_FAIL_TTL):
            return got[0]
        h = dict(self.h(), Range="bytes=0-%d" % (PROBE_BYTES - 1))
        req = urllib.request.Request(self.base + part_key, headers=h)
        n, t0 = 0, time.monotonic()
        try:
            with _opener.open(req, timeout=PROBE_S) as r:   # connect at most 3 s
                t0 = time.monotonic()            # connect, TLS and first byte excluded
                while time.monotonic() - t0 < PROBE_S and n < PROBE_BYTES:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    n += len(chunk)
        except Exception as e:
            L.info("throughput probe failed: %s", str(e).split("?")[0])
            _MEASURED[self.base] = (None, time.monotonic())
            return None
        dt = max(time.monotonic() - t0, 0.05)
        kbps = n * 8 / 1000.0 / dt
        _MEASURED[self.base] = (kbps, time.monotonic())
        L.info("throughput %.0f kbps (%d bytes in %.2f s)", kbps, n, dt)
        return kbps

    def transcode_query(self, item_id, offset, q, session, subs, local, retry=False):
        return {
            "path": "/library/metadata/%s" % item_id, "mediaIndex": 0, "partIndex": 0,
            "protocol": "hls", "offset": int(max(0, offset)), "fastSeek": 1,
            "directPlay": 0, "directStream": 0 if retry else 1, "directStreamAudio": 1,
            "videoQuality": 99, "maxVideoBitrate": q["bitrate"], "videoResolution": q["res"],
            "subtitles": "burn" if subs else "none", "audioBoost": 100, "subtitleSize": 100,
            "location": "lan" if local else "wan", "autoAdjustQuality": 0, "hasMDE": 1,
            "addDebugOverlay": 0, "session": session, "X-Plex-Session-Identifier": session,
            "X-Plex-Client-Identifier": self.client_id, "X-Plex-Product": "Batoplex",
            "X-Plex-Version": VERSION, "X-Plex-Platform": "Linux", "X-Plex-Device": "Batocera",
            # The server has no profile for this platform and refuses with HTTP 400 unless
            # the request names one. Generic plus PROFILE_EXTRA gives an h264/aac HLS stream.
            "X-Plex-Client-Profile-Name": "Generic",
        }

    def transcode_headers(self, session):
        h = self.h()
        h["X-Plex-Session-Identifier"] = session
        h["X-Plex-Client-Profile-Extra"] = PROFILE_EXTRA
        return h

    def transcode_decision(self, query, session):
        """None when the server will convert; else raises. 401/403 -> AuthError
        (sign-in), 2xxx/3xxx -> TranscodeRefused, 4xxx/5xxx/network -> PlexError."""
        url = self.base + "/video/:/transcode/universal/decision?" + urllib.parse.urlencode(query)
        try:
            d = _request(url, self.transcode_headers(session), timeout=10)
        except AuthError:
            raise
        except PlexError as e:
            if "HTTP 403" in str(e):
                raise AuthError("HTTP 403", code=403)
            raise
        mc = d.get("MediaContainer", {}) if isinstance(d, dict) else {}
        code = int(mc.get("generalDecisionCode") or 0)
        text = mc.get("generalDecisionText") or mc.get("transcodeDecisionText") or ""
        tcode = int(mc.get("transcodeDecisionCode") or 0)
        if code in (1000, 1001) and tcode not in range(2000, 4000):
            return None
        L.info("transcode decision %s/%s: %s", code, tcode, text or mc.get("transcodeDecisionText"))
        bad = tcode if tcode >= 2000 else code
        if 2000 <= bad < 4000:
            raise TranscodeRefused("conversion refused", bad, text)
        raise PlexError("server error %s: %s" % (bad, text))

    def _transcode_info(self, pi, full, q, reason, opts):
        session = opts.get("session") or uuid.uuid4().hex
        local = pi.conn == "local"
        subs = bool(pi.sub_index is not None or pi.sub_url)
        item_id = full.id

        def query(offset):
            return self.transcode_query(item_id, offset, q, session, subs, local,
                                        retry=reason == "retry")
        self.transcode_decision(query(pi.start_s), session)

        def url_at(t):
            return (self.base + "/video/:/transcode/universal/start.m3u8?"
                    + urllib.parse.urlencode(query(t)))
        h = self.transcode_headers(session)
        pi.headers = {"X-Plex-Token": self.token,
                      "X-Plex-Client-Identifier": self.client_id,
                      "X-Plex-Session-Identifier": session,
                      "X-Plex-Client-Profile-Extra": h["X-Plex-Client-Profile-Extra"]}
        pi.mode, pi.quality, pi.reason, pi.session = "transcode", q["label"], reason, session
        pi.offset_s = float(int(max(0, pi.start_s)))
        pi.url_at = url_at
        pi.url = url_at(pi.offset_s)
        return pi

    def stop_transcode(self, session):
        _request(self.base + "/video/:/transcode/universal/stop?" +
                 urllib.parse.urlencode({"session": session}), self.h(), timeout=3, raw=True)

    def ping_transcode(self, session):
        _request(self.base + "/video/:/transcode/universal/ping?" +
                 urllib.parse.urlencode({"session": session}), self.h(), timeout=3, raw=True)

    def _direct_info(self, item, resume):
        full = self.details(item)
        md = full.extra.get("raw") or {}
        media = (md.get("Media") or [None])[0]
        if not media:
            raise PlexError("no media for %s" % item.title)
        part = (media.get("Part") or [None])[0]
        streams = part.get("Stream") or []
        audio = sorted([s for s in streams if s.get("streamType") == 2 and "index" in s],
                       key=lambda s: s["index"])
        subs = [s for s in streams if s.get("streamType") == 3]
        emb = sorted([s for s in subs if "index" in s and not s.get("key")], key=lambda s: s["index"])
        pi = PlayInfo(url=self.base + part["key"], headers={"X-Plex-Token": self.token},
                      # Only Part[0] plays: a stacked item must use that part's length.
                      duration_ms=int((part.get("duration") if len(media.get("Part") or []) > 1
                                       else None) or md.get("duration") or part.get("duration") or 0),
                      start_s=(int(md.get("viewOffset") or 0) / 1000.0) if resume else 0.0,
                      title=full.hero_title if full.type != "episode" else
                      " • ".join(x for x in (full.show_title, _ep_code(full)) if x),
                      part_id=part.get("id"), tracks=tracks.parse(part),
                      markers=tracks.parse_markers(md), base_url=self.base)
        for n, s in enumerate(audio, 1):
            if s.get("selected"):
                pi.audio_index, pi.audio_pos = s["index"], n
        for s in subs:
            if not s.get("selected"):
                continue
            if s.get("key"):
                pi.sub_url = self.base + s["key"]
            elif s in emb:
                pi.sub_index, pi.sub_pos = s["index"], emb.index(s) + 1
        return pi, full, media

    def timeline(self, item, state, time_ms, duration_ms, session_id):
        q = urllib.parse.urlencode({"ratingKey": item.id, "key": "/library/metadata/%s" % item.id,
                                    "identifier": IDENT, "state": state, "time": int(time_ms),
                                    "duration": int(duration_ms)})
        h = self.h()
        h["X-Plex-Session-Identifier"] = session_id
        _request(self.base + "/:/timeline?" + q, h, timeout=5)

    def set_streams(self, part_id, audio_id=None, sub_id=None):
        """Write the track choice back to Plex so other clients agree.
        sub_id 0 means subtitles off."""
        q = {"allParts": 1}
        if audio_id is not None:
            q["audioStreamID"] = audio_id
        if sub_id is not None:
            q["subtitleStreamID"] = sub_id
        _request(self.base + "/library/parts/%s?%s" % (part_id, urllib.parse.urlencode(q)),
                 self.h(), method="PUT", timeout=5, raw=True)

    def unscrobble(self, item):
        q = urllib.parse.urlencode({"key": item.id, "identifier": IDENT})
        _request(self.base + "/:/unscrobble?" + q, self.h(), timeout=5)

    def children_rows(self, item):
        items = self._items(self.get("/library/metadata/%s/children" % item.id))
        return [Row("children", item.title, items)]

    def scrobble(self, item):
        q = urllib.parse.urlencode({"key": item.id, "identifier": IDENT})
        _request(self.base + "/:/scrobble?" + q, self.h(), timeout=5)
