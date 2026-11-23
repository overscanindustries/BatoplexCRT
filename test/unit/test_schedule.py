import json
import os
import sys
import time
import unittest
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(__file__))
from batoplex import schedule as S  # noqa: E402

NY = "America/New_York"
E = S.Entry


def loc(y, m, d, h, mi=0, tz=NY):
    return int(datetime(y, m, d, h, mi, tzinfo=S.zone(tz)).timestamp())


def ep(show, s, e, dur=1320, title=None, srv="S"):
    return E("%s/%s-%d-%d" % (srv, show, s, e), "%s-%d-%d" % (show, s, e), srv, dur, "%s/%s" % (srv, show),
             "episode", s, e, title or "Ep %d" % e, show.upper(), "TV-PG", 2000)


def mv(i, dur=5400, srv="S"):
    return E("%s/m%d" % (srv, i), "m%d" % i, srv, dur, S.MOVIES, "movie", None, None,
             "Movie %d" % i, "", "PG", 1990 + i % 30)


def shows(n_shows, n_eps, dur=1320, seasons=1):
    return [ep("sh%d" % k, 1 + (e % seasons), e, dur) for k in range(n_shows) for e in range(1, n_eps + 1)]


def plan(mode, block=None, seed="abc123", rev="r1", key="main"):
    return S.Plan(seed, rev, (S.Slot(key, mode, block),), ())


def pools(entries, key="main", **kw):
    return {key: S.build_pool(entries, **kw)}


def rt(cache):
    """A cache as it comes back from disk."""
    return json.loads(json.dumps(cache))


def tape(cache):
    return S.Tape.of(cache)


def ids(t, lo=None, hi=None):
    return [(a.start, a.unit.key) for a in t.rows if (lo is None or a.start >= lo) and (hi is None or a.start < hi)]


class TestPrimitives(unittest.TestCase):
    def test_fixed_hash_values(self):
        # sha256, not the random module: these never change
        self.assertEqual(S.rand("c7f3a1", 0), 11610609994179587138)
        self.assertEqual(S.rand("c7f3a1", "g", 1), 7016877730310651936)
        g = S.Gen(S.build_pool([mv(i) for i in range(6)]), "shuffle", None, "c7f3a1")
        self.assertEqual([g.next().key for _ in range(6)], ["S/m%d" % i for i in (4, 3, 2, 0, 5, 1)])

    def test_day_start_and_dst_lengths(self):
        b, d = S.day_start(loc(2026, 11, 21, 3, 59), NY)
        self.assertEqual((b, d), (loc(2026, 11, 20, 4), date(2026, 11, 20)))
        self.assertEqual(S.day_start(loc(2026, 11, 21, 4), NY)[0], loc(2026, 11, 21, 4))
        tz = S.zone(NY)
        # spring forward 2026-03-08: a 23 h broadcast day; fall back 2026-11-01: 25 h
        self.assertEqual(S.day_bounds(date(2026, 3, 8), tz) - S.day_bounds(date(2026, 3, 7), tz), 23 * 3600)
        self.assertEqual(S.day_bounds(date(2026, 11, 1), tz) - S.day_bounds(date(2026, 10, 31), tz), 25 * 3600)

    def test_missing_durations_specials_duplicates(self):
        es = [ep("a", 1, 1), ep("a", 1, 2, dur=0), ep("a", 1, 3, dur=30), ep("a", 0, 1), ep("a", 1, 1),
              ep("a", 1, 4)]
        p = S.build_pool(es)
        self.assertEqual([u.key for u in p.units], ["S/a-1-1", "S/a-1-4"])
        p = S.build_pool([ep("a", 1, 1), ep("a", 1, 2), ep("a", 1, 1)], repeats=True)   # a playlist
        self.assertEqual([u.key for u in p.units], ["S/a-1-1", "S/a-1-2", "S/a-1-1#2"])

    def test_over_eight_hours_left_out(self):
        p = S.build_pool([mv(1, dur=8 * 3600), mv(2, dur=8 * 3600 + 1)])
        self.assertEqual([u.key for u in p.units], ["S/m1"])

    def test_canonical_order_and_cap_ignore_server_order(self):
        es = shows(30, 10)
        rev = list(reversed(es))
        a, b = S.build_pool(es, canonical=True, cap=100), S.build_pool(rev, canonical=True, cap=100)
        self.assertEqual(a.fp, b.fp)
        self.assertEqual(len(a), 100)

    def test_multi_part_units(self):
        es = [ep("a", 1, 1, title="Pilot"), ep("a", 1, 2, 1300, "The Contest (1)"),
              ep("a", 1, 3, 1400, "The Contest (2)"), ep("a", 1, 4, title="The Contest (3)"),
              ep("a", 1, 5, title="Best of Both Worlds"), ep("a", 1, 6, title="Best of Both Worlds, Part II"),
              ep("a", 1, 7, title="Ending Pt. 1"), ep("a", 1, 9, title="Ending Pt. 2"),   # not adjacent
              ep("b", 1, 8, title="Ending Pt. 2")]
        p = S.build_pool(es)
        kids = [[k.index for k in u.children] for u in p.units]
        self.assertEqual(kids, [[1], [2, 3, 4], [5, 6], [7], [9], [8]])
        u = p.units[1]
        self.assertEqual((u.dur, u.offsets), (1300 + 1400 + 1320, (0, 1300, 2700)))
        self.assertEqual(S.part_mark("Episode (2000)"), None)


