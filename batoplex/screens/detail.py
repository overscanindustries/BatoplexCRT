"""Detail page of a movie, a show or a single episode.

Top zone: the poster, title, meta, rating badges, summary and the buttons. Down on the buttons
goes to the rows zone: an embedded rows view (one row on screen, as Home) with Cast & Crew,
Extras and More Like This.
Up on the first row comes back to the button that had the focus."""
import pygame

from .. import anim, draw, inputs, tokens
from ..model import Row
from ..tokens import C, DETAIL
from ..overlay import icon
from . import Screen, ep_code, fmt_clock, meta_line

PW, PH = DETAIL["poster"]
ROW_ORDER = ("cast", "extras", "related")          # stock Plex order; an empty row is left out
ROW_TITLES = {"cast": "Cast & Crew", "extras": "Extras", "related": "More Like This"}
# A button that went away gives the focus to the one that took its place.
FOCUS_ALT = {"resume": "play", "restart": "play", "play": "resume", "watch": "unwatch",
             "unwatch": "watch", "watchlist": "watch"}


class DetailScreen(Screen):
    def __init__(self, app, item):
        super().__init__(app)
        self.item = item
        self.name = {"movie": "Movie", "show": "Show"}.get(item.type, "Episode")
        self.focus = 0
        self.fkey = None                 # the focused button's key
        self.full = item
        self.zone = "top"                # "top" | "rows"
        self.parts = {}                  # row key -> items (kept for a return to the page)
        self.home = None                 # the embedded rows view
        self.zfade = anim.Anim(tokens.ROW_FADE, anim.LINEAR)   # zone change: a crossfade cut
        self.zfade_img = None
        self._band = None
        # The provider and the sources generation are taken now; a reply that comes
        # back after a profile or server change is dropped before it starts other calls.
        self.prov = app.provider_for(item)
        self.gen = app.src_gen
        self._rows_started = False
        prov = self.prov
        self._bg(lambda: prov.details(item), self._loaded, lambda e: self._start_rows())

    def _bg(self, fn, done, fail=None):
        gen = self.gen

        def ok(r):
            if self.app.src_gen == gen:
                done(r)

        def bad(e):
            if fail is not None and self.app.src_gen == gen:
                fail(e)
        self.run_bg(fn, ok, bad)         # a failure is logged (run_bg); its row does not show

    def _loaded(self, full):
        if full is not None:
            self.full = full
        self._set_part("cast", self.full.extra.get("people") or [])
        self._start_rows()
        self.anim = True

    def _reload(self):
        """A show after a played episode or a mark: On Deck and the counts again; the
        rows and the focus stay."""
        prov, item = self.prov, self.item
        self._bg(lambda: prov.details(item), self._loaded)

    def _start_rows(self):
        """Extras and More Like This load on their own workers, once; each row
        shows when it lands. An episode makes no related call."""
        if self._rows_started:
            return
        self._rows_started = True
        prov, it = self.prov, self.item
        if hasattr(prov, "extras"):
            self._bg(lambda: prov.extras(it), lambda r: self._set_part("extras", r))
        if hasattr(prov, "related") and it.type in ("movie", "show"):
            self._bg(lambda: prov.related(it), lambda r: self._set_part("related", r))

    def _set_part(self, key, items):
        """A row landed. Rows have stable keys; the current row and each row's focus stay,
        whatever order the replies come back in."""
        self.parts[key] = list(items)
        rows = [Row(k, ROW_TITLES[k], list(self.parts[k])) for k in ROW_ORDER if self.parts.get(k)]
        if self.home is None:
            from .home import HomeScreen
            self.home = HomeScreen(self.app, title=self.full.hero_title, rows=rows,
                                   embedded=True, root=False, backdrop=False)
        else:
            self.home.set_rows(rows, keep_focus=True)
        if self.zone == "rows" and not self.home.rows:
            self.zone = "top"
        self.anim = True

    @property
    def has_rows(self):
        return self.home is not None and bool(self.home.rows)

    # ------------------------------------------------------------ buttons
    def buttons(self):
        """Stock Plex detail row: Resume, Play from Start, Mark as Played (icon buttons; the
        focused one shows its label)."""
        it = self.full
        b = []
        if it.type == "show":
            od = it.extra.get("on_deck")         # play is the On Deck episode
            if od is not None:
                b.append(("play", "play", " ".join(x for x in (
                    "Resume" if od.view_offset_ms and not od.watched else "Play",
                    ep_code(od, " ")) if x)))
        elif it.view_offset_ms and not it.watched:
            b.append(("resume", "play", "Resume from " + fmt_clock(it.view_offset_ms)))
            b.append(("restart", "restart", "Play from Start"))
        else:
            b.append(("play", "play", "Play"))
        from .actions import can_watch               # before Mark as Played
        if can_watch(self.app, it):
            on = self.app.watchlist.item_state(it)
            b.append(("watchlist", "bookmark_on" if on else "bookmark",
                      "Remove from Watchlist" if on else "Add to Watchlist"))
        if it.watched:
            b.append(("unwatch", "uncheck", "Mark as Unplayed"))
        else:
            b.append(("watch", "check", "Mark as Played"))
        if self.more_items():
            b.append(("more", "more", "More"))
        return b

    def _focus_index(self, btns):
        keys = [k for k, _i, _l in btns]
        k = self.fkey
        if k in keys:
            return keys.index(k)
        if FOCUS_ALT.get(k) in keys:
            return keys.index(FOCUS_ALT[k])
        return min(self.focus, len(keys) - 1)

    def _sync_focus(self, btns):
        """The key is kept only once the user moved or pressed: before that the first button
        has the focus, also when Play arrives late (a show's On Deck)."""
        self.focus = self._focus_index(btns) if self.fkey is not None else 0

    def apply_files(self, files, exact=False, reload=True):
        for f in files:
            if f["item_id"] == self.full.id and f.get("server_id") in (None, self.full.server_id):
                self.full.view_offset_ms = f["position_ms"]
                self.full.watched = f["watched"] if exact else (f["watched"] or self.full.watched)
        if self.home is not None:            # the rows below show marks too
            self.home.apply_files(files, exact, reload=False)
        if files and reload and self.full.type == "show":
            self._reload()                   # an episode played or the show was marked
        self.anim = True

    def theme_ref(self):
        return self.full.theme if self.full.type in ("episode", "show") else None

    def theme_changed(self):
        if self.home is not None:
            self.home.theme_changed()

    @property
    def menu_title(self):
        return self.full.hero_title

    @property
    def menu_ok(self):
        """Menu and Y work in the top zone only."""
        return self.zone == "top"

    def more_items(self, watch=False):
        """More (…): what the row does not show (Plex More menu). watch: the
        Watchlist row too (the Start menu; the More button leaves it to its button)."""
        it = self.full
        rows = []
        if it.type == "episode" and it.show_id:
            rows.append(("Go to Show", lambda: self.app.go_to_show(it)))
        trailer = self.trailer()
        if trailer is not None:
            rows.append(("Play Trailer", lambda: self.app.play(trailer, resume=False)))
        from .actions import item_rows
        return rows + item_rows(self, it, watch=watch)

    def menu_items(self):
        it = self.full
        rows = []
        if it.view_offset_ms and not it.watched:
            rows.append(("Play from Start", lambda: self.app.play(it, resume=False)))
        rows.append(("Mark as Unplayed" if it.watched else "Mark as Played",
                     lambda: self._mark(not it.watched)))
        return rows + self.more_items(watch=True)

    def trailer(self):
        if self.full.type != "movie":
            return None
        return next((c for c in self.parts.get("extras") or []
                     if c.extra.get("subtype") == "trailer"), None)

    def _mark(self, watched):
        self.app.mark_watched(self.full, watched)
        self.anim = True

    # ------------------------------------------------------------ input
    def _set_zone(self, z):
        canvas = getattr(self.app, "canvas", None)
        if canvas is not None:
            self.zfade_img = canvas.copy()
            self.zfade.start(255, 0)
        self.zone = z
        self.anim = True

    def handle(self, a):
        self.anim = True
        if self.zone == "rows":
            if a == inputs.UP and self.home.r == 0:
                self._set_zone("top")
            elif a == inputs.OK:
                it = self.home.item
                if it is not None:
                    self.app.open_item(it)     # a person page, a title's page
            elif a in (inputs.LEFT, inputs.RIGHT, inputs.UP, inputs.DOWN, inputs.BACK):
                self.home.handle(a)                    # B leaves the page (stock Plex)
            return
        btns = self.buttons()
        self._sync_focus(btns)
        if a == inputs.BACK:
            self.app.pop()
        elif a == inputs.DOWN:
            if self.has_rows:
                self._set_zone("rows")
        elif a == inputs.LEFT and self.focus > 0:
            self.focus -= 1
        elif a == inputs.RIGHT and self.focus < len(btns) - 1:
            self.focus += 1
        elif a == inputs.Y:
            self.app.mark_watched(self.full, not self.full.watched)
        elif a == inputs.OK:
            key = btns[self.focus][0]
            if key == "play" and self.full.type == "show":
                self.app.play(self.full.extra["on_deck"], resume=True)
            elif key in ("resume", "play"):
                self.app.play(self.full, resume=True)
            elif key == "restart":
                self.app.play(self.full, resume=False)
            elif key in ("watch", "unwatch"):
                self._mark(key == "watch")
            elif key == "watchlist":
                self.app.watchlist.set(self.full, not self.app.watchlist.item_state(self.full))
            elif key == "more":
                from .actions import ordered
                self.app.open_menu(ordered(self.more_items()), settings=False)
        self.fkey = btns[self.focus][0]

    def on_resume(self, result=None):
        files = (result or {}).get("files") or []
        for f in files:
            if f["item_id"] == self.full.id and f.get("server_id") in (None, self.full.server_id):
                self.full.view_offset_ms = f["position_ms"]
                self.full.watched = f["watched"] or self.full.watched
        if files and self.home is not None:
            self.home.apply_files(files, reload=False)
        self.anim = True

    # ------------------------------------------------------------ update/draw
    def update(self):
        d, self.anim = self.anim, False
        band = None
        if self.zfade_img is not None:
            d = True
            if not self.zfade.active:
                self.zfade_img = None
        elif self.zone == "rows" and self.home is not None and self.home.update():
            band = self.home.band()                    # the row slide: its band only
            if not d:
                self._band = band
                return True
        self._band = None
        wv = getattr(getattr(self.app, "watchlist", None), "version", None)
        if wv != getattr(self, "_wv", None):         # the Watchlist button state changed
            self._wv, d = wv, True
        return d or self.app.images.version != getattr(self, "_iv", -1)

    def band(self):
        return self._band

    def image_band(self):
        if self.zone == "rows" and self.home is not None and self.zfade_img is None \
                and getattr(self, "_back_shown", False):   # a late backdrop: full pass
            return self.home.image_band()
        return None

    def tint(self, back):
        """The mean colour of the backdrop, S capped at 45 %, V 14 %; with it
        raised = tint + 16 and track = tint + 48. None: no art, or High Contrast."""
        from .. import theme
        if back is None or theme.high_contrast:
            return None
        key = id(back)
        if getattr(self, "_tint_key", None) != key:
            self._tint_key, self._tint = key, tint_of(back)
        return self._tint

    def _draw_back(self, s):
        """The page backdrop and its scrim (both zones). Returns (raised, track) for the page."""
        it = self.full
        back = self.app.images.get(it.art or it.poster, 641, tokens.BACKDROP[1],
                                   tokens.GAIN["backdrop_detail"])
        self._back_shown = back is not None
        tint = self.tint(back)
        bg, raised, track = (tint, lift(tint, 16), lift(tint, 48)) if tint else \
            (C["bg"], None, C["track"])
        s.fill(bg)
        if back is not None:
            s.blit(back, (0, 0))
            if tint:                    # the backdrop shows top right under a scrim of the tint
                if getattr(self, "_scrim", (None,))[0] != tint:
                    self._scrim = (tint, tinted_scrim(tint))
                s.blit(self._scrim[1], (0, 0))
            else:
                draw.rect(s, C["bg"], (0, 0, 641, tokens.BACKDROP[1]), alpha=int(0.90 * 255))
        return raised, track

    def draw(self, s):
        self._iv = self.app.images.version
        raised, track = self._draw_back(s)
        if self.zone == "rows" and self.home is not None:
            self.topbar(s, self.full.hero_title)
            self.home.draw(s)
        else:
            self._draw_top(s, raised, track)
        if self.zfade_img is not None and self.zfade.active:
            self.zfade_img.set_alpha(int(self.zfade.value))
            s.blit(self.zfade_img, (0, 0))
            self.zfade_img.set_alpha(None)

    def _draw_top(self, s, raised, track):
        it = self.full
        sf = self.safe
        self.topbar(s, self.name)
        # Rhythm: flexible gap under the top bar, 4 px inside a group,
        # 20 px between groups, the lowest line 8 px above the safe bottom.
        x = sf["left"]
        top = sf["top"] + 24 + 20
        img = self.app.images.get(it.poster, PW, PH, tokens.GAIN["art"])
        draw.art(s, it, (x, top, PW, PH), img)
        cx = x + PW + DETAIL["gap_col"]
        cw = 640 - sf["right"] - cx
        bottom = 480 - sf["bottom"] - 8
        # bottom group, from the bottom up: media note (with the row cue right), cast
        note = self.foot_note()
        foot_y = bottom - 24
        cast_y = foot_y - 24 - 4 if it.cast else None
        foot_top = cast_y if cast_y is not None else foot_y
        # buttons 20 px above the bottom group; the progress slot (16 px) under the buttons is
        # always kept, so the row does not move with progress
        bw, bh = DETAIL["btn"]
        by = foot_top - 20 - PROGRESS_SLOT - bh
        # The focused label sits right of the row; when a label cannot fit there (four buttons
        # at wide margins) all labels move above the row, so they never cut off.
        btns = self.buttons()
        self._sync_focus(btns)
        row_end = cx + len(btns) * (bw + DETAIL["btn_gap"])
        lw = 640 - sf["right"] - row_end - 12
        above = any(draw.width(l, "label") > lw for k, _i, l in btns if k != "resume") or \
            any(k == "resume" and draw.width("Resume", "label") > lw for k, _i, _l in btns)
        limit = by - 20 - (32 if above else 0)
        # title group from the top; the summary takes the lines that fit above the buttons
        y = top
        title = it.title if it.type != "episode" else it.show_title
        room = limit - top
        badges = it.ratings
        lines = draw.wrap(title, "title", cw, 2 if room >= 40 * 2 + 28 + 4 + 24 + 52 + (
            BADGE_H + 4 if badges else 0) else 1)
        for ln in lines:
            draw.text(s, ln, "title", C["text"], cx, y, w=cw)
            y += 40
        if it.type == "episode":
            draw.text(s, it.title, "heading", C["text"], cx, y, w=cw)
            y += 28
        draw.text(s, meta_line(it), "label", C["text2"], cx, y + 4, w=cw)
        y += 4 + 24
        if badges:
            # The badge line 4 px under the meta line. The gap under
            # it closes from 12 to 8 when that gives the summary one more line.
            draw_badges(s, badges, cx, y + 4, cw, raised)
            y += 4 + BADGE_H
            gap = 12 if (limit - y - 12) // 26 >= (limit - y - 8) // 26 else 8
            y += gap
        else:
            y += 12
        n = max(0, min(SUMMARY_LINES, (limit - y) // 26))
        if n:
            draw.paragraph(s, it.summary, "body", C["text2"], cx, y, cw, n)
        # Icon buttons: Play or Resume is the primary; the focused one's label
        # sits to the right of the row
        bx = cx
        for i, (key, ic, _label) in enumerate(btns):
            draw.button(s, (bx, by, bw, bh), icon=lambda sf_, ix, iy, col, ic=ic: icon(
                sf_, ic, ix, iy, col), kind="primary" if key in ("play", "resume") else
                "secondary", focused=i == self.focus, rest=raised)
            bx += bw + DETAIL["btn_gap"]
        label = btns[self.focus][2]
        if above:
            draw.text(s, label, "label", C["text"], cx, by - 32, w=cw)
        else:
            if draw.width(label, "label") > lw and btns[self.focus][0] == "resume":
                label = "Resume"                  # the progress line shows where
            draw.text(s, label, "label", C["text"], bx + 12, by + 8, w=lw)
        if it.progress > 0:
            draw.progress(s, cx, by + bh + 12, cw, it.progress, track=track)
        if cast_y is not None:
            draw.text(s, ", ".join(it.cast), "small", C["text2"], cx, cast_y, w=cw)
        # The row cue, right on the foot line: a down triangle and the name
        # of the first row, while one row or more has items.
        cue_w = 0
        if self.has_rows:
            cue = self.home.rows[0].title
            tw = draw.text(s, cue, "small", C["dim"], 640 - sf["right"], foot_y, align="right")
            cue_w = tw + 8 + tokens.CUE
            draw.triangle(s, C["dim"], 640 - sf["right"] - cue_w, foot_y + 6)
        draw.text(s, note, "small", C["dim"], cx, foot_y, w=cw - cue_w - (12 if cue_w else 0))

    def foot_note(self):
        it = self.full
        if it.type == "show":
            n, left = it.leaf_count, it.leaf_count - it.viewed_leaf_count
            if not n:
                return ""
            return "%d episode%s" % (n, "" if n == 1 else "s") + (
                " • %d unplayed" % left if 0 < left < n else "")
        return "Direct Play" + (" • " + it.media_note if it.media_note else "")


PROGRESS_SLOT = 16               # 12 px gap + the 4 px progress line under the buttons
SUMMARY_LINES = 6
BADGE_H, BADGE_MARK, BADGE_GAP = 24, 20, 16


def badge_width(r):
    """One badge: the 20 px mark (or the source tag box), 8 px, the value."""
    if r.source in ("imdb", "tmdb"):
        mark = draw.width(r.source.upper(), "tag") + 12
    else:
        mark = BADGE_MARK
    return mark + 8 + draw.width(r.value, "label")


def badges_fit(ratings, w):
    """How many badges fit in w, in order (the last ones drop)."""
    n, x = 0, 0
    for r in ratings:
        bw = badge_width(r)
        if x + bw > w:
            break
        x += bw + BADGE_GAP
        n += 1
    return n


def draw_badges(s, ratings, x, y, w, raised=None):
    """Generic marks (no logos, no trademarks), fill against outline for the
    state. y: the top of the 24 px line; the mark is 20 px at y+2."""
    for r in ratings[:badges_fit(ratings, w)]:
        my = y + 2
        if r.source in ("imdb", "tmdb"):
            tag = r.source.upper()
            mw = draw.width(tag, "tag") + 12
            draw.rect(s, raised or C["raised"], (x, my, mw, BADGE_MARK))
            draw.text(s, tag, "tag", C["text2"], x + 6, my)
        else:
            mw = BADGE_MARK
            _mark(s, r, x, my)
        draw.text(s, r.value, "label", C["text"], x + mw + 8, y)
        x += badge_width(r) + BADGE_GAP


def _mark(s, r, x, y):
    m = BADGE_MARK
    cx, cy = x + m // 2, y + m // 2
    if r.source == "rt" and r.kind == "audience":
        cup = [(x + 1, y + 2), (x + m - 1, y + 2), (x + m - 5, y + m - 2), (x + 5, y + m - 2)]
        if r.state == "down":
            pygame.draw.polygon(s, C["dim"], cup, 2)
        else:
            pygame.draw.polygon(s, C["accent"], cup)
    elif r.source == "rt":
        if r.state == "rotten":
            pygame.draw.circle(s, C["ok"], (cx, cy), m // 2 - 1, 2)
            pygame.draw.line(s, C["ok"], (x + 4, y + m - 4), (x + m - 4, y + 4), 3)
        else:
            pygame.draw.circle(s, C["error"], (cx, cy), m // 2)
    else:
        import math
        pts = []
        for k in range(10):
            rad = (m // 2) if k % 2 == 0 else (m // 5)
            a = math.pi / 2 + k * math.pi / 5
            pts.append((cx + rad * math.cos(a), cy - rad * math.sin(a)))
        pygame.draw.polygon(s, C["accent"], pts)


def tint_of(img):
    """The page tint of a backdrop."""
    import colorsys
    src = img if img.get_bitsize() in (24, 32) else img.convert(24)
    r, g, b = pygame.transform.smoothscale(src, (1, 1)).get_at((0, 0))[:3]
    h, sat, _v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    rr, gg, bb = colorsys.hsv_to_rgb(h, min(sat, 0.45), 0.14)
    return (int(round(rr * 255)), int(round(gg * 255)), int(round(bb * 255)))


def lift(c, d):
    return tuple(min(255, v + d) for v in c)


def scrim_mask(w=641, h=None):
    """White with alpha: opaque left and at the bottom, 72 % at the top right, where the
    backdrop shows (text over it keeps its contrast)."""
    h = h or tokens.BACKDROP[1]

    def fn(x, y):
        left = 1.0 if x <= 200 else max(0.72, 1.0 - 0.28 * (x - 200) / 280)
        bot = 0.0 if y <= 140 else min(1.0, (y - 140) / 160)
        return 1 - (1 - left) * (1 - bot)
    return draw.gradient_alpha(w, h, (255, 255, 255), fn)


def tinted_scrim(tint):
    m = draw.cached("detail_scrim_mask", scrim_mask).copy()
    m.fill((*tint, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return m
