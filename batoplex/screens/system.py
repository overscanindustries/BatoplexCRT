"""Sign-in, settings, screen-margin calibration, exit confirmation, playback overlay."""
import threading
import time

import pygame

from .. import boot, draw, inputs, log, plex, saveropts, tokens
from ..tokens import C
from . import Overlay, Screen, listlayout

L = log.get("screen")


class SignInScreen(Screen):
    ambient = True                # The idle ribbon may show here
    no_saver = True
    name = "SIGN IN"

    def __init__(self, app, token=None):
        super().__init__(app)
        self.code = None
        self.status = "Getting a code…"
        self.error = None
        self.stop = token is not None
        self.token = token           # set once plex.tv gives a token
        self.focus = 0
        if not self.stop:
            threading.Thread(target=self._flow, daemon=True).start()

    def _flow(self):
        cid = self.app.secrets.get("client_id")
        while not self.stop:
            try:
                pin = plex.PinLogin(cid)
                self.code = pin.start()
                self.status = "Waiting for you to enter the code"
                self.error = None
                start = time.monotonic()
                while not self.stop and time.monotonic() - start < 600:
                    tok = pin.poll()
                    if tok:
                        self.stop = True
                        self.status = "Signed in. Finding your server…"
                        self.app.post(lambda t=tok: self.app.finish_signin(t, self))
                        return
                    time.sleep(2)
            except Exception as e:
                L.info("plex.tv sign-in failed: %s", e)    # the log only
                self.error = "Can't reach plex.tv. Retrying…"
                time.sleep(5)

    def handle(self, a):
        if a == inputs.OK and self.token:
            self.app.finish_signin(self.token, self)
        elif a == inputs.BACK:
            self.app.confirm_exit()

    def update(self):
        """The sign-in thread changes these fields without app.post."""
        sig = (self.code, self.status, self.error, self.token)
        d, self._sig = sig != getattr(self, "_sig", None), sig
        return d

    def draw(self, s):
        sf = self.safe
        draw.background(s, self)
        x = sf["left"]
        self.topbar(s, "Batoplex")
        draw.text(s, "Sign in to Plex", "title", C["text"], x, sf["top"] + 44)
        draw.text(s, "On a phone or computer, go to", "body", C["text2"], x, sf["top"] + 100)
        draw.text(s, "plex.tv/link", "title", C["text"], x, sf["top"] + 128)
        draw.text(s, "and enter this code:", "body", C["text2"], x, sf["top"] + 176)
        code = self.code or "••••"
        draw.rect(s, C["raised"], (x, sf["top"] + 212, 300, 112))
        draw.text(s, " ".join(code), "code", C["text"], x, sf["top"] + 218, w=300, align="center")
        if self.error:
            draw.error_line(s, self.error, x, sf["top"] + 336, 560)
        else:
            draw.text(s, self.status, "small", C["text2"], x, sf["top"] + 336, w=560)
        self.hintbar(s, ([("A", "Retry")] if self.token else []) + [("B", "Exit")])


def fit_lines(text, style, w, n):
    """text in at most n lines of width w, all of it shown. When it does not fit, the middle
    goes (an ellipsis): two long names that start the same still differ at their ends."""
    def fits(t):
        lines = draw.wrap(t, style, w, n)
        return (" ".join(lines).split() == t.split()
                and all(draw.width(ln, style) <= w for ln in lines)), lines
    ok, lines = fits(text)
    if ok:
        return lines
    for cut in range(1, len(text)):
        half = (len(text) - cut) // 2
        t = text[:half].rstrip() + "…" + text[len(text) - (len(text) - cut - half):].lstrip()
        ok, lines = fits(t)
        if ok:
            return lines
    return draw.wrap(text, style, w, n)


