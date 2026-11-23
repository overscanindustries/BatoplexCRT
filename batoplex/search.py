"""Live search across servers. No pygame: the screen owns the drawing.

A session holds the query, starts one request per server after a short delay, and merges the
replies as they land. Each server has at most one request in flight: when the query changes
during a request, the newest query starts when the reply arrives. A reply for an old query is
dropped. The query is user text, so it is never logged.
"""
import time

from . import log
from .model import Row

L = log.get("search")

GROUP_MAX = 24
# Agent guids that name one title everywhere; local:// numbers are per server and can collide.
GLOBAL_GUIDS = ("plex://", "imdb://", "tmdb://", "tvdb://", "com.plexapp.agents.imdb",
                "com.plexapp.agents.themoviedb", "com.plexapp.agents.thetvdb")


def merge(parts, order, cap=GROUP_MAX):
    """parts: {server_id: [Row]}; order: server ids, home first. One Row per type, in the order
    the types first appear (the home server's hub order, then types only other servers have).
    Inside a type the servers take turns by rank; a copy with a guid already shown is dropped."""
    groups = {}
    for sid in order:
        for row in parts.get(sid) or []:
            g = groups.setdefault(row.key, [row.title, []])
            g[1].append(list(row.items))
    out = []
    music = ("artist", "album", "track")       # after the video groups
    keys = [k for k in groups if k not in music] + [k for k in music if k in groups]
    for key in keys:
        title, lists = groups[key]
        seen, items = set(), []
        for i in range(max(len(x) for x in lists)):
            for lst in lists:
                if i >= len(lst):
                    continue
                it = lst[i]
                guid = it.extra.get("guid")
                if guid and not guid.startswith(GLOBAL_GUIDS):
                    guid = None                 # local://<id> and the like: per server only
                if guid and guid in seen:
                    continue
                if guid:
                    seen.add(guid)
                items.append(it)
        if items:
            out.append(Row(key, title, items[:cap]))
    return out


class SearchSession:
    """targets() -> [(server_id, provider)]; run(fn, done, fail) runs fn off the UI thread and
    calls done or fail on the UI thread (Screen.run_bg)."""

    def __init__(self, targets, run, clock=time.monotonic, min_len=2, delay_s=0.3, limit=12,
                 on_auth=None, cap=GROUP_MAX):
        self.targets, self.run, self.clock = targets, run, clock
        self.min_len, self.delay_s, self.limit, self.cap = min_len, delay_s, limit, cap
        self.on_auth = on_auth
        self.query = ""
        self.gen = 0
        self.due = None             # time to start the current query
        self.started = None         # time the current query started
        self.provs = {}             # server_id -> provider, of the current query
        self.order = []
        self.busy = set()           # server ids with a request in flight
        self.asked, self.parts, self.failed = set(), {}, set()
        self.expect = set()         # server ids the current query waits for
        self.rows, self.state = [], "idle"     # idle | searching | done | error
        self.changed = False

    def set_query(self, q):
        if q == self.query:
            return
        self.query = q
        self.gen += 1
        self.asked, self.parts, self.failed = set(), {}, set()
        if len(q.strip()) < self.min_len:
            self.due = self.started = None
            if self.rows or self.state != "idle":
                self.rows, self.state, self.changed = [], "idle", True
        else:
            self.due = self.clock() + self.delay_s

    def retry(self):
        """Run the current query again now (after an error)."""
        if len(self.query.strip()) >= self.min_len:
            self.gen += 1
            self.asked, self.parts, self.failed = set(), {}, set()
            self.due = self.clock()

    def tick(self):
        if self.due is not None and self.clock() >= self.due:
            self.due = None
            self.started = self.clock()
            try:
                targets = list(self.targets())
            except Exception as e:                  # not fatal: nothing to search
                L.info("search targets failed: %s", e)
                targets = []
            self.order = [sid for sid, _ in targets]
            self.provs = dict(targets)
            self.expect = set(self.order)
            for sid in self.order:
                self._ask(sid)
            self._update()
        ch, self.changed = self.changed, False
        return ch

    def elapsed(self):
        return 0.0 if self.started is None else self.clock() - self.started

    def _ask(self, sid):
        if sid in self.busy or sid in self.asked:
            return
        p = self.provs.get(sid)
        if p is None:
            return
        g, q = self.gen, self.query.strip()
        self.busy.add(sid)
        self.asked.add(sid)
        L.info("search: %d characters, server %d of %d", len(q),
               self.order.index(sid) + 1 if sid in self.order else 0, len(self.order))
        self.run(lambda: p.search(q, limit=self.limit),
                 lambda rows: self._reply(sid, g, rows, None),
                 lambda e: self._reply(sid, g, None, e))

    def _reply(self, sid, g, rows, err):
        self.busy.discard(sid)
        if g != self.gen:
            # An old query: drop it. If the current query waits for this server, start it.
            if self.due is None and self.started is not None and sid in self.provs:
                self._ask(sid)
            return
        if err is not None:
            from . import plex
            L.info("search on server %s failed: %s", sid, type(err).__name__)
            self.failed.add(sid)
            if isinstance(err, plex.AuthError) and self.order and sid == self.order[0] \
                    and self.on_auth:
                self.on_auth()
        else:
            self.parts[sid] = rows or []
        self._update()

    def _update(self):
        if self.started is None:
            return
        pending = self.expect - set(self.parts) - self.failed
        if self.parts:
            rows = merge(self.parts, self.order, self.cap)
        elif pending:
            rows = self.rows                 # the old results stay until new ones land
        else:
            rows = []
        if pending and not self.parts:
            state = "searching"
        elif not pending and self.failed and not self.parts:
            state = "error"
        elif not self.expect:
            state = "error"                  # nothing to search (no server)
        else:
            state = "done" if (rows or not pending) else "searching"
        if rows is not self.rows or state != self.state:
            self.rows, self.state, self.changed = rows, state, True
        L.debug("search state %s, %d groups", state, len(rows))
