"""The open station directory radio-browser.info. No pygame.

Batoplex asks the directory only while Add Stations is open. A request carries only the
User-Agent and Accept headers: no Plex header, no token, no client id, no host name. No click
or vote call. The search text is user text: it is never logged.
"""
import http.client
import json
import os
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from . import log, radio
from .config import VERSION
from .model import Item, Row

L = log.get("stationdir")

DNS_NAME = "all.api.radio-browser.info"
NAME_RE = re.compile(r"^[a-z0-9-]+\.api\.radio-browser\.info$")
TRIES = 3
TIMEOUT_S = 6
MAX_REPLY = 512 * 1024
LIMIT = 50
POPULAR_S = 15 * 60
QUERIES = 8
MAX_URL = 2048


def _test_base():
    """BATOPLEX_RADIO_DIR points the tests at a mock, and only at 127.0.0.1 (the _plex_tv
    guard)."""
    v = os.environ.get("BATOPLEX_RADIO_DIR", "")
    if not v:
        return None
    u = urllib.parse.urlsplit(v)
    try:
        port = u.port
    except ValueError:
        return None
    if u.scheme == "http" and u.hostname == "127.0.0.1" and port and not u.username \
            and not u.password and u.netloc == "127.0.0.1:%d" % port:
        return "http://127.0.0.1:%d" % port
    return None


