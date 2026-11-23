"""The Watchlist: the saved movies and shows of the Plex account, shown only when
a copy is on one of the user's servers (no Discover content anywhere).

Three calls go to the Discover host: the list, add and remove (plex.watchlist*). Each title is
looked up on the servers by its guid (PlexProvider.by_guid) in a background job; each
(server, guid) pair is found, absent or unavailable, and only the first two are kept. The state
of one account and profile is cached in cache/watchlist/{id}.json. Titles never go into a log
line. No pygame here.

Worker results are checked against (src_gen, epoch, id) before they change memory or disk, and
a server result also against the provider object it came from: Screen.run_bg does no check of
its own, and a new server does not change src_gen. Writes go to the Discover host, not to a
server: they are checked against the identity (account, profile, token) only, so a reconnect
of the same profile keeps them.
"""
import json
import os
import queue
import threading
import time

from . import config, log, plex

L = log.get("watchlist")
BATCH = 20                  # guids per lookup call
STALE_S = 300               # the screen loads again when the list is older than this
AGAIN_S = 30                # one more load after a play: Plex removes a played title late
RETRY_S = 20                # one more lookup on a server whose sections did not load
REFUSED = (401, 403, 404, 498)
UNKNOWN, YES, NO = "unknown", "yes", "no"


