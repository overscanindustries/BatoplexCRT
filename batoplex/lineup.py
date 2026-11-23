"""Live TV lineup.

- The time zone of the box, read once (schedule.py takes an IANA name).
- Pools: the shared source queries of channels.plan_queries(), fetched by FETCH_WORKERS
  threads with a deadline, the queries of the lowest channel numbers first. A query whose
  library did not change since the last fetch (the section stamp of Plex) uses the stored
  entries and makes no list request. Each channel posts its tape as soon as its own queries
  are done (the guide fills row by row); the end result stays the authority.
- The daily freeze: schedule.freeze() for each channel, at start and at 04:00 local time while
  the app runs; caches on disk for each account and profile. A start on the same broadcast
  day fetches nothing.
- A status code for each channel (why a channel is hidden), and what the guide and the player
  read: the visible channels and their tapes, in memory only.
- Pure helpers for tuning and surfing: the tune point with the 30 s start snap, channel
  up/down with wrap, the last channel.
- Make Channels for Me: a light index of each pinned library, the library
  summaries for channels.propose() and the theme analysis for themes.curate(), on a worker.

Nothing here runs on the UI thread except reads of memory. Channel names are user text: never
logged.
"""
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import Future
from datetime import date, timedelta, timezone

from . import channels, config, log, schedule, themes

L = log.get("lineup")

DEADLINE_S = 120.0           # one freeze worker: what has not arrived waits for the retry
FETCH_WORKERS = 2            # queries fetched at once by a freeze (the weak box: 2)
RETRY_S = 600                # a failed fetch tries again after 10 min
FREEZE_AFTER_S = 30          # the daily freeze runs this long after 04:00 local time
FIRST_DELAY_S = 4.0          # after start or a profile change: Home loads first
SCAN_S = 20.0                # Make Channels for Me: the scan deadline
INDEX_KEEP_S = 600           # a complete scan index serves the next freeze this long
FAILED_FILE = "failed.json"  # the query keys that failed in the last freeze: fetched last
SNAP_S = 30                  # a tune this soon after a programme start shows it from its start

ON, EMPTY, OFFLINE, MISSING, FILE, OFF = "on", "empty", "offline", "missing", "file", "off"
STATUS_TEXT = {ON: "On", EMPTY: "No items for this profile", OFFLINE: "Server not available",
               MISSING: "Source not found", FILE: "Error in channels.json", OFF: "Off"}


# ---------------------------------------------------------------- time zone

ZONE_FILES = ("/userdata/system/batocera.conf", "/etc/timezone", "/etc/localtime")


def _valid(name):
    if not name or schedule.ZoneInfo is None:
        return None
    try:
        schedule.ZoneInfo(name)
        return name
    except Exception:
        return None


