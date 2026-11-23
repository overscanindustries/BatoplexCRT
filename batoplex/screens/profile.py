"""Plex Home: the profile picker ("Who's Watching?") and the PIN pad.

The picker opens at launch when the account has more than one Home user and Automatically
Sign In is off, and from Settings > User. A user with a PIN gets the PIN pad; the fourth
digit sends it. The switch itself is App.switch_profile.
"""
import pygame

from .. import avatars, draw, inputs, tokens
from ..tokens import C
from . import Screen

TILE, PITCH = 112, 128
PAD_W = 3 * 80 - 8                      # the PIN pad: three 72 px keys on an 80 px pitch
KEYS = ("1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "del")
DIGITS = {"d%d" % n: str(n) for n in range(10)}


class ProfileScreen(Screen):
    ambient = True                # the idle ribbon may show here
    name = "PROFILES"

    def __init__(self, app, at_launch=False):
        super().__init__(app)
        self.at_launch = at_launch
        self.users = None
        self.focus = 0
        self.first = 0
        self.error = None
        self.busy = False                 # a switch is under way
        self.avatars = {}                 # thumb URL -> round Surface, None (failed), or ... (busy)
        self.dirty = True
        self.load()

    def load_avatars(self):
        """Each user's plex.tv avatar, once; a user with none (or a failed fetch) keeps the
        initial."""
        for u in self.users or []:
            url = u.get("thumb")
            if not url or not str(url).startswith(("http://", "https://")) or url in self.avatars:
                continue
            self.avatars[url] = ...

            def done(data, url=url):
                self.avatars[url] = avatars.round_image(data, TILE)
                self.dirty = True

            def fail(e, url=url):
                avatars.L.info("avatar failed: %s", e)
                self.avatars[url] = None
            self.run_bg(lambda url=url: avatars.fetch(url), done, fail)

    def load(self):
        from .. import account
        if getattr(self, "loading", False):
            return
        self.loading = True
        cid, tok = self.app.secrets.get("client_id"), self.app.secrets.get("user_token")
        self.error = None

        def gone():
            self.loading = False
            return self not in self.app.stack        # Back pressed, or the stack replaced

        def done(users):
            if gone():
                return
            self.users = users
            self.load_avatars()
            self.app.secrets.set(home_users_n=len(users))
            cur = (self.app.secrets.get("profile") or {}).get("uuid")
            self.focus = next((i for i, u in enumerate(users)
                               if (u.get("uuid") or str(u.get("id") or "")) == cur), 0)
            self.dirty = True

        def fail(e):
            from .. import plex
            if gone():
                return
            if isinstance(e, plex.AuthError):
                self.app.sign_out()           # the account token is no longer valid
                return
            # plex.tv is not reachable: offer the profile stored on this box.
            # No profile stored means the owner's servers. A PIN can't be checked offline, so
            # a profile with a PIN gets no offline entry.
            prof = self.app.secrets.get("profile") or {"title": "", "admin": True}
            ok = self.at_launch and not prof.get("protected") \
                and bool(self.app.secrets.get("server_token"))
            self.users = [dict(prof, stored=True)] if ok else []
            self.load_avatars()                       # the disk copy, when there is one
            self.error = "Can't reach plex.tv. Press A to try again." if not self.users else None
            self.dirty = True
        self.run_bg(lambda: account.home_users(cid, tok), done, fail)

    def choose(self, user, pin=None):
        self.busy, self.dirty = True, True
        self.app.switch_profile(user, pin, self)

    def switch_done(self):
        self.busy, self.dirty = False, True

    def switch_failed(self, kind, msg):
        self.busy, self.dirty = False, True
        top = self.app.top
        if isinstance(top, PinScreen):
            top.fail(msg)                     # the pad clears and shows why
        else:
            self.error = msg

    # ---------------------------------------------------------------- input
    def handle(self, a):
        self.dirty = True
        if self.busy:
            return
        if a == inputs.BACK:
            if self.at_launch:
                self.app.confirm_exit()
            else:
                self.app.pop()
            return
        if a == inputs.QUIT:
            self.app.quit()
            return
        if not self.users:
            if a == inputs.OK:
                self.load()
            return
        if a in (inputs.LEFT, inputs.RIGHT):
            self.focus = max(0, min(len(self.users) - 1, self.focus + (1 if a == inputs.RIGHT else -1)))
        elif a == inputs.OK:
            u = self.users[self.focus]
            if u.get("stored"):
                self.app.enter_stored()              # offline: the profile kept on this box
            elif u.get("protected"):
                self.app.push(PinScreen(self.app, self, u))
            else:
                self.choose(u)

    def update(self):
        d, self.dirty = self.dirty, False
        return d or self.late_due()

    # ---------------------------------------------------------------- draw
    def draw(self, s):
        sf = self.safe
        x0, x1, T = sf["left"], 640 - sf["right"], sf["top"]
        draw.background(s, self)
        self.topbar(s, "Plex Home")
        draw.text(s, "Who's Watching?", "title", C["text"], x0, T + 44)
        if self.users is None:
            if self.late(True):
                draw.text(s, "Loading…", "body", C["text2"], x0, T + 132)
            return
        self.late(False)
        if self.error and not self.users:
            draw.error_line(s, self.error, x0, T + 132, x1 - x0, style="body")
            return
        n_view = max(1, (x1 - x0 + PITCH - TILE) // PITCH)
        if self.focus < self.first:
            self.first = self.focus
        elif self.focus >= self.first + n_view:
            self.first = self.focus - n_view + 1
        y = T + 132
        for k in range(self.first, min(len(self.users), self.first + n_view)):
            u = self.users[k]
            x = x0 + (k - self.first) * PITCH
            foc = k == self.focus
            # Round, as stock Plex's user picker: the avatar, else the initial on a disc.
            c = (x + TILE // 2, y + TILE // 2)
            img = self.avatars.get(u.get("thumb"))
            if isinstance(img, pygame.Surface):
                s.blit(img, (x, y))
            else:
                pygame.draw.circle(s, C["raised"], c, TILE // 2)
                initial = (u.get("title") or "?")[:1].upper()
                draw.text(s, initial, "display", C["text"], x, y + (TILE - 52) // 2, w=TILE,
                          align="center")
            if foc:
                draw.focus_ring(s, c, TILE // 2)
            name = u.get("title") or "User"
            if u.get("stored"):
                name = "Continue as " + name if u.get("title") else "Continue"
            draw.text(s, name, "label", C["text"] if foc else C["text2"], x, y + TILE + 16,
                      w=TILE, align="center")
            if u.get("protected"):
                draw.text(s, "PIN", "tag", C["text2"], x, y + TILE + 44, w=TILE, align="center")
        if self.first + n_view < len(self.users):   # more users to the right
            draw.triangle_right(s, C["text2"], x1 - tokens.CUE // 2 - 2, y + TILE // 2 - 6,
                                size=tokens.CUE)
        if self.busy:
            draw.text(s, "Switching…", "small", C["text2"], x0, T + 92)
        elif self.error:
            draw.error_line(s, self.error, x0, T + 92, x1 - x0)


class PinScreen(Screen):
    ok_on_press = True            # a key grid, OK at once
    ambient = True                # the idle ribbon may show here
    name = "PIN"
    takes_backspace = True                # Backspace deletes a digit here

    def __init__(self, app, picker, user):
        super().__init__(app)
        self.picker, self.user = picker, user
        self.digits = ""
        self.key = 0                      # on "1"
        self.msg = None
        self.dirty = True

    def fail(self, msg):
        self.digits, self.msg, self.dirty = "", msg, True

    def _type(self, d):
        if self.picker.busy or len(self.digits) >= 4:
            return
        self.digits += d
        self.msg = None
        if len(self.digits) == 4:
            self.picker.choose(self.user, self.digits)

    def _delete(self):
        self.digits = self.digits[:-1]

    def handle(self, a):
        self.dirty = True
        if a == inputs.BACK:
            self.app.pop()
        elif a == inputs.QUIT:
            self.app.quit()
        elif a in DIGITS:
            self._type(DIGITS[a])
        elif a in (inputs.X, inputs.BACKSPACE):
            self._delete()
        elif a in (inputs.LEFT, inputs.RIGHT):
            col = self.key % 3 + (1 if a == inputs.RIGHT else -1)
            if 0 <= col < 3:
                self.key += 1 if a == inputs.RIGHT else -1
        elif a in (inputs.UP, inputs.DOWN):
            k = self.key + (3 if a == inputs.DOWN else -3)
            if 0 <= k < len(KEYS):
                self.key = k
        elif a == inputs.OK:
            k = KEYS[self.key]
            if k == "del":
                self._delete()
            elif k:
                self._type(k)

    def update(self):
        d, self.dirty = self.dirty, False
        return d or self.picker.busy

    def draw(self, s):
        sf = self.safe
        x0, x1, T = sf["left"], 640 - sf["right"], sf["top"]
        draw.background(s, self)
        self.topbar(s, "Plex Home")
        draw.text(s, self.user.get("title") or "User", "title", C["text"], x0, T + 44,
                  w=x1 - PAD_W - 24 - x0)
        draw.text(s, "Enter PIN", "body", C["text2"], x0, T + 92)
        for i in range(4):
            r = (x0 + i * 60, T + 140, 48, 56)
            draw.rect(s, C["raised"], r)
            if i < len(self.digits):
                draw.rect(s, C["text"], (r[0] + 16, r[1] + 20, 16, 16))
        if self.picker.busy:
            draw.text(s, "Checking…", "small", C["text2"], x0, T + 208)
        elif self.msg:
            draw.error_line(s, self.msg, x0, T + 208, x1 - PAD_W - 24 - x0)
        # PIN keys 72 x 40 on an 80 x 44 pitch
        kx, ky = x1 - PAD_W, T + 44
        for i, k in enumerate(KEYS):
            if not k:
                continue
            r = (kx + (i % 3) * 80, ky + (i // 3) * 44, 72, tokens.CTRL_H)
            draw.button(s, r, "Delete" if k == "del" else k, focused=i == self.key)
