"""Curated Make Channels for Me: classification, the pick, shape, order,
split, mix and marathon rules."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import channels as C  # noqa: E402
from batoplex import themes as T  # noqa: E402


class Lib:
    """Invented libraries: section 1 shows, section 2 movies, format sets by id."""

    def __init__(self, server="s1"):
        self.server, self.rows, self.sets, self.n = server, [], {}, 0

    def add(self, typ, genres, rating, year, leaves, dur, fmt, viewed, last, title, score=0.0):
        self.n += 1
        r = T.Row(str(self.n), typ, title or "%s %d" % (typ, self.n), year, rating, tuple(genres),
                  dur * 60000, leaves, viewed, 0, last, "", score)
        if fmt:
            self.sets.setdefault(fmt, set()).add(r.id)
        self.rows.append(r)
        return r

    def shows(self, n, genres=(), rating="TV-PG", year=None, leaves=100, dur=22, fmt=None,
              viewed=0, last=0, title=None, score=0.0):
        return [self.add("show", genres, rating, year, leaves, dur, fmt, viewed, last, title, score)
                for _ in range(n)]

    def movies(self, n, genres=(), rating="PG-13", year=2001, dur=100, fmt=None):
        return [self.add("movie", genres, rating, year, 0, dur, fmt, 0, 0, None) for _ in range(n)]

    def ix(self, kind, section, complete=True, title="Library"):
        return {"server": self.server, "section": section, "kind": kind, "title": title,
                "rows": [r for r in self.rows if r.type == kind], "rows_complete": True,
                "genres": [], "dir_ok": True, "action": None,
                "sets": {f: {"ids": frozenset(ids), "complete": complete}
                         for f, ids in self.sets.items()}}

    def indexes(self):
        return [self.ix("show", 1), self.ix("movie", 2)]


def names(rows, on=True):
    return [r["channel"]["name"] for r in rows if r["default"] is on and r["kind"] == "theme"]


class TestClassify(unittest.TestCase):
    def test_name_variants(self):
        self.assertEqual(T.names("Sci-Fi & Fantasy"), ["science fiction", "fantasy"])
        self.assertEqual(T.names("Action/Adventure"), ["action", "adventure"])
        self.assertEqual(T.names("War & Politics"), ["war", "politics"])
        self.assertEqual(T.names("Science Fiction"), ["science fiction"])
        for tag, want in (("Talk", "talk"), ("Talk Show", "talk"), ("Home and Garden", "home and garden"),
                          ("Game Show", "game show"), ("Children", "kids"), ("Kids", "kids"),
                          ("Anime", "anime"), ("Film-Noir", "film noir"), ("Suspense", "suspense"),
                          ("Soap", "soap"), ("Stand-Up", "standup"), ("Food", "food"),
                          ("Travel", "travel"), ("Mini-Series", "mini series")):
            self.assertEqual(T.names(tag), [want], tag)
        self.assertEqual(T.norm_rating("gb/15"), "15")
        self.assertEqual(T.norm_rating(" TV-PG "), "tv-pg")
        fk, ak = T.format_keys([("1", "Animation"), ("2", "Talk Show"), ("3", "Reality"),
                                ("4", "Sci-Fi & Fantasy"), ("5", "Comedy"), ("6", "Children")])
        self.assertEqual(fk, {"animation": ["1"], "reality": ["2", "3"], "kids": ["6"]})
        self.assertEqual(ak, ["4"])

    def test_format_wins_over_genre(self):
        lib = Lib()
        (toon,) = lib.shows(1, ("Action", "Adventure"), fmt="animation")   # tags miss Animation
        (spy,) = lib.shows(1, ("Action", "Adventure"))
        (talk,) = lib.shows(1, ("Talk", "Comedy"))
        ix = lib.ix("show", 1)
        c = T.classify(toon, {"animation": ix["sets"]["animation"]["ids"]})
        self.assertEqual((c.format, c.adult, c.decade), ("animation", False, None))
        self.assertEqual(T.members(ix, "action"), [spy])
        self.assertEqual(T.members(ix, "cartoons"), [toon])
        self.assertEqual(T.members(ix, "reality"), [talk])          # a format from the tags
        self.assertNotIn(talk, T.members(ix, "comedy"))

    def test_adult_split_and_anime_hint(self):
        lib = Lib()
        (adult,) = lib.shows(1, ("Comedy",), rating="TV-MA", fmt="animation")
        (kid,) = lib.shows(1, ("Comedy",), rating="TV-Y7", fmt="animation")
        (unrated,) = lib.shows(1, ("Comedy",), rating="", fmt="animation")
        (plain,) = lib.shows(1, ("Drama",))
        ix = lib.ix("show", 1)
        self.assertEqual(T.members(ix, "adultanim"), [adult])
        self.assertEqual(T.members(ix, "cartoons"), [kid, unrated])
        self.assertEqual(T.members(ix, "kids_day"), [kid])
        anime = dict(ix, title="My Anime")              # a library named for anime
        self.assertEqual(T.members(anime, "anime"), [adult, kid, unrated, plain])
        self.assertEqual(T.members(anime, "drama"), [])

    def test_incomplete_format_set_leaves_the_section_out(self):
        lib = Lib()
        lib.shows(6, ("Comedy",), fmt="animation")
        ix = lib.ix("show", 1, complete=False)
        self.assertEqual(T.classify_index(ix), [])
        self.assertIsNone(T.members(ix, "comedy"))
        self.assertEqual(len(T.analyse([ix]).mins), 0)
        self.assertIsNone(T.members(dict(lib.ix("show", 1), rows_complete=False), "comedy"))
        self.assertIsNone(T.members(dict(lib.ix("show", 1), dir_ok=False), "comedy"))
        act = dict(lib.ix("show", 1), action={"ids": frozenset(), "complete": False})
        self.assertIsNone(T.members(act, "actionanim"))            # needs the action sets
        self.assertFalse(T.index_complete(act))


class TestCurate(unittest.TestCase):
    def test_minimum_fill_shape_and_name(self):
        lib = Lib()
        lib.shows(4, ("Western",), leaves=500)                     # 4 shows: no
        lib.shows(5, ("Comedy",))                                  # 183 h of shows
        lib.movies(14, ("Comedy",))                                # a movie slot
        lib.shows(5, ("Drama",))
        lib.movies(10, ("Drama",))                                 # under 14: shows all day
        lib.movies(33, ("Horror",), dur=90)                        # 49.5 h of movies only
        lib.movies(32, ("Mystery",), dur=90)                       # 48 h exactly
        lib.movies(31, ("Thriller",), dur=60)                      # 31 h: no
        out = T.curate(lib.indexes())
        by = {r["channel"]["name"]: r["channel"] for r in out}
        self.assertNotIn("WESTERNS", by)
        self.assertNotIn("THRILLER", by)
        com = by["COMEDY"]
        self.assertEqual([b["start"] for b in com["blocks"]], ["04:00", "20:00"])
        self.assertEqual(com["blocks"][0]["source"]["kind"], "show")
        self.assertEqual((com["blocks"][1]["order"], com["blocks"][1]["once"]), ("shuffle", True))
        self.assertEqual(com["blocks"][1]["source"]["kind"], "movie")
        self.assertEqual((by["DRAMA"]["order"], by["DRAMA"]["source"]["kind"]), ("blocks", "show"))
        self.assertEqual((by["HORROR MOVIES"]["order"], by["HORROR MOVIES"]["source"]["kind"]),
                         ("shuffle", "movie"))
        self.assertIn("CRIME MOVIES", by)                          # 32 x 90 min = 48 h
        for r in out:                                              # each row loads back
            lu = C.parse(json.dumps({"channels": [dict(r["channel"], id="%06x" % r["channel"]["number"])]}))
            self.assertEqual((lu.faults, len(lu.channels)), (0, 1), r["channel"]["name"])

    def big(self):
        """Eleven themes over 150 h, one theme of 55 h."""
        lib = Lib()
        for g in ("Comedy", "Drama", "Action", "Science Fiction", "Crime", "Horror", "Western"):
            lib.shows(5, (g,), leaves=100, dur=45)                 # 375 h each
        lib.movies(100, rating="G")                                # Family Movies, 167 h
        lib.movies(100, year=1960, rating="NR")                    # Classic Movies
        lib.shows(5, ("Documentary",), fmt="documentary", leaves=100)
        lib.shows(5, ("Reality",), fmt="reality", leaves=100)
        lib.shows(5, rating="TV-Y7", fmt="animation", leaves=30)   # Cartoons, 55 h
        return lib

    def test_cap_ties_go_to_the_higher_row(self):
        out = T.curate(self.big().indexes())
        on = names(out)
        self.assertEqual(len(on), 10)
        # all eleven big themes rank equal at 150 h: the lowest row (Westerns) is out, and
        # Cartoons (55 h) though it is a higher row
        self.assertEqual(on, ["COMEDY", "DRAMA", "ACTION", "SCI-FI", "CRIME", "HORROR",
                              "FAMILY MOVIES", "CLASSIC MOVIES", "DOCUMENTARIES", "REALITY"])
        self.assertEqual(names(out, False), ["CARTOONS", "WESTERNS"])
        self.assertEqual([r["channel"]["number"] for r in out], list(range(2, 16)))

    def test_decade_limit_and_numbers_after_existing(self):
        existing = C.parse(json.dumps({"channels": [
            {"id": "aaaaaa", "number": 2, "name": "X", "source": {"type": "library", "section": 9}},
            {"id": "bbbbbb", "number": 4, "name": "Y", "source": {"type": "library", "section": 8}}]})).channels
        lib = Lib()
        for y, n in ((1975, 40), (1985, 70), (1995, 50), (2005, 60)):
            lib.shows(5, ("Musical",), year=y, leaves=n)
        lib.shows(5, ("News",), year=1995, leaves=200)           # news is not scripted
        out = T.curate(lib.indexes(), existing)
        dec = [n for n in names(out) if n.endswith("TV")]
        self.assertEqual(dec, ["80S TV", "2000S TV"])            # the two largest decades
        self.assertEqual(sorted(names(out, False)), ["70S TV", "90S TV"])
        self.assertEqual([r["channel"]["number"] for r in out][:3], [3, 5, 6])

    def test_overlap_skip(self):
        lib = Lib()
        lib.shows(8, ("Drama", "Crime"), leaves=200)               # Crime is all Drama
        lib.shows(2, ("Crime",), leaves=200)
        lib.shows(6, ("Comedy",), leaves=200)
        out = T.curate(lib.indexes())
        self.assertEqual(names(out), ["COMEDY", "DRAMA"])
        self.assertEqual(names(out, False), ["CRIME"])             # 80 % on Drama

    def mixes(self, n_toon_action, n_anime_action, toons=10, anime=10):
        lib = Lib()
        lib.shows(toons, ("Comedy",), rating="TV-G", fmt="animation", leaves=60)
        lib.shows(n_toon_action, ("Action",), rating="TV-G", fmt="animation", leaves=150)
        lib.shows(anime, ("Drama",), rating="TV-14", fmt="anime", leaves=60)
        lib.shows(n_anime_action, ("Action",), rating="TV-14", fmt="anime", leaves=150)
        return T.curate(lib.indexes())

    def test_mix_rules(self):
        # (a) all action animation is cartoons: that channel by another name
        out = self.mixes(6, 0, toons=0)
        self.assertNotIn("ACTION ANIMATION", names(out))
        # (b) half cartoons, half anime: neither holds 70 %, but it is the two merged
        out = self.mixes(5, 5, toons=0, anime=0)
        self.assertEqual(names(out), ["CARTOONS", "ANIME"])
        self.assertIn("ACTION ANIMATION", names(out, False))
        # a real mix: a small part of two large channels
        out = self.mixes(3, 3, toons=20, anime=20)
        self.assertIn("ACTION ANIMATION", names(out))
        aa = next(r["channel"] for r in out if r["channel"]["name"] == "ACTION ANIMATION")
        self.assertEqual(aa["order"], "blocks")                    # 22:00 part needs 5 shows

    def test_splits_and_take_back(self):
        lib = Lib()
        lib.shows(6, ("Comedy",), dur=22, leaves=60)               # sitcoms, 132 h
        lib.shows(6, ("Comedy",), dur=45, leaves=60)               # the rest, 270 h
        out = T.curate(lib.indexes())
        self.assertEqual(names(out), ["COMEDY", "SITCOMS"])
        com = next(r["channel"] for r in out if r["channel"]["name"] == "COMEDY")
        self.assertEqual(com["source"]["theme"], "comedy_rest")
        # the rest under the minimum: Sitcoms is not a candidate, Comedy keeps all
        lib = Lib()
        lib.shows(6, ("Comedy",), dur=22, leaves=60)
        lib.shows(4, ("Comedy",), dur=45, leaves=60)
        out = T.curate(lib.indexes())
        self.assertEqual(names(out) + names(out, False), ["COMEDY"])
        # Sitcoms not picked (eleven larger themes): Comedy takes its items back
        lib = self.big()
        lib.shows(5, ("Comedy",), dur=22, leaves=30)               # 55 h of sitcoms
        out = T.curate(lib.indexes())
        com = next(r["channel"] for r in out if r["channel"]["name"] == "COMEDY")
        self.assertEqual(com["source"]["theme"], "comedy")
        self.assertIn("SITCOMS", names(out, False))
        # Classic Anime and Anime: 2005 splits them
        lib = Lib()
        lib.shows(6, rating="TV-PG", year=1995, fmt="anime", leaves=60)
        lib.shows(6, rating="TV-PG", year=2010, fmt="anime", leaves=60)
        out = T.curate(lib.indexes())
        self.assertEqual(names(out), ["ANIME", "CLASSIC ANIME"])
        an = next(r["channel"] for r in out if r["channel"]["name"] == "ANIME")
        self.assertEqual(an["source"]["theme"], "anime_new")

    def test_kids_and_family_and_the_kids_fallback(self):
        lib = Lib()
        lib.shows(6, ("Family",), rating="TV-Y", fmt="kids", leaves=60)
        lib.shows(6, ("Comedy",), rating="TV-PG", dur=22, leaves=60)
        out = T.curate(lib.indexes())
        kf = next(r["channel"] for r in out if r["channel"]["name"] == "KIDS & FAMILY")
        self.assertEqual([(b["start"], b["source"]["theme"]) for b in kf["blocks"]],
                         [("06:00", "kids_day"), ("20:00", "sitcoms_family")])
        lib = Lib()
        lib.shows(6, ("Family",), rating="TV-Y", fmt="kids", leaves=60)
        lib.shows(6, ("Comedy",), rating="TV-14", dur=22, leaves=60)  # no family sitcoms
        out = T.curate(lib.indexes())
        self.assertIn("KIDS", names(out))
        self.assertNotIn("KIDS & FAMILY", names(out) + names(out, False))

    def test_marathons(self):
        lib = Lib()
        lib.shows(5, ("Drama",), leaves=100)
        lib.shows(1, ("News",), leaves=300, title="Long Show")
        lib.shows(1, ("News",), leaves=40, viewed=10, last=50, title="Seen Less")
        lib.shows(1, ("News",), leaves=40, viewed=30, last=10, title="Seen Most")
        lib.shows(1, ("News",), leaves=10, viewed=10, last=99, title="Too Short")
        out = T.curate(lib.indexes())
        mar = [r for r in out if r["kind"] == "show"]
        self.assertEqual([r["channel"]["name"] for r in mar], ["SEEN MOST", "SEEN LESS"])
        self.assertEqual([r["channel"]["number"] for r in out[:3]], [2, 3, 4])   # after themes
        self.assertEqual(mar[0]["channel"]["order"], "order")
        lib = Lib()
        lib.shows(1, ("News",), leaves=40, viewed=3, title="Seen")
        lib.shows(1, ("News",), leaves=300, title="Long Show")
        lib.shows(1, ("News",), leaves=100, title="Mid Show")
        out = T.curate(lib.indexes())
        self.assertEqual([r["channel"]["name"] for r in out if r["kind"] == "show"],
                         ["SEEN", "LONG SHOW"])                    # fallback: most episodes
        lib = Lib()
        lib.shows(1, ("News",), leaves=40, viewed=30, title="Twin Show")
        lib.shows(1, ("News",), leaves=40, viewed=20, title="Twin Show")   # the same show twice
        lib.shows(1, ("News",), leaves=40, viewed=5, title="Other Show")
        out = T.curate(lib.indexes())
        self.assertEqual([r["channel"]["name"] for r in out if r["kind"] == "show"],
                         ["TWIN SHOW", "OTHER SHOW"])               # one marathon per title
        lib = Lib()
        lib.shows(1, ("News",), leaves=40, viewed=30, score=7.0, title="Watched Fine")
        lib.shows(1, ("News",), leaves=40, score=9.1, title="Acclaimed")
        lib.shows(1, ("News",), leaves=40, viewed=2, score=8.6, title="Strong Seen")
        lib.shows(1, ("News",), leaves=400, score=6.0, title="Long Weak")
        out = T.curate(lib.indexes())
        self.assertEqual([r["channel"]["name"] for r in out if r["kind"] == "show"],
                         ["ACCLAIMED", "STRONG SEEN"])             # quality first, a watch bonus

    def test_existing_theme_is_not_proposed_again(self):
        lib = Lib()
        lib.shows(6, ("Comedy",), dur=45, leaves=60)
        first = T.curate(lib.indexes())
        ch = dict(first[0]["channel"], id="cccccc")
        existing = C.parse(json.dumps({"channels": [ch]})).channels
        self.assertEqual(names(T.curate(lib.indexes(), existing)), [])


class TestNamesAndRatings(unittest.TestCase):
    def test_movie_names_fit(self):
        for t in (t for t in T.BASE if t.kinds != "show"):     # a theme that can play movies
            n = C.make_name(T.name_of(t, "movie"))
            self.assertEqual(n, T.name_of(t, "movie").upper(), t.id)       # never cut
        self.assertEqual(T.name_of(T.BY_ID["western"], "movie"), "Western Movies")

    def test_pg13_animation_u_family_and_adult_movies(self):
        lib = Lib()
        (pg13,) = lib.shows(1, rating="PG-13", fmt="animation")
        (u,) = lib.movies(1, rating="U")
        ix = lib.ix("show", 1)
        self.assertEqual(T.members(ix, "cartoons"), [pg13])
        self.assertEqual(T.members(lib.ix("movie", 2), "family"), [u])
        lib = Lib()
        lib.shows(6, rating="TV-MA", fmt="animation", leaves=60)
        lib.movies(14, rating="R", fmt="animation")
        (ch,) = [r["channel"] for r in T.curate(lib.indexes()) if r["kind"] == "theme"]
        self.assertEqual(ch["name"], "ADULT ANIMATION")
        movie = ch["blocks"][-1]
        self.assertEqual((movie["start"], movie["source"]["kind"], movie["once"]),
                         ("22:00", "movie", True))
        self.assertNotIn("sat", movie["days"])                           # the anime night


class TestSuggestions(unittest.TestCase):
    def test_curated_first_then_raw_sources_off(self):
        lib = Lib()
        lib.shows(6, ("Comedy",), dur=45, leaves=60)
        cur = T.curate(lib.indexes())
        self.assertEqual([r["channel"]["number"] for r in cur], [2, 3, 4])   # a theme, 2 marathons

        def raw(src):
            return {"kind": src["type"], "count": 9, "what": "episodes", "minutes": 4000,
                    "score": 4000, "default": False,
                    "channel": {"number": 2, "name": "TV", "order": "blocks", "source": src}}
        out = C.suggestions(cur, [raw({"type": "library", "section": 1}),
                                  raw(dict(cur[1]["channel"]["source"]))], used={5})
        self.assertEqual([(r["channel"]["number"], r["default"]) for r in out],
                         [(2, True), (3, True), (4, True), (6, False)])   # the marathon once


if __name__ == "__main__":
    unittest.main()
