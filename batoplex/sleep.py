"""Sleep Timer: stop video or music after a time, or at the end of the
item that plays. One timer for the app: a time set in a video goes on into music. No pygame.
"""
import math
import time

MINUTES = (15, 30, 45, 60, 90)
END = "end"
IDLE_S = 60                     # a time turns off after this long with nothing playing


class SleepTimer:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.mode = None                # None | END | minutes (int)
        self.deadline = None
        self.owner = None               # what played when End was set (owner_fn)
        self.owner_fn = None            # the app: a key for the video or music that plays
        self.idle_at = None

    @property
    def on(self):
        return self.mode is not None

    @property
    def at_end(self):
        return self.mode == END

    def choices(self, end=True):
        return [None] + list(MINUTES) + ([END] if end else [])

    def set(self, mode):
        self.mode = mode if mode in self.choices() else None
        self.deadline = self.clock() + 60 * mode if isinstance(mode, int) else None
        self.owner = self.owner_fn() if (self.owner_fn and self.mode is not None) else None

    def step(self, d=1, end=True):
        """The next choice from the current one (A / Right), or the one before (Left). A
        running time steps from its own choice, so 30 then Right gives 45 minutes again."""
        opts = self.choices(end)
        i = opts.index(self.mode) if self.mode in opts else 0
        self.set(opts[(i + d) % len(opts)])
        return self.mode

    def clear(self):
        self.set(None)

    def left_min(self):
        if self.deadline is None:
            return 0
        return max(0, int(math.ceil((self.deadline - self.clock()) / 60.0)))

    def label(self, end_name="End of Episode"):
        if self.mode is None:
            return "Off"
        if self.mode == END:
            return end_name
        return "%d min" % self.left_min()

    def tick(self, key):
        """Once a frame, with the key of what plays (None: nothing). Returns "stop" when the
        time is up, "moved" when End mode ended because other video or music started.
        End mode is for what played when it was set; a time turns off after IDLE_S with
        nothing playing, so it never stops a later video."""
        if not self.on:
            self.idle_at = None
            return None
        if self.due():
            return "stop"
        if key is None:
            now = self.clock()
            if self.at_end:
                self.clear()
            elif self.idle_at is None:
                self.idle_at = now
            elif now - self.idle_at > IDLE_S:
                self.clear()
            return None
        self.idle_at = None
        if self.at_end and self.owner is not None and key != self.owner:
            self.clear()
            return "moved"
        return None

    def due(self):
        """True once when the time is up; the timer is then off."""
        if self.deadline is not None and self.clock() >= self.deadline:
            self.clear()
            return True
        return False


def choice_label(mode, end_name="End of Episode"):
    if mode is None:
        return "Off"
    if mode == END:
        return end_name
    return "%d minutes" % mode
