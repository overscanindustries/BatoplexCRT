"""Curated themes for Make Channels for Me.

Pure: no I/O, no network, no `random`. The input is the light library index of
lineup.index(): rows of shows and movies with at most 2 genre tags each, and for each format
genre of a section the id set of a genre-filter query (list replies carry at most 2 tags, so a
cartoon can miss "Animation" in its tags).

- classify(): the format, rating band, genres and decade of one row.
- THEMES: the themed channels in priority order, then the hidden pattern parts and variants.
- analyse(): the members of each theme over all sections (worker; bounded memory).
- curate(): the first set of 10 themes and 2 marathons, then the themes that fill but were not
  picked (Off), as channels.propose() rows.
- members(): the rows of one theme in one section index (the theme source fetch).

Titles are user text: never logged.
"""
import math
import re
from array import array
from collections import namedtuple

from . import channels

Row = namedtuple("Row", "id type title year rating genres dur leaves viewed views last studio score",
                 defaults=(0.0,))
"""One show or movie of a list reply: dur in ms (a show: its typical episode), leaves (shows:
episodes), viewed (shows: watched episodes), views and last (lastViewedAt) when Plex sends them,
score: Plex's audienceRating (0-10; TMDB for shows), 0.0 when there is none."""

Cls = namedtuple("Cls", "format adult genres decade rating act")
"""format: one of FORMATS or None. act: in an action genre (True/False), None when the
section's action sets are not complete."""

MIN_MINUTES = 48 * 60         # minimum fill: 48 h ...
MIN_SHOWS, MIN_MOVIES = 5, 30  # ... plus 5 shows or 30 movies
SLOT_MOVIES = 14              # a movie slot in the pattern needs 14 movies
PART_SHOWS = 5                # a pattern part needs 5 shows, else the main pool plays
RANK_CAP = 150 * 60           # hours count for the rank up to 150 h
PICKS = 10
MAX_DECADES = 2
OVERLAP = 0.7
MARATHONS = 2
MARATHON_EPISODES = 20
EP_MINUTES, MOVIE_MINUTES = 30, 100   # when a row has no duration
SITCOM_MS = 35 * 60000        # a sitcom: a typical episode under 35 min

# -- names

SYNONYMS = {"sci fi": "science fiction", "scifi": "science fiction", "talk show": "talk",
            "children": "kids", "kid": "kids", "stand up": "standup",
            "stand up comedy": "standup", "home": "home and garden", "garden": "home and garden",
            "reality tv": "reality", "documentaries": "documentary", "docuseries": "documentary",
            "game shows": "game show", "gameshow": "game show", "martial art": "martial arts",
            "biopic": "biography"}
FORMATS = (("anime", frozenset({"anime"})), ("animation", frozenset({"animation"})),
           ("standup", frozenset({"standup"})), ("documentary", frozenset({"documentary"})),
           ("kids", frozenset({"kids"})),
           ("reality", frozenset({"reality", "game show", "talk", "food", "travel",
                                  "home and garden"})))
ACTION = frozenset({"action", "adventure", "science fiction", "fantasy", "martial arts"})
LOW = frozenset({"tv-y", "tv-y7", "tv-y7-fv", "tv-g", "tv-pg", "g", "pg", "u", "pg-13", "12",
                 "12a", "m"})      # cartoons; under ADULT, so PG-13 animation airs somewhere
UNRATED = frozenset({"", "nr", "not rated", "unrated"})
ADULT = frozenset({"tv-14", "tv-ma", "r", "nc-17", "x", "15", "16", "18", "r18", "r18+",
                   "ma15+"})
YOUNG = frozenset({"tv-y", "tv-y7", "tv-y7-fv"})


def names(title):
    """The normal genre names of one tag: casefold, "&" and "/" split, "-" as a space."""
    out = []
    for p in re.split(r"[&/]", (title or "").casefold().replace("-", " ")):
        p = " ".join(p.split())
        if p:
            out.append(SYNONYMS.get(p, p))
    return out


def norm_rating(r):
    """"TV-PG" -> "tv-pg"; a country prefix goes ("gb/15" -> "15")."""
    r = (r or "").strip().casefold()
    return " ".join(r.rsplit("/", 1)[-1].split())