class TestGenerator(unittest.TestCase):
    def take(self, g, n):
        return [g.next() for _ in range(n)]

    def test_order_loops(self):
        p = S.build_pool([ep("a", 1, e) for e in range(1, 4)])
        g = S.Gen(p, "order", None, "x")
        self.assertEqual([u.index for u in self.take(g, 7)], [1, 2, 3, 1, 2, 3, 1])

    def test_shuffle_bags_and_no_repeat_at_bag_change(self):
        p = S.build_pool([mv(i) for i in range(7)])
        for seed in ("a", "b", "c", "d", "e", "f"):
            g = S.Gen(p, "shuffle", None, seed)
            seq = [u.key for u in self.take(g, 70)]
            for k in range(10):
                self.assertEqual(sorted(seq[k * 7:(k + 1) * 7]), sorted(u.key for u in p.units))
            self.assertTrue(all(seq[i] != seq[i + 1] for i in range(69)))

    def test_growing_pool_keeps_the_bag(self):
        # 200 movies, 16 a day for 120 days, the state moved to a new pool each day. Repeats
        # inside 7 days (112 picks), measured: fixed pool 284 before / 285 now; one movie added
        # each day 622 before (each new pool started a new bag) / 180 now.
        def repeats(grow):
            st, seq = None, []
            for d in range(120):
                pool = S.build_pool([mv(i) for i in range(200 + (d if grow else 0))])
                g = S.Gen(pool, "shuffle", None, "seed", st)
                seq += [g.next().key for _ in range(16)]
                st = json.loads(json.dumps(g.state()))
            return sum(1 for i, k in enumerate(seq) if k in seq[max(0, i - 112):i])
        self.assertLessEqual(repeats(True), repeats(False) * 1.2)

    def test_show_weight_by_square_root(self):
        # a 6-episode show, a 220-episode show and four of 50 episodes. Air share, measured:
        # 16.7 % / 17.1 % before (equal weight), 9.0 % / 21.3 % now
        es = [ep("small", 1, e) for e in range(1, 7)] + [ep("big", 1, e) for e in range(1, 221)] + shows(4, 50)
        g = S.Gen(S.build_pool(es), "blocks", 2, "seedY")
        n = {}
        for _ in range(4000):
            u = g.next()
            n[u.group] = n.get(u.group, 0) + 1
        self.assertLess(n["S/small"], 0.12 * 4000)
        self.assertGreater(n["S/big"], 0.19 * 4000)

    def test_blocks_no_group_twice_in_a_row(self):
        p = S.build_pool(shows(5, 30) + [mv(i) for i in range(6)])
        g = S.Gen(p, "blocks", 2, "seed")
        picks = []
        for _ in range(200):
            u = g.next()
            if g.left == (0 if u.group == S.MOVIES else 1):     # the first unit of a block
                picks.append(u.group)
        self.assertTrue(all(picks[i] != picks[i + 1] for i in range(len(picks) - 1)))
        self.assertEqual(set(picks), set(p.groups))
        # each show plays its episodes in order from its own cursor
        g = S.Gen(p, "blocks", 2, "seed")
        seen = {}
        for _ in range(300):
            u = g.next()
            if u.group != S.MOVIES:
                seen.setdefault(u.group, []).append(u.index)
        for idx in seen.values():
            self.assertEqual(idx, [(i % 30) + 1 for i in range(len(idx))])

    def test_blocks_with_one_group(self):
        p = S.build_pool([ep("a", 1, e) for e in range(1, 6)])
        g = S.Gen(p, "blocks", 2, "seed")
        self.assertEqual([u.index for u in self.take(g, 12)], [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1, 2])

    def test_restart_mid_block(self):
        p = S.build_pool(shows(6, 20))
        g = S.Gen(p, "blocks", 2, "seed")
        self.take(g, 4)
        first = g.next()
        self.assertEqual(g.left, 1)                   # after the first episode of a 2-episode block
        st = json.loads(json.dumps(g.state()))
        cont = self.take(g, 9)
        g2 = S.Gen(p, "blocks", 2, "seed", st)
        again = self.take(g2, 9)
        self.assertEqual([u.key for u in again], [u.key for u in cont])
        self.assertEqual(again[0].group, first.group)
        self.assertEqual(again[0].index, first.index + 1)

    def test_state_moves_to_a_new_pool(self):
        es = [ep("a", 1, e) for e in range(1, 11)]
        g = S.Gen(S.build_pool(es), "order", None, "x")
        self.take(g, 4)                               # next: episode 5
        st = g.state()
        gone = [e for e in es if e.index not in (5, 6)]   # 5 and 6 deleted
        g2 = S.Gen(S.build_pool(gone), "order", None, "x", st)
        self.assertEqual(g2.next().index, 7)
        g3 = S.Gen(S.build_pool([ep("a", 1, 99)] + es), "order", None, "x", st)   # an addition
        self.assertEqual(g3.next().index, 5)


