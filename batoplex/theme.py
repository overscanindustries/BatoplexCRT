"""Themes: palette roles, the High Contrast transform, the contrast
checks, and apply(), which changes tokens.C and the heading rows of tokens.TYPE in place.

Themes: Stock, Ribbon (a palette from the month), Midnight, Hearth, Console. High Contrast is
a separate switch over any theme. This module does not import tokens at the top, because
tokens imports it to fill C.
"""
import colorsys
from dataclasses import dataclass, field

# Floors. Contrast is WCAG; "lower of on-bg and on-raised".
FLOORS = {"text": 7.0, "text2": 7.0, "dim": 4.5, "accent": 3.0, "ink_accent": 4.5,
          "ink_focus": 7.0, "focus_accent": 1.5, "error": 3.0}
FLOORS_HC = {"text": 12.0, "text2": 12.0, "dim": 9.0, "accent": 3.0}
MAX_SAT = 0.80
CH_MIN, CH_MAX = 16, 235

ROLES = ("bg", "raised", "track", "text", "text2", "dim", "accent", "focus", "ink", "ok",
         "error")


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    palette: object           # role -> "#RRGGBB"; Ribbon: a function of the month
    focus: str = "fill"       # "fill" | "cut"
    heading_font: str = "bold"
    heading_upper: bool = False
    ambient: str = None
    sounds: str = None
    extra: dict = field(default_factory=dict)


# Stock accent: "about #E0A42C". #E0A42C has saturation 80.4 %, over the
# 80 % ceiling; #E0A42E is 79.5 % with the same look. The set test (pattern
# "Theme Colors") picks it or the fallback #E0B040; the focus fill is #EBEBE6 or #D8D8D2.
STOCK_ACCENT, STOCK_ACCENT_FALLBACK, PLEX_ACCENT = "#E0A42E", "#E0B040", "#E5A00D"
STOCK_FOCUS, STOCK_FOCUS_FALLBACK = "#EBEBE6", "#D8D8D2"

STOCK = Theme("stock", "Stock", {
    "bg": "#141414", "raised": "#242424", "track": "#444444",
    "text": "#EBEBE6", "text2": "#B8B8B4", "dim": "#8E8E8A",
    "accent": STOCK_ACCENT, "focus": STOCK_FOCUS, "ink": "#1C1C1C",
    "ok": "#58B878", "error": "#D8604C",
})

# Midnight and Console bg/ink lifted so every channel is 16
# or more; Console raised #1A221A.
MIDNIGHT = Theme("midnight", "Midnight", {
    "bg": "#101830", "raised": "#162848", "track": "#2E4670",
    "text": "#E4EAEB", "text2": "#B0BECE", "dim": "#8A9CB8",
    "accent": "#38C0AC", "focus": "#CFE9E6", "ink": "#101830",
    "ok": "#58B878", "error": "#E0705C",
})

HEARTH = Theme("hearth", "Hearth", {
    "bg": "#1A1410", "raised": "#2C2118", "track": "#4E3C2C",
    "text": "#EBE4D2", "text2": "#C4B8A0", "dim": "#9C8E76",
    "accent": "#D8743C", "focus": "#E6C36A", "ink": "#1A1410",
    "ok": "#7CB868", "error": "#E0705C",
}, sounds="hearth")

CONSOLE = Theme("console", "Console", {
    "bg": "#101410", "raised": "#1A221A", "track": "#364836",
    "text": "#E1EBD7", "text2": "#B0BEA6", "dim": "#86987E",
    "accent": "#8CC832", "focus": "#DCEBB8", "ink": "#101410",
    "ok": "#58B878", "error": "#E0705C",
}, focus="cut", heading_font="cond_bold", heading_upper=True)

# Ribbon: one hue for each month, January first.
RIBBON_HUES = (215, 255, 170, 140, 110, 190, 20, 345, 35, 285, 235, 0)


def _hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return hexs(round(c * 255) for c in (r, g, b))


def ribbon_palette(month):
    """The Ribbon palette for a month (1-12). Derived again: bg saturation
    comes down until every channel is 16 or more, and text2 and dim go up until they pass
    their floors on bg and on raised; accent as written (ink on accent 5.0 or more)."""
    h = RIBBON_HUES[(int(month) - 1) % 12]
    s = 0.55
    while min(rgb(_hsv(h, s, 0.105))) < CH_MIN:
        s -= 0.01
    p = {"bg": _hsv(h, s, 0.105), "raised": _hsv(h, 0.48, 0.19), "track": _hsv(h, 0.38, 0.34),
         "text": "#EBEBE6", "focus": _hsv(h, 0.06, 0.92), "ok": "#58B878", "error": "#E0705C"}
    p["ink"] = p["bg"]

    def low(c):
        return min(contrast(c, p["bg"]), contrast(c, p["raised"]))
    for role, sat, v, floor in (("text2", 0.08, 0.76, FLOORS["text2"]),
                                ("dim", 0.14, 0.62, FLOORS["dim"])):
        while low(_hsv(h, sat, v)) < floor + 0.05 and v < 0.92:
            v += 0.01
        p[role] = _hsv(h, sat, v)
    s, v = 0.55, 0.80
    while contrast(p["ink"], _hsv(h, s, v)) < 5.0 and s > 0.35:
        if v < 0.90:
            v += 0.02
        else:
            s -= 0.02
    p["accent"] = _hsv(h, s, v)
    return p