def format_keys(genres):
    """({format: [genre keys]}, [action genre keys]) from a section's genre directory
    [(key, title)]: one OR query each (genre=K1,K2)."""
    fk, ak = {}, []
    for key, title in genres:
        ns = set(names(title))
        for f, fn in FORMATS:
            if ns & fn:
                fk.setdefault(f, []).append(key)
                break
        if ns & ACTION:
            ak.append(key)
    return fk, ak


def needs_action(fk, lib_title):
    """The action sets are read for a section with animation or anime."""
    return "anime" in fk or "animation" in fk or "anime" in (lib_title or "").casefold()


def classify(row, sets, lib_title=""):
    """(format, adult, genres, decade, rating, act) of one row. sets: {format: ids} of the
    section, and "action": ids (None: not complete; left out: the tags only). A library
    named for anime makes its items anime."""
    tags = set()
    for g in row.genres:
        tags.update(names(g))
    hint = "anime" in (lib_title or "").casefold()
    fmt = None
    for f, fn in FORMATS:
        if row.id in sets.get(f, ()) or tags & fn or (hint and f == "anime"):
            fmt = f
            break
    rating = norm_rating(row.rating)
    act = sets.get("action", ())
    act = None if act is None else (row.id in act or bool(tags & ACTION))
    return Cls(fmt, rating in ADULT, frozenset(tags), row.year // 10 * 10 if row.year else None,
               rating, act)


def classify_index(ix):
    """[(row, Cls)] of one section index, or [] when a format set (or the genre directory)
    is not complete: an incomplete Animation set would put cartoons on content channels."""
    if not ix.get("dir_ok") or any(not s["complete"] for s in ix["sets"].values()):
        return []
    sets = {f: s["ids"] for f, s in ix["sets"].items()}
    a = ix.get("action")
    if a is not None:
        sets["action"] = a["ids"] if a["complete"] else None
    return [(r, classify(r, sets, ix.get("title", ""))) for r in ix["rows"]
            if r.type in ("show", "movie")]


# -- themes

def _content(*gs):
    g = frozenset(gs)
    return lambda r, c: c.format is None and bool(c.genres & g)


def _sitcom(r, c):
    return r.type == "show" and c.format is None and "comedy" in c.genres and 0 < r.dur < SITCOM_MS


def _cartoon(r, c):
    return c.format == "animation" and (c.rating in LOW or c.rating in UNRATED)


def _anime(r, c):
    return c.format == "anime"


def _actionanim(r, c):
    return c.format in ("anime", "animation") and c.act is True


def _kids_day(r, c):
    return r.type == "show" and (c.format == "kids" or (c.format == "animation" and c.rating in YOUNG))


def _sitcom_family(r, c):
    return _sitcom(r, c) and c.rating in ("tv-g", "tv-pg")


def _decade(d):
    return lambda r, c: r.type == "show" and c.format is None and "news" not in c.genres \
        and c.decade == d


def _movie_decade(d):
    return lambda r, c: r.type == "movie" and c.format is None and c.decade == d


Theme = namedtuple("Theme", "id name kinds pattern test hidden")
"""kinds: "both" | "show" | "movie" (the items it may hold). pattern: the daily schedule
(_plan). hidden: a pattern part or a variant, never proposed alone."""

