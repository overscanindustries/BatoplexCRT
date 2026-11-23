"""Channels.

- channels.json in the data directory: read, validate, atomic edits (as radio.py). One file
  for the box; each profile sees only the channels with content it may see.
- The rule model: a validated Channel, and its schedule.Plan.
- Pool collection as a plan: the shared source queries of all channels (each fetched once
  for each profile and freeze), and the pools of a channel from their results.
- "Make Channels for Me": proposals from library summaries, as a pure function.

Channel names are user text: they are never logged. A fault is logged by channel position
and a reason code only.
"""
import hashlib
import json
import os
import re
import secrets
import time
from collections import namedtuple

from . import config, log, schedule

L = log.get("channels")
FILE = "channels.json"
VERSION = 1
MAX_BYTES = 1024 * 1024       # 999 wizard channels are about 350 KB
MAX_CHANNELS = 999           # no practical limit: users add as many as they want
MAX_BLOCKS = 48
WIZARD_BLOCKS = 8             # more time blocks than this are file only
FIRST_NUMBER = 2
LAST_NUMBER = 999
NAME_MAX = 16                 # proposal names: 16 characters, cut at a word
NAME_FILE_MAX = 40            # a name from the file is cut to this
SOURCE_TYPES = ("show", "genre", "network", "collection", "playlist", "library", "theme")
ORDERS = schedule.MODES
MANAGED = ("id", "number", "name", "order", "block", "source", "blocks", "off")   # keys the wizard writes
THEME_SECTIONS = 64           # [server, section] pairs of one theme source

_cache = {"key": None, "lineup": None}


def path():
    return os.path.join(config.data_dir(), FILE)


class Refused(Exception):
    """A change that wrote nothing; str(e) is the toast."""


def clean(s, limit=None):
    """No control characters (pygame cannot draw them), no BOM, spaces collapsed."""
    if not isinstance(s, str):
        return ""
    s = " ".join("".join(c if c.isprintable() else " " for c in s.replace("﻿", "")).split())
    return s[:limit].strip() if limit else s


# -- sources

def norm_source(s):
    """A clean source dict, or None when it is not valid. Keys: type, server ("" = the home
    server), and id, title, section, genre, network as the type needs. A theme:
    theme (id), kind ("show" | "movie"; none = both), sections ([[server, section], ...])."""
    if not isinstance(s, dict) or s.get("type") not in SOURCE_TYPES:
        return None
    t = s["type"]
    if t == "theme":
        return _norm_theme(s)
    out = {"type": t, "server": clean(s.get("server"), 64) if isinstance(s.get("server"), str) else ""}
    v = s.get("id")
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    if isinstance(v, str) and clean(v, 64):
        out["id"] = clean(v, 64)
    if clean(s.get("title"), 200):
        out["title"] = clean(s.get("title"), 200)
    sec = s.get("section")
    if isinstance(sec, str) and sec.isdigit():
        sec = int(sec)
    if isinstance(sec, int) and not isinstance(sec, bool) and sec > 0:
        out["section"] = sec
    for k in ("genre", "network"):
        if clean(s.get(k), 100):
            out[k] = clean(s.get(k), 100)
    need = {"show": ("id", "title"), "collection": ("id", "title"), "playlist": ("id", "title"),
            "library": ("section",), "genre": ("section", "genre"), "network": ("section", "network")}[t]
    if t in ("show", "collection", "playlist"):
        return out if ("id" in out or "title" in out) else None
    return out if all(k in out for k in need) else None


def _norm_theme(s):
    tid = s.get("theme")
    if not isinstance(tid, str) or not re.fullmatch(r"[a-z0-9_]{1,32}", tid):
        return None
    out = {"type": "theme", "server": "", "theme": tid}
    if s.get("kind") is not None:
        if s["kind"] not in ("show", "movie"):
            return None
        out["kind"] = s["kind"]
    secs = s.get("sections")
    if not isinstance(secs, list) or not secs:
        return None
    pairs = set()
    for p in secs:
        if not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str)):
            return None
        sec = int(p[1]) if isinstance(p[1], str) and p[1].isdigit() else p[1]
        if _num(sec, 1, 10 ** 9) is None:
            return None
        pairs.add((clean(p[0], 64), sec))
    if len(pairs) > THEME_SECTIONS:
        return None
    out["sections"] = [list(p) for p in sorted(pairs)]
    if clean(s.get("title"), 200):
        out["title"] = clean(s.get("title"), 200)
    return out


def source_lib_type(src, default=None):
    """The library type that sets the order choices of a source: a theme is "movie" for kind
    movie, else "show"; another source: default."""
    if src.get("type") == "theme":
        return "movie" if src.get("kind") == "movie" else "show"
    return default


def source_key(src):
    """A unique, stable text key for a source: equal sources share one query and one slot.
    By id when there is one, else by title (any case)."""
    t, srv = src["type"], src.get("server", "")
    if t == "theme":
        return json.dumps([t, src["theme"], src.get("kind", ""), src["sections"]],
                          ensure_ascii=False, separators=(",", ":"))
    if t in ("show", "collection", "playlist"):
        ref = ["id", src["id"]] if "id" in src else ["title", src["title"].casefold()]
        parts = [t, srv] + ref + ([src["section"]] if t == "collection" and "section" in src else [])
    elif t == "library":
        parts = [t, srv, src["section"]]
    else:
        parts = [t, srv, src["section"], src[t].casefold()]
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))


# -- the rule model

