"""Episode picker: season tabs, 7-row episode list, still + summary.
The list never scrolls smoothly: it steps one row in one field (no vertical motion)."""
from .. import draw, inputs, tokens
from ..tokens import C, EPISODES as E
from . import Screen, ep_code, fmt_dur

SW, SH = E["still"]
ROW_H, PITCH = tokens.ROW_H, tokens.ROW_PITCH


class EpisodeScreen(Screen):
    def __init__(self, app, show_id, show_title, focus_episode=None, show=None):
        super().__init__(app)
        self.name = "TV"
        self.show_id = show_id
        self.show_title = show_title
        self.show = show
        sid = getattr(focus_episode, "server_id", None) or getattr(show, "server_id", None)
        self.sid = sid
        self.prov = app.sources.provider(sid)     # the show's own server
        self.seasons = []
        self.s = 0
        self.eps = {}          # season id -> list
        self.focus = {}        # season id -> index
        self.top = {}          # season id -> first visible row
        self.in_tabs = False
        self.in_title = False      # up from the tabs; A: the show page
        self.target = focus_episode
        self.loading = True
        self.error = None
        self._load_seasons()
        self._load_show()

    def _load_show(self):
        """An episode list opened from an episode has a show with only its id
        and title. Its metadata (the guid) loads in the background; the Watchlist row shows
        when it is there."""
        wv = getattr(self.app, "watch_visible", None)
        if self.show is not None and self.show.extra.get("guid") or not (wv and wv()):
            return
        if not hasattr(self.prov, "details"):
            return
        gen, base = getattr(self.app, "src_gen", 0), self.show_item()

        def done(full):
            if getattr(self.app, "src_gen", 0) != gen or full is None:
                return
            if full.type == "show" and full.extra.get("guid"):
                self.show = full
        self.run_bg(lambda: self.prov.details(base), done, lambda e: None)

    def _load_seasons(self):
        self.loading, self.error = True, None
        self.run_bg(lambda: self.prov.seasons(self.show_id), self._seasons, self._fail)

    def retry(self):
        """A after a failed load: the season list, else the current season."""
        if self.loading:
            return
        if not self.seasons:
            self._load_seasons()
        else:
            self._load_season()
        self.anim = True

    def _fail(self, e):
        self.loading, self.error = False, str(e)
        self.anim = True

    def _seasons(self, seasons):
        self.seasons = [x for x in seasons if x.type == "season"]
        if not self.seasons:
            self._fail(Exception("No seasons"))
            return
        if self.target and self.target.season_id:
            for i, se in enumerate(self.seasons):
                if se.id == self.target.season_id:
                    self.s = i
        else:  # first season with unwatched episodes
            for i, se in enumerate(self.seasons):
                if not se.watched:
                    self.s = i
                    break
        self._load_season()

    def _load_season(self):
        se = self.seasons[self.s]
        if se.id in self.eps:
            self.anim = True
            return
        self.loading, self.error = True, None
        sid = se.id

        def current():
            se = self.season
            return se is not None and se.id == sid

        def done(eps):
            self.eps[sid] = eps
            if current():                 # an older season's reply leaves the flags alone
                self.loading, self.error = False, None
            idx = 0
            if self.target and any(e.id == self.target.id for e in eps):
                idx = next(i for i, e in enumerate(eps) if e.id == self.target.id)
                self.target = None
            else:
                idx = next((i for i, e in enumerate(eps) if not e.watched), 0)
            self.focus[sid] = idx
            self.top[sid] = max(0, min(idx - 3, len(eps) - self._geo()["rows"]))
            self.anim = True
        def fail(e):
            if current():
                self._fail(e)
        self.run_bg(lambda: self.prov.episodes(sid), done, fail)

    def apply_files(self, files, exact=False, reload=True):
        """Playback results and mark played/unplayed from any screen above this one."""
        show_id, sid = getattr(self, "show_id", None), getattr(self, "sid", None)
        if reload and show_id and any(f["item_id"] == show_id and f.get("server_id") in (None, sid)
                                      for f in files):
            self._reload_all()                    # the show was marked
            return
        done = {(f.get("server_id"), f["item_id"]): f for f in files}
        for eps in self.eps.values():
            for e in eps:
                f = done.get((e.server_id, e.id)) or done.get((None, e.id))
                if f:
                    e.view_offset_ms = f["position_ms"]
                    e.watched = f["watched"] if exact else (f["watched"] or e.watched)
        if files and not exact:
            self.follow(files[-1]["item_id"], files[-1].get("server_id"))
        self.anim = True

    def _reload_all(self):
        """Seasons and episodes again, the focus on the same episode."""
        self.target = self.episode or self.target
        self.eps = {}
        self._load_seasons()
        self.anim = True

    def show_item(self):
        if self.show is not None:
            return self.show
        from ..model import Item
        return Item(id=self.show_id, type="show", title=self.show_title, server_id=self.sid)

    def open_show(self):
        from .detail import DetailScreen
        self.app.push(DetailScreen(self.app, self.show_item()))

    def theme_ref(self):
        ep = self.episode
        return (ep.theme if ep else None) or (self.show.theme if self.show else None)

    @property
    def menu_title(self):
        ep = self.episode
        return ep.title if ep else self.show_title

    def menu_items(self):
        ep, se = self.episode, self.season
        rows = []
        if ep is not None:
            if ep.view_offset_ms and not ep.watched:
                rows.append(("Play from Start", lambda: self.app.play(ep, resume=False)))
            rows.append(("Mark as Unplayed" if ep.watched else "Mark as Played",
                         lambda: self.app.mark_watched(ep, not ep.watched)))
            from .detail import DetailScreen
            rows.append(("Details", lambda: self.app.push(DetailScreen(self.app, ep))))
        if se is not None and se.id in self.eps:
            rows.append(("Mark Season as Played", lambda: self._mark_season(se)))
        rows.append(("Show Details", self.open_show))
        from .actions import item_rows, watch_row
        wr = watch_row(self.app, self.show_item())
        if wr is not None:
            rows.append(wr)
        if ep is not None and not self.in_tabs:
            rows += item_rows(self, ep)
        elif se is not None:
            rows += item_rows(self, se)
        return rows

    def _mark_season(self, se):
        self.app.mark_watched(se, True)
        for e in self.eps.get(se.id) or []:
            e.watched, e.view_offset_ms = True, 0
        self.anim = True

    def follow(self, item_id, server_id=None):
        """Focus the episode that played last (autoplay may have moved to a later one)."""
        for i, se in enumerate(self.seasons):
            eps = self.eps.get(se.id) or []
            for n, e in enumerate(eps):
                if e.id == item_id and server_id in (None, e.server_id):
                    self.s, self.focus[se.id] = i, n
                    top = self.top.get(se.id, 0)
                    if not top <= n < top + self._geo()["rows"]:
                        self.top[se.id] = max(0, min(n - 3, len(eps) - self._geo()["rows"]))
                    self.in_tabs = False
                    return
        # An episode of a season that is not loaded yet: keep the current focus.

    @property
    def season(self):
        return self.seasons[self.s] if self.seasons else None

    @property
    def episode(self):
        se = self.season
        if not se or se.id not in self.eps or not self.eps[se.id]:
            return None
        return self.eps[se.id][self.focus.get(se.id, 0)]

    def _move(self, d):
        se = self.season
        if se is None:                    # the season list did not load
            return
        eps = self.eps.get(se.id) or []
        n = self.focus.get(se.id, 0) + d
        if n < 0:
            self.in_tabs = True
            return
        if n >= len(eps):
            return
        self.focus[se.id] = n
        top = self.top.get(se.id, 0)
        if n < top:
            top = n
        elif n >= top + self._geo()["rows"]:
            top = n - self._geo()["rows"] + 1
        self.top[se.id] = top

    def _season(self, d):
        n = self.s + d
        if 0 <= n < len(self.seasons):
            self.s = n
            self._load_season()

    def handle(self, a):
        self.anim = True
        if self.in_title:
            if a == inputs.OK:
                self.open_show()
            elif a in (inputs.DOWN, inputs.BACK):
                self.in_title = False
            return
        if a == inputs.BACK:
            if self.in_tabs:
                self.in_tabs = False
            else:
                self.app.pop()
            return
        if a == inputs.LB:
            self._season(-1)
        elif a == inputs.RB:
            self._season(1)
        elif self.in_tabs:
            if a == inputs.LEFT:
                self._season(-1)
            elif a == inputs.RIGHT:
                self._season(1)
            elif a in (inputs.DOWN, inputs.OK):
                self.in_tabs = False
            elif a == inputs.UP:
                self.in_title = True
        elif a == inputs.OK and self.error and self.episode is None:
            self.retry()
        elif a == inputs.UP:
            self._move(-1)
        elif a == inputs.DOWN:
            self._move(1)
        elif a == inputs.OK and self.episode:
            self.app.play(self.episode, resume=True)
        elif a == inputs.X and self.episode:
            from .detail import DetailScreen
            self.app.push(DetailScreen(self.app, self.episode))     # X: Details
        elif a == inputs.Y and self.episode:
            ep = self.episode
            self.app.mark_watched(ep, not ep.watched)

    def on_resume(self, result=None):
        done = {f["item_id"]: f for f in (result or {}).get("files") or []}
        for eps in self.eps.values():
            for e in eps:
                f = done.get(e.id)
                if f:
                    e.view_offset_ms = f["position_ms"]
                    e.watched = f["watched"] or e.watched
        if done and result.get("item_id") in done:
            self.follow(result["item_id"], result.get("server_id"))
        self.anim = True

    def update(self):
        d = self.anim
        self.anim = False
        ep = self.episode
        if ep:
            g = self._geo()
            self.app.images.prefetch(ep.still, g["sw"], g["sh"], tokens.GAIN["art"])
        return d or self.late_due() or self.app.images.version != getattr(self, "_iv", -1)

    def _geo(self):
        """Layout from the live safe area: rows of 40 on a 44 pitch,
        the last one ending 12 px above the safe bottom (no hint bar); the
        list narrows and the still shrinks so both stay inside the safe area."""
        sf = self.safe
        x, right = sf["left"], 640 - sf["right"]
        ly = sf["top"] + 32 + 40
        hint_y = 480 - sf["bottom"]
        rows = max(3, (hint_y - 12 - ly + PITCH - ROW_H) // PITCH)
        lw = E["list_w"] if right - (x + E["list_w"] + 16) >= 200 else 288
        px = x + lw + 16
        pw = right - px
        sw = min(SW, pw) & ~1
        sh = (sw * 9 // 16) & ~1
        return dict(x=x, right=right, ly=ly, rows=rows, lw=lw, px=px, pw=pw, sw=sw, sh=sh,
                    hint_y=hint_y)

    def tab_labels(self):
        """"Season 1" for 5 seasons or fewer, else "S1"."""
        long = len(self.seasons) <= 5
        out = []
        for se in self.seasons:
            if not se.index:
                out.append("Specials" if long else "SP")
            else:
                out.append(("Season %s" if long else "S%s") % se.index)
        return out

    def draw(self, s):
        self._iv = self.app.images.version
        sf = self.safe
        s.fill(C["bg"])
        x, right = sf["left"], 640 - sf["right"]
        top = sf["top"]
        g = self._geo()
        # Page-title top bar: the show in heading; the year and seasons and
        # the clock right.
        info = ""
        if self.show and self.show.year and self.seasons:
            info = "%s • %d season%s" % (self.show.year, len(self.seasons),
                                          "" if len(self.seasons) == 1 else "s")
        iw = draw.width(info, "small") + 16 if info else 0
        cx = right - self.topbar(s, "" if self.in_title else self.show_title, reserve=iw,
                                 page=True)
        if self.in_title:                         # the show title has the control focus
            tw = min(draw.width(self.show_title, "heading") + 2 * tokens.INSET,
                     cx - 16 - iw - x)
            draw.focus_fill(s, (x, top - 2, tw, tokens.CHIP_H))
            draw.text(s, self.show_title, "heading", C["ink"], x + tokens.INSET, top,
                      w=tw - 2 * tokens.INSET)
        if info:
            draw.text(s, info, "small", C["text2"], cx - 16, top, align="right")
        if self.error and not self.seasons:
            draw.state(s, x, g["ly"], right - x, "Can't load this show", "Press A to try again.")
            return
        if self.late(self.loading and not self.seasons, "seasons"):
            draw.state(s, x, g["ly"], right - x, "Loading…")
        if not self.seasons:
            return
        ty = top + 32
        self.tab_off = draw.tabs(s, self.tab_labels(), self.s, self.in_tabs and not self.in_title,
                                 x, right, ty,
                                 getattr(self, "tab_off", 0))
        ly = g["ly"]
        se = self.season
        eps = self.eps.get(se.id) if se else None
        rows, lw = g["rows"], g["lw"]
        if se is not None and se.id in self.eps:    # the safe area may have changed (Calibrate)
            fi, eps_n = self.focus.get(se.id, 0), len(self.eps[se.id])
            topi = max(0, min(self.top.get(se.id, 0), max(0, eps_n - rows)))
            if fi < topi:
                topi = fi
            elif fi >= topi + rows:
                topi = fi - rows + 1
            self.top[se.id] = topi
        if eps is None and self.error and not self.loading:
            draw.state(s, x, ly, lw, "Can't load this season", "Press A to try again.")
        elif eps is None:
            if self.late(True, "season"):
                draw.state(s, x, ly, lw, "Loading…")
        else:
            self.late(False, "season")
            topi = self.top.get(se.id, 0)
            fi = self.focus.get(se.id, 0)
            for r, i in enumerate(range(topi, min(len(eps), topi + rows))):
                self._row(s, eps[i], x, ly + r * PITCH, lw, i == fi and not self.in_tabs)
            draw.scrollbar(s, x + lw + 4, ly, rows * PITCH - (PITCH - ROW_H), topi, rows,
                           len(eps))
        # right pane
        ep = self.episode
        px, pw = g["px"], g["pw"]
        if ep:
            sw, sh = g["sw"], g["sh"]
            img = self.app.images.get(ep.still, sw, sh, tokens.GAIN["art"])
            draw.art(s, ep, (px, ly, sw, sh), img, lines=3)
            yy = ly + sh + 12
            bits = [ep_code(ep)] if ep_code(ep) else []
            if ep.view_offset_ms and not ep.watched:
                bits.append("%s left" % fmt_dur(ep.duration_ms - ep.view_offset_ms))
            elif ep.duration_ms:
                bits.append(fmt_dur(ep.duration_ms))
            draw.text(s, " • ".join(bits), "label", C["text2"], px, yy, w=pw)
            draw.paragraph(s, ep.summary, "small", C["text2"], px, yy + 32, pw,
                           max(1, (g["hint_y"] - 12 - (yy + 32)) // 24))

    def _row(self, s, e, x, y, lw, foc):
        """A 40 px row; the number, the title, the length right and
        the state column at the right edge. Watched: the title in dim."""
        if foc:
            draw.focus_fill(s, (x, y, lw, ROW_H))
        ink = C["ink"]
        tx = x + tokens.INSET + 32
        state_x = x + lw - tokens.INSET - 8
        dur = fmt_dur(e.duration_ms) if e.duration_ms else ""
        dw = draw.width(dur, "small") + 12 if dur else 0
        draw.text(s, "%02d" % (e.index or 0), "list", ink if foc else C["dim"],
                  x + tokens.INSET, y + 8)
        tcol = ink if foc else (C["dim"] if e.watched else C["text"])
        draw.text(s, e.title, "list", tcol, tx, y + 8, w=state_x - 12 - dw - tx)
        if dur:
            draw.text(s, dur, "small", ink if foc else C["text2"], state_x - 12, y + 8,
                      align="right")
        draw.row_marks(s, e, state_x, y, tx, foc)