_comedy = _content("comedy")
THEMES = (
    Theme("comedy", "Comedy", "both", "movie20", _comedy, False),
    Theme("sitcoms", "Sitcoms", "show", "sitcoms", _sitcom, False),
    Theme("cartoons", "Cartoons", "both", "cartoons", _cartoon, False),
    Theme("drama", "Drama", "both", "movie20",
          _content("drama", "romance", "history", "war", "soap", "biography"), False),
    Theme("action", "Action", "both", "movie20", _content("action", "adventure", "martial arts"),
          False),
    Theme("scifi", "Sci-Fi", "both", "movie20", _content("science fiction", "fantasy"), False),
    Theme("docs", "Documentaries", "both", "movie20", lambda r, c: c.format == "documentary",
          False),
    Theme("crime", "Crime", "both", "movie20",
          _content("crime", "mystery", "thriller", "suspense", "film noir"), False),
    Theme("tv70", "70s TV", "show", "shows", _decade(1970), False),
    Theme("tv80", "80s TV", "show", "shows", _decade(1980), False),
    Theme("tv90", "90s TV", "show", "shows", _decade(1990), False),
    Theme("tv00", "2000s TV", "show", "shows", _decade(2000), False),
    Theme("family", "Family Movies", "movie", "movies",
          lambda r, c: r.type == "movie" and c.rating in ("g", "pg", "u")
          and c.format not in ("documentary", "standup"), False),
    Theme("horror", "Horror", "both", "horror", _content("horror"), False),
    Theme("anime", "Anime", "both", "anime", _anime, False),
    Theme("actionanim", "Action Animation", "both", "actionanim", _actionanim, False),
    Theme("late", "Late Night", "both", "late",
          lambda r, c: c.format is None and ((r.type == "show" and c.rating == "tv-ma")
                                             or (r.type == "movie" and c.rating == "r")), False),
    Theme("classic", "Classic Movies", "movie", "movies",
          lambda r, c: r.type == "movie" and c.format is None and bool(r.year) and r.year < 1970,
          False),
    Theme("kidsfam", "Kids & Family", "show", "kidsfam",
          lambda r, c: _kids_day(r, c) or _sitcom_family(r, c), False),
    Theme("classicanime", "Classic Anime", "both", "anime",
          lambda r, c: _anime(r, c) and bool(r.year) and r.year < 2005, False),
    Theme("reality", "Reality", "both", "blocks", lambda r, c: c.format == "reality", False),
    Theme("adultanim", "Adult Animation", "both", "adultanim",
          lambda r, c: c.format == "animation" and c.adult, False),
    Theme("western", "Westerns", "both", "movie20", _content("western"), False),
    Theme("mov80", "80s Movies", "movie", "movies", _movie_decade(1980), False),
    Theme("mov90", "90s Movies", "movie", "movies", _movie_decade(1990), False),
    Theme("standup", "Stand-Up", "both", "shuffle", lambda r, c: c.format == "standup", False),
    # the Kids fallback of Kids & Family, the pattern parts and the split variants
    Theme("kids", "Kids", "show", "shows", lambda r, c: r.type == "show" and c.format == "kids",
          True),
    Theme("cartoons_classic", "Classic Cartoons", "show", "shows",
          lambda r, c: r.type == "show" and _cartoon(r, c) and bool(r.year) and r.year < 2000, True),
    Theme("sitcoms_family", "Family Sitcoms", "show", "shows", _sitcom_family, True),
    Theme("anime_mature", "Anime After Dark", "show", "shows",
          lambda r, c: r.type == "show" and _anime(r, c) and c.rating in ("tv-14", "tv-ma"), True),
    Theme("actionanim_late", "Action Animation", "show", "shows",
          lambda r, c: r.type == "show" and _actionanim(r, c) and c.rating in ("tv-14", "tv-ma"),
          True),
    Theme("kids_day", "Kids", "show", "shows", _kids_day, True),
    Theme("comedy_rest", "Comedy", "both", "movie20", lambda r, c: _comedy(r, c) and not _sitcom(r, c),
          True),
    Theme("anime_new", "Anime", "both", "anime",
          lambda r, c: _anime(r, c) and not (r.year and r.year < 2005), True),
)
BY_ID = {t.id: t for t in THEMES}
BASE = tuple(t for t in THEMES if not t.hidden)
SPLITS = {"sitcoms": ("comedy", "comedy_rest"), "classicanime": ("anime", "anime_new")}
MIXES = ("actionanim", "kidsfam")
DECADES = ("tv70", "tv80", "tv90", "tv00")
SLOT_PATTERNS = ("movie20", "cartoons", "horror", "anime", "actionanim", "late", "adultanim")
FORMAT_THEMES = ("cartoons", "kidsfam", "kids", "actionanim", "anime", "classicanime",
                 "adultanim", "docs", "reality", "standup")