class Channel:
    """One valid channel of the file. id: 6 hex characters; temporary ids (a channel with no
    id, until ensure_ids() writes one) start with "~" and must not key a cache."""

    def __init__(self, id, number, name, order, block, source, blocks, filters, index, file_only,
                 off=False):
        self.id, self.number, self.name, self.off = id, number, name, off
        self.order, self.block, self.source = order, block, source
        self.blocks, self.filters, self.index, self.file_only = blocks, filters, index, file_only

    @property
    def persistent(self):
        return not self.id.startswith("~")

    def _slot(self, src, order, block):
        if order is None and src["type"] in ("show", "playlist"):
            order = "order"
        key = json.dumps([source_key(src), order, block], ensure_ascii=False, separators=(",", ":"))
        return schedule.Slot(key, order, block)

    def slots(self):
        """[(Slot, source)] in file order, one for each distinct slot."""
        out, seen = [], set()
        rows = [(b["source"], b["order"], b["block"]) for b in self.blocks] if self.blocks \
            else [(self.source, self.order, self.block)]
        for src, order, block in rows:
            s = self._slot(src, order, block)
            if s.key not in seen:
                seen.add(s.key)
                out.append((s, src))
        return out

    def rule(self):
        """The fields that change the schedule (not the name or the number)."""
        bl = [{"start": b["start"], "days": sorted(b["days"]) if b["days"] else None,
               "source": b["source"], "order": b["order"], "block": b["block"]} for b in self.blocks]
        for r, b in zip(bl, self.blocks):
            if b.get("once"):                 # only when true: older revisions do not change
                r["once"] = True
        f = dict(self.filters)
        if "ratings" in f:
            f["ratings"] = sorted(f["ratings"])
        return {"source": self.source, "order": self.order, "block": self.block, "blocks": bl,
                "filters": f}

    def plan(self):
        rev = hashlib.sha256(json.dumps(self.rule(), sort_keys=True, ensure_ascii=False)
                             .encode("utf-8")).hexdigest()[:12]
        slots = self.slots()
        times = ()
        if self.blocks:
            times = tuple((b["start"], b["days"], self._slot(b["source"], b["order"], b["block"]).key,
                           bool(b.get("once"))) for b in self.blocks)
        return schedule.Plan(self.id, rev, tuple(s for s, _src in slots), times)


Lineup = namedtuple("Lineup", "channels faults error line stamp needs_ids")
"""channels: valid Channels in number order. faults: channels left out. error: None, or
"parse" (line: its line), "version", "size", "format", "read" - then no channel loads and every
write is refused. stamp: the file stamp it was read at (pass it to the edits). needs_ids: a
channel has no persistent id; call ensure_ids() before any cache is used."""


def _stamp(st):
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _hhmm(v):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", v) if isinstance(v, str) else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _num(v, lo, hi):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _channel(raw, index):
    """(Channel fields, reason code) for one raw entry; fields None when it has a fault."""
    if not isinstance(raw, dict):
        return None, "not an object"
    order = raw.get("order")
    if order is not None and order not in ORDERS:
        return None, "order"
    block = raw.get("block")
    if block is not None and _num(block, 1, 4) is None:
        return None, "block"
    src = None
    if raw.get("source") is not None:
        src = norm_source(raw["source"])
        if src is None:
            return None, "source"
    blocks = []
    rb = raw.get("blocks")
    if rb is not None:
        if not isinstance(rb, list) or not 0 < len(rb) <= MAX_BLOCKS:
            return None, "blocks"
        for b in rb:
            if not isinstance(b, dict):
                return None, "block entry"
            start, bsrc = _hhmm(b.get("start")), norm_source(b.get("source"))
            if start is None or bsrc is None:
                return None, "block entry"
            days = b.get("days")
            if days is not None:
                if not isinstance(days, list) or not days or \
                        any(not isinstance(x, str) or x.lower()[:3] not in schedule.DAYS for x in days):
                    return None, "days"
                days = frozenset(x.lower()[:3] for x in days)
            bo, bb = b.get("order", order), b.get("block", block)
            if (bo is not None and bo not in ORDERS) or (bb is not None and _num(bb, 1, 4) is None):
                return None, "block entry"
            blocks.append({"start": start, "days": days, "source": bsrc, "order": bo, "block": bb,
                           "once": b.get("once") is True})
    if src is None and not blocks:
        return None, "no source"
    filters = {}
    if raw.get("ratings") is not None:
        r = raw["ratings"]
        if not isinstance(r, list) or any(not isinstance(x, str) for x in r):
            return None, "ratings"
        filters["ratings"] = frozenset(clean(x).casefold() for x in r)
    if raw.get("years") is not None:
        y = raw["years"]
        if not (isinstance(y, list) and len(y) == 2 and all(_num(x, 1800, 3000) for x in y)
                and y[0] <= y[1]):
            return None, "years"
        filters["years"] = (y[0], y[1])
    for k in ("min_minutes", "max_minutes"):
        if raw.get(k) is not None:
            if _num(raw[k], 0, 100000) is None:
                return None, k
            filters[k] = raw[k]
    name = clean(raw.get("name"), NAME_FILE_MAX)
    if not name:
        s0 = src or blocks[0]["source"]
        name = clean(s0.get("title") or s0.get("genre") or s0.get("network") or "", NAME_FILE_MAX).upper()
    file_only = bool(filters) or len(blocks) > WIZARD_BLOCKS
    cid = raw.get("id")
    cid = cid.lower() if isinstance(cid, str) and re.fullmatch(r"[0-9a-fA-F]{6}", cid) else None
    return dict(id=cid, number=_num(raw.get("number"), 1, LAST_NUMBER), name=name, order=order,
                block=block, source=src, blocks=blocks, filters=filters, index=index,
                file_only=file_only, off=raw.get("off") is True), None


