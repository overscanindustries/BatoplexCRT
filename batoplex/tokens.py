"""Design tokens for the 480i interface.

All values are canvas pixels on a 640x480 logical canvas. Vertical values are even
and on a 4 px grid so horizontal edges stay stable on a 480i picture.
"""

MODE_W, MODE_H = 641, 480          # framebuffer = X mode (641x480 interlaced)
CANVAS_W, CANVAS_H = 640, 480      # logical canvas, placed at x 0; column 640 is spare

# Default safe area. The calibration screen replaces these (settings.json).
SAFE = {"left": 40, "right": 40, "top": 36, "bottom": 36}
SAFE_LIMITS = {"h": (16, 72), "v": (16, 72), "step": 4}


# Palette roles. theme.apply() fills C in place; the end of this file fills
# it with Stock so a module that never calls apply() still draws.
C = {}

GAIN_BASE = {"art": 0.92, "unfocused": 0.72, "backdrop": 0.80, "backdrop_detail": 0.50}
GAIN_HC = dict(GAIN_BASE, unfocused=0.60, backdrop=0.50)
GAIN = dict(GAIN_BASE)

# (font file key, size, line box, uppercase). Sizes and line boxes are the
# same in every theme; a theme changes only the face and case of display, title, heading.
TYPE_BASE = {
    "display": ("bold", 44, 52, False),
    "title":   ("bold", 32, 40, False),
    "heading": ("bold", 24, 28, False),
    "label":   ("semi", 22, 24, False),
    "list":    ("cond_semi", 22, 24, False),
    "body":    ("semi", 20, 26, False),
    "small":   ("semi", 18, 24, False),
    "tag":     ("bold", 16, 20, True),
    "code":    ("cond_bold", 96, 100, True),
}
TYPE = dict(TYPE_BASE)

FONT_FILES = {
    "cond_bold": "BarlowCondensed-Bold.ttf",
    "cond_semi": "BarlowCondensed-SemiBold.ttf",
    "semi": "Barlow-SemiBold.ttf",
    "bold": "Barlow-Bold.ttf",
    "rain": "NotoSansJP-Rain.ttf",       # Digital Rain only: 60 glyphs
}

# Home rhythm. The content chain is anchored to
# the safe bottom; the gap under the top bar is the only flexible one (minimum hero_top_min).
# Spacing scale: 4 / 8 / 12 / 20 / 48, all y and heights on the 4 px grid.
HOME = {
    "title_w": 360,
    "poster": (112, 168), "poster_f": (128, 192), "poster_mid": (120, 180),
    "gap": 20, "pitch": 132,
    "gap_title_meta": 4, "gap_hero_row": 20, "gap_label_row": 12, "row_bottom_inset": 8,
    "hero_top_min": 16,
}
BACKDROP = (640, 360)
FOCUS_OUTLINE, FOCUS_KEYLINE = 4, 2
PROGRESS_H, PROGRESS_INSET = 4, 8

DETAIL = {"poster": (144, 216), "gap_col": 20, "btn": (48, 40), "btn_gap": 8}
# Detail page rows: the cell of a row by its item type. Art ends on the focused
# art's bottom (ftop + fh_end), so the two text lines under it align.
DETAIL_ROWS = {
    "person": {"pw": 112, "ph": 112, "fw": 128, "fh": 128, "pitch": 132, "round": True},
    "clip": {"pw": 160, "ph": 88, "fw": 192, "fh": 108, "pitch": 180, "round": False},
}
DETAIL_ART_END = 128             # px from the row top to the art bottom of person and clip cells

EPISODES = {"list_w": 320, "still": (224, 126)}

# ------------------------------------------------------------ geometry
# Vertical values are multiples of 4.
SPACE = (4, 8, 12, 16, 20, 24, 32, 48)
RADIUS, CUT = 0, 8
INSET = 16                     # text inset in rows, buttons, chips, panels, toasts
PANEL_PAD, DIALOG_PAD = 8, 24
CTRL_H = 40                    # buttons, icon buttons, skip, toast, PIN keys
CHIP_H = 32                    # tabs, chips, text field, on-screen keys
ICON_BTN_W = 48
BTN_GAP, BTN_PAD, BTN_MIN_W = 8, 16, 96
ROW_H, ROW_PITCH = 40, 44
ROW_COMPACT_H, ROW_COMPACT_PITCH = 32, 36
ROW_TWO_H, ROW_TWO_PITCH = 56, 60
HEAD_H = 36
GROUP_GAP = 16          # the space between two groups with no heading
SCROLLBAR_W, CUE = 4, 12
SCRIM_BASE, SCRIM_HC = 176, 224
SCRIM = SCRIM_BASE             # theme.apply() sets it (High Contrast: 224)
OVERLAY_ALPHA = 230
DIALOG_W, MENU_W = 360, 320
PANEL_EDGE, PANEL_ROW_W = 256, 240
TOAST_GAP = 24                 # toast to clock

# ------------------------------------------------------------ motion, ms
INSTANT, FAST, BASE, SETTLE, SLOW = 0, 117, 167, 250, 300
ROW_FADE, OVERLAY_FADE = 133, 100
TOAST_IN, TOAST_HOLD, TOAST_OUT = 100, 2500, 167
AMBIENT_DELAY, AMBIENT_IN = 8000, 600
Q_Y, MIN_STEP_Y, Q_X, MIN_STEP_X = 4, 8, 4, 8
FIELD_MS = 1000.0 / 59.94

# D-pad repeat in milliseconds, so the rate does not depend on the loop rate.
REPEAT_DELAY_MS, REPEAT_EVERY_MS = 333, 133
OK_HOLD_MS = 500          # a hold of OK opens the menu (as Menu)
FPS = 60

# Fill C with Stock (theme does not import tokens at its top, so this is not circular).
from . import theme as _theme  # noqa: E402
C.update(_theme.colors())
