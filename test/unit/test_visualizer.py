import io
import os
import signal
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("BATOPLEX_HOME", tempfile.mkdtemp())

from batoplex import visualizer  # noqa: E402


class FakeProc:
    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        self.returncode = None
        self.signals = []
        self.stdout = io.BytesIO(b"")
        self.stderr = io.BytesIO(b"")

    def poll(self):
        return self.returncode

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self.send_signal(signal.SIGTERM)
        self.returncode = -15

    def kill(self):
        self.send_signal(signal.SIGKILL)
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Rig:
    def __init__(self, binary=True, parec=True, fail=()):
        self.t = 0.0
        self.procs = []
        self.fail = set(fail)          # program names whose spawn raises OSError
        self.bin = tempfile.NamedTemporaryFile(delete=False).name if binary else "/nonexistent"
        self.v = visualizer.Visualizer({"visualizer": self.bin, "mpv": "mpv"},
                                       spawn=self.spawn, clock=lambda: self.t,
                                       which=lambda n: "/usr/bin/parec" if parec else None)

    def spawn(self, args, **kw):
        name = os.path.basename(args[0])
        if name in self.fail or args[0] in self.fail:
            raise OSError("no " + name)
        p = FakeProc(args, **kw)
        self.procs.append(p)
        return p

    def named(self, name):
        return [p for p in self.procs if os.path.basename(p.args[0]) == name]

    @property
    def main(self):
        return self.v.proc