def parse(text, stamp=None):
    """A Lineup from the file text. Pure."""
    if len(text.encode("utf-8", "surrogateescape")) > MAX_BYTES:
        return Lineup([], 0, "size", None, stamp, False)
    try:
        doc = json.loads(text.lstrip("﻿"))
    except ValueError as e:
        return Lineup([], 0, "parse", getattr(e, "lineno", None), stamp, False)
    if not isinstance(doc, dict) or not isinstance(doc.get("channels", []), list):
        return Lineup([], 0, "format", None, stamp, False)
    if doc.get("version", VERSION) != VERSION:
        return Lineup([], 0, "version", None, stamp, False)
    raws = doc.get("channels", [])
    faults, found, ids = 0, [], set()
    for i, raw in enumerate(raws):
        if len(found) >= MAX_CHANNELS:
            faults += len(raws) - i
            L.info("channels.json: %d channels left out (over %d)", len(raws) - i, MAX_CHANNELS)
            break
        f, why = _channel(raw, i)
        if f is None:
            faults += 1
            L.info("channels.json: channel %d left out (%s)", i + 1, why)
            continue
        if f["id"] in ids:                            # a second channel with one id
            faults += 1
            L.info("channels.json: channel %d left out (id used before)", i + 1)
            continue
        if f["id"]:
            ids.add(f["id"])
        found.append(f)
    needs = False
    for f in found:                                   # temporary ids: unique, not hex
        if not f["id"]:
            needs = True
            f["id"] = "~%05d" % f["index"]
    used = set()
    for f in found:                                   # the earlier channel keeps a number
        if f["number"] is not None and f["number"] not in used:
            used.add(f["number"])
        else:
            f["number"] = None
    for f in found:
        if f["number"] is None:
            f["number"] = free_number(used)
            if f["number"] is None:
                faults += 1
                continue
            used.add(f["number"])
    chans = sorted((Channel(**f) for f in found if f["number"] is not None), key=lambda c: c.number)
    return Lineup(chans, faults, None, None, stamp, needs)


def load():
    """The Lineup of the file now; read again only when its stamp changes (as radio)."""
    p = path()
    try:
        st = os.stat(p)
    except OSError:
        _cache.update(key=None, lineup=None)
        return Lineup([], 0, None, None, None, False)
    key = _stamp(st)
    if key == _cache["key"]:
        return _cache["lineup"]
    try:
        if st.st_size > MAX_BYTES:
            lu = Lineup([], 0, "size", None, key, False)
        else:
            with open(p, "rb") as f:
                lu = parse(f.read(MAX_BYTES + 1).decode("utf-8", "replace"), key)
    except OSError as e:
        L.info("channels.json not read: %s", e.strerror or e.__class__.__name__)
        lu = Lineup([], 0, "read", None, key, False)
    if lu.error:
        L.info("channels.json not loaded (%s%s)", lu.error, " line %d" % lu.line if lu.line else "")
    _cache.update(key=key, lineup=lu)
    return lu


def free_number(used, start=FIRST_NUMBER):
    """The first number from `start` to LAST_NUMBER that is not used; None when all are."""
    for n in range(start, LAST_NUMBER + 1):
        if n not in used:
            return n
    return None


# -- edits: read again, check the stamp, change, one atomic replace

def _read_doc(stamp):
    """(doc, line end text) of the file now. Refused when the file changed since `stamp`
    (pass the Lineup stamp; None skips the check), does not parse or has another version."""
    p = path()
    try:
        st = os.stat(p)
    except FileNotFoundError:
        if stamp is not None:
            raise Refused("channels.json changed")
        return {"version": VERSION, "channels": []}
    except OSError:
        raise Refused("Can't change channels.json")
    if stamp is not None and _stamp(st) != stamp:
        raise Refused("channels.json changed")
    if st.st_size > MAX_BYTES:
        raise Refused("channels.json is too large")
    try:
        with open(p, "rb") as f:
            doc = json.loads(f.read().decode("utf-8").lstrip("﻿"))
    except (OSError, ValueError):
        raise Refused("channels.json has an error")
    if not isinstance(doc, dict) or not isinstance(doc.get("channels", []), list):
        raise Refused("channels.json has an error")
    if doc.get("version", VERSION) != VERSION:
        raise Refused("channels.json is from a newer version")
    doc.setdefault("channels", [])
    return doc


def _write(doc):
    """Atomic as radio._write: a temporary file in the same directory, fsync, os.replace; the
    mode stays and a symlink stays (its target changes)."""
    data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_BYTES:
        raise Refused("channels.json is full")
    try:
        config.atomic_write(os.path.realpath(path()), data, prefix=".channels-")
    except OSError as e:
        L.info("channels.json not written: %s", e.strerror or e.__class__.__name__)
        raise Refused("Can't save channels")
    _cache.update(key=None, lineup=None)


def _ids(doc):
    return {r["id"].lower() for r in doc["channels"]
            if isinstance(r, dict) and isinstance(r.get("id"), str)}


def new_id(used, token=None):
    """6 hex characters not in used."""
    token = token or (lambda: secrets.token_hex(3))
    while True:
        i = token()
        if i not in used:
            return i


def _assign_ids(doc, token=None):
    """Give each object entry with no valid id, or with an id an earlier entry has, a new id.
    Returns True when an id changed."""
    seen, changed = set(), False
    all_ids = _ids(doc)
    for r in doc["channels"]:
        if not isinstance(r, dict):
            continue
        cid = r.get("id")
        ok = isinstance(cid, str) and re.fullmatch(r"[0-9a-fA-F]{6}", cid) and cid.lower() not in seen
        if not ok:
            cid = new_id(all_ids | seen, token)
            r["id"] = cid
            all_ids.add(cid)
            changed = True
        seen.add(r["id"].lower())
    return changed


def edit(fn, stamp, token=None):
    """Read the file again, apply fn(doc) (it changes doc["channels"] in place), give missing
    ids, write once. Entries fn does not touch stay as they are, invalid ones too; unknown keys
    stay. Returns what fn returned. Raises Refused (nothing written)."""
    doc = _read_doc(stamp)
    before = len(doc["channels"])
    out = fn(doc)
    _assign_ids(doc, token)
    if len(doc["channels"]) > max(before, MAX_CHANNELS):
        raise Refused("Channels are full")
    _write(doc)
    return out