class TestFreeze(unittest.TestCase):
    def setUp(self):
        self.p = plan("blocks", 2)
        self.es = shows(8, 40) + [mv(i) for i in range(20)]

    def test_no_gaps_and_coverage(self):
        now = loc(2026, 11, 21, 9)
        c = S.freeze(self.p, pools(self.es), now, NY)
        t = tape(c)
        self.assertEqual(t.rows[0].start, loc(2026, 11, 21, 4))   # a new start: today's 04:00
        for a, b in zip(t.rows, t.rows[1:]):
            self.assertEqual(a.end, b.start)
        self.assertGreaterEqual(t.end, now + 24 * 3600)
        self.assertGreaterEqual(t.end, loc(2026, 11, 23, 4))

    def test_deterministic(self):
        now = loc(2026, 11, 21, 9)
        a = S.freeze(self.p, pools(self.es), now, NY)
        b = S.freeze(self.p, pools(list(reversed(self.es)), canonical=True), now, NY)
        c = S.freeze(self.p, pools(self.es, canonical=True), now, NY)
        self.assertEqual(json.dumps(b), json.dumps(c))
        self.assertEqual(ids(tape(a)), ids(tape(S.freeze(self.p, pools(self.es), now, NY))))

    def test_restart_and_library_change_same_day_keep_today(self):
        now = loc(2026, 11, 21, 5)
        c1 = rt(S.freeze(self.p, pools(self.es), now, NY))
        changed = [e._replace(dur=e.dur + 60) if e.index == 3 else e for e in self.es if e.index != 7] + \
            shows(2, 30) + [ep("new", 1, e) for e in range(1, 30)]
        c2 = S.freeze(self.p, pools(changed), loc(2026, 11, 21, 18), NY, c1)
        self.assertEqual(ids(tape(c2), hi=tape(c1).end), ids(tape(c1)))
        self.assertEqual(c2["pools"], c1["pools"])    # the frozen pool, not the new one

    def test_damaged_cache_with_zero_duration_starts_again(self):
        now = loc(2026, 11, 21, 5)
        c1 = json.loads(json.dumps(S.freeze(self.p, pools(self.es), now, NY)))
        for v in c1["pools"].values():
            for e in v:
                e[3] = 0
        c2 = S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 6), NY, c1)
        self.assertIsNotNone(c2)
        self.assertTrue(all(e[3] >= S.MIN_DUR for v in c2["pools"].values() for e in v))

    def test_next_day_keeps_tape_before_t_star(self):
        c1 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 9), NY))
        b = loc(2026, 11, 22, 4)
        old = tape(c1)
        new_es = [e for e in self.es if e.group != "S/sh3"] + [ep("new", 1, e) for e in range(1, 30)]
        c2 = S.freeze(self.p, pools(new_es), loc(2026, 11, 22, 6), NY, c1)
        t2 = tape(c2)
        t_star = c2["anchor"]
        self.assertGreaterEqual(t_star, b)
        self.assertLess(old.rows[old.at(b) - 1].start if old.at(b) else 0, t_star)
        self.assertEqual(ids(t2, hi=t_star), ids(old, lo=t2.rows[0].start, hi=t_star))
        self.assertEqual(c2["date"], "2026-11-22")
        self.assertNotIn("S/sh3", {a.unit.group for a in t2.rows if a.start >= t_star})

    def test_equal_pool_next_day_gives_the_same_tape(self):
        c1 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 9), NY))
        c2 = S.freeze(self.p, pools(self.es), loc(2026, 11, 22, 6), NY, c1)
        t1, t2 = tape(c1), tape(c2)
        self.assertEqual(ids(t2, hi=t1.end), ids(t1, lo=t2.rows[0].start))

    def test_three_days_offline(self):
        es = [ep("a", 1, e) for e in range(1, 400)]
        p = plan("order")
        c = rt(S.freeze(p, pools(es), loc(2026, 11, 21, 9), NY))
        daily = c
        for day in (22, 23, 24):
            daily = rt(S.freeze(p, pools(es), loc(2026, 11, day, 9), NY, daily))
        jump = S.freeze(p, pools(es), loc(2026, 11, 24, 9), NY, c)
        self.assertEqual(jump["anchor"], daily["anchor"])     # the old schedule ran on, not a new start
        self.assertEqual(ids(tape(jump), lo=jump["anchor"]), ids(tape(daily), lo=daily["anchor"]))
        self.assertIsNotNone(tape(jump).lookup(loc(2026, 11, 24, 9)))

    def test_clock_jumps(self):
        c1 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 9), NY))
        c2 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 22, 4, 5), NY, c1))
        # an NTP correction back over the day boundary: the same tape, still covered
        back = S.freeze(self.p, pools(self.es), loc(2026, 11, 22, 3, 50), NY, c2)
        self.assertEqual(back["rows"], c2["rows"])
        self.assertIsNotNone(tape(back).lookup(loc(2026, 11, 22, 3, 50)))
        # back before the tape: a new start
        old = S.freeze(self.p, pools(self.es), loc(2026, 11, 19, 12), NY, c2)
        self.assertEqual(old["anchor"], loc(2026, 11, 19, 4))
        # 40 days on: a new start
        far = S.freeze(self.p, pools(self.es), loc(2026, 12, 31, 12), NY, c2)
        self.assertEqual(far["anchor"], loc(2026, 12, 31, 4))

    def test_new_revision_starts_again(self):
        c1 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 9), NY))
        p2 = plan("shuffle", rev="r2")
        c2 = S.freeze(p2, pools(self.es), loc(2026, 11, 21, 15), NY, c1)
        self.assertEqual((c2["anchor"], c2["rev"]), (loc(2026, 11, 21, 4), "r2"))

    def test_failed_fetch_keeps_pool_then_stale_refreeze_from_now(self):
        c1 = rt(S.freeze(self.p, pools(self.es), loc(2026, 11, 21, 9), NY))
        self.assertIsNone(S.freeze(self.p, None, loc(2026, 11, 21, 9), NY))   # nothing to play
        now = loc(2026, 11, 22, 6)
        c2 = rt(S.freeze(self.p, None, now, NY, c1))
        self.assertTrue(c2["stale"])
        self.assertEqual(c2["pools"], c1["pools"])
        later = now + 600
        c3 = S.freeze(self.p, pools(self.es[:100]), later, NY, c2)
        self.assertFalse(c3["stale"])
        self.assertGreaterEqual(c3["anchor"], later)
        self.assertEqual(ids(tape(c3), hi=c3["anchor"]), ids(tape(c2), lo=tape(c3).rows[0].start, hi=c3["anchor"]))
        t3, t2 = tape(c3), tape(c2)
        self.assertEqual(t3.rows[t3.at(later)], t2.rows[t2.at(later)])    # the programme on now stays

    def test_empty_pool_is_nothing(self):
        self.assertIsNone(S.freeze(self.p, pools([ep("a", 1, 1, dur=0)]), loc(2026, 11, 21, 9), NY))

    def test_dst_spring_and_fall(self):
        for day, now in ((date(2026, 3, 8), loc(2026, 3, 8, 3, 30)), (date(2026, 11, 1), loc(2026, 11, 1, 3, 30))):
            c = rt(S.freeze(self.p, pools(self.es), now, NY))
            t = tape(c)
            self.assertGreaterEqual(t.end, now + 24 * 3600)
            nxt = S.freeze(self.p, pools(self.es), now + 3600, NY, c)     # 04:30: the day changed
            self.assertEqual(nxt["date"], day.isoformat())
            self.assertGreaterEqual(nxt["anchor"], S.day_bounds(day, S.zone(NY)))
            tn = tape(nxt)
            for a, b in zip(tn.rows, tn.rows[1:]):
                self.assertEqual(a.end, b.start)
            self.assertGreaterEqual(tn.end, now + 3600 + 24 * 3600)
            self.assertEqual(ids(tn, hi=nxt["anchor"]), ids(t, lo=tn.rows[0].start, hi=nxt["anchor"]))

    def test_time_zone_change(self):
        now = loc(2026, 11, 21, 23, 30)
        c1 = rt(S.freeze(self.p, pools(self.es), now, NY))
        t1 = tape(c1)
        # same instant, Tokyo: a later broadcast date; the tape before t* and the programme on now stay
        c2 = S.freeze(self.p, pools(self.es), now + 60, "Asia/Tokyo", c1)
        self.assertEqual(c2["tz"], "Asia/Tokyo")
        self.assertEqual(c2["date"], "2026-11-22")
        t2 = tape(c2)
        self.assertEqual(t2.rows[t2.at(now)], t1.rows[t1.at(now)])
        self.assertEqual(ids(t2, hi=t1.end), ids(t1, lo=t2.rows[0].start))   # equal pool: equal tape
        # an earlier date in the new zone (clock or zone went back): the same tape
        c3 = S.freeze(self.p, pools(self.es), now + 120, "Pacific/Honolulu", rt(c2))
        self.assertEqual(c3["rows"], rt(c2)["rows"])


