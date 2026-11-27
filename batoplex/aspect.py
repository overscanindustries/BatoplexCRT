"""Aspect: how a picture wider than the 4:3 tube fills it. No pygame.

Letterbox shows all of the picture. 14:9 is the broadcast middle way for 16:9 on 4:3: it
cuts a little from each side and leaves thin bars. Zoom fills the screen and cuts the sides.
14:9 and Zoom first remove black bars that are part of the file (a 16:9 film in a 4:3 file):
mpv's cropdetect filter runs for a short time over IPC, then video-crop cuts the bars.
"""
MODES = [("letterbox", "Letterbox"), ("14:9", "14:9"), ("zoom", "Zoom")]
LABEL = dict(MODES)
SCREEN = 4 / 3

DETECT_FILTER = "@bpxcrop:lavfi=[cropdetect=limit=24/255:round=2:reset=0]"
DETECT_LABEL = "bpxcrop"
DETECT_WAIT_S = 2.0         # after the picture moves: no black frame of the start
DETECT_RUN_S = 3.0          # reset=0: the box grows to the union of all frames in this time
DETECT_RETRY_S = 20.0       # a dark scene gave no box: try again later
DETECT_TRIES = 3


def valid(mode):
    return mode if mode in LABEL else "letterbox"


def next_mode(mode, d=1):
    keys = [k for k, _ in MODES]
    return keys[(keys.index(valid(mode)) + d) % len(keys)]


def panscan(mode, aspect):
    """mpv panscan (0 letterbox, 1 fill the height) for a picture of display aspect `aspect`
    on the 4:3 screen. mpv grows the picture linearly between the two sizes."""
    mode = valid(mode)
    if mode == "letterbox" or not aspect or aspect <= SCREEN + 0.01:
        return 0.0
    if mode == "zoom":
        return 1.0
    h0 = SCREEN / aspect                     # letterbox height, as a part of the screen height
    target = SCREEN / (14 / 9)               # the height that shows a 14:9 part of the picture
    if target <= h0:
        return 0.0                           # narrower than 14:9 already
    return round(min(1.0, (target - h0) / (1.0 - h0)), 4)


def crop_from_meta(meta, w, h):
    """(cw, ch, cx, cy) from cropdetect metadata for a w x h frame, or None when there are
    no clear bars. Only bars of the same size on both sides count (a dark corner is not a
    bar), and only when they take 3% or more of the frame; never more than half the frame."""
    if not meta or not w or not h:
        return None
    try:
        x1 = int(meta["lavfi.cropdetect.x1"])
        x2 = int(meta["lavfi.cropdetect.x2"])
        y1 = int(meta["lavfi.cropdetect.y1"])
        y2 = int(meta["lavfi.cropdetect.y2"])
    except (KeyError, TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None                          # all black so far
    top, bottom, left, right = y1, h - 1 - y2, x1, w - 1 - x2
    tol_y, tol_x = max(4, h // 50), max(4, w // 50)
    cut_y = min(top, bottom) if abs(top - bottom) <= tol_y else 0
    cut_x = min(left, right) if abs(left - right) <= tol_x else 0
    cut_y = cut_y if 2 * cut_y >= 0.03 * h else 0
    cut_x = cut_x if 2 * cut_x >= 0.03 * w else 0
    # One direction only: a dark scene can look like bars at the sides of a wide film.
    # Keep the larger bars (as a part of the frame); the other side stays whole.
    if cut_y and cut_x:
        if cut_y / float(h) >= cut_x / float(w):
            cut_x = 0
        else:
            cut_y = 0
    cut_y, cut_x = cut_y - cut_y % 2, cut_x - cut_x % 2
    if not cut_y and not cut_x:
        return None
    cw, ch = w - 2 * cut_x, h - 2 * cut_y
    if cw * ch < 0.5 * w * h:
        return None
    return cw, ch, cut_x, cut_y


def to_frac(c, w, h):
    """(x, y) bar sizes as parts of a w x h frame, for a crop (cw, ch, x, y)."""
    return (c[2] / float(w), c[3] / float(h)) if c and w and h else None


def from_frac(f, w, h):
    """The crop for a w x h frame from to_frac bars (a new stream size of the same film),
    or None when there are no bars."""
    if not f or not w or not h:
        return None
    x, y = int(round(f[0] * w)), int(round(f[1] * h))
    x, y = x - x % 2, y - y % 2
    if not x and not y:
        return None
    return w - 2 * x, h - 2 * y, x, y


def blank(meta):
    """True when cropdetect saw only black so far (a dark scene): try again later."""
    try:
        return int(meta["lavfi.cropdetect.x2"]) <= int(meta["lavfi.cropdetect.x1"]) or \
            int(meta["lavfi.cropdetect.y2"]) <= int(meta["lavfi.cropdetect.y1"])
    except (KeyError, TypeError, ValueError):
        return False


def crop_arg(c):
    return "%dx%d+%d+%d" % c if c else ""
