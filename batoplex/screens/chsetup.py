"""Set Up Channels, Add a Channel, Manage Channels.

Set Up (the first run): More > Set Up Channels (A), Make Channels for Me (A), Start (Save): the
guide opens. The scan reads a light index of each library on a worker (20 s at most) and fetches
no pool. The result list has the curated set On (10 themes and 2 marathons) and,
under "Suggested", the themes that were not picked and every other good source, Off (one press
turns one on). The list scrolls above a fixed "Save N Channels" bar over the hint line (Down from
the last row; Start saves from any row). B goes back one step.

Add a Channel: "Suggested" first (one press adds one), then "Choose What Plays": kind, source,
a summary with Save in focus (Name, Number and Order are optional rows). A suggestion is one
press; a show of your choice is RT RT (to Choose What Plays), then A A A.

Manage Channels: each channel with its status (why a channel is not in the guide). A opens
Rename, Schedule, Move, Turn Off and Delete Channel. Start Over (with a confirm step) deletes
every channel and runs Make Channels for Me again. Schedule: the time blocks of a channel;
Add a Time Block is kind, source, then a summary with the fixed Save Block bar in focus (Starts,
Days and the order are optional rows with smart defaults; Start saves from any row). Every edit
goes through channels.py with the stamp of the file the screen shows: a Refused edit writes
nothing and its message is the toast. The lineup then runs its freeze on its worker (no wait on
the UI thread).

Channel names are user text: never logged.
"""
import time

from .. import channels, draw, inputs, lineup, themes, tokens
from ..tokens import C
from . import Screen, listlayout

INTRO = "Channels play your library like TV. Nothing changes on your server."
WIDTH = 440


def count_text(p):
    n, what = p["count"], p["what"]
    if n == 1:
        what = what[:-1]
    h = p["minutes"] // 60
    return "%d %s • %d h" % (n, what, h)


class SetupScreen(Screen):
    name = "LIVETV_SETUP"
    ok_on_press = True            # menu is Save here, OK acts at once

    def __init__(self, app):
        super().__init__(app)
        self.state = "intro"                 # intro | scan | result | empty
        self.props, self.on, self.on0 = [], [], []
        self.used = set()                    # numbers of the channels already in the file
        self.focus = 0
        self.top = 0
        self.ptop = 0                        # result list scroll (px)
        self.gen = 0

    # -- result rows: ("head", label) | ("ch", index); focus len(focusable()) is the Save bar
    def items(self):
        out = []
        sugg = False
        for i, p in enumerate(self.props):
            if not p["default"] and not sugg:
                sugg = True
                out.append(("head", "Suggested"))
            out.append(("ch", i))
        return out

    def focusable(self):
        return [k for k, r in enumerate(self.items()) if r[0] != "head"]

    def n_on(self):
        return sum(self.on)

    def numbers(self, used):
        """The number each On row gets when saved (list order, no gaps); None for Off rows."""
        used, out, at = set(used), [], channels.FIRST_NUMBER
        for on in self.on:
            n = channels.free_number(used, at) if on else None
            if n is not None:
                used.add(n)
                at = n + 1
            out.append(n)
        return out

    def dirty(self):
        """The result has edits: an On/Off differs from what the scan gave."""
        return self.state == "result" and self.on != self.on0

    def _intro(self):
        self.state, self.focus, self.gen = "intro", 0, self.gen + 1

    def handle(self, a):
        if a == inputs.BACK:
            if self.dirty():
                ask_discard(self.app, self._intro)
            elif self.state in ("result", "empty", "scan"):
                self._intro()
            else:
                self.app.pop()
            return
        if a == inputs.QUIT:
            self.app.quit()
            return
        if self.state == "intro":
            if a in (inputs.UP, inputs.DOWN):
                self.focus = 0 if a == inputs.UP else 1
            elif a == inputs.OK and self.focus == 1:
                self.app.push(AddScreen(self.app, done=self._added))
            elif a == inputs.OK:
                self._scan()
        elif self.state == "result":
            fs = self.focusable()
            if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
                self.focus = bar_step(self.focus, len(fs), a)
            elif a == inputs.MENU or (a == inputs.OK and self.focus >= len(fs)):
                self._save()
            elif a == inputs.OK:
                r = self.items()[fs[self.focus]]
                self.on[r[1]] = not self.on[r[1]]

    def _added(self, cid):
        if cid:                                   # a channel exists now: the guide on it
            self.app.pop()
            from .guide import GuideScreen
            self.app.push(GuideScreen(self.app, focus=cid))

    def _scan(self):
        self.state = "scan"
        self.gen += 1
        gen, src = self.gen, self.app.sources
        libs = src.pinned(self.app.settings)          # UI thread: memory only

        def work():                                   # worker: the scan and the curation
            libs_s, pls, analysis = lineup.scan(src, libs)
            existing = channels.load().channels
            used = {c.number for c in existing}
            return used, channels.suggestions(themes.curate(analysis, existing),
                                              channels.propose(libs_s, pls, existing=existing),
                                              used)

        def done(res):
            if gen != self.gen or self.state != "scan":
                return
            self.used, self.props = res
            self.on = [p["default"] for p in self.props]
            self.on0 = list(self.on)
            self.focus, self.ptop = 0, 0
            self.state = "result" if self.props else "empty"

        def fail(_e):
            if gen == self.gen:
                self.state = "empty"
        self.run_bg(work, done, fail)

    def _save(self):
        if not self.n_on():
            self.app.toast("Turn on a channel first")
            return
        lu = channels.load()
        chs = []
        nums = self.numbers({c.number for c in lu.channels})
        for p, on, n in zip(self.props, self.on, nums):
            if on:
                chs.append(dict(p["channel"], number=n))
        try:
            ids = channels.add_channels(chs, lu.stamp)
        except channels.Refused as e:
            self.app.toast(str(e))
            return
        self.app.lineup.refresh()
        from .guide import GuideScreen
        self.app.pop()
        self.app.push(GuideScreen(self.app, focus=ids[0] if ids else None))

    def update(self):
        return self.late_due()

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Channels")
        x, full, y0, y1 = listlayout.area(self)
        w = min(full, WIDTH)
        if self.state == "intro":
            draw.paragraph(s, INTRO, "body", C["text2"], x, y0, w, 2)
            listlayout.draw_list(self, s, [("row", "make", "Make Channels for Me", ""),
                                           ("row", "add", "Add a Channel", "")], self.focus,
                                 width=w, skip=64)
            self.hintbar(s, [("A", "Select"), ("B", "Back")])
        elif self.state == "scan":
            if self.late(True, "scan"):
                draw.state(s, x, y0, w, "Looking at your libraries…")
            self.hintbar(s, [("B", "Back")])
        elif self.state == "empty":
            self.late(False, "scan")
            draw.state(s, x, y0, full, "No channels to make",
                       "Pin a library with 48 hours of shows or movies.")
            self.hintbar(s, [("B", "Back")])
        else:
            self.late(False, "scan")
            self._result(s, x, y0, y1, w)

    def _result(self, s, x, y0, y1, w):
        items = self.items()
        fs = self.focusable()
        bar = self.focus >= len(fs)
        fk = None if bar else fs[self.focus]
        pos, yy = [], 0
        for r in items:
            h = tokens.HEAD_H if r[0] == "head" else tokens.ROW_TWO_PITCH
            pos.append((r, yy, h))
            yy += h
        y1, _by = bar_area(y1)
        view = y1 - y0
        _r, fy, fh = pos[fk] if not bar else (None, self.ptop, 0)   # the bar: the list stays
        if fk == fs[0]:
            self.ptop = 0
        elif fy < self.ptop:
            self.ptop = fy - (tokens.HEAD_H if fk > 0 and items[fk - 1][0] == "head" else 0)
        elif fy + fh > self.ptop + view:
            self.ptop = fy + fh - view
        self.ptop = max(0, min(self.ptop, max(0, yy - view)))
        lw = w - 12 if yy > view else w
        clip = s.get_clip()
        s.set_clip((0, y0, tokens.MODE_W, view))
        nums = self.numbers(self.used)
        for k, (r, ry, h) in enumerate(pos):
            y = y0 + ry - self.ptop
            if y + h <= y0 or y >= y1:
                continue
            foc = k == fk
            if r[0] == "head":
                draw.heading_row(s, x, y, lw, r[1])
            else:
                p = self.props[r[1]]
                ch = p["channel"]
                n = nums[r[1]]
                draw.row_two(s, (x, y, lw), "%s  %s" % (lineup.num(n), ch["name"]) if n
                             else ch["name"],
                             count_text(p), right="On" if self.on[r[1]] else "Off", focused=foc)
        s.set_clip(clip)
        draw.scrollbar(s, x + w - tokens.SCROLLBAR_W, y0, view, self.ptop, view, yy)
        n = self.n_on()
        draw_bar(s, x, y1, lw, "Save %d Channel%s" % (n, "" if n == 1 else "s") if n
                 else "Save Channels", bar, enabled=bool(n), scrolls=yy > view)
        self.hintbar(s, bar_hints([("A", "Save" if bar else "On or Off")]))