class TestTimeBlocks(unittest.TestCase):
    def tplan(self, times):
        keys = sorted({k for _m, _d, k in times})
        return S.Plan("tb0001", "r1", tuple(S.Slot(k, "order", None) for k in keys), tuple(times))

    def pools2(self, empty=()):
        return {"A": S.build_pool([] if "A" in empty else [ep("a", 1, e, 1500) for e in range(1, 200)]),
                "B": S.build_pool([] if "B" in empty else [mv(i, 3000) for i in range(50)]),
                "C": S.build_pool([ep("c", 1, e, 1500) for e in range(1, 200)])}

    def slot_at(self, t, ta):
        return ta.rows[ta.at(t)].slot

    def test_lookback_across_midnight_and_overrun(self):
        p = self.tplan([(6 * 60, None, "A"), (20 * 60, None, "B")])
        c = S.freeze(p, self.pools2(), loc(2026, 11, 21, 9), NY)
        t = tape(c)
        # 02:00 on Sunday: the block that started at 20:00 on Saturday
        self.assertEqual(self.slot_at(loc(2026, 11, 22, 2), t), "B")
        six = loc(2026, 11, 22, 6)
        first_a = next(a for a in t.rows if a.slot == "A" and a.start >= six)
        before = t.rows[t.rows.index(first_a) - 1]
        self.assertEqual(before.slot, "B")
        self.assertLess(before.start, six)           # it plays to its end; the block starts late
        self.assertEqual(before.end, first_a.start)
        self.assertLess(first_a.start - six, 3000)

    def test_weekdays_and_duplicate_starts(self):
        # Saturday and Sunday 06:00: A; every day 20:00: B; Monday 06:00: B again (no weekday A)
        p = self.tplan([(6 * 60, frozenset({"sat", "sun"}), "A"), (20 * 60, None, "B"),
                        (12 * 60, None, "A"), (12 * 60, None, "C")])      # a duplicate: the later wins
        tz = S.zone(NY)
        tm = S._Times(p, tz, {"A", "B", "C"})
        self.assertEqual(tm.at(loc(2026, 11, 21, 7)), "A")      # Saturday 07:00
        self.assertEqual(tm.at(loc(2026, 11, 23, 7)), "B")      # Monday 07:00: Sunday 20:00
        self.assertEqual(tm.at(loc(2026, 11, 23, 13)), "C")     # 12:00 twice: the later one
        self.assertEqual(tm.at(loc(2026, 11, 23, 2)), "B")      # Monday 02:00

    def test_empty_scheduled_source_is_ignored(self):
        p = self.tplan([(6 * 60, None, "A"), (20 * 60, None, "B")])
        c = S.freeze(p, self.pools2(empty=("A",)), loc(2026, 11, 21, 9), NY)
        self.assertEqual({a.slot for a in tape(c).rows}, {"B"})

    def test_blocks_share_a_slot_and_keep_their_cursor(self):
        p = self.tplan([(6 * 60, None, "A"), (12 * 60, None, "B"), (18 * 60, None, "A")])
        c = S.freeze(p, self.pools2(), loc(2026, 11, 21, 9), NY)
        idx = [a.unit.index for a in tape(c).rows if a.slot == "A"]
        self.assertEqual(idx, list(range(idx[0], idx[0] + len(idx))))   # one cursor, no restart

    def test_rule_edit_keeps_the_other_cursors(self):
        """A new source for the 20:00 block keeps the tape up to the end of the
        programme on now and the cursor of the 06:00 block."""
        p = self.tplan([(6 * 60, None, "A"), (20 * 60, None, "B")])
        now = loc(2026, 11, 21, 9)
        c = rt(S.freeze(p, self.pools2(), now, NY))
        old = tape(c)
        later = loc(2026, 11, 21, 21, 10)                 # in the old 20:00 block (B)
        p2 = S.Plan("tb0001", "r2", (S.Slot("A", "order", None), S.Slot("C", "order", None)),
                    ((6 * 60, None, "A"), (20 * 60, None, "C")))
        c2 = S.freeze(p2, {"C": self.pools2()["C"]}, later, NY, c)
        new = tape(c2)
        t_star = next(a.start for a in old.rows if a.start >= later)
        self.assertEqual(ids(new, hi=t_star), ids(old, lo=new.rows[0].start, hi=t_star))
        self.assertEqual(self.slot_at(later, new), "B")                    # B plays on
        self.assertEqual(self.slot_at(loc(2026, 11, 22, 21), new), "C")     # the edit is in
        a_old = [a.unit.key for a in old.rows if a.slot == "A" and a.start >= t_star]
        a_new = [a.unit.key for a in new.rows if a.slot == "A" and a.start >= t_star]
        self.assertEqual(a_new[:5], a_old[:5])                             # the cursor goes on
        self.assertFalse(c2["stale"])

    def test_dst_block_times(self):
        p = self.tplan([(20 * 60, None, "B"), (6 * 60, None, "A")])
        tm = S._Times(p, S.zone(NY), {"A", "B"})
        for d in (7, 8, 9):                            # across the spring change
            self.assertEqual(tm.at(loc(2026, 3, d, 20)), "B")
            self.assertEqual(tm.at(loc(2026, 3, d, 20) - 1), "A")