MOVIE_NAMES = {"cartoons": "Cartoon Movies", "docs": "Documentaries", "standup": "Stand-Up",
               "late": "Late Movies", "western": "Western Movies",
               "actionanim": "Animated Action", "classicanime": "Classic Anime",
               "adultanim": "Adult Animation"}   # 16 characters at most (NAME_MAX)
# cable-guide order: "@movies" holds the channels that play only movies
ORDER = ("comedy", "sitcoms", "drama", "action", "scifi", "crime", "horror", "western",
         "@movies", "family", "classic", "mov80", "mov90", "tv70", "tv80", "tv90", "tv00",
         "late", "cartoons", "kidsfam", "kids", "actionanim", "anime", "classicanime",
         "adultanim", "docs", "reality", "standup")


def members(ix, theme, media=None, classified=None):
    """The rows of theme `theme` in one section index, shows or movies only when media says
    so. None when the index is not complete (rows or format sets): never a part pool.
    classified: classify_index(ix) when the caller keeps it (one pass for all themes)."""
    t = BY_ID.get(theme)
    if t is None or not index_complete(ix):
        return None
    cl = classified if classified is not None else classify_index(ix)
    return [r for r, c in cl if (media is None or r.type == media)
            and (t.kinds == "both" or r.type == t.kinds) and t.test(r, c)]


def index_complete(ix):
    a = ix.get("action")
    return bool(ix.get("rows_complete") and ix.get("dir_ok")
                and all(s["complete"] for s in ix["sets"].values())
                and (a is None or a["complete"]))


# -- analysis (worker)

def row_minutes(r):
    if r.type == "show":
        return int(r.leaves * (r.dur / 60000.0 if r.dur else EP_MINUTES))
    return int(r.dur / 60000.0) if r.dur else MOVIE_MINUTES


class Analysis:
    """The members of every theme over all sections, as item numbers (small memory for
    tens of thousands of items). refs: [(server, section)]; for item i: ref[i], show[i]
    (1 show, 0 movie), mins[i]. themes: {theme id: array of items}. shows: marathon
    candidates (server, id, title, leaves, viewed, last, minutes)."""

    def __init__(self):
        self.refs, self.ref, self.show = [], array("i"), bytearray()
        self.mins = array("l")
        self.themes = {t.id: array("i") for t in THEMES}
        self.shows = []


def analyse(indexes):
    a = Analysis()
    for ix in indexes:
        if ix.get("kind") == "show":
            for r in ix["rows"]:
                if r.type == "show" and r.leaves >= MARATHON_EPISODES:
                    a.shows.append((ix["server"], r.id, r.title, r.leaves, r.viewed, r.last,
                                    row_minutes(r), r.score))
        cl = classify_index(ix)
        if not cl:
            continue
        ri = len(a.refs)
        a.refs.append((ix["server"], ix["section"]))
        for r, c in cl:
            i = len(a.mins)
            a.ref.append(ri)
            a.show.append(1 if r.type == "show" else 0)
            a.mins.append(row_minutes(r))
            for t in THEMES:
                if (t.kinds == "both" or r.type == t.kinds) and t.test(r, c):
                    a.themes[t.id].append(i)
    return a


def _items(a, tid, kind):
    """The items of a theme for a shape: "both", "show" or "movie"."""
    if kind == "both":
        return list(a.themes[tid])
    want = 1 if kind == "show" else 0
    return [i for i in a.themes[tid] if a.show[i] == want]


def _stats(a, tid):
    ns = nm = hs = hm = 0
    for i in a.themes[tid]:
        if a.show[i]:
            ns, hs = ns + 1, hs + a.mins[i]
        else:
            nm, hm = nm + 1, hm + a.mins[i]
    return ns, nm, hs, hm


