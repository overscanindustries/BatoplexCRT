import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import sleep  # noqa: E402


class T:
    t = 100.0

    def __call__(self):
        return self.t


class TestSleep(unittest.TestCase):
    def test_minutes_count_down_and_fire_once(self):
        c = T()
        st = sleep.SleepTimer(clock=c)
        self.assertEqual(st.label(), "Off")
        st.step()
        self.assertEqual((st.mode, st.label()), (15, "15 min"))
        c.t += 14 * 60 + 1
        self.assertEqual(st.label(), "1 min")
        self.assertFalse(st.due())
        c.t += 60
        self.assertTrue(st.due())
        self.assertFalse(st.due())
        self.assertFalse(st.on)

    def test_step_wraps_and_end(self):
        st = sleep.SleepTimer(clock=T())
        st.step(-1)
        self.assertTrue(st.at_end)
        self.assertEqual(st.label("End of Track"), "End of Track")
        self.assertFalse(st.due())
        st.step()
        self.assertEqual(st.mode, None)
        st.step(-1, end=False)
        self.assertEqual(st.mode, 90)

    def test_bad_value_is_off(self):
        st = sleep.SleepTimer(clock=T())
        st.set(7)
        self.assertFalse(st.on)


if __name__ == "__main__":
    unittest.main()


class TestSleepTick(unittest.TestCase):
    def setUp(self):
        self.c = T()
        self.key = [("music", "a")]
        self.st = sleep.SleepTimer(clock=self.c)
        self.st.owner_fn = lambda: self.key[0]

    def test_time_turns_off_after_a_minute_of_nothing(self):
        self.st.set(30)
        self.key[0] = None
        self.assertIsNone(self.st.tick(None))
        self.c.t += 61
        self.st.tick(None)
        self.assertFalse(self.st.on)

    def test_time_goes_on_across_a_short_gap_and_stops(self):
        self.st.set(15)
        self.st.tick(None)
        self.c.t += 30
        self.assertIsNone(self.st.tick(("video", 1)))
        self.c.t += 15 * 60
        self.assertEqual(self.st.tick(("video", 1)), "stop")

    def test_end_mode_is_for_what_played_when_set(self):
        self.st.set(sleep.END)
        self.assertIsNone(self.st.tick(("music", "a")))
        self.assertEqual(self.st.tick(("music", "b")), "moved")
        self.assertFalse(self.st.on)
        self.st.set(sleep.END)
        self.st.tick(None)
        self.assertFalse(self.st.on)