class TestOnce(unittest.TestCase):
    """A `once` block airs one unit at the first item boundary at or after its
    start, then the latest start that is not `once` goes on."""

    def plan(self, once=True):
        times = ((4 * 60, None, "S"), (20 * 60, None, "M", once) if once is not None else
                 (20 * 60, None, "M"))
        return S.Plan("on0001", "r1", (S.Slot("S", "order", None), S.Slot("M", "shuffle", None)),
                      times)

    def day_m(self, t, d):
        lo, hi = loc(2026, 11, d, 4), loc(2026, 11, d + 1, 4)
        return [a for a in t.rows if a.slot == "M" and lo <= a.start < hi]

    def test_long_show_ends_then_one_movie(self):
        s = [ep("s", 1, 1, 5400)] + [ep("s", 1, e, 7200) for e in range(2, 40)]
        ps = {"S": S.build_pool(s), "M": S.build_pool([mv(i, 6000) for i in range(30)])}
        t = tape(S.freeze(self.plan(), ps, loc(2026, 11, 21, 9), NY))
        show = next(a for a in t.rows if a.start == loc(2026, 11, 21, 19, 30))
        self.assertEqual(show.slot, "S")                          # 19:30-21:30 crosses 20:00
        i = t.rows.index(show)
        self.assertEqual([a.slot for a in t.rows[i + 1:i + 3]], ["M", "S"])   # then one movie
        self.assertEqual(len(self.day_m(t, 21)), 1)

    def test_short_movies_air_once(self):
        ps = {"S": S.build_pool([ep("s", 1, e, 1320) for e in range(1, 400)]),
              "M": S.build_pool([mv(i, 1800) for i in range(30)])}
        t = tape(S.freeze(self.plan(), ps, loc(2026, 11, 21, 9), NY))
        for d in (21, 22):
            (m,) = self.day_m(t, d)
            self.assertGreaterEqual(m.start, loc(2026, 11, d, 20))
            self.assertLess(m.start, loc(2026, 11, d, 20, 30))
        # a normal block: movies until the next start
        t = tape(S.freeze(self.plan(once=False), ps, loc(2026, 11, 21, 9), NY))
        self.assertGreater(len(self.day_m(t, 21)), 10)

    def test_mark_survives_freeze_and_extend(self):
        ps = {"S": S.build_pool([ep("s", 1, e, 1320) for e in range(1, 400)]),
              "M": S.build_pool([mv(i, 1800) for i in range(30)])}
        p = self.plan()
        c1 = rt(S.freeze(p, ps, loc(2026, 11, 21, 21), NY))       # after the 20:00 window
        c2 = rt(S.freeze(p, ps, loc(2026, 11, 22, 6), NY, c1))    # the next day: a replay
        self.assertEqual(c2["state"][S.ONCE], loc(2026, 11, 21, 20))
        t1, t2 = tape(c1), tape(c2)
        self.assertEqual(ids(t2, hi=t1.end), ids(t1, lo=t2.rows[0].start))
        self.assertEqual(len(self.day_m(t2, 22)), 1)
        cut = dict(c2, end=0, rows=c2["rows"][:3])                # an extend from the anchor
        grown = S.freeze(p, ps, loc(2026, 11, 22, 7), NY, cut)
        after = [r for r in grown["rows"] if r[0] >= c2["anchor"]]
        self.assertEqual(after, [r for r in c2["rows"] if r[0] >= c2["anchor"]][:len(after)])
        self.assertGreater(len(after), 50)

    def test_no_once_gives_the_same_tape(self):
        ps = {"S": S.build_pool([ep("s", 1, e, 1320) for e in range(1, 400)]),
              "M": S.build_pool([mv(i, 1800) for i in range(30)])}
        a = S.freeze(self.plan(once=None), ps, loc(2026, 11, 21, 9), NY)
        b = S.freeze(self.plan(once=False), ps, loc(2026, 11, 21, 9), NY)
        self.assertEqual(json.dumps(a), json.dumps(b))
        self.assertNotIn(S.ONCE, a["state"])
        only = S.Plan("on0001", "r1", (S.Slot("M", "shuffle", None),), ((20 * 60, None, "M", True),))
        t = tape(S.freeze(only, {"M": ps["M"]}, loc(2026, 11, 21, 9), NY))
        self.assertGreater(len(t.rows), 30)                       # only `once`: a normal block