def shape(a, t, tid=None):
    """"both", "show" or "movie": what the channel of theme t plays, or None when it does not
    fill (48 h, plus 5 shows or 30 movies; a movie slot needs 14 movies, else shows all day)."""
    ns, nm, hs, hm = _stats(a, tid or t.id)
    if t.kinds == "movie":
        return "movie" if nm >= MIN_MOVIES and hm >= MIN_MINUTES else None
    if t.kinds == "show":
        return "show" if ns >= MIN_SHOWS and hs >= MIN_MINUTES else None
    if t.pattern in SLOT_PATTERNS:
        if ns >= MIN_SHOWS and nm >= SLOT_MOVIES and hs + hm >= MIN_MINUTES:
            return "both"
        if ns >= MIN_SHOWS and hs >= MIN_MINUTES:
            return "show"
        if nm >= MIN_MOVIES and hm >= MIN_MINUTES:
            return "movie"
        return None
    return "both" if (ns >= MIN_SHOWS or nm >= MIN_MOVIES) and hs + hm >= MIN_MINUTES else None


def _part_ok(a, tid):
    return sum(1 for i in a.themes[tid] if a.show[i]) >= PART_SHOWS


class Cand:
    """One theme that fills: tid is the theme id its items come from (a split variant)."""

    def __init__(self, a, t, tid, kind, prio):
        self.t, self.tid, self.kind, self.prio = t, tid, kind, prio
        self.set(a, tid, kind)

    def set(self, a, tid, kind):
        self.tid, self.kind = tid, kind
        if self.t.id == "kidsfam":
            its = set(_items(a, "kids_day", "show")) | set(_items(a, "sitcoms_family", "show"))
        else:
            its = set(_items(a, tid, kind))
        self.items = its
        self.minutes = sum(a.mins[i] for i in its)

    def rank(self):
        return (-min(self.minutes, RANK_CAP), self.prio)


def _candidates(a):
    """(candidates in priority order, the Kids fallback of Kids & Family or None)."""
    out, kids = [], None
    children = {}
    for child, (_parent, rest) in SPLITS.items():
        t = BY_ID[child]
        if shape(a, t) and shape(a, BY_ID[rest]):
            children[child] = rest
    for prio, t in enumerate(BASE):
        tid = t.id
        if t.id in SPLITS and t.id not in children:
            continue
        for child, rest in children.items():
            if SPLITS[child][0] == t.id:
                tid = rest
        if t.id == "kidsfam":
            k = shape(a, BY_ID["kids"])
            kids = Cand(a, BY_ID["kids"], "kids", k, prio) if k else None
            if not (shape(a, t) and _part_ok(a, "kids_day") and _part_ok(a, "sitcoms_family")):
                if kids is not None:
                    out.append(kids)
                    kids = None
                continue
        k = shape(a, t, tid)
        if k:
            out.append(Cand(a, t, tid, k, prio))
    return out, kids


def _hours(a, its):
    return sum(a.mins[i] for i in its)


def _skip(a, c, picked):
    if c.t.id in MIXES:
        # (a) one picked channel holds 70 % of its hours: that channel by another name
        if any(_hours(a, c.items & p.items) >= OVERLAP * c.minutes for p in picked):
            return True
        # (b) it holds 70 % of the hours of the picked channels it draws from: them merged
        union = set()
        for p in picked:
            if c.items & p.items:
                union |= p.items
        return bool(union) and _hours(a, c.items & union) >= OVERLAP * _hours(a, union)
    n = len(c.items)
    return any(len(c.items & p.items) >= OVERLAP * n for p in picked)


def pick(a, cands, kids=None):
    """The picked candidates: by hours (capped) with ties to the higher row, 10 at most, two
    decades at most, no near duplicate. A skipped Kids & Family gives its place to Kids."""
    queue = sorted(cands, key=Cand.rank)
    picked, decades = [], 0
    while queue and len(picked) < PICKS:
        c = queue.pop(0)
        if c.t.id in DECADES and decades >= MAX_DECADES:
            continue
        if _skip(a, c, picked):
            if c.t.id == "kidsfam" and kids is not None:
                queue = sorted(queue + [kids], key=Cand.rank)
                kids = None
            continue
        picked.append(c)
        decades += c.t.id in DECADES
    ids = {c.t.id for c in picked}
    for c in picked:                          # the parent takes its items back
        for child, (parent, rest) in SPLITS.items():
            if c.t.id == parent and c.tid == rest and child not in ids:
                c.set(a, parent, shape(a, c.t) or c.kind)
    return picked


# -- channels

