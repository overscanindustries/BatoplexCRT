"""Batoplex main loop.

Display: on the CRT the X screen is already 641x480 interlaced. The app opens a borderless
641x480 window at 0,0, so no mode change happens. On other screens it opens scaled full
screen (--window: a 641x480 window; an unknown desktop size: a window too). Every UI frame goes through the vertical flicker filter;
video (mpv) never does.
"""
import collections
import os
import threading
import time

from . import config, lineup, log
from . import anim
from .tokens import MODE_W, MODE_H, FPS, BASE, FAST, OVERLAY_FADE, TOAST_IN, TOAST_HOLD, TOAST_OUT
from .tokens import OK_HOLD_MS
from .inputs import HOLD, OK_DOWN, OK_UP

LOW_LINES = 288                 # a desktop this tall or less: flicker filter Off by default
FRAME_FAILS = 3                 # faults in a row on one screen before it closes
FRAME_FATAL = 30                # faults in a row (any screen) before the app exits ...
FRAME_FATAL_S = 10.0            # ... and only when the run has lasted this long
FAIL_LOG_GAP = 5.0              # s between short log lines in a run of frame faults
SAVE_FAIL_GAP = 5.0             # s between two save-failed toasts
MEDIA_KEYS = ("playpause", "play", "pause", "next", "prev", "stop")
MENU_SOUNDS = {"up": "move", "down": "move", "left": "move", "right": "move", "ok": "select",
               "back": "back"}

L = log.get("app")


def band_union(parts):
    """The band of a frame: the union of its parts; None (a full pass) when there is no part
    or a part is None."""
    import pygame
    if not parts or any(p is None for p in parts):
        return None
    return pygame.Rect(parts[0]).unionall([pygame.Rect(p) for p in parts[1:]])


def _sdl_env():
    os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
    os.environ.setdefault("SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS", "0")
    os.environ.setdefault("SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR", "0")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")


def load_sections(p, name, tries=3):
    """Load a server's sections on a worker thread, with retries: without them its pins show
    as unavailable. A failure is logged; the server still joins."""
    from . import plex
    for n in range(tries):
        try:
            p.load_sections()
            return True
        except plex.AuthError:
            raise                                    # not a network problem: no retry
        except Exception as e:
            L.info("libraries of %s failed (%d/%d): %s", name, n + 1, tries, e)
            if n + 1 < tries:
                time.sleep(2.0)
    return False


