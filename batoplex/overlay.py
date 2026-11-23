"""Playback overlay: pygame draws the control bar, lists and toasts; mpv shows them with
overlay-add (measured on the box: 0 dropped frames).

Pipeline per component (pygame only, about 2 ms for a 641x168 bar):
  1. draw on an opaque surface with the normal draw.py helpers
  2. flicker filter (the same one as the UI) - it must run before alpha, because
     average_surfaces drops alpha
  3. alpha from a cached mask (230 inside, a 2-row feather on the top and bottom edges)
  4. premultiply, BGRA bytes, write to /dev/shm, overlay-add

Updates are event driven. After an overlay-add, the file of that component is not written
again until mpv replies; a change in between is kept and the latest one is sent then.
Backgrounds can reach the screen edge; text and controls stay inside settings["safe"].
"""
import os

import pygame

from . import draw, fonts, log, tokens
from .tokens import C

L = log.get("overlay")

BAR, PANEL, TOAST, SKIP = 0, 1, 2, 3
FRAME0 = 4                   # 4..7: focus frame edges around the small Up Next video
ALPHA = tokens.OVERLAY_ALPHA
FEATHER = (58, 172)          # first rows of an edge: the [1 2 1] response, so no edge flicker

BAR_H = 132                  # content height above the bottom safe margin
MINI_H = 48
BTN_W, BTN_H, BTN_GAP = tokens.ICON_BTN_W, tokens.CTRL_H, tokens.BTN_GAP


def _even(v):
    return int(v) & ~1


def shm_dir():
    return "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else "/tmp"


# ---------------------------------------------------------------- bytes

_masks = {}


def mask(w, h):
    key = (w, h)
    m = _masks.get(key)
    if m is None:
        m = pygame.Surface((w, h), pygame.SRCALPHA, 32)
        m.fill((255, 255, 255, ALPHA))
        for i, a in enumerate(FEATHER):
            if i < h:
                m.fill((255, 255, 255, a), (0, i, w, 1))
                m.fill((255, 255, 255, a), (0, h - 1 - i, w, 1))
        _masks[key] = m
    return m