def _sections(a, its):
    return [list(a.refs[r]) for r in sorted({a.ref[i] for i in its})]


def _src(a, tid, kind, title):
    """A theme source; kind "both" is left out (shows and movies)."""
    its = _items(a, tid, kind)
    if not its:
        return None
    s = {"type": "theme", "theme": tid}
    if kind != "both":
        s["kind"] = kind
    s["sections"] = _sections(a, its)
    s["title"] = title
    return s


def _blk(start, days, src, order, once=False, block=None):
    return channels.block_raw(start, days, src, order, once, block)


def name_of(t, kind):
    if kind == "movie" and t.kinds != "movie":
        return MOVIE_NAMES.get(t.id, t.name + " Movies")
    return t.name


def plan_of(a, t, tid, kind):
    """{"order", "source"} or {"blocks"} of a theme channel: the light daily
    pattern with its time blocks; a part that does not fill leaves its hours to the main
    pool."""
    title = name_of(t, kind)
    if kind == "movie":
        return {"order": "shuffle", "source": _src(a, tid, "movie", title)}
    if t.pattern in ("blocks", "shuffle"):
        return {"order": t.pattern, "source": _src(a, tid, kind, title)}

    def part(pid):
        return _src(a, pid, "show", BY_ID[pid].name) if _part_ok(a, pid) else None
    if t.pattern == "kidsfam":
        return {"blocks": [_blk(6 * 60, None, part("kids_day"), "blocks"),
                           _blk(20 * 60, None, part("sitcoms_family"), "blocks", block=1)]}
    main = _src(a, tid, "show", title)
    movie = _src(a, tid, "movie", name_of(t, "movie")) if kind == "both" else None
    bl = [_blk(4 * 60, None, main, "blocks")]
    p = t.pattern
    if p == "movie20" and movie:
        bl.append(_blk(20 * 60, None, movie, "shuffle", once=True))
    elif p == "horror" and movie:
        bl.append(_blk(20 * 60, None, movie, "shuffle"))
    elif p == "late" and movie:
        bl.append(_blk(22 * 60, None, movie, "shuffle", once=True))
    elif p == "anime" and movie:
        bl.append(_blk(20 * 60, ["fri"], movie, "shuffle", once=True))
    elif p == "cartoons":
        cl = part("cartoons_classic")
        if cl:
            bl += [_blk(7 * 60, ["sat"], cl, "blocks", block=1), _blk(12 * 60, ["sat"], main, "blocks")]
        if movie:
            bl.append(_blk(12 * 60, ["sat", "sun"], movie, "shuffle", once=True))
    elif p == "sitcoms":
        fam = part("sitcoms_family")
        if fam:
            bl += [_blk(20 * 60, ["fri"], fam, "blocks", block=1), _blk(22 * 60, ["fri"], main, "blocks")]
    elif p == "actionanim":
        late = part("actionanim_late")
        if late:
            bl.append(_blk(22 * 60, None, late, "blocks"))
        if movie:
            bl.append(_blk(20 * 60, ["sat"], movie, "shuffle", once=True))
    elif p == "adultanim":
        mature = part("anime_mature")
        if mature:
            bl.append(_blk(22 * 60, ["sat"], mature, "blocks"))
        if movie:                             # Saturday 22:00 is the anime night
            bl.append(_blk(22 * 60, ["sun", "mon", "tue", "wed", "thu", "fri"], movie, "shuffle",
                           once=True))
    if len(bl) == 1:
        return {"order": "blocks", "source": main}
    return {"blocks": bl}


def _order_key(c):
    g = "@movies" if c.kind == "movie" and c.t.kinds != "movie" and c.t.id not in FORMAT_THEMES \
        else c.t.id
    return (ORDER.index(g), ORDER.index(c.t.id))


def _what(a, c):
    ns = sum(1 for i in c.items if a.show[i])
    nm = len(c.items) - ns
    if nm and ns:
        return len(c.items), "titles"
    return (ns, "shows") if ns else (nm, "movies")


def _sources(ch):
    return [ch["source"]] if ch.get("source") else [b["source"] for b in ch.get("blocks", ())]