# ---------------------------------------------------------------- shared

SCAN_KEEP_S = 300            # a second Add a Channel within 5 min uses the last scan
SUGGEST_FIRST = 6            # suggestions shown before "More Suggestions"
PAGE = 6                     # LT/RT rows
_scan = {"key": None, "t": 0.0, "res": None}

FILE_ERRORS = {"version": "channels.json is from a newer version",
               "size": "channels.json is too large", "read": "Can't read channels.json"}


def ask_discard(app, discard):
    """B with unsaved edits asks first; the focus starts on Cancel."""
    from .system import ConfirmDialog
    app.overlay(ConfirmDialog(app, app.top, "Discard changes?", "Discard", discard))


def block_state(b):
    """A time block as the unsaved-edits check compares it: a new block has
    no "once" and a toggle there and back leaves once=False; days may come as a list."""
    days = tuple(x for x in channels.schedule.DAYS if x in b["days"]) if b.get("days") else None
    return (b.get("start"), days, channels.source_key(b["source"]), b.get("order"),
            bool(b.get("once")))


def file_error(rules):
    """The line for a channels.json that did not load, or one with channels left out."""
    if rules.error:
        if rules.error == "parse" and rules.line:
            return "channels.json has an error (line %d)" % rules.line
        return FILE_ERRORS.get(rules.error, "channels.json has an error")
    if rules.faults:
        n = rules.faults
        return "%d channel%s ha%s an error in channels.json" % (n, "" if n == 1 else "s",
                                                                 "s" if n == 1 else "ve")
    return None


def plural(n, what):
    return "%d %s" % (n, what[:-1] if n == 1 else what)


def status_text(ch, status):
    """The status value of a channel in Manage Channels: why it is not in the
    guide. A channel the lineup has not frozen yet (new, or just turned on) is "Loading…"."""
    if ch.off:
        return lineup.STATUS_TEXT[lineup.OFF]
    st = status.get(ch.id)
    if st is None or st == lineup.OFF:
        return "Loading…"
    return lineup.STATUS_TEXT[st]


def kind_name(ch):
    return "Time Blocks" if ch.blocks else channels.KIND_NAMES[ch.source["type"]]


def scan_work(src, libs):
    """(libraries, playlists, the theme catalog) for catalog() and propose() (worker)."""
    libs_s, pls, analysis = lineup.scan(src, libs)
    return libs_s, pls, themes.catalog(analysis)


