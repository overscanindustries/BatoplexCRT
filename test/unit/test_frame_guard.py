import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import app as app_mod  # noqa: E402


class TestFrameGuard(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(app_mod, "L")
        self.L = p.start()
        self.addCleanup(p.stop)
        a = self.app = app_mod.App.__new__(app_mod.App)
        a.stack = ["root", "child"]
        a.playback = type("P", (), {"session": "idle"})()
        a._fails, a._fail_top, a._fail_run, a._fail_log_t = 0, None, 0, 0.0
        a.pop = lambda: a.stack.pop()

    def fail(self, n):
        return [self.app._step_failed(ValueError("x")) for _ in range(n)]

    def test_three_on_one_screen_pop_it(self):
        self.fail(2)
        self.assertEqual(self.app.stack, ["root", "child"])
        self.fail(1)
        self.assertEqual(self.app.stack, ["root"])
        self.fail(10)
        self.assertEqual(self.app.stack, ["root"])            # never the root

    def test_no_pop_while_a_video_plays(self):
        self.app.playback.session = "playing"
        self.fail(10)
        self.assertEqual(self.app.stack, ["root", "child"])

    def test_thirty_in_a_row_exit_only_after_ten_seconds(self):
        t = [100.0]
        with mock.patch.object(app_mod.time, "monotonic", lambda: t[0]):
            self.assertEqual(self.fail(app_mod.FRAME_FATAL + 5), [False] * (app_mod.FRAME_FATAL + 5))
            t[0] += app_mod.FRAME_FATAL_S
            self.assertTrue(self.app._step_failed(ValueError("x")))

    def test_alternate_faults_log_few_tracebacks(self):
        for _ in range(20):
            self.fail(1)
            self.app._fail_run = 0                            # a good frame between faults
        self.assertEqual(self.L.exception.call_count, 1)

    def test_one_traceback_per_run(self):
        self.fail(20)
        self.assertEqual(self.L.exception.call_count, 1)
        self.assertLessEqual(self.L.warning.call_count, 3)    # the pop line, few short lines


class TestSaved(unittest.TestCase):
    def test_toast_once_in_the_gap(self):
        a = app_mod.App.__new__(app_mod.App)
        msgs = []
        a.toast = msgs.append
        a.saved(True)
        a.saved(False)
        a.saved(False)
        self.assertEqual(msgs, ["Couldn't save settings"])
        a._save_fail_t -= app_mod.SAVE_FAIL_GAP
        a.saved(False)
        self.assertEqual(len(msgs), 2)


if __name__ == "__main__":
    unittest.main()