def curate(data, existing=()):
    """The Make Channels for Me first set. data: section indexes, or their
    Analysis. existing: the Channels of the file (their sources, numbers and names are not
    used again). Returns propose()-style rows [{"kind", "count", "what", "minutes", "score",
    "default", "channel"}]: the picked themes in cable-guide order and the marathons On,
    then the themes that fill but were not picked, Off; numbered from 02 or the next free
    numbers."""
    a = data if isinstance(data, Analysis) else analyse(data)
    have = {channels.source_key(src) for ch in existing for _s, src in ch.slots()}
    used = {ch.number for ch in existing}
    names_used = [ch.name for ch in existing]
    cands, kids = _candidates(a)

    def fresh(c):
        ch = plan_of(a, c.t, c.tid, c.kind)
        srcs = _sources(ch)
        return all(srcs) and not any(channels.source_key(channels.norm_source(s)) in have
                                     for s in srcs)
    cands = [c for c in cands if fresh(c)]
    if kids is not None and not fresh(kids):
        kids = None
    picked = pick(a, cands, kids)
    rows = []

    def add(kind, count, what, minutes, ch, on):
        n = channels.free_number(used)
        if n is None:
            return
        used.add(n)
        name = channels.make_name(ch.pop("title"), names_used)
        names_used.append(name)
        rows.append({"kind": kind, "count": count, "what": what, "minutes": int(minutes),
                     "score": int(minutes), "default": on,
                     "channel": dict(ch, number=n, name=name)})

    def add_theme(c, on):
        count, what = _what(a, c)
        add("theme", count, what, c.minutes, dict(plan_of(a, c.t, c.tid, c.kind),
                                                  title=name_of(c.t, c.kind)), on)
    for c in sorted(picked, key=_order_key):
        add_theme(c, True)
    for s in marathons(a, have):
        srv, sid, title, leaves, _v, _l, mins = s[:7]
        src = {"type": "show", "id": sid, "title": channels.clean(title)}
        if srv:
            src["server"] = srv
        add("show", leaves, "episodes", mins, {"order": "order", "source": src, "title": title},
            True)
    taken = {c.t.id for c in picked}
    for c in cands:
        if c.t.id not in taken:
            if c.tid != c.t.id:               # a suggestion keeps all its items
                c.set(a, c.t.id, shape(a, c.t) or c.kind)
            add_theme(c, False)
    return rows


MARATHON_NO_SCORE = 6.5        # a show with no rating counts as a little below the usual 7.9


def marathon_rank(s):
    """Quality first (marathons favor critically strong shows): the audience
    score, plus a bonus of up to 1 point for shows the profile watches (1 episode 0.25, 3
    episodes 0.5, 15 or more 1.0)."""
    q = s[7] if len(s) > 7 and s[7] else MARATHON_NO_SCORE
    bonus = min(1.0, 0.25 * math.log2(1 + s[4])) if s[4] > 0 else 0.0
    return (-(q + bonus), -s[4], -s[5], -s[3], s[1])


def marathons(a, have=()):
    """The two best shows for a marathon (marathon_rank), 20 episodes at least. One show per
    title: a library with the same show twice (two ids, one title) gives one marathon, not
    "NADIA" and "NADIA 2"."""
    def ok(s):
        src = {"type": "show", "server": s[0], "id": s[1], "title": s[2]}
        return channels.source_key(src) not in have
    out, seen = [], set()
    for s in sorted((s for s in a.shows if ok(s)), key=marathon_rank):
        k = channels.clean(s[2]).casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) == MARATHONS:
            break
    return out


def catalog(data):
    """[{"source", "title", "count", "what"}] for Add a Channel: every theme that fills, as one
    source with all its items."""
    a = data if isinstance(data, Analysis) else analyse(data)
    out = []
    for t in BASE:
        k = shape(a, t) if t.id != "kidsfam" else None   # two parts: Make Channels for Me only
        if not k:
            continue
        c = Cand(a, t, t.id, k, 0)
        count, what = _what(a, c)
        src = _src(a, t.id, k, name_of(t, k))
        if src:
            out.append({"source": src, "title": name_of(t, k), "count": count, "what": what})
    return out
