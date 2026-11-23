"""Drawing helpers that follow the interlace rules: even y positions, 2-4 px horizontal
lines, square corners, text at integer even rows."""
import collections
import threading

import pygame

from . import fonts, tokens
from .tokens import C


def even(v):
    return int(v) & ~1


TEXT_CACHE_N = 512
TEXT_CACHE_BYTES = 8 << 20           # and about 8 MB of pixels
_texts = collections.OrderedDict()   # (font, text, colour, w) -> rendered line
_texts_ver = [None]
_texts_bytes = [0]
_texts_lock = threading.Lock()


def _line(f, upper, s, color, w):
    """The rendered line (upper case, ellipsized to w), kept in an LRU of 512 entries and
    about 8 MB that empties when the theme changes (theme.version). Callers only blit it."""
    from . import theme
    key = (id(f), upper, s, tuple(color), w)
    with _texts_lock:
        if _texts_ver[0] != theme.version:
            _texts.clear()
            _texts_ver[0] = theme.version
            _texts_bytes[0] = 0
        img = _texts.get(key)
        if img is not None:
            _texts.move_to_end(key)
            return img
    t = s.upper() if upper else s
    if w is not None:
        t = ellipsize(f, t, w)
    img = f.render(t, True, color)
    with _texts_lock:
        old = _texts.pop(key, None)              # another thread rendered it too
        if old is not None:
            _texts_bytes[0] -= _nbytes(old)
        _texts[key] = img
        _texts_bytes[0] += _nbytes(img)
        while len(_texts) > 1 and (len(_texts) > TEXT_CACHE_N
                                   or _texts_bytes[0] > TEXT_CACHE_BYTES):
            _texts_bytes[0] -= _nbytes(_texts.popitem(last=False)[1])
    return img


def _nbytes(img):
    w, h = img.get_size()
    return w * h * img.get_bytesize()


def text(surf, s, style, color, x, y, w=None, align="left", alpha=None):
    """Draw one line. y is the top of the line box (snapped even). Returns the width used."""
    f, line, upper = fonts.style(style)
    if alpha is None:
        img = _line(f, upper, s, color, w)
    else:                                       # set_alpha changes the surface: not kept
        s = s.upper() if upper else s
        if w is not None:
            s = ellipsize(f, s, w)
        img = f.render(s, True, color)
        img.set_alpha(alpha)
    tx = x
    if align == "right":
        tx = x - img.get_width()
    elif align == "center":
        tx = x + ((w or 0) - img.get_width()) // 2
    ty = even(y + (line - f.get_height()) // 2)
    surf.blit(img, (int(tx), ty))
    return img.get_width()


def width(s, style):
    f, _, upper = fonts.style(style)
    return f.size(s.upper() if upper else s)[0]


def ellipsize(f, s, w):
    if f.size(s)[0] <= w:
        return s
    ell = "…"
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.size(s[:mid].rstrip() + ell)[0] <= w:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo].rstrip() + ell


def wrap(s, style, w, max_lines):
    f, _, upper = fonts.style(style)
    s = s.upper() if upper else s
    words, lines, cur = s.split(), [], ""
    for i, wd in enumerate(words):
        trial = (cur + " " + wd).strip()
        if f.size(trial)[0] <= w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = wd
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = len(" ".join(lines).split())
    if consumed < len(words) and lines:
        lines[-1] = ellipsize(f, lines[-1] + " " + " ".join(words[consumed:]), w)
        if not lines[-1].endswith("\u2026"):
            lines[-1] = ellipsize(f, lines[-1] + "\u2026", w)
    return lines[:max_lines]


def paragraph(surf, s, style, color, x, y, w, max_lines):
    _, line, _ = fonts.style(style)
    lines = wrap(s, style, w, max_lines)
    for i, ln in enumerate(lines):
        text(surf, ln, style, color, x, y + i * line)
    return len(lines) * line


def rect(surf, color, r, alpha=None):
    x, y, w, h = r
    r = pygame.Rect(int(x), even(y), int(w), max(2, even(h)))
    if alpha is None or alpha >= 255:
        surf.fill(color, r)
    else:
        tmp = pygame.Surface(r.size)
        tmp.fill(color)
        tmp.set_alpha(alpha)
        surf.blit(tmp, r.topleft)


def frame(surf, color, r, t):
    """Outline of thickness t (even) drawn outside rect r."""
    x, y, w, h = r
    rect(surf, color, (x - t, y - t, w + 2 * t, t))
    rect(surf, color, (x - t, y + h, w + 2 * t, t))
    surf.fill(color, (x - t, y, t, h))
    surf.fill(color, (x + w, y, t, h))


def focus_frame(surf, r):
    """Artwork focus: a 4 px focus outline outside the art, a 2 px bg
    keyline outside that."""
    x, y, w, h = r
    o, k = tokens.FOCUS_OUTLINE, tokens.FOCUS_KEYLINE
    frame(surf, C["bg"], (x - o, y - o, w + 2 * o, h + 2 * o), k)
    frame(surf, C["focus"], r, o)


def progress(surf, x, y, w, frac, track=None, fill=None):
    rect(surf, track or C["track"], (x, y, w, tokens.PROGRESS_H))
    if frac > 0:
        rect(surf, fill or C["accent"], (x, y, max(4, int(w * frac)), tokens.PROGRESS_H))


def hints(surf, items, x, y, w, rec=None, ox=0, oy=0):
    """items: list of (glyph label, text). Glyph = 24 px disc in text2 with the letter in bg;
    label in small text2; 8 px glyph to label, 24 px between hints, 4 at most."""
    t = _texter(rec, ox, oy)
    cx = x
    for glyph, label in items[:4]:
        gw = glyph_draw(surf, glyph, cx, y)
        cx += gw + 8
        cx += t(surf, label, "small", C["text2"], cx, y) + 24