class ConfirmDialog(Overlay):
    """A question with two buttons, "{Verb}" then "Cancel". The focus starts
    on Cancel (focus=1); the exit dialog starts on Exit, as stock Plex does. The title wraps to
    three lines inside the box; the box grows and stays in the safe area."""
    name = "CONFIRM"
    W = tokens.DIALOG_W

    def __init__(self, app, under, title, yes_label, on_yes, no_label="Cancel", focus=1,
                 on_no=None):
        super().__init__(app, under)
        self.title = title
        self.labels, self.on_yes, self.on_no = (yes_label, no_label), on_yes, on_no
        self.focus = focus

    def handle(self, a):
        """The dialog closes first, then its callback runs. Cancel by button or by B both run
        on_no."""
        if a in (inputs.LEFT, inputs.RIGHT):
            self.focus = 1 - self.focus
        elif a == inputs.BACK:
            self.app.pop()
            if self.on_no:
                self.on_no()
        elif a == inputs.OK:
            self.app.pop()
            if self.focus == 0:
                self.on_yes()
            elif self.on_no:
                self.on_no()
        elif a == inputs.QUIT:
            self.app.quit()

    def geo(self):
        lines = fit_lines(self.title, "heading", self.W - 2 * tokens.DIALOG_PAD, 3)
        x, y, w, h = draw.dialog_geo(self.safe, len(lines))
        return x, y, w, h, lines

    def draw(self, s):
        self.draw_under(s)
        lines = self.geo()[4]
        draw.dialog_box(s, self.safe, lines, self.labels, self.focus)


class ExitDialog(ConfirmDialog):
    name = "EXIT"

    def __init__(self, app, under, on_no=None):
        super().__init__(app, under, "Exit Batoplex?", "Exit", app.quit, focus=0, on_no=on_no)


# Player Experience: Plex names and values (Plex TV apps, "Player" settings).
CHOICES = {
    "skip_intro": [("disabled", "Disabled"), ("manual", "Manually"), ("auto", "Automatically")],
    "skip_credits": [("disabled", "Disabled"), ("manual", "Manually"), ("auto", "Automatically")],
    "auto_play": [(False, "Off"), (True, "On")],
    "up_next": [("credits", "During Final Credits"), ("end", "When Video Finishes")],
    "autoplay_countdown_s": [(0, "Immediate"), (5, "5 Seconds"), (10, "10 Seconds"),
                             (15, "15 Seconds"), (30, "30 Seconds"), (60, "60 Seconds")],
    "passout_protection_h": [(0, "Never Prompt"), (1, "After 1 Hour"), (2, "After 2 Hours"),
                             (3, "After 3 Hours")],
    "rewind_on_resume_s": [(0, "None"), (5, "5 Seconds"), (10, "10 Seconds"), (15, "15 Seconds"),
                           (30, "30 Seconds")],
    "theme_music": [(False, "Off"), (True, "On")],
    "night_audio": [(False, "Off"), (True, "On")],
    "high_contrast": [(False, "Off"), (True, "On")],
    "menu_sounds": [(False, "Off"), (True, "On")],
    "auto_sign_in": [(False, "Off"), (True, "On")],
    "theme_volume": [(25, "Low"), (50, "Medium"), (75, "High")],
    "flicker_filter": [("normal", "Normal"), ("light", "Light"), ("off", "Off")],
    "aspect": [("letterbox", "Letterbox"), ("14:9", "14:9"), ("zoom", "Zoom")],
}


QUALITY_KEYS = ("quality_home", "quality_remote")   # Plex: Home Streaming, Remote Streaming


def choices(key):
    """The values of a setting: (value, label). Video Quality labels follow the screen (576p on
    a PAL screen), so they are made when asked."""
    return plex.quality_choices() if key in QUALITY_KEYS else CHOICES[key]