def catalog(res):
    """channels.catalog() of a scan result (an older result has no theme catalog)."""
    return channels.catalog(res[0], res[1], res[2] if len(res) > 2 else ())


def scan(scr, done, fail):
    """scan_work on a worker; a scan of the same account and profile under SCAN_KEEP_S old
    is used again at once."""
    app = scr.app
    key = getattr(app.lineup, "key", None)
    if _scan["res"] is not None and _scan["key"] == key \
            and time.monotonic() - _scan["t"] < SCAN_KEEP_S:
        done(_scan["res"])
        return
    src = app.sources
    libs = src.pinned(app.settings)                  # UI thread: memory only

    def ok(res):
        _scan.update(key=key, t=time.monotonic(), res=res)
        done(res)
    scr.run_bg(lambda: scan_work(src, libs), ok, fail)


def edit(scr, call):
    """One channels.py edit with the stamp of the file the screen shows. Returns (True, the
    result); Refused: (False, None), its message as the toast. The screen reads the file again
    either way, and after a write the lineup refreshes on its worker."""
    try:
        out = call(scr.rules.stamp)
    except channels.Refused as e:
        scr.app.toast(str(e))
        scr.reload()
        return False, None
    scr.reload()
    scr.app.lineup.refresh()
    return True, out


def mixed_list(scr, s, rows, fk, x, y0, y1, w, height, paint):
    """A list with rows of mixed heights (headings, rows, two-line rows). scr.ptop keeps the
    focused row, and the heading above it, in view; a cut row is not drawn.
    fk None: no row has the focus (the Save bar has it) and the list stays where it was.
    Returns the row width (narrower when the list scrolls)."""
    pos, yy = [], 0
    for r in rows:
        h = height(r)
        pos.append((r, yy, h))
        yy += h
    view = y1 - y0
    _r, fy, fh = pos[fk] if fk is not None else (None, scr.ptop, 0)   # the bar: the list stays
    first = next(k for k, r in enumerate(rows) if r[0] != "head")
    if fk == first:
        scr.ptop = 0
    elif fy < scr.ptop:
        scr.ptop = fy - (pos[fk - 1][2] if rows[fk - 1][0] == "head" else 0)
    elif fy + fh > scr.ptop + view:
        scr.ptop = fy + fh - view
    scr.ptop = max(0, min(scr.ptop, max(0, yy - view)))
    lw = w - 12 if yy > view else w
    for k, (r, ry, h) in enumerate(pos):
        y = y0 + ry - scr.ptop
        fill = h if r[0] == "head" else h - 4
        if y < y0 or y + fill > y1:
            continue
        paint(r, x, y, lw, k == fk)
    draw.scrollbar(s, x + w - tokens.SCROLLBAR_W, y0, view, scr.ptop, view, yy)
    return lw


# The Save bar: a list that can be longer than its view scrolls above a fixed
# Save row, directly above the hint line. The focus index n (the row count) is the bar: Down from
# the last row reaches it, Up goes back; Start saves from any row.
BAR_GAP = 12                 # list bottom to bar top; a 2 px raised line in it when it scrolls


def bar_area(y1):
    """(the list bottom, the bar top) in a list area that ends at y1."""
    by = y1 - tokens.ROW_H
    return by - BAR_GAP, by


def bar_step(focus, n, a):
    """The focus after Up, Down, LT or RT over n rows and the bar (n). LT/RT page the rows."""
    if a in (inputs.UP, inputs.DOWN):
        return max(0, min(n, focus + (1 if a == inputs.DOWN else -1)))
    if a == inputs.RT and focus >= n:
        return n
    return max(0, min(n - 1, focus + (PAGE if a == inputs.RT else -PAGE)))


