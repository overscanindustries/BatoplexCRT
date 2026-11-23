"""The name of a playlist: the Search field and key grid, then Create (or
Save) and Cancel under the grid. The grid has lower-case letters only; a letter at the start
or after a space becomes upper case. A real keyboard types as it is. B is Cancel.

The name is user text: it is never logged.
"""
import pygame

from .. import draw, inputs, tokens
from . import Screen
from .keys import KeyGrid, draw_field, draw_keys, keys_top

MAX_LEN = 60


def suggest(it, taken=()):
    """The name to start from: the album, artist, show or season title, else "New Playlist";
    " 2" when a playlist of that name exists."""
    if it is not None and it.type in ("album", "artist", "show"):
        name = it.title
    elif it is not None and it.type == "season":
        name = " • ".join(p for p in (it.show_title, it.title) if p)
    else:
        name = "New Playlist"
    name = name[:MAX_LEN]
    low = {t.lower() for t in taken}
    if name.lower() in low:
        n = 2
        while ("%s %d" % (name, n)).lower() in low:
            n += 1
        name = "%s %d" % (name, n)
    return name


def grid_char(text, c):
    """A grid letter: upper case at the start of the name or after a space."""
    if c.isalpha() and (not text or text.endswith(" ")):
        return c.upper()
    return c


class NameScreen(Screen):
    ok_on_press = True            # a key grid, OK at once
    ambient = True                # the idle ribbon may show here
    name = "NAME"
    takes_text = True
    takes_backspace = True

    def __init__(self, app, text, action, on_done, placeholder="Playlist name", max_len=MAX_LEN):
        """action: "Create" or "Save". on_done(name) runs after the screen closes."""
        super().__init__(app)
        self.placeholder, self.max_len = placeholder, max_len
        self.text = text[:max_len]
        self.action, self.on_done = action, on_done
        self.zone = "keys"                       # keys | buttons
        self.btn = 0
        self.keys = KeyGrid(lambda c: self._type(c, grid=True), self._delete,
                            lambda: self._set(""))
        self.dirty = True

    # ------------------------------------------------------------ text
    def _set(self, t):
        self.text = t[:self.max_len]
        if not self.text.strip() and self.zone == "buttons":
            self.zone = "keys"
        self.dirty = True

    def _type(self, c, grid=False):
        if c == " " and (not self.text or self.text.endswith(" ")):
            return                               # no leading or double spaces
        self._set(self.text + (grid_char(self.text, c) if grid else c))

    def _delete(self):
        self._set(self.text[:-1])

    @property
    def value(self):
        return " ".join(self.text.split())

    # ------------------------------------------------------------ input
    def handle(self, a):
        self.dirty = True
        if a.startswith(inputs.CHAR):
            self._type(a[len(inputs.CHAR):])
            self.zone, self.keys.typing = "keys", True
            return
        if a in (inputs.BACKSPACE, inputs.X):
            self._delete()
            return
        if a == inputs.Y:
            self._type(" ")
            return
        if a == inputs.BACK:
            self.app.pop()
            return
        if a == inputs.QUIT:
            self.app.quit()
            return
        if self.zone == "keys":
            if self.keys.handle(a, lambda: bool(self.value)):
                self.zone, self.btn, self.keys.typing = "buttons", 0, False
            return
        if a in (inputs.LEFT, inputs.RIGHT):
            self.btn = 0 if a == inputs.LEFT else 1
        elif a == inputs.UP:
            self.zone = "keys"
        elif a == inputs.OK:
            self.app.pop()
            if self.btn == 0 and self.value:
                self.on_done(self.value)

    def update(self):
        d, self.dirty = self.dirty, False
        return d

    # ------------------------------------------------------------ geometry
    def geo(self):
        sf = self.safe
        x0, x1, T = sf["left"], 640 - sf["right"], sf["top"]
        pitch = 40 if x1 - x0 >= 548 else 36
        g = dict(x0=x0, x1=x1, T=T, kh=32, kp=36, keys_top=keys_top(T), pitch=pitch,
                 side_x=x0 + 12 * pitch)
        g["btn_y"] = g["keys_top"] + 2 * 36 + 32 + 24
        return g

    def btn_rect(self, i, g=None):
        """Text + 32 wide (96 at least), 8 apart."""
        g = g or self.geo()
        x = g["x0"]
        for k, label in enumerate((self.action, "Cancel")):
            w = draw.button_w(label)
            if k == i:
                return pygame.Rect(x, g["btn_y"], w, tokens.CTRL_H)
            x += w + tokens.BTN_GAP

    # ------------------------------------------------------------ draw
    def draw(self, s):
        g = self.geo()
        draw.background(s, self)
        draw_field(s, g["x0"], g["x1"], g["T"], self.text, self.placeholder, scr=self)
        draw_keys(s, g, self.keys, self.zone == "keys" and not self.keys.typing)
        ready = bool(self.value)
        for i, label in enumerate((self.action, "Cancel")):
            draw.button(s, self.btn_rect(i, g), label, focused=self.zone == "buttons" and
                        self.btn == i, enabled=ready or i == 1)
