"""The pixel scenes: Night City events, Flying Objects, the art."""
import os
import random
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from batoplex import config, saver, saveropts, savers, tokens  # noqa: E402


def make(kind, seed=1, **opts):
    st = config.Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
    for k, v in opts.items():
        saveropts.put(st, kind, k, v)
    app = types.SimpleNamespace(settings=st, images=None, src_gen=1, sources=None,
                                secrets=types.SimpleNamespace(get=lambda k, d=None: d),
                                saver=None)
    t = [0.0]
    sv = saver.Screensaver(app, clock=lambda: t[0] / 1000, rng=random.Random(seed),
                           ms=lambda: t[0], kind=kind)
    app.saver = sv
    return sv, t


class TestScenes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_art_sheets_load(self):
        from batoplex.savers import aquarium as aq
        for name, n in (("toaster", 4), ("tv", 4), ("toast", 1), ("decor", 5),
                        ("land1", 5), ("land2", 5), ("vehicles", 5), ("fish_clown", 4), ("fish_tetra", 4),
                        ("fish_angel", 4), ("puffball", 1), ("jelly", 1), ("frog", 4), ("critters", 5),
                        ("reefdecor", 6), ("big", 2), ("plants", 5), ("seaturtle", 4),
                        ("slider", 4), ("turn_seaturtle", 2), ("turn_slider", 2), ("wall_reef", 1),
                        ("wall_fresh", 1)) + tuple(("fish_" + k, 4) for k in aq.SPECIES if k != "jelly"):
            frames = savers.sheet(name, n)
            self.assertEqual(len(frames), n, name)
            for f in frames:
                self.assertGreater(f.get_bounding_rect().w, 4, name)    # each cell drew
        old, savers.ART_DIR = savers.ART_DIR, tempfile.mkdtemp()
        savers._sheets.clear()
        try:
            for kind in ("flying", "aquarium", "city"):          # an install with no art/
                sv, t = make(kind)
                for _ in range(20):
                    t[0] += tokens.FIELD_MS
                    sv.update()
                sv.draw(pygame.Surface((641, 480)))
        finally:
            savers.ART_DIR = old
            savers._sheets.clear()

    def test_city_events_one_at_a_time_and_each_ends(self):
        sv, t = make("city", 4)
        c = sv.impl
        seen, starts = set(), []
        for f in range(60 * 60 * 12):                       # 12 minutes at 60 fields
            was = c.event
            t[0] += tokens.FIELD_MS
            sv.update()
            if c.event is not None and was is None:
                starts.append(t[0] / 1000.0)
                seen.add(c.event["kind"])
        self.assertGreaterEqual(len(starts), 3)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        self.assertTrue(all(gap >= 120 for gap in gaps), gaps)
        for kind in ("ufo", "blimp", "train", "fireworks"):
            c.event, c.sparks = None, []
            c.start_event(kind)
            for _ in range(60 * 90):                        # each ends within 90 s
                t[0] += tokens.FIELD_MS
                sv.update()
                if c.event is None:
                    break
            self.assertIsNone(c.event, kind)

    def test_flying_objects_kinds_and_respawn(self):
        sv, t = make("flying", 2, objects="tvs")
        f = sv.impl
        self.assertEqual({o.kind for o in f.objs}, {"tv"})
        for _ in range(60 * 60):
            t[0] += tokens.FIELD_MS
            sv.update()
            for o in f.objs:
                w, h = f._size(o.kind)
                self.assertTrue(-w - 8 <= o.x <= f.W + 24 and -h - 24 <= o.y <= f.H + 8)
        sv, t = make("flying", 2)
        self.assertEqual({o.kind for o in sv.impl.objs} - {"toaster", "toast"}, set())


