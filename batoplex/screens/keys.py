"""The on-screen key grid and the text field of Search, shared with Add
Stations. The screen owns the query and the geometry; g is a dict with
x0, x1, keys_top, kh, kp, pitch and side_x."""

import pygame

from .. import draw, inputs, lineup, tokens
from ..tokens import C

ROWS = ("abcdefghijkl", "mnopqrstuvwx", "yz0123456789")
SIDE = ("Space", "Delete", "Clear")


class KeyGrid:
    """Key focus (row, column; column 12 is Space / Delete / Clear) and its moves. on_clear
    and on_retry are separate: Clear empties the field, then on_retry
    may load again after an error."""

    def __init__(self, on_char, on_delete, on_clear, on_retry=None):
        self.on_char, self.on_delete = on_char, on_delete
        self.on_clear, self.on_retry = on_clear, on_retry
        self.kr, self.kc = 0, 0
        self.typing = False          # a real keyboard typed: no key shows focus

    def handle(self, a, can_leave):
        """A d-pad or OK action in the grid. Returns True when the focus leaves the grid
        downward (can_leave() is true); the screen then moves it below."""
        if self.typing:
            if a in (inputs.DOWN, inputs.OK):
                if can_leave():
                    return True
                if a == inputs.DOWN:
                    self.typing = False
                return False
            if a in (inputs.LEFT, inputs.RIGHT, inputs.UP):
                self.typing = False
                return False
        if a == inputs.LEFT and self.kc > 0:
            self.kc -= 1
        elif a == inputs.RIGHT and self.kc < 12:
            self.kc += 1
        elif a == inputs.UP and self.kr > 0:
            self.kr -= 1
        elif a == inputs.DOWN:
            if self.kr < 2:
                self.kr += 1
            elif can_leave():
                return True
        elif a == inputs.OK:
            if self.kc == 12:
                if self.kr == 0:
                    self.on_char(" ")
                elif self.kr == 1:
                    self.on_delete()
                else:
                    self.on_clear()
                    if self.on_retry:
                        self.on_retry()
            else:
                self.on_char(ROWS[self.kr][self.kc])
        return False


def key_rect(g, r, c):
    y = g["keys_top"] + r * g["kp"]
    if c == 12:
        return pygame.Rect(g["side_x"], y, g["x1"] - g["side_x"], g["kh"])
    return pygame.Rect(g["x0"] + c * g["pitch"], y, g["pitch"] - 4, g["kh"])


def draw_keys(s, g, grid, show):
    """show: the grid has the focus (and no real keyboard typed). Raised keys,
    list text; the focused key in the focus fill."""
    for r in range(3):
        for c in range(13):
            rect = key_rect(g, r, c)
            foc = show and (r, c) == (grid.kr, grid.kc)
            if foc:
                draw.focus_fill(s, rect)
            else:
                draw.rect(s, C["raised"], rect)
            label = SIDE[r] if c == 12 else ROWS[r][c]
            draw.text(s, label, "list", C["ink"] if foc else C["text"], rect.x,
                      rect.y + (rect.h - 24) // 2, w=rect.w, align="center")


def keys_top(T):
    """The keys start 44 px under the safe top on every key screen."""
    return T + 44


def draw_field(s, x0, x1, T, q, placeholder, scr=None):
    """The 32 px field and the clock (at the safe top). scr: also draw that screen's music
    mark left of the clock and keep its room free."""
    clock = lineup.clock_hm()
    cw = draw.width(clock, "small")
    draw.text(s, clock, "small", C["text2"], x1, T, align="right")
    mark = scr.music_mark(s, x1 - cw - 16) if scr is not None else 0
    fw = x1 - x0 - cw - 24 - mark
    draw.rect(s, C["raised"], (x0, T, fw, tokens.CHIP_H))
    room = fw - 24 - 4
    if not q:
        draw.rect(s, C["text"], (x0 + 12, T + 6, 2, 20))
        draw.text(s, placeholder, "list", C["dim"], x0 + 18, T + 4, w=room - 6)
        return
    t = q
    if draw.width(t, "list") > room:
        while t and draw.width("…" + t, "list") > room:
            t = t[1:]
        t = "…" + t
    tw = draw.text(s, t, "list", C["text"], x0 + 12, T + 4)
    draw.rect(s, C["text"], (x0 + 12 + tw + 2, T + 6, 2, 20))
