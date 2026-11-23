"""Rating badges, Cast & Crew, the person page, the detail page rows."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, inputs, plex  # noqa: E402
from batoplex.model import Item  # noqa: E402
from batoplex.screens import Screen  # noqa: E402
from batoplex.screens import detail  # noqa: E402
from batoplex.screens.library import sort_titles  # noqa: E402

pygame.init()


class TestRatings(unittest.TestCase):
    def test_old_fields(self):
        r = plex.ratings({"rating": 8.7, "ratingImage": "rottentomatoes://image.rating.ripe",
                          "audienceRating": 3.8,
                          "audienceRatingImage": "rottentomatoes://image.rating.spilled"})
        self.assertEqual(r, [plex.Rating("rt", "critic", "87%", "fresh"),
                             plex.Rating("rt", "audience", "38%", "down")])

    def test_rating_array_wins_and_orders_critic_first(self):
        r = plex.ratings({"rating": 5.0, "Rating": [
            {"image": "imdb://image.rating", "value": 7.4, "type": "audience"},
            {"image": "rottentomatoes://image.rating.rotten", "value": 4.1, "type": "critic"},
            {"image": "themoviedb://image.rating", "value": 8.2, "type": "audience"}]})
        self.assertEqual([(x.source, x.value, x.state) for x in r],
                         [("rt", "41%", "rotten"), ("imdb", "7.4", None), ("tmdb", "82%", None)])

    def test_unknown_image_none_and_rt_without_suffix(self):
        self.assertEqual(plex.ratings({"rating": 8.0}), [plex.Rating("plain", "critic", "8.0", None)])
        self.assertEqual(plex.ratings({}), [])
        self.assertEqual(plex.ratings({"rating": None}), [])
        r = plex.ratings({"rating": 5.9, "ratingImage": "rottentomatoes://x"})
        self.assertEqual(r[0].state, "rotten")
        r = plex.ratings({"rating": 6.0, "ratingImage": "rottentomatoes://x"})
        self.assertEqual(r[0].state, "fresh")

    def test_badges_drop_from_the_right(self):
        rs = plex.ratings({"Rating": [
            {"image": "rottentomatoes://image.rating.ripe", "value": 8.7, "type": "critic"},
            {"image": "rottentomatoes://image.rating.upright", "value": 9.1, "type": "audience"},
            {"image": "imdb://image.rating", "value": 8.7, "type": "audience"}]})
        self.assertEqual(detail.badges_fit(rs, 396), 3)
        w2 = detail.badge_width(rs[0]) + detail.BADGE_GAP + detail.badge_width(rs[1])
        self.assertEqual(detail.badges_fit(rs, w2), 2)
        self.assertEqual(detail.badges_fit(rs, 10), 0)


class TestPeople(unittest.TestCase):
    def test_order_limit_roles_and_no_thumb(self):
        md = {"Writer": [{"id": 9, "tag": "W"}], "Director": [{"id": 8, "tag": "D"}],
              "Role": [{"id": i, "tag": "A%d" % i, "role": "R%d" % i,
                        "thumb": "http://h/%d.jpg" % i if i else None} for i in range(25)]}
        ps = plex.people(md, "srv")
        self.assertEqual(len(ps), 20)
        self.assertEqual([p.title for p in ps[:2]], ["A0", "A1"])
        self.assertEqual(ps[1].extra, {"kind": "actor", "role": "R1"})
        self.assertIsNone(ps[0].poster)
        self.assertEqual(ps[1].server_id, "srv")
        ps = plex.people({"Role": [{"id": 1, "tag": "A"}], "Director": [{"id": 8, "tag": "D"}],
                          "Writer": [{"id": 9, "tag": "W"}]})
        self.assertEqual([(p.title, p.extra["role"]) for p in ps],
                         [("A", ""), ("D", "Director"), ("W", "Writer")])


class FakePlex(plex.PlexProvider):
    """Section listings from a dict; one section can fail."""

    def __init__(self, data, fail=()):
        self.server_id, self.data, self.fail, self.calls = "srv", data, set(fail), []
        self.sections = [{"id": k, "type": t, "title": str(k)} for k, (t, _x) in data.items()]

    def _visible_sections(self):
        return list(self.sections)

    def get(self, path, params=None, start=None, size=None, timeout=8):
        sid = int(path.split("/")[3])
        self.calls.append((sid, dict(params), start, size))
        if sid in self.fail:
            raise plex.PlexError("HTTP 500")
        items = self.data[sid][1]
        return {"totalSize": len(items), "Metadata": items[start:start + size]}


def md(rk, t, title, date=None, year=None):
    m = {"ratingKey": rk, "type": t, "title": title}
    if date:
        m["originallyAvailableAt"] = date
    if year:
        m["year"] = year
    return m


class TestPersonTitles(unittest.TestCase):
    def test_merge_pages_sorts_and_failed_section(self):
        many = [md(str(1000 + i), "movie", "M%03d" % i) for i in range(230)]
        p = FakePlex({1: ("movie", many), 2: ("show", [md("7", "show", "The Show")]),
                      3: ("movie", [md("8", "movie", "X")])}, fail={3})
        person = Item("42", "person", "P", extra={"kind": "director"})
        out = p.person_titles(person)
        self.assertEqual(len(out), 231)                   # more than one page of 200
        first = p.calls[0]
        self.assertEqual(first[1], {"type": 1, "director": "42"})
        self.assertEqual([c[2] for c in p.calls if c[0] == 1], [0, 200])
        self.assertTrue(all(i.server_id == "srv" for i in out))

    def test_all_sections_fail_raises(self):
        p = FakePlex({1: ("movie", [])}, fail={1})
        with self.assertRaises(plex.PlexError):
            p.person_titles(Item("42", "person", "P"))

    def test_sorts(self):
        def it(t, date=None, year=None):
            i = Item(t, "movie", t, year=year)
            if date:
                i.extra["date"] = date
            return i
        items = [it("The Zoo", "2001-05-01"), it("Apple", "2001-05-01"), it("An Owl", None, 1999),
                 it("Nodate"), it("Bee", "2010-01-02")]
        self.assertEqual([i.title for i in sort_titles(items, "originallyAvailableAt:desc")],
                         ["Bee", "Apple", "The Zoo", "An Owl", "Nodate"])
        self.assertEqual([i.title for i in sort_titles(items, "titleSort")],
                         ["Apple", "Bee", "Nodate", "An Owl", "The Zoo"])


class TestExtrasRelated(unittest.TestCase):
    def prov(self, reply):
        p = plex.PlexProvider.__new__(plex.PlexProvider)
        p.server_id, p.base, p.token, p.calls = "srv", "http://s:32400", "tok", []

        def get(path, params=None, start=None, size=None, timeout=8):
            p.calls.append(path)
            r = reply(path)
            if isinstance(r, Exception):
                raise r
            return r
        p.get = get
        return p

    def test_extras_labels_and_a_clip_with_no_part(self):
        md = [{"ratingKey": "1", "type": "clip", "subtype": "trailer", "title": "T",
               "Media": [{"Part": [{"key": "/library/parts/1/f.mkv", "duration": 5000}]}]},
              {"ratingKey": "2", "type": "clip", "extraType": "5", "title": "B",
               "Media": [{"Part": [{"key": "/library/parts/2/f.mkv"}]}]},
              {"ratingKey": "3", "type": "clip", "title": "None", "Media": []}]
        p = self.prov(lambda path: {"Metadata": md})
        out = p.extras(Item("9", "movie", "M"))
        self.assertEqual([c.id for c in out], ["1", "2"])
        self.assertEqual([plex.extra_label(c.extra["subtype"]) for c in out],
                         ["Trailer", "Behind the Scenes"])
        self.assertEqual(plex.extra_label("other"), "Extra")
        self.assertTrue(out[0].extra["no_state"])
        pi, it = p.play_info(out[0], resume=True)          # direct, from the start
        self.assertEqual((pi.url, pi.start_s, pi.mode), ("http://s:32400/library/parts/1/f.mkv",
                                                         0.0, "direct"))
        self.assertEqual(p.calls, ["/library/metadata/9/extras"])   # no second metadata call

    def test_show_details_parse_on_deck(self):
        show = {"ratingKey": "2000", "type": "show", "title": "S", "OnDeck": {"Metadata": [
            {"ratingKey": "2002", "type": "episode", "title": "E2", "index": 2, "parentIndex": 1,
             "grandparentThumb": "/t"}]}}
        p = self.prov(lambda path: {"Metadata": [show]})
        params = []
        get = p.get
        p.get = lambda path, q=None, **k: params.append(q) or get(path, q, **k)
        full = p.details(Item("2000", "show", "S"))
        self.assertEqual(params[0].get("includeOnDeck"), 1)
        od = full.extra["on_deck"]
        self.assertEqual((od.id, od.type, od.server_id, od.poster), ("2002", "episode", "srv", "srv|/t"))

    def test_related_uses_the_similar_hub_only(self):
        hubs = {"Hub": [{"hubIdentifier": "movie.by.actor", "Metadata": [
            {"ratingKey": "5", "type": "movie", "title": "Other"}]},
            {"hubIdentifier": "movie.similar", "Metadata": [
                {"ratingKey": "6", "type": "movie", "title": "Sim"},
                {"ratingKey": "7", "type": "episode", "title": "Ep"}]}]}
        p = self.prov(lambda path: hubs)
        self.assertEqual([i.title for i in p.related(Item("9", "movie", "M"))], ["Sim"])
        p = self.prov(lambda path: {"Hub": hubs["Hub"][:1]})
        self.assertEqual(p.related(Item("9", "movie", "M")), [])
        p = self.prov(lambda path: plex.PlexError("HTTP 404") if path.startswith("/library")
                      else hubs)
        self.assertEqual(len(p.related(Item("9", "movie", "M"))), 1)
        self.assertEqual(p.calls, ["/library/metadata/9/related", "/hubs/metadata/9/related"])


# ------------------------------------------------------------------ the detail page focus
class Images:
    version = 0

    def get(self, *a, **k):
        return None

    def prefetch(self, *a, **k):
        pass


class App:
    def __init__(self, prov):
        self.settings = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.prov, self.images, self.src_gen = prov, Images(), 0
        self.sources, self.music, self.stack = None, None, []
        self.opened, self.popped = [], 0
        self.canvas = pygame.Surface((641, 480))

    def provider_for(self, item):
        return self.prov

    def open_item(self, it):
        self.opened.append(it)

    def pop(self):
        self.popped += 1


class Prov:
    def __init__(self, people=(), fail=False):
        self.people, self.fail = list(people), fail

    def details(self, item):
        if self.fail:
            raise plex.PlexError("HTTP 500")
        full = Item(item.id, "movie", item.title, server_id="srv")
        full.extra["people"] = self.people
        return full


def sync(self, fn, done, fail=None):
    try:
        r = fn()
    except Exception as e:
        if fail:
            fail(e)
        return
    done(r)


class TestDetailFocus(unittest.TestCase):
    def setUp(self):
        self.real = Screen.run_bg
        Screen.run_bg = sync

    def tearDown(self):
        Screen.run_bg = self.real

    def person(self, n):
        return Item(str(n), "person", "P%d" % n, server_id="srv", extra={"role": "R"})

    def test_down_up_returns_to_the_same_button(self):
        app = App(Prov([self.person(1), self.person(2)]))
        scr = detail.DetailScreen(app, Item("9", "movie", "M"))
        scr.handle(inputs.RIGHT)                          # Mark as Played
        self.assertEqual(scr.fkey, "watch")
        scr.handle(inputs.DOWN)
        self.assertEqual(scr.zone, "rows")
        self.assertEqual(scr.home.row.title, "Cast & Crew")
        scr.handle(inputs.RIGHT)
        scr.handle(inputs.OK)
        self.assertEqual(app.opened[-1].title, "P2")
        scr.handle(inputs.UP)
        self.assertEqual(scr.zone, "top")
        self.assertEqual(scr.fkey, "watch")

    def test_no_rows_down_does_nothing_and_failed_load_has_no_row(self):
        for prov in (Prov([]), Prov(fail=True)):
            scr = detail.DetailScreen(App(prov), Item("9", "movie", "M"))
            scr.handle(inputs.DOWN)
            self.assertEqual(scr.zone, "top")
            self.assertFalse(scr.has_rows)

    def test_rows_keep_order_and_focus_when_replies_come_reversed(self):
        held = []
        Screen.run_bg = lambda self_, fn, done, fail=None: held.append((fn, done))
        prov = Prov([self.person(1)])
        prov.extras = lambda it: [Item("c1", "clip", "Trailer", extra={"subtype": "trailer"})]
        prov.related = lambda it: [Item("r1", "movie", "R1"), Item("r2", "movie", "R2")]
        scr = detail.DetailScreen(App(prov), Item("9", "movie", "M"))
        fn, done = held.pop(0)
        done(fn())                                        # details: cast, then two workers
        (fe, de), (fr, dr) = held
        dr(fr())                                          # More Like This lands first
        scr.handle(inputs.DOWN)
        scr.handle(inputs.DOWN)
        scr.handle(inputs.RIGHT)
        self.assertEqual(scr.home.item.title, "R2")
        de(fe())                                          # Extras lands while the user is there
        self.assertEqual([r.title for r in scr.home.rows],
                         ["Cast & Crew", "Extras", "More Like This"])
        self.assertEqual(scr.home.item.title, "R2")
        self.assertEqual(scr.trailer().title, "Trailer")

    def test_show_page_plays_on_deck_and_reloads_after_playback(self):
        ep = Item("2002", "episode", "E2", index=2, season_index=1, server_id="srv")
        calls = []

        class ShowProv(Prov):
            def details(self, item):
                calls.append(item.id)
                full = Item(item.id, "show", "S", server_id="srv")
                full.extra["on_deck"] = ep
                return full
        app = App(ShowProv())
        app.play = lambda it, resume=True: app.opened.append(("play", it.id, resume))
        scr = detail.DetailScreen(app, Item("2000", "show", "S"))
        self.assertEqual(scr.buttons()[0][2], "Play S1 E2")
        scr.handle(inputs.OK)
        self.assertEqual(app.opened[-1], ("play", "2002", True))
        scr.apply_files([{"item_id": "2002", "server_id": "srv", "position_ms": 0,
                          "watched": True}])
        self.assertEqual(calls, ["2000", "2000"])           # On Deck again

    def test_menu_only_in_the_top_zone(self):
        scr = detail.DetailScreen(App(Prov([self.person(1)])), Item("9", "movie", "M"))
        self.assertTrue(scr.menu_ok)
        scr.handle(inputs.DOWN)
        self.assertFalse(scr.menu_ok)


if __name__ == "__main__":
    unittest.main()