class TestVisualizer(unittest.TestCase):
    def setUp(self):
        sent = []
        self._orig_send = visualizer.Visualizer._send

        def capture(v, line):
            sent.append(line)
            self._orig_send(v, line)
        visualizer.Visualizer._send = capture
        self.sent = sent

    def tearDown(self):
        visualizer.Visualizer._send = self._orig_send

    def test_ready_then_running_and_keys(self):
        r = Rig()
        self.assertTrue(r.v.start())
        self.assertEqual((r.v.state, r.v.engine), ("starting", "projectm"))
        self.assertIn("--ctl-fd", r.main.args)
        self.assertIs(r.main.kw["stdin"], r.named("parec")[0].stdout)
        r.v.send("next")                         # not running yet: dropped
        self.assertEqual(self.sent, [])
        r.v.lines.extend(["ready AMD", "key left"])
        r.v.tick()
        self.assertEqual(r.v.state, "running")
        self.assertEqual(r.v.pop_key(), "left")
        r.v.send("next")
        self.assertEqual(self.sent, ["next"])

    def test_no_ready_in_time_gives_showcqt(self):
        r = Rig()
        r.v.start()
        first = r.main
        r.t = visualizer.READY_S
        r.v.tick()
        self.assertEqual(first.signals, [signal.SIGKILL])
        self.assertEqual((r.v.state, r.v.engine, r.v.broken), ("running", "showcqt", True))
        self.assertEqual(os.path.basename(r.main.args[0]), "mpv")
        self.assertEqual(len(r.named("parec")), 2)
        self.assertIsNotNone(r.named("parec")[0].returncode)   # the first parec ended

    def test_exit_before_ready_gives_showcqt(self):
        r = Rig()
        r.v.start()
        r.main.returncode = 2
        r.v.tick()
        self.assertEqual(r.v.engine, "showcqt")

    def test_missing_binary_gives_showcqt_at_once(self):
        r = Rig(binary=False)
        r.v.start()
        self.assertEqual((r.v.state, r.v.engine), ("running", "showcqt"))

    def test_showcqt_early_exit_is_unavailable(self):
        r = Rig(binary=False)
        r.v.start()
        r.t = 1.0
        r.main.returncode = 1
        r.v.tick()
        self.assertEqual((r.v.state, r.v.unavailable), ("idle", True))
        self.assertEqual(r.v.pop_event(), "unavailable")
        self.assertFalse(r.v.start())

    def test_showcqt_back_key_is_a_clean_end(self):
        r = Rig(binary=False)
        r.v.start()
        r.t = 1.0
        r.main.returncode = 0                      # ESC or Alt+F4 in the mpv window
        r.v.tick()
        self.assertEqual((r.v.state, r.v.unavailable, r.v.pop_event()), ("idle", False, None))

    def test_showcqt_early_exit_0_is_unavailable(self):
        r = Rig(binary=False)
        r.v.start()
        r.t = 1.0
        r.named("parec")[-1].returncode = 1
        r.main.returncode = 0                      # EOF: parec gave no audio
        r.v.tick()
        self.assertEqual((r.v.unavailable, r.v.pop_event()), (True, "unavailable"))

    def test_no_parec_and_no_binary_is_unavailable(self):
        r = Rig(binary=False, parec=False)
        self.assertFalse(r.v.start())
        self.assertTrue(r.v.unavailable)

    def test_no_parec_projectm_still_starts(self):
        r = Rig(parec=False)
        r.v.start()
        self.assertEqual(r.v.engine, "projectm")
        self.assertEqual(r.main.kw["stdin"], visualizer.subprocess.DEVNULL)

    def test_crash_after_ready_twice_sets_broken(self):
        r = Rig()
        for n in (1, 2):
            r.v.start()
            r.v.lines.append("ready x")
            r.v.tick()
            r.main.returncode = -11
            r.v.tick()
            self.assertEqual(r.v.state, "idle")
            self.assertEqual(r.v.pop_event(), "crashed")
            self.assertEqual(r.v.broken, n == 2)
        r.v.start()
        self.assertEqual(r.v.engine, "showcqt")

    def test_parec_end_restarts_projectm_after_a_wait_twice_at_most(self):
        r = Rig()
        r.v.start()
        for n in (1, 2, 3):
            r.v.lines.append("ready x")
            r.v.tick()
            first = r.main
            r.t += visualizer.AUDIO_OK_S
            r.named("parec")[-1].returncode = 1    # PipeWire restarted
            r.v.tick()
            self.assertIs(r.main, first)           # wait for PipeWire first
            r.t += visualizer.AUDIO_RETRY_S
            r.v.tick()
            if n < 3:
                self.assertEqual((r.v.state, r.v.audio_restarts), ("starting", n))
                self.assertEqual(first.signals, [signal.SIGKILL])
            else:                                  # no more restarts: the picture goes on
                self.assertEqual((r.v.state, r.main), ("running", first))
        self.assertIsNone(r.v.pop_event())

    def test_parec_that_never_worked_gives_no_restart(self):
        r = Rig()
        r.v.start()
        r.v.lines.append("ready x")
        r.v.tick()
        first = r.main
        r.t = 0.1
        r.named("parec")[-1].returncode = 1        # no pulse server: parec ends at once
        r.v.tick()
        for r.t in (visualizer.AUDIO_OK_S, visualizer.AUDIO_OK_S + visualizer.AUDIO_RETRY_S, 30.0):
            r.v.tick()
        self.assertIs(r.main, first)

    def test_failed_restart_does_not_break_projectm(self):
        r = Rig()
        r.v.start()
        r.v.lines.append("ready x")
        r.v.tick()
        r.t = visualizer.AUDIO_OK_S
        r.named("parec")[-1].returncode = 1
        r.v.tick()
        r.t += visualizer.AUDIO_RETRY_S
        r.v.tick()
        r.t += visualizer.READY_S                  # the new engine gives no "ready"
        r.v.tick()
        self.assertEqual((r.v.state, r.v.broken, r.v.pop_event()), ("idle", False, "crashed"))
        r.v.start()
        self.assertEqual(r.v.engine, "projectm")

    def test_stop_quit_then_term_then_kill(self):
        r = Rig()
        r.v.start()
        r.v.lines.append("ready x")
        r.v.tick()
        main, parec = r.main, r.named("parec")[0]
        r.v.stop()
        self.assertEqual(self.sent, ["quit"])
        self.assertIsNone(r.v.ctl)
        self.assertEqual(r.v.state, "stopping")
        r.t = 0.4
        r.v.tick()
        self.assertEqual(main.signals, [])
        r.t = visualizer.TERM_S
        r.v.tick()
        self.assertEqual(main.signals, [signal.SIGTERM])
        self.assertEqual(parec.signals, [signal.SIGTERM])
        r.t = visualizer.KILL_S
        r.v.tick()
        self.assertEqual(main.signals[-1], signal.SIGKILL)
        main.returncode = -9
        r.v.tick()
        self.assertEqual(r.v.state, "idle")
        self.assertIsNotNone(parec.returncode)

    def test_start_while_stopping_waits_for_reap(self):
        r = Rig()
        r.v.start()
        first = r.main
        r.v.stop()
        self.assertTrue(r.v.start())
        self.assertIs(r.main, first)               # no second engine yet
        first.returncode = 0
        r.v.tick()
        self.assertEqual(r.v.state, "starting")
        self.assertIsNot(r.main, first)

    def test_stop_cancels_a_waiting_start(self):
        r = Rig()
        r.v.start()
        r.v.stop()
        r.v.start()
        r.v.stop()
        r.main.returncode = 0
        r.v.tick()
        self.assertEqual(r.v.state, "idle")

    def test_full_pipe_drops_the_command(self):
        r = Rig()
        r.v.start()
        r.v.lines.append("ready x")
        r.v.tick()
        visualizer.Visualizer._send = self._orig_send
        for _ in range(20000):                     # fill the pipe; must not block or raise
            r.v.send("overlay /tmp/x 1 1 0 0 4.0")
        self.assertEqual(r.v.state, "running")

    def test_shutdown_kills_a_hung_engine(self):
        r = Rig()
        r.v.start()
        clock = [0.0]

        def tick_clock():
            clock[0] += 0.3
            return clock[0]
        r.v.clock = tick_clock
        r.v.shutdown(wait=1.0)
        self.assertEqual(r.v.state, "idle")


if __name__ == "__main__":
    unittest.main()