class App:
    version = config.VERSION
    _okp = None             # A pending OK (time, screen and focus, flushes)
    rep = False             # the action in dispatch is a repeat (the panel wraps on a new one)
    _holding = False        # open_options runs for a hold of OK (no app-rows-only menu)

    def __init__(self, args):
        _sdl_env()
        import pygame
        self.pg = pygame
        self.args = args
        self.settings = config.Settings()
        self.secrets = config.Secrets()
        self.settings.on_profile = self.apply_theme    # Each profile its theme
        self.apply_theme()
        from . import mpv
        mpv.probe_async(self.settings["mpv"])   # runs while the display opens
        pygame.display.init()
        pygame.font.init()
        self._open_display()
        pygame.mouse.set_visible(False)
        from . import draw, inputs, playback, tracks
        self.inputs = inputs.Input()
        self.canvas = draw.opaque_surface((MODE_W, MODE_H))   # no alpha: see opaque_surface
        self.filter = draw.FlickerFilter((MODE_W, MODE_H))
        self.playback = playback.PlaybackController(
            self.settings, memory=tracks.Memory(os.path.join(config.data_dir(), "track_prefs.json")))
        self.playback.is_held = self.inputs.is_held
        self.playback.guide_view = self._picture_guide
        self.playback.image = self._playback_image
        # A fixed queue's items go to their own server; None: not here.
        self.playback.provider_for = lambda it: (self.sources.strict(it.server_id)
                                                 if self.sources is not None else None)
        from . import audio
        # A scripted test passes its audio output (--ao=null) to the audio mpv too.
        aud = [a for a in self.settings["mpv_extra"] if a.startswith("--ao")] if args.script else []
        self.audio = audio.AudioPlayer(self.settings["mpv"], extra=aud)
        from .music import MusicController
        self.src_gen = 0              # the source generation: late server results are dropped
        self.music = MusicController(self.audio, post=self.post, toast=self.toast)
        from .sleep import SleepTimer
        self.sleep = SleepTimer()
        self.sleep.owner_fn = self._play_key
        self.playback.sleep = self.sleep
        self.music.sleep_end = lambda: self.sleep.at_end
        self.music.stamp = lambda: self.src_gen
        self.music.on_played = self.music_played
        from .visualizer import Visualizer
        self.visualizer = Visualizer(self.settings)
        self._music_v = -1
        self._theme_want = self._theme_played = None
        self._theme_since = 0.0
        self._session = playback.IDLE
        self.stack = []
        self.posted = collections.deque()
        self.running = True
        self.base = None              # the filtered frame of the top screen
        self.pre = None               # the last composite before the toast layer
        self.last_out = None          # the last frame on screen (with the toast)
        self.trans = None             # the screen transition that runs
        self.comp = None              # the transition composite
        self.toast_out = None         # the frame with the toast layer
        self._toast = None            # the toast layer (toast(), _toast_alpha())
        self._sig = None              # (top, stack size, saver) of the last frame
        self._pband = None            # the band of this frame's presses and worker results
        self._stats = [0, 0]          # drawn fields, fields (the idle check reads the log)
        anim.fixed = bool(args.script)                    # scripted runs: a fixed clock
        self.dirty_check = os.environ.get("BATOPLEX_DIRTY_CHECK") == "1"
        self.sources = None
        self.source_key = "home"      # the root shown: "home" or (server_id, section_id)
        self.images = None
        self._quit_after_play = False
        self.script = self._parse_script(args.script)
        self.frame = 0
        self.last_minute = None
        self.saver = None             # the screensaver while it shows
        self.vqueue = []              # the local video queue
        self.idle_t = time.monotonic()
        from .ambient import Ambient
        self.ambient = Ambient()      # the idle Ribbon background
        self.input_t = time.monotonic()                   # the last input of any kind
        self.ambient_ok = not args.script or os.environ.get("BATOPLEX_AMBIENT") == "1"
        from .lineup import Lineup
        self.lineup = Lineup(self.post)   # Live TV: tapes in memory
        from .watchlist import Watchlist
        self.watchlist = Watchlist(self)  # Reset for each account and profile

    # ------------------------------------------------------------ display
    def _open_display(self):
        pg = self.pg
        sizes = pg.display.get_desktop_sizes() if hasattr(pg.display, "get_desktop_sizes") else []
        desk = tuple(sizes[0]) if sizes else (0, 0)
        L.info("desktop %s, driver %s", desk, pg.display.get_driver())
        from . import plex
        plex.PAL = desk[1] == 576          # Video Quality: 576p targets on a PAL screen
        if 0 < desk[1] <= LOW_LINES:
            # A 240p-class mode has no interlace flicker to filter: Off unless the user chose.
            self.settings.set_default("flicker_filter", "off")
        if self.args.window:
            self.screen = pg.display.set_mode((MODE_W, MODE_H))
        elif desk == (MODE_W, MODE_H):
            os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"
            self.screen = pg.display.set_mode((MODE_W, MODE_H), pg.NOFRAME)
        elif self.args.fullscreen or desk != (0, 0):
            # Another display size (an HD TV, a 240p mode): the 641x480 frame scaled to fill it.
            self.screen = pg.display.set_mode((MODE_W, MODE_H), pg.SCALED | pg.FULLSCREEN)
        else:
            self.screen = pg.display.set_mode((MODE_W, MODE_H))   # size unknown (dev machines)
        pg.display.set_caption("Batoplex")

    # ------------------------------------------------------------ providers
    def _use(self, provider):
        from .images import ImageCache
        from .sources import Sources
        if self.images is not None:
            self.images.close()
        if not getattr(provider, "server_name", ""):
            provider.server_name = self.secrets.get("server_name") or ""
        if getattr(self, "music", None) is not None:
            self.music.stop()                # the old provider's queue ends (sign-out, switch)
        if getattr(self, "sleep", None) is not None:
            self.sleep.clear()               # a timer belongs to the user who set it
        self.sources = Sources([provider])
        if hasattr(self, "vqueue"):
            self.vqueue.clear()              # the queue belongs to the user who made it
        self.drop_saver()                  # its art came from the old provider
        self.source_key = "home"
        self.src_gen = self.src_gen + 1     # late server results are dropped
        cache = "none" if getattr(provider, "local_images", False) else "plex"
        self.images = ImageCache(self.sources, os.path.join(config.data_dir(), "cache", cache),
                                 fmt=getattr(self, "canvas", None))
        if hasattr(self, "lineup"):          # channel caches for each account and profile
            self.lineup.reset(self._lineup_key(provider), self.sources)
        if hasattr(self, "watchlist"):
            self._watch_reset(provider)
        threading.Thread(target=self._drop_old_caches, daemon=True).start()

    def _lineup_key(self, provider):
        """The Live TV cache key: the account and the profile. None when no server is
        signed in."""
        if getattr(provider, "local_images", False):
            return None
        from . import channels
        prof = self.secrets.get("profile") or {}
        return channels.cache_key(prof.get("uuid") or self.secrets.get("server_id") or "",
                                  self.settings.profile)

    def watchlist_token(self):
        """The token for the Discover host: the account's for the owner, a Home
        profile's own token, else None. Never the owner's token for another profile
        (resource_token() falls back to it), never a server token."""
        prof = self.secrets.get("profile") or {}
        if not prof or prof.get("admin"):
            return self.secrets.get("user_token") or None
        return prof.get("token") or None

    def _watch_reset(self, provider):
        """A new provider, account or profile: the Watchlist state of that account and profile.
        Its file name holds the owner's account uuid and the profile uuid; with no uuid known
        the state stays in memory. The first time a profile has a Watchlist, its pin goes
        last."""
        from . import channels
        tok = None if getattr(provider, "local_images", False) else self.watchlist_token()
        prof = self.secrets.get("profile") or {}
        acct = self.secrets.get("account_uuid") or (prof.get("uuid") if prof.get("admin") else None)
        wid = channels.cache_key(acct, prof.get("uuid") or acct) if tok and acct else None
        self.watchlist.reset(wid, tok, self.secrets.get("client_id"),
                             owner=not prof or bool(prof.get("admin")))
        if tok and self.settings.seed_watch():
            try:
                self.settings.save()
            except OSError:
                pass

    def watch_visible(self):
        """The Watchlist source and its actions show for this profile."""
        wl = getattr(self, "watchlist", None)
        src = self.sources
        return wl is not None and wl.visible() and src is not None and not src.local_images

    def _drop_old_caches(self):
        """Version 1 kept one cache folder per server URL (cache/plex-<hash>); early builds
        also had cache/demo."""
        import shutil
        root = os.path.join(config.data_dir(), "cache")
        try:
            for n in os.listdir(root):
                if n.startswith("plex-") or n == "demo":
                    shutil.rmtree(os.path.join(root, n), ignore_errors=True)
        except OSError:
            pass

    # ------------------------------------------------------------ sources (side panel)
    def open_panel(self, focus=None):
        from .screens.panel import PanelScreen
        self.overlay(PanelScreen(self, self.top, focus=focus), "panel")

    def _source_keys(self):
        libs = self.sources.pinned(self.settings)
        keys = [((l.server_id, l.section_id), not l.extra.get("down")) for l in libs]
        wi = self.settings.watch_index() if getattr(self, "watch_visible", bool)() else None
        if wi is not None:                           # A root source
            keys.insert(min(wi, len(keys)), ("watchlist", True))
        return ["home"] + [k for k, up in keys if up]

    def go_source(self, key):
        """Replace the root with Home or a pinned library."""
        from .screens.home import HomeScreen
        if key == "home":
            root = HomeScreen(self, title="Home", loader=self.home_loader)
        elif key == "watchlist":
            from .screens.watchlist import WatchlistScreen
            root = WatchlistScreen(self, root=True)
        else:
            lib = next((l for l in self.sources.pinned(self.settings)
                        if (l.server_id, l.section_id) == key), None)
            if lib is None:
                return
            from .screens.library import LibraryScreen
            root = LibraryScreen(self, lib, root=True)
        self.source_key = key
        keys = self._source_keys()
        self.source_slot = keys.index(key) if key in keys else 0
        self.replace(root)

    def pins_changed(self):
        """Pins decide the merged Continue Watching, the order of library rows on Home, and
        which servers Search asks."""
        from .screens.home import HomeScreen
        from .screens.search import SearchScreen
        for scr in self.stack:
            if isinstance(scr, HomeScreen) and scr.library is None and scr.loader:
                scr.reload(keep_focus=True)
            elif isinstance(scr, SearchScreen):
                scr.sources_changed()
        if self.saver is not None:
            self.saver.sources_changed()
        self.watchlist.sources_changed()            # The server set changed

    def music_played(self, t, stamp):
        """A track scrobbled. Home's music rows come from the home server
        only, so a track of that server makes Home stale (it loads again when it shows).
        A reply from an earlier profile or server (src_gen moved on) is dropped; a track of
        another server is not Home's, even with the same section id."""
        from .screens.home import HomeScreen
        if stamp != self.src_gen or self.sources is None or t is None \
                or t.server_id != self.sources.home_id:
            return
        for scr in self.stack:
            if isinstance(scr, HomeScreen) and scr.library is None and scr.loader:
                scr.mark_stale()

    def home_loader(self):
        """Home rows from the Sources in use when the load runs (a reconnect replaces it)."""
        return self.sources.home_rows()

    def next_source(self, d):
        keys = self._source_keys()
        if keys == [self.source_key]:
            return                                   # nothing else to go to
        if self.source_key in keys:
            i = keys.index(self.source_key) + d
        else:
            # The current library was unpinned: its old slot holds the next source now.
            i = min(getattr(self, "source_slot", 0), len(keys)) + (0 if d > 0 else -1)
        self.go_source(keys[i % len(keys)])

    def provider_for(self, item):
        return self.sources.provider_for(item)

    def start(self):
        from .screens.system import SignInScreen
        if self.secrets.get("server_token") and self.secrets.get("server_url"):
            self.settings.profile = self.profile_key()
            if self.home_users_n() > 1 and not self.settings["auto_sign_in"] \
                    and self.secrets.get("user_token"):
                from .screens.profile import ProfileScreen
                self._use_none()
                self.apply_theme(owner=True)         # the launch picker: the owner's theme
                self.replace(ProfileScreen(self, at_launch=True))
                return
            self.enter_stored()
            self._count_home_users()
        elif self.secrets.get("user_token"):
            self._use_none()
            scr = SignInScreen(self, token=self.secrets.get("user_token"))
            self.replace(scr)
            self.finish_signin(self.secrets.get("user_token"), scr)
        else:
            self._use_none()
            self.replace(SignInScreen(self))

    # ------------------------------------------------------------ theme
    def apply_theme(self, owner=False):
        """Apply the theme and High Contrast of the profile (owner: the account
        owner's, for the pickers), then let screens drop art kept at the old gain
        and draw again."""
        from . import theme as ui_theme
        st = self.settings
        src = {k: st.owner(k) for k in ("theme", "high_contrast")} if owner else st
        ui_theme.apply(src)
        from . import ambient, draw
        if ambient.enabled():                # build the Ribbon strip now, on this press, not as a
            draw.cached(("ribbon",), ambient.build_strip)   # stall in the idle fade
        for scr in getattr(self, "stack", ()):
            hook = getattr(scr, "theme_changed", None)
            if hook is not None:
                hook()
        self._redraw_n = max(getattr(self, "_redraw_n", 0), 2)

    # ------------------------------------------------------------ Plex Home profiles
    def home_users_n(self):
        try:
            return int(self.secrets.get("home_users_n") or 0)
        except (TypeError, ValueError):
            return 0

    def profile_key(self):
        """Settings key of the stored profile: "default" for the account owner."""
        prof = self.secrets.get("profile") or {}
        return "default" if not prof or prof.get("admin") else prof.get("uuid") or "default"

    def resource_token(self):
        """The token that lists servers: the switched Home user's, else the account's."""
        return (self.secrets.get("profile") or {}).get("token") or self.secrets.get("user_token")

    def enter_stored(self):
        """Home from the servers stored for the current profile (no plex.tv call first)."""
        from .plex import PlexProvider
        from .screens.home import HomeScreen
        self.settings.profile = self.profile_key()
        url = self.settings["server_url"] or self.secrets.get("server_url")
        self._use(PlexProvider(url, self.secrets.get("server_token"), self.secrets.get("client_id"),
                               self.settings, server_id=self.secrets.get("server_id")))
        self.replace(HomeScreen(self, loader=self.home_loader))
        self._connect_others()

    def _count_home_users(self):
        """Refresh the number of Home users in the background (it decides the picker)."""
        from . import account
        cid, tok, gen = self.secrets.get("client_id"), self.secrets.get("user_token"), self.src_gen
        if not tok:
            return

        def work():
            try:
                users = account.home_users(cid, tok)
            except Exception as e:
                L.info("home users failed: %s", e)
                return
            self.post(lambda: gen == self.src_gen and self.note_users(users))
        threading.Thread(target=work, daemon=True).start()

    def note_users(self, users):
        """Keep the number of Home users. With no profile stored, the servers on this box are
        the owner's: keep the owner's record, so an offline launch knows about a PIN."""
        self.secrets.set(home_users_n=len(users))
        admin = next((u for u in users if u.get("admin")), None)
        if admin is not None and admin.get("uuid") and \
                self.secrets.get("account_uuid") != admin["uuid"]:
            self.secrets.set(account_uuid=admin["uuid"])   # the Watchlist cache key
        prof = self.secrets.get("profile")
        me = next((u for u in users if prof and u.get("uuid") == prof.get("uuid")), None)
        if me is not None and me.get("thumb") != prof.get("thumb"):
            self.secrets.set(profile=dict(prof, thumb=me.get("thumb")))
        if not self.secrets.get("profile"):
            owner = next((u for u in users if u.get("admin")), None)
            if owner:
                self.secrets.set(profile={"uuid": owner.get("uuid"), "title": owner.get("title") or "",
                                          "admin": True, "protected": bool(owner.get("protected")),
                                          "thumb": owner.get("thumb")})

    def open_profiles(self):
        from .screens.profile import ProfileScreen
        self.push(ProfileScreen(self))

    def switch_profile(self, user, pin, screen):
        """Become a Plex Home user: the switch call gives the user's token, the user's servers
        come from it, and Home opens on them. The account token never changes."""
        from . import account, plex
        cid, acct = self.secrets.get("client_id"), self.secrets.get("user_token")
        want = self.secrets.get("server_id")
        gen = self.src_gen

        def work():
            if user.get("admin"):
                if user.get("protected"):
                    account.switch_user(cid, acct, user, pin)   # checks the owner's PIN
                tok = acct
            else:
                tok = account.switch_user(cid, acct, user, pin)
            servers = plex.discover_servers(cid, tok)
            servers.sort(key=lambda r: (r["id"] != want, not r.get("owned")))
            last = plex.PlexError("No Plex Media Server for this user")
            for srv in servers:
                try:
                    return tok, srv, plex.pick_connection(cid, srv), servers
                except plex.AuthError:
                    raise
                except plex.PlexError as e:
                    last = e
            raise last

        def done(res):
            if gen != self.src_gen:
                screen.switch_done()                   # the picker takes input again
                return                                 # signed out meanwhile
            tok, srv, url, servers = res
            prof = {"uuid": user.get("uuid") or str(user.get("id") or ""), "title": user.get("title") or "",
                    "admin": bool(user.get("admin")), "protected": bool(user.get("protected")),
                    "thumb": user.get("thumb")}
            if not user.get("admin"):
                prof["token"] = tok
            # The previous user's server tokens go: each user has their own.
            self.secrets.v["servers"] = {}
            self.secrets.v.pop("home_server", None)
            self.secrets.set(profile=prof, server_token=srv["token"], server_url=url,
                             server_name=srv["name"], server_id=srv["id"])
            screen.switch_done()
            self.settings.profile = self.profile_key()
            self.source_key = "home"
            from .plex import PlexProvider
            from .screens.home import HomeScreen
            self._use(PlexProvider(url, srv["token"], cid, self.settings, server_id=srv["id"]))
            self.replace(HomeScreen(self, loader=self.home_loader))
            self._connect_others(servers)
            L.info("profile switched")

        def fail(e):
            if gen != self.src_gen:
                screen.switch_done()
                return
            kind = getattr(e, "kind", None)
            if kind == "auth" or isinstance(e, plex.AuthError):
                screen.switch_failed("auth", "")
                self.sign_out()
                return
            if kind != "pin":
                L.info("profile switch failed: %s", e)      # the log only
            screen.switch_failed(kind or "other",
                                 "Incorrect PIN" if kind == "pin" else "Can't switch user")
        screen.run_bg(work, done, fail)

    def _use_none(self):
        from .model import NoProvider
        self._use(NoProvider(self.settings))     # no server until sign-in

    def start_signin(self):
        from .screens.system import SignInScreen
        self.replace(SignInScreen(self))

    def finish_signin(self, user_token, screen):
        from . import plex
        from .screens.home import HomeScreen
        if getattr(screen, "finding", False):
            return                                   # a second A while the search runs
        screen.finding = True
        cid = self.secrets.get("client_id")
        self.secrets.set(user_token=user_token)   # keep it: a failed server search can retry
        screen.token = user_token
        screen.error = None
        screen.status = "Signed in. Finding your server\u2026"

        gen = self.src_gen

        def work():
            from . import account
            try:
                users = account.home_users(cid, user_token)
            except plex.PlexError as e:
                # An account with no Plex Home can refuse the list; a bad token fails below.
                L.info("home users failed: %s", e)
                users = []
            servers = plex.discover_servers(cid, user_token)
            if not servers:
                raise plex.PlexError("No Plex Media Server on this account")
            servers.sort(key=lambda s: not s.get("owned"))
            last = None
            for srv in servers:
                try:
                    url = plex.pick_connection(cid, srv, self.settings["server_url"])
                    return srv, url, servers, users
                except plex.PlexError as e:
                    last = e
            raise last

        def done(res):
            try:
                _done(res)
            finally:
                screen.finding = False           # a fault here must not block a retry

        def _done(res):
            if gen != self.src_gen:
                return                               # signed out meanwhile
            srv, url, servers, users = res
            self.secrets.v.pop("profile", None)      # a new link starts as the account owner
            self.note_users(users)
            self.secrets.set(user_token=user_token, server_token=srv["token"], server_url=url,
                             server_name=srv["name"], server_id=srv["id"])
            self.settings.profile = "default"
            if self.home_users_n() > 1:
                from .screens.profile import ProfileScreen
                self.replace(ProfileScreen(self, at_launch=True))
                return
            self._use(plex.PlexProvider(url, srv["token"], cid, self.settings, server_id=srv["id"]))
            self.replace(HomeScreen(self, loader=self.home_loader))
            self._connect_others(servers)

        def fail(e):
            screen.finding = False
            if isinstance(e, plex.AuthError):
                self.sign_out()
                return
            screen.error = "Signed in, but can't reach a server: %s. Press A to retry." % e
        screen.run_bg(work, done, fail)

    def _connect_others(self, servers=None):
        """Connect every other server of the account in the background:
        each joins Sources when it answers; a server that does not answer stays out."""
        from . import plex
        tok = self.resource_token()
        if not tok:
            return
        cid, gen = self.secrets.get("client_id"), self.src_gen
        home_p = getattr(self.sources, "home", None)

        def one(srv):
            try:
                url = plex.pick_connection(cid, srv)
            except plex.PlexError as e:
                L.info("server %s unavailable: %s", srv.get("name"), e)
                return
            p = plex.PlexProvider(url, srv["token"], cid, self.settings, server_id=srv["id"])
            p.migrate = False            # version 1 pins belong to the home server only
            try:
                load_sections(p, srv.get("name"))
            except plex.AuthError as e:
                L.info("server %s refused the token: %s", srv.get("name"), e)
                return
            self.post(lambda: self._add_server(gen, srv, url, p))

        def work():
            try:
                srvs = servers if servers is not None else plex.discover_servers(cid, tok)
            except plex.PlexError as e:
                L.info("server list failed: %s", e)
                return
            # Read the home id here, after the server list: /identity may have answered.
            home = self.secrets.get("server_id") or getattr(home_p, "server_id", None)
            if not home:
                # An old auth.json without the home id, and /identity has not answered:
                # the home server is the one with the stored name.
                name = self.secrets.get("server_name")
                srvs = [r for r in srvs if not (name and r.get("owned") and r.get("name") == name)]
            for srv in srvs:
                if srv.get("id") and srv["id"] != home:
                    threading.Thread(target=one, args=(srv,), daemon=True).start()
        threading.Thread(target=work, daemon=True).start()

    def _add_server(self, gen, srv, url, p):
        if gen != self.src_gen or self.sources is None:
            return                                   # signed out or reconnected meanwhile
        if srv["id"] == getattr(self.sources.home, "server_id", None):
            return                                   # the home server (its id came late)
        p.server_name, p.owned = srv.get("name") or "", bool(srv.get("owned"))
        self.sources.add(p)
        servers = self.secrets.v.setdefault("servers", {})
        servers[srv["id"]] = {"name": srv.get("name"), "token": srv["token"], "url": url,
                              "owned": bool(srv.get("owned"))}
        self.secrets.save()                          # a background save: a failure only logs
        L.info("server %s connected", srv.get("name"))
        self.lineup.server_added()
        self.watchlist.server_added(srv["id"])
        if self.saver is not None:
            self.saver.sources_changed()
        for scr in self.stack:
            if hasattr(scr, "sources_changed"):
                scr.sources_changed()
        if self.settings.pins(srv["id"]):            # its pinned items join Home now
            from .screens.home import HomeScreen
            for scr in self.stack:
                if isinstance(scr, HomeScreen) and scr.library is None and scr.loader:
                    scr.reload(keep_focus=True)

    def reconnect(self, home):
        """Retry from an error on Home: find the server again (its address can change),
        and go back to sign-in if the token is no longer valid."""
        from . import plex
        if not self.secrets.get("user_token"):
            home.loading = True
            home.reload()
            return
        cid, tok = self.secrets.get("client_id"), self.resource_token()

        def work():
            servers = plex.discover_servers(cid, tok)
            want = self.secrets.get("server_id")
            servers.sort(key=lambda s: (s["id"] != want, not s.get("owned")))
            for srv in servers:
                try:
                    url = plex.pick_connection(cid, srv, self.settings["server_url"])
                except plex.PlexError:
                    continue
                p = plex.PlexProvider(url, srv["token"], cid, self.settings, server_id=srv["id"])
                p.migrate = srv["id"] == want or not want   # version 1 pins: home server only
                # Pins need the sections; one try, as Home loads them again if this fails.
                try:
                    load_sections(p, srv.get("name"), tries=1)
                except plex.AuthError as e:
                    L.info("server %s refused the token: %s", srv.get("name"), e)
                    continue                         # a revoked share: try the next server
                return srv, url, p
            raise plex.PlexError("no server is reachable")

        gen = self.src_gen

        def done(res):
            from .screens.home import HomeScreen
            if gen != self.src_gen or home not in self.stack:
                return                               # signed out or left meanwhile
            srv, url, p = res
            self.secrets.set(server_token=srv["token"], server_url=url, server_name=srv["name"],
                             server_id=srv["id"])
            key = self.source_key
            self._use(p)
            lib = getattr(home, "library", None)
            if lib is not None and lib.server_id != srv["id"]:
                # Another server answered: this library is not on it, so go Home.
                self.replace(HomeScreen(self, loader=self.home_loader))
                self._connect_others()
                return
            if lib is not None:
                # Library and collection loaders call self.sources when they run, so they
                # already use the new provider.
                self.source_key = key
            else:
                home.loader = self.home_loader
            home.loading, home.error = True, None
            home.reload()
            self._connect_others()

        def fail(e):
            if gen != self.src_gen or home not in self.stack:
                return
            home.loading = False
            if isinstance(e, plex.AuthError):
                self.auth_lost()
            else:
                home.error = str(e)
        home.loading, home.error = True, None
        home.run_bg(work, done, fail)

    def auth_lost(self):
        """A server or plex.tv refused the token in use. A Home user's token goes back to the
        picker (the account can still be fine); the account's own token means a new sign-in."""
        prof = self.secrets.get("profile") or {}
        if prof.get("token") and self.secrets.get("user_token") and self.home_users_n() > 1:
            from .screens.profile import ProfileScreen
            self.secrets.v["servers"] = {}
            self.secrets.v.pop("home_server", None)
            self.secrets.set(profile=dict(prof, token=None))
            self._use_none()
            self.apply_theme(owner=True)             # the picker: the owner's theme
            self.replace(ProfileScreen(self, at_launch=True))
            return
        self.sign_out()

    def sign_out(self):
        self.secrets.clear_login()
        self.settings.profile = "default"
        self._use_none()          # the previous user's provider and images go; src_gen moves on
        self.start_signin()

    # ------------------------------------------------------------ navigation
    def post(self, fn):
        self.posted.append(fn)

    def _snapshot(self, kind="fade", width=0):
        """Start a transition from the frame on screen now, without the toast.
        kind: "fade" (push, pop, replace, the screensaver: 167 ms), "menu" (menus and dialogs,
        open and close: 100 ms), "panel" / "panel_close" (the side panel column slides, the
        dim fades: 117 ms). width: the panel column width."""
        if self.pre is None:
            return
        if kind in ("panel", "panel_close"):
            a = anim.Anim(FAST, anim.EASE_OUT, q=4, min_step=8)
            a.start(-width, 0) if kind == "panel" else a.start(0, -width)
        else:
            a = anim.Anim(OVERLAY_FADE if kind == "menu" else BASE, anim.LINEAR)
            a.start(0, 1)
        dim = anim.Anim(a.ms, anim.LINEAR)       # the panel dim; a fade ends with its anim
        dim.start(0, 1)
        self.trans = {"kind": kind, "old": self.pre.copy(), "anim": a, "dim": dim, "w": width}

    def overlay(self, scr, kind="menu"):
        """Open a menu, a dialog ("menu") or the side panel ("panel") over the
        top screen with its transition; pop() closes it with the matching one."""
        scr.overlay_kind = kind
        self._snapshot(kind, getattr(scr, "column_w", lambda: 0)())
        self.stack.append(scr)

    def push(self, scr, panel=None):
        """panel: the side panel row that opens scr; B on scr reopens the panel on it."""
        self._snapshot()
        scr.panel_origin = panel
        self.stack.append(scr)
        scr.on_enter()

    def back(self):
        """B that closes the top page: a
        page opened from the side panel reopens the panel on its row. Other closes (playback,
        menu actions, a page that closes itself) use pop() and keep the panel shut. The origin
        lives on the screen, so a stack replace (profile switch, sign-out) drops it."""
        origin = self.top.panel_origin
        self.pop()
        if origin is not None:
            self.open_panel(focus=origin)

    def pop(self, result=None):
        kind = getattr(self.top, "overlay_kind", None) if self.stack else None
        if kind == "panel":
            self._snapshot("panel_close", self.top.column_w())
        else:
            self._snapshot(kind or "fade")
        if len(self.stack) > 1:
            self.stack.pop()
        self.stack[-1].anim = True
        self.stack[-1].on_resume(result)

    def replace(self, scr):
        self._snapshot()
        self.stack = [scr]
        scr.on_enter()

    @property
    def top(self):
        return self.stack[-1]

    def open_item(self, it):
        from .model import Item
        from .screens.detail import DetailScreen
        from .screens.episodes import EpisodeScreen
        if it.type == "library":
            from .screens.library import LibraryScreen
            self.push(LibraryScreen(self, it))
        elif it.type == "album":
            from .screens.album import AlbumScreen
            self.push(AlbumScreen(self, it))
        elif it.type == "track":
            self.open_album_of(it)
        elif it.type == "station":
            self.play_station(it)
        elif it.type == "artist":
            self.open_artist_of(it)
        elif it.type == "clip":
            self.play(it, resume=False)                  # an extra: from the start, no state
        elif it.type == "person":
            if it.id:                                    # an old server gives no tag id
                from .screens.library import PersonScreen
                self.push(PersonScreen(self, it))
            else:
                self.toast("No titles for this person on this server")
        elif it.type == "show":
            self.push(EpisodeScreen(self, it.id, it.title, show=it))
        elif it.type == "season":
            tgt = Item(id="", type="season", title="", season_id=it.id)
            self.push(EpisodeScreen(self, it.show_id, it.show_title, focus_episode=tgt))
        elif it.type == "episode" and it.show_id:
            self.push(EpisodeScreen(self, it.show_id, it.show_title, focus_episode=it))
        elif it.type == "collection" and hasattr(self.sources.provider_for(it), "collection_items"):
            from .screens.library import GridScreen
            lib = Item(id=str(it.section_id or ""), type="library", title=it.title,
                       section_id=it.section_id, server_id=it.server_id)
            self.push(GridScreen(self, lib, it.title, collection=it))
        else:
            self.push(DetailScreen(self, it))

    def open_menu(self, rows=None, title=None, settings=True):
        """Start: the menu of the screen on top, with the app rows (Now Playing, Play Queue,
        Settings) last. With no rows argument: a screen without menu_items, or
        in Move mode, opens nothing; one with no rows of its own gets the app rows only (no
        jump to Settings). menu_ok False (Detail rows zone): no own rows. menu_app_rows False
        (tool screens): own rows only. True when a menu opened."""
        from .screens.menu import MenuScreen
        top = self.top
        own = rows is not None
        if rows is None:
            if not hasattr(top, "menu_items") or getattr(top, "moving", None) is not None:
                return False                     # Move mode: A places, B cancels
            rows = top.menu_items() if getattr(top, "menu_ok", True) else []
            from .screens.actions import ordered
            rows = ordered(rows)                 # One order for every menu
            own = bool(rows)
            if self._holding and not own:
                return False                     # a hold here is OK
            settings = settings and getattr(top, "menu_app_rows", True)
        rows = list(rows)
        if settings:
            if self.music.active and not getattr(top, "is_now_playing", False):
                rows.append(("Now Playing", self.open_now_playing))
            from .screens.actions import queue_row
            qr = queue_row(self)
            if qr is not None and not getattr(top, "is_queue", False):
                rows.append(qr)
            rows.append(("Settings", self.open_settings))
        if not rows:
            return False
        if title is None:
            title = getattr(top, "menu_title", "") if own else ""
        self.overlay(MenuScreen(self, top, title, rows))
        return True

    def open_search(self, panel=None):
        """Y on Home and the panel's Search row. One Search screen at most: an open one comes
        back to the top with its query. panel: as push()."""
        from .screens.search import SearchScreen
        if any(isinstance(scr, SearchScreen) for scr in self.stack):
            while not isinstance(self.top, SearchScreen):
                self.pop()
            self.top.panel_origin = panel        # the way in now decides what B does
            return
        self.push(SearchScreen(self), panel=panel)

    def go_to_show(self, it):
        from .screens.episodes import EpisodeScreen
        self.push(EpisodeScreen(self, it.show_id, it.show_title, focus_episode=it))

    def open_settings(self, panel=None):
        from .screens.system import SettingsScreen
        self.push(SettingsScreen(self), panel=panel)

    def confirm_exit(self, on_no=None):
        """on_no: what Cancel (the button or B) does after the dialog closes."""
        from .screens.system import ExitDialog
        self.overlay(ExitDialog(self, self.top, on_no=on_no))

    def quit(self):
        self.running = False

    def mark_watched(self, item, watched):
        item.watched = watched
        item.view_offset_ms = 0
        prov = self.provider_for(item)

        gen = getattr(self, "src_gen", 0)

        def work():
            try:
                if watched:
                    prov.scrobble(item)
                elif hasattr(prov, "unscrobble"):
                    prov.unscrobble(item)
                if watched:                          # Only a good mark
                    self.post(lambda: gen == self.src_gen and self.watch_played(
                        item.extra.get("guid") or self.watchlist.guid_of(item.server_id, item.id)))
            finally:
                # Reload Home only after the server has the change, or it puts the old copy back.
                self.post(lambda: self._refresh_homes([f], exact=True))
        f = {"item_id": item.id, "server_id": item.server_id, "position_ms": 0, "watched": watched}
        self._refresh_homes([f], exact=True, reload=False)
        threading.Thread(target=work, daemon=True).start()

    def watch_played(self, guid=None):
        """The Watchlist is stale after a play; an open Watchlist screen loads it again."""
        self.watchlist.mark_played(guid)
        if any(getattr(scr, "is_watchlist", False) for scr in self.stack):
            self.watchlist.load()

    def _refresh_homes(self, files, exact=False, reload=True):
        """files: [{item_id, position_ms, watched}]. Playback results only ever set watched
        (exact=False); mark played/unplayed sets it either way (exact=True)."""
        # Every screen in the stack that lists items takes the change (Home, Episodes, Detail).
        for scr in self.stack:
            fn = getattr(scr, "apply_files", None)
            if fn is not None:
                fn(files, exact, reload)

    def _theme_tick(self):
        """Show theme music (Plex "Play theme music while browsing TV shows"): starts 1 s after
        a show's Episodes or episode Detail screen opens, plays once, stops on leaving."""
        if self.music.active:                # no theme over music
            return
        top = getattr(self.top, "under", self.top)          # a menu over the screen keeps it
        ref = None
        if self.settings["theme_music"] and self.sources is not None and self.sources.can_theme():
            fn = getattr(top, "theme_ref", None)
            ref = fn() if fn else None
        now = time.monotonic()
        if ref != self._theme_want:
            self._theme_want, self._theme_since = ref, now
            if ref is None or ref != self._theme_played:
                self.audio.stop()
                self._theme_played = None
        if ref and ref != self._theme_played and now - self._theme_since >= 1.0:
            self._theme_played = ref
            L.info("theme %s", ref)
            try:
                url, headers = self.sources.theme_url(ref)
            except Exception as e:
                L.info("theme url failed: %s", e)
                return
            self.audio.play(url, headers, self.settings["theme_volume"])

    def _playback_image(self, ref, w, h):
        """Images for the playback overlay (the Up Next still). UI thread only."""
        if not self.images:
            return None
        from . import tokens
        return self.images.get(ref, w, h, tokens.GAIN["art"])

    # ------------------------------------------------------------ playback
    @property
    def playing(self):
        """True while a playback session runs (starting, playing, error card)."""
        return self.playback.session != "idle"

    def play(self, item, resume=True, queue=None):
        """A video session. queue: a fixed queue (a playlist or the local video queue) whose
        current item is item; its items go to their own servers."""
        from .screens.system import PlayingScreen
        if not self.playback.available:
            self.toast("Playback is not available")
            return
        if self.music.active:                # A video ends the music session;
            self.music.stop(wait_s=1.5)      # its "stopped" goes out before the video starts
        self.audio.stop(fade=False)          # free the audio device for the video
        self.visualizer.stop()
        self._theme_played = self._theme_want
        prov = self.provider_for(item) if queue is None else self.sources.strict(item.server_id)
        if prov is None:
            self.toast("Can't play %s" % item.title)
            return
        self.push(PlayingScreen(self, item))
        self.playback.play(item, prov, resume=resume, queue=queue)

    # ------------------------------------------------------------ Live TV
    def _picture_guide(self, cid):
        """RB in a channel session: the guide over the picture (playback draws and drives it)."""
        from .screens.guide import PictureGuide
        return PictureGuide(self, cid)

    def open_guide(self, focus=None, panel=None):
        from .screens.guide import GuideScreen
        self.push(GuideScreen(self, focus=focus), panel=panel)

    def live_seed(self):
        """Once per profile, the first time a channel shows: pin Live TV last. An unpin
        stays (the profile's live_seeded flag)."""
        src = self.sources
        if src is None or src.local_images or not self.lineup.visible():
            return
        if self.settings.seed_live():
            try:
                self.settings.save()
            except OSError:
                pass
            from .screens.panel import PanelScreen
            for scr in self.stack:
                if isinstance(scr, PanelScreen):
                    scr.refresh()

    def open_livetv_setup(self):
        from .screens.chsetup import SetupScreen
        self.push(SetupScreen(self))

    def tune(self, cid):
        """A channel session from the clock point of channel cid."""
        from .lineup import Surfer
        from .screens.system import PlayingScreen
        if not self.playback.available:
            self.toast("Playback is not available")
            return
        if self.playing:
            return
        sf = Surfer(self.lineup, self.sources, self.settings)
        t = sf.tune(cid)
        prov = sf.provider(t) if t is not None else None
        if prov is None:
            self.toast("This channel is not available" if t is None else "Server not available")
            return
        if self.music.active:
            self.music.stop(wait_s=1.5)
        self.audio.stop(fade=False)
        self.visualizer.stop()
        self._theme_played = self._theme_want
        self.push(PlayingScreen(self, t.item))
        self.playback.play_channel(t, prov, sf)

    def queue_changed(self):
        """The local video queue changed: screens that show it update."""
        for scr in self.stack:
            fn = getattr(scr, "queue_changed", None)
            if fn is not None:
                fn()

    def playlist_changed(self, pl):
        """A playlist write succeeded: playlist views load again, and the
        composite is fetched again; when its ref did not change, the cached art goes."""
        gen, old = self.src_gen, getattr(pl, "poster", None)
        for scr in self.stack:
            fn = getattr(scr, "playlists_changed", None)
            if fn is not None:
                fn(pl)
        if pl is None or getattr(pl, "type", "") != "playlist":
            return

        def work():
            try:
                new = self.sources.playlist(pl)
            except Exception as e:
                L.info("playlist refresh failed: %s", e)
                return
            self.post(lambda: gen == self.src_gen and self._playlist_fresh(pl, new, old))
        threading.Thread(target=work, daemon=True).start()

    def _playlist_fresh(self, pl, new, old):
        if new is None:
            return
        if old and new.poster == old and self.images is not None:
            self.images.forget(old)
        for scr in self.stack:
            fn = getattr(scr, "playlist_fresh", None)
            if fn is not None:
                fn(new)

    def play_playlist(self, items, index=0, shuffle=False, resume=True, origin=None, local=False):
        """Video items of a playlist or the local queue, one after another from items[index]:
        no Up Next card, Auto Play does not stop the list. Shuffle: a
        random order of all items, from the first. resume=False: the first item from the
        start (Play from Start)."""
        if not items or self.playing or (origin is not None and origin not in self.stack):
            return
        import random
        from .model import Queue
        items = list(items)
        if shuffle:
            random.shuffle(items)
            index = 0
        index = max(0, min(index, len(items) - 1))
        self.play(items[index], resume=resume, queue=Queue(items, index, fixed=True))
        self._local_queue = local and self.playing

    def _end_playback(self, res):
        """A session ended (stop, end of file, cancel, or error dismissed)."""
        from .screens.system import PlayingScreen
        L.info("return to UI at %s", type(self.stack[-2]).__name__ if len(self.stack) > 1 else "?")
        # No window raise here: pygame 2.6.1 _sdl2.video.Window.from_display_module()
        # corrupted memory on Batocera (segfaults after returns from playback). The UI
        # window is the only one left when mpv closes, and the pad works without focus.
        if res.get("files"):
            self._refresh_homes(res["files"])
            for f in res["files"]:
                if f.get("watched"):                 # A played item
                    self.watch_played(self.watchlist.guid_of(f.get("server_id"), f.get("item_id")))
        if res.get("queue_end") and getattr(self, "_local_queue", False):
            self.vqueue.clear()              # The queue empties at its end
            self.queue_changed()
        self._local_queue = False
        if isinstance(self.top, PlayingScreen):
            self.pop(res)
        if res.get("error") and not res.get("never_loaded"):
            L.info("playback stopped: %s", res["error"])
            self.toast("Playback stopped unexpectedly")
            self.menu_sound("error")
        if self._quit_after_play:
            self.running = False

    def _play_key(self):
        """What plays now: the video session or the music session; None when nothing plays."""
        if self.playing and self.playback.s is not None:
            return ("video", id(self.playback.s))
        if self.music.active:
            return ("music", self.music.session_id)
        return None

    def _sleep_tick(self):
        """Sleep Timer (sleep.SleepTimer.tick): at the time, stop the video and the music;
        the screen saver follows."""
        r = self.sleep.tick(self._play_key())
        if r == "stop":
            L.info("sleep timer: stop")
            if self.playing:
                self.playback.stop_now()
            if self.music.active:
                self.music.stop()
        elif r == "moved":
            self.music.sleep_changed()            # the new music gets its next entry back

    def open_sleep_menu(self):
        """Now Playing > Sleep Timer: the choices as a menu (Plexamp has it in its menu)."""
        from .sleep import choice_label
        end = not self.music.live

        def pick(c):
            self.sleep.set(c)
            self.music.sleep_changed()
            self.toast("Sleep Timer Off" if c is None else
                       "Sleep Timer: %s" % choice_label(c, "End of Track"))
        rows = [(choice_label(c, "End of Track"), lambda c=c: pick(c))
                for c in self.sleep.choices(end)]
        self.open_menu(rows=rows, title="SLEEP TIMER", settings=False)
        opts = self.sleep.choices(end)
        if self.sleep.mode in opts and hasattr(self.top, "focus"):
            self.top.focus = opts.index(self.sleep.mode)       # the menu opens on the current one
            self.top.top = max(0, self.top.focus - self.top.visible_rows() + 1)

    def menu_sound(self, event):
        """A Hearth menu sound when the profile has Menu Sounds on and no
        video, music or theme music runs."""
        from . import sounds, theme
        busy = (self.playing or self.music.active or self.audio.playing is not None
                or self.audio.starting or self.visualizer.state == "running")
        if sounds.wanted(self.settings, theme.cur.sounds, busy):
            sounds.play(theme.cur.sounds, event)
        elif sounds.is_open() and not sounds.wanted(self.settings, theme.cur.sounds, False):
            sounds.close()                   # Menu Sounds off or a theme with none: free the device

    def saved(self, ok):
        """The result of a save the user asked for: a failed one shows a toast, at most once
        in SAVE_FAIL_GAP seconds. Returns ok."""
        if ok is False:
            now = time.monotonic()
            if now - getattr(self, "_save_fail_t", -SAVE_FAIL_GAP) >= SAVE_FAIL_GAP:
                self._save_fail_t = now
                self.toast("Couldn't save settings")
        return ok

    def toast(self, msg, private=False):
        """One toast layer. A new toast replaces the shown one and starts its
        hold again with no new fade-in. private: the message has user text (a playlist name):
        it is not logged."""
        L.info("toast: %s", "(private)" if private else msg)
        t = self._toast
        if t is not None and t["state"] in ("in", "hold"):
            t["msg"] = msg
            t["until"] = anim.now() + (TOAST_IN if t["state"] == "in" else 0) + TOAST_HOLD
            return
        a = anim.Anim(TOAST_IN, anim.LINEAR)
        a.start(0, 255)
        self._toast = {"msg": msg, "state": "in", "anim": a, "key": None, "layer": None,
                       "until": anim.now() + TOAST_IN + TOAST_HOLD, "shown": None}

    def _toast_alpha(self):
        """The alpha of the toast in this frame; None when there is none."""
        t = self._toast
        if t is None:
            return None
        if t["state"] == "in" and not t["anim"].active:
            t["state"] = "hold"
        if t["state"] in ("hold", "out") and anim.now() >= t["until"] + TOAST_OUT:
            self._toast = None               # it ran out while not drawn (playback): drop it
            return None
        if t["state"] == "hold":
            if anim.now() < t["until"]:
                return 255
            t["state"] = "out"
            t["anim"] = anim.Anim(TOAST_OUT, anim.LINEAR)
            t["anim"].start(255, 0)
        a = int(t["anim"].value)
        if t["state"] == "out" and not t["anim"].active:
            self._toast = None
            return None
        return a

    def _toast_layer(self, mode):
        """The toast as a filtered layer (draw.toast_layer), built when its text, place,
        filter mode or theme changes."""
        from . import draw, theme
        t = self._toast
        cw = draw.width(lineup.clock_hm(), "small")
        mm = getattr(self.top, "music_mark", None)
        cw += mm(None, 0, measure=True) if mm else 0        # clear of the mark
        r = draw.toast_rect(t["msg"], self.settings["safe"], cw)
        key = (t["msg"], r, mode, theme.version)
        if key != t["key"]:
            t["key"], t["layer"], t["rect"] = key, draw.toast_layer(t["msg"], r, mode), r
        return t["layer"], t["rect"]

    # ------------------------------------------------------------ test script
    def _parse_script(self, s):
        if not s:
            return collections.deque()
        if os.path.exists(s):
            with open(s) as f:
                s = f.read()
        return collections.deque(t.strip() for t in s.replace("\n", ",").split(",") if t.strip())

    def _run_script(self):
        if not self.script:
            return
        if getattr(self, "_wait", 0) > 0:
            self._wait -= 1
            return
        tok = self.script.popleft()
        if tok.startswith("wait:"):
            self._wait = int(tok[5:])
        elif tok.startswith("waitplay"):
            if self.playing:
                self.script.appendleft(tok)
        elif tok == "waitstart":
            if self.playback.session in ("idle", "starting"):
                self.script.appendleft(tok)
        elif tok.startswith("waitmusic:"):
            if self.music.state != tok[10:]:
                self.script.appendleft(tok)
        elif tok.startswith("waitstate:"):
            if self.playback.session != tok[10:]:
                self.script.appendleft(tok)
        elif tok.startswith("sh:"):
            import subprocess
            subprocess.run(tok[3:], shell=True)
        elif tok.startswith("waitvis:"):
            if self.visualizer.state != tok[8:]:
                self.script.appendleft(tok)
        elif tok.startswith("waitidle"):
            if self.playing or getattr(self.top, "loading", False):
                self.script.appendleft(tok)
        elif tok.startswith("shot:"):
            # an opaque copy: the frame's alpha bytes are not kept clean (the display ignores
            # them), and a PNG with alpha 0 shows white in an image viewer
            shot = self.pg.Surface(self.last_out.get_size(), 0, 24)
            shot.blit(self.last_out, (0, 0))
            self.pg.image.save(shot, tok[5:])
            L.info("screenshot %s", tok[5:])
        elif tok == "exit":
            self.quit()
        else:
            self.inputs.inject(tok)

    # ------------------------------------------------------------ music
    def music_key(self, a):
        m = self.music
        if a in ("playpause", "play", "pause"):
            m.toggle() if a != "pause" else m.pause()
        elif a == "next":
            m.next()
        elif a == "prev":
            m.prev()
        elif a == "stop":
            m.stop()

    def play_radio(self, stations, index, show=True):
        """A radio station: the station list is the queue. show=False: a
        preview from Add Stations, with no change of screen."""
        if self.playing or not stations:
            return
        self._theme_played = self._theme_want
        self.audio.stop(fade=False)
        self.music.play_live(stations, index)
        if show:
            self.open_now_playing()

    def play_station(self, station):
        """Library Radio and the other library stations: the server's play queue, refilled
        near its end."""
        origin, src = self.top, self.sources
        if getattr(self, "_station_busy", False):
            return                                   # a second A while the queue is made
        self._station_busy = True
        self.toast("Starting %s…" % station.title)

        def done(tracks):
            self._station_busy = False
            if not tracks:
                self.toast("Can't play this station")
                return
            self.play_music(tracks, 0, origin=origin,
                            refill=lambda: src.station_tracks(station))
        def fail(e):
            self._station_busy = False
            self.toast("Can't play this station")
        origin.run_bg(lambda: src.station_tracks(station), done, fail)

    def play_music(self, items, index=0, shuffle=False, origin=None, refill=None):
        """Start a queue from items[index] on the items' own server, then show Now Playing.
        origin: the screen that asked; a late worker result is dropped
        when that screen is gone (sign-out, profile switch) or a video plays."""
        if not items or self.playing or (origin is not None and origin not in self.stack):
            return
        self._theme_played = self._theme_want
        self.audio.stop(fade=False)
        servers = {i.server_id for i in items}
        if len(servers) > 1:
            # A playlist with entries of other servers: each track goes to its own server.
            from .sources import Router
            prov = Router(self.sources)
        else:
            prov = self.provider_for(items[0])
        self.music.play(items, index, prov, shuffle=shuffle, refill=refill)
        self.open_now_playing()

    def play_quiet(self, items):
        """Music with no Now Playing and no reports (the Weather saver's playlist). Returns
        the music session id, or None when nothing started."""
        if not items or self.playing or self.music.active:
            return None
        servers = {i.server_id for i in items}
        if len(servers) > 1:
            from .sources import Router
            prov = Router(self.sources)
        else:
            prov = self.provider_for(items[0])
        self.music.play(items, 0, prov, shuffle=True, quiet=True)
        return self.music.session_id

    def music_accepts(self, items, session=None):
        from .screens.actions import music_accepts
        return music_accepts(self, items, session)

    def open_album_of(self, track):
        """The track's album, with the focus on the track."""
        from .screens.album import AlbumScreen, album_of
        if track.album_id:
            self.push(AlbumScreen(self, album_of(track), follow=track.id))

    def open_artist_of(self, it):
        """The artist page of an album or track."""
        from .model import Item
        from .screens.library import GridScreen
        aid = it.artist_id if it.type != "artist" else it.id
        if not aid:
            return
        name = (it.extra.get("album_artist") or it.artist) if it.type != "artist" else it.title
        artist = Item(id=aid, type="artist", title=name or "", server_id=it.server_id,
                      section_id=it.section_id)
        lib = Item(id=str(it.section_id or ""), type="library", title="",
                   section_id=it.section_id, server_id=it.server_id)
        self.push(GridScreen(self, lib, artist.title, artist=artist))

    def now_playing_art(self):
        """The playing track's art ref for the screensaver; None when paused
        or idle."""
        t = self.music.track
        return t.poster if t is not None and self.music.state == "playing" else None

    def open_now_playing(self, panel=None):
        """One Now Playing screen at most: an open one comes back to the top. panel: as
        push()."""
        from .screens.nowplaying import NowPlayingScreen
        if not self.music.active:
            return
        if any(isinstance(scr, NowPlayingScreen) for scr in self.stack):
            while not isinstance(self.top, NowPlayingScreen):
                self.pop()
            self.top.panel_origin = panel        # the way in now decides what B does
            return
        self.push(NowPlayingScreen(self), panel=panel)

    def open_visualizer(self, saver=False):
        """The visualizer over the music. saver: started by the screensaver."""
        from .screens.visualizer import VisualizerScreen
        if not self.music.active or self.playing or isinstance(self.top, VisualizerScreen):
            return
        if self.visualizer.unavailable:
            if not saver:
                self.toast("The visualizer is not available")
            return
        self.push(VisualizerScreen(self, saver=saver))

    def _visualizer_tick(self):
        from .screens.visualizer import VisualizerScreen
        v = self.visualizer
        if v.active and not isinstance(self.top, VisualizerScreen):
            v.stop()                       # a video, sign-out or any other screen change
        was = v.state
        v.tick()
        if was != "idle" and v.state == "idle":
            self._redraw_n = 30            # the engine window went: draw the UI again
        while True:
            k = v.pop_key()
            if k is None:
                break
            self.inputs.inject(k)          # keyboard keys from the engine window
        while True:
            ev = v.pop_event()
            if ev is None:
                break
            self.toast("The visualizer is not available" if ev == "unavailable"
                       else "The visualizer stopped")

    # ------------------------------------------------------------ screensaver
    def saver_delay_s(self):
        """Seconds with no input before the screensaver starts; None = Off. BATOPLEX_SAVER_S
        replaces it in a scripted test run."""
        if getattr(self.args, "script", None) and os.environ.get("BATOPLEX_SAVER_S"):
            return float(os.environ["BATOPLEX_SAVER_S"])
        m = self.settings["screensaver_min"]
        return m * 60.0 if m else None

    def _idle_tick(self):
        """Input activity: restart the idle timer; while the screensaver shows, close it and
        drop the press (a held direction does not repeat into the screen). Start+Select still
        quits."""
        if not self.inputs.take_activity():
            return
        self.idle_t = self.input_t = time.monotonic()
        if self.ambient.stop():
            self._glob = True         # the ribbon goes in the field of the press (a cut)
        if self.saver is None:
            if getattr(self.top, "saver_mode", False):     # the visualizer as screensaver
                keep = [(a, r) for a, r in self.inputs.queue
                        if a == "quit" or (self.music.active and a in MEDIA_KEYS)]
                self.inputs.flush()
                self.inputs.queue.extend(keep)
                self.top.close()
                L.info("screensaver off (visualizer)")
            return
        if getattr(self.saver, "preview", False):     # Left / Right, LB / RB: the rare moments
            q = list(self.inputs.queue)
            fresh = [a for a, rep in q if not rep]    # A repeat is consumed
            if q and all(a in ("lb", "rb", "left", "right") for a, _r in q) \
                    and all([self.saver.rare(1 if a in ("rb", "right") else -1) for a in fresh]):
                self.inputs.flush()
                return
        # Quit and the media keys pass (a media key wakes the screen and also acts). The
        # saver closes first: music it started (Weather) stops, and then a media key has no
        # music to act on and is dropped, not turned into OK on the screen under it.
        self.drop_saver()
        keep = [(a, r) for a, r in self.inputs.queue
                if a == "quit" or (self.music.active and a in MEDIA_KEYS)]
        self.inputs.flush()
        self.inputs.queue.extend(keep)
        self._snapshot()
        self.top.anim = True
        L.info("screensaver off")

    def _saver_tick(self):
        """No screensaver while a video session runs; the timer starts
        again when it ends."""
        if self.playback.session not in ("idle", "error"):
            self.idle_t = time.monotonic()
            if self.saver is not None:
                self.drop_saver()
                L.info("screensaver off (playback)")
            return
        delay = self.saver_delay_s()
        if any(getattr(scr, "no_saver", False) for scr in self.stack):
            self.idle_t = time.monotonic()     # sign-in (the link code) and screen margins,
            if self.saver is not None:         # also under a dialog; a sign-out closes it
                self.drop_saver()
                self._snapshot()
            return
        if self.saver is None and delay is not None and time.monotonic() - self.idle_t >= delay:
            self.start_saver()

    def drop_saver(self):
        """The screensaver ends: it stops what it started (the Weather playlist)."""
        sv, self.saver = self.saver, None
        if sv is not None:
            try:
                sv.close()
            except Exception:
                L.exception("screensaver close")

    def start_saver(self, kind=None, preview=False):
        """Start the screensaver now (the idle timer, or Preview with kind). During Music =
        Visualizer and a track on: the visualizer, when its engine runs."""
        from . import saveropts
        kind = kind or self.settings["screensaver_type"]
        word = "preview" if preview else "on"
        if kind in saveropts.SAVERS and kind not in saveropts.NO_MUSIC_ROW \
                and saveropts.get(self.settings, kind, "music") == "vis" \
                and self.music.state in ("playing", "loading") \
                and not self.visualizer.unavailable:
            self.idle_t = time.monotonic()
            L.info("screensaver %s (visualizer)", word)
            self.open_visualizer(saver=True)
            return
        from .saver import Screensaver
        self._snapshot()
        self.saver = Screensaver(self, kind=kind, preview=preview)
        self.idle_t = time.monotonic()
        L.info("screensaver %s (%s)", word, self.saver.type)

    # ------------------------------------------------------------ loop
    def run(self):
        pg = self.pg
        clock = pg.time.Clock()
        self.start()
        self._fails, self._fail_top = 0, None
        self._fail_run, self._fail_log_t = 0, 0.0
        test = bool(self.args.script or self.args.frames)
        while self.running:
            try:
                stop = self._step(clock)
            except Exception as e:
                if test or self._step_failed(e):
                    raise                    # a test sees the crash; 30 in a row: the app exits
                clock.tick(FPS)
                self.frame += 1
                if self.args.frames and self.frame >= self.args.frames:
                    break
                continue
            if self._fail_run:
                if self._fail_run > 1:
                    L.warning("frame failed %d times in a row, then recovered", self._fail_run)
                self._fail_run = 0
            self._fails, self._fail_top = 0, None
            if stop:
                break
        self.close()
        pg.quit()

    def _step_failed(self, e):
        """A fault in one frame must not end the app: log it, redraw in full next frame.
        Three faults in a row on the same top screen close that screen (never the root, and
        never while a video plays). True: FRAME_FATAL faults in a row, the caller re-raises.
        Only the first fault of a run logs its traceback."""
        self._fail_run += 1
        now = time.monotonic()
        if self._fail_run == 1:
            self._fail_start = now
        if now - getattr(self, "_fail_tb_t", -FAIL_LOG_GAP) >= FAIL_LOG_GAP:
            L.exception("frame failed")      # a full traceback at most every FAIL_LOG_GAP,
            self._fail_tb_t = self._fail_log_t = now   # also when faults and good frames alternate
        elif now - self._fail_log_t >= FAIL_LOG_GAP:
            L.warning("frame failed (%d in a row): %s: %s", self._fail_run, type(e).__name__, e)
            self._fail_log_t = now
        if self._fail_run >= FRAME_FATAL and now - self._fail_start >= FRAME_FATAL_S:
            L.error("frame failed %d times in a row: exit", self._fail_run)
            return True
        top = self.stack[-1] if self.stack else None
        if top is not None and top is self._fail_top:
            self._fails += 1
        else:
            self._fail_top, self._fails = top, 1
        self.base = None                     # a full pass next frame
        self.trans = None
        if self._fails >= FRAME_FAILS and len(self.stack) > 1 and not self.playing:
            L.warning("%s failed %d frames in a row: closed", type(top).__name__, self._fails)
            self._fails, self._fail_top = 0, None
            try:
                self.pop()
            except Exception:
                L.warning("pop after failed frames failed", exc_info=True)
                if len(self.stack) > 1 and self.stack[-1] is top:
                    self.stack.pop()
            self.base = None
        return False

    # ------------------------------------------------------------ input dispatch
    def _ok_ident(self):
        """The screen and its focused thing when OK went down: a pending OK that
        finds another one acts on nothing."""
        top = self.top
        try:
            return (id(top), top.focus_identity())
        except Exception:
            return (id(top), None)

    def _ok_event(self, a):
        """OK_DOWN / OK_UP -> "ok", "hold" or None."""
        if a == OK_DOWN:
            if self.playing or getattr(self.top, "ok_on_press", False):
                self._okp = None
                return "ok"                     # playback and key grids: at once
            self._okp = (time.monotonic(), self._ok_ident(), self.inputs.flushes)
            return None
        return self._ok_finish() if self._okp is not None else None

    def _ok_finish(self):
        """The pending OK ends: HOLD at OK_HOLD_MS or later, else OK. None when the
        screen or its focus changed, or the queue was flushed (saver, playback) meanwhile."""
        t0, ident, gen = self._okp
        self._okp = None
        if gen != self.inputs.flushes or ident != self._ok_ident():
            return None
        return HOLD if time.monotonic() - t0 >= OK_HOLD_MS / 1000.0 else "ok"

    def _ok_tick(self):
        """Once per frame: a held OK becomes HOLD at 0.5 s. A pending OK whose button went
        away (a device removed) is dropped."""
        if self._okp is None:
            return None
        if self.inputs.flushes != self._okp[2] or not self.inputs.is_held("ok"):
            self._okp = None
            return None
        if time.monotonic() - self._okp[0] >= OK_HOLD_MS / 1000.0:
            return self._ok_finish()
        return None

    def _dispatch(self, a, rep):
        """One action to playback or the top screen. True: leave the loop (quit)."""
        self.rep = rep                          # The panel wraps on a new press
        if a == "backspace" and (self.playing
                                 or not getattr(self.top, "takes_backspace", False)):
            a = "back"
        if self.playing:
            if not a.startswith("char:") and a != HOLD:
                self.playback.action(a, rep)    # every press reaches playback
            return False
        if a == "quit":
            self.running = False
            return True
        if self.music.active and a in MEDIA_KEYS:
            self.music_key(a)
            return False
        if a in ("playpause", "play"):          # remote Play on a menu screen = select
            a = "ok"
        elif a in ("pause", "stop", "next", "prev"):
            return False
        if a == HOLD:                           # The menu, else an OK
            top = self.top
            self._holding = True                # no own rows: no app-rows-only menu
            try:
                opened = top.open_options()
            finally:
                self._holding = False
            if opened:
                self._glob = True
                return False
            a = "ok"
        if a in MENU_SOUNDS:
            self.menu_sound(MENU_SOUNDS[a])
        if a == "menu":                         # Options, else the screen
            top = self.top
            if top.open_options():
                self._glob = True
            else:
                self._banded(lambda top=top: top.handle("menu"))
        else:
            self._banded(lambda a=a: self.top.handle(a))   # input changes the top screen
        return False

    def _step(self, clock):
        """One pass of the main loop. True: leave the loop."""
        pg = self.pg
        anim.tick()
        self._glob = False        # a change the screen does not know about: a full pass
        while self.posted:
            fn = self.posted.popleft()
            self._banded(fn)      # a worker result changed a screen: its band, else full
        for e in pg.event.get():
            if e.type == pg.QUIT:
                if not self.playing:
                    self.running = False
                else:
                    self._quit_after_play = True
            else:
                self.inputs.text = (not self.playing
                                    and bool(getattr(self.top, "takes_text", False)))
                self.inputs.handle(e)
        self.inputs.tick()
        self._run_script()
        self._idle_tick()
        while True:
            ev = self.inputs.pop_event()
            if ev is None:
                break
            a, rep = ev
            if a in (OK_DOWN, OK_UP):           # OK on release, or HOLD
                a, rep = self._ok_event(a), False
                if a is None:
                    continue
            elif self._okp is not None:         # 2: a pending OK goes first
                r = self._ok_finish()
                if r is not None and self._dispatch(r, False):
                    break
            if self._dispatch(a, rep):
                break
        r = self._ok_tick()
        if r is not None:
            self._dispatch(r, False)
        self.playback.tick()
        self.lineup.tick()
        # The first Watchlist load once the root screen has its rows
        self.watchlist.tick(bool(self.stack) and not getattr(self.stack[0], "loading", False))
        if self.lineup.version != getattr(self, "_lineup_v", None):
            self._lineup_v = self.lineup.version
            self.live_seed()
        while self.playback.notices:
            self.toast(self.playback.notices.popleft())
        self._saver_tick()
        self.audio.tick()
        self.music.tick()
        self._sleep_tick()
        self._visualizer_tick()
        if not self.playing:
            self._theme_tick()
        if self.playback.session != self._session:
            # A held button must not repeat into the next state (audit 5).
            self._session = self.playback.session
            self.inputs.flush()
        while True:
            res = self.playback.pop_result()
            if res is None:
                break
            self._end_playback(res)
        if not self.running:
            return True
        if self.playback.session in ("playing", "finishing", "up_next"):
            # mpv covers the screen: skip the UI draw, keep reading the pad. Images still
            # land, for the Up Next still.
            if self.images:
                self.images.pump()
            clock.tick(30)
            self.frame += 1
            return False
        if self.visualizer.state == "running":
            # The engine window covers the screen: keep the screen logic, skip the draw.
            self.top.update()
            if self.images:
                self.images.pump()
            clock.tick(30)
            self.frame += 1
            return bool(self.args.frames and self.frame >= self.args.frames)
        self._frame()
        clock.tick(FPS)
        self.frame += 1
        return bool(self.args.frames and self.frame >= self.args.frames)

    def _banded(self, fn):
        """Run fn (a press, a worker result) on the top screen. When the same screen stays on
        top and names the rectangle of every change fn made (screen.press_band()), that
        rectangle joins this frame's band; otherwise the frame is a full pass."""
        top, n = (self.top, len(self.stack)) if self.stack else (None, 0)
        if top is not None:
            top.press_band()                 # an old value never counts
        fn()
        pb = None
        if top is not None and self.stack and self.top is top and len(self.stack) == n:
            pb = top.press_band()
        if pb is None:
            self._glob = True
        else:
            self._pband = band_union([self._pband, pb] if self._pband is not None else [pb])

    def _frame(self):
        """One UI field. The top screen is drawn and filtered only when it
        changed: a band pass when it names the rows that changed (screen.band()), else a full
        pass. Transitions and the toast are composited after the filter, so they cost no
        filter pass."""
        pg = self.pg
        t0 = time.perf_counter()
        top = self.top
        dirty = bool(top.update())             # always called: its animations move
        band = top.band() if dirty else None
        from . import ambient, draw
        allowed = (self.ambient_ok and ambient.enabled() and self.saver is None
                   and self.trans is None and getattr(top, "ambient", False)
                   and self.playback.session in ("idle", "error"))
        if self.ambient.update(allowed, time.monotonic() - self.input_t, anim.now()):
            # the ribbon rows join the band (a full pass when the screen gave none)
            band = ambient.BAND if not dirty else (
                None if band is None else pg.Rect(band).union(ambient.BAND))
            dirty = True
        img = False
        sig = (id(top), len(self.stack), self.saver is not None)
        glob = self._glob or sig != self._sig
        self._sig = sig
        pband, self._pband = getattr(self, "_pband", None), None
        if self.saver is not None:
            dirty, pband = bool(self.saver.update()), None
            band = self.saver.band() if dirty else None
        if self.images:
            self.images.pump()
        iv = self.images.version if self.images else 0
        if iv != getattr(self, "_iv", None):
            self._iv, img = iv, True                 # an image landed
        if self.music.mark_version != self._music_v:   # a track change or pause redraws
            self._music_v, glob = self.music.mark_version, True
        minute = lineup.clock_hm()
        if minute != self.last_minute:
            self.last_minute, glob = minute, True
            from . import theme as ui_theme
            if ui_theme.month_changed():             # Ribbon: a new month, a new hue
                self.apply_theme()
        if getattr(self, "_redraw_n", 0):
            self._redraw_n -= 1
            glob = True
        mode = "off" if getattr(top, "raw_output", False) else self.settings["flicker_filter"]
        if mode != getattr(self, "_mode", None):
            self._mode, glob = mode, True
        self.redraw_gen = getattr(self, "redraw_gen", 0) + (1 if glob or img else 0)
        why, rows, td = "", 0, t0
        if glob or dirty or img or pband is not None or self.base is None:
            if self.saver is not None:
                self.saver.draw(self.canvas)
            else:
                top.draw(self.canvas)
            td = time.perf_counter()
            rect = None
            if not glob and self.base is not None:
                if self.saver is not None:
                    # an image that lands under the saver may show anywhere: a full pass
                    parts = [band] if dirty and not img else []
                else:
                    parts = (([band] if dirty else []) + ([pband] if pband is not None else [])
                             + ([top.image_band()] if img else []))
                rect = band_union(parts)
            if rect is not None:
                self.base = self.filter.apply_rect(self.canvas, rect, mode)
                rows = rect.h
                if self.dirty_check:
                    self._check_band(rect, mode)
            else:
                # The active saver mode says whether its frame is already filtered
                # (Digital Rain), not one fixed flag: album art over the rain is filtered.
                pre = self.saver is not None and self.saver.prefiltered
                self.base = self.filter.full(self.canvas, "off" if pre else mode)
                rows = 0 if pre else MODE_H
            why = "full" if rect is None else "band"
        tf = time.perf_counter()
        changed = bool(why)
        pre = self.base
        tr = self.trans
        if tr is not None:
            if self.comp is None:
                self.comp = draw.opaque_surface((MODE_W, MODE_H))
            self._compose(tr)
            pre, changed = self.comp, True
            if not tr["anim"].active and not tr["dim"].active:
                self.trans = None
        self.pre = pre
        out = pre
        alpha = self._toast_alpha() if self.saver is None else None
        if alpha is not None:
            layer, r = self._toast_layer(mode)
            if self.toast_out is None:
                self.toast_out = draw.opaque_surface((MODE_W, MODE_H))
            self.toast_out.blit(pre, (0, 0))
            layer.set_alpha(alpha)
            self.toast_out.blit(layer, (r[0], r[1] - 1))
            out = self.toast_out
        shown = (alpha, self._toast["key"] if self._toast else None)
        if shown != getattr(self, "_toast_shown", None):
            self._toast_shown, changed = shown, True
        self.last_out = out
        self._stats[1] += 1
        if changed:
            self._stats[0] += 1
            self.screen.blit(out, (0, 0))
            pg.display.flip()
            t1 = time.perf_counter()
            ms = (t1 - t0) * 1000
            L.debug("field %.1f ms: draw %.1f, filter %.1f (%s %d rows), comp %.1f%s",
                    ms, (td - t0) * 1000, (tf - td) * 1000, why or "none", rows,
                    (t1 - tf) * 1000, " trans" if tr is not None else "")
            if ms > 16:
                L.debug("slow field %.1f ms", ms)
            if self.saver is not None:
                self.saver.note_field(ms)
        if self._stats[1] >= 600:
            L.debug("drawn fields: %d of %d", self._stats[0], self._stats[1])
            self._stats = [0, 0]

    def _compose(self, tr):
        """A transition frame into self.comp. fade / menu: the old frame over the new one,
        alpha falling. panel: the panel column slides; the rest crossfades, and the strip the
        column has not reached yet gets the dim."""
        from . import draw, tokens
        comp, old, new = self.comp, tr["old"], self.base
        a = tr["anim"]
        if tr["kind"] in ("fade", "menu"):
            comp.blit(new, (0, 0))
            old.set_alpha(int(round(255 * (1 - a.value))))
            comp.blit(old, (0, 0))
            old.set_alpha(None)
            return
        w, x = tr["w"], int(a.value)
        p = tr["dim"].value
        plain, panel = (old, new) if tr["kind"] == "panel" else (new, old)
        o = p if tr["kind"] == "panel" else 1 - p
        comp.blit(plain, (0, 0))
        panel.set_alpha(int(round(255 * o)))
        comp.blit(panel, (w, 0), (w, 0, MODE_W - w, MODE_H))
        panel.set_alpha(None)
        sx = max(0, x + w)
        if sx < w and o > 0:
            draw.rect(comp, draw.C["bg"], (sx, 0, w - sx, MODE_H), alpha=int(tokens.SCRIM * o))
        if x + w > 0:
            comp.blit(panel, (x, 0), (0, 0, w, MODE_H))

    def _check_band(self, rect, mode):
        """BATOPLEX_DIRTY_CHECK=1: the band result must equal a full pass."""
        pg = self.pg
        ref = self.filter.apply(self.canvas, mode) if mode != "off" else self.canvas
        a, b = pg.image.tobytes(ref, "RGB"), pg.image.tobytes(self.base, "RGB")
        if a != b:
            row = MODE_W * 3
            bad = [y for y in range(MODE_H) if a[y * row:(y + 1) * row] != b[y * row:(y + 1) * row]]
            L.warning("dirty check: band %s differs from a full pass in rows %d-%d (%s)",
                      tuple(rect), bad[0], bad[-1], type(self.top).__name__)
            self.base = self.filter.full(self.canvas, mode)

    def close(self):
        """Stop the players. Also called after a fatal error, so no mpv is left playing."""
        self.visualizer.shutdown()
        self.music.stop(wait_s=1.5)        # the last music report, then the audio mpv goes
        self.playback.shutdown()           # final 'stopped' report on SIGTERM; mpv closed
        self.audio.shutdown()
