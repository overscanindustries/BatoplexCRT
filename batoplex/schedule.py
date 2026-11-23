"""Channel schedules.

Pure: no I/O, no pygame, no network, no `random` module. The same inputs give the same tape on
every box and every Python version.

Terms
- Entry: one playable item of a pool (a movie or an episode), with its display fields.
- Unit: what the generator picks. One entry, or a multi-part episode (adjacent episodes
  "Title (1)", "Title (2)") kept whole, with its children and their cumulative start offsets.
- Pool: the units of one source after the rules (no duration, specials, duplicates, cap).
- Slot: one source with one order. A channel has one slot, or one for each distinct
  (source, order, block size) of its time blocks; equal time blocks share a slot and its state.
- Airing: (start, duration, slot, unit). The tape is a list of airings with no gaps.
- Broadcast day: from 04:00 local time (an explicit time zone) to the next 04:00. A calendar
  boundary, so a day is 23 or 25 h long at a daylight-saving change.
- Cache: the JSON-able dict freeze() returns; the caller stores one for each channel and profile.

Restart stability: the cache keeps the broadcast date of the last freeze, the rule
revision, the frozen pools, the anchor (start time and generator state) and the tape. A second
freeze on the same date with the same revision returns the same tape, whatever the new pools.
"""
import bisect
import hashlib
import math
import re
from collections import namedtuple
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                                   # pragma: no cover (Python < 3.9)
    ZoneInfo = None

VERSION = 2                   # 2: rank bags (the cursor is a rank, not a position)
DAY_HOUR = 4                  # the broadcast day starts at 04:00 local time
MIN_DUR = 60                  # s; an item with no duration or under 60 s is left out
MAX_DUR = 8 * 3600            # s; an item over 8 h is left out (a bad duration or a recording)
MAX_POOL = 5000               # entries kept for one pool, after the stable order
TAIL = 10                     # s; under this left of a child: tune the next one at 0
KEEP_BEFORE = 6 * 3600        # s of tape kept before a freeze boundary (clock corrections)
HORIZON = 26 * 3600           # s of tape after now, at least (the guide shows 24 h)
MAX_AGE = 30 * 86400          # s; an older anchor starts the schedule again
LOOKBACK_DAYS = 8             # time blocks: the last start is looked for this far back
CLOCK_MIN = 1767225600        # 2026-01-01 UTC; a clock before this has no time yet
MODES = ("order", "shuffle", "blocks")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MOVIES = "movies"             # the one group of all movies in `blocks`
TYPES = ("episode", "movie")

Entry = namedtuple("Entry", "key id server dur group type season index title show rating year")
Unit = namedtuple("Unit", "key children dur offsets group season index")


class Airing(namedtuple("Airing", "start dur slot unit")):
    __slots__ = ()

    @property
    def end(self):
        return self.start + self.dur


class Slot(namedtuple("Slot", "key mode block")):
    """mode: one of MODES, or None (blocks when the pool has episodes, else shuffle).
    block: 1-4 units of one show for each pick in `blocks`, or None (2 when the median
    episode is under 35 min, else 1)."""
    __slots__ = ()


class Plan(namedtuple("Plan", "seed rev slots times")):
    """What freeze() needs of a channel. seed: the channel id (persistent). rev: the rule
    revision (a new source, order or rule starts the schedule again; a new name or number does
    not). slots: a tuple of Slot. times: () for a channel with one slot, else a tuple of
    (minute of the day, frozenset of DAYS or None for every day, slot key[, once]), in file
    order; of two starts at one time the later one wins. once (optional, False when left out):
    the block airs one unit at the first item boundary at or after its start, then the latest
    start that is not `once` goes on."""
    __slots__ = ()


# -- time

def zone(tz):
    """A tzinfo from an IANA name, a tzinfo, or None/"" (UTC). An unknown name: UTC."""
    if tz is None or tz == "":
        return timezone.utc
    if not isinstance(tz, str):
        return tz
    try:
        return ZoneInfo(tz)
    except Exception:
        return timezone.utc


