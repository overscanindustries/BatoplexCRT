import os
import shutil
import signal
import tempfile
import unittest

from batoplex import evmapy_guard as g


class FakeProc:
    """A /proc with stat, comm, cmdline and fd symlinks ("pipe:[N]" targets)."""

    def __init__(self):
        self.root = tempfile.mkdtemp()

    def add(self, pid, comm, argv, fds, start="500"):
        d = os.path.join(self.root, str(pid))
        os.makedirs(os.path.join(d, "fd"))
        with open(os.path.join(d, "stat"), "w") as f:
            f.write("%d (%s) S 1 %s\n" % (pid, comm, " ".join(["0"] * 17 + [start, "0"])))
        with open(os.path.join(d, "comm"), "w") as f:
            f.write(comm + "\n")
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"\0".join(a.encode() for a in argv) + b"\0")
        for fd, target in fds.items():
            os.symlink(target, os.path.join(d, "fd", str(fd)))

    def remove(self, pid):
        shutil.rmtree(os.path.join(self.root, str(pid)), ignore_errors=True)


class Clock:
    def __init__(self, on_tick=None):
        self.t = 0.0
        self.on_tick = on_tick

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.on_tick:
            self.on_tick(self.t)


ES_PIPE = "pipe:[111]"


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.p = FakeProc()
        self.addCleanup(shutil.rmtree, self.p.root, True)
        self.p.add(10, "emulatorlaunch", ["python", "/usr/bin/emulatorlauncher"],
                   {1: ES_PIPE, 2: "/userdata/system/logs/es_launch_stderr.log"}, start="900")
        self.p.add(11, "head", ["head", "-300"], {0: ES_PIPE})
        self.p.add(20, "evmapy", ["/usr/bin/python", "/usr/bin/evmapy"], {1: ES_PIPE, 2: "x"})
        self.p.add(30, "evmapy", ["/usr/bin/python", "/usr/bin/evmapy"], {1: "pipe:[222]"})
        self.p.add(40, "sh", ["sh", "-c", "evmapy"], {1: ES_PIPE})
        self.killed = []

    def kill(self, pid, sig):
        self.killed.append((pid, sig))
        self.p.remove(pid)
        if pid == 20:
            self.p.remove(11)              # the pipe closes: head gets EOF and ends
            self.p.remove(40)

    def test_reads_pipes_and_start_time(self):
        self.assertEqual(g.starttime(10, proc=self.p.root), "900")
        self.assertIsNone(g.starttime(99, proc=self.p.root))
        self.assertEqual(g.holders([ES_PIPE], proc=self.p.root), [10, 11, 20, 40])

    def test_ends_only_the_evmapy_on_this_pipe_after_emulatorlauncher(self):
        def tick(t):
            if t >= 2:
                self.p.remove(10)          # emulatorlauncher exits after 2 s
        clock = Clock(tick)

        def kill(pid, sig):
            self.assertGreaterEqual(clock.t, 2)
            self.kill(pid, sig)
        ended = g.guard(10, "900", [ES_PIPE], proc=self.p.root, kill=kill,
                        sleep=clock.sleep, clock=clock, log=lambda m: None)
        self.assertEqual(ended, [20])
        self.assertEqual(self.killed, [(20, signal.SIGTERM)])
        self.assertTrue(os.path.isdir(os.path.join(self.p.root, "30")))   # another launch's

    def test_kills_hard_when_term_is_ignored(self):
        self.p.remove(10)
        calls = []

        def kill(pid, sig):
            calls.append((pid, sig))
            if sig == signal.SIGKILL:
                self.kill(pid, sig)
        clock = Clock()
        g.guard(10, "900", [ES_PIPE], proc=self.p.root, kill=kill, sleep=clock.sleep,
                clock=clock, log=lambda m: None)
        self.assertEqual(calls, [(20, signal.SIGTERM), (20, signal.SIGKILL)])

    def test_nothing_while_emulatorlauncher_runs(self):
        clock = Clock()
        ended = g.guard(10, "900", [ES_PIPE], proc=self.p.root, kill=self.kill,
                        sleep=clock.sleep, clock=clock, log=lambda m: None)
        self.assertEqual(ended, [])
        self.assertEqual(self.killed, [])

    def test_a_reused_pid_counts_as_ended(self):
        self.p.remove(10)
        self.p.add(10, "bash", ["bash"], {}, start="5000")
        clock = Clock()
        ended = g.guard(10, "900", [ES_PIPE], proc=self.p.root, kill=self.kill,
                        sleep=clock.sleep, clock=clock, log=lambda m: None)
        self.assertEqual(ended, [20])


if __name__ == "__main__":
    unittest.main()
