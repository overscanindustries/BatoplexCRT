"""The shared menu: Start on any screen, and the More (…) button on Detail.

A list on the right over the dimmed screen, in the style of the playback track panel.
Rows come from Screen.menu_items(); Settings is always the last row.
"""
import pygame

from .. import draw, inputs, tokens
from . import Overlay

W, ROW_H, HEAD_H = tokens.MENU_W, tokens.ROW_PITCH, 44
ROWS = 7


class MenuScreen(Overlay):
    name = "MENU"

    def __init__(self, app, under, title, rows):
        """rows: [(label, fn)]; fn runs after the menu closes."""
        super().__init__(app, under)
        self.title = title
        self.rows = rows
        self.focus = 0
        self.top = 0

    def handle(self, a):
        n = len(self.rows)
        if a in (inputs.UP, inputs.DOWN):
            self.focus = max(0, min(n - 1, self.focus + (1 if a == inputs.DOWN else -1)))
            if self.focus < self.top:
                self.top = self.focus
            elif self.focus >= self.top + self.visible_rows():
                self.top = self.focus - self.visible_rows() + 1
            x, y, w, h = self.rect()
            self._press = pygame.Rect(x - 4, y - 4, w + 8, h + 8)   # only the menu box changes
        elif a in (inputs.BACK, inputs.MENU):
            self.app.pop()
        elif a == inputs.OK and n:
            fn = self.rows[self.focus][1]
            self.app.pop()
            fn()
        elif a == inputs.QUIT:
            self.app.quit()

    def visible_rows(self):
        """Rows that fit the safe height (7 at most): the menu never starts above the safe top."""
        return draw.menu_fit(self.safe, ROWS)

    def rect(self):
        return draw.menu_rect(self.safe, min(self.visible_rows(), len(self.rows)))

    def draw(self, s):
        self.draw_under(s)
        draw.menu_panel(s, self.rect(), self.title, [r[0] for r in self.rows], self.focus,
                        top=self.top, fit=self.visible_rows())
