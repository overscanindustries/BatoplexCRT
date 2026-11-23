"""After Batoplex exits, end an evmapy left holding EmulationStation's launch pipe.

ES runs a game as `(emulatorlauncher ... ; echo $?) | head -300` and waits for head, which
waits until no process holds the pipe open. emulatorlauncher's controller monitor thread
restarts evmapy (`batocera-evmapy start`, a `nohup evmapy &`) when a pad reconnects. When that
happens just as the game stops, the restart comes after the context manager's own
`batocera-evmapy stop`, the new evmapy inherits the pipe as its stdout, and ES hangs.

Started by launcher/Batoplex.sh as it exits, detached and with no copy of any launch pipe:
    python3 -m batoplex.evmapy_guard <emulatorlauncher pid> <starttime> "pipe:[N] ..."
It waits until that emulatorlauncher has exited (so no Batocera context still needs an
evmapy of this launch), then ends only processes named evmapy that hold one of those pipes.
An evmapy of a later launch holds that launch's own pipe and is never touched. It stops when
no process holds the pipes any more, or after SETTLE_S."""
import os
import signal
import sys
import time

WAIT_S = 120       # emulatorlauncher runs gameStop scripts and a mode restore after the game
SETTLE_S = 15      # batocera-evmapy start waits up to 5 s for evmapy's ready flag
STEP_S = 0.25
KILL_AFTER_S = 1.0


def starttime(pid, proc="/proc"):
    """Field 22 of /proc/<pid>/stat (start time in clock ticks), or None if pid is gone."""
    try:
        with open("%s/%d/stat" % (proc, pid)) as f:
            stat = f.read()
    except (OSError, ValueError):
        return None
    try:
        return stat[stat.rindex(")") + 2:].split()[19]   # fields after "pid (comm)"
    except (ValueError, IndexError):
        return None


def name_of(pid, proc="/proc"):
    try:
        with open("%s/%d/comm" % (proc, pid)) as f:
            return f.read().strip()
    except OSError:
        return ""


def is_evmapy(pid, proc="/proc"):
    """evmapy is a Python script: comm is "evmapy" when run by its shebang; argv names it too."""
    if name_of(pid, proc) == "evmapy":
        return True
    try:
        with open("%s/%d/cmdline" % (proc, pid), "rb") as f:
            argv = f.read().split(b"\0")[:2]
    except OSError:
        return False
    return any(os.path.basename(a) == b"evmapy" for a in argv)


def holders(pipes, proc="/proc", me=None):
    """Pids (sorted) of the processes with an fd on one of the pipes."""
    want = set(pipes)
    out = []
    for ent in os.listdir(proc):
        if not ent.isdigit() or int(ent) == me:
            continue
        d = "%s/%s/fd" % (proc, ent)
        try:
            fds = os.listdir(d)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink("%s/%s" % (d, fd)) in want:
                    out.append(int(ent))
                    break
            except OSError:
                continue
    return sorted(out)


def guard(el_pid, el_start, pipes, proc="/proc", kill=os.kill, sleep=time.sleep,
          clock=time.monotonic, log=print):
    """Returns the pids it ended."""
    if not pipes:
        return []
    end = clock() + WAIT_S
    while starttime(el_pid, proc) == el_start:
        if clock() >= end:
            log("evmapy guard: emulatorlauncher still runs; nothing done")
            return []
        sleep(STEP_S)
    ended, first = [], {}
    end = clock() + SETTLE_S
    while clock() < end:
        held = holders(pipes, proc, me=os.getpid())
        if not held:
            break
        for pid in held:
            if not is_evmapy(pid, proc):
                continue              # head, the ES shell, inotifywait: they end on their own
            now = clock()
            sig = signal.SIGTERM if pid not in first else signal.SIGKILL
            if sig == signal.SIGKILL and now - first[pid] < KILL_AFTER_S:
                continue
            first.setdefault(pid, now)
            try:
                kill(pid, sig)
            except OSError:
                continue
            if pid not in ended:
                ended.append(pid)
                log("evmapy guard: ended evmapy %d (it held the ES launch pipe)" % pid)
        sleep(STEP_S)
    return ended


def main(argv):
    try:
        el_pid, el_start = int(argv[0]), argv[1]
    except (IndexError, ValueError):
        return 2
    pipes = " ".join(argv[2:]).split()          # one quoted word: no shell glob on "[N]"
    guard(el_pid, el_start, [p for p in pipes if p.startswith("pipe:[")],
          log=lambda m: print(time.strftime("%Y-%m-%d %H:%M:%S ") + m, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