def _find(doc, cid):
    for r in doc["channels"]:
        if isinstance(r, dict) and isinstance(r.get("id"), str) and r["id"].lower() == cid:
            return r
    return None


def ensure_ids(stamp, token=None):
    """Write persistent ids for channels with none (before any cache is used). False when
    nothing needed one."""
    doc = _read_doc(stamp)
    if not _assign_ids(doc, token):
        return False
    _write(doc)
    return True


def save_channel(ch, stamp, token=None):
    """Add or change one channel: ch is a dict of MANAGED keys (a value None removes the key).
    With an id in the file, that object changes and keeps its other keys and its place; else
    it is added at the end. Returns the channel id."""
    def fn(doc):
        r = _find(doc, ch.get("id")) if ch.get("id") else None
        if r is None:
            r = {}
            doc["channels"].append(r)
        for k in MANAGED:
            if k in ch:
                if ch[k] is None:
                    r.pop(k, None)
                else:
                    r[k] = ch[k]
        if not r.get("id"):
            r["id"] = new_id(_ids(doc), token)
        return r["id"]
    return edit(fn, stamp, token)


def add_channels(chs, stamp, token=None):
    """Add several channels in one write (Make Channels for Me). Returns their ids."""
    def fn(doc):
        out = []
        for ch in chs:
            r = {k: ch[k] for k in MANAGED if ch.get(k) is not None}
            r["id"] = new_id(_ids(doc), token)
            doc["channels"].append(r)
            out.append(r["id"])
        return out
    return edit(fn, stamp, token)


def delete_channel(cid, stamp):
    def fn(doc):
        r = _find(doc, cid)
        if r is None:
            raise Refused("Channel not found")
        doc["channels"].remove(r)
    return edit(fn, stamp)


def clear(stamp):
    """Start Over: one edit that removes every channel (the other keys of the file stay)."""
    def fn(doc):
        doc["channels"] = []
    return edit(fn, stamp)


def swap_numbers(a, b, stamp):
    """Two channels exchange numbers in one file replace (Move Up, Move Down)."""
    def fn(doc):
        ra, rb = _find(doc, a), _find(doc, b)
        if ra is None or rb is None:
            raise Refused("Channel not found")
        ra["number"], rb["number"] = rb.get("number"), ra.get("number")
        for r in (ra, rb):
            if r["number"] is None:
                r.pop("number")
    return edit(fn, stamp)


def set_numbers(numbers, stamp):
    """Give several channels new numbers in one file replace (Move in Manage Channels).
    numbers: {channel id: number}."""
    def fn(doc):
        rows = [(_find(doc, cid), n) for cid, n in numbers.items()]
        if any(r is None for r, _n in rows):
            raise Refused("Channel not found")
        for r, n in rows:
            r["number"] = n
    return edit(fn, stamp)


# -- time blocks: Schedule in Manage Channels

DAY_CHOICES = (None, ("mon", "tue", "wed", "thu", "fri"), ("sat", "sun")) + \
    tuple((d,) for d in schedule.DAYS)
DAY_NAMES = ("Every Day", "Weekdays", "Weekends", "Mondays", "Tuesdays", "Wednesdays",
             "Thursdays", "Fridays", "Saturdays", "Sundays")
BASE_START = 4 * 60           # the channel's own source becomes a block from the day start
START_STEP = 30               # Starts: Left/Right in 30 min steps
START_DEFAULTS = (20 * 60, 6 * 60, 12 * 60, 17 * 60, 23 * 60, 9 * 60, 15 * 60, 1 * 60)


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def days_name(days):
    """"Every Day", "Weekends", "Mondays", or "Mon, Wed, Fri" for a set from the file."""
    key = tuple(d for d in schedule.DAYS if d in days) if days else None
    if key in DAY_CHOICES:
        return DAY_NAMES[DAY_CHOICES.index(key)]
    return ", ".join(d.capitalize() for d in key)


def blocks_clash(a_days, b_days):
    """Two blocks at one start clash when they share a day (None is every day)."""
    return a_days is None or b_days is None or bool(set(a_days) & set(b_days))


