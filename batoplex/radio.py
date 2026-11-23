"""Radio: stations the user lists in radio.m3u in the data directory.

Batoplex ships no station, no station name and no art, and makes no request for radio itself:
only mpv opens the user's URL. The file is an extended M3U:

    #EXTM3U
    #EXTINF:-1,Station name
    https://example.com/stream.aac

A URL with no #EXTINF name uses its host. Only http and https URLs count. The file is read
again only when its time stamp changes. Add Stations appends a station as
three lines (#RADIOBROWSER:<uuid>, #EXTINF, the URL) and removes only those lines.
"""
import os
import re
import urllib.parse

from . import config, log
from .model import Item

L = log.get("radio")
MAX_BYTES = 256 * 1024
MAX_STATIONS = 200
FILE = "radio.m3u"

_cache = {"key": None, "stations": []}


def path():
    return os.path.join(config.data_dir(), FILE)


def _clean(raw):
    # No control characters: pygame cannot draw a NUL. Drop a BOM too.
    return "".join(c for c in raw if c >= " " and c not in "\x7f\ufeff").strip()


def url_host(line):
    try:
        u = urllib.parse.urlsplit(line)
        return u.hostname if u.scheme in ("http", "https") and u.hostname else None
    except ValueError:                               # one bad line, not the whole file
        return None


def entries(text):
    """[(name, url, uuid, span)] for every valid station; span: the indexes in
    text.splitlines() of the #RADIOBROWSER line, the #EXTINF line (None when absent) and the
    URL line. A name or uuid line holds until the next line that is not a comment."""
    out, name, uuid, ext_i, rb_i = [], None, "", None, None
    for i, raw in enumerate(text.splitlines()):
        line = _clean(raw)
        if not line:
            continue
        if line.startswith("#"):
            up = line.upper()
            if up.startswith("#EXTINF:") and "," in line:
                name, ext_i = line.split(",", 1)[1].strip() or None, i
            elif up.startswith("#RADIOBROWSER:"):
                uuid, rb_i = line.split(":", 1)[1].strip(), i
            continue
        host = url_host(line)
        if host:
            out.append((name or host, line, uuid, (rb_i, ext_i, i)))
        name, uuid, ext_i, rb_i = None, "", None, None
    return out


def parse(text):
    """[(name, url)] from M3U text."""
    return [(n, u) for n, u, _id, _s in entries(text)][:MAX_STATIONS]


def clean_name(name, url=""):
    """A directory name for the file: no control characters, spaces collapsed, 80
    characters at most; empty: the URL host."""
    name = " ".join("".join(c if c.isprintable() else " " for c in name or "").split())   # also lone surrogates
    return name[:80].strip() or (url_host(url) or "")


class Refused(Exception):
    """An add or remove that wrote nothing; str(e) is the toast (19 D11b)."""


def _read():
    """(text, line end) of the file on disk now; ("", "\n") with no file."""
    p = path()
    try:
        with open(p, "rb") as f:
            data = f.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return "", "\n"
    except OSError as e:
        L.info("radio.m3u not read: %s", e)
        raise Refused("Can't change radio.m3u")
    if len(data) > MAX_BYTES:
        raise Refused("Can't change radio.m3u")
    text = data.decode("utf-8", "surrogateescape")
    first = text.splitlines(True)[:1]
    return text, "\r\n" if first and first[0].endswith("\r\n") else "\n"


def _write(text):
    """Atomic: a temporary file in the same directory, fsync, os.replace; the mode stays and a
    symlink stays (its target changes). On the UI thread: the file is 256 KB at most."""
    try:
        config.atomic_write(os.path.realpath(path()), text.encode("utf-8", "surrogateescape"),
                            prefix=".radio-")
    except OSError as e:
        L.info("radio.m3u not written: %s", e)
        raise Refused("Can't save station")


def _matches(entry, urls, uuid):
    return entry[1] in urls or bool(uuid and entry[2] == uuid)


def has(text, urls, uuid=""):
    """True when a station in text has one of urls (exact text) or the directory uuid."""
    urls = {u for u in urls if u}
    return any(_matches(e, urls, uuid) for e in entries(text))


def read_text():
    """The file text for has(); "" when it cannot be read."""
    try:
        return _read()[0]
    except Refused:
        return ""


def add(name, url, uuid=""):
    """Append a station. False when the URL or uuid is in the file already
    (nothing written). Raises Refused with the toast text."""
    text, nl = _read()
    found = entries(text)
    if any(_matches(e, {url}, uuid) for e in found):
        return False
    if len(found) >= MAX_STATIONS:
        raise Refused("Radio is full")
    uuid = re.sub(r"[^0-9A-Za-z-]", "", uuid or "")[:64]
    lines = (["#RADIOBROWSER:" + uuid] if uuid else []) + \
        ["#EXTINF:-1," + clean_name(name, url), url]
    head = text if not text or text.endswith(("\n", "\r")) else text + nl
    new = (head or "#EXTM3U" + nl) + nl.join(lines) + nl
    if len(new.encode("utf-8", "surrogateescape")) > MAX_BYTES:
        raise Refused("Radio is full")
    _write(new)
    return True


def remove(urls, uuid=""):
    """Delete every station with one of urls or the uuid: its URL line and the #EXTINF and
    #RADIOBROWSER lines the parse attached to it. All other lines stay
    byte for byte. Returns the URLs removed ([] when no station matched)."""
    text, _nl = _read()
    urls = {u for u in urls if u}
    drop, gone = set(), []
    for e in entries(text):
        if _matches(e, urls, uuid):
            drop.update(i for i in e[3] if i is not None)
            gone.append(e[1])
    if not drop:
        return []
    _write("".join(ln for i, ln in enumerate(text.splitlines(True)) if i not in drop))
    return gone


def stations():
    """The station Items from the user's file ([] with no file)."""
    p = path()
    try:
        st = os.stat(p)
    except OSError:
        _cache.update(key=None, stations=[])
        return []
    key = (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)
    if key == _cache["key"]:
        return _cache["stations"]
    items = []
    try:
        if st.st_size > MAX_BYTES:
            raise ValueError("over %d KB" % (MAX_BYTES // 1024))
        with open(p, encoding="utf-8", errors="replace") as f:
            items = [Item(id=url, type="station", title=name, extra={"url": url})
                     for name, url in parse(f.read())]
    except (OSError, ValueError) as e:
        L.info("radio.m3u not read: %s", e)
    _cache.update(key=key, stations=items)
    return items


def split_title(icy):
    """"Artist - Title" -> (title, artist); a title with no " - " -> (title, "")."""
    icy = (icy or "").strip()
    if " - " in icy:
        artist, title = icy.split(" - ", 1)
        return title.strip(), artist.strip()
    return icy, ""