def glyph_draw(surf, glyph, x, y):
    f = fonts.get("bold", 16)
    if glyph == "menu":      # Xbox Menu button: three bars
        pygame.draw.circle(surf, C["text2"], (x + 12, y + 12), 12)
        for k in (6, 10, 14):
            surf.fill(C["bg"], (x + 6, y + k, 12, 2))
        return 24
    if glyph == "updown":    # D-pad up/down
        pygame.draw.circle(surf, C["text2"], (x + 12, y + 12), 12)
        triangle(surf, C["bg"], x + 7, y + 4, up=True, size=10)
        triangle(surf, C["bg"], x + 7, y + 14, up=False, size=10)
        return 24
    if glyph == "dpad":
        surf.fill(C["text2"], (x + 8, y, 8, 24))
        surf.fill(C["text2"], (x, y + 8, 24, 8))
        return 24
    if len(glyph) == 1:
        pygame.draw.circle(surf, C["text2"], (x + 12, y + 12), 12)
        img = f.render(glyph, True, C["bg"])
        surf.blit(img, (x + 12 - img.get_width() // 2, even(y + 12 - img.get_height() // 2)))
        return 24
    img = f.render(glyph, True, C["bg"])
    w = img.get_width() + 16
    surf.fill(C["text2"], (x, y, w, 24))
    surf.blit(img, (x + 8, even(y + 12 - img.get_height() // 2)))
    return w


def triangle(surf, color, x, y, up=False, size=12):
    h = size // 2 * 2
    pts = [(x, y + h), (x + size, y + h), (x + size // 2, y)] if up else \
        [(x, y), (x + size, y), (x + size // 2, y + h)]
    pygame.draw.polygon(surf, color, pts)


def triangle_left(surf, color, x, y, size=12):
    """A left-pointing triangle, size tall (even) and size // 2 + 2 wide."""
    h = size // 2 * 2
    pygame.draw.polygon(surf, color, [(x + size // 2 + 2, y), (x + size // 2 + 2, y + h), (x, y + h // 2)])


def triangle_right(surf, color, x, y, size=12):
    """A right-pointing triangle, size tall (even) and size // 2 + 2 wide."""
    h = size // 2 * 2
    pygame.draw.polygon(surf, color, [(x, y), (x, y + h), (x + size // 2 + 2, y + h // 2)])


# ------------------------------------------------------------ components
# One function for each component. Every helper that draws text takes rec, ox, oy: with an
# overlay Recorder the text goes through rec.text, so the overlay tests still see it.

def _texter(rec, ox=0, oy=0):
    if rec is None:
        return lambda surf, s, style, col, x, y, w=None, align="left": text(
            surf, s, style, col, x, y, w=w, align=align)
    return lambda surf, s, style, col, x, y, w=None, align="left": rec.text(
        surf, s, style, col, x, y, w=w, align=align, ox=ox, oy=oy)


_cache = {}


def cached(key, build):
    """A coloured surface kept until the theme changes (theme.version)."""
    from . import theme
    hit = _cache.get(key)
    if hit is None or hit[0] != theme.version:
        hit = (theme.version, build())
        _cache[key] = hit
    return hit[1]


def background(surf, scr):
    """A screen's solid background: the `bg` fill, then the idle Ribbon ambient when it shows
    on this screen."""
    surf.fill(C["bg"])
    app = getattr(scr, "app", None)
    amb = getattr(app, "ambient", None)
    if amb is not None and amb.on and app.stack and app.stack[-1] is scr:
        amb.draw(surf)


def _cut():
    from . import theme
    return theme.cur.focus == "cut"


def _hc():
    from . import theme
    return theme.high_contrast


def focus_fill(surf, r):
    """Control focus: a solid focus fill (Console: an 8 px cut on the top-left corner)."""
    x, y, w, h = (int(v) for v in r)
    y, h = even(y), max(2, even(h))
    if _cut():
        pygame.draw.polygon(surf, C["focus"], _cut_pts(x, y, w, h))
    else:
        surf.fill(C["focus"], (x, y, w, h))


def _cut_pts(x, y, w, h):
    k = tokens.CUT
    return [(x + k, y), (x + w - 1, y), (x + w - 1, y + h - 1), (x, y + h - 1), (x, y + k)]


def focus_ring(surf, centre, radius):
    """Round art focus: a 2 px gap, then a 4 px focus ring."""
    pygame.draw.circle(surf, C["focus"], centre, radius + 2 + 4, 4)


def current_bar(surf, r):
    """Current, not focused: a 4 px accent bar at the row's left edge."""
    x, y, _w, h = r
    rect(surf, C["accent"], (x, y, 4, h))


def scrim(surf, r=None):
    """The one scrim under menus, dialogs and the side panel: bg at tokens.SCRIM."""
    rect(surf, C["bg"], r or (0, 0, tokens.MODE_W, tokens.MODE_H), alpha=tokens.SCRIM)


def panel_fill(surf, r):
    """A floating panel (dialog, menu, toast): raised, no frame; High Contrast adds a 2 px
    text2 frame inside the edge. Console: the 8 px cut on the top-left corner."""
    if _cut():
        x, y, w, h = (int(v) for v in r)
        pygame.draw.polygon(surf, C["raised"], _cut_pts(x, y, w, h))
        if _hc():
            pygame.draw.polygon(surf, C["text2"], _cut_pts(x + 2, y + 2, w - 4, h - 4), 2)
        return
    rect(surf, C["raised"], r)
    if _hc():
        x, y, w, h = r
        frame(surf, C["text2"], (x + 2, y + 2, w - 4, h - 4), 2)


def button_w(label=None, icon=False):
    if icon and not label:
        return tokens.ICON_BTN_W
    return max(tokens.BTN_MIN_W, width(label or "", "label") + 2 * tokens.BTN_PAD)


def button(surf, r, label=None, icon=None, kind="secondary", focused=False, on=False,
           enabled=True, surface="bg", rest=None, rec=None, ox=0, oy=0):
    """kind: primary | secondary (icon and toggle buttons are secondary with an
    icon). icon: a callable(surf, x, y, colour) that draws 24 px. on: the toggle bar.
    rest: the secondary rest fill on a tinted page."""
    x, y, w, h = r
    focused = focused and enabled
    if focused:
        focus_fill(surf, r)
        fg = C["ink"]
    else:
        primary = kind == "primary" and enabled
        fill = C["accent"] if primary else (rest or (C["track"] if surface == "raised"
                                                     else C["raised"]))
        rect(surf, fill, r)
        fg = C["ink"] if primary else (C["text"] if enabled else C["dim"])
    if icon is not None:
        icon(surf, x + (w - 24) // 2, y + (h - 24) // 2, fg)
    if label:
        _texter(rec, ox, oy)(surf, label, "label", fg, x, y + 8, w=w, align="center")
    if on:
        rect(surf, C["ink"] if focused else C["accent"], (x + 8, y + h - 4, w - 16, 4))


def row(surf, r, label, value=None, focused=False, current=False, enabled=True, rec=None,
        ox=0, oy=0, mark=None):
    """Fill 40 in a 44 pitch. Label at x+16, y+8 in label; value right-aligned
    at x+w-16 in text2, 240 px at most. Focused: all content in ink. mark: a callable(surf, x,
    y, colour) that draws a 24 px icon right-aligned at x+w-16 (the chosen track's check)."""
    x, y, w = r[0], r[1], r[2]
    t = _texter(rec, ox, oy)
    if focused:
        focus_fill(surf, (x, y, w, tokens.ROW_H))
        col = vcol = C["ink"]
    else:
        if current:
            current_bar(surf, (x, y, w, tokens.ROW_H))
        col = C["text"] if enabled else C["dim"]
        vcol = C["text2"] if enabled else C["dim"]
    vw = min(240, width(value, "label")) + 16 if value else 0
    if mark is not None:
        mark(surf, x + w - tokens.INSET - 24, y + 8, col)
        vw = max(vw, 24 + 8)
    t(surf, label, "label", col, x + tokens.INSET, y + 8, w=w - 2 * tokens.INSET - vw)
    if value:
        t(surf, value, "label", vcol, x + w - tokens.INSET, y + 8, w=240, align="right")


def row_compact(surf, r, num, title, second=None, right=None, focused=False, playing=False,
                dimmed=False, nw=20, moving=False, rec=None, ox=0, oy=0):
    """Fill 32 in a 36 pitch, list at y+4. num right-aligned in small dim,
    ending at x+16+nw; the title 12 px after it. playing: an accent triangle for the number.
    moving: the row in Move mode: the focus fill, up and down triangles in ink."""
    x, y, w = r[0], r[1], r[2]
    t = _texter(rec, ox, oy)
    h = tokens.ROW_COMPACT_H
    focused = focused or moving
    if focused:
        focus_fill(surf, (x, y, w, h))
    ink = C["ink"]
    nx = x + tokens.INSET + nw
    if moving:
        triangle(surf, ink, nx - 10, y + 4, up=True, size=10)
        triangle(surf, ink, nx - 10, y + 18, up=False, size=10)
    elif playing:
        col = ink if focused else C["accent"]
        pygame.draw.polygon(surf, col, [(nx - 10, y + 10), (nx - 10, y + 22), (nx, y + 16)])
    elif num is not None:
        t(surf, str(num), "small", ink if focused else C["dim"], nx, y + 4, align="right")
    tx = nx + 12 if nw else x + tokens.INSET      # nw 0: no number column (recent searches)
    rw = 0
    if right:
        rw = width(right, "small") + 16
        t(surf, right, "small", ink if focused else C["text2"], x + w - tokens.INSET, y + 4,
          align="right")
    room = x + w - tokens.INSET - rw - tx
    tcol = ink if focused else (C["dim"] if dimmed and not playing else C["text"])
    used = t(surf, title, "list", tcol, tx, y + 4, w=room)
    if second:
        sep = " • "
        free = room - used - width(sep, "list")
        if free >= 60:
            t(surf, sep + second, "list", ink if focused else C["dim"], tx + used, y + 4,
              w=room - used)


def row_two(surf, r, label, line2, right=None, focused=False, current=False, enabled=True,
            rec=None, ox=0, oy=0, moving=False):
    """Fill 56 in a 60 pitch. Line 1 label at y+4; line 2 small text2 at
    y+28. moving: the row in Move mode: the focus fill, up and down triangles in ink at
    the right (in place of `right`)."""
    x, y, w = r[0], r[1], r[2]
    t = _texter(rec, ox, oy)
    if moving:
        focused, right = True, None
        focus_fill(surf, (x, y, w, tokens.ROW_TWO_H))
        triangle(surf, C["ink"], x + w - tokens.INSET - 12, y + 14, up=True, size=12)
        triangle(surf, C["ink"], x + w - tokens.INSET - 12, y + 30, up=False, size=12)
        w -= 24
    elif focused:
        focus_fill(surf, (x, y, w, tokens.ROW_TWO_H))
    elif current:
        current_bar(surf, (x, y, w, tokens.ROW_TWO_H))
    c1 = C["ink"] if focused else (C["text"] if enabled else C["dim"])
    c2 = C["ink"] if focused else (C["text2"] if enabled else C["dim"])
    rw = width(right, "small") + 16 if right else 0
    ly = y + 4 if line2 else y + (tokens.ROW_TWO_H - 24) // 2   # one line is centred
    t(surf, label, "label", c1, x + tokens.INSET, ly, w=w - 2 * tokens.INSET - rw)
    if line2:
        t(surf, line2, "small", c2, x + tokens.INSET, y + 28, w=w - 2 * tokens.INSET - rw)
    if right:
        t(surf, right, "small", c2, x + w - tokens.INSET, y + 4, align="right")


def heading_row(surf, x, y, w, label, rec=None, ox=0, oy=0):
    """A group heading in a list: 36 high, tag caps text2 at x+16, y+12."""
    _texter(rec, ox, oy)(surf, label, "tag", C["text2"], x + tokens.INSET, y + 12,
                         w=w - 2 * tokens.INSET)


def scrollbar(surf, x, y, h, top, view, total):
    """4 px: track raised, thumb text2, 16 px at least. top, view, total in pixels or rows."""
    if total <= view:
        return
    th = max(16, h * view // total) // 2 * 2
    ty = even(y + (h - th) * top // max(1, total - view))
    rect(surf, C["raised"], (x, y, tokens.SCROLLBAR_W, h))
    rect(surf, C["text2"], (x, ty, tokens.SCROLLBAR_W, th))


def focusable(r):
    """A list row the focus can take: not a heading, not a text row."""
    return r[0] not in ("head", "text", "gap")


def list_layout(rows, focus, view, top):
    """The scroll rule of the settings-style list. rows: (kind, key, label, value), kind "head"
    is a heading, kind "text" a row the focus skips. focus counts focusable rows. Returns
    (positions [(row, y, h)], new top, total height)."""
    pos, yy, fi, k = [], 0, None, -1
    for r in rows:
        h = tokens.HEAD_H if r[0] == "head" else \
            tokens.GROUP_GAP if r[0] == "gap" else tokens.ROW_PITCH
        pos.append((r, yy, h))
        if focusable(r):
            k += 1
            if k == focus:
                fi = (len(pos) - 1, yy, h)
        yy += h
    if fi is not None:
        idx, fy, fh = fi
        if fy < top:
            top = fy
            if idx > 0 and pos[idx - 1][0][0] == "head":   # keep the group heading in view
                top = pos[idx - 1][1]
        elif fy + fh > top + view:
            top = fy + fh - view
    top = max(0, min(top, max(0, yy - view)))
    return pos, top, yy


CHEVRON = "\x00chevron"     # a row value that draws chevron() (opens a page)


def chevron(surf, x, y, col):
    """The "opens a page" mark: the house triangle (a thin ">" flickers on an interlaced line),
    12 px tall, centred in the 24 px mark slot of row()."""
    triangle_right(surf, col, x + 8, y + 6)


def list_rows(surf, rows, focus, x, y0, y1, full, top=0, enabled=None, current=None):
    """The settings-style list (Settings, More, Radio). Width = full - 12 when the scroll bar
    shows. enabled(row) / current(row) mark a row. Returns the new top (pixels)."""
    its = [r for r in rows if focusable(r)]
    focus = max(0, min(focus, len(its) - 1)) if its else -1
    view = y1 - y0
    pos, top, total = list_layout(rows, focus, view, top)
    w = full - 12 if total > view else full
    clip = surf.get_clip()
    surf.set_clip((0, y0, tokens.MODE_W, view))
    k = -1
    for r, ry, h in pos:
        y = y0 + ry - top
        if focusable(r):
            k += 1
        if r[0] == "gap":                            # space only
            continue
        fill = h if r[0] == "head" else tokens.ROW_H
        if y < y0 or y + fill > y1:                  # a cut row shows no cut text
            continue
        if r[0] == "head":
            heading_row(surf, x, y, w, r[2])
            continue
        chev = r[3] == CHEVRON
        row(surf, (x, y, w), r[2], None if chev else (r[3] or None),
            focused=focusable(r) and k == focus, current=bool(current and current(r)),
            enabled=enabled(r) if enabled else True, mark=chevron if chev else None)
    surf.set_clip(clip)
    scrollbar(surf, x + full - tokens.SCROLLBAR_W, y0, view, top, view, total)
    return top


def tab_widths(labels):
    return [width(t, "list") + 24 for t in labels]


def tabs(surf, labels, sel, focused, x0, x1, y, off=0, rec=None, ox=0, oy=0, bg=None):
    """32 high, list text at y+4, item width text + 24, gap 8. Rest text2;
    selected with the focus elsewhere: text and a 4 px accent underline at y+28; focused: the
    focus fill and ink. Overflow scrolls so the selected tab shows whole; a 12 px triangle
    shows at a cut edge, on bg (the colour under the line: raised on a panel). Returns the new
    offset."""
    t = _texter(rec, ox, oy)
    ws = tab_widths(labels)
    xs, cx = [], 0
    for w in ws:
        xs.append(cx)
        cx += w + 8
    total = cx - 8 if ws else 0
    avail = x1 - x0
    if total <= avail:
        off = 0
    elif ws:
        cue = tokens.CUE + 4
        lo, hi = xs[sel], xs[sel] + ws[sel]
        if lo - off < (cue if off > 0 else 0):
            off = max(0, lo - cue)
        if hi - off > avail - cue:
            off = hi - avail + cue
        off = max(0, min(off, total - avail))
    clip = surf.get_clip()
    # Inside the caller's clip: the tab line slides up under the top bar with the grid.
    surf.set_clip(pygame.Rect(x0, y - 4, avail, tokens.CHIP_H + 8).clip(clip))
    for i, label in enumerate(labels):
        tx = x0 + xs[i] - off
        if tx + ws[i] < x0 or tx > x1:
            continue
        if i == sel and focused:
            focus_fill(surf, (tx, y, ws[i], tokens.CHIP_H))
            col = C["ink"]
        else:
            col = C["text"] if i == sel else C["text2"]
        tw = t(surf, label, "list", col, tx + 12, y + 4)
        if i == sel and not focused:
            rect(surf, C["accent"], (tx + 12, y + 28, tw, 4))
    surf.set_clip(clip)
    if off > 0:
        rect(surf, bg or C["bg"], (x0, y, tokens.CUE + 4, tokens.CHIP_H))
        triangle_left(surf, C["text2"], x0, y + 10, size=tokens.CUE)
    if total - off > avail:
        rect(surf, bg or C["bg"], (x1 - tokens.CUE - 4, y, tokens.CUE + 4, tokens.CHIP_H))
        triangle_right(surf, C["text2"], x1 - tokens.CUE // 2 - 2, y + 10, size=tokens.CUE)
    return off


def chip(surf, x, y, label, focused=False, arrow=None, maxw=None, rec=None, ox=0, oy=0):
    """32 high, text + 32 wide, raised, list text. arrow "up"/"down": the
    sort direction, an 8 px triangle after the text. maxw: a longer label ends in an
    ellipsis. Returns the width."""
    tw = width(label, "list")
    if maxw is not None:
        tw = min(tw, maxw)
    w = tw + 32 + (16 if arrow else 0)
    if focused:
        focus_fill(surf, (x, y, w, tokens.CHIP_H))
    else:
        rect(surf, C["raised"], (x, y, w, tokens.CHIP_H))
    col = C["ink"] if focused else C["text"]
    _texter(rec, ox, oy)(surf, label, "list", col, x + 16, y + 4, w=tw)
    if arrow:
        triangle(surf, col, x + 16 + tw + 8, y + 12, up=arrow == "up", size=8)
    return w


def art(surf, item, r, img, lines=4):
    """The image, or a track fill with the title in list text2, centred. A library tile (Home
    pins) is a raised fill with its name in heading."""
    r = pygame.Rect(r)
    if getattr(item, "type", "") == "library":
        surf.fill(C["raised"], r)
        paragraph(surf, item.title, "heading", C["text"], r.x + 8, r.y + r.h // 2 - 28,
                  r.w - 16, 2)
        return
    if img is not None:
        surf.blit(img, r.topleft)
        return
    surf.fill(C["track"], r)
    lns = wrap(getattr(item, "title", "") or "", "list", r.w - 16, lines)
    y = even(r.y + (r.h - 24 * len(lns)) // 2)
    for ln in lns:
        text(surf, ln, "list", C["text2"], r.x + 8, y, w=r.w - 16, align="center")
        y += 24


def art_marks(surf, item, r):
    """Unwatched movie or episode with no progress: a 20 px accent corner, top right. In
    progress: a 28 px bottom scrim and a 4 px bar inset 8. Watched: no mark."""
    r = pygame.Rect(r)
    frac = getattr(item, "progress", 0) or 0
    if frac > 0:
        sc = cached(("art_scrim", r.w), lambda: poster_bottom_scrim(r.w))
        surf.blit(sc, (r.x, r.bottom - 28))
        ins = tokens.PROGRESS_INSET
        progress(surf, r.x + ins, r.bottom - ins - tokens.PROGRESS_H, r.w - 2 * ins, frac)
    elif item.type in ("movie", "episode") and not item.watched:
        k = 20
        pygame.draw.polygon(surf, C["accent"], [(r.right - k, r.y), (r.right, r.y),
                                                (r.right, r.y + k)])


def row_marks(surf, item, state_x, y, title_x, focused=False):
    """On a 40 px row at y: unwatched (no progress) = an 8 x 8 accent square
    in the state column; in progress = a 4 px bar at y+32, 160 px wide, from the title x.
    Watched shows no mark (the title is dim). Focused: the marks in ink."""
    frac = getattr(item, "progress", 0) or 0
    if frac > 0:
        if focused:
            progress(surf, title_x, y + 32, 160, frac, track=C["dim"], fill=C["ink"])
        else:
            progress(surf, title_x, y + 32, 160, frac)
    elif not item.watched:
        rect(surf, C["ink"] if focused else C["accent"], (state_x, y + 16, 8, 8))


def live_tag(surf, x, y, w=None):
    """"LIVE": tag text2 with an 8 x 8 accent square before it. y: the top
    of a 20 px tag line. w: centre the tag in that width. Returns the width."""
    tw = width("LIVE", "tag") + 16
    if w is not None:
        x += (w - tw) // 2
    rect(surf, C["accent"], (x, y + 6, 8, 8))
    text(surf, "LIVE", "tag", C["text2"], x + 16, y)
    return tw


def channel_tag(surf, r, number):
    """A channel number on a poster (Home "On Right Now"): a 20 px raised tag, top left, tag
    text in text2 (the imdb badge box)."""
    r = pygame.Rect(r)
    tw = width(number, "tag") + 12
    rect(surf, C["raised"], (r.x, r.y, tw, 20))
    text(surf, number, "tag", C["text2"], r.x + 6, r.y)
    return tw


def error_line(surf, msg, x, y, w, style="small", rec=None, ox=0, oy=0):
    """An 8 x 8 error mark left of the line; the text in text."""
    _f, line, _u = fonts.style(style)
    rect(surf, C["error"], (x, y + (line - 8) // 2, 8, 8))
    _texter(rec, ox, oy)(surf, msg, style, C["text"], x + 16, y, w=w - 16)


def letter_box(surf, centre, letter):
    """The jump letter: an 80 x 80 raised box with the letter in display."""
    r = pygame.Rect(0, 0, 80, 80)
    r.center = centre
    r.y = even(r.y)
    rect(surf, C["raised"], r)
    text(surf, letter, "display", C["text"], r.x, r.y + 14, w=r.w, align="center")


def music_cell(surf, item, r, img, focused, line2=""):
    """Square art at the top of the cell, the title at
    art.bottom + 4 and line2 at art.bottom + 28 in small; the focus frame goes round the art.
    A cell with no room under the art (a peek row) shows the art only."""
    a = pygame.Rect(r.x, r.y, r.w, r.w)
    art(surf, item, a, img, lines=3)
    if r.h - r.w >= 24:
        text(surf, item.title, "small", C["text"] if focused else C["text2"], r.x,
             a.bottom + 4, w=r.w)
    if line2 and r.h - r.w >= 48:
        text(surf, line2, "small", C["dim"], r.x, a.bottom + 28, w=r.w)
    if focused:
        focus_frame(surf, a)


def safe_box(safe):
    """(x0, y0, x1, y1) of the safe area."""
    return (safe["left"], safe["top"], tokens.CANVAS_W - safe["right"],
            tokens.CANVAS_H - safe["bottom"])


def dialog_geo(safe, n_lines):
    """360 wide, centred in the safe area; padding 24, title lines of 28,
    buttons 24 below. (x, y, w, h), y on the 4 px grid."""
    x0, y0, x1, y1 = safe_box(safe)
    pad = tokens.DIALOG_PAD
    h = pad + 28 * n_lines + pad + tokens.CTRL_H + pad
    h = (h + 3) // 4 * 4
    w = tokens.DIALOG_W
    x = x0 + (x1 - x0 - w) // 2
    y = max(y0, (y0 + (y1 - y0 - h) // 2) // 4 * 4)
    return x, y, w, h


def dialog_box(surf, safe, lines, labels, focus, rec=None, ox=0, oy=0):
    """The dialog: title in heading (3 lines at most), then the buttons, secondary on raised,
    left-aligned with the title. Returns the button rects."""
    t = _texter(rec, ox, oy)
    x, y, w, h = dialog_geo(safe, len(lines))
    pad = tokens.DIALOG_PAD
    panel_fill(surf, (x, y, w, h))
    ty = y + pad
    for ln in lines:
        t(surf, ln, "heading", C["text"], x + pad, ty, w=w - 2 * pad)
        ty += 28
    by, bx, rects = ty + pad, x + pad, []
    for i, label in enumerate(labels):
        bw = button_w(label)
        rects.append((bx, by, bw, tokens.CTRL_H))
        button(surf, rects[-1], label, focused=i == focus, surface="raised", rec=rec, ox=ox,
               oy=oy)
        bx += bw + tokens.BTN_GAP
    return rects


def menu_h(n_rows):
    return 44 + n_rows * tokens.ROW_PITCH + tokens.PANEL_PAD


def menu_rect(safe, n_rows):
    """The menu: 320 wide, right edge at the safe right, bottom 8 above the safe bottom."""
    _x0, _y0, x1, y1 = safe_box(safe)
    h = menu_h(n_rows)
    return x1 - tokens.MENU_W, even(y1 - 8 - h), tokens.MENU_W, h


def menu_fit(safe, most=7):
    """Rows that fit between the safe edges (`most` at most); more rows scroll."""
    x0, y0, x1, y1 = safe_box(safe)
    return max(1, min(most, (y1 - y0 - 8 - 44 - tokens.PANEL_PAD) // tokens.ROW_PITCH))


def menu_panel(surf, r, title, labels, focus, top=0, fit=None, rec=None, ox=0, oy=0,
               marks=None):
    """Raised; a 44 header with the title in tag at x+24, y+16; rows of
    draw.row in the padded area (x+8, width w-16); 12 px cues in text2 for more rows.
    marks: {row index: mark callable} (the chosen value's check)."""
    x, y, w, h = r
    fit = fit or len(labels)
    t = _texter(rec, ox, oy)
    panel_fill(surf, r)
    pad = tokens.PANEL_PAD
    t(surf, title or "", "tag", C["text2"], x + pad + tokens.INSET, y + 16,
      w=w - 2 * (pad + tokens.INSET) - 20)
    ry = y + 44
    last = min(len(labels), top + fit)
    for i in range(top, last):
        # The down cue sits on the last row: keep its label clear of the cue.
        lab = labels[i]
        if i == last - 1 and last < len(labels):
            lab = ellipsize(fonts.style("label")[0], lab,
                            w - 2 * pad - 2 * tokens.INSET - tokens.CUE - 8)
        row(surf, (x + pad, ry, w - 2 * pad), lab, focused=i == focus, rec=rec, ox=ox,
            oy=oy, mark=(marks or {}).get(i))
        ry += tokens.ROW_PITCH
    cx = x + w - pad - tokens.INSET - tokens.CUE
    if top > 0:
        triangle(surf, C["text2"], cx, y + 16, up=True, size=tokens.CUE)
    if last < len(labels):
        ly = y + 44 + (last - 1 - top) * tokens.ROW_PITCH
        triangle(surf, C["ink"] if last - 1 == focus else C["text2"], cx, ly + 14, up=False,
                 size=tokens.CUE)


def toast_rect(msg, safe, clock_w=0):
    """Safe left, safe top; 40 high; text + 32 wide, at most the safe width
    minus the clock and 24."""
    x0, y0, x1, _y1 = safe_box(safe)
    room = x1 - x0 - (clock_w + tokens.TOAST_GAP if clock_w else 0)
    w = min(room, width(msg, "label") + 2 * tokens.INSET)
    return x0, even(y0), w, tokens.CTRL_H


def toast(surf, msg, r, rec=None, ox=0, oy=0):
    x, y, w, h = r
    panel_fill(surf, r)
    _texter(rec, ox, oy)(surf, msg, "label", C["text"], x + tokens.INSET, y + 8,
                         w=w - 2 * tokens.INSET)


_FEATHER = {"normal": (64, 191), "light": (32, 223), "off": (0, 255)}


def toast_layer(msg, r, mode):
    """The toast as a layer blitted after the frame filter. The toast is drawn
    with one more row above and below, filtered alone, and those edge rows get the alpha of
    the filter's response to a step (normal [1 2 1]: 1/4 and 3/4; the overlay mask is proven
    on the set), so the layer over a filtered frame looks as if it was filtered with it.
    Blit it at (x, y - 1)."""
    x, y, w, h = r
    surf = opaque_surface((w, h + 2))
    surf.fill(C["raised"])
    toast(surf, msg, (0, 1, w, h))
    flt = FlickerFilter((w, h + 2))
    src = flt.apply(surf, mode) if mode != "off" else surf
    out = pygame.Surface((w, h + 2), pygame.SRCALPHA, 32)
    out.blit(src, (0, 0))
    m = pygame.Surface((w, h + 2), pygame.SRCALPHA, 32)
    m.fill((255, 255, 255, 255))
    for i, a in enumerate(_FEATHER.get(mode, _FEATHER["normal"])):
        m.fill((255, 255, 255, a), (0, i, w, 1))
        m.fill((255, 255, 255, a), (0, h + 1 - i, w, 1))
    if _cut():                               # Console: the cut corner shows the frame below
        k = tokens.CUT + 1                   # the toast starts at row 1 of the layer
        pygame.draw.polygon(out, (0, 0, 0, 0), [(0, 0), (k, 0), (0, k)])
    out.blit(m, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return out


def state(surf, x, y, w, head, line=None, rec=None, ox=0, oy=0):
    """State text: head in heading text; line in body text2, 36 below."""
    t = _texter(rec, ox, oy)
    t(surf, head, "heading", C["text"], x, y, w=w)
    if line:
        t(surf, line, "body", C["text2"], x, y + 36, w=w)


def fmt_time(s):
    s = max(0, int(s))
    return "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60) if s >= 3600 else \
        "%d:%02d" % (s // 60, s % 60)


def seekbar(surf, x, y, w, pos_s, dur_s, focused=False, scrub_s=None, markers=(), chapters=(),
            times=True, rec=None, ox=0, oy=0):
    """With the playback data: positions in seconds. A 4 px track,
    accent fill, markers (start_s, end_s) in text2, chapter notches 2 px raised. Knob 8 x 12 in
    text2; focused 12 x 20 in focus. Times in label 12 below: position text, remaining text2.
    An unknown duration (0 or None) shows no fill, no knob and no remaining time."""
    pos = scrub_s if scrub_s is not None else (pos_s or 0)
    dur = dur_s or 0
    frac = max(0.0, min(1.0, pos / dur)) if dur else 0.0
    rect(surf, C["track"], (x, y, w, 4))
    if dur:
        for a, b in markers or ():
            ax, bx = x + int(w * a / dur), x + int(w * b / dur)
            rect(surf, C["text2"], (ax, y, max(2, bx - ax), 4))
    if frac > 0:
        rect(surf, C["accent"], (x, y, max(2, int(w * frac)), 4))
    if dur:
        for c in chapters or ():
            if 0 < c < dur:
                cx = x + int(w * c / dur)
                rect(surf, C["raised"], (cx - 1, y, 2, 4))
        kw, kh = (12, 20) if focused else (8, 12)
        mx = x + int(w * frac)
        rect(surf, C["focus"] if focused else C["text2"],
             (max(x, min(x + w - kw, mx - kw // 2)), y + 2 - kh // 2, kw, kh))
    if times:
        t = _texter(rec, ox, oy)
        # No position yet (a track that loads or buffers): "…", not 0:00.
        known = scrub_s is not None or pos_s is not None
        t(surf, fmt_time(pos) if known else "…", "label", C["text"], x, y + 12)
        if dur and known:
            t(surf, "-" + fmt_time(dur - pos), "label", C["text2"], x + w, y + 12, align="right")


# ------------------------------------------------------------ scrims

def gradient_alpha(w, h, color, fn):
    """Surface with per-pixel alpha from fn(x, y) -> 0..1. Built once and cached by caller."""
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    # columns or rows are enough for separable scrims
    for x in range(0, w, 2):
        for y in range(0, h, 2):
            a = max(0.0, min(1.0, fn(x, y)))
            s.fill((*color, int(a * 255)), (x, y, 2, 2))
    return s


def home_scrim(w=tokens.MODE_W, h=tokens.BACKDROP[1]):
    """Kept per size and bg colour (a pixel loop: 30+ ms). Callers only blit it."""
    return cached(("home_scrim", w, h, tuple(C["bg"])), lambda: _home_scrim(w, h))


def _home_scrim(w, h):
    def fn(x, y):
        if x <= 200:
            left = 0.90
        elif x >= 440:
            left = 0.0
        else:
            left = 0.90 * (440 - x) / 240
        if y <= 120:
            bot = 0.0
        elif y >= 216:
            bot = 1.0
        else:
            bot = (y - 120) / 96
        # a top shade so the top bar (label, clock, tabs) reads on a bright backdrop
        top = 0.65 * (1 - y / 96) if y < 96 else 0.0
        return 1 - (1 - left) * (1 - bot) * (1 - top)
    return gradient_alpha(w, h, C["bg"], fn)


def poster_bottom_scrim(w, h=28):
    return gradient_alpha(w, h, C["bg"], lambda x, y: 0.85 * y / max(1, h - 2))


# ------------------------------------------------------------ flicker filter

def opaque_surface(size, like=None):
    """A surface in the format of like (default: the display's) without its alpha channel. A
    32-bit display (a macOS window: ARGB8888) gives new surfaces an alpha mask; their alpha
    bytes go stale (an alpha blit writes them), and a copy() or subsurface() of such a surface
    blends by them: old pixels show through a blit. A frame surface carries no alpha."""
    s = pygame.Surface(size) if like is None else pygame.Surface(size, 0, like)
    m = s.get_masks()
    if m[3]:
        s = pygame.Surface(size, 0, s.get_bitsize(), m[:3] + (0,))
    return s


def _no_blend(s):
    """s as a blit source that copies: a subsurface or copy of a surface with an alpha mask
    blends by its alpha bytes otherwise."""
    if s.get_masks()[3]:
        s.set_alpha(None)
    return s


class FlickerFilter:
    """Vertical low-pass on the final UI frame. normal = [1 2 1]/4, light = [1 6 1]/8.
    Built from pygame.transform.average_surfaces (C code), so it is cheap enough per frame."""

    def __init__(self, size):
        self.size = size
        self.up = opaque_surface(size)
        self.dn = opaque_surface(size)
        self.out = None              # the kept result of full() and apply_rect()
        self._scratch = {}           # (format, n) -> kept full-size work surface

    def _keep(self, src, n):
        """Work surface n with src's size and pixel format (average_surfaces writes into it:
        the same arithmetic as a new surface, without the allocation). One full-size surface
        for each format and n; a band pass gets a subsurface of it, so band heights add no
        surfaces."""
        sw, sh = src.get_size()
        w, h = self.size
        alpha = src.get_flags() & pygame.SRCALPHA
        k = (src.get_bitsize(), src.get_masks(), alpha, n)
        if sw > w or sh > h:
            k = (src.get_size(),) + k            # larger than the filter: its own surface
            w, h = sw, sh
        s = self._scratch.get(k)
        if s is None:
            s = self._scratch[k] = (pygame.Surface((w, h), alpha, src) if alpha
                                    else opaque_surface((w, h), src))
        return s if (sw, sh) == (w, h) else s.subsurface((0, 0, sw, sh))

    def apply(self, src, mode):
        """A filtered copy of src (a new surface: callers may keep it)."""
        if mode == "off":
            return src
        return self._run(src, self.up, self.dn, mode)

    def _run(self, src, up, dn, mode, dest=None):
        w, h = src.get_size()
        up.blit(src, (0, -1))
        up.blit(src, (0, h - 1), (0, h - 1, w, 1))
        dn.blit(src, (0, 1))
        dn.blit(src, (0, 0), (0, 0, w, 1))
        avg = pygame.transform.average_surfaces
        # each result takes the format of its first input, as a new surface would
        if mode == "light":
            nb = avg([up, dn], self._keep(up, 1))
            pair = [src, avg([src, nb], self._keep(src, 2))]
        else:
            pair = [avg([src, up], self._keep(src, 1)), avg([src, dn], self._keep(src, 2))]
        return avg(pair) if dest is None else avg(pair, dest)

    def full(self, src, mode):
        """A full pass into the kept surface `out`. Returns it."""
        if mode == "off":
            if self.out is None or self.out.get_size() != src.get_size():
                self.out = opaque_surface(src.get_size(), src)
            self.out.blit(src, (0, 0))
            return self.out
        if self.out is None or self.out.get_size() != src.get_size() \
                or self.out.get_bitsize() != src.get_bitsize() \
                or self.out.get_masks()[:3] != src.get_masks()[:3]:
            self.out = opaque_surface(src.get_size(), src)
        self._run(src, self.up, self.dn, mode, self.out)
        return self.out

    def apply_rect(self, src, rect, mode):
        """A band pass. The filter is vertical, so a change in rows
        rect.top..rect.bottom changes the output in one more row above and below: those rows
        of rect's columns are filtered again (from src rows one further out) into `out`. The
        result equals a full pass when src changed only inside rect. Returns `out`."""
        if self.out is None:
            return self.full(src, mode)
        w, h = self.size
        r = pygame.Rect(rect).clip(pygame.Rect(0, 0, w, h))
        if not r.w or not r.h:
            return self.out
        y0, y1 = max(0, r.top - 1), min(h, r.bottom + 1)       # rows written
        if mode == "off":
            part = src.subsurface((r.x, y0, r.w, y1 - y0))
            self.out.blit(part if src.get_flags() & pygame.SRCALPHA else _no_blend(part),
                          (r.x, y0))
            return self.out
        s0, s1 = max(0, y0 - 1), min(h, y1 + 1)                # rows read
        strip = src.subsurface((r.x, s0, r.w, s1 - s0))
        if not src.get_flags() & pygame.SRCALPHA:
            _no_blend(strip)             # a blit from the strip copies, as one from src does
        size = (r.w, s1 - s0)
        res = self._run(strip, self.up.subsurface((0, 0) + size),
                        self.dn.subsurface((0, 0) + size), mode, self._keep(strip, 3))
        self.out.blit(_no_blend(res), (r.x, y0), (0, y0 - s0, r.w, y1 - y0))
        return self.out