def free_start(blocks, days, skip=None, start=None, d=1):
    """A start with no clash for a block on `days`: the next from `start` in direction d, in
    START_STEP steps; with no start, the first free of START_DEFAULTS. blocks: parsed blocks;
    skip: the index of the block being edited. None when every start clashes."""
    if not blocks:                      # the first block: save_block adds the BASE_START
        blocks = [{"start": BASE_START, "days": None}]   # every-day block too
        skip = None
    used = [b["start"] for k, b in enumerate(blocks) if k != skip and blocks_clash(b["days"], days)]
    if start is None:
        return next((m for m in START_DEFAULTS if m not in used), None)
    m = start
    for _k in range(24 * 60 // START_STEP):
        m = (m + d * START_STEP) % (24 * 60)
        if m not in used:
            return m
    return None


def block_raw(start, days, source, order, once=False, block=None):
    """One block as the file keeps it. once: the block airs one item (a movie window)."""
    r = {"start": hhmm(start), "source": source}
    if days:
        r["days"] = [d for d in schedule.DAYS if d in days]
    if order:
        r["order"] = order
    if block:
        r["block"] = block
    if once:
        r["once"] = True
    return r


def save_block(cid, index, block, stamp):
    """Add (index None) or change one time block of a channel; block: {"start" (minutes),
    "days" (None or names), "source", "order"}. A channel with no blocks first gets its own
    source as a block from BASE_START, so it still plays outside the new block. Other keys of
    a block and of the channel stay. Returns the block index."""
    def fn(doc):
        r = _find(doc, cid)
        if r is None:
            raise Refused("Channel not found")
        new = block_raw(block["start"], block.get("days"), block["source"], block.get("order"),
                        block.get("once"))
        bl = r.get("blocks")
        if not isinstance(bl, list) or not bl:
            if not isinstance(r.get("source"), dict):
                raise Refused("Channel not found")
            bl = r["blocks"] = [block_raw(BASE_START, None, r["source"], r.get("order"))]
            if isinstance(r.get("block"), int):
                bl[0]["block"] = r["block"]
        if index is None:
            bl.append(new)
            return len(bl) - 1
        if not 0 <= index < len(bl) or not isinstance(bl[index], dict):
            raise Refused("channels.json changed")
        old = bl[index]
        for k in ("days", "order", "once"):
            if k not in new:
                old.pop(k, None)
        if new["source"] != old.get("source"):
            old.pop("block", None)            # a block size of the old source does not carry
        old.update(new)
        return index
    return edit(fn, stamp)


def delete_block(cid, index, stamp):
    """Remove one time block. One block left for every day: the channel plays its source all
    day again (no blocks)."""
    def fn(doc):
        r = _find(doc, cid)
        bl = r.get("blocks") if r is not None else None
        if not isinstance(bl, list) or not 0 <= index < len(bl):
            raise Refused("channels.json changed")
        bl.pop(index)
        if len(bl) == 1 and isinstance(bl[0], dict) and not bl[0].get("days"):
            last = r.pop("blocks")[0]
            r["source"] = last["source"]
            for k in ("order", "block"):
                if k in last:
                    r[k] = last[k]
                else:
                    r.pop(k, None)
        elif not bl:
            r.pop("blocks")
    return edit(fn, stamp)


def reorder(chans, ids):
    """Move: the channels keep their set of numbers and take them in the new order `ids`.
    chans: the Channels in number order. Returns {id: new number} for EVERY channel: a
    duplicate number in the file gets a free number only in memory, so a channel whose shown
    number did not change must still be written. Pure."""
    nums = sorted(c.number for c in chans)
    return dict(zip(ids, nums))


def step_number(n, used, d):
    """The next free number from n in direction d (+1 or -1), FIRST_NUMBER to LAST_NUMBER;
    n when there is none."""
    k = n + d
    while FIRST_NUMBER <= k <= LAST_NUMBER:
        if k not in used:
            return k
        k += d
    return n


def cache_key(account, profile):
    """A file name part for the channel caches of one account and profile (the
    account owner's profile key is "default" for every account)."""
    return hashlib.sha256(("%s\n%s" % (account or "", profile or "")).encode("utf-8")).hexdigest()[:16]


# -- pool collection

class Query(namedtuple("Query", "key kind server id title section value canonical repeats "
                                "cap read_cap show_cap theme media sections")):
    """One shared source fetch. The fetch layer (a later landing) implements:

        fetch(query, deadline) -> [schedule.Entry] or None

    - server: sources.strict(server) only, never the Home fallback ("" = the home server).
    - id first; else title (any case) on that server only (and in `section` when given). More
      than one match, or none: None.
    - kind "show": allLeaves. "library": /library/sections/{section}/all (episodes for a show
      library, movies for a movie library), pages of 200. "genre" / "network": the same with
      the filter (value); fallback: the leaves of the first show_cap shows by title sort.
      "collection" / "playlist": items in server order, a show becomes its leaves in order.
    - kind "theme": the items of theme `theme` (themes.py) in each of `sections`
      ((server, section) pairs), only shows or movies when `media` says so; a show becomes the
      leaves of at most show_cap shows in a fixed seeded order.
    - Read at most read_cap items. Entries from schedule.entry(item, server). Any failure
      or a passed deadline: None (never a part list).
    The order and the cap that the schedule sees come from schedule.build_pool (canonical,
    repeats, cap), so they do not depend on the server's order for library sources."""
    __slots__ = ()


def query_for(src):
    t = src["type"]
    return Query(key=source_key(src), kind=t, server=src.get("server", ""), id=src.get("id", ""),
                 title=src.get("title", ""), section=src.get("section"),
                 value=src.get("genre") or src.get("network") or "",
                 canonical=t in ("library", "genre", "network", "theme"), repeats=t == "playlist",
                 cap=schedule.MAX_POOL, read_cap=2 * schedule.MAX_POOL,
                 show_cap=60 if t == "theme" else 40, theme=src.get("theme", ""),
                 media=src.get("kind"),
                 sections=tuple(tuple(p) for p in src.get("sections", ())))


def plan_queries(channels):
    """The unique queries of all channels, sorted by key (one fetch each for each profile and
    freeze, whatever the number of channels that use them)."""
    qs = {}
    for ch in channels:
        for _slot, src in ch.slots():
            q = query_for(src)
            qs.setdefault(q.key, q)
    return [qs[k] for k in sorted(qs)]


def collect(queries, fetch, previous=None, deadline=None, clock=time.monotonic, skipped=None,
            workers=1, done=None):
    """Run fetch for each query, at most `workers` at once (daemon threads; the queries start
    in list order). A failed query keeps its result from `previous` (the last complete fetch)
    when there is one. Returns (results: {query key: [Entry]}, failed: [query key]), both in
    query order whatever the finish order. skipped: a list that gets the keys not fetched
    because the deadline passed (a part of failed). done(query, result or None, skipped): on
    the caller's thread as each query ends, in finish order."""
    def one(q):
        if deadline is not None and clock() >= deadline:
            return None, True
        try:
            return fetch(q, deadline), False
        except Exception as e:                        # a fetch bug must not stop the others
            L.info("channel source fetch failed: %s", e.__class__.__name__)
            return None, False
    got = {}
    if workers <= 1 or len(queries) <= 1:
        for q in queries:
            got[q.key] = one(q)
            if done is not None:
                done(q, *got[q.key])
    else:
        import queue
        import threading
        lock, nxt, ended = threading.Lock(), [0], queue.Queue()

        def run():
            while True:
                with lock:
                    i = nxt[0]
                    nxt[0] += 1
                if i >= len(queries):
                    return
                r = (None, False)
                try:
                    r = one(queries[i])
                finally:                              # the caller always gets an answer
                    ended.put((i, r))
        for _ in range(min(workers, len(queries))):
            threading.Thread(target=run, daemon=True).start()
        for _ in range(len(queries)):
            i, r = ended.get()
            got[queries[i].key] = r
            if done is not None:
                done(queries[i], *r)
    results, failed = {}, []
    for q in queries:
        r, skip = got[q.key]
        if skip and skipped is not None:
            skipped.append(q.key)
        if r is None:
            failed.append(q.key)
            if previous and q.key in previous:
                results[q.key] = previous[q.key]
        else:
            results[q.key] = list(r)
    return results, failed


def apply_filters(entries, filters):
    """The entries a channel's file rules allow (ratings, years, min/max minutes)."""
    if not filters:
        return list(entries)
    rt, yr = filters.get("ratings"), filters.get("years")
    lo = filters.get("min_minutes", 0) * 60
    hi = filters.get("max_minutes")
    hi = hi * 60 if hi is not None else None
    out = []
    for e in entries:
        if rt is not None and (e.rating or "").casefold() not in rt:
            continue
        if yr is not None and (e.year is None or not yr[0] <= e.year <= yr[1]):
            continue
        if e.dur < lo or (hi is not None and e.dur > hi):
            continue
        out.append(e)
    return out


def channel_pools(ch, results):
    """{slot key: schedule.Pool} for freeze(), or None when a source of the channel has no
    result (freeze then keeps the cached pools)."""
    out = {}
    for slot, src in ch.slots():
        q = query_for(src)
        es = results.get(q.key)
        if es is None:
            return None
        out[slot.key] = schedule.build_pool(apply_filters(es, ch.filters), canonical=q.canonical,
                                            repeats=q.repeats, cap=q.cap)
    return out


# -- Make Channels for Me
# No ceiling and no quotas: the only gate is quality, the per-kind minimums and a
# pool of at least POOL_MIN_MINUTES.

LIB_EPISODES, LIB_MOVIES = 20, 10
SHOW_EPISODES = 20
NET_SHOWS, NET_EPISODES = 2, 20
GENRE_MOVIES, GENRE_SHOWS, GENRE_SHARE = 10, 3, 0.6
COLL_ITEMS = 4
PLAYLIST_MINUTES = 180
POOL_MIN_MINUTES = 48 * 60    # 2 days of content, so a proposed channel does not repeat within a weekend
EP_MINUTES, MOVIE_MINUTES = 30, 100   # estimates when a summary has counts but no minutes


def _mins(d, count, per):
    """The minutes of a summary entry: its "minutes", else count x an estimate."""
    m = d.get("minutes")
    return m if isinstance(m, (int, float)) and m > 0 else count * per


NAME_WEAK = {"THE", "A", "AN", "OF", "AND", "&", "-", "+", "TO", "IN", "ON", "AT", "FOR", "WITH"}


def _cut(base):
    """base cut at a word to NAME_MAX, with no weak word ("THE", "OF") at the end; a trailing
    possessive goes only when 3+ words stay. "" when no word fits."""
    cut = base[:NAME_MAX + 1].rsplit(" ", 1)[0] if len(base) > NAME_MAX else base
    if not (0 < len(cut) <= NAME_MAX):
        return ""
    ws = cut.strip(" :,-").split()
    while len(ws) > 1 and ws[-1] in NAME_WEAK:
        ws.pop()
    if len(ws) > 2 and ws[-1].endswith(("'S", "’S")) and ws[-2] not in NAME_WEAK:
        ws.pop()                                 # "ROLLING STONE'S" stays: one word is too little
    return " ".join(ws).strip(" :,-")


def _short(base):
    """A name over NAME_MAX, shortened the way a TV guide would: "HEAD: SUB TITLE" with a 2-4
    word subtitle that starts with "THE" or has no weak word keeps the head and the subtitle's
    initials ("STAR TREK: TNG"), else the head when it fits; else the best cut at a word, with
    and without a leading "THE" (the one that keeps more real words, then the longer)."""
    head, sep, tail = base.partition(": ")
    head = head.strip(" :,-")
    words = [w for w in tail.split() if w[:1].isalnum()]
    if sep and head and not _real(head):          # "THE: A SUBTITLE": the subtitle is the name
        sub = tail.strip(" :,-")
        return (_short(sub) if len(sub) > NAME_MAX else sub) or base[:NAME_MAX].strip()
    if sep and head:
        if 2 <= len(words) <= 4 and (words[0] == "THE" or not any(w in NAME_WEAK for w in words)):
            cand = "%s: %s" % (head, "".join(w[0] for w in words))
            if len(cand) <= NAME_MAX:
                return cand
        if len(head) <= NAME_MAX:
            return head
    body = base.strip(" :,-")
    if body.startswith("THE ") and len(body) - 4 <= NAME_MAX:
        return body[4:]
    cands = [_cut(body)] + ([_cut(body[4:])] if body.startswith("THE ") else [])

    def score(c):
        return (_real(c), len(c))
    best = max(cands, key=score)
    if not _real(best):                           # only weak words fit: a hard cut, no "THE"
        hard = (body[4:] if body.startswith("THE ") else body)[:NAME_MAX].strip()
        return hard or base[:NAME_MAX]
    return best


def _real(s):
    """The number of words in s that are not weak."""
    return sum(1 for w in s.split() if w.strip(" :,-") and w.strip(" :,-") not in NAME_WEAK)


def make_name(title, used=()):
    """The source title in capitals, NAME_MAX characters at most (see _short); a name in
    `used` (any case) gets " 2", " 3"..."""
    base = clean(title).upper()
    if len(base) > NAME_MAX:
        base = _short(base)
    used = {u.casefold() for u in used}
    name, n = base, 1
    while name.casefold() in used:
        n += 1
        sfx = " %d" % n
        name = base[:NAME_MAX - len(sfx)].rstrip() + sfx
    return name


def propose(libraries, playlists=(), existing=()):
    """Proposed channels, numbered and named. Pure.

    libraries: pinned video libraries in pin order, each a dict:
        server, section, title, type ("show" | "movie"), episodes, movies, minutes,
        shows: [{id, title, episodes, network, minutes}], genres: [{genre, count, minutes}]
        (shows of the genre in a show library, movies in a movie library),
        collections: [{id, title, count, minutes}]
    playlists: [{server, id, title, minutes, video}]
    existing: the Channels of the file; their sources are not proposed again, their numbers
        and names are not used again.
    "minutes" may be missing: then count x EP_MINUTES (episodes, shows) or MOVIE_MINUTES.

    Returns [{"kind", "count", "what", "minutes", "score", "default", "channel": {number, name,
    order, source}}] in number order: every source that passes its kind's minimum and has
    POOL_MIN_MINUTES of content, with no ceiling, numbered in kind order (libraries, shows,
    networks, genres, collections, playlists). "default" is always False: the curated
    channels of themes.curate() are the first set; these are suggestions."""
    have = set()
    for ch in existing:
        for _slot, src in ch.slots():
            have.add(source_key(src))
    used_n = {ch.number for ch in existing}
    names = [ch.name for ch in existing]
    cand = []

    def add(kind, src, title, count, what, order, minutes):
        k = source_key(src)
        if k not in have and minutes >= POOL_MIN_MINUTES:
            have.add(k)
            cand.append((kind, src, title, count, what, order, int(minutes)))

    def lib_order(lib):
        return "blocks" if lib.get("type") == "show" else "shuffle"

    def show_mins(s):
        return _mins(s, s.get("episodes", 0), EP_MINUTES)

    for lib in libraries:
        eps, mov = lib.get("episodes", 0), lib.get("movies", 0)
        show = lib.get("type") == "show"
        if (show and eps >= LIB_EPISODES) or (lib.get("type") == "movie" and mov >= LIB_MOVIES):
            add("library", {"type": "library", "server": lib.get("server", ""), "section": lib["section"]},
                lib.get("title", ""), eps if show else mov, "episodes" if show else "movies",
                lib_order(lib), _mins(lib, eps if show else mov, EP_MINUTES if show else MOVIE_MINUTES))
    shows = sorted(((s, lib) for lib in libraries if lib.get("type") == "show"
                    for s in lib.get("shows", ()) if s.get("episodes", 0) >= SHOW_EPISODES),
                   key=lambda x: (-x[0]["episodes"], clean(x[0].get("title")).casefold(), str(x[0].get("id"))))
    for s, lib in shows:
        add("show", {"type": "show", "server": lib.get("server", ""), "id": str(s["id"]),
                     "title": clean(s.get("title"))}, s.get("title", ""), s["episodes"], "episodes",
            "order", show_mins(s))
    nets = {}
    for lib in libraries:
        if lib.get("type") != "show":
            continue
        for s in lib.get("shows", ()):
            n = clean(s.get("network"))
            if n:
                k = (lib.get("server", ""), lib["section"], n.casefold())
                e = nets.setdefault(k, {"lib": lib, "name": n, "shows": 0, "episodes": 0, "minutes": 0})
                e["shows"] += 1
                e["episodes"] += s.get("episodes", 0)
                e["minutes"] += show_mins(s)
    nets = sorted((v for v in nets.values() if v["shows"] >= NET_SHOWS and v["episodes"] >= NET_EPISODES),
                  key=lambda v: (-v["episodes"], v["name"].casefold()))
    for v in nets:
        lib = v["lib"]
        add("network", {"type": "network", "server": lib.get("server", ""), "section": lib["section"],
                        "network": v["name"]}, v["name"], v["episodes"], "episodes", "blocks", v["minutes"])
    genres = []
    for lib in libraries:
        show = lib.get("type") == "show"
        total = len(lib.get("shows", ())) if show else lib.get("movies", 0)
        per = (lib.get("episodes", 0) * EP_MINUTES // max(1, total)) if show else MOVIE_MINUTES
        for g in lib.get("genres", ()):
            c = g.get("count", 0)
            if c < (GENRE_SHOWS if show else GENRE_MOVIES) or (total and c > GENRE_SHARE * total):
                continue
            genres.append((c, lib, clean(g.get("genre")), _mins(g, c, per)))
    genres.sort(key=lambda x: (-x[0], x[2].casefold(), x[1]["section"]))
    for c, lib, g, m in genres:
        add("genre", {"type": "genre", "server": lib.get("server", ""), "section": lib["section"],
                      "genre": g}, g, c, "shows" if lib.get("type") == "show" else "movies",
            lib_order(lib), m)
    colls = sorted(((c, lib) for lib in libraries for c in lib.get("collections", ())
                    if c.get("count", 0) >= COLL_ITEMS),
                   key=lambda x: (-x[0]["count"], clean(x[0].get("title")).casefold(), str(x[0].get("id"))))
    for c, lib in colls:
        add("collection", {"type": "collection", "server": lib.get("server", ""), "id": str(c["id"]),
                           "title": clean(c.get("title")), "section": lib["section"]},
            c.get("title", ""), c["count"], "items", "shuffle",
            _mins(c, c["count"], EP_MINUTES if lib.get("type") == "show" else MOVIE_MINUTES))
    pls = sorted((p for p in playlists if p.get("video") and p.get("minutes", 0) >= PLAYLIST_MINUTES),
                 key=lambda p: (-p["minutes"], clean(p.get("title")).casefold(), str(p.get("id"))))
    for p in pls:
        add("playlist", {"type": "playlist", "server": p.get("server", ""), "id": str(p["id"]),
                         "title": clean(p.get("title"))}, p.get("title", ""), p["minutes"] // 60,
            "hours", "order", p["minutes"])
    out = []
    for kind, src, title, count, what, order, minutes in cand:
        num = free_number(used_n)
        if num is None:
            break
        used_n.add(num)
        name = make_name(title, names)
        names.append(name)
        out.append({"kind": kind, "count": count, "what": what, "minutes": minutes,
                    "score": minutes, "default": False,
                    "channel": {"number": num, "name": name, "order": order,
                                "source": {k: v for k, v in src.items() if v != ""}}})
    out.sort(key=lambda o: o["channel"]["number"])
    return out


# -- Add a Channel: kind, source, summary with Save in focus

KINDS = ("theme", "show", "genre", "network", "collection", "playlist", "library")
KIND_ROWS = {"theme": "A Theme", "show": "A Show", "genre": "A Genre", "network": "A Network",
             "collection": "A Collection", "playlist": "A Playlist", "library": "A Library"}
KIND_NAMES = {"theme": "Theme", "show": "Show", "genre": "Genre", "network": "Network",
              "collection": "Collection", "playlist": "Playlist", "library": "Library"}
ORDER_NAMES = {"order": "In Order", "shuffle": "Shuffle", "blocks": "Block Shuffle"}


def catalog(libraries, playlists=(), themes=()):
    """Every source the wizard can offer, from the same summaries as propose(). Pure.
    themes: [{"source", "title", "count", "what"}] of the themes that fill (themes.catalog).
    Returns {kind: [{"source", "title", "count", "what", "lib_type", "lib"}]}: kinds with no
    source are left out; sources in title order (libraries in pin order)."""
    out = {k: [] for k in KINDS}

    def add(kind, src, title, count, what, lib):
        if count > 0 and clean(title):
            out[kind].append({"source": {k: v for k, v in src.items() if v != ""},
                              "title": clean(title), "count": count, "what": what,
                              "lib_type": source_lib_type(src, lib.get("type") if lib else None),
                              "lib": clean(lib.get("title")) if lib else ""})

    for t in themes:
        add("theme", t["source"], t["title"], t["count"], t["what"], None)

    for lib in libraries:
        show = lib.get("type") == "show"
        srv, sec = lib.get("server", ""), lib.get("section")
        n = lib.get("episodes", 0) if show else lib.get("movies", 0)
        add("library", {"type": "library", "server": srv, "section": sec}, lib.get("title", ""),
            n, "episodes" if show else "movies", lib)
        nets = {}
        for s in lib.get("shows", ()) if show else ():
            add("show", {"type": "show", "server": srv, "id": str(s.get("id")),
                         "title": clean(s.get("title"))}, s.get("title", ""),
                s.get("episodes", 0), "episodes", lib)
            net = clean(s.get("network"))
            if net and s.get("episodes", 0) > 0:
                nets.setdefault(net.casefold(), [net, 0])[1] += 1
        for net, count in nets.values():
            add("network", {"type": "network", "server": srv, "section": sec, "network": net},
                net, count, "shows", lib)
        for g in lib.get("genres", ()):
            add("genre", {"type": "genre", "server": srv, "section": sec,
                          "genre": clean(g.get("genre"))}, g.get("genre", ""), g.get("count", 0),
                "shows" if show else "movies", lib)
        for c in lib.get("collections", ()):
            add("collection", {"type": "collection", "server": srv, "id": str(c.get("id")),
                               "title": clean(c.get("title")), "section": sec},
                c.get("title", ""), c.get("count", 0), "items", lib)
    for p in playlists:
        if p.get("video"):
            add("playlist", {"type": "playlist", "server": p.get("server", ""),
                             "id": str(p.get("id")), "title": clean(p.get("title"))},
                p.get("title", ""), p.get("minutes", 0) // 60, "hours", None)
    for k in KINDS:
        if k != "library":
            out[k].sort(key=lambda e: (e["title"].casefold(), e["lib"].casefold()))
    return {k: v for k, v in out.items() if v}


def order_choices(kind, lib_type):
    """The orders the wizard offers for a source, the default first. A movie library or genre
    has one (no Order row)."""
    if kind in ("show", "playlist"):
        return ["order", "shuffle"]
    if kind == "collection":
        return ["shuffle", "order"]
    return ["blocks", "shuffle"] if lib_type == "show" else ["shuffle"]


def draft(entry, existing):
    """The new channel the summary starts from (smart defaults, so Save is one press): the
    source title as the name, the first free number, the default order. existing: the
    Channels of the file."""
    return {"number": free_number({c.number for c in existing}),
            "name": make_name(entry["title"], [c.name for c in existing]),
            "order": order_choices(entry["source"]["type"],
                                   source_lib_type(entry["source"], entry.get("lib_type")))[0],
            "source": dict(entry["source"])}


def suggestions(curated, proposed, used=()):
    """The Make Channels for Me list: the curated rows, then the propose()
    rows whose source no curated row has, numbered on after the curated ones. used: the
    numbers of the channels in the file."""
    keys = set()
    for p in curated:
        ch = p["channel"]
        for src in [ch["source"]] if ch.get("source") else [b["source"] for b in ch["blocks"]]:
            keys.add(source_key(norm_source(src)))
    mine = {p["channel"]["number"] for p in curated}
    used = set(used) | mine
    out = list(curated)
    for p in proposed:
        if source_key(norm_source(p["channel"]["source"])) in keys:
            continue
        n = free_number(used, max(mine) + 1 if mine else FIRST_NUMBER)
        if n is None:
            break
        used.add(n)
        out.append(dict(p, channel=dict(p["channel"], number=n)))
    return out