class Watchlist:
    def __init__(self, app, root=None):
        self.app = app              # post, src_gen, sources, settings, toast
        self.root = root            # the cache folder; None: <data>/cache/watchlist
        self.epoch = 0
        self.version = 0            # moves on each change a screen shows
        self.clock = time.monotonic
        self._wq = None
        self._seq = 0
        self.ident = 0              # moves when the account, profile or token changes
        self.id = self.token = self.cid = None
        self.pending = {}
        self.reset(None, None, None)

    # ---------------------------------------------------------------- identity
    def reset(self, wid, token, client_id, owner=True):
        """A new account, profile or provider (App._use): the old state goes from memory; the
        new one comes from its file. wid None: memory only, no file is written. The same
        account and profile again (a reconnect): the writes not yet confirmed stay."""
        self.epoch += 1
        same = bool(token) and (wid, token, client_id) == (self.id, self.token, self.cid)
        if not same:
            self.ident += 1
        self.id, self.token, self.cid, self.owner = wid, token, client_id, owner
        self.titles = []            # [{key, guid, type, added_at}] in list order (all titles)
        self.complete = False       # the last list had all its pages
        self.support = UNKNOWN
        self.found = {}             # (server, guid) -> {key, section, stamp, tile} | {absent, stamp}
        if not same:
            self.pending = {}       # key -> {want, seq, entry}: writes not yet confirmed
        self.since = {}             # key -> (want, entry): writes confirmed while a load runs
        self.retry = {}             # server -> when: one more lookup (its sections failed)
        self.retried = set()        # servers with a retry used since their last good sections
        self.failed = set()         # servers whose sections did not load in the last lookup
        self.loaded_at = None
        self.loading = self.error = self.stale = False
        self.resolving = 0
        self.again = None
        self.played = None          # the guid of a title played since the last load
        self.need_load = bool(token)
        if wid and token:
            self._read()
        self._changed()

    def _changed(self):
        self.version += 1

    def _ticket(self):
        return (getattr(self.app, "src_gen", 0), self.epoch, self.id)

    def _ok(self, t):
        return t == self._ticket()

    def _alive(self, t):
        return t[1] == self.epoch

    def visible(self):
        """The Watchlist shows (panel, More, buttons, menu rows): a token, and not refused."""
        return bool(self.token) and self.support != NO

    # ---------------------------------------------------------------- disk
    def _dir(self):
        return self.root or os.path.join(config.data_dir(), "cache", "watchlist")

    def _path(self):
        return os.path.join(self._dir(), "%s.json" % self.id)

    def _read(self):
        d = config.load_dict(self._path(), "watchlist cache")
        if not d:
            return
        self.titles = [t for t in d.get("titles") or [] if isinstance(t, dict) and t.get("key")]
        self.complete = bool(d.get("complete")) and isinstance(d.get("titles"), list)
        self.support = d.get("support") if d.get("support") in (UNKNOWN, YES, NO) else UNKNOWN
        for e in d.get("found") or []:
            if isinstance(e, list) and len(e) == 3 and isinstance(e[2], dict):
                self.found[(e[0], e[1])] = e[2]

    def _write(self):
        if not self.id:
            return
        data = {"v": 1, "titles": self.titles, "complete": self.complete,
                "support": self.support,
                "found": [[s, g, m] for (s, g), m in self.found.items()]}
        try:
            os.makedirs(self._dir(), mode=0o700, exist_ok=True)
            config.atomic_write(self._path(), json.dumps(data), mode=0o600, fsync=False)
        except OSError as e:
            L.info("watchlist cache not saved: %s", e)

    # ---------------------------------------------------------------- the list
    def tick(self, ready):
        """Each frame: the first load once Home has its rows (ready), and the one more load
        after a play."""
        if self.need_load and ready:
            self.load()
        if self.again is not None and self.clock() >= self.again:
            self.again = None
            self.load()
        for sid, when in list(self.retry.items()):
            if self.clock() >= when:
                del self.retry[sid]
                self.resolve(only=sid)

    def opened(self):
        """The Watchlist screen opened: the cache shows at once; load again when it is older
        than 5 minutes or stale."""
        if self.loaded_at is None or self.stale or self.clock() - self.loaded_at > STALE_S:
            self.load()

    def load(self):
        if not self.token or self.loading:
            return False
        self.loading, self.need_load = True, False
        self.since = {}
        t, tok, cid = self._ticket(), self.token, self.cid

        def work():
            try:
                res = (True, plex.watchlist(cid, tok))
            except Exception as e:
                res = (False, e)
            self.app.post(lambda: self._loaded(t, res))
        threading.Thread(target=work, daemon=True).start()
        self._changed()
        return True

    def _loaded(self, t, res):
        if not self._ok(t):
            return                                   # another profile or a sign-out meanwhile
        self.loading = False
        ok, v = res
        since, self.since = self.since, {}
        if ok:
            # A write confirmed after this load started is not in its list: it goes on top.
            for key, (want, entry) in since.items():
                v = [x for x in v if x["key"] != key]
                if want:
                    v.insert(0, entry)
            self.titles, self.complete, self.support = v, True, YES
            self.error, self.stale = False, False
            self.loaded_at = self.clock()
            self._write()
            self.resolve()
            if self.played and any(x["guid"] == self.played for x in self.titles):
                self.again = self.clock() + AGAIN_S
            self.played = None
        else:
            code = getattr(v, "code", None)
            L.info("watchlist load failed: %s", v)
            if not self.owner and code in REFUSED:
                self.support = NO                    # this Home profile has no Watchlist
                self._write()
            else:
                self.error = True                    # the last complete list stays
        self._changed()

    def state(self, key):
        """On the list: True or False; None while no complete list is known (no action shows)."""
        if not key:
            return None
        if key in self.pending:
            return self.pending[key]["want"]
        if not self.complete:
            return None
        return any(x["key"] == key for x in self.titles)

    def item_state(self, it):
        if it is None or it.type not in plex.WATCH_TYPES:
            return None
        return self.state(plex.watch_key(it.extra.get("guid")))

    def view_titles(self):
        """The titles with the pending writes applied: a pending add first (newest)."""
        out = [x for x in self.titles if self.pending.get(x["key"], {}).get("want", True)]
        have = {x["key"] for x in out}
        adds = sorted((p for k, p in self.pending.items() if p["want"] and k not in have),
                      key=lambda p: -p["seq"])
        return [p["entry"] for p in adds] + out

    # ---------------------------------------------------------------- writes
    def set(self, it, want):
        """Add or remove a library movie or show. The view changes at once; one write worker
        sends the writes in order."""
        guid = it.extra.get("guid")
        key = plex.watch_key(guid)
        if not key or not self.token:
            return False
        self._seq += 1
        entry = {"key": key, "guid": guid, "type": it.type, "added_at": int(time.time())}
        self.pending[key] = {"want": want, "seq": self._seq, "entry": entry}
        if want and it.server_id:
            self._note_item(it)
        self._changed()
        self._send((self.ident, self.token, self.cid, key, want, self._seq, entry))
        return True

    def _send(self, op):
        if self._wq is None:
            self._wq = queue.Queue()
            threading.Thread(target=self._writer, args=(self._wq,), daemon=True).start()
        self._wq.put(op)

    def _writer(self, q):
        while True:
            self._write_one(q.get())

    def _write_one(self, op):
        t, tok, cid, key, want, seq, entry = op
        if t != self.ident:
            return                                   # a write of an earlier profile
        try:
            (plex.watchlist_add if want else plex.watchlist_remove)(cid, tok, key)
            err = None
        except Exception as e:
            err = e
        self.app.post(lambda a=(t, key, want, seq, entry, err): self._written(*a))

    def _written(self, t, key, want, seq, entry, err):
        if t != self.ident:
            return
        mine = self.pending.get(key, {}).get("seq") == seq
        if err is None:
            self.titles = [x for x in self.titles if x["key"] != key]
            if want:
                self.titles.insert(0, entry)
            if self.loading:
                self.since[key] = (want, entry)      # the later one wins
            if mine:
                del self.pending[key]
            self._write()
            self.app.toast("Added to Watchlist" if want else "Removed from Watchlist")
        else:
            L.info("watchlist write failed: %s", err)
            if mine:
                del self.pending[key]                # only this title goes back
            self.app.toast("Can't change your Watchlist")
        self._changed()

    def _note_item(self, it):
        """A title added from a library page is found on that server at once (the grid shows
        it first without a lookup)."""
        p = self.app.sources.strict(it.server_id) if self.app.sources is not None else None
        stamp = next((s.get("stamp") for s in getattr(p, "sections", None) or []
                      if s.get("id") == it.section_id), None)
        self.found[(it.server_id, it.extra.get("guid"))] = {
            "key": it.id, "section": it.section_id, "stamp": stamp, "tile": {
                "type": it.type, "title": it.title, "year": it.year, "date": it.extra.get("date"),
                "poster": _path(it.poster), "art": _path(it.art), "dur": it.duration_ms,
                "offset": it.view_offset_ms, "views": 1 if it.watched else 0,
                "leaf": it.leaf_count, "viewed": it.viewed_leaf_count, "child": it.child_count,
                "rating": it.content_rating}}

    # ---------------------------------------------------------------- servers
    def servers(self):
        """[(server id, provider)]: the home server, then each connected server with a pin
        (Sources.search_providers), with a guid lookup."""
        src = self.app.sources
        if src is None or getattr(src, "local_images", False):
            return []
        return [(getattr(p, "server_id", None) or sid, p)
                for sid, p in src.search_providers(self.app.settings) if hasattr(p, "by_guid")]

    def resolve(self, only=None):
        """A background job: the current section stamps of each server first, then the titles
        without a fresh mapping in batches of 20. Each batch is published when it lands. A
        failed call changes nothing: the pair is tried again next time."""
        if not self.token:
            return
        provs = [(s, p) for s, p in self.servers() if only is None or s == only]
        guids = []
        for x in self.view_titles():
            if x.get("guid") and x["guid"] not in guids:
                guids.append(x["guid"])
        if not provs or not guids:
            return
        t, found = self._ticket(), dict(self.found)
        self.resolving += 1

        def work():
            for sid, p in provs:
                if not self._alive(t):
                    break
                try:
                    p.load_sections()                # fresh stamps (Home loads them once)
                except Exception as e:
                    L.info("watchlist: sections of a server failed: %s", e)
                    self.app.post(lambda sid=sid, p=p: self._sections_failed(t, sid, p))
                    continue                         # unavailable: its found titles stay
                secs = list(getattr(p, "sections", None) or [])
                stamps = {s["id"]: s.get("stamp") for s in secs}
                sig = server_sig(secs)
                todo = [g for g in guids if not fresh(found.get((sid, g)), stamps, sig)]
                self.app.post(lambda sid=sid, p=p: self._server_up(t, sid, p))
                for i in range(0, len(todo), BATCH):
                    if not self._alive(t):
                        break
                    part = todo[i:i + BATCH]
                    try:
                        got = p.by_guid(part)
                    except Exception as e:
                        L.info("watchlist: a lookup failed: %s", e)
                        continue                     # unavailable: no mapping changes
                    self.app.post(lambda a=(t, sid, p, part, got, stamps, sig): self._resolved(*a))
            self.app.post(lambda: self._resolve_done(t))
        threading.Thread(target=work, daemon=True).start()

    def _same(self, t, sid, p):
        src = self.app.sources
        return self._ok(t) and src is not None and (src.strict(sid) is p)

    def _sections_failed(self, t, sid, p):
        """The server is still connected: its found titles stay. One more lookup in 20 s; no
        more until its sections load again."""
        if not self._same(t, sid, p):
            return
        if sid not in self.failed:
            self.failed.add(sid)
            self._changed()
        if sid not in self.retried:
            self.retried.add(sid)
            self.retry[sid] = self.clock() + RETRY_S

    def _server_up(self, t, sid, p):
        if self._same(t, sid, p):
            self.retried.discard(sid)
            self.retry.pop(sid, None)
            if sid in self.failed:
                self.failed.discard(sid)
                self._changed()

    def _resolved(self, t, sid, p, part, got, stamps, sig):
        if not self._same(t, sid, p):
            return                                   # a late reply: another profile or provider
        for g in part:
            v = got.get(g)
            if v:
                key, sec, tile = v
                self.found[(sid, g)] = {"key": key, "section": sec, "stamp": stamps.get(sec),
                                        "tile": tile}
            else:
                self.found[(sid, g)] = {"absent": True, "stamp": sig}
        self._write()
        self._changed()

    def _resolve_done(self, t):
        if self._alive(t):
            self.resolving = max(0, self.resolving - 1)
            self._changed()

    def server_added(self, sid):
        """A server connected: its found titles show now from the cache; then a lookup."""
        self._changed()
        self.resolve(only=sid)

    def sources_changed(self):
        """Pins or hidden libraries changed: the server set or the sections did."""
        self._changed()
        self.resolve()

    def mark_played(self, guid=None):
        """A mark as played succeeded, or playback ended with a played item: the list is stale
        (Plex removes a played title on its side). The caller loads it when a screen shows it."""
        if not self.token:
            return
        self.stale = True
        self.played = guid or self.played
        self._changed()

    def apply_files(self, files, exact=False):
        """Playback results and marks: the cached tiles take the watch state."""
        changed = False
        for f in files:
            for (sid, _g), m in self.found.items():
                tl = m.get("tile")
                if tl is None or m.get("key") != f.get("item_id") \
                        or f.get("server_id") not in (None, sid):
                    continue
                if exact or f.get("watched"):
                    tl["views"] = 1 if f.get("watched") else 0
                    if tl.get("type") == "show":
                        tl["viewed"] = tl.get("leaf", 0) if f.get("watched") else 0
                tl["offset"] = 0 if f.get("watched") else int(f.get("position_ms") or 0)
                changed = True
        if changed:
            self._changed()

    def guid_of(self, server_id, item_id):
        for (sid, g), m in self.found.items():
            if sid == server_id and m.get("key") == item_id:
                return g
        return None

    # ---------------------------------------------------------------- the grid
    def first_load(self):
        """No list and no title to show yet, and a load or lookup runs: the grid's loading
        state."""
        return not self.complete and not self.titles and (self.loading or self.need_load)

    def shown(self):
        """The titles to show, in list order: on the list (pending applied) and found on a
        connected server (the home server first, then Sources order). A server whose sections
        failed comes last: Sources has no offline state, and another server's copy plays. A
        title on no server is not shown, nor counted."""
        servers = self.servers()
        servers = [x for x in servers if x[0] not in self.failed] + \
            [x for x in servers if x[0] in self.failed]
        st = self.app.settings
        hidden = {s: st.hidden(s) for s, _p in servers}
        out = []
        for x in self.view_titles():
            for sid, _p in servers:
                m = self.found.get((sid, x.get("guid")))
                if not m or m.get("absent") or m.get("section") in hidden[sid]:
                    continue
                out.append(tile_item(x, sid, m))
                break
        return out