def read_zone(files=ZONE_FILES):
    """The IANA name of the box zone, or None. Box check: batocera.conf system.timezone, then
    /etc/timezone, then the /etc/localtime link (…/zoneinfo/Europe/Paris)."""
    conf, etc_tz, localtime = files
    try:
        with open(conf, encoding="utf-8", errors="replace") as f:
            for ln in f:
                k, _, v = ln.strip().partition("=")
                if k.strip() == "system.timezone" and _valid(v.strip()):
                    return v.strip()
    except OSError:
        pass
    try:
        with open(etc_tz, encoding="utf-8", errors="replace") as f:
            n = _valid(f.read().strip())
            if n:
                return n
    except OSError:
        pass
    try:
        target = os.path.realpath(localtime)
        if "zoneinfo/" in target:
            return _valid(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    return None


def box_zone(files=ZONE_FILES):
    """A tzinfo: the box zone, or the fixed offset of the system now (one log line)."""
    name = read_zone(files)
    if name:
        return schedule.zone(name)
    off = time.localtime().tm_gmtoff
    L.info("no time zone name on this box: fixed offset %+d min", off // 60)
    return timezone(timedelta(seconds=off))


_clock_zone = []


def clock_hm():
    """The on-screen clock, "HH:MM" in the box zone (as the guide and the schedules). The
    zone is read once; with no zone name on the box, the system local time."""
    if not _clock_zone:
        name = read_zone()
        _clock_zone.append(schedule.zone(name) if name else None)
    tz = _clock_zone[0]
    if tz is None:
        return time.strftime("%H:%M")
    from datetime import datetime
    return datetime.now(tz).strftime("%H:%M")


# ---------------------------------------------------------------- freeze timing

def next_freeze(now, tz):
    """Epoch of the next daily freeze: the next local 04:00 after now, plus FREEZE_AFTER_S."""
    _b, d = schedule.day_start(now, tz)
    return schedule.day_bounds(d + timedelta(days=1), tz) + FREEZE_AFTER_S


def needs_pools(cache, plan, now, tz):
    """True when schedule.freeze() would take new pools: no usable cache, a new rule revision,
    a later broadcast date, or a stale cache (a fetch failed). Else the tape only grows."""
    if not isinstance(cache, dict) or cache.get("v") != schedule.VERSION \
            or cache.get("rev") != plan.rev:
        return True
    try:
        cd = date.fromisoformat(cache["date"])
    except (KeyError, TypeError, ValueError):
        return True
    return schedule.broadcast_date(now, tz) > cd or bool(cache.get("stale"))


# ---------------------------------------------------------------- tuning and surfing

def tune_point(tape, now, snap=SNAP_S):
    """(airing index, child index, offset s) to tune at now, or None outside the tape. Under
    `snap` s into a programme: from its start."""
    r = tape.lookup(now) if tape is not None else None
    if r is None:
        return None
    i, c, off = r
    return i, c, 0 if off < snap else int(off)


def step(order, cur, d):
    """The channel d places from cur in `order` (number order), with wrap. Up (+1) is the
    higher number, as CH+ on a TV remote."""
    if not order:
        return None
    if cur not in order:
        return order[0]
    return order[(order.index(cur) + d) % len(order)]


def remember(recent, cid):
    """The last two channels, newest first, after a tune of cid."""
    return ([cid] + [x for x in recent if x != cid])[:2]


def last_channel(recent, cur, order):
    """The channel before cur (LB), when it is still in order; else None."""
    for x in recent:
        if x != cur and x in order:
            return x
    return None


def num(n):
    """A channel number as the guide shows it: "02" below 10, else the plain number."""
    return "%02d" % n


# ---------------------------------------------------------------- pools

class Kept:
    """The complete indexes of the last scan, for the freeze that follows a save (it then
    reads none of them again). Keyed by (server, section); an index serves only the same
    provider, at the same section stamp, kind and title, for INDEX_KEEP_S. A freeze drops the
    ones it used; Lineup.tick drops old ones; a new account or profile drops all (memory:
    about 25 MB for 50 000 rows)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock, self.m = threading.Lock(), {}

    def put(self, server, section, prov, stamp, ix):
        if stamp is None or not themes.index_complete(ix):
            return                                # never an incomplete index for a pool
        with self._lock:
            self.m[(server or "", section)] = (self.clock(), prov, stamp, ix)

    def get(self, k, prov, stamp, kind, title):
        with self._lock:
            e = self.m.get(k)
        if e is None:
            return None
        t, p, st, ix = e
        if self.clock() - t > INDEX_KEEP_S or p is not prov or stamp is None or st != stamp \
                or ix.get("kind") != kind or ix.get("title") != title:
            return None
        return ix

    def drop(self, keys):
        with self._lock:
            for k in keys:
                self.m.pop(k, None)

    def expire(self):
        if not self.m:                            # each frame: no lock when empty
            return
        with self._lock:
            now = self.clock()
            for k in [k for k, e in self.m.items() if now - e[0] > INDEX_KEEP_S]:
                del self.m[k]

    def clear(self):
        with self._lock:
            self.m.clear()


KEPT = Kept()


class Store:
    """The entries of each query with the section stamp they were fetched at, one file each,
    and the keys of the entries the profile has watched (an older file has none)."""

    def __init__(self, root):
        self.root = root

    def _path(self, key):
        return os.path.join(self.root, "q-%s.json" % hashlib.sha256(key.encode("utf-8")).hexdigest()[:16])

    def get(self, key):
        try:
            with open(self._path(key)) as f:
                d = json.load(f)
            seen = d.get("seen")
            return (d["stamp"], [schedule.Entry(*e) for e in d["entries"]],
                    [str(k) for k in seen] if isinstance(seen, list) else None)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, key, stamp, entries, seen=None):
        if stamp is None:
            return                                   # no stamp: it is fetched each time
        doc = {"stamp": stamp, "entries": [list(e) for e in entries]}
        if seen is not None:
            doc["seen"] = list(seen)
        _write_json(self._path(key), doc)


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        config.atomic_write(path, json.dumps(doc, separators=(",", ":")))
    except OSError as e:
        L.info("lineup cache not written: %s", e.strerror or e.__class__.__name__)


class Fetcher:
    """fetch(query, deadline) of channels.Query, for the fetch threads of one freeze. reasons: {query key: OFFLINE |
    MISSING} for the queries that failed. seen: {query key: keys of the watched entries} (the
    start rule)."""

    def __init__(self, sources, store, kept=None):
        self.sources, self.store, self.kept = sources, store, kept
        self.reasons, self.secs, self.requests, self.reused = {}, {}, 0, 0
        self.seen, self.indexes, self.leaf_memo, self.classified = {}, {}, {}, {}
        self.kept_used = []                  # (server, section) of the scan indexes used
        self._lock, self._locks = threading.Lock(), {}

    # -- several fetch threads: counters under a lock, each memo filled once
    def _count(self, n=1, reused=False):
        with self._lock:
            if reused:
                self.reused += n
            else:
                self.requests += n

    def _key_lock(self, k):
        with self._lock:
            return self._locks.setdefault(k, threading.Lock())

    def _prov(self, server):
        p = self.sources.strict(server or None) if self.sources is not None else None
        return p if p is not None and hasattr(p, "listing") else None

    def _sections(self, prov):
        """{section id: section dict} of a server, read once for each worker (the stamps)."""
        sid = id(prov)
        with self._key_lock(("secs", sid)):
            if sid not in self.secs:
                try:
                    self._count()
                    self.secs[sid] = {s["id"]: s for s in prov.load_sections()}
                except Exception as e:
                    L.info("sections for a channel source failed: %s", e)
                    self.secs[sid] = None
        return self.secs[sid]

    @staticmethod
    def _stamp(q, secs):
        if q.kind == "playlist":
            return None
        if q.section is not None:
            s = secs.get(q.section)
            return str(s.get("stamp")) if s and s.get("stamp") else None
        st = [str(s.get("stamp") or "") for _k, s in sorted(secs.items())
              if s.get("type") in ("show", "movie")]
        return ",".join(st) if st and all(st) else None

    def fetch(self, q, deadline):
        from .plex import PlexError
        if q.kind == "theme":
            try:
                return self._theme(q, deadline)
            except PlexError as e:
                self.reasons[q.key] = MISSING if getattr(e, "code", None) == 404 else OFFLINE
                return None
        prov = self._prov(q.server)
        secs = self._sections(prov) if prov is not None else None
        if secs is None:
            self.reasons[q.key] = OFFLINE
            return None
        if q.section is not None and q.section not in secs:
            self.reasons[q.key] = MISSING
            return None
        stamp = self._stamp(q, secs)
        prev = self.store.get(q.key) if stamp else None
        if prev is not None and prev[0] == stamp:
            self._count(reused=True)
            if prev[2] is not None:
                self.seen[q.key] = prev[2]
            return prev[1]
        try:
            items = self._items(prov, q, secs, deadline)
        except PlexError as e:
            self.reasons[q.key] = MISSING if getattr(e, "code", None) == 404 else OFFLINE
            return None
        if items is None:
            self.reasons[q.key] = MISSING
            return None
        srv = getattr(prov, "server_id", "") or ""
        out, seen = [], []
        for i in items:
            e = schedule.entry(i, srv)
            if e is not None:
                out.append(e)
                if i.watched:
                    seen.append(e.key)
        self.seen[q.key] = seen
        self.store.put(q.key, stamp, out, seen)
        return out

    # -- theme sources
    def _index(self, prov, server, section, sec, deadline):
        """The light index of one section, read once for each freeze: the complete index of a
        scan just before (kept) when the section did not change since, else from the server."""
        k = (server, section)
        with self._key_lock(("ix",) + k):
            if k not in self.indexes:
                kind, title = sec.get("type") or "movie", sec.get("title") or ""
                ix = self.kept.get(k, prov, sec.get("stamp"), kind, title) \
                    if self.kept is not None else None
                if ix is not None:
                    self.kept_used.append(k)
                else:
                    ix = index(prov, section, kind, deadline, title)
                    self._count(ix["requests"])
                self.classified[k] = themes.classify_index(ix) if themes.index_complete(ix) else None
                self.indexes[k] = ix
        return self.indexes[k], self.classified[k]

    def _show_leaves(self, prov, server, sid, deadline):
        """[(Entry, watched)] of one show, read once for each freeze (one thread reads it, the
        others wait for it)."""
        k = (server, sid)
        with self._key_lock(("leaves",) + k):
            return self._show_leaves_once(prov, k, sid, deadline)

    def _show_leaves_once(self, prov, k, sid, deadline):
        from .plex import PlexError
        if k not in self.leaf_memo:
            srv = getattr(prov, "server_id", "") or ""
            out = []
            try:
                items = self._list(prov, "/library/metadata/%s/allLeaves" % sid, None, 2000, deadline)
            except PlexError as e:
                if getattr(e, "code", None) != 404:
                    raise
                L.info("a theme show is gone (404): passed over")
                items = []
            for i in items:
                e = schedule.entry(i, srv)
                if e is not None:
                    out.append((e, i.watched))
            self.leaf_memo[k] = out
        return self.leaf_memo[k]

    def _theme(self, q, deadline):
        """The items of a theme over its sections. A server that does not answer: OFFLINE; a
        section that is gone is passed over (MISSING when none is left). A result is stored
        only from complete indexes of every section."""
        if q.theme not in themes.BY_ID:             # a theme of a newer build
            self.reasons[q.key] = MISSING
            return None
        parts = []
        for server, section in q.sections:
            prov = self._prov(server)
            secs = self._sections(prov) if prov is not None else None
            if secs is None:
                self.reasons[q.key] = OFFLINE
                return None
            if section in secs:
                parts.append((server, section, prov, secs[section]))
        if not parts:
            self.reasons[q.key] = MISSING
            return None
        st = ["%s/%s=%s" % (s, n, sec.get("stamp") or "") for s, n, _p, sec in parts]
        stamp = ",".join(st) if all(sec.get("stamp") for _s, _n, _p, sec in parts) else None
        prev = self.store.get(q.key) if stamp else None
        if prev is not None and prev[0] == stamp:
            self._count(reused=True)
            if prev[2] is not None:
                self.seen[q.key] = prev[2]
            return prev[1]
        rows = []
        for server, section, prov, sec in parts:
            ix, cl = self._index(prov, server, section, sec, deadline)
            got = themes.members(ix, q.theme, q.media, cl)
            if got is None:                       # an incomplete index: proposals only
                self.reasons[q.key] = OFFLINE
                return None
            rows += [(server, prov, r) for r in got]
        out, seen = [], []
        shows = sorted((x for x in rows if x[2].type == "show"),
                       key=lambda x: (schedule.rand(q.theme, x[0], x[2].id), x[2].id))
        for server, prov, r in shows[:q.show_cap]:
            if len(out) >= q.read_cap:
                break
            for e, w in self._show_leaves(prov, server, r.id, deadline):
                out.append(e)
                if w:
                    seen.append(e.key)
        for server, prov, r in rows:
            if r.type == "movie" and len(out) < q.read_cap:
                e = schedule.entry(row_item(r, getattr(prov, "server_id", "") or ""))
                if e is not None:
                    out.append(e)
        self.seen[q.key] = seen
        self.store.put(q.key, stamp, out, seen)
        return out

    def _list(self, prov, path, params, cap, deadline):
        self._count()
        return prov.listing(path, params, cap=cap, deadline=deadline)

    def _leaves(self, prov, items, cap, deadline):
        """Movies and episodes as they are; a show becomes its episodes in order."""
        out = []
        for it in items:
            if len(out) >= cap:
                break
            if it.type == "show":
                out += self._list(prov, "/library/metadata/%s/allLeaves" % it.id, None,
                                  cap - len(out), deadline)
            elif it.type in schedule.TYPES:
                out.append(it)
        return out

    def _filter_key(self, prov, section, field, value):
        self._count()
        mc = prov.get("/library/sections/%d/%s" % (section, field))
        for d in mc.get("Directory") or []:
            if (d.get("title") or "").casefold() == value.casefold():
                return str(d.get("key"))
        return None

    def _items(self, prov, q, secs, deadline):
        cap = q.read_cap
        if q.kind == "show":
            sid = q.id or self._find_show(prov, q, secs, deadline)
            return None if not sid else self._list(prov, "/library/metadata/%s/allLeaves" % sid,
                                                   None, cap, deadline)
        if q.kind in ("library", "genre", "network"):
            sec = secs[q.section]
            show = sec.get("type") == "show"
            path = "/library/sections/%d/all" % q.section
            if q.kind == "library":
                return self._list(prov, path, {"type": 4 if show else 1}, cap, deadline)
            key = None
            for field in ((q.kind,) if q.kind == "genre" else ("network", "studio")):
                key = self._filter_key(prov, q.section, field, q.value)
                if key is not None:
                    break
            if key is None:
                return None
            if not show:
                return self._list(prov, path, {"type": 1, field: key}, cap, deadline)
            shows = self._list(prov, path, {"type": 2, field: key, "sort": "titleSort"},
                               q.show_cap, deadline)
            return self._leaves(prov, shows, cap, deadline)
        if q.kind == "collection":
            cid = q.id or self._find(prov, "/library/sections/%d/collections" % q.section
                                     if q.section else None, q.title, deadline)
            if not cid:
                return None
            return self._leaves(prov, self._list(prov, "/library/collections/%s/children" % cid,
                                                 None, cap, deadline), cap, deadline)
        if q.kind == "playlist":
            pid = q.id
            if not pid:
                pls = prov.playlists("video")
                hit = [p for p in pls if p.title.casefold() == q.title.casefold()]
                pid = hit[0].id if len(hit) == 1 else None
            if not pid:
                return None
            return self._leaves(prov, self._list(prov, "/playlists/%s/items" % pid, None, cap,
                                                 deadline), cap, deadline)
        return None

    def _find(self, prov, path, title, deadline):
        if not path:
            return None
        hit = [i for i in self._list(prov, path, None, 2000, deadline)
               if i.title.casefold() == title.casefold()]
        return hit[0].id if len(hit) == 1 else None

    def _find_show(self, prov, q, secs, deadline):
        hit = []
        for sid, s in secs.items():
            if s.get("type") == "show":
                hit += [i for i in self._list(prov, "/library/sections/%d/all" % sid,
                                              {"type": 2, "title": q.title}, 50, deadline)
                        if i.title.casefold() == q.title.casefold()]
        return hit[0].id if len(hit) == 1 else None


# ---------------------------------------------------------------- the lineup

def _read_json(path):
    return config.load_dict(path)


class Lineup:
    """The channels of the box as this profile sees them. UI thread: tick(), reset(),
    refresh() and the reads; the freeze runs on one worker and posts its result."""

    def __init__(self, post, clock=time.time, zone=None):
        self.post, self.clock = post, clock
        self.zone = zone                     # a tzinfo for tests; else the box zone, once
        self.key = self.sources = None
        self.gen = 0
        self.rules = channels.Lineup([], 0, None, None, None, False)
        self.caches, self.tapes, self.status = {}, {}, {}
        self.busy = self.again = False
        self.due = None
        self.loaded = False                  # the first freeze of this key arrived
        self.version = 0                     # moves with each result: screens draw again

    # -- control (UI thread)
    def reset(self, key, sources):
        """A new account, profile or provider. key None: no Live TV (no server signed in)."""
        self.gen += 1
        self.key, self.sources = key, sources
        KEPT.clear()                         # the scan indexes of the last profile
        self.caches, self.tapes, self.status = {}, {}, {}
        self.busy = self.again = self.loaded = False
        self.rules = channels.load() if key else channels.Lineup([], 0, None, None, None, False)
        self.due = self.clock() + FIRST_DELAY_S if key else None
        self.version += 1

    def refresh(self):
        """Run the freeze worker now (a wizard save, a hand edit, a server that came back)."""
        if self.key is None:
            return
        if self.busy:
            self.again = True
        else:
            self.due = self.clock()

    def check_file(self):
        """The guide opens: a hand edit of channels.json shows now."""
        if self.key is not None and channels.load() is not self.rules:
            self.rules = channels.load()
            self.refresh()

    def server_added(self):
        if any(v == OFFLINE for v in self.status.values()):
            self.refresh()

    def tick(self):
        KEPT.expire()
        if self.key is None or self.busy or self.due is None or self.clock() < self.due:
            return
        self.due = None
        self.rules = channels.load()
        if not self.rules.channels:
            self.status, self.tapes, self.loaded = {}, {}, True
            self.version += 1
            tz = self._tz()
            self.due = next_freeze(self.clock(), tz)
            return
        self.busy = True
        args = (self.gen, self.rules, self.key, self.sources, dict(self.caches))
        threading.Thread(target=self._work, args=args, daemon=True).start()

    def _tz(self):
        if self.zone is None:
            self.zone = box_zone()
        return self.zone

    # -- worker
    def _work(self, gen, rules, key, sources, mem):
        def publish(part):
            self.post(lambda: self._partial(gen, part))
        try:
            res = self.build(rules, key, sources, mem, publish)
        except Exception:
            L.exception("lineup freeze failed")
            res = None
        self.post(lambda: self._done(gen, res))

    def cache_dir(self, key):
        return os.path.join(config.data_dir(), "channels-cache", key)

    @staticmethod
    def _failed(root):
        """The query keys that failed in the last freeze (FAILED_FILE)."""
        f = (_read_json(os.path.join(root, FAILED_FILE)) or {}).get("failed")
        return {k for k in f if isinstance(k, str)} if isinstance(f, list) else set()

    def build(self, rules, key, sources, mem=None, publish=None, kept=KEPT):
        """The freeze of every channel (worker). Returns {"caches", "tapes", "status",
        "retry"}; writes the changed caches. publish(part): called with the channels whose
        own queries are done, as soon as they are ({"rules", "status", "caches", "tapes"} of
        those channels only; first the ones that fetch nothing). kept: the indexes of the
        last scan (Kept)."""
        t0 = time.monotonic()
        if rules.needs_ids and not rules.error:
            try:
                channels.ensure_ids(rules.stamp)
                rules = channels.load()
            except channels.Refused as e:
                L.info("channel ids not written: %s", e)
        tz = self._tz()
        now = int(self.clock())
        root = self.cache_dir(key)
        chans = [c for c in rules.channels if c.persistent and not c.off]
        status = {c.id: FILE if not c.persistent else OFF for c in rules.channels
                  if not c.persistent or c.off}       # an Off channel is not frozen
        caches = {}
        for c in chans:
            caches[c.id] = (mem or {}).get(c.id) or _read_json(os.path.join(root, c.id + ".json"))
        plans, need = {}, []
        for c in chans:
            try:
                plans[c.id] = c.plan()
                if needs_pools(caches[c.id], plans[c.id], now, tz):
                    need.append(c)
            except Exception:                    # one bad channel: the others still freeze
                L.exception("channel %s: no plan", c.id)
                plans.pop(c.id, None)
                status[c.id] = EMPTY
        L.info("lineup: freeze start, %d channels need pools", len(need))
        need_ids = {c.id for c in need}
        fetcher = Fetcher(sources, Store(root), kept)
        qs = channels.plan_queries(need) if need else []
        keys = {c.id: [channels.query_for(src).key for _s, src in c.slots()]
                for c in chans if c.id in plans}
        fetched = {q.key for q in qs}
        # Queries not done yet; a channel with a valid cache waits for none.
        wait = {cid: (set(ks) & fetched) if cid in need_ids else set() for cid, ks in keys.items()}
        results, out, tapes, sent, flags = {}, {}, {}, set(), {"retry": False}

        def freeze_one(c):
            old = caches[c.id]
            pools = channels.channel_pools(c, results) if c.id in need_ids else None
            seen = {s.key: fetcher.seen.get(channels.query_for(src).key)
                    for s, src in c.slots()} if pools is not None else None
            try:
                new = schedule.freeze(plans[c.id], pools, now, tz, old, seen)
                tape = schedule.Tape.of(new) if new is not None else None
            except Exception:
                L.exception("channel %s: freeze failed; a new start", c.id)
                old = None                       # as if it had no cache
                try:
                    new = schedule.freeze(plans[c.id], pools, now, tz, None, seen)
                    tape = schedule.Tape.of(new) if new is not None else None
                except Exception:
                    L.exception("channel %s: no schedule", c.id)
                    status[c.id] = EMPTY
                    return
            why = [fetcher.reasons.get(k) for k in keys[c.id]]
            if MISSING in why:
                st = MISSING
            elif new is None:
                st = OFFLINE if OFFLINE in why else EMPTY
            else:
                st = OFFLINE if OFFLINE in why else ON
            flags["retry"] = flags["retry"] or OFFLINE in why
            status[c.id] = st
            if new is not None:
                out[c.id] = new
                tapes[c.id] = tape
                if new is not old and schedule.clock_ok(now):
                    _write_json(os.path.join(root, c.id + ".json"), new)

        def freeze_ready(first=False):
            """Freeze the channels whose queries are all done (number order) and publish
            them; the first call also publishes the statuses known before any fetch."""
            part = [c for c in chans if c.id in wait and c.id not in sent and not wait[c.id]]
            for c in part:
                sent.add(c.id)
                freeze_one(c)
            ids = {c.id for c in part} | (set(status) if first else set())
            if publish is None or not ids:
                return
            publish({"rules": rules, "status": {k: status[k] for k in ids if k in status},
                     "caches": {k: out[k] for k in ids if k in out},
                     "tapes": {k: tapes[k] for k in ids if k in tapes}})

        def done(q, r, skip):
            if r is not None:
                results[q.key] = list(r)
            elif skip:                           # not fetched before the deadline: try again
                fetcher.reasons.setdefault(q.key, OFFLINE)   # soon; stored results are reused
            for ws in wait.values():
                ws.discard(q.key)
            freeze_ready()

        freeze_ready(first=True)
        failed = []
        if need:
            last = self._failed(root)            # failed last time: last, so the others still
            top = {}                             # make progress; then the lowest channel
            for c in sorted(need, key=lambda c: c.number):   # numbers first (the guide top)
                for k in keys.get(c.id, ()):
                    top.setdefault(k, c.number)
            qs.sort(key=lambda q: (q.key in last, top.get(q.key, 0)))
            _results, failed = channels.collect(qs, fetcher.fetch,
                                                deadline=time.monotonic() + DEADLINE_S,
                                                workers=FETCH_WORKERS, done=done)
            if set(failed) != last:
                _write_json(os.path.join(root, FAILED_FILE), {"failed": sorted(failed)})
        freeze_ready()                           # none left; a guard against a lost answer
        if kept is not None and fetcher.kept_used:
            kept.drop(fetcher.kept_used)         # used: the memory goes
        L.info("lineup: %d channels, %d need pools, %d queries failed, %d requests, %d reused,"
               " %d scan indexes, %.0f ms", len(chans), len(need), len(failed), fetcher.requests,
               fetcher.reused, len(fetcher.kept_used), (time.monotonic() - t0) * 1000)
        return {"caches": out, "tapes": tapes, "status": status, "retry": flags["retry"],
                "rules": rules, "now": now}

    def _partial(self, gen, part):
        """Some channels are frozen (UI thread): they show now; the others stay "Loading…"
        (or as they were) until their own part or the end result."""
        if gen != self.gen or not self.busy:
            return                               # another profile, or the end result is in
        if self.again:
            return                               # saved meanwhile: this build has old rules
        self.rules = part["rules"]
        for cid, st in part["status"].items():
            self.status[cid] = st
            if cid in part["tapes"]:
                self.tapes[cid], self.caches[cid] = part["tapes"][cid], part["caches"][cid]
            else:
                self.tapes.pop(cid, None)
                self.caches.pop(cid, None)
        self.version += 1

    def _done(self, gen, res):
        if gen != self.gen:
            return                               # another profile or provider meanwhile
        self.busy = False
        tz = self._tz()
        now = self.clock()
        if res is None:
            self.due = now + RETRY_S
        else:
            if not self.again or self.rules is None:
                self.rules = res["rules"]        # saved meanwhile: keep the new rules
            self.caches, self.tapes, self.status = res["caches"], res["tapes"], res["status"]
            self.loaded = True
            self.due = now + RETRY_S if res["retry"] else next_freeze(now, tz)
        if self.again:
            self.again, self.due = False, now
        self.version += 1

    # -- reads (UI thread, memory only)
    def visible(self):
        """Channels with a tape that this profile may see, in number order (the guide, Up and
        Down, the panel row). A channel whose server is offline stays, dim."""
        return [c for c in self.rules.channels if not c.off       # Off shows at once
                and self.status.get(c.id) in (ON, OFFLINE) and c.id in self.tapes]

    def pending(self):
        """Channels with no result yet, also ones a save added since the last freeze (the
        guide shows "Loading…" for them)."""
        return [c for c in self.rules.channels if c.id not in self.status
                or (not c.off and self.status.get(c.id) == OFF)]   # just turned On

    def channel(self, cid):
        return next((c for c in self.rules.channels if c.id == cid), None)

    def tape(self, cid):
        return self.tapes.get(cid)

    def count(self):
        return len(self.rules.channels)

    def on_now(self, now):
        """What is on now (Home "On Right Now"): one dict for each visible channel with an
        airing at now, in number order. Keys: cid, number, name, entry (the programme), start,
        end, frac (0..1), left (s), offline. Memory only."""
        out = []
        for c in self.visible():
            tape = self.tapes.get(c.id)
            i = tape.at(now) if tape is not None else None
            if i is None:
                continue
            a = tape.rows[i]
            dur = a.end - a.start
            out.append({"cid": c.id, "number": c.number, "name": c.name,
                        "entry": a.unit.children[0], "start": a.start, "end": a.end,
                        "frac": max(0.0, min(1.0, (now - a.start) / dur)) if dur else 0.0,
                        "left": max(0, a.end - now),
                        "offline": self.status.get(c.id) == OFFLINE})
        return out


# ---------------------------------------------------------------- Make Channels for Me

INDEX_PAGE = 500             # rows for each list request of the index
ROW_CAP = 50000              # rows of one section in the index; more: not complete
LIGHT = {"excludeElements": "Media,Role,Director,Writer,Country",
         "excludeFields": "summary,tagline"}     # halves the list page bytes (box check)


def _stamps(prov, memo, lock):
    """{section id: stamp} of a server, read once for each scan, before its indexes (an index
    is at least as new as its stamp). {} when the read fails: nothing is kept then."""
    with lock:
        k = id(prov)
        if k not in memo:
            try:
                memo[k] = {_int(d.get("key")): d.get("contentChangedAt") or d.get("updatedAt")
                           for d in prov.get("/library/sections", timeout=8).get("Directory") or []}
            except Exception as e:
                L.info("section stamps for the scan not read: %s", e.__class__.__name__)
                memo[k] = {}
        return memo[k]


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _float(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _row(m):
    """A themes.Row from one list item; repeated strings are shared (memory)."""
    return themes.Row(str(m.get("ratingKey") or ""), m.get("type") or "", m.get("title") or "",
                      _int(m.get("year")) or None, sys.intern(m.get("contentRating") or ""),
                      tuple(sys.intern(g.get("tag") or "") for g in (m.get("Genre") or [])[:2]),
                      _int(m.get("duration")), _int(m.get("leafCount")),
                      _int(m.get("viewedLeafCount")), _int(m.get("viewCount")),
                      _int(m.get("lastViewedAt")), sys.intern(m.get("studio") or ""),
                      _float(m.get("audienceRating")))


def _pages(prov, path, params, cap, deadline, conv, page=INDEX_PAGE, count=None, pause=0.02,
           early=True):
    """(items through conv, complete). complete is False when the deadline passed or the cap
    was reached first. With totalSize the list ends at it (a short page from a server or
    proxy that caps the size is not the end); with none it ends at an empty page, the next
    pages asked at the size the server sent. early: an empty page right after a page shorter
    than the largest one sent is the end too (a totalSize above the rows a managed user may
    see). An id set passes early=False: a set cut short would put cartoons on content
    channels, so it is complete only at totalSize (or empty with none). count: a list whose
    [0] counts the requests. A short pause between pages lets the UI thread run (as
    plex.listing)."""
    out, start, short, seen = [], 0, False, 0
    while True:
        if len(out) >= cap or (deadline is not None and time.monotonic() > deadline):
            return out[:cap], False
        if start:
            time.sleep(pause)
        if count is not None:
            count[0] += 1
        mc = prov.get(path, params, start=start, size=page, timeout=8)
        md = mc.get("Metadata") or []
        out += [conv(m) for m in md]
        start += len(md)
        total = _int(mc.get("totalSize"))
        if total:
            if start >= total:
                return out[:cap], len(out) <= cap
            if not md:
                if early and short:               # the rows end before totalSize
                    L.info("list ends under its totalSize: %s", path)
                    return out[:cap], len(out) <= cap
                return out[:cap], False
            seen = max(seen, len(md))
            short = len(md) < seen
        elif not md:
            return out[:cap], (early or start == 0) and len(out) <= cap
        else:
            page = min(page, len(md))


def index(prov, section, kind, deadline, title=""):
    """The light index of one section (worker): rows (themes.Row) of its shows or movies, and
    for each format genre of its genre directory the id set of one OR query (genre=K1,K2);
    the action sets too for a section with animation or anime. Each part records whether it
    is complete; a failed genre directory read leaves dir_ok False."""
    path = "/library/sections/%d/all" % section
    t = 2 if kind == "show" else 1
    count = [0]
    ix = {"server": getattr(prov, "server_id", "") or "", "section": section, "kind": kind,
          "title": title, "rows": [], "rows_complete": False, "genres": [], "dir_ok": False,
          "sets": {}, "action": None, "requests": 0}
    try:                              # the small parts first: a deadline cuts only the rows
        count[0] += 1
        gs = prov.get("/library/sections/%d/genre" % section, timeout=8).get("Directory") or []
        ix["dir_ok"] = True
    except Exception as e:
        L.info("library %s genres not read: %s", section, e.__class__.__name__)
        gs = []
    ix["genres"] = [(str(g["key"]), g.get("title") or "") for g in gs if g.get("key") is not None]
    fk, ak = themes.format_keys(ix["genres"])

    def ids(keys):
        try:
            got, ok = _pages(prov, path, dict(LIGHT, type=t, genre=",".join(keys)), ROW_CAP,
                             deadline, lambda m: str(m.get("ratingKey") or ""), count=count,
                             early=False)
            ok = ok and len(set(got)) == len(got)   # a server that ignores start repeats rows
            if not ok:
                L.info("library %s genre set incomplete: its themes are left out", section)
            return {"ids": frozenset(got), "complete": ok}
        except Exception as e:
            L.info("library %s genre set not read: %s", section, e.__class__.__name__)
            return {"ids": frozenset(), "complete": False}
    if ix["dir_ok"]:
        for f in sorted(fk):
            ix["sets"][f] = ids(fk[f])
        if themes.needs_action(fk, title):
            ix["action"] = ids(ak) if ak else {"ids": frozenset(), "complete": True}
    try:
        ix["rows"], ix["rows_complete"] = _pages(prov, path, dict(LIGHT, type=t), ROW_CAP,
                                                 deadline, _row, count=count)
    except Exception as e:
        L.info("library %s index not read: %s", section, e.__class__.__name__)
    ix["requests"] = count[0]
    return ix


def summary_of(ix, server=""):
    """One library as channels.propose() reads it, from its index (no count requests).
    Genre counts come from the 2 tags of the list rows."""
    show = ix["kind"] == "show"
    d = {"server": server, "section": ix["section"], "title": ix["title"], "type": ix["kind"],
         "episodes": 0, "movies": 0, "minutes": 0, "shows": [], "genres": [], "collections": []}
    rows = [r for r in ix["rows"] if r.type == ("show" if show else "movie")]
    counts = {}
    for r in rows:
        m = themes.row_minutes(r)
        d["minutes"] += m
        if show:
            d["shows"].append({"id": r.id, "title": r.title, "episodes": r.leaves,
                               "network": r.studio, "minutes": m})
            d["episodes"] += r.leaves
        for g in r.genres:
            c = counts.setdefault(g.casefold(), [0, 0])
            c[0] += 1
            c[1] += m
    d["movies"] = 0 if show else len(rows)
    for _key, title in ix["genres"]:
        c = counts.get(title.casefold())
        if c:
            d["genres"].append({"genre": title, "count": c[0], "minutes": c[1]})
    return d


def library_scan(prov, lib, deadline, stamps=None, kept=None):
    """(summary, index) of one pinned video library (worker). stamps(prov): the section stamps
    read before the index; with them a complete index goes to kept for the next freeze."""
    sec = int(lib.section_id)
    kind = lib.extra.get("section_type") or "movie"
    stamp = stamps(prov).get(sec) if stamps is not None else None
    ix = index(prov, sec, kind, deadline, lib.title or "")
    ix["server"] = lib.server_id or ""
    if kept is not None:
        kept.put(lib.server_id, sec, prov, stamp, ix)
    d = summary_of(ix, lib.server_id or "")
    n = len(d["shows"]) if kind == "show" else d["movies"]
    per_item = d["minutes"] / n if n else channels.MOVIE_MINUTES
    if time.monotonic() < deadline:
        try:
            cols, _ok = _pages(prov, "/library/sections/%d/collections" % sec, None, 500,
                               deadline, dict, page=200)
            for m in cols:
                c = _int(m.get("childCount"))
                d["collections"].append({"id": str(m.get("ratingKey")), "title": m.get("title") or "",
                                         "count": c, "minutes": int(c * per_item)})
        except Exception as e:
            L.info("library %s collections not read: %r", sec, e)
    return d, ix


def _daemon_pool(n):
    """submit(fn, *a) -> Future, at most n at once, on daemon threads: a scan left behind
    (its deadline passed, the app quits) never holds up the exit, unlike ThreadPoolExecutor."""
    gate = threading.Semaphore(n)

    def submit(fn, *a):
        f = Future()

        def run():
            with gate:
                if not f.set_running_or_notify_cancel():
                    return
                try:
                    f.set_result(fn(*a))
                except BaseException as e:
                    f.set_exception(e)
        threading.Thread(target=run, daemon=True).start()
        return f
    return submit


def scan(sources, libs, deadline_s=SCAN_S, kept=KEPT):
    """(libraries, playlists, analysis) for channels.propose() and themes.curate() (worker).
    libs: the pinned libraries in pin order (Sources.pinned, read on the UI thread); every
    connected server's video playlists. What has not arrived by the deadline is left out; a
    library whose index is not complete gives no themes. Only the complete indexes are kept,
    in `kept`, for the freeze after a save (Kept: dropped after use or INDEX_KEEP_S)."""
    deadline = time.monotonic() + deadline_s
    memo, lock = {}, threading.Lock()

    def stamps(p):
        return _stamps(p, memo, lock)
    libs = [l for l in libs if not l.extra.get("down")
            and l.extra.get("section_type") in ("show", "movie")]
    submit = _daemon_pool(4)
    jobs = []
    for l in libs:
        p = sources.strict(l.server_id)
        if p is not None and hasattr(p, "listing"):
            jobs.append(submit(library_scan, p, l, deadline, stamps, kept))
    servers = {id(p): p for p in sources.providers.values() if hasattr(p, "listing")}
    pjobs = [submit(lambda p=p: p.playlists("video")) for p in servers.values()]
    out, pls, idx = [], [], []
    for j in jobs:
        try:
            d, ix = j.result(timeout=max(0.1, deadline - time.monotonic() + 2))
            out.append(d)
            idx.append(ix)
        except Exception as e:
            L.info("library summary failed: %s", e.__class__.__name__)
    for j in pjobs:
        try:
            for p in j.result(timeout=max(0.1, deadline - time.monotonic() + 2)):
                pls.append({"server": p.server_id or "", "id": p.id, "title": p.title,
                            "minutes": int(p.duration_ms / 60000), "video": True})
        except Exception as e:
            L.info("playlist summary failed: %s", e.__class__.__name__)
    t0 = time.monotonic()
    analysis = themes.analyse(idx)
    L.info("scan: %d libraries, %d complete for themes, %d items, %.0f ms analysis", len(idx),
           sum(1 for ix in idx if themes.index_complete(ix)), len(analysis.mins),
           (time.monotonic() - t0) * 1000)
    return out, pls, analysis


def row_item(row, server):
    """A model.Item for schedule.entry() from an index row (a theme movie): its exact
    duration, its own section's server."""
    from .model import Item
    return Item(id=row.id, type=row.type, title=row.title, year=row.year,
                duration_ms=row.dur, content_rating=row.rating, server_id=server or None)


# ---------------------------------------------------------------- the channel session

BEHIND_S = 60                 # end of an item: a session this far behind the tape (no pause)
                              # tunes the clock point


class Tune:
    """One tune of a channel: the tape child to play from `at` s. a_start: its airing start
    (an index can move when a freeze trims the tape; a start cannot)."""
    __slots__ = ("cid", "number", "name", "a_start", "c", "at", "entry", "start", "end", "item")

    def __init__(self, ch, airing, c, at):
        self.cid, self.number, self.name = ch.id, ch.number, ch.name
        self.a_start, self.c, self.at = airing.start, c, int(at)
        self.entry = airing.unit.children[c]
        self.start = airing.start + airing.unit.offsets[c]
        self.end = self.start + self.entry.dur
        self.item = entry_item(self.entry)


def entry_item(e):
    """A model.Item for play_info from a tape entry."""
    from .model import Item
    it = Item(id=e.id, type=e.type, title=e.title, year=e.year, duration_ms=int(e.dur) * 1000,
              content_rating=e.rating or "", server_id=e.server or None, show_title=e.show or "",
              season_index=e.season, index=e.index)
    if e.type == "episode" and "/" in e.group:
        it.show_id = e.group.split("/", 1)[1]
    return it


def prog_title(e):
    return (e.show or e.title) if e.type == "episode" else e.title


def prog_meta(e, sep=" • "):
    """"S4 • E12 • The Contest" for an episode, "1994 • PG" for a movie."""
    if e.type == "episode":
        parts = (["S%s" % e.season] if e.season is not None else []) + \
            (["E%s" % e.index] if e.index is not None else [])
        if e.show and e.title:
            parts.append(e.title)
        return sep.join(parts)
    return sep.join(str(x) for x in (e.year, e.rating) if x)


def hhmm(t, tz):
    from datetime import datetime
    return datetime.fromtimestamp(t, tz).strftime("%H:%M")


def mins_left(s):
    m = max(1, int((s + 59) // 60))
    return "%d min left" % m if m < 60 else "%d h %02d min left" % (m // 60, m % 60)


class Surfer:
    """What a channel session asks of the lineup (playback.py holds one while it plays)."""

    def __init__(self, lineup, sources, settings, clock=time.time):
        self.lineup, self.sources, self.settings, self.clock = lineup, sources, settings, clock

    def order(self):
        return [c.id for c in self.lineup.visible()]

    def step(self, cur, d):
        """Up/Down pass a channel whose server does not answer."""
        order = self.order()
        up = [c for c in order if c == cur or self.lineup.status.get(c) != OFFLINE]
        return step(up if len(up) > 1 else order, cur, d)

    def last(self, cur):
        return last_channel(self.settings.live_recent(), cur, self.order())

    def note(self, cid):
        self.settings.set_live_recent(remember(self.settings.live_recent(), cid))
        try:
            self.settings.save()
        except OSError:
            pass

    def tune(self, cid, now=None):
        """The Tune of channel cid at now (start snap), or None."""
        ch, tape = self.lineup.channel(cid), self.lineup.tape(cid)
        tp = tune_point(tape, self.clock() if now is None else now)
        if ch is None or tp is None:
            return None
        i, c, off = tp
        return Tune(ch, tape.rows[i], c, off)

    def next_tune(self, t, bad=(), now=None, behind=True):
        """The tune after t (the next child or airing at 0, its bad entries passed over); with
        behind, a session over BEHIND_S behind the tape tunes the clock point instead."""
        import bisect
        ch, tape = self.lineup.channel(t.cid), self.lineup.tape(t.cid)
        if ch is None or tape is None:
            return None
        now = self.clock() if now is None else now
        i = bisect.bisect_left(tape.starts, t.a_start)
        if i >= len(tape.rows) or tape.rows[i].start != t.a_start:
            return self.tune(t.cid, now)
        nx = tape.next_playable(i, t.c + 1, set(bad))
        if nx is None:
            return None
        i, c = nx
        if behind and now - tape.child_start(i, c) > BEHIND_S:
            tp = tune_point(tape, now)
            if tp is not None and tape.rows[tp[0]].unit.children[tp[1]].key not in bad:
                return Tune(ch, tape.rows[tp[0]], tp[1], tp[2])
        return Tune(ch, tape.rows[i], c, 0)

    def provider(self, t):
        return self.sources.strict(t.entry.server or None) if self.sources is not None else None

    def banner(self, cid, t=None, now=None, pos_s=None, dur_s=None):
        """The info banner model: the item that plays (t, with the player's position) or, for
        a channel change, the tape at now."""
        now = self.clock() if now is None else now
        tz = self.lineup._tz()
        ch, tape = self.lineup.channel(cid), self.lineup.tape(cid)
        if ch is None:
            return None
        if t is None:
            tp = tune_point(tape, now, snap=0)
            if tp is None:
                return None
            a = tape.rows[tp[0]]
            e = a.unit.children[tp[1]]
            pos, dur, a_start = now - (a.start + a.unit.offsets[tp[1]]), e.dur, a.start
        else:
            e, a_start = t.entry, t.a_start
            dur = dur_s or e.dur
            pos = pos_s if pos_s is not None else now - t.start
        nxt = ""
        if tape is not None:
            j = tape.at(a_start)
            if j is not None and j + 1 < len(tape.rows):
                na = tape.rows[j + 1]
                nxt = "Next %s • %s" % (hhmm(na.start, tz), prog_title(na.unit.children[0]))
        frac = max(0.0, min(1.0, pos / dur)) if dur else 0.0
        return {"number": num(ch.number), "name": ch.name, "title": prog_title(e),
                "line": prog_meta(e), "frac": frac, "left": mins_left(max(0, dur - pos)),
                "next": nxt, "clock": hhmm(now, tz)}