class TestAquarium(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def run_for(self, sv, t, seconds, each=None):
        for f in range(int(seconds * 60)):
            t[0] += tokens.FIELD_MS
            sv.update()
            if each:
                each(f)

    def test_each_tank_has_its_own_animals(self):
        from batoplex.savers import aquarium as aq
        for tank, frogs, turtles in (("reef", 0, {"seaturtle"}), ("fresh", 2, {"slider"}),
                                     ("mixed", 2, {"seaturtle", "slider"})):
            sv, t = make("aquarium", 3, tank=tank)
            a = sv.impl
            kinds = {f.name for f in a.fish}
            self.assertTrue(all(aq.SPECIES[k][0] == tank for k in kinds) or tank == "mixed", kinds)
            self.assertGreaterEqual(len(kinds), 7, tank)
            self.assertEqual(len(a.frogs), frogs, tank)
            self.assertEqual({x.kind for x in a.turtles}, turtles, tank)
        few, _t = make("aquarium", 3, tank="reef", fish="few")
        many, _t = make("aquarium", 3, tank="reef", fish="many")
        self.assertLess(len(few.impl.fish), len(many.impl.fish))

    def test_animals_stay_in_the_water_and_frogs_and_turtles_breathe(self):
        from batoplex.savers import aquarium as aq
        sv, t = make("aquarium", 7, tank="mixed")
        a = sv.impl
        seen = {"air": set(), "turtle_air": set()}

        def check(_f):
            for f in a.fish:
                w, h = f.size()
                self.assertTrue(-1 <= f.x - w / 2 and f.x + w / 2 <= aq.TANK_W + 1, f.name)
                self.assertTrue(aq.SURF <= f.y <= aq.SAND_Y + 4, (f.name, f.y))
            for g in a.frogs:
                self.assertTrue(aq.SURF < g.y <= aq.SAND_Y and 0 < g.x < aq.TANK_W)
                if g.state == "air":
                    seen["air"].add(id(g))
            for x in a.turtles:
                self.assertTrue(aq.SURF < x.y <= aq.SAND_Y and 0 < x.x < aq.TANK_W)
                if x.state == "air":
                    seen["turtle_air"].add(x.kind)
        self.run_for(sv, t, 60 * 6, check)
        self.assertEqual(len(seen["air"]), 2)                   # each frog went up for air
        self.assertEqual(seen["turtle_air"], {"seaturtle", "slider"})
        self.assertTrue(all(0 <= w.x <= aq.TANK_W for w in a.walkers))

    def test_fish_never_swim_backwards_and_turn_through_the_three_quarter_view(self):
        sv, t = make("aquarium", 5, tank="mixed")
        a = sv.impl
        turned = set()

        def check(_f):
            for f in [x for x in a.fish if x.habit != "jelly"] + a.far + a.turtles:
                if f.turn is None:
                    self.assertGreaterEqual(f.vx * f.face, -1e-6, getattr(f, "name", "turtle"))
                else:
                    turned.add(id(f))
                    self.assertGreaterEqual(f.vx * f.f0 * (1 if f.turn < 0.5 else -1), -1e-6)
        self.run_for(sv, t, 60 * 3, check)
        self.assertGreater(len(turned), 5)
        b = a.art["goldfish"][0]
        self.assertTrue(b.q)
        self.assertIsNone(a.turtle_art["slider"].q)              # its turn art is another turtle

    def test_a_paused_fish_does_not_slide_back_on_the_screen_in_the_pan(self):
        for activity, pan in (("normal", "normal"), ("calm", "fast"), ("lively", "slow")):
            for name in ("clown", "lion"):
                sv, t = make("aquarium", 5, tank="reef", activity=activity, pan=pan)
                a = sv.impl
                self.run_for(sv, t, 2)
                f = [x for x in a.fish if x.name == name][0]
                a.event = None
                a.phase = 0.25                                   # the pan at its fastest, right
                a._pan(0.0)
                f.face, f.turn, f.x, f.y = 1, None, a.view + 100, 120.0
                f.tx, f.ty, f.timer, f.resting, f.mode = f.x, f.y, 30.0, True, None
                f.v = f.speed                                    # it swam here, then paused
                last, seen, t0 = [f.x - a.view], [], t[0]

                def check(_f):
                    x = f.x - a.view
                    self.assertIsNone(f.turn, (activity, name))   # a pause: no turn
                    if a.view_v > 1.0 and t[0] - t0 > 1500:       # (a slow fish eases up)
                        seen.append(1)
                        self.assertGreaterEqual(x, last[0] - 0.01, (activity, name))
                    last[0] = x
                self.run_for(sv, t, 5, check)
                self.assertGreater(len(seen), 60)

    def test_a_paused_fish_goes_to_a_flake(self):
        sv, t = make("aquarium", 5, tank="reef", activity="calm", pan="fast")
        a = sv.impl
        self.run_for(sv, t, 2)
        f = [x for x in a.fish if x.name == "clown"][0]
        a.event = None
        a.phase = 0.25                                           # the pan goes the way it faces
        a._pan(0.0)
        f.face, f.turn, f.x, f.y = 1, None, a.view + 160, 120.0
        f.tx, f.ty, f.timer, f.resting, f.mode = f.x, f.y, 5.0, True, None
        a.flakes = [[f.x - 60, 120.0, 0.0, 0.0]]
        self.run_for(sv, t, 3)
        self.assertEqual(f.face, -1)                             # it turned to the flake
        self.assertFalse(f.resting)

    def test_feed_time_brings_the_fish_and_the_flakes_go(self):
        sv, t = make("aquarium", 4, tank="fresh")
        a = sv.impl
        self.run_for(sv, t, 2)
        a.event = None
        a.start_event("feed")
        self.run_for(sv, t, 4)
        self.assertTrue(a.flakes)
        eaten = []
        n = [len(a.flakes)]

        def count(_f):
            if len(a.flakes) < n[0]:
                eaten.append(1)
            n[0] = len(a.flakes)
        self.run_for(sv, t, 60, count)
        self.assertTrue(eaten)
        self.assertEqual(a.flakes, [])                           # eaten, or gone in 40 s

    def test_puffer_puffs_and_shark_passes(self):
        sv, t = make("aquarium", 2, tank="reef")
        a = sv.impl
        puffer = [f for f in a.fish if f.habit == "puffer"][0]
        puffed = []
        self.run_for(sv, t, 150, lambda _f: puffed.append(puffer.puffed > 0) if _f % 30 == 0 else None)
        self.assertTrue(any(puffed) and not all(puffed))
        a.event = None
        a.start_event("shark")
        self.run_for(sv, t, 60)
        self.assertNotEqual((a.event or {}).get("kind"), "shark")   # it went across and out
        sv.draw(pygame.Surface((641, 480)))

    def test_options_species_and_one_event_at_a_time(self):
        sv, t = make("aquarium", 4, tank="mixed", frogs=0, turtles=False, crabs=False,
                     feed="off", big=False, bursts=False, moon="always")
        a = sv.impl
        self.assertEqual((a.frogs, a.turtles, a.walkers, a.ev_every), ([], [], [], {}))
        self.run_for(sv, t, 20)
        self.assertEqual((a.event, a.night), (None, 1.0))
        st = sv.app.settings
        saveropts.put_aq_species(st, "mixed", ["clown", "pleco", "goldfish", "jelly"])
        sv2 = saver.Screensaver(sv.app, clock=lambda: 0, rng=random.Random(4), ms=lambda: 0,
                                kind="aquarium")
        b = sv2.impl
        self.assertEqual({f.name for f in b.fish}, {"clown", "pleco", "goldfish", "jelly"})
        self.assertEqual(sum(f.name == "pleco" for f in b.fish), 1)
        self.assertEqual({f.name for f in b.far}, {"goldfish"})   # cruisers only
        sv, t = make("aquarium", 6, tank="reef", feed="often")
        a = sv.impl
        starts, prev = [], [None]

        def watch(_f):
            if a.event is not None and prev[0] is None:
                starts.append(a.event["kind"])
            prev[0] = a.event
            self.assertTrue(all(v > -400 for v in a.due.values()))      # none waits for ever
        self.run_for(sv, t, 60 * 30, watch)
        self.assertGreaterEqual(starts.count("feed"), 9)                 # each 2 to 3 minutes
        self.assertTrue({"bubbles"} & set(starts) and {"shark", "manta"} & set(starts))

    def test_rare_moments_in_preview(self):
        sv, t = make("aquarium", 3, tank="fresh", feed="off")
        sv.preview = True
        a = sv.impl
        ks = a.rare_list()
        self.assertEqual(ks, ["bubbles", "chest", "frog_air", "frog_hang", "turtle", "air", "moon"])
        self.assertTrue(sv.rare(-1))                                     # LB first: the last
        self.assertEqual((a.rare_key, a.label[0]), ("moon", "Moonlight On"))
        self.assertTrue(sv.rare(1))                                      # RB wraps
        self.assertEqual(a.rare_key, "bubbles")
        self.assertEqual(a.event["kind"], "bubbles")
        self.run_for(sv, t, 1)
        g = a.frogs[0]
        g.x = a.view + a.W + 200 if a.view < 200 else a.view - 200      # out of view
        for f in a.frogs[1:]:
            f.x = g.x
        a.rare_key = "chest"
        sv.rare(1)
        self.assertIsNotNone(a.glide)
        before = a.view
        self.run_for(sv, t, 2.2)
        self.assertIsNone(a.glide)
        self.assertLess(abs(a.view + a.W / 2 - g.x), abs(before + a.W / 2 - g.x))
        v0 = a.view
        self.run_for(sv, t, 0.2)
        self.assertLess(abs(a.view - v0), 2)                             # the pan goes on, no jump
        seen = []
        self.run_for(sv, t, 25, lambda _f: seen.append(g.state))
        self.assertIn("air", seen)
        surf = pygame.Surface((641, 480))
        a.label = None
        sv.draw(surf)


class TestThreeD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    def test_maze_reachable_walker_exits_and_ray_distance(self):
        from batoplex.savers import maze
        rng = random.Random(5)
        m = maze.make_maze(rng)
        n = maze.SIZE
        seen, todo = {(1, 1)}, [(1, 1)]
        while todo:
            x, y = todo.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                q = (x + dx, y + dy)
                if m[q[1]][q[0]] == 0 and q not in seen:
                    seen.add(q)
                    todo.append(q)
        self.assertTrue(all((2 * i + 1, 2 * j + 1) in seen for i in range(n) for j in range(n)))
        sv, t = make("maze", 5)
        z = sv.impl
        first = z.m
        for _ in range(60 * 60 * 20):                       # 20 minutes: it reaches the exit
            t[0] += tokens.FIELD_MS
            sv.update()
            if z.m is not first:
                break
        self.assertIsNot(z.m, first)
        z.m = [[1, 1, 1], [1, 0, 1], [1, 1, 1]]
        z.x, z.y = 1.5, 1.5
        for ang in (0.0, 1.5707963, 3.14159265):
            d, _side, _u = z.cast(ang)
            self.assertAlmostEqual(d, 0.5, places=3)

    def test_shuffle_changes_on_time_never_twice_and_set_never_empty(self):
        sv, t = make("shuffle", 3, every=5)
        seen = [sv.type]
        for _ in range(60 * 60 * 16):
            t[0] += tokens.FIELD_MS
            sv.update()
            if sv.type != seen[-1]:
                seen.append(sv.type)
        self.assertEqual(len(seen), 4)                          # 0, 5, 10, 15 minutes
        self.assertNotIn("shuffle", seen)
        st = sv.app.settings
        saveropts.put_extra(st, "shuffle", "set", [])
        self.assertTrue(saveropts.shuffle_set(st))
        saveropts.put_extra(st, "shuffle", "set", ["pong"])
        sv, t = make("shuffle", 3)
        sv.app.settings = st
        self.assertEqual(saveropts.shuffle_set(st), ["pong"])


if __name__ == "__main__":
    unittest.main()
