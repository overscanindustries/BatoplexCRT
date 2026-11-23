"""Motion: one frame clock for all, and Anim, a value that moves from one
number to another over a time.

Timing rule: t = elapsed / ms, and the elapsed time of the first frame is one field
(16.683 ms), so the frame of the press already shows progress. value = frm + (to - frm) *
ease(t), with the distance from frm rounded to a multiple of q. A step from the value shown in
the previous frame is min_step at least; when less than min_step remains, the value is `to`.
Quad ease-out, 117 ms, q 4, min step 8 on 132 px gives 36, 64, 88, 108, 120, 132.

A late frame gives a larger step, still a multiple of q; the duration does not change. A
scripted run uses a fixed clock (frames x 16.683 ms), so test shots repeat.
"""
import math
import time

from .tokens import FIELD_MS

frame = 0          # frames since start; tick() adds 1
_now = 0.0         # ms on the frame clock
fixed = False      # a scripted run: the clock counts frames


def tick(t=None):
    """Once for each frame, before input. t: seconds (tests)."""
    global frame, _now
    frame += 1
    if t is not None:
        _now = t * 1000.0
    elif fixed:
        _now = frame * FIELD_MS
    else:
        _now = time.monotonic() * 1000.0


def now():
    """The frame clock in ms."""
    return _now


def EASE_OUT(t):
    return 1.0 - (1.0 - t) * (1.0 - t)


def LINEAR(t):
    return t


def _quant(d, q):
    if not q:
        return d
    s = -1 if d < 0 else 1
    return s * int(math.floor(abs(d) / q + 0.5)) * q


class Anim:
    def __init__(self, ms, ease=EASE_OUT, q=0, min_step=0):
        self.ms, self.ease, self.q, self.min_step = ms, ease, q, min_step
        self.frm = self.to = 0
        self.t0 = 0.0
        self._run = False
        self._shown = 0
        self._at = -1              # the frame of the last evaluation
        self._end = -1             # the frame in which the value reached `to`

    def start(self, frm, to):
        self.frm, self.to = frm, to
        self.t0 = _now - FIELD_MS
        self._shown, self._at = frm, -1
        self._run = frm != to and self.ms > 0
        self._end = -1 if self._run else frame

    def retarget(self, to):
        """From the value shown now to a new end; the clock starts again."""
        self.start(self.value, to)

    def stop(self, v=None):
        """End now at v (default: the end value)."""
        self.to = self.to if v is None else v
        self._run, self._at = False, -1
        self._end = frame

    def set(self, v):
        """A cut: the value is v at once."""
        self.frm = self.to = v
        self._run, self._at = False, -1
        self._end = frame

    @property
    def value(self):
        if self._at == frame:
            return self._shown
        self._at = frame
        if not self._run:
            self._shown = self.to
            return self.to
        t = (_now - self.t0) / self.ms
        if t >= 1.0:
            v = self.to
        else:
            raw = (self.to - self.frm) * self.ease(max(0.0, t))
            v = self.frm + _quant(raw, self.q) if self.q else self.frm + raw
            if self.min_step:
                d = v - self._shown
                if d and abs(d) < self.min_step:      # never a step under min_step
                    v = self._shown + (self.min_step if d > 0 else -self.min_step)
                rest = self.to - v
                if abs(rest) < self.min_step or (rest > 0) != (self.to - self.frm > 0):
                    v = self.to
        if v == self.to:
            self._run = False
            self._end = frame
        self._shown = v
        return v

    @property
    def active(self):
        """True while the value moves, and in the frame in which it reaches the end (that frame
        still has to be drawn)."""
        if self._run:
            self.value                                # may end it in this frame
        return self._run or self._end == frame

    @property
    def progress(self):
        """0..1 of the distance shown."""
        if self.to == self.frm:
            return 1.0
        return (self.value - self.frm) / float(self.to - self.frm)