def zone_name(tz):
    return getattr(tz, "key", None) or str(tz)


def clock_ok(now):
    """False while the box has no time yet: keep tapes in memory only, write nothing."""
    return now >= CLOCK_MIN


def _local(d, minute, tz):
    """Epoch seconds of local wall time `minute` on date d. A time in a spring-forward gap
    moves forward by the gap; an ambiguous fall-back time is its first occurrence."""
    return int(datetime(d.year, d.month, d.day, minute // 60, minute % 60, tzinfo=tz).timestamp())


def day_bounds(d, tz):
    """Epoch seconds of 04:00 local time on date d: the start of broadcast date d."""
    return _local(d, DAY_HOUR * 60, tz)


def day_start(now, tz):
    """(epoch of the latest local 04:00 at or before now, its broadcast date)."""
    tz = zone(tz)
    d = datetime.fromtimestamp(now, tz).date()
    b = day_bounds(d, tz)
    if b > now:
        d -= timedelta(days=1)
        b = day_bounds(d, tz)
    return b, d


def broadcast_date(now, tz):
    return day_start(now, tz)[1]


# -- random numbers: sha256 of the seed and a counter

def rand(seed, *parts):
    """A 64-bit integer from sha256("<seed>:<part>:..."). Fixed for all time."""
    s = ":".join(str(p) for p in (seed,) + parts)
    return int.from_bytes(hashlib.sha256(s.encode("utf-8")).digest()[:8], "big")


# -- entries and pools

def entry(item, server=""):
    """An Entry from a model.Item (movie or episode), or None for any other type."""
    if getattr(item, "type", None) not in TYPES:
        return None
    srv = item.server_id or server or ""
    key = "%s/%s" % (srv, item.id)
    if item.type == "episode":
        group = "%s/%s" % (srv, item.show_id) if item.show_id else key
    else:
        group = MOVIES
    return Entry(key, str(item.id), srv, int(item.duration_ms or 0) // 1000, group, item.type,
                 _int(item.season_index), _int(item.index), item.title or "",
                 item.show_title or "", item.content_rating or "", _int(item.year))


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_NUM = r"([0-9]{1,2}|[ivx]{1,4}|one|two|three|four|five|six)"
_PART = re.compile(r"\s*[-:,]?\s*(?:\(\s*(?:(?:part|pt\.?)\s*)?" + _NUM + r"\s*\)|(?:part|pt\.?)\s*" +
                   _NUM + r")\s*$", re.I)


def part_mark(title):
    """("base title" in lower case, part number) for "Title (2)", "Title, Part Two",
    "Title Pt. II"; None for a title with no end mark."""
    m = _PART.search(title or "")
    if not m:
        return None
    raw = (m.group(1) or m.group(2)).lower()
    n = int(raw) if raw.isdigit() else _WORDS.get(raw) or _ROMAN.get(raw)
    base = title[:m.start()].strip().lower()
    return (base, n) if n and base else None


def _canon(e):
    big = 1 << 30
    return (0 if e.type == "episode" else 1, e.group,
            big if e.season is None else e.season, big if e.index is None else e.index,
            e.title.casefold(), e.key)


def build_pool(entries, canonical=False, repeats=False, cap=MAX_POOL, specials=False):
    """A Pool from entries in source order. Left out: other types, no duration, under
    MIN_DUR or over MAX_DUR, season 0 (unless specials), a repeated key (unless repeats: a
    playlist plays an item twice; the second gets the key "<key>#2"). canonical: sort by (episodes first, show,
    season, episode, title, key) for sources with no meaningful order (library, genre, network),
    so the cap and the group order do not follow the server's order. Then the first `cap`."""
    seen, out = {}, []
    for e in entries:
        if e is None or e.type not in TYPES or not e.dur or not MIN_DUR <= e.dur <= MAX_DUR:
            continue
        if e.type == "episode" and e.season == 0 and not specials:
            continue
        n = seen.get(e.key, 0) + 1
        seen[e.key] = n
        if n > 1:
            if not repeats:
                continue
            e = e._replace(key="%s#%d" % (e.key, n))
        out.append(e)
    if canonical:
        out.sort(key=_canon)
    return Pool(out[:max(0, cap)])


class Pool:
    """The units of one slot. entries: the kept entries (what a cache stores)."""

    def __init__(self, entries):
        self.entries = list(entries)
        es, units, i = self.entries, [], 0
        while i < len(es):
            e = es[i]
            kids, mark, j = [e], part_mark(e.title), i + 1
            while j < len(es) and e.type == "episode":
                nx, prev = es[j], kids[-1]
                if nx.group != e.group or nx.type != "episode" or nx.season != e.season:
                    break
                if prev.index is not None and nx.index is not None and nx.index != prev.index + 1:
                    break
                nm = part_mark(nx.title)
                if not nm:
                    break
                if mark:                              # the next part number
                    ok = nm == (mark[0], mark[1] + len(kids))
                else:                                 # "Title" then "Title (2)"
                    ok = len(kids) == 1 and nm == (e.title.strip().lower(), 2)
                    if ok:
                        mark = (nm[0], 1)
                if not ok:
                    break
                kids.append(nx)
                j += 1
            offs, t = [], 0
            for k in kids:
                offs.append(t)
                t += k.dur
            units.append(Unit(e.key, tuple(kids), t, tuple(offs), e.group, e.season, e.index))
            i = j
        self.units = units
        self.by_key = {u.key: k for k, u in enumerate(units)}
        self.groups, self.group_units, self.gpos = [], {}, {}
        for k, u in enumerate(units):
            lst = self.group_units.get(u.group)
            if lst is None:
                lst = self.group_units[u.group] = []
                self.groups.append(u.group)
            self.gpos[k] = len(lst)
            lst.append(k)
        self.has_episodes = any(u.children[0].type == "episode" for u in units)
        self.fp = hashlib.sha256("\n".join(u.key for u in units).encode("utf-8")).hexdigest()[:16]

    def __len__(self):
        return len(self.units)


def auto_block(pool):
    """2 when the median episode unit is under 35 min, else 1."""
    d = sorted(u.dur for u in pool.units if u.children[0].type == "episode")
    return 2 if d and d[len(d) // 2] < 35 * 60 else 1


# -- generator

class _Bag:
    """Units of a list in a random order, a new order for each bag. A bag orders its units by
    rank = rand(seed, "bag", bag, unit key); the cursor is the rank of the last unit played. So a
    changed pool keeps the bag: a new unit falls at a random place, a played unit stays played.
    The first unit of a new bag never repeats the unit played last (it changes places with the
    second)."""

    def __init__(self, pool, items, seed, st):
        self.pool, self.items, self.seed = pool, items, seed
        st = st if isinstance(st, dict) else {}
        self.b = int(st.get("bag", 0))
        cur = st.get("cur")
        self.cur = (int(cur[0]), str(cur[1])) if isinstance(cur, list) and len(cur) == 2 else None
        self.pend, self.last = st.get("pend"), st.get("last")
        self._o, self._in = None, set(items)

    def _order(self):
        if self._o is None or self._o[0] != self.b:
            keys = self.pool.units
            o = sorted((rand(self.seed, "bag", self.b, keys[k].key), keys[k].key, k) for k in self.items)
            self._o = (self.b, [x[:2] for x in o], [x[2] for x in o])
        return self._o[1], self._o[2]

    def next(self):
        if self.pend is not None:                     # the unit the seam swap put back
            k = self.pool.by_key.get(self.pend)
            self.pend = None
            if k in self._in:
                self.last = self.pool.units[k].key
                return k
        ranks, ks = self._order()
        i = 0 if self.cur is None else bisect.bisect_right(ranks, self.cur)
        if i >= len(ks):                              # the bag is done: a new bag
            self.b, self.cur = self.b + 1, None
            ranks, ks = self._order()
            i = 0
        if i == 0 and len(ks) > 1 and ranks[0][1] == self.last:
            self.pend, i = ranks[0][1], 1
        self.cur = ranks[i]
        k = ks[i]
        self.last = self.pool.units[k].key
        return k

    def state(self):
        return {"bag": self.b, "cur": list(self.cur) if self.cur else None, "pend": self.pend,
                "last": self.last}


class Gen:
    """Picks the units of one slot. state: what Gen.state() returned, maybe for another pool:
    it is moved to this pool (a cursor finds its unit by key, then by season and episode in
    the same show, then the show start; a bag keeps its rank cursor)."""

    def __init__(self, pool, mode, block, seed, state=None):
        self.p, self.seed = pool, seed
        self.mode = mode if mode in MODES else ("blocks" if pool.has_episodes else "shuffle")
        self.block = block if block in (1, 2, 3, 4) else auto_block(pool)
        st = state if isinstance(state, dict) and state.get("m") == self.mode else {}
        getattr(self, "_load_" + self.mode)(st)

    def _ref(self, k):
        u = self.p.units[k]
        return [u.key, u.group, u.season, u.index]

    def _find(self, ref):
        """The unit index for a stored reference in this pool, or None."""
        if not isinstance(ref, list) or len(ref) != 4:
            return None
        k = self.p.by_key.get(ref[0])
        if k is not None:
            return k
        lst = self.p.group_units.get(ref[1])
        if not lst or ref[2] is None or ref[3] is None:
            return None
        want = (ref[2], ref[3])
        for k in lst:
            u = self.p.units[k]
            if u.season is not None and u.index is not None and (u.season, u.index) >= want:
                return k
        return lst[0]                                 # past the last episode: from the start

    # order: the pool in order, in a loop
    def _load_order(self, st):
        k = self._find(st.get("next"))
        self.i = k if k is not None else int(st.get("i", 0)) % max(1, len(self.p))

    def _next_order(self):
        k = self.i % len(self.p)
        self.i = (k + 1) % len(self.p)
        return k

    def _state_order(self):
        return {"m": "order", "i": self.i, "next": self._ref(self.i % len(self.p))}

    # shuffle: bags of all units
    def _load_shuffle(self, st):
        self.bag = _Bag(self.p, list(range(len(self.p))), self.seed, st.get("bag"))

    def _next_shuffle(self):
        return self.bag.next()

    def _state_shuffle(self):
        return {"m": "shuffle", "bag": self.bag.state()}

    # blocks: a show (or all movies) at random, never one of the last picks; `block` units of
    # a show from its cursor, one movie from the movie bag
    def _load_blocks(self, st):
        p = self.p
        self.cur = {}
        for g, ref in (st.get("cur") or {}).items():
            if g in p.group_units and g != MOVIES:
                k = self._find(ref)
                if k is not None and p.units[k].group == g:
                    self.cur[g] = p.gpos[k]
        self.n = int(st.get("n", 0))
        g = st.get("g")
        self.g = g if g in p.group_units else None
        self.left = int(st.get("left", 0)) if self.g else 0
        self.recent = [x for x in (st.get("recent") or []) if x in p.group_units]
        self.mbag = _Bag(p, p.group_units.get(MOVIES, []), self.seed + ":movies", st.get("mbag"))

    def _next_blocks(self):
        p = self.p
        if self.left <= 0 or self.g is None:
            gs = p.groups
            if len(gs) == 1:                          # one group: it repeats
                g = gs[0]
            else:
                recent = self.recent[-max(1, min(4, len(gs) // 2)):]
                cand = [x for x in gs if x not in recent] or gs
                # weight: the integer square root of the units of a show (larger shows air
                # more, but one 500-episode show does not fill the channel)
                w = [math.isqrt(len(p.group_units[x])) for x in cand]
                r = rand(self.seed, "g", self.n) % sum(w)
                for g, x in zip(cand, w):
                    if r < x:
                        break
                    r -= x
            self.n += 1
            self.g, self.left = g, 1 if g == MOVIES else self.block
            self.recent = (self.recent + [g])[-4:]
        self.left -= 1
        if self.g == MOVIES:
            return self.mbag.next()
        lst = p.group_units[self.g]
        pos = self.cur.get(self.g, 0) % len(lst)
        self.cur[self.g] = (pos + 1) % len(lst)
        return lst[pos]

    def _state_blocks(self):
        gu = self.p.group_units
        return {"m": "blocks", "n": self.n, "g": self.g, "left": self.left,
                "recent": list(self.recent), "mbag": self.mbag.state(),
                "cur": {g: self._ref(gu[g][pos % len(gu[g])]) for g, pos in sorted(self.cur.items())}}

    def next(self):
        return self.p.units[getattr(self, "_next_" + self.mode)()]

    def state(self):
        return getattr(self, "_state_" + self.mode)()


# -- time blocks

class _Times:
    """The slot in effect at an epoch: the latest eligible start at or before it, looked for
    across up to LOOKBACK_DAYS previous dates. Starts of slots with an empty pool are left out,
    so the slot before them goes on."""

    def __init__(self, plan, tz, live):
        self.tz = tz
        self.rows = [(r[0], r[1], r[2], n, bool(r[3]) if len(r) > 3 else False)
                     for n, r in enumerate(plan.times) if r[2] in live]
        if all(r[4] for r in self.rows):              # only `once` blocks: normal blocks
            self.rows = [r[:4] + (False,) for r in self.rows]
        self.t, self.keys, self.once, self.span = [], [], [], None

    def _build(self, t):
        d0 = datetime.fromtimestamp(t, self.tz).date() - timedelta(days=LOOKBACK_DAYS)
        ev = []
        for i in range(LOOKBACK_DAYS + 4):
            d = d0 + timedelta(days=i)
            wd = DAYS[d.weekday()]
            for m, days, key, n, once in self.rows:
                if days is None or wd in days:
                    ev.append((_local(d, m, self.tz), n, key, once))
        ev.sort()
        self.t = [e[0] for e in ev]
        self.keys = [e[2] for e in ev]
        self.once = [e[3] for e in ev]
        self.span = (day_bounds(d0 + timedelta(days=LOOKBACK_DAYS), self.tz) - 86400,
                     day_bounds(d0 + timedelta(days=LOOKBACK_DAYS + 3), self.tz))

    def at(self, t):
        i = self.index(t)
        return self.keys[i] if i is not None else None

    def index(self, t):
        """The event index of the latest start at or before t, or None."""
        if self.span is None or not (self.span[0] <= t < self.span[1]):
            self._build(t)
        i = bisect.bisect_right(self.t, t) - 1
        return i if i >= 0 else None

    def normal(self, i):
        """The slot of the latest start at or before event i that is not `once`, or None."""
        while i >= 0 and self.once[i]:
            i -= 1
        return self.keys[i] if i >= 0 else None


ONCE = "@once"                # state key: the start (epoch) of the last `once` block served


def _gens(plan, pools, states, seen=None):
    """A Gen for each slot with units. seen: {slot key: keys of watched entries}; a slot with
    no state and an entry in seen starts where its shows were left (_first_state)."""
    slots = {s.key: s for s in plan.slots}
    out = {}
    for k, p in pools.items():
        if k not in slots or not len(p):
            continue
        seed = "%s:%s" % (plan.seed, k)
        st = (states or {}).get(k)
        if st is None and seen and seen.get(k) is not None:
            st = _first_state(p, slots[k], seed, seen[k])
        out[k] = Gen(p, slots[k].mode, slots[k].block, seed, st)
    return out


def _start_unit(pool, units, watched, seed, group):
    """Where one show starts: nothing watched: its first unit; partly watched:
    the unit after the last watched one (as On Deck); watched to its last unit: a seeded
    point."""
    last = None
    for j, k in enumerate(units):
        if any(c.key in watched for c in pool.units[k].children):
            last = j
    if last is None:
        return units[0]
    if last + 1 < len(units):
        return units[last + 1]
    return units[rand(seed, "start", group) % len(units)]


def _first_state(pool, slot, seed, seen):
    """The first generator state of a slot from the watch state: `blocks` (each show) and
    `order` with one show. None for the other modes (the usual start)."""
    mode = slot.mode if slot.mode in MODES else ("blocks" if pool.has_episodes else "shuffle")
    watched = set(seen)

    def ref(k):
        u = pool.units[k]
        return [u.key, u.group, u.season, u.index]
    if mode == "blocks":
        return {"m": "blocks", "cur": {
            g: ref(_start_unit(pool, pool.group_units[g], watched, seed, g))
            for g in pool.groups if g != MOVIES}}
    if mode == "order" and len(pool.groups) == 1 and pool.has_episodes:
        g = pool.groups[0]
        return {"m": "order", "next": ref(_start_unit(pool, pool.group_units[g], watched, seed, g))}
    return None


def _run(plan, gens, tz, t, until, once=None):
    """Airings from t while the start is before until. once: the start of the last `once`
    block served (state ONCE). Returns (airings, the next start, once)."""
    out = []
    if not gens:
        return out, t, once
    times = _Times(plan, tz, gens) if plan.times else None
    only = next(iter(gens)) if not plan.times else None
    while t < until:
        if times:
            i = times.index(t)
            k = times.keys[i] if i is not None else None
            if i is not None and times.once[i]:
                if once != times.t[i]:                # the window opens: one unit, then it closes
                    once = times.t[i]
                else:
                    k = times.normal(i)
        else:
            k = only
        if k is None:                                 # no block yet (no start in the lookback)
            k = min(gens)
        u = gens[k].next()
        out.append(Airing(t, u.dur, k, u))
        t += u.dur
    return out, t, once


def _states(gens, old, once=None):
    st = dict(old or {})
    st.update({k: g.state() for k, g in gens.items()})
    if once is not None:
        st[ONCE] = once
    return st


def _once_of(state):
    v = (state or {}).get(ONCE)
    return v if isinstance(v, int) and not isinstance(v, bool) else None


# -- the daily freeze

def freeze(plan, pools, now, tz, cache=None, seen=None):
    """The schedule of a channel at `now`. pools: {slot key: Pool} just fetched, or None when
    the fetch failed (the cached pools stay; the cache is marked stale). cache: what this
    function returned before (or None). Returns the new cache, or None when there is nothing
    to play (no cache and no pool, or every pool empty).

    - Same broadcast date and revision as the cache (or an earlier date: the clock went back):
      the same tape. Only its end grows, from the cached pools, to cover now + HORIZON.
    - A later date: the old schedule runs on (from its anchor and pools, also across days
      offline) to t*, the first start at or after today's 04:00. The tape before t* stays. The
      generator state at t* moves to the new pools; that is the new anchor.
    - A stale cache and a successful fetch on the same date: as a later date, with t* the first
      start at or after now (the programme on now stays).
    - A new start (anchor at today's 04:00, first state): no or bad cache, a new revision, an
      anchor older than MAX_AGE, or a clock before the tape start.

    seen: {slot key: keys of the watched entries of the profile}, or None. Read only for a
    slot with no generator state, on a new start or a rule edit (_first_state); never on a
    daily freeze or an extend, so a cursor never moves after the first freeze.
    """
    tz, now = zone(tz), int(now)
    b, d = day_start(now, tz)
    c = _open(cache, plan, now)
    if c is None and (plan.times or _had_blocks(cache)):   # also back to one slot
        edited = _edit(plan, tz, d, pools, cache, now, seen)
        if edited is not None:
            return edited
    if c is None:
        if not pools:
            return None
        return _make(plan, tz, d, pools, b, {}, [], until=_until(d, now, tz), stale=False,
                     seen=seen)
    fresh = pools is not None
    cd = c["date"]
    if d < cd or (d == cd and not (c["stale"] and fresh)):
        return _extend(plan, tz, cache, c, now)
    boundary = b if d > cd else max(b, now)
    old = _pools(c)
    gens = _gens(plan, old, c["state"])
    replay, t_star, once = _run(plan, gens, tz, c["anchor"], boundary, _once_of(c["state"]))
    state = _states(gens, c["state"], once)
    keep = [a for a in c["rows"] if a.start < c["anchor"]] + replay
    keep = [a for a in keep if a.end > boundary - KEEP_BEFORE]
    new = pools if fresh else old
    return _make(plan, tz, d, new, t_star, state, keep, until=_until(d, now, tz),
                 stale=not fresh)


def _had_blocks(cache):
    """The cached tape came from more than one slot (time blocks): a Delete Block back to the
    channel's own source keeps the programme on now too."""
    try:
        return len({r[1] for r in cache["rows"]}) > 1
    except (KeyError, TypeError, IndexError):
        return False


def _edit(plan, tz, d, pools, cache, now, seen=None):
    """A rule edit of a time-block channel on the day of its freeze: the
    tape up to the end of the programme on now stays, and each slot still in the plan goes on
    from its cursor. The other slots start fresh. None when this does not apply (a new
    start)."""
    c = _open(cache, plan, now, any_rev=True)
    if c is None or c["rev"] == plan.rev or c["date"] != d:
        return None
    rows = c["rows"]
    t_star = next((a.start for a in rows if a.start >= now), c["end"])
    gens = _gens(plan, _pools(c), c["state"])
    for a in rows:                                    # the old tape gives each cursor at t*
        if c["anchor"] <= a.start < t_star and a.slot in gens:
            gens[a.slot].next()
    once = _once_at(plan, tz, rows, t_star, _once_of(c["state"]))
    state = _states(gens, {k: v for k, v in c["state"].items() if k in gens}, once)
    new = dict(_pools(c))
    new.update(pools or {})
    keep = [a for a in rows if a.start < t_star and a.end > now - KEEP_BEFORE]
    missing = any(s.key not in new for s in plan.slots)
    return _make(plan, tz, d, new, t_star, state, keep, until=_until(d, now, tz),
                 stale=pools is None or missing, seen=seen)


def _once_at(plan, tz, rows, t, old):
    """The ONCE mark at t under a new plan (a rule edit): the latest `once` start at or before
    t counts as served when the kept tape aired its slot since that start."""
    if not any(len(r) > 3 and r[3] for r in plan.times):
        return old
    tm = _Times(plan, tz, {r[2] for r in plan.times})
    i = tm.index(t)
    if i is None or not tm.once[i]:
        return old
    s, key = tm.t[i], tm.keys[i]
    return s if any(s <= a.start < t and a.slot == key for a in rows) else old


def _until(d, now, tz):
    return max(day_bounds(d + timedelta(days=2), tz) + 7200, now + HORIZON)


def _make(plan, tz, d, pools, anchor, state, keep, until, stale, seen=None):
    gens = _gens(plan, pools, state, seen)
    if not gens:
        return None
    anchor_state = _states(gens, state)
    rows, end, _once = _run(plan, gens, tz, anchor, until, _once_of(state))
    return {"v": VERSION, "rev": plan.rev, "tz": zone_name(tz), "date": d.isoformat(),
            "stale": stale, "anchor": anchor, "state": anchor_state, "end": end,
            "pools": {k: [list(e) for e in p.entries] for k, p in sorted(pools.items())},
            "rows": [_row_json(a) for a in keep + rows]}


def _extend(plan, tz, cache, c, now):
    """The cached tape, its end grown (deterministically, from the anchor) to now + HORIZON."""
    if c["end"] >= now + HORIZON:
        return cache
    gens = _gens(plan, _pools(c), c["state"])
    rows, end, _once = _run(plan, gens, tz, c["anchor"], now + HORIZON, _once_of(c["state"]))
    out = dict(cache)
    out["end"] = end
    out["rows"] = [_row_json(a) for a in c["rows"] if a.start < c["anchor"]] + \
        [_row_json(a) for a in rows]
    return out


def _open(cache, plan, now, any_rev=False):
    """The parsed cache when it can go on, else None (a new start). any_rev: also a cache of
    another rule revision (a rule edit, _edit)."""
    try:
        if not isinstance(cache, dict) or cache.get("v") != VERSION or \
                (cache.get("rev") != plan.rev and not any_rev):
            return None
        anchor = int(cache["anchor"])
        if now - anchor > MAX_AGE:
            return None
        rows = [_row_parse(r) for r in cache["rows"]]
        if not rows or now < rows[0].start:
            return None
        keys = {s.key for s in plan.slots}
        raw = {k: [Entry(*e) for e in v] for k, v in cache["pools"].items() if k in keys}
        # A damaged or hand-edited cache: an entry under MIN_DUR (or 0) would loop _run forever.
        if any(not isinstance(e.dur, (int, float)) or e.dur < MIN_DUR
               for v in raw.values() for e in v) or any(a.dur <= 0 for a in rows):
            return None
        date = datetime.strptime(cache["date"], "%Y-%m-%d").date()
        return {"anchor": anchor, "rows": rows, "raw": raw, "date": date, "rev": cache.get("rev"),
                "state": dict(cache.get("state") or {}), "stale": bool(cache.get("stale")),
                "end": int(cache["end"])}
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _pools(c):
    """The cached pools, built only when a freeze needs them (not on a same-day restart)."""
    return {k: Pool(v) for k, v in c["raw"].items()}


def _row_json(a):
    return [a.start, a.slot, [list(e) for e in a.unit.children]]


def _row_parse(r):
    kids = tuple(Entry(*e) for e in r[2])
    offs, t = [], 0
    for k in kids:
        offs.append(t)
        t += k.dur
    if not kids:
        raise ValueError("an airing with no items")      # a damaged cache
    f = kids[0]
    return Airing(int(r[0]), t, r[1], Unit(f.key, kids, t, tuple(offs), f.group, f.season, f.index))


# -- lookups

class Tape:
    """The airings of a cache, for the guide and for tuning. Needs no pool."""

    def __init__(self, airings):
        self.rows = list(airings)
        self.starts = [a.start for a in self.rows]

    @classmethod
    def of(cls, cache):
        return cls(_row_parse(r) for r in (cache or {}).get("rows", []))

    @property
    def end(self):
        return self.rows[-1].end if self.rows else None

    def at(self, t):
        """The airing index at t, or None outside the tape."""
        i = bisect.bisect_right(self.starts, t) - 1
        return i if i >= 0 and t < self.rows[i].end else None

    def lookup(self, t, tail=TAIL):
        """(airing index, child index, offset s in that child) to tune at t, or None. With
        under `tail` s of the child left, the next child (or airing) at 0."""
        i = self.at(t)
        if i is None:
            return None
        a = self.rows[i]
        local = t - a.start
        c = bisect.bisect_right(a.unit.offsets, local) - 1
        off = local - a.unit.offsets[c]
        if a.unit.children[c].dur - off < tail:
            if c + 1 < len(a.unit.children):
                return i, c + 1, 0
            if i + 1 < len(self.rows):
                return i + 1, 0, 0
        return i, c, off

    def next_playable(self, i, c, bad, limit=8):
        """The first (airing, child) from (i, c) on whose entry key is not in bad, trying at
        most `limit` children (a tune that fails goes to the next item); None if none."""
        for _ in range(limit):
            if i >= len(self.rows):
                return None
            kids = self.rows[i].unit.children
            if c >= len(kids):
                i, c = i + 1, 0
                continue
            if kids[c].key not in bad:
                return i, c
            c += 1
        return None

    def window(self, t0, t1):
        """The airings that overlap [t0, t1), in order (guide rows)."""
        i = max(0, bisect.bisect_right(self.starts, t0) - 1)
        j = bisect.bisect_left(self.starts, t1)
        return [a for a in self.rows[i:j] if a.end > t0]

    def child_start(self, i, c):
        a = self.rows[i]
        return a.start + a.unit.offsets[c]
