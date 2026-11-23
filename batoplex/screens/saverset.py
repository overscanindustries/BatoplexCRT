"""Settings > Screen > Screen Saver.

SaverScreen: Start After, Type, the options of the type, Preview. A live preview box beside
the rows shows each change at once. Type: Left / Right step; A opens TypeScreen, the types as
rows with a larger live preview (as Theme). Preview starts the saver now; any press wakes it
and the focus is on Preview again (the stack does not change).
"""
import json

import pygame

from .. import draw, inputs, saveropts
from ..tokens import C
from . import Screen, listlayout

START = [(0, "Off"), (3, "3 Minutes"), (5, "5 Minutes"), (10, "10 Minutes")]
GAP = 24
FRAME = 2


def value_label(choices, v):
    return dict(choices).get(v, str(v))


def step(choices, cur, a):
    """The next value for Left / Right (clamped) or A (wraps)."""
    vals = [c[0] for c in choices]
    i = vals.index(cur) if cur in vals else 0
    if a == inputs.LEFT:
        i = max(0, i - 1)
    elif a == inputs.RIGHT:
        i = min(len(vals) - 1, i + 1)
    else:
        i = (i + 1) % len(vals)
    return vals[i]


class PreviewBox:
    """A live saver of one type and its current options, scaled into a 4:3 box."""

    def __init__(self, app):
        self.app = app
        self.sv = None
        self.key = None
        self.canvas = None
        self.img = None
        self.dirty = False

    def show(self, kind):
        """Make the saver again when the type or an option changed."""
        key = (kind, json.dumps(saveropts.all_of(self.app.settings, kind), sort_keys=True,
                                default=str))
        if key == self.key:
            return
        self.close()
        from ..saver import Screensaver
        self.key = key
        self.img = None                           # no picture of the old type for a field
        self.sv = Screensaver(self.app, kind=kind, music=False, inset=True)
        self.sv.held = True
        self.dirty = True

    def close(self):
        if self.sv is not None:
            self.sv.held = False
        self.sv, self.key = None, None

    def update(self, size):
        """Once a field. True when the box picture changed. Nothing while a real saver
        shows (Preview): the page is under it."""
        if self.sv is None or getattr(self.app, "saver", None) is not None:
            return False
        changed = self.sv.update() or self.dirty
        if changed or self.img is None or self.img.get_size() != size:
            if self.canvas is None:
                self.canvas = draw.opaque_surface((641, 480))
            self.sv.draw(self.canvas)
            self.img = pygame.transform.smoothscale(self.canvas, size)
            self.dirty = False
            return True
        return False

    def draw(self, s, rect):
        r = pygame.Rect(rect)
        draw.rect(s, C["track"], r.inflate(2 * FRAME, 2 * FRAME))
        if self.img is not None and self.img.get_size() == r.size:
            s.blit(self.img, r.topleft)
        else:
            s.fill((0, 0, 0), r)


