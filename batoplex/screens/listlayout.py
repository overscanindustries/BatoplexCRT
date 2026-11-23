"""The settings-style list shared by Settings, More and Radio: a thin caller of draw.list_rows.

Rows are (kind, key, label, value). kind "head" is a group heading (36 px, not focusable);
kind "text" is a row that only shows text (not focusable); every other kind
is a row, 40 px fill on a 44 px pitch. The list scrolls so that the focused
row, and the heading above a group's first row, stay in view. A 4 px bar shows the scroll.
"""
from .. import draw, tokens

ROW_H, HEAD_H = tokens.ROW_H, tokens.HEAD_H


def items(rows):
    """The focusable rows: the focus index counts these."""
    return [r for r in rows if draw.focusable(r)]


def area(scr, width=None, skip=0):
    """(x, full width, y0, y1) of the list: first row at T+44, 48 px above the safe bottom
    (the hint line and a 24 px gap). width: a narrower list, left-aligned. skip: px kept free
    above the first row (state text)."""
    sf = scr.safe
    full = 640 - sf["left"] - sf["right"]
    return (sf["left"], min(full, width or full), sf["top"] + 44 + skip,
            480 - sf["bottom"] - 48)


def draw_list(scr, s, rows, focus, enabled=None, current=None, width=None, skip=0):
    """Draw rows and keep scr.top (pixels) so that the focused row shows."""
    if not items(rows):
        return
    x, full, y0, y1 = area(scr, width, skip)
    scr.top = draw.list_rows(s, rows, focus, x, y0, y1, full, getattr(scr, "top", 0),
                             enabled=enabled, current=current)