class TestFirstState(unittest.TestCase):
    """Where each show starts on a new start of the tape."""

    def es(self):
        return [ep(x, 1, e, 1320) for x in ("a", "b", "c") for e in range(1, 11)]

    def first(self, cache, show):
        return next(a.unit.index for a in tape(cache).rows if a.unit.group == "S/" + show)

    def test_unwatched_partly_and_fully_watched(self):
        p = plan("blocks", 1)
        seen = {"main": ["S/a-1-1", "S/a-1-3", "S/a-1-4",          # one skipped early episode
                         ] + ["S/b-1-%d" % e for e in range(1, 11)]}
        c = S.freeze(p, pools(self.es()), loc(2026, 11, 21, 9), NY, seen=seen)
        self.assertEqual(self.first(c, "a"), 5)
        self.assertEqual(self.first(c, "b"), 1 + S.rand("abc123:main", "start", "S/b") % 10)
        self.assertEqual(self.first(c, "c"), 1)
        none = S.freeze(p, pools(self.es()), loc(2026, 11, 21, 9), NY)
        self.assertEqual([self.first(none, x) for x in "abc"], [1, 1, 1])
        o = S.freeze(plan("order"), pools([ep("a", 1, e, 1320) for e in range(1, 11)]),
                     loc(2026, 11, 21, 9), NY, seen={"main": ["S/a-1-2"]})
        self.assertEqual(tape(o).rows[0].unit.index, 3)            # one show in order

    def test_a_daily_freeze_does_not_move_a_cursor(self):
        p = plan("blocks", 1)
        c1 = rt(S.freeze(p, pools(self.es()), loc(2026, 11, 21, 9), NY))
        everything = {"main": [e.key for e in self.es()]}
        a = S.freeze(p, pools(self.es()), loc(2026, 11, 22, 6), NY, c1, seen=everything)
        b = S.freeze(p, pools(self.es()), loc(2026, 11, 22, 6), NY, c1)
        self.assertEqual(a["rows"], b["rows"])
        self.assertEqual(a["state"], b["state"])