# The help line under the list for each row, by row key.
# A category row's key is "cat:" + its id. At most 52 characters on a page, 40 on the
# category list (the version text shares that line).
HELP = {
    "cat:experience": "Theme music and automatic sign-in",
    "cat:appearance": "Theme, contrast and screen saver",
    "cat:player": "Intros, credits and what plays next",
    "cat:video": "Quality, display mode and night audio",
    "cat:tvsetup": "Fit and tune the picture on this TV",
    "cat:account": "Your Plex server and account",
    "exit": "Close Batoplex",
    "theme_music": "Play a show's theme music while you browse it",
    "theme_volume": "How loud the theme music plays",
    "auto_sign_in": "Skip Who's Watching? when Batoplex starts",
    "theme": "Change the colors and look of the menus",
    "high_contrast": "Stronger colors that make text easier to read",
    "menu_sounds": "Play sounds as you move through the menus",
    "saver": "Pick what shows when idle, and when it starts",
    "skip_intro": "Show a Skip Intro button, or skip intros for you",
    "skip_credits": "Show a Skip Credits button, or skip credits for you",
    "auto_play": "Play the next episode when this one ends",
    "up_next": "Show Up Next during the credits or after the video",
    "autoplay_countdown_s": "How long Up Next waits before the next one plays",
    "passout_protection_h": "Ask if you are still watching after a few hours",
    "rewind_on_resume_s": "Jump back a little when you resume from pause",
    "quality_home": "Video quality from a server on your home network",
    "quality_remote": "Video quality from a server away from home",
    "aspect": "How widescreen video fits the screen by default",
    "night_audio": "Quiet sounds up, loud sounds down, clearer dialogue",
    "margins": "Move the edges so the TV cuts nothing off",
    "patterns": "Test images to check and adjust the TV's picture",
    "flicker_filter": "Soften fine lines so menus flicker less",
    "boot": "Open Batoplex when the system turns on",
    "user": "Change who is watching",
    "signout": "Sign out of your Plex account on this device",
}

# The category list, in order: (id, title).
CATEGORIES = [("experience", "Experience"), ("appearance", "Appearance"),
              ("player", "Player Experience"), ("video", "Video & Audio"),
              ("tvsetup", "TV Setup"), ("account", "Account")]


def page_rows(app, cat, boot_ok=False, boot_on=False):
    """The rows of one Settings page: (kind, key, label, value). kind: choice | action | boot |
    text (only text: the focus skips it). A row shows only when it applies."""
    st = app.settings

    def ch(key, label):
        val = dict(choices(key)).get(st[key], str(st[key]))
        return ("choice", key, label, val)
    users = app.home_users_n() > 1
    out = []
    if cat == "experience":
        out.append(ch("theme_music", "Theme Music"))
        if st["theme_music"]:
            out.append(ch("theme_volume", "Theme Music Volume"))
        if users:
            out.append(ch("auto_sign_in", "Automatically Sign In"))
    elif cat == "appearance":
        from .. import theme
        out += [("action", "theme", "Theme", theme.cur.name), ch("high_contrast", "High Contrast")]
        if theme.cur.sounds:
            out.append(ch("menu_sounds", "Menu Sounds"))
        out.append(("action", "saver", "Screen Saver",
                    saveropts.label(st["screensaver_type"]) if st["screensaver_min"] else "Off"))
    elif cat == "player":
        out += [ch("skip_intro", "Skip Intro"), ch("skip_credits", "Skip Credits"),
                ch("auto_play", "Auto Play")]
        if st["auto_play"]:
            out += [ch("up_next", "Up Next"),
                    ch("autoplay_countdown_s", "Auto Play Countdown Time"),
                    ch("passout_protection_h", "Passout Protection")]
        out.append(ch("rewind_on_resume_s", "Rewind on Resume"))
    elif cat == "video":
        out += [ch("quality_home", "Home Streaming"), ch("quality_remote", "Remote Streaming")]
        out += [ch("aspect", "Display Mode"), ch("night_audio", "Night Audio")]
    elif cat == "tvsetup":
        out += [("action", "margins", "Screen Margins", draw.CHEVRON),
                ("action", "patterns", "Test Patterns", draw.CHEVRON),
                ch("flicker_filter", "Flicker Filter")]
        if boot_ok:
            out.append(("boot", "boot", "Start on Boot", "On" if boot_on else "Off"))
    elif cat == "account":
        srv = app.secrets.get("server_name") or "None"
        out.append(("text", "server", "Server", srv))
        src = getattr(app, "sources", None)
        if src is not None and src.server_count() > 1:
            others = [src.server_name(k) for k in src.order if k != src.home_id]
            others = [n for n in others if n]
            if others:
                more = " +%d" % (len(others) - 1) if len(others) > 1 else ""
                out.append(("text", "servers", "Other Servers", others[0] + more))
        if users:
            prof = app.secrets.get("profile") or {}
            out.append(("action", "user", "Switch User", prof.get("title") or "Owner"))
        out.append(("action", "signout", "Sign Out", ""))
    return out