class _SameHost(urllib.request.HTTPRedirectHandler):
    """A redirect to another host is refused."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a, b = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if (a.scheme, a.netloc) != (b.scheme, b.netloc):
            raise urllib.error.HTTPError(newurl, code, "redirect to another host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _resolve():
    """The directory server names (E1): the DNS addresses of all.api, then a reverse lookup
    of each. Only names of the form <x>.api.radio-browser.info stay."""
    names = []
    for _f, _t, _p, _c, addr in socket.getaddrinfo(DNS_NAME, 443, proto=socket.IPPROTO_TCP):
        try:
            n = socket.gethostbyaddr(addr[0])[0].lower().rstrip(".")
        except OSError:
            continue
        if NAME_RE.match(n) and n not in names:
            names.append(n)
    return names


class BadReply(Exception):
    pass


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _text(v):
    return " ".join("".join(c if c.isprintable() else " " for c in str(v)).split())


def _url(v):
    """v when it is an http or https URL with a host, 2048 characters at most, all printable
    (no control character, line separator or lone surrogate: one line in the file); else ""."""
    if not isinstance(v, str) or not v or len(v) > MAX_URL or v != v.strip() or \
            not v.isprintable():
        return ""
    return v if radio.url_host(v) else ""


def line2(st):
    """"JAZZ • SMOOTH • US • MP3 128": 3 tags, the country, the codec and bitrate (19 2.2).
    Bitrate 0 and codec UNKNOWN are left out."""
    tags = [_text(t)[:24] for t in str(st.get("tags") or "").split(",")]
    parts = [t for t in tags if t][:3]
    cc = _text(st.get("countrycode") or "")
    if re.match(r"^[A-Za-z]{2}$", cc):
        parts.append(cc.upper())
    codec = _text(st.get("codec") or "")[:12]
    if codec.upper() == "UNKNOWN":
        codec = ""
    br = _int(st.get("bitrate"))
    cb = " ".join(p for p in (codec, str(br) if br > 0 else "") if p)
    if cb:
        parts.append(cb)
    return " • ".join(parts)


def rows_from(data, limit=LIMIT):
    """Station Items from a directory reply, after the filters: lastcheckok 1, ssl_error
    0, no HLS, a valid url_resolved; one Item per url_resolved."""
    if not isinstance(data, list):
        raise BadReply("not a list")
    out, seen = [], set()
    for st in data:
        if not isinstance(st, dict):
            continue
        url = _url(st.get("url_resolved"))
        if not url or url in seen or _int(st.get("lastcheckok")) != 1 or \
                _int(st.get("ssl_error")) != 0 or _int(st.get("hls")) != 0:
            continue
        seen.add(url)
        uuid = str(st.get("stationuuid") or "")
        uuid = uuid if re.match(r"^[0-9A-Za-z-]{1,64}$", uuid) else ""
        name = radio.clean_name(st.get("name") if isinstance(st.get("name"), str) else "", url)
        out.append(Item(id=url, type="station", title=name,
                        extra={"url": url, "alt": _url(st.get("url")), "uuid": uuid,
                               "line2": line2(st)}))
        if len(out) >= limit:
            break
    return out


class Directory:
    """search(q, limit) -> [Row]: q "" is Popular, else a search by name. Runs on a worker;
    one request at a time (SearchSession)."""

    def __init__(self, opener=None, resolve=_resolve, clock=time.monotonic, rng=random):
        self.opener = opener or urllib.request.build_opener(_SameHost())
        self.resolve, self.clock, self.rng = resolve, clock, rng
        self.base = _test_base()
        self.servers = None
        self.popular = None             # (time, [Item])
        self.queries = OrderedDict()    # query -> [Item], the last 8 of the app run
        self.lock = threading.Lock()
        self.cache_lock = threading.Lock()     # two screens can have requests in flight

    def _servers(self):
        if self.base:
            return [self.base]
        with self.lock:
            if not self.servers:
                try:
                    names = self.resolve()
                except OSError as e:
                    L.info("station directory lookup failed: %s", e)
                    names = []
                names = list(names)
                self.rng.shuffle(names)
                self.servers = ["https://" + n for n in (names or [DNS_NAME])]
            return list(self.servers)

    def _get(self, path):
        """JSON from the first server that answers; a network error, a timeout, HTTP 5xx or
        bad JSON (not a list) moves to the next one (3 at most). The server that answered stays first."""
        servers = self._servers()
        err = None
        for base in servers[:TRIES]:
            host = urllib.parse.urlsplit(base).hostname
            req = urllib.request.Request(base + path, headers={
                "User-Agent": "BatoplexCRT/%s" % VERSION, "Accept": "application/json"})
            try:
                with self.opener.open(req, timeout=TIMEOUT_S) as r:
                    body = r.read(MAX_REPLY + 1)
                if len(body) > MAX_REPLY:
                    raise BadReply("over %d KB" % (MAX_REPLY // 1024))
                data = json.loads(body.decode("utf-8"))
                if not isinstance(data, list):
                    raise BadReply("not a list")
            except (OSError, ValueError, BadReply, http.client.HTTPException) as e:
                err = e                     # HTTPError too: a 403 or 429 from one server
                err = e
            else:
                with self.lock:
                    if self.servers and base in self.servers:
                        self.servers.remove(base)
                        self.servers.insert(0, base)
                return data, host
            L.info("station directory %s failed: %s", host, type(err).__name__)
        with self.lock:
            self.servers = None             # all failed: the next request looks them up again
        raise err or BadReply("no server")

    def search(self, q, limit=LIMIT):
        limit = max(1, min(LIMIT, limit))
        q = q.strip()
        if not q:
            with self.cache_lock:
                pop = self.popular
            if pop and self.clock() - pop[0] < POPULAR_S:
                items = pop[1]
            else:
                data, host = self._get("/json/stations/topclick/%d?hidebroken=true" % LIMIT)
                items = rows_from(data)
                with self.cache_lock:
                    self.popular = (self.clock(), items)
                L.info("station directory %s: popular, %d stations", host, len(items))
            return [Row("popular", "Popular", list(items[:limit]))] if items else []
        with self.cache_lock:
            items = self.queries.get(q)
            if items is not None:
                self.queries.move_to_end(q)
        if items is None:
            data, host = self._get("/json/stations/search?" + urllib.parse.urlencode(
                {"name": q, "hidebroken": "true", "order": "clickcount", "reverse": "true",
                 "limit": LIMIT}))
            items = rows_from(data)
            with self.cache_lock:
                self.queries[q] = items
                while len(self.queries) > QUERIES:
                    self.queries.popitem(last=False)
            L.info("station directory %s: %d characters, %d stations", host, len(q), len(items))
        return [Row("stations", "Stations", list(items[:limit]))] if items else []


_dir = None


def get():
    """The directory of this app run (the server list and caches stay)."""
    global _dir
    if _dir is None:
        _dir = Directory()
    return _dir