def _path(ref):
    """An image ref without its server tag."""
    return ref.split("|", 1)[1] if ref and "|" in ref else ref


def fresh(m, stamps, sig):
    """A mapping is used again only when its stamp is the current one: a found title's section
    stamp, an absent title's server stamp. No stamp: no reuse (a title added later shows)."""
    if not m:
        return False
    if m.get("absent"):
        return sig is not None and m.get("stamp") == sig
    st = stamps.get(m.get("section"))
    return st is not None and m.get("stamp") == st


def server_sig(sections):
    """One stamp for the video sections of a server; None when one has no stamp."""
    parts = []
    for s in sorted(sections, key=lambda s: s["id"]):
        if s.get("type") not in plex.VIDEO_SECTION_TYPES:
            continue
        if s.get("stamp") is None:
            return None
        parts.append("%s:%s" % (s["id"], s["stamp"]))
    return ",".join(parts)


def tile_item(x, sid, m):
    """A library Item for one shown title, from the cached tile (no server call)."""
    from .model import Item
    tl = m.get("tile") or {}
    typ = tl.get("type") or x.get("type") or "movie"
    tag = lambda ref: "%s|%s" % (sid, ref) if ref and sid and "|" not in ref else ref
    it = Item(id=str(m["key"]), type=typ, title=tl.get("title") or "", year=tl.get("year"),
              duration_ms=int(tl.get("dur") or 0), view_offset_ms=int(tl.get("offset") or 0),
              content_rating=tl.get("rating") or "", poster=tag(tl.get("poster")),
              art=tag(tl.get("art")), section_id=m.get("section"), server_id=sid)
    if typ == "show":
        it.leaf_count, it.viewed_leaf_count = int(tl.get("leaf") or 0), int(tl.get("viewed") or 0)
        it.child_count = int(tl.get("child") or 0)
        it.watched = bool(it.leaf_count) and it.viewed_leaf_count >= it.leaf_count
    else:
        it.watched = bool(tl.get("views"))
    it.extra.update(guid=x.get("guid"), added_at=x.get("added_at") or 0)
    if tl.get("date"):
        it.extra["date"] = str(tl["date"])
    return it