class _SettingsList(Screen):
    """A Settings list with the help line of the focused row under it. The
    focus follows the row's key, so rows that come and go leave it on its row."""
    ambient = True                # The idle ribbon may show here
    title = "Settings"

    def __init__(self, app):
        super().__init__(app)
        self.focus = 0
        self.focus_key = None
        self.top = 0

    def rows(self):
        return []

    def items(self):
        return listlayout.items(self.rows())

    def _resolve(self, items):
        if self.focus_key is not None:
            k = next((i for i, r in enumerate(items) if r[1] == self.focus_key), None)
            if k is not None:
                self.focus = k
        self.focus = max(0, min(self.focus, len(items) - 1))
        return items[self.focus] if items else None

    def close(self):
        """B: save and go back one level."""
        self.app.saved(self.app.settings.save())
        self.app.pop()

    def handle(self, a):
        items = self.items()
        r = self._resolve(items)
        if a == inputs.BACK:                     # Menu is not Back
            self.close()
        elif a in (inputs.UP, inputs.DOWN) and items:
            self.focus = max(0, min(len(items) - 1, self.focus + (1 if a == inputs.DOWN else -1)))
            self.focus_key = items[self.focus][1]
            _x, _w, y0, _y1 = listlayout.area(self)
            # The list and the help line under it change.
            self._press = pygame.Rect(0, y0 - 4, 641, 480 - self.safe["bottom"] - y0 + 4)
        elif a in (inputs.OK, inputs.LEFT, inputs.RIGHT) and r is not None:
            self.press(a, r[0], r[1])

    def press(self, a, kind, key):
        pass

    def update(self):
        return False                  # input and worker results redraw in the app

    def help_w(self):
        sf = self.safe
        return 640 - sf["left"] - sf["right"]

    def draw(self, s):
        draw.background(s, self)
        self.topbar(s, self.title)
        rows = self.rows()
        r = self._resolve(listlayout.items(rows))
        listlayout.draw_list(self, s, rows, self.focus)
        if r is not None and not getattr(self, "hide_hints", False):   # not under a dialog
            sf = self.safe
            draw.text(s, HELP.get(r[1], ""), "small", C["text2"], sf["left"],
                      480 - sf["bottom"] - 24, w=self.help_w())


class SettingsScreen(_SettingsList):
    """Settings: the category list. A category opens its page; the version
    shows in the bottom right corner here only."""
    name = "SETTINGS"

    def __init__(self, app):
        super().__init__(app)
        self.boot_ok = boot.available()           # Batocera with the Ports launcher only

    def rows(self):
        out = [("cat", "cat:" + c, title, "") for c, title in CATEGORIES
               if page_rows(self.app, c, self.boot_ok)]       # 1.6: no empty page
        return out + [("action", "exit", "Exit Batoplex", "")]

    def close(self):
        self.app.saved(self.app.settings.save())
        self.app.back()                           # opened from the side panel: back to it

    def press(self, a, kind, key):
        if a != inputs.OK:
            return
        if kind == "cat":
            c = key[len("cat:"):]
            self.app.push(SettingsPage(self.app, c, dict(CATEGORIES)[c], self.boot_ok))
        elif key == "exit":
            self.app.saved(self.app.settings.save())
            self.app.confirm_exit()

    def version(self):
        return "Batoplex %s" % self.app.version

    def help_w(self):
        return super().help_w() - draw.width(self.version(), "small") - 16

    def draw(self, s):
        super().draw(s)
        sf = self.safe
        draw.text(s, self.version(), "small", C["dim"], 640 - sf["right"],
                  480 - sf["bottom"] - 24, align="right")