class TestLookup(unittest.TestCase):
    def test_tune_inside_each_child_of_a_multi_part_unit(self):
        es = [ep("a", 1, 1, 1000, "Pilot"), ep("a", 1, 2, 1300, "Big (1)"), ep("a", 1, 3, 1400, "Big (2)")]
        c = S.freeze(plan("order"), pools(es), loc(2026, 11, 21, 9), NY)
        t = tape(c)
        i = next(k for k, a in enumerate(t.rows) if len(a.unit.children) == 2)
        a = t.rows[i]
        self.assertEqual(t.lookup(a.start + 100), (i, 0, 100))
        self.assertEqual(t.lookup(a.start + 1300 + 250), (i, 1, 250))     # inside child 2
        self.assertEqual(t.lookup(a.start + 1300 - 5), (i, 1, 0))         # under 10 s left: child 2
        self.assertEqual(t.lookup(a.start + 2700 - 5), (i + 1, 0, 0))     # under 10 s left: next airing
        self.assertEqual(t.child_start(i, 1), a.start + 1300)
        self.assertIsNone(t.lookup(t.end + 5))
        self.assertIsNone(t.lookup(t.rows[0].start - 5))

    def test_deleted_items_are_passed_over(self):
        es = [ep("a", 1, e, 600) for e in range(1, 30)]
        c = S.freeze(plan("order"), pools(es), loc(2026, 11, 21, 9), NY)
        t = tape(c)
        i = t.at(loc(2026, 11, 21, 9))
        bad = {t.rows[i].unit.key, t.rows[i + 1].unit.key}
        self.assertEqual(t.next_playable(i, 0, bad), (i + 2, 0))
        self.assertIsNone(t.next_playable(i, 0, {a.unit.key for a in t.rows}, limit=5))

    def test_guide_window(self):
        c = S.freeze(plan("order"), pools([ep("a", 1, e, 1800) for e in range(1, 9)]), loc(2026, 11, 21, 9), NY)
        t = tape(c)
        w = t.window(loc(2026, 11, 21, 9, 15), loc(2026, 11, 21, 11))
        self.assertEqual(w[0].start, loc(2026, 11, 21, 9))
        self.assertEqual(w[-1].start, loc(2026, 11, 21, 10, 30))


