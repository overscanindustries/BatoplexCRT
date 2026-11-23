"""Settings > Screen > Theme: the five themes as rows on the left, a preview
on the right. A move applies the theme to the whole screen at once; A keeps it, B puts the old
one back."""
from types import SimpleNamespace

from .. import draw, inputs, theme, tokens
from ..tokens import C
from . import Screen, listlayout

LIST_W = 240
GAP = 24
POSTER = (72, 108)
SAMPLE = SimpleNamespace(title="Poster", type="movie")


class ThemeScreen(Screen):
    name = "THEME"

    def __init__(self, app):
        super().__init__(app)
        self.keys = list(theme.THEMES)
        self.old = app.settings["theme"]
        self.focus = self.keys.index(self.old) if self.old in self.keys else 0
        self.top = 0
        self.dirty = True

    def _try(self, key):
        self.app.settings["theme"] = key
        self.app.apply_theme()

    def handle(self, a):
        self.dirty = True
        st = self.app.settings
        if a in (inputs.UP, inputs.DOWN):
            n = self.focus + (1 if a == inputs.DOWN else -1)
            if 0 <= n < len(self.keys):
                self.focus = n
                self._try(self.keys[n])
        elif a == inputs.OK:
            if st["theme"] != self.keys[self.focus]:
                self._try(self.keys[self.focus])
            self.app.saved(st.save())
            self.app.pop()
        elif a == inputs.BACK:
            if st["theme"] != self.old:
                self._try(self.old)
                self.app.saved(st.save())                    # a worker save may have written the preview
            self.app.pop()

    def update(self):
        d, self.dirty = self.dirty, False
        return d

    def draw(self, s):
        sf = self.safe
        s.fill(C["bg"])
        self.topbar(s, "Theme")
        rows = [("theme", k, theme.THEMES[k].name, "") for k in self.keys]
        listlayout.draw_list(self, s, rows, self.focus, width=LIST_W,
                             current=lambda r: r[1] == self.old)
        x, _full, y0, y1 = listlayout.area(self)
        self.preview(s, x + LIST_W + GAP, y0, 640 - sf["right"], y1)
        self.hintbar(s, [("A", "Select"), ("B", "Cancel")])

    def preview(self, s, x0, y0, x1, y1):
        """A poster with artwork focus, the three text colours, a tab line, a progress bar, a
        primary and a secondary button, two rows (one focused) and a toast."""
        w = x1 - x0
        o = tokens.FOCUS_OUTLINE + tokens.FOCUS_KEYLINE
        pw, ph = POSTER
        pr = (x0 + o, y0 + o, pw, ph)
        draw.art(s, SAMPLE, pr, None)
        draw.focus_frame(s, pr)
        cx = x0 + pw + 2 * o + 12
        cw = x1 - cx
        for i, (label, role) in enumerate((("Text", "text"), ("Secondary", "text2"),
                                           ("Disabled", "dim"))):
            draw.text(s, label, "label", C[role], cx, y0 + i * 24, w=cw)
        draw.tabs(s, ["Movies", "Shows"], 0, False, cx, x1, y0 + 76)
        draw.progress(s, x0 + o + 8, y0 + o + ph - 12, pw - 16, 0.4)
        y = y0 + ph + 2 * o + 8
        bw = draw.button_w("Play")
        draw.button(s, (x0, y, bw, tokens.CTRL_H), "Play", kind="primary")
        sw = draw.button_w("Shuffle")
        if bw + tokens.BTN_GAP + sw <= w:
            draw.button(s, (x0 + bw + tokens.BTN_GAP, y, sw, tokens.CTRL_H), "Shuffle")
        y += tokens.CTRL_H + 8
        draw.row(s, (x0, y, w), "Focused Row", focused=True)
        y += tokens.ROW_PITCH
        draw.row(s, (x0, y, w), "Row", "Value")
        y += tokens.ROW_PITCH + 4
        msg = "Added to Queue"
        tw = min(w, draw.width(msg, "label") + 2 * tokens.INSET)
        if y + tokens.CTRL_H <= y1:
            draw.toast(s, msg, (x0, y, tw, tokens.CTRL_H))