class SettingsPage(_SettingsList):
    """One Settings category: its rows, the category name in the top bar.
    Values change in the row: Left/Right step (clamped), A steps and wraps; each change
    saves."""
    name = "SETTINGS_PAGE"

    def __init__(self, app, cat, title, boot_ok=False):
        super().__init__(app)
        self.cat, self.title = cat, title
        self.boot_ok = boot_ok
        self.boot_on = boot_ok and boot.enabled()

    def rows(self):
        return page_rows(self.app, self.cat, self.boot_ok, self.boot_on)

    def press(self, a, kind, key):
        st = self.app.settings
        if kind == "choice":
            opts = [v for v, _ in choices(key)]
            if st[key] in opts:
                i = opts.index(st[key])
            elif isinstance(st[key], (int, float)) and not isinstance(st[key], bool):
                i = min(range(len(opts)), key=lambda k: abs(opts[k] - st[key]))   # old value
            else:
                i = 0
            if a == inputs.LEFT:
                i = max(0, i - 1)
            elif a == inputs.RIGHT:
                i = min(len(opts) - 1, i + 1)
            else:
                i = (i + 1) % len(opts)            # A steps through and wraps
            st[key] = opts[i]
            self.app.saved(st.save())
            if key == "high_contrast":
                self.app.apply_theme()
        elif kind == "boot":
            on = {inputs.LEFT: False, inputs.RIGHT: True}.get(a, not self.boot_on)
            if on != self.boot_on and self.app.saved(boot.set_enabled(on)):
                self.boot_on = on
                L.info("start on boot: %s", "on" if on else "off")
        elif a != inputs.OK or kind != "action":
            return
        elif key == "theme":
            from .themes import ThemeScreen
            self.app.push(ThemeScreen(self.app))
        elif key == "margins":
            self.app.push(CalibrateScreen(self.app))
        elif key == "saver":
            from .saverset import SaverScreen
            self.app.push(SaverScreen(self.app))
        elif key == "patterns":
            from .patterns import PatternsScreen
            self.app.push(PatternsScreen(self.app))
        elif key == "user":
            self.app.saved(st.save())
            self.app.open_profiles()
        elif key == "signout":
            # The question lives at this row only; app.sign_out() also runs
            # when the token is refused (no question there).
            self.app.overlay(ConfirmDialog(self.app, self, "Sign out of Plex?", "Sign Out",
                                           self.app.sign_out))