class TestPerformance(unittest.TestCase):
    BUDGET = 0.5      # s: a day for 12 channels of 5,000 episodes on the Mac (Docker)

    def test_twelve_channels_of_5000(self):
        es = shows(100, 50, dur=1320, seasons=5)                   # 5,000 episodes
        movies = [mv(i) for i in range(2000)]
        modes = ["blocks", "shuffle", "order", "blocks"] * 3
        plans = [plan(m, seed="ch%04d" % i) for i, m in enumerate(modes)]
        now = loc(2026, 11, 21, 9)
        t0 = time.perf_counter()
        caches = [S.freeze(p, pools(es if i % 3 else es + movies, canonical=True), now, NY)
                  for i, p in enumerate(plans)]
        first = time.perf_counter() - t0
        disk = [rt(c) for c in caches]
        changed = es[:4000] + shows(5, 40)
        t0 = time.perf_counter()
        for p, c in zip(plans, disk):
            S.freeze(p, pools(changed, canonical=True), loc(2026, 11, 22, 6), NY, c)
        nxt = time.perf_counter() - t0
        t0 = time.perf_counter()
        for p, c in zip(plans, disk):
            S.freeze(p, pools(es, canonical=True), now + 3600, NY, c)
            S.Tape.of(c).lookup(now)
        restart = time.perf_counter() - t0
        self.assertLess(first, self.BUDGET)
        self.assertLess(nxt, 2 * self.BUDGET)          # two pools (old replay + new) per channel
        self.assertLess(restart, self.BUDGET)


if __name__ == "__main__":
    unittest.main()