def box_rect(scr, list_w):
    """The 4:3 preview box right of a list of width list_w, at the list top."""
    x, full, y0, y1 = listlayout.area(scr)
    w = full - list_w - GAP - 2 * FRAME
    w -= w % 4
    h = (w * 3 // 4) // 4 * 4
    return pygame.Rect(x + list_w + GAP + FRAME, y0 + FRAME, w, h)


class SaverScreen(Screen):
    ambient = True
    name = "SCREEN SAVER"
    LIST_W = 320

    def __init__(self, app):
        super().__init__(app)
        self.focus = 0
        self.top = 0
        self.box = PreviewBox(app)

    @property
    def kind(self):
        return self.app.settings["screensaver_type"]

    def rows(self):
        st = self.app.settings
        kind = self.kind
        out = [("choice", "min", "Start After", value_label(START, st["screensaver_min"])),
               ("action", "type", "Type", saveropts.label(kind))]
        for key, label, choices, _d, _w in saveropts.visible(st, kind):
            out.append(("choice", "opt:" + key, label,
                        value_label(choices, saveropts.get(st, kind, key))))
            if kind == "weather" and key == "place" and saveropts.get(st, kind, key) == "custom":
                where = saveropts.extra(st, kind, "where") or {}
                out.append(("action", "where", "Place", where.get("name") or "Set"))
            if kind == "aquarium" and key == "fish":
                tank = saveropts.get(st, kind, "tank")
                on, _d = saveropts.aq_species(st, tank)
                out.append(("action", "species", "Species",
                            "%d of %d" % (len(on), len(saveropts.aq_choices(tank)))))
        if kind == "shuffle":
            n = len(saveropts.shuffle_set(st))
            out.append(("action", "set", "Savers", "%d Savers" % n))
        if kind == "weather":
            pl = saveropts.extra(st, kind, "playlist") or {}
            out.append(("action", "playlist", "Music", pl.get("title") or "None"))
        out.append(("action", "preview", "Preview", ""))
        return out

    def handle(self, a):
        rows = self.rows()
        self.focus = min(self.focus, len(rows) - 1)
        st = self.app.settings
        if a == inputs.BACK:
            self.box.close()
            self.app.saved(st.save())
            self.app.pop()
            return
        if a in (inputs.UP, inputs.DOWN):
            self.focus = max(0, min(len(rows) - 1, self.focus + (1 if a == inputs.DOWN else -1)))
            x, _w, y0, y1 = listlayout.area(self, self.LIST_W)
            self._press = pygame.Rect(0, y0 - 4, x + self.LIST_W + 8, y1 - y0 + 8)
            # The help line under the list changes too (full width)
            self._press.union_ip(pygame.Rect(0, 480 - self.safe["bottom"] - 28, 641, 32))
            return
        if a not in (inputs.OK, inputs.LEFT, inputs.RIGHT):
            return
        key = rows[self.focus][1]
        if key == "min":
            st["screensaver_min"] = step(START, st["screensaver_min"], a)
        elif key == "type":
            if a == inputs.OK:
                self.app.push(TypeScreen(self.app))
                return
            types = [(k, saveropts.label(k)) for k in saveropts.SAVERS]
            st["screensaver_type"] = step(types, self.kind, a)
        elif key.startswith("opt:"):
            o = saveropts.option(self.kind, key[4:])
            saveropts.put(st, self.kind, o[0], step(o[2], saveropts.get(st, self.kind, o[0]), a))
        elif key == "preview":
            if a == inputs.OK:
                self.app.saved(st.save())
                self.app.start_saver(self.kind, preview=True)
            return
        elif key == "set":
            if a == inputs.OK:
                self.app.push(shuffle_set_screen(self.app))
            return
        elif key == "species":
            if a == inputs.OK:
                self.app.push(species_screen(self.app))
            return
        elif key == "where":
            if a == inputs.OK:
                self._ask_place()
            return
        elif key == "playlist":
            if a == inputs.OK:
                self._pick_playlist()
            return
        self.app.saved(st.save())
        self.focus = min(self.focus, len(self.rows()) - 1)   # a row can hide (Art: Posters)

    # ------------------------------------------------------------ Weather
    def _ask_place(self):
        from .name import NameScreen
        st = self.app.settings
        cur = (saveropts.extra(st, "weather", "where") or {}).get("typed", "")
        self.app.push(NameScreen(self.app, cur, "Save", self._geocode,
                                 placeholder="ZIP or City", max_len=40))

    def _geocode(self, typed):
        from .. import weather
        typed = (typed or "").strip()
        if not typed:
            return
        country = (weather.load_cache().get("geoip") or {}).get("country")

        def done(found):
            if self not in self.app.stack:
                return                            # the page closed meanwhile
            if not found:
                self.app.toast("Can't find %s" % typed)
            elif len(found) == 1:
                self._set_place(found[0], typed)
            else:
                rows = [(", ".join(p for p in (f["name"], f["region"], f["country"]) if p),
                         lambda f=f: self._set_place(f, typed)) for f in found]
                from .menu import MenuScreen
                self.app.push(MenuScreen(self.app, self, "Place", rows))

        def fail(e):
            self.app.toast("Can't look up places right now")
        self.run_bg(lambda: weather.geocode(typed, country), done, fail)

    def _set_place(self, f, typed):
        st = self.app.settings
        saveropts.put_extra(st, "weather", "where", dict(f, typed=typed))
        self.app.saved(st.save())
        self.box.key = None                       # the preview fetches the new place

    def _pick_playlist(self):
        src = getattr(self.app, "sources", None)
        if src is None or not src.has_playlists():
            self.app.toast("No music playlists")
            return

        def done(res):
            if self not in self.app.stack:
                return
            items = [p for p in res[0] if p.type == "playlist"]
            rows = [("None", lambda: self._set_playlist(None))]
            rows += [(p.title, lambda p=p: self._set_playlist(p)) for p in items]
            from .menu import MenuScreen
            self.app.push(MenuScreen(self.app, self, "Music", rows))

        def fail(e):
            self.app.toast("Can't load playlists")
        self.run_bg(lambda: src.playlists("audio", self.app.settings), done, fail)

    def _set_playlist(self, p):
        st = self.app.settings
        saveropts.put_extra(st, "weather", "playlist", None if p is None else {
            "id": p.id, "server_id": p.server_id, "title": p.title})
        self.app.saved(st.save())

    def on_resume(self, result=None):
        self.focus = min(self.focus, len(self.rows()) - 1)

    def update(self):
        self.box.show(self.kind)
        return self.box.update(box_rect(self, self.LIST_W).size)

    def band(self):
        return box_rect(self, self.LIST_W).inflate(2 * FRAME + 2, 2 * FRAME + 2)

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Screen Saver")
        rows = self.rows()
        listlayout.draw_list(self, s, rows, self.focus, width=self.LIST_W)
        r = listlayout.items(rows)[min(self.focus, len(rows) - 1)] if rows else None
        if r is not None and not getattr(self, "hide_hints", False):
            sf = self.safe
            draw.text(s, saveropts.help_for(self.kind, r[1]), "small", C["text2"], sf["left"],
                      480 - sf["bottom"] - 24, w=640 - sf["left"] - sf["right"])
        self.box.show(self.kind)
        if self.box.img is None:
            self.box.update(box_rect(self, self.LIST_W).size)
        self.box.draw(s, box_rect(self, self.LIST_W))


class TypeScreen(Screen):
    """The types as rows, a live preview of the focused one. A keeps it, B goes back."""
    ambient = True
    name = "SCREEN SAVER TYPE"
    LIST_W = 220

    def __init__(self, app):
        super().__init__(app)
        self.keys = list(saveropts.SAVERS)
        cur = app.settings["screensaver_type"]
        self.focus = self.keys.index(cur) if cur in self.keys else 0
        self.top = 0
        self.box = PreviewBox(app)

    def handle(self, a):
        if a in (inputs.UP, inputs.DOWN):
            self.focus = max(0, min(len(self.keys) - 1,
                                    self.focus + (1 if a == inputs.DOWN else -1)))
        elif a == inputs.OK:
            st = self.app.settings
            st["screensaver_type"] = self.keys[self.focus]
            self.app.saved(st.save())
            self.box.close()
            self.app.pop()
        elif a == inputs.BACK:
            self.box.close()
            self.app.pop()

    def update(self):
        self.box.show(self.keys[self.focus])
        return self.box.update(box_rect(self, self.LIST_W).size)

    def band(self):
        return box_rect(self, self.LIST_W).inflate(2 * FRAME + 2, 2 * FRAME + 2)

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Screen Saver Type")
        cur = self.app.settings["screensaver_type"]
        rows = [("type", k, saveropts.label(k), "") for k in self.keys]
        listlayout.draw_list(self, s, rows, self.focus, width=self.LIST_W,
                             current=lambda r: r[1] == cur)
        self.box.show(self.keys[self.focus])
        if self.box.img is None:
            self.box.update(box_rect(self, self.LIST_W).size)
        self.box.draw(s, box_rect(self, self.LIST_W))
        self.hintbar(s, [("A", "Select"), ("B", "Cancel")])


class SetScreen(Screen):
    """A list of items, each On or Off; A, Left or Right changes it. At least one stays On.
    rows: (key, label) or ("head", label) for a group heading; get() the keys On; put(keys)."""
    ambient = True

    def __init__(self, app, title, rows, get, put, name="SET"):
        super().__init__(app)
        self.title, self.all, self.get, self.put = title, rows, get, put
        self.name = name
        self.keys = [k for k, _l in rows if k != "head"]
        self.focus = 0
        self.top = 0

    def handle(self, a):
        st = self.app.settings
        if a in (inputs.UP, inputs.DOWN):
            self.focus = max(0, min(len(self.keys) - 1,
                                    self.focus + (1 if a == inputs.DOWN else -1)))
        elif a in (inputs.OK, inputs.LEFT, inputs.RIGHT):
            on = list(self.get())
            k = self.keys[self.focus]
            if k in on and len(on) > 1:
                on.remove(k)
            elif k not in on:
                on.append(k)
            self.put([x for x in self.keys if x in on])
            self.app.saved(st.save())
        elif a == inputs.BACK:
            self.app.pop()

    def update(self):
        return False

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, self.title)
        on = self.get()
        rows = [("head", "", lb, "") if k == "head" else
                ("choice", k, lb, "On" if k in on else "Off") for k, lb in self.all]
        listlayout.draw_list(self, s, rows, self.focus)


def shuffle_set_screen(app):
    """Shuffle's savers: every type but Shuffle."""
    st = app.settings
    keys = [k for k in saveropts.SAVERS if k != "shuffle"]
    return SetScreen(app, "Shuffle", [(k, saveropts.label(k)) for k in keys],
                     lambda: saveropts.shuffle_set(st),
                     lambda on: saveropts.put_extra(st, "shuffle", "set", on), "SHUFFLE SAVERS")


def species_screen(app):
    """The Aquarium species of the current tank (Mixed: all, under Reef and Freshwater)."""
    st = app.settings
    tank = saveropts.get(st, "aquarium", "tank")
    rows = []
    for group, head in (("reef", "Reef"), ("fresh", "Freshwater")):
        if tank == "mixed":
            rows.append(("head", head))
        rows += [(k, lb) for k, lb, t in saveropts.AQ_SPECIES
                 if t == group and (tank == "mixed" or t == tank)]
    return SetScreen(app, "Species", rows, lambda: saveropts.aq_species(st, tank)[0],
                     lambda on: saveropts.put_aq_species(st, tank, on), "AQUARIUM SPECIES")