def draw_bar(s, x, lb, w, label, focused, enabled=True, scrolls=False):
    """The bar under a list that ends at lb (bar_area): a row at the list's x and row width."""
    by = lb + BAR_GAP
    if scrolls:
        draw.rect(s, C["raised"], (x, by - BAR_GAP // 2 - 2, w, 2))
    draw.row(s, (x, by, w), label, focused=focused, enabled=enabled)


def bar_hints(first):
    """The row's hints, then Start: Save (left out when A already says Save: the bar)."""
    start = [] if ("A", "Save") in first else [("menu", "Save")]
    return first + start + [("B", "Back")]


def rename(app, name, on_done):
    from .name import NameScreen
    app.push(NameScreen(app, name, "Save",
                        lambda n: on_done(channels.clean(n, channels.NAME_FILE_MAX).upper()),
                        placeholder="Channel name", max_len=channels.NAME_FILE_MAX))


# ---------------------------------------------------------------- Add a Channel

class AddScreen(Screen):
    """Steps: home (Suggested, then the kinds), source, summary. B goes back one step; B on
    home closes. done(channel id or None) runs after the screen closes: the last channel
    added."""
    name = "LIVETV_ADD"

    def __init__(self, app, done=None, pick=None):
        super().__init__(app)
        self.done = done
        self.pick = pick                     # Schedule: pick(entry) takes the source; no save
        self.step = "scan"                   # scan | home | source | summary | empty
        self.rules = channels.load()
        self.props, self.cat = [], {}
        self.more = False                    # every suggestion shows
        self.kind = self.entry = self.ch = None
        self.focus, self.ptop = 0, 0
        self.back_focus = {}
        self.last = None
        scan(self, self.scanned, self._failed)

    def reload(self):
        self.rules = channels.load()

    def scanned(self, res):
        if self.step != "scan":
            return
        libs, pls = res[0], res[1]
        self.props = [] if self.pick else channels.propose(libs, pls,
                                                            existing=self.rules.channels)
        self.cat = catalog(res)
        self.step = "home" if self.props or self.cat else "empty"
        self.focus, self.ptop = 0, 0

    def _failed(self, _e):
        if self.step == "scan":
            self.step = "empty"

    # -- rows
    def have(self):
        """{source key: channel number} of the channels in the file."""
        return {channels.source_key(src): c.number for c in self.rules.channels
                for _s, src in c.slots()}

    def orders(self):
        src = self.entry["source"]
        return channels.order_choices(src["type"], channels.source_lib_type(
            src, self.entry.get("lib_type")))

    def rows(self):
        if self.step == "home":
            out = []
            if self.props:
                n = len(self.props)
                show = n if self.more or n <= SUGGEST_FIRST + 1 else SUGGEST_FIRST
                out += [("head", "Suggested")] + [("sug", i) for i in range(show)]
                if show < n:
                    out.append(("more", n - show))
            if self.cat:
                out += [("head", "Choose What Plays")] + \
                    [("kind", k) for k in channels.KINDS if k in self.cat]
            return out
        if self.step == "source":
            return [("src", i) for i in range(len(self.cat[self.kind]))]
        if self.step == "summary":
            return [("save",), ("name",), ("number",)] + ([("order",)] if len(self.orders()) > 1
                                                         else [])
        return []

    @staticmethod
    def focusable(rows):
        return [k for k, r in enumerate(rows) if r[0] != "head"]

    def focused(self):
        rows = self.rows()
        fs = self.focusable(rows)
        if not fs:
            return rows, fs, None
        self.focus = max(0, min(self.focus, len(fs) - 1))
        return rows, fs, rows[fs[self.focus]]

    # -- input
    def handle(self, a):
        if a == inputs.QUIT:
            self.app.quit()
            return
        if a == inputs.BACK:
            self._back()
            return
        _rows, fs, r = self.focused()
        if r is None:
            return
        if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
            d = {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -PAGE, inputs.RT: PAGE}[a]
            self.focus = max(0, min(len(fs) - 1, self.focus + d))
        elif a in (inputs.LEFT, inputs.RIGHT) and self.step == "summary":
            self.adjust(r[0], -1 if a == inputs.LEFT else 1)
        elif a == inputs.OK:
            self._ok(r)

    def _ok(self, r):
        k = r[0]
        if k == "sug":
            self._add_suggestion(self.props[r[1]])
        elif k == "more":
            self.more = True                     # the focus stays: on the next suggestion
        elif k == "kind":
            self.back_focus["home"] = self.focus
            self.kind, self.step, self.focus, self.ptop = r[1], "source", 0, 0
        elif k == "src" and self.pick is not None:
            self.step = "closed"
            self.app.pop()
            self.pick(self.cat[self.kind][r[1]])
        elif k == "src":
            self.back_focus["source"] = self.focus
            self.entry = self.cat[self.kind][r[1]]
            self.ch = channels.draft(self.entry, self.rules.channels)
            self.step, self.focus, self.ptop = "summary", 0, 0
        elif k == "save":
            self.save()
        elif k == "name":
            rename(self.app, self.ch["name"], lambda n: self.ch.update(name=n) if n else None)
        else:
            self.adjust(k, 1)

    def adjust(self, k, d):
        """Left/Right (and A) on the Number and Order rows of the summary."""
        if k == "number" and self.ch["number"] is not None:
            used = {c.number for c in self.rules.channels}
            self.ch["number"] = channels.step_number(self.ch["number"], used, d)
        elif k == "order":
            o = self.orders()
            self.ch["order"] = o[(o.index(self.ch["order"]) + d) % len(o)] \
                if self.ch["order"] in o else o[0]

    def _add_suggestion(self, p):
        if channels.source_key(p["channel"]["source"]) in self.have():
            return                               # added already
        n = channels.free_number({c.number for c in self.rules.channels})
        if n is None:
            self.app.toast("Channels are full")
            return
        ok, ids = edit(self, lambda st: channels.add_channels([dict(p["channel"], number=n)], st))
        if ok:
            self.last = ids[0]
            self.app.toast("Added as Channel %s" % lineup.num(n))

    def save(self):
        if self.ch["number"] is None:
            self.app.toast("Channels are full")
            return
        if self.ch["number"] in {c.number for c in self.rules.channels}:   # taken meanwhile
            self.ch["number"] = channels.free_number({c.number for c in self.rules.channels})
        ok, cid = edit(self, lambda st: channels.save_channel(dict(self.ch), st))
        if ok:
            self.last = cid
            self.app.toast("Added as Channel %s" % lineup.num(self.ch["number"]))
            self._close()

    def _back(self):
        if self.step == "summary":
            self.step, self.focus, self.ptop = "source", self.back_focus.get("source", 0), 0
        elif self.step == "source":
            self.step, self.focus, self.ptop = "home", self.back_focus.get("home", 0), 0
        else:
            self._close()

    def _close(self):
        self.step = "closed"
        self.app.pop()
        if self.done is not None:
            self.done(self.last)

    def on_resume(self, result=None):
        self.reload()

    # -- frame
    def update(self):
        return self.late_due()

    def title(self):
        if self.pick is not None and self.step != "source":
            return "What Plays"
        if self.step == "source":
            return "Choose " + channels.KIND_ROWS[self.kind].replace("A ", "a ", 1)
        return "New Channel" if self.step == "summary" else "Add a Channel"

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, self.title(), page=True)
        x, full, y0, y1 = listlayout.area(self)
        if self.step == "scan":
            if self.late(True, "scan"):
                draw.state(s, x, y0, full, "Looking at your libraries…")
            self.hintbar(s, [("B", "Back")])
            return
        self.late(False, "scan")
        if self.step == "empty":
            draw.state(s, x, y0, full, "Nothing to make a channel from",
                       "Pin a library with shows or movies.")
            self.hintbar(s, [("B", "Back")])
            return
        rows, fs, r = self.focused()
        if r is None:
            return
        if self.step == "summary":
            e = self.entry
            draw.text(s, e["title"], "heading", C["text"], x, y0, w=full)
            draw.text(s, self._line(e), "small", C["text2"], x, y0 + 32, w=full)
            y0 += 64
        self.have_now = self.have()
        mixed_list(self, s, rows, fs[self.focus], x, y0, y1, full, self._height, self._paint(s))
        k = r[0]
        hint = {"sug": "Add", "more": "Show", "save": "Save", "name": "Rename"}.get(k, "Select")
        hints = [("dpad", "Change")] if k in ("number", "order") else [("A", hint)]
        if k == "sug" and channels.source_key(self.props[r[1]]["channel"]["source"]) \
                in self.have_now:
            hints = []                               # added already: A does nothing
        self.hintbar(s, hints + [("B", "Back")])     # no LT/RT hint

    @staticmethod
    def _height(r):
        if r[0] == "head":
            return tokens.HEAD_H
        return tokens.ROW_TWO_PITCH if r[0] in ("sug", "src") else tokens.ROW_PITCH

    def _line(self, e):
        line = plural(e["count"], e["what"])
        libs = {x["lib"] for x in self.cat.get(e["source"]["type"], ())}
        return line + (" • " + e["lib"] if len(libs) > 1 and e["lib"] else "")

    def _paint(self, s):
        have = self.have_now

        def paint(r, x, y, w, foc):
            k = r[0]
            if k == "head":
                draw.heading_row(s, x, y, w, r[1])
            elif k == "sug":
                p = self.props[r[1]]
                added = channels.source_key(p["channel"]["source"]) in have
                draw.row_two(s, (x, y, w), p["channel"]["name"],
                             "%s • %s" % (channels.KIND_NAMES[p["kind"]], count_text(p)),
                             right="Added" if added else None, focused=foc, enabled=not added)
            elif k == "more":
                draw.row(s, (x, y, w), "More Suggestions", str(r[1]), focused=foc)
            elif k == "kind":
                draw.row(s, (x, y, w), channels.KIND_ROWS[r[1]], str(len(self.cat[r[1]])),
                         focused=foc)
            elif k == "src":
                e = self.cat[self.kind][r[1]]
                n = have.get(channels.source_key(e["source"]))
                draw.row_two(s, (x, y, w), e["title"], self._line(e),
                             right="Channel %s" % lineup.num(n) if n else None, focused=foc)
            elif k == "save":
                draw.row(s, (x, y, w), "Save Channel", focused=foc)
            elif k == "name":
                draw.row(s, (x, y, w), "Name", self.ch["name"], focused=foc)
            elif k == "number":
                n = self.ch["number"]
                draw.row(s, (x, y, w), "Number", lineup.num(n) if n else "Full", focused=foc)
            elif k == "order":
                draw.row(s, (x, y, w), "Order", channels.ORDER_NAMES[self.ch["order"]],
                         focused=foc)
        return paint


# ---------------------------------------------------------------- Manage Channels

class ManageScreen(Screen):
    """Add a Channel, Show on Home, a file error line when there is one, then every channel in
    number order with its kind and status. A (or Start) on a channel: Rename, Move, Turn Off or On, Delete
    Channel. Move: Up/Down carry the channel, A places it (the channels keep their numbers
    and take them in the new order), B puts it back."""
    name = "LIVETV_MANAGE"
    menu_title = "CHANNELS"

    def __init__(self, app, focus=None):
        super().__init__(app)
        self.fkey = ("ch", focus) if focus else ("add",)
        self.focus, self.ptop = 0, 0
        self.moving = None                    # {"orig": [ids], "cid": id}; app.open_menu waits
        self.order = None                     # the ids in Move order
        self._v = None
        self.reload()

    def reload(self):
        lu = channels.load()
        if lu.needs_ids and not lu.error:     # every edit finds a channel by its id
            try:
                channels.ensure_ids(lu.stamp)
                lu = channels.load()
            except channels.Refused:
                pass
        self.rules = lu

    def chans(self):
        if self.order is not None:
            by = {c.id: c for c in self.rules.channels}
            return [by[i] for i in self.order if i in by]
        return list(self.rules.channels)

    def rows(self):
        out = [("add",), ("home",)]                # "Show on Home": On Right Now, per profile
        err = file_error(self.rules)
        if err:
            out.append(("err", err))
        cs = self.chans()
        if cs:
            out.append(("head", plural(len(cs), "channels")))
            out += [("ch", c, k) for k, c in enumerate(cs)]
            if self.moving is None:
                out.append(("over",))
        return out

    @staticmethod
    def key(r):
        return ("ch", r[1].id) if r[0] == "ch" else (r[0],)

    def focused(self):
        rows = self.rows()
        fs = [k for k, r in enumerate(rows) if r[0] in ("add", "home", "ch", "over")]
        k = next((i for i, f in enumerate(fs) if self.key(rows[f]) == self.fkey), None)
        self.focus = k if k is not None else max(0, min(self.focus, len(fs) - 1))
        r = rows[fs[self.focus]]
        self.fkey = self.key(r)
        return rows, fs, r

    def _set_focus(self, i):
        rows, fs, _r = self.focused()
        self.focus = max(0, min(len(fs) - 1, i))
        self.fkey = self.key(rows[fs[self.focus]])

    # -- input
    def handle(self, a):
        if self.moving is not None:
            if a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
                self.move_step({inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -PAGE,
                                inputs.RT: PAGE}[a])
            elif a == inputs.OK:
                self.move_end(True)
            elif a == inputs.BACK:
                self.move_end(False)
            return
        if a == inputs.BACK:
            self.app.pop()
        elif a == inputs.QUIT:
            self.app.quit()
        elif a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
            _rows, _fs, _r = self.focused()
            self._set_focus(self.focus + {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -PAGE,
                                          inputs.RT: PAGE}[a])
        elif a == inputs.OK:
            _rows, _fs, r = self.focused()
            if r[0] == "add":
                self.app.push(AddScreen(self.app, done=self._added))
            elif r[0] == "home":
                self.toggle_home()
            elif r[0] == "over":
                self.ask_start_over()
            else:
                c = r[1]
                self.app.open_menu(self.channel_rows(c), title="%s %s" % (lineup.num(c.number),
                                                                           c.name), settings=False)

    def menu_items(self):
        _rows, _fs, r = self.focused()
        return self.channel_rows(r[1]) if r[0] == "ch" else []

    def channel_rows(self, c):
        rows = [("Rename", lambda: rename(self.app, c.name, lambda n: self.set_name(c.id, n)))]
        if len(self.rules.channels) > 1:
            rows.append(("Move", lambda: self.move_start(c.id)))
        if not c.file_only:
            rows.append(("Schedule", lambda: self.app.push(ScheduleScreen(self.app, c.id))))
        rows.append(("Turn On" if c.off else "Turn Off", lambda: self.set_off(c.id, not c.off)))
        rows.append(("Delete Channel", lambda: self.ask_delete(c)))
        return rows

    # -- edits
    def toggle_home(self):
        st = self.app.settings
        st.set_live_home(not st.live_home())
        try:
            self.app.saved(st.save())
        except OSError:
            pass

    def _added(self, cid):
        self.reload()
        if cid:
            self.fkey = ("ch", cid)

    def set_name(self, cid, name):
        if name:
            edit(self, lambda st: channels.save_channel({"id": cid, "name": name}, st))

    def set_off(self, cid, off):
        edit(self, lambda st: channels.save_channel({"id": cid, "off": True if off else None}, st))

    def ask_delete(self, c):
        from .system import ConfirmDialog
        self.app.overlay(ConfirmDialog(self.app, self.app.top, "Delete “%s”?" % c.name,
                                       "Delete", lambda: self.delete(c.id)))

    def delete(self, cid):
        ok, _ = edit(self, lambda st: channels.delete_channel(cid, st))
        if ok:
            self.fkey = None                     # the row at the same place takes the focus

    def ask_start_over(self):
        from .system import ConfirmDialog
        self.app.overlay(ConfirmDialog(self.app, self.app.top,
                                       "Delete all channels and start over?", "Start Over",
                                       self.start_over))

    def start_over(self):
        """One edit empties the file, then Make Channels for Me scans again."""
        ok, _ = edit(self, channels.clear)
        if ok:
            self.fkey = ("add",)
            scr = SetupScreen(self.app)
            self.app.push(scr)
            scr._scan()

    def move_start(self, cid):
        self.order = [c.id for c in self.rules.channels]
        self.moving = {"orig": list(self.order), "cid": cid}
        self.fkey = ("ch", cid)

    def move_step(self, d):
        cid = self.moving["cid"]
        i = self.order.index(cid)
        j = max(0, min(len(self.order) - 1, i + d))
        self.order.insert(j, self.order.pop(i))

    def move_end(self, keep):
        orig, order = self.moving["orig"], self.order
        self.moving = self.order = None
        if not keep or order == orig:
            return
        nums = channels.reorder(self.rules.channels, order)
        if nums:
            edit(self, lambda st: channels.set_numbers(nums, st))

    def on_resume(self, result=None):
        self.reload()

    # -- frame
    def update(self):
        v = getattr(self.app.lineup, "version", None)
        d, self._v = v != self._v, v
        return d or self.late_due()

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Channels", page=True)
        x, full, y0, y1 = listlayout.area(self)
        rows, fs, r = self.focused()
        nums = sorted(c.number for c in self.rules.channels)
        status = self.app.lineup.status

        def height(r):
            return {"head": tokens.HEAD_H, "err": 36, "ch": tokens.ROW_TWO_PITCH}.get(
                r[0], tokens.ROW_PITCH)

        def paint(r, x, y, w, foc):
            if r[0] == "add":
                draw.row(s, (x, y, w), "Add a Channel", focused=foc)
            elif r[0] == "home":
                draw.row(s, (x, y, w), "Show on Home",
                         "On" if self.app.settings.live_home() else "Off", focused=foc)
            elif r[0] == "over":
                draw.row(s, (x, y, w), "Start Over", focused=foc)
            elif r[0] == "err":
                draw.error_line(s, r[1], x + tokens.INSET, y + 6, w - 2 * tokens.INSET)
            elif r[0] == "head":
                draw.heading_row(s, x, y, w, r[1])
            else:
                c, k = r[1], r[2]
                n = nums[k] if self.moving is not None else c.number
                mv = self.moving is not None and c.id == self.moving["cid"]
                draw.row_two(s, (x, y, w), "%s  %s" % (lineup.num(n), c.name),
                             "%s • %s" % (kind_name(c), status_text(c, status)),
                             focused=foc and self.moving is None, moving=mv,
                             enabled=not c.off)
        mixed_list(self, s, rows, fs[self.focus], x, y0, y1, full, height, paint)
        if self.moving is not None:
            hints = [("updown", "Move"), ("A", "Place"), ("B", "Cancel")]
        else:
            hints = [("A", "Edit" if r[0] == "ch" else "Select"), ("B", "Back")]
        self.hintbar(s, hints)


# ---------------------------------------------------------------- Schedule (time blocks)

def src_title(src, cat):
    """The title of a source: from the scan catalog, else from the file."""
    key = channels.source_key(src)
    for es in (cat or {}).values():
        for e in es:
            if channels.source_key(e["source"]) == key:
                return e["title"]
    return src.get("title") or src.get("genre") or src.get("network") or \
        channels.KIND_NAMES.get(src.get("type"), "")


def _channel(rules, cid):
    return next((c for c in rules.channels if c.id == cid), None)


class ScheduleScreen(Screen):
    """The time blocks of one channel in start order, then Add a Time Block. A block plays
    from its start until the next start. A channel with no blocks plays its source all day."""
    name = "LIVETV_SCHEDULE"

    def __init__(self, app, cid):
        super().__init__(app)
        self.cid = cid
        self.focus, self.ptop = 0, 0
        self.cat = {}
        self.reload()
        scan(self, self._scanned, lambda _e: None)

    def _scanned(self, res):
        self.cat = catalog(res)

    def reload(self):
        self.rules = channels.load()
        self.ch = _channel(self.rules, self.cid)

    def rows(self):
        out = [("add",)]
        if self.ch is None:
            return out
        if not self.ch.blocks:
            return out + [("head", "All Day"), ("all",)]
        order = sorted(range(len(self.ch.blocks)),
                       key=lambda k: (self.ch.blocks[k]["start"], k))
        return out + [("head", plural(len(order), "time blocks"))] + [("blk", k) for k in order]

    def focused(self):
        rows = self.rows()
        fs = [k for k, r in enumerate(rows) if r[0] in ("add", "blk")]
        self.focus = max(0, min(self.focus, len(fs) - 1))
        return rows, fs, rows[fs[self.focus]]

    def handle(self, a):
        if a == inputs.QUIT:
            self.app.quit()
        elif a == inputs.BACK:
            self.app.pop()
        elif a in (inputs.UP, inputs.DOWN, inputs.LT, inputs.RT):
            self.focused()
            self.focus += {inputs.UP: -1, inputs.DOWN: 1, inputs.LT: -PAGE, inputs.RT: PAGE}[a]
            self.focused()
        elif a == inputs.OK and self.ch is not None:
            _rows, _fs, r = self.focused()
            if r[0] == "add":
                self.add()
            else:
                b = self.ch.blocks[r[1]]
                self.app.push(BlockScreen(self.app, self, r[1], dict(b), self.cat))

    def add(self):
        if len(self.ch.blocks) >= channels.WIZARD_BLOCKS:
            self.app.toast("A channel has %d time blocks at most" % channels.WIZARD_BLOCKS)
            return
        self.app.push(AddScreen(self.app, pick=self.picked))

    def picked(self, e):
        """The source of a new block: its summary opens with smart defaults (Save is one
        press): the first free of 20:00, 06:00, 12:00, …, every day, the default order."""
        start = channels.free_start(self.ch.blocks, None)
        if start is None:
            self.app.toast("Every start time is in use")
            return
        order = channels.order_choices(e["source"]["type"],
                                       channels.source_lib_type(e["source"], e.get("lib_type")))[0]
        b = {"start": start, "days": None, "source": dict(e["source"]), "order": order}
        self.app.push(BlockScreen(self.app, self, None, b, self.cat, entry=e))

    def on_resume(self, result=None):
        self.reload()

    def update(self):
        return self.late_due()

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "Schedule", page=True)
        x, full, y0, y1 = listlayout.area(self)
        if self.ch is None:
            draw.state(s, x, y0, full, "Channel not found")
            self.hintbar(s, [("B", "Back")])
            return
        draw.text(s, "%s  %s" % (lineup.num(self.ch.number), self.ch.name), "heading", C["text"],
                  x, y0, w=full)
        y0 += 40
        rows, fs, r = self.focused()

        def height(r):
            return {"head": tokens.HEAD_H, "add": tokens.ROW_PITCH}.get(r[0], tokens.ROW_TWO_PITCH)

        def paint(r, x, y, w, foc):
            if r[0] == "add":
                draw.row(s, (x, y, w), "Add a Time Block", focused=foc)
            elif r[0] == "head":
                draw.heading_row(s, x, y, w, r[1])
            elif r[0] == "all":
                src = self.ch.source
                draw.row_two(s, (x, y, w), src_title(src, self.cat),
                             channels.KIND_NAMES.get(src["type"], ""), focused=False)
            else:
                b = self.ch.blocks[r[1]]
                draw.row_two(s, (x, y, w), "%s  %s" % (channels.hhmm(b["start"]),
                                                       src_title(b["source"], self.cat)),
                             "%s • %s" % (channels.days_name(b["days"]),
                                          channels.KIND_NAMES.get(b["source"]["type"], "")),
                             focused=foc)
        mixed_list(self, s, rows, fs[self.focus], x, y0, y1, full, height, paint)
        self.hintbar(s, [("A", "Edit" if r[0] == "blk" else "Select"), ("B", "Back")])