class CalibrateScreen(Screen):
    """Move each edge until its bracket is just visible on the set."""
    no_saver = True
    name = "MARGINS"
    EDGES = ["left", "top", "right", "bottom"]

    menu_title = "SCREEN MARGINS"
    menu_app_rows = False          # A tool menu, no Settings row

    def __init__(self, app):
        super().__init__(app)
        self.edge = 0

    def menu_items(self):
        return [("Reset", self.reset)]

    def reset(self):
        self.app.settings["safe"] = dict(tokens.SAFE)
        self.app.settings["video_margins"] = False

    def handle(self, a):
        safe = self.app.settings["safe"]
        e = self.EDGES[self.edge]
        lo, hi = tokens.SAFE_LIMITS["h"] if e in ("left", "right") else tokens.SAFE_LIMITS["v"]
        step = tokens.SAFE_LIMITS["step"]
        if a in (inputs.OK, inputs.RB):
            self.edge = (self.edge + 1) % 4
        elif a == inputs.LB:
            self.edge = (self.edge - 1) % 4
        elif a == inputs.BACK:
            self.app.saved(self.app.settings.save())
            self.app.pop()
        elif a == inputs.X:
            self.reset()
        elif a in (inputs.UP, inputs.DOWN, inputs.LEFT, inputs.RIGHT):
            inward = {"left": inputs.RIGHT, "right": inputs.LEFT, "top": inputs.DOWN,
                      "bottom": inputs.UP}[e]
            outward = {"left": inputs.LEFT, "right": inputs.RIGHT, "top": inputs.UP,
                       "bottom": inputs.DOWN}[e]
            old = safe[e]
            if a == inward:
                safe[e] = min(hi, safe[e] + step)
            elif a == outward:
                safe[e] = max(lo, safe[e] - step)
            if safe[e] != old:
                self.app.settings["video_margins"] = True   # a measurement of this set

    def update(self):
        return False                  # it changes only on input

    def draw(self, s):
        sf = self.safe
        s.fill(C["bg"])
        l, t, r, b = sf["left"], sf["top"], 640 - sf["right"], 480 - sf["bottom"]
        draw.frame(s, C["track"], (l, t, r - l, b - t), 2)
        L = 40
        cur = self.EDGES[self.edge]
        # ruler: a tick every 8 px outside the active edge, so the user can count what is cut off
        for k in range(1, 6):
            d = 8 * k
            if cur == "left" and l - d >= 0:
                draw.rect(s, C["text2"], (l - d, (t + b) // 2 - 12, 2, 24))
            elif cur == "right" and r + d < 641:
                draw.rect(s, C["text2"], (r + d - 2, (t + b) // 2 - 12, 2, 24))
            elif cur == "top" and t - d >= 0:
                draw.rect(s, C["text2"], ((l + r) // 2 - 12, t - d, 24, 2))
            elif cur == "bottom" and b + d < 480:
                draw.rect(s, C["text2"], ((l + r) // 2 - 12, b + d - 2, 24, 2))
        for edge, pts in (("left", [(l, t, 4, L), (l, b - L, 4, L)]),
                          ("right", [(r - 4, t, 4, L), (r - 4, b - L, 4, L)]),
                          ("top", [(l, t, L, 4), (r - L, t, L, 4)]),
                          ("bottom", [(l, b - 4, L, 4), (r - L, b - 4, L, 4)])):
            col = C["focus"] if edge == cur else C["dim"]
            for p in pts:
                draw.rect(s, col, p)
        cx = l + 24
        draw.text(s, "Screen Margins", "title", C["text"], cx, t + 40)
        draw.paragraph(s, "Move each bright edge until it just shows on the screen. "
                          "Everything inside the brackets stays visible.",
                       "body", C["text2"], cx, t + 92, r - l - 48, 3)
        draw.text(s, "Edge: %s   %d px" % (cur.capitalize(), sf[cur]), "label", C["text"], cx, t + 180)
        # Inside the frame, clear of the bottom brackets (the standard hint row sits on them here).
        if not getattr(self, "hide_hints", False):   # not under its menu
            draw.hints(s, [("dpad", "Move"), ("A", "Next Edge"), ("menu", "Options"),
                           ("B", "Back")], cx, b - 56, r - l - 48)


class PlayingScreen(Screen):
    """Start card while mpv starts (B cancels), and the error card if playback fails."""
    name = "PLAYING"

    def __init__(self, app, item):
        super().__init__(app)
        self.item = item
        self.message = "Starting…"

    def update(self):
        """The playback session moves on its own (starting, error, the next item)."""
        pb = self.app.playback
        it = pb.s.item if pb.s is not None else self.item
        sig = (pb.session, pb.message, self.message, id(it))
        d, self._sig = sig != getattr(self, "_sig", None), sig
        return d

    def draw(self, s):
        s.fill((0, 0, 0))
        sf = self.safe
        pb = self.app.playback
        err = pb.session == "error"
        it = pb.s.item if pb.s is not None else self.item     # a playlist can move on
        w = 640 - sf["right"] - sf["left"]
        draw.text(s, it.hero_title, "heading", C["text"], sf["left"], 200, w=w)
        if err:      # the cause is in the log (playback error: ...), not on screen (2.8)
            draw.error_line(s, pb.message or "Can't play this item.", sf["left"], 236, w,
                            style="body")
        else:
            draw.text(s, self.message, "body", C["text2"], sf["left"], 236, w=w)
        if err:
            self.hintbar(s, [("A", "OK")])
        elif pb.session == "starting":
            self.hintbar(s, [("B", "Cancel")])