def to_bgra(opaque, flicker=None, mode="off"):
    """Opaque surface -> premultiplied BGRA bytes for overlay-add."""
    w, h = opaque.get_size()
    src = flicker.apply(opaque, mode) if (flicker is not None and mode != "off") else opaque
    s = pygame.Surface((w, h), pygame.SRCALPHA, 32)
    s.blit(src, (0, 0))
    s.blit(mask(w, h), (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    s = s.premul_alpha()
    return pygame.image.tobytes(s, "BGRA")


# ---------------------------------------------------------------- renderer

class _Comp:
    def __init__(self, cid):
        self.cid = cid
        self.path = os.path.join(shm_dir(), "batoplex-%d-ov%d.bgra" % (os.getpid(), cid))
        self.shown = False
        self.sig = None
        self.pending = False      # overlay-add sent, no reply yet
        self.queued = None        # latest (x, y, w, h, draw_fn, sig) while pending
        self.flt = None


class OverlayRenderer:
    """Owns the overlay ids of one mpv. cmd(*args, cb=None) sends to mpv."""

    def __init__(self, cmd, settings):
        self.cmd = cmd
        self.settings = settings
        self.comps = {}
        self.text_mode = False            # overlay-add failed: toasts go through show-text
        self.osd = (641, 480)
        self.sent = 0

    def _comp(self, cid):
        c = self.comps.get(cid)
        if c is None:
            c = self.comps[cid] = _Comp(cid)
        return c

    def show(self, cid, x, y, w, h, draw_fn, sig=None):
        """Show component cid at (x, y), size (w, h). draw_fn(surface) draws it opaque.
        sig: any value; when it equals the shown one, nothing is sent."""
        c = self._comp(cid)
        if sig is not None:                 # a theme change draws the overlay again
            from . import theme
            sig = (theme.version, sig)
        if sig is not None and c.shown and sig == c.sig and not c.pending:
            return
        if c.pending:
            # The frame in flight already has this sig: drop any older queued frame too.
            c.queued = None if (sig is not None and sig == c.sig) else (x, y, w, h, draw_fn, sig)
            return
        self._send(c, x, y, w, h, draw_fn, sig)

    def _send(self, c, x, y, w, h, draw_fn, sig):
        x, y, w, h = int(x), _even(y), int(w), _even(h)
        surf = draw.opaque_surface((w, h))          # no stale alpha in the BGRA bytes
        surf.fill(C["bg"])
        draw_fn(surf)
        mode = self.settings["flicker_filter"]
        if c.flt is None or c.flt.size != (w, h):
            c.flt = draw.FlickerFilter((w, h))
        data = to_bgra(surf, c.flt, mode)
        try:
            fd = os.open(c.path, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                os.ftruncate(fd, len(data))
                os.pwrite(fd, data, 0)
            finally:
                os.close(fd)
        except OSError as e:
            L.info("overlay write failed: %s", e)
            return
        args = ["overlay-add", c.cid, x, y, c.path, 0, "bgra", w, h, w * 4]
        ow, oh = self.osd
        if (ow, oh) != (641, 480) and ow and oh:     # dev window: scale to the OSD size
            sx, sy = ow / 641.0, oh / 480.0
            args = ["overlay-add", c.cid, int(x * sx), int(y * sy), c.path, 0, "bgra", w, h,
                    w * 4, int(w * sx), int(h * sy)]
        c.pending, c.shown, c.sig = True, True, sig
        self.sent += 1

        def ack(err, _data, c=c):
            c.pending = False
            if err not in (None, "success"):
                L.info("overlay-add failed (%s); using text mode", err)
                self.text_mode = True
                return
            if c.queued and c.shown:
                q, c.queued = c.queued, None
                if q[5] is None or q[5] != c.sig:
                    self._send(c, *q)
        if self.cmd(*args, cb=ack) is None:
            c.pending = False

    def hide(self, cid):
        c = self.comps.get(cid)
        if c is None or not c.shown:
            return
        c.shown, c.sig, c.queued = False, None, None
        self.cmd("overlay-remove", cid)

    def cleanup(self):
        for c in self.comps.values():
            try:
                os.unlink(c.path)
            except OSError:
                pass


# ---------------------------------------------------------------- drawing
# Every component draws with the draw.py helpers: panels raised (the
# mask makes them 230 alpha), text in text and text2 (never dim: over a white picture dim
# fails), buttons and focus as in the UI. Overlays are cuts: no animation (each frame costs
# an IPC call).

class Recorder:
    """Records the line box of each text a draw function makes, and the box of the component
    it lies in (tests check both: text inside its component and inside the safe area)."""

    def __init__(self):
        self.rects = []
        self.boxes = []
        self.colors = []
        self.texts = []

    def text(self, surf, s, style, color, x, y, w=None, align="left", ox=0, oy=0):
        used = draw.text(surf, s, style, color, x, y, w=w, align=align)
        tx = x - used if align == "right" else (x + ((w or 0) - used) // 2 if align == "center" else x)
        line = fonts.style(style)[1]
        self.rects.append(pygame.Rect(tx + ox, draw.even(y) + oy, used, line))
        sw, sh = surf.get_size()
        self.boxes.append(pygame.Rect(ox, oy, sw, sh))
        self.colors.append(tuple(color))
        self.texts.append(s)
        return used


fmt_time = draw.fmt_time


def icon(surf, name, x, y, col):
    """24 px playback icons on whole pixels; every stroke 2 or 4 px, horizontal edges on even
    rows."""
    if name == "play":
        pygame.draw.polygon(surf, col, [(x + 6, y + 2), (x + 6, y + 22), (x + 21, y + 12)])
    elif name == "pause":
        surf.fill(col, (x + 5, y + 2, 5, 20))
        surf.fill(col, (x + 14, y + 2, 5, 20))
    elif name == "prev":
        surf.fill(col, (x + 3, y + 2, 4, 20))
        pygame.draw.polygon(surf, col, [(x + 21, y + 2), (x + 21, y + 22), (x + 8, y + 12)])
    elif name == "next":
        surf.fill(col, (x + 17, y + 2, 4, 20))
        pygame.draw.polygon(surf, col, [(x + 3, y + 2), (x + 3, y + 22), (x + 16, y + 12)])
    elif name == "subs":     # speech box with two text lines
        draw.frame(surf, col, (x + 3, y + 4, 18, 14), 2)
        surf.fill(col, (x + 6, y + 8, 8, 2))
        surf.fill(col, (x + 6, y + 12, 12, 2))
        pygame.draw.polygon(surf, col, [(x + 6, y + 20), (x + 12, y + 20), (x + 6, y + 24)])
    elif name == "stop":
        surf.fill(col, (x + 4, y + 4, 16, 16))
    elif name == "restart":  # play from start: a bar and a play triangle
        surf.fill(col, (x + 3, y + 2, 4, 20))
        pygame.draw.polygon(surf, col, [(x + 9, y + 2), (x + 9, y + 22), (x + 22, y + 12)])
    elif name == "check":    # mark as played
        pygame.draw.lines(surf, col, False, [(x + 3, y + 12), (x + 9, y + 18), (x + 21, y + 6)], 4)
    elif name == "more":     # three dots
        for k in (3, 10, 17):
            surf.fill(col, (x + k, y + 10, 4, 4))
    elif name == "uncheck":  # mark as unplayed: an open ring
        pygame.draw.circle(surf, col, (x + 12, y + 12), 10, 4)
    elif name == "shuffle":  # two crossed arrows
        pygame.draw.line(surf, col, (x + 2, y + 6), (x + 17, y + 18), 4)
        pygame.draw.line(surf, col, (x + 2, y + 18), (x + 17, y + 6), 4)
        pygame.draw.polygon(surf, col, [(x + 16, y + 2), (x + 22, y + 6), (x + 16, y + 10)])
        pygame.draw.polygon(surf, col, [(x + 16, y + 14), (x + 22, y + 18), (x + 16, y + 22)])
    elif name in ("repeat", "repeat_one"):   # two arrows in a loop
        surf.fill(col, (x + 3, y + 6, 14, 4))          # top line, arrow to the right
        pygame.draw.polygon(surf, col, [(x + 16, y + 2), (x + 22, y + 8), (x + 16, y + 14)])
        surf.fill(col, (x + 3, y + 6, 4, 8))
        surf.fill(col, (x + 7, y + 14, 14, 4))         # bottom line, arrow to the left
        pygame.draw.polygon(surf, col, [(x + 8, y + 10), (x + 2, y + 16), (x + 8, y + 22)])
        surf.fill(col, (x + 17, y + 10, 4, 8))
        if name == "repeat_one":
            surf.fill(col, (x + 10, y + 10, 4, 4))
    elif name == "queue":    # three lines, the last two shorter, and a play mark
        for k, lw in ((4, 16), (12, 10), (20, 10)):
            surf.fill(col, (x + 2, y + k, lw, 4))
        pygame.draw.polygon(surf, col, [(x + 15, y + 12), (x + 15, y + 22), (x + 22, y + 17)])
    elif name in ("bookmark", "bookmark_on"):   # Watchlist: outline = not on it, filled = on it
        pts = [(x + 5, y + 2), (x + 19, y + 2), (x + 19, y + 22), (x + 12, y + 16), (x + 5, y + 22)]
        if name == "bookmark_on":
            pygame.draw.polygon(surf, col, pts)
        else:
            pygame.draw.lines(surf, col, True, pts, 2)
            surf.fill(col, (x + 5, y + 2, 14, 2))
    elif name == "visualizer":   # four level bars of different heights
        for k, hgt in ((1, 10), (7, 20), (13, 14), (19, 6)):
            surf.fill(col, (x + k, y + 22 - hgt, 4, hgt))


def _icon(name):
    return lambda surf, x, y, col, n=name: icon(surf, n, x, y, col)


def bar_top(safe):
    return _even(480 - safe["bottom"] - BAR_H)


def draw_bar(surf, m, safe, rec=None, oy=0):
    """Full control bar. m: dict with title, buttons [(icon, label)], focus_row
    ("seek"|"buttons"), focus_btn, pos_s, dur_s, scrub_s, markers [(start_s, end_s)], chapters.
    A raised band; the buttons are bare icons at rest (stock Plex) with the control focus
    fill."""
    rec = rec or Recorder()
    w0, h0 = surf.get_size()
    draw.panel_fill(surf, (0, 0, w0, h0))
    left, right = safe["left"], 640 - safe["right"]
    w = right - left
    btn_lbl = ""
    if m.get("focus_row") == "buttons" and m.get("buttons"):
        btn_lbl = m["buttons"][m.get("focus_btn", 0)][1]
    lw = draw.width(btn_lbl, "label") if btn_lbl else 0
    rec.text(surf, m.get("title", ""), "heading", C["text"], left, 12, w=w - lw - 24, oy=oy)
    if btn_lbl:
        rec.text(surf, btn_lbl, "label", C["text2"], right, 14, align="right", oy=oy)
    bx = left
    for i, (ic, _lbl) in enumerate(m.get("buttons") or []):
        foc = m.get("focus_row") == "buttons" and i == m.get("focus_btn", 0)
        draw.button(surf, (bx, 44, BTN_W, BTN_H), icon=_icon(ic), focused=foc, rest=C["raised"])
        bx += BTN_W + BTN_GAP
    _seek(surf, m, left, w, 92, rec, oy, focus=m.get("focus_row", "seek") == "seek")
    return rec


def _seek(surf, m, left, w, y, rec, oy, focus=True):
    draw.seekbar(surf, left, y, w, m.get("pos_s", 0), m.get("dur_s"), focused=focus,
                 scrub_s=m.get("scrub_s"), markers=m.get("markers") or (),
                 chapters=m.get("chapters") or (), rec=rec, oy=oy)


ROW_H = tokens.ROW_PITCH


def panel_rect(n_rows, safe):
    """Track panel: the menu's place and size, the tabs in its header."""
    return draw.menu_rect(safe, max(1, n_rows))


def draw_panel(surf, m, rec=None, ox=0, oy=0):
    """m: tab ("sub"|"audio"), rows [(label, active)], focus (visible index), more_up/down.
    The tabs sit in the 44 px header (draw.tabs; the focus is in the rows, so the selected tab
    is underlined); the chosen track has a check."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    draw.panel_fill(surf, (0, 0, w, h))
    pad = tokens.PANEL_PAD
    sel = 0 if m["tab"] == "sub" else 1
    cx = w - pad - tokens.INSET - tokens.CUE
    draw.tabs(surf, ["Subtitles", "Audio"], sel, False, pad + tokens.INSET - 12, cx - 8, 8,
              rec=rec, ox=ox, oy=oy, bg=C["raised"])
    y = 44
    n = len(m["rows"])
    for i, (label, active) in enumerate(m["rows"]):
        if m.get("more_down") and i == n - 1:      # keep the label clear of the down cue
            label = draw.ellipsize(fonts.style("label")[0], label,
                                   w - 2 * pad - 2 * tokens.INSET - 32 - tokens.CUE - 8)
        draw.row(surf, (pad, y, w - 2 * pad), label, focused=i == m["focus"],
                 mark=_icon("check") if active else None, rec=rec, ox=ox, oy=oy)
        y += ROW_H
    if m.get("more_up"):
        draw.triangle(surf, C["text2"], cx, 16, up=True, size=tokens.CUE)
    if m.get("more_down") and n:
        # On the last row, left of the check column; the row's label is cut short above.
        ly = 44 + (n - 1) * ROW_H
        draw.triangle(surf, C["ink"] if m["focus"] == n - 1 else C["text2"], cx - 32, ly + 14,
                      up=False, size=tokens.CUE)
    return rec


def menu_fit(safe):
    """Playback menu rows that fit between the safe edges (7 at most); more rows scroll."""
    return draw.menu_fit(safe)


def menu_rect(n_rows, safe):
    """Playback menu: the UI menu, in the same place as the track panel."""
    return draw.menu_rect(safe, max(1, min(n_rows, menu_fit(safe))))


def draw_menu(surf, rows, focus, rec=None, ox=0, oy=0, top=0, fit=None, title="Playback",
              checked=None):
    """checked: the index of the chosen value (a check on its row), or None."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    marks = {checked: _icon("check")} if checked is not None else None
    draw.menu_panel(surf, (0, 0, w, h), title, rows, focus, top=top, fit=fit, rec=rec,
                    ox=ox, oy=oy, marks=marks)
    return rec


SKIP_H, SKIP_SHOW_S = tokens.CTRL_H, 8.0


def skip_rect(label, safe, bar_open):
    """Skip Intro / Skip Credits: bottom right, above the bar or the mini bar."""
    w = draw.width(label, "label") + 2 * tokens.BTN_PAD
    above = bar_top(safe) if bar_open else mini_top(safe)
    return 640 - safe["right"] - w, _even(above - 8 - SKIP_H), w, SKIP_H


def draw_skip(surf, label, rec=None, ox=0, oy=0):
    """The skip button has focus when it shows (stock Plex), so it is drawn focused."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    draw.button(surf, (0, 0, w, h), label, focused=True, rec=rec, ox=ox, oy=oy)
    return rec


def mini_top(safe):
    return _even(480 - safe["bottom"] - MINI_H)


def draw_mini(surf, m, safe, rec=None, oy=0):
    """Seek line and times only, shown for a moment after a seek."""
    rec = rec or Recorder()
    w0, h0 = surf.get_size()
    draw.panel_fill(surf, (0, 0, w0, h0))
    left, right = safe["left"], 640 - safe["right"]
    _seek(surf, m, left, right - left, 12, rec, oy)   # the 20 px knob clears the feather
    return rec


def toast_rect(msg, safe):
    """The UI toast: safe left, safe top. No clock shows in playback."""
    return draw.toast_rect(msg, safe)


def draw_toast(surf, msg, rec=None, ox=0, oy=0):
    rec = rec or Recorder()
    w, h = surf.get_size()
    draw.toast(surf, msg, (0, 0, w, h), rec=rec, ox=ox, oy=oy)
    return rec


CARD_W, CARD_H = 400, 96


def card_rect(safe):
    """Start card: centred in the safe area."""
    left, right = safe["left"], 640 - safe["right"]
    w = min(CARD_W, right - left)
    x = left + (right - left - w) // 2
    y = _even(safe["top"] + (480 - safe["top"] - safe["bottom"] - CARD_H) // 2)
    return x, y, w, CARD_H


def draw_card(surf, title, rec=None, ox=0, oy=0):
    """Title, "Starting…" and the one thing B does now (a hint)."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    pad = tokens.INSET
    draw.panel_fill(surf, (0, 0, w, h))
    rec.text(surf, title, "heading", C["text"], pad, 16, w=w - 2 * pad, ox=ox, oy=oy)
    hw = 24 + 8 + draw.width("Cancel", "small")
    rec.text(surf, "Starting…", "body", C["text2"], pad, 56, w=w - 2 * pad - hw - 16, ox=ox,
             oy=oy)
    draw.hints(surf, [("B", "Cancel")], w - pad - hw, 56, hw, rec=rec, ox=ox, oy=oy)
    return rec


# ---------------------------------------------------------------- Live TV banner

BANNER = 8                   # the overlay id of the channel info banner (after FRAME0..3)
BANNER_H = 136
GUIDE = 9                    # the overlay id of the guide over the picture


def banner_rect(safe):
    """The safe width, at the safe bottom."""
    left, right = safe["left"], 640 - safe["right"]
    return left, _even(480 - safe["bottom"] - BANNER_H), right - left, BANNER_H


def draw_banner(surf, m, rec=None, ox=0, oy=0):
    """The channel info banner. m: number, name, title, line, frac, left, next, clock."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    pad = tokens.INSET
    draw.panel_fill(surf, (0, 0, w, h))
    cw = rec.text(surf, m.get("clock", ""), "small", C["text2"], w - pad, 8, align="right",
                  ox=ox, oy=oy)
    nw = rec.text(surf, m["number"], "label", C["text2"], pad, 8, ox=ox, oy=oy)
    rec.text(surf, m["name"], "label", C["text"], pad + nw + 12, 8,
             w=w - 2 * pad - nw - 12 - cw - 16, ox=ox, oy=oy)
    rec.text(surf, m["title"], "heading", C["text"], pad, 36, w=w - 2 * pad, ox=ox, oy=oy)
    if m.get("line"):
        rec.text(surf, m["line"], "small", C["text2"], pad, 64, w=w - 2 * pad, ox=ox, oy=oy)
    lw = rec.text(surf, m["left"], "small", C["text2"], w - pad, 88, align="right", ox=ox, oy=oy)
    draw.progress(surf, pad, 98, w - 2 * pad - lw - 16, m.get("frac", 0))
    if m.get("next"):
        rec.text(surf, m["next"], "small", C["text2"], pad, 108, w=w - 2 * pad - lw - 16, ox=ox,
                 oy=oy)
    return rec


# ---------------------------------------------------------------- Up Next

UPNEXT_GAP = 16
UPNEXT_PAD = tokens.INSET
THUMB_RATIO = 9 / 16.0
FRAME_T = 4                  # focus frame around the small video (even, so no odd edge rows)
STILL_Q = "Are you still watching?"


def upnext_video_box(safe):
    """Top-left 4:3 box for the small video during Up Next (x, y, w, h)."""
    left, right = safe["left"], 640 - safe["right"]
    vw = _even(min(320, (right - left - UPNEXT_GAP) * 0.58))
    return left, _even(safe["top"]), vw, _even(vw * 3 // 4)


def upnext_card_rect(safe, still=False):
    """Right of the small video, its top at the safe top; the height from the content."""
    x, y, vw, _vh = upnext_video_box(safe)
    cx = x + vw + UPNEXT_GAP
    w = 640 - safe["right"] - cx
    iw = w - 2 * UPNEXT_PAD
    if still:
        h = 16 + 28 * len(draw.wrap(STILL_Q, "heading", iw, 2)) + 8 + 48 + 16 + \
            2 * tokens.CTRL_H + tokens.BTN_GAP
    else:
        h = 40 + _even(iw * THUMB_RATIO) + 12 + 92 + 24
    return cx, y, w, _even(h + UPNEXT_PAD)


def upnext_thumb_size(safe):
    """The still in the Up Next card (w, h)."""
    _x, _y, w, _h = upnext_card_rect(safe)
    iw = w - 2 * UPNEXT_PAD
    return iw, _even(iw * THUMB_RATIO)


def upnext_frame_rects(safe):
    """Four thin overlays that outline the small video when it has focus."""
    x, y, w, h = upnext_video_box(safe)
    t = FRAME_T
    return [(x - t, y - t, w + 2 * t, t), (x - t, y + h, w + 2 * t, t),
            (x - t, y, t, h), (x + w, y, t, h)]


def draw_frame_edge(surf):
    surf.fill(C["focus"])


def draw_upnext(surf, m, rec=None, ox=0, oy=0):
    """Up Next card. m: show, line (S1 E5), title, count (seconds or None), still (bool),
    focus ("next" | "video" | "continue" | "stop"), img (surface or None)."""
    rec = rec or Recorder()
    w, h = surf.get_size()
    draw.panel_fill(surf, (0, 0, w, h))
    pad = UPNEXT_PAD
    iw = w - 2 * pad
    if m.get("still"):
        lines = draw.wrap(STILL_Q, "heading", iw, 2)
        for i, ln in enumerate(lines):
            rec.text(surf, ln, "heading", C["text"], pad, 16 + 28 * i, w=iw, ox=ox, oy=oy)
        y = 16 + 28 * len(lines) + 8
        rec.text(surf, m.get("show") or "", "label", C["text2"], pad, y, w=iw, ox=ox, oy=oy)
        if m.get("line"):
            rec.text(surf, m["line"], "label", C["text2"], pad, y + 24, w=iw, ox=ox, oy=oy)
        by = y + 48 + 16
        for key, label in (("continue", "Continue"), ("stop", "Stop")):
            draw.button(surf, (pad, by, iw, tokens.CTRL_H), label,
                        focused=m.get("focus") == key, surface="raised", rec=rec, ox=ox, oy=oy)
            by += tokens.CTRL_H + tokens.BTN_GAP
        return rec
    rec.text(surf, "UP NEXT", "tag", C["text2"], pad, 12, ox=ox, oy=oy)
    th = _even(iw * THUMB_RATIO)
    art = pygame.Rect(pad, 40, iw, th)
    img = m.get("img")
    if img is not None:
        sub = img if img.get_size() == (iw, th) else pygame.transform.smoothscale(img, (iw, th))
        surf.blit(sub, art.topleft)
    else:
        draw.rect(surf, C["track"], art)
    if m.get("focus") == "next":
        draw.focus_frame(surf, art)
    y = art.bottom + 12
    rec.text(surf, m.get("show") or "", "heading", C["text"], pad, y, w=iw, ox=ox, oy=oy)
    if m.get("line"):
        rec.text(surf, m["line"], "label", C["text2"], pad, y + 32, w=iw, ox=ox, oy=oy)
    if m.get("title"):
        rec.text(surf, m["title"], "label", C["text"], pad, y + 60, w=iw, ox=ox, oy=oy)
    if m.get("count") is not None:
        rec.text(surf, "Starts in %d" % m["count"], "label", C["text"], pad, y + 92, w=iw,
                 ox=ox, oy=oy)
    return rec