class BlockScreen(Screen):
    ok_on_press = True            # menu is Save here, OK acts at once
    """One time block: Starts, Days, What Plays, the order when the source has two, Plays for a
    movie source (one movie, or movies until the next block), and Delete Block for a saved one,
    above the fixed Save Block bar (focus; Start saves from any row). Left/Right change Starts
    (30 min steps, past the starts of other blocks on the same days), Days, the order and
    Plays."""
    name = "LIVETV_BLOCK"

    def __init__(self, app, sched, index, block, cat, entry=None):
        super().__init__(app)
        self.sched, self.cid, self.index, self.b, self.cat = sched, sched.cid, index, block, cat
        self.lib_type = entry.get("lib_type") if entry else self._lib_type(block["source"])
        self.rules = sched.rules
        self.focus, self.ptop = len(self.rows()), 0          # the Save bar
        self.b0 = block_state(block)                         # what B compares against

    def _lib_type(self, src):
        if src.get("type") == "theme":
            return channels.source_lib_type(src)
        key = channels.source_key(src)
        for es in self.cat.values():
            for e in es:
                if channels.source_key(e["source"]) == key:
                    return e.get("lib_type")
        return None

    def reload(self):
        self.rules = channels.load()
        self.sched.reload()

    def blocks(self):
        ch = _channel(self.rules, self.cid)
        return ch.blocks if ch is not None else []

    def orders(self):
        return channels.order_choices(self.b["source"]["type"], self.lib_type)

    def rows(self):
        out = [("start",), ("days",), ("what",)]
        if len(self.orders()) > 1:
            out.append(("order",))
        if self.lib_type == "movie":
            out.append(("once",))
        if self.index is not None:
            out.append(("delete",))
        return out

    def handle(self, a):
        if a == inputs.QUIT:
            self.app.quit()
            return
        if a == inputs.BACK:
            if self.dirty():
                ask_discard(self.app, self.app.pop)
            else:
                self.app.pop()
            return
        rows = self.rows()
        self.focus = max(0, min(self.focus, len(rows)))
        k = rows[self.focus][0] if self.focus < len(rows) else "save"
        if a in (inputs.UP, inputs.DOWN):
            self.focus = bar_step(self.focus, len(rows), a)
        elif a == inputs.MENU:
            self.save()
        elif a in (inputs.LEFT, inputs.RIGHT):
            self.adjust(k, -1 if a == inputs.LEFT else 1)
        elif a == inputs.OK:
            if k == "save":
                self.save()
            elif k == "what":
                self.app.push(AddScreen(self.app, pick=self.set_source))
            elif k == "delete":
                from .system import ConfirmDialog
                self.app.overlay(ConfirmDialog(self.app, self.app.top, "Delete this time block?",
                                               "Delete", self.delete))
            else:
                self.adjust(k, 1)

    def dirty(self):
        return block_state(self.b) != self.b0

    def adjust(self, k, d):
        b, bl = self.b, self.blocks()
        if k == "start":
            m = channels.free_start(bl, b["days"], skip=self.index, start=b["start"], d=d)
            if m is not None:
                b["start"] = m
        elif k == "days":
            cur = tuple(x for x in channels.schedule.DAYS if x in b["days"]) if b["days"] else None
            ch = channels.DAY_CHOICES
            i = ch.index(cur) if cur in ch else (-1 if d > 0 else 0)
            b["days"] = ch[(i + d) % len(ch)]
            used = [x["start"] for j, x in enumerate(bl) if j != self.index
                    and channels.blocks_clash(x["days"], b["days"])]
            if b["start"] in used:              # the new days clash: the next free start
                m = channels.free_start(bl, b["days"], skip=self.index, start=b["start"], d=1)
                if m is not None:
                    b["start"] = m
        elif k == "order":
            o = self.orders()
            b["order"] = o[(o.index(b["order"]) + d) % len(o)] if b.get("order") in o else o[0]
        elif k == "once":
            b["once"] = not b.get("once")

    def set_source(self, e):
        if channels.source_key(e["source"]) != channels.source_key(self.b["source"]):
            self.b["source"] = dict(e["source"])
            self.lib_type = channels.source_lib_type(e["source"], e.get("lib_type"))
            self.b["order"] = self.orders()[0]
            if self.lib_type != "movie":
                self.b.pop("once", None)

    def save(self):
        ok, _ = edit(self, lambda st: channels.save_block(self.cid, self.index, self.b, st))
        if ok:
            self.app.pop()

    def delete(self):
        ok, _ = edit(self, lambda st: channels.delete_block(self.cid, self.index, st))
        if ok:
            self.app.pop()

    def update(self):
        return self.late_due()

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, "New Time Block" if self.index is None else "Time Block", page=True)
        x, full, y0, y1 = listlayout.area(self)
        b = self.b
        draw.text(s, src_title(b["source"], self.cat), "heading", C["text"], x, y0, w=full)
        ch = _channel(self.rules, self.cid)
        line = "%s  %s" % (lineup.num(ch.number), ch.name) if ch is not None else ""
        if ch is not None and not ch.blocks:
            line = "At other times: %s" % src_title(ch.source, self.cat)
        draw.text(s, line, "small", C["text2"], x, y0 + 32, w=full)
        y0 += 64
        rows = self.rows()
        self.focus = max(0, min(self.focus, len(rows)))
        bar = self.focus == len(rows)

        def paint(r, x, y, w, foc):
            k = r[0]
            if k == "start":
                draw.row(s, (x, y, w), "Starts", channels.hhmm(b["start"]), focused=foc)
            elif k == "days":
                draw.row(s, (x, y, w), "Days", channels.days_name(b["days"]), focused=foc)
            elif k == "what":
                draw.row(s, (x, y, w), "What Plays",
                         channels.KIND_NAMES.get(b["source"]["type"], ""), focused=foc)
            elif k == "order":
                draw.row(s, (x, y, w), "Order", channels.ORDER_NAMES.get(b.get("order"), ""),
                         focused=foc)
            elif k == "once":
                draw.row(s, (x, y, w), "Plays", "One Movie" if b.get("once") else "Until Next Block",
                         focused=foc)
            elif k == "delete":
                draw.row(s, (x, y, w), "Delete Block", focused=foc)
        lb, _by = bar_area(y1)
        lw = mixed_list(self, s, rows, None if bar else self.focus, x, y0, lb, full,
                        lambda r: tokens.ROW_PITCH, paint)
        draw_bar(s, x, lb, lw, "Save Block", bar, scrolls=lw != full)
        k = "save" if bar else rows[self.focus][0]
        hint = {"save": "Save", "what": "Change", "delete": "Delete"}.get(k)
        self.hintbar(s, bar_hints([("A", hint)] if hint else [("dpad", "Change")]))