RIBBON = Theme("ribbon", "Ribbon", ribbon_palette, ambient="ribbon")

# The order of the Theme screen.
THEMES = {t.key: t for t in (STOCK, RIBBON, MIDNIGHT, HEARTH, CONSOLE)}

cur = STOCK
high_contrast = False
month = None
version = 0


# ------------------------------------------------------------ colour maths

def rgb(h):
    if isinstance(h, tuple):
        return h
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexs(c):
    return "#%02X%02X%02X" % tuple(c)


def luminance(c):
    def lin(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def saturation(c):
    r, g, b = (v / 255.0 for v in rgb(c))
    return colorsys.rgb_to_hsv(r, g, b)[1]


def mix(a, b, t):
    """t of the way from a to b, rounded."""
    a, b = rgb(a), rgb(b)
    return hexs(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------ resolve and check

def resolve(theme=None, hc=False, month=None):
    """Role -> "#RRGGBB" for a theme, with High Contrast applied. month:
    1-12 for Ribbon (default: this month)."""
    pal = (theme or STOCK).palette
    if callable(pal):
        pal = pal(month or _today().month)
    p = dict(pal)
    if hc:
        p["text2"] = p["text"]
        p["dim"] = mix(p["dim"], p["text"], 0.60)
        p["raised"] = p["bg"]
        p["track"] = mix(p["track"], p["text"], 0.25)
    return p


def checks(p):
    """The measured values of a resolved palette: {check: value}."""
    def low(role):
        return min(contrast(p[role], p["bg"]), contrast(p[role], p["raised"]))
    out = {k: low(k) for k in ("text", "text2", "dim", "accent", "error")}
    out["ink_accent"] = contrast(p["ink"], p["accent"])
    out["ink_focus"] = contrast(p["ink"], p["focus"])
    out["focus_accent"] = contrast(p["focus"], p["accent"])
    out["sat"] = max(saturation(v) for v in p.values())
    out["ch_min"] = min(min(rgb(v)) for v in p.values())
    out["ch_max"] = max(max(rgb(v)) for v in p.values())
    return out


def failures(p, hc=False):
    """The checks a resolved palette fails: [] when it passes every floor."""
    m = checks(p)
    floors = dict(FLOORS, **(FLOORS_HC if hc else {}))
    bad = ["%s %.2f < %.1f" % (k, m[k], f) for k, f in floors.items() if m[k] < f]
    if m["sat"] > MAX_SAT:
        bad.append("saturation %.3f" % m["sat"])
    if m["ch_min"] < CH_MIN or m["ch_max"] > CH_MAX:
        bad.append("channels %d-%d" % (m["ch_min"], m["ch_max"]))
    return bad


# ------------------------------------------------------------ apply

def _setting(settings, key, default):
    if settings is None:
        return default
    try:
        v = settings[key]
    except (KeyError, TypeError):
        return default
    return default if v is None else v


def _today():
    import datetime
    return datetime.date.today()


def colors(theme=None, hc=False, month=None):
    """tokens.C contents: role -> (r, g, b)."""
    p = resolve(theme, hc, month)
    return {k: rgb(v) for k, v in p.items()}


def apply(settings=None, today=None):
    """Resolve the palette for the settings and change tokens in place. Every module keeps its
    `from .tokens import C`. Returns the new version."""
    global cur, high_contrast, month, version
    from . import tokens
    cur = THEMES.get(_setting(settings, "theme", "stock"), STOCK)
    high_contrast = bool(_setting(settings, "high_contrast", False))
    month = (today or _today()).month
    tokens.C.clear()
    tokens.C.update(colors(cur, high_contrast, month))
    face = "cond_bold" if cur.heading_font == "cond_bold" else "bold"
    for role in ("display", "title", "heading"):
        _f, size, line, _u = tokens.TYPE_BASE[role]
        tokens.TYPE[role] = (face, size, line, cur.heading_upper)
    tokens.SCRIM = tokens.SCRIM_HC if high_contrast else tokens.SCRIM_BASE
    tokens.GAIN.clear()
    tokens.GAIN.update(tokens.GAIN_HC if high_contrast else tokens.GAIN_BASE)
    version += 1
    return version


def month_changed(today=None):
    """True when Ribbon is on and the month moved since apply() (the minute tick asks)."""
    return callable(cur.palette) and (today or _today()).month != month
