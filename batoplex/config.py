"""Paths, settings, and the secret store.

The data directory holds settings.json, auth.json (0600) and the artwork cache.
Default: $BATOPLEX_HOME, else /userdata/system/batoplex/data on Batocera, else ~/.batoplex.
"""
import json
import os
import tempfile
import threading
import uuid

from . import log, tokens

L = log.get("config")

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(APP_DIR, "fonts")
VERSION = "0.1.0"


def atomic_write(path, data, mode=None, fsync=True, prefix=None):
    """Replace path in one step: a temporary file in the same directory (unique per call),
    os.replace, and with fsync the file and the directory reach the disk first. mode: the
    file mode; None keeps the mode of the old file (0644 for a new one). data: str or bytes.
    A symlink at path is replaced, not followed (pass the realpath to write its target).
    prefix: of the temporary file name. Raises OSError; the temporary file is removed."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    d = os.path.dirname(path) or "."
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=prefix or "." + os.path.basename(path) + ".",
                               suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if fsync:
        try:
            dfd = os.open(d, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass                     # a file system that cannot sync a directory


def load_dict(path, name=None):
    """The JSON object in a state file, or None: missing, unreadable, not JSON, or JSON that
    is not an object (null, a list: a damaged or hand-edited file). The last two are logged."""
    try:
        with open(path) as f:
            d = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        L.info("%s not read: %s", name or os.path.basename(path), e)
        return None
    if not isinstance(d, dict):
        L.warning("%s holds %s, not an object: defaults used", name or os.path.basename(path),
                  type(d).__name__)
        return None
    return d


_mem_total = []


def mem_total(path="/proc/meminfo"):
    """MemTotal in bytes, or None when unknown (not Linux). Read once."""
    if not _mem_total:
        n = None
        try:
            with open(path) as f:
                for ln in f:
                    if ln.startswith("MemTotal:"):
                        n = int(ln.split()[1]) * 1024
                        break
        except (OSError, ValueError, IndexError):
            n = None
        _mem_total.append(n)
    return _mem_total[0]


LOW_RAM = 2 * 1024 ** 3


def low_ram():
    """True only on a box with less than 2 GB; unknown counts as not low (full quality)."""
    n = mem_total()
    return n is not None and n < LOW_RAM


def data_dir():
    d = os.environ.get("BATOPLEX_HOME")
    if not d:
        d = "/userdata/system/batoplex/data" if os.path.isdir("/userdata/system") else \
            os.path.expanduser("~/.batoplex")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


DEFAULTS = {
    "safe": dict(tokens.SAFE),
    "flicker_filter": "normal",       # off | light | normal
    "server_url": "",                 # optional override, e.g. http://192.168.1.20:32400
    "mpv": "mpv",
    "visualizer": "",                 # path override for the visualizer engine (tests)
    "mpv_extra": [],
    "sub_scale": 1.0,
    # Player Experience, with the Plex names and TV defaults (settings for this device)
    "skip_intro": "manual",           # disabled | manual | auto
    "skip_credits": "manual",         # disabled | manual | auto
    "auto_play": True,                # play the next episode after this one
    "up_next": "credits",             # credits (during final credits) | end (when video finishes)
    "autoplay_countdown_s": 15,       # 0 = Immediate; 5, 10, 15, 30, 60
    "passout_protection_h": 2,        # 0 = Never Prompt
    "rewind_on_resume_s": 0,          # 0 = None; seconds back when playback resumes from pause
    "theme_music": True,
    "theme_volume": 50,               # 25 low | 50 medium | 75 high
    "auto_sign_in": False,            # Plex Home: skip "Who's watching?" at launch
    # Screen Saver, per device: minutes with no input (0 = Off),
    # the type (saver.SAVERS), and the changed options of each type {type: {option: value}}.
    "screensaver_min": 5,
    "screensaver_type": "bounce",
    "saver_opts": {},
    # Video Quality, per device: auto | original | 8000 | 4000 | 2000 | 480p
    # (1.5 Mbps) | 720k.
    "quality_home": "original",
    "quality_remote": "auto",
    # Aspect of a picture wider than 4:3, per device: letterbox | 14:9 | zoom.
    # The Playback menu keeps a choice for each show or movie; this is the default.
    "aspect": "letterbox",
    # Video inside the Screen Margins: on when the user moves an edge, off on Reset. The
    # default margins are a guess for the menus, not a measurement of the set.
    "video_margins": False,
    # Night Audio, per device: quiet sounds up, loud sounds down, dialogue up.
    "night_audio": False,
    # Theme, per profile: stock | ribbon | midnight | hearth | console.
    "theme": "stock",
    "high_contrast": False,
    "menu_sounds": False,             # Shown only on a theme with sounds
    # Pinned and hidden libraries per Plex Home profile: {"default": {"pins": [...],
    # "hidden": [...]}}; each entry is {"server": machineIdentifier, "section": id}.
    "profiles": {},
}

# The Live TV pin (the side panel row): a pin entry with this server id, never a library.
LIVE_PIN = "livetv"
LIVE_ENTRY = {"server": LIVE_PIN, "section": 0}
# The Watchlist pin: the same kind of entry, never a library.
WATCH_PIN = "watchlist"
WATCH_ENTRY = {"server": WATCH_PIN, "section": 0}
SPECIAL_PINS = (LIVE_PIN, WATCH_PIN)

# Version 1 keys, still accepted in settings.json (hand edits) and moved into "profiles".
OLD_PIN_KEYS = ("library_order", "hidden_libraries")

# Player Experience and theme settings belong to the Plex Home user (stock Plex saves device
# settings per user). Screen, margins, flicker and mpv stay per device.
PROFILE_KEYS = ("skip_intro", "skip_credits", "auto_play", "up_next", "autoplay_countdown_s",
                "passout_protection_h", "rewind_on_resume_s", "theme_music", "theme_volume", "theme",
                "high_contrast", "menu_sounds")


# The Server Name and Posters types are now Bounce (Art = Posters, the default).
OLD_SAVER_TYPES = ("name", "posters")


def migrate_saver(v):
    """An old or unknown screensaver_type becomes Bounce; saver_opts must be a dict."""
    from . import saveropts
    if v.get("screensaver_type") in OLD_SAVER_TYPES or \
            v.get("screensaver_type") not in saveropts.SAVERS:
        v["screensaver_type"] = "bounce"
    if not isinstance(v.get("saver_opts"), dict):
        v["saver_opts"] = {}


def migrate_old_pins(v):
    """Early builds could save pins of a built-in sample library (server "demo"). Drop them;
    a profile left with no library pin seeds its libraries again."""
    for p in (v.get("profiles") or {}).values():
        if not isinstance(p, dict) or not isinstance(p.get("pins"), list):
            continue
        pins = [e for e in p["pins"] if not (isinstance(e, dict) and e.get("server") == "demo")]
        if len(pins) != len(p["pins"]):
            p["pins"] = pins
            if not any(isinstance(e, dict) and e.get("server") not in SPECIAL_PINS for e in pins):
                p["seeded"] = False


class Settings:
    # save() runs on the UI thread; migrate_pins() can run on a connect worker.
    _lock = threading.RLock()

    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "settings.json")
        self.v = json.loads(json.dumps(DEFAULTS))
        self.defaults = dict(DEFAULTS)       # set_default() can change one for this box
        self.saved = set()                   # keys the file holds
        self.removed = set()
        self._profile = "default"            # the Plex Home user key (uuid; "default" = owner)
        self.on_profile = None               # called after each profile set (the app: theme)
        d = load_dict(self.path)
        if d:
            self.v.update(d)
            self.saved = set(d)
        migrate_saver(self.v)
        migrate_old_pins(self.v)

    def set_default(self, k, val):
        """A default for this box only (a 240p screen: flicker filter Off). A value the user
        saved wins; save() writes the key only when it differs from this default."""
        with self._lock:
            self.defaults[k] = val
            if k not in self.saved:
                self.v[k] = val

    @property
    def profile(self):
        return self._profile

    @profile.setter
    def profile(self, key):
        self._profile = key
        if self.on_profile is not None:
            self.on_profile()

    def owner(self, k):
        """The account owner's value of a key, whatever profile is set."""
        return self.v.get(k, self.defaults.get(k))

    def __getitem__(self, k):
        if k in PROFILE_KEYS and self.profile != "default":
            prefs = (self.v.get("profiles") or {}).get(self.profile, {}).get("prefs") or {}
            return prefs.get(k, DEFAULTS[k])       # other users start from the defaults
        return self.v[k]                           # the owner keeps the top-level values

    def __setitem__(self, k, val):
        if k in PROFILE_KEYS and self.profile != "default":
            self._prof(self.profile).setdefault("prefs", {})[k] = val
            return
        self.v[k] = val

    def save(self):
        """Write only values that differ from the defaults, merged over the file on disk,
        so hand edits to other keys survive and new defaults still reach users. True when
        written; a full or read-only disk: False, the values stay in memory."""
        with self._lock:
            return self._save()

    def _save(self):
        disk = load_dict(self.path) or {}
        for k in self.removed:
            disk.pop(k, None)
        for k, v in self.v.items():
            if k in self.defaults and v == self.defaults[k]:
                disk.pop(k, None)
            else:
                disk[k] = v
        try:
            atomic_write(self.path, json.dumps(disk, indent=2))
        except OSError as e:             # a full or read-only disk: kept in memory only
            L.warning("settings not saved: %s", e)
            return False
        self.saved = set(disk)
        return True


    # -- library pins (per profile, per server)
    def _prof(self, profile):
        profs = self.v.setdefault("profiles", {})
        return profs.setdefault(profile or "default", {"pins": [], "hidden": []})

    def migrate_pins(self, server_id, profile=None):
        """Move version 1 keys (section ids of one server) into profiles. Runs whenever the
        old keys are present, so a hand edit of library_order still works. Needs the server
        id, so the provider calls it once it knows it. Returns True if anything moved."""
        with self._lock:
            return self._migrate_pins(server_id, profile)

    def _migrate_pins(self, server_id, profile=None):
        if not server_id or not any(k in self.v for k in OLD_PIN_KEYS):
            return False
        p = self._prof(profile)
        for old, new in (("library_order", "pins"), ("hidden_libraries", "hidden")):
            have = {(e.get("server"), e.get("section")) for e in p.setdefault(new, [])}
            for sid in self.v.pop(old, None) or []:
                try:
                    sid = int(sid)
                except (TypeError, ValueError):
                    continue
                if (server_id, sid) not in have:
                    p[new].append({"server": server_id, "section": sid})
                    have.add((server_id, sid))
            self.removed.add(old)
        try:
            self.save()
        except OSError:
            pass
        return True

    def pin_list(self, profile=None):
        """[(server_id, section_id)] of the profile, in panel order."""
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return [(e.get("server"), e.get("section")) for e in p.get("pins", [])
                if e.get("server") is not None and e.get("section") is not None]

    def set_pins(self, pins, profile=None):
        """pins: [(server, section)] or [{"server", "section", "title"?, "server_name"?}]."""
        p = self._prof(profile or self.profile)
        old = p.get("pins", [])
        p["pins"] = [dict(e) if isinstance(e, dict) else {"server": e[0], "section": e[1]}
                     for e in pins]
        for pin, flag in ((LIVE_PIN, "live_seeded"), (WATCH_PIN, "watch_seeded")):
            if any(e.get("server") == pin for e in old + p["pins"]):
                p[flag] = True                       # a user edit of the pin: no seed undoes it
        # an edit of the Live TV or Watchlist pin alone does not count as the library seed
        p["seeded"] = bool(p.get("seeded")) or any(e.get("server") not in SPECIAL_PINS
                                                   for e in p["pins"])

    def live_recent(self, profile=None):
        """Live TV: the last two channel ids of the profile, newest first."""
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return [c for c in p.get("live_recent", []) if isinstance(c, str)][:2]

    def set_live_recent(self, ids, profile=None):
        self._prof(profile or self.profile)["live_recent"] = list(ids)[:2]

    def pin_entries(self, profile=None):
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return [dict(e) for e in p.get("pins", []) if e.get("server") is not None]

    def seeded(self, profile=None):
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return bool(p.get("seeded"))

    # -- the Live TV pin: one entry {"server": "livetv", "section": 0} in the pin order
    def live_pinned(self, profile=None):
        return any(e.get("server") == LIVE_PIN for e in self.pin_entries(profile))

    def live_index(self, profile=None):
        """The number of library pins before the Live TV pin (its row's place among the
        libraries), or None when it is not pinned."""
        return self.special_index(LIVE_PIN, profile)

    def special_index(self, pin, profile=None):
        """The number of library pins before a special pin (Live TV, Watchlist), or None."""
        k = 0
        for e in self.pin_entries(profile):
            if e.get("server") == pin:
                return k
            if e.get("server") not in SPECIAL_PINS:
                k += 1
        return None

    def seed_live(self, profile=None):
        """Once per profile: pin Live TV last. Does not touch the library seed. True when the
        pin was added (the caller saves)."""
        p = self._prof(profile or self.profile)
        if p.get("live_seeded"):
            return False
        p["live_seeded"] = True
        if any(e.get("server") == LIVE_PIN for e in p.setdefault("pins", [])):
            return False
        p["pins"].append(dict(LIVE_ENTRY))
        return True

    # -- the Watchlist pin
    def watch_pinned(self, profile=None):
        return any(e.get("server") == WATCH_PIN for e in self.pin_entries(profile))

    def watch_index(self, profile=None):
        return self.special_index(WATCH_PIN, profile)

    def seed_watch(self, profile=None):
        """Once per profile: pin the Watchlist last. True when the pin was added (the caller
        saves)."""
        p = self._prof(profile or self.profile)
        if p.get("watch_seeded"):
            return False
        p["watch_seeded"] = True
        if any(e.get("server") == WATCH_PIN for e in p.setdefault("pins", [])):
            return False
        p["pins"].append(dict(WATCH_ENTRY))
        return True

    WATCH_SORTS = ("added", "title", "release")
    WATCH_FILTERS = ("all", "movie", "show")

    def watch_view(self, profile=None):
        """The Watchlist sort ("added", "title", "release"), its direction and the filter
        ("all", "movie", "show") of the profile."""
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        v = p.get("watch_view") or {}
        sort = v.get("sort") if v.get("sort") in self.WATCH_SORTS else "added"
        filt = v.get("filter") if v.get("filter") in self.WATCH_FILTERS else "all"
        desc = v.get("desc") if isinstance(v.get("desc"), bool) else sort != "title"
        return {"sort": sort, "desc": desc, "filter": filt}

    def set_watch_view(self, sort, desc, filt, profile=None):
        self._prof(profile or self.profile)["watch_view"] = {"sort": sort, "desc": bool(desc),
                                                             "filter": filt}

    def live_home(self, profile=None):
        """Live TV "Show on Home" (the On Right Now row), default On."""
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return p.get("live_home", True) is not False

    def set_live_home(self, on, profile=None):
        p = self._prof(profile or self.profile)
        if on:
            p.pop("live_home", None)
        else:
            p["live_home"] = False

    def pins(self, server_id, profile=None):
        """Pinned section ids of one server, in order."""
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return [e["section"] for e in p.get("pins", []) if e.get("server") == server_id]

    def hidden(self, server_id, profile=None):
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return {e["section"] for e in p.get("hidden", []) if e.get("server") == server_id}

    # -- recent searches (per profile, on this box; newest first)
    RECENT_MAX = 4

    def recent_searches(self, profile=None):
        p = (self.v.get("profiles") or {}).get(profile or self.profile) or {}
        return [q for q in p.get("recent_searches") or [] if isinstance(q, str) and q]

    def add_recent_search(self, q, profile=None):
        q = (q or "").strip()
        if not q:
            return
        p = self._prof(profile or self.profile)
        rest = [x for x in self.recent_searches(profile) if x.lower() != q.lower()]
        p["recent_searches"] = ([q] + rest)[:self.RECENT_MAX]

    def clear_recent_searches(self, profile=None):
        p = (self.v.get("profiles") or {}).get(profile or self.profile)
        if p:
            p.pop("recent_searches", None)


class Secrets:
    """auth.json: client id, user token, chosen server. Mode 0600, never logged."""

    def __init__(self, path=None):
        self.path = path or os.path.join(data_dir(), "auth.json")
        self.v = {}
        try:
            if os.path.exists(self.path):
                os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.v = load_dict(self.path, "auth.json") or {}
        changed = self.migrate()
        from . import log
        for tok in [self.v.get("account_token"), (self.v.get("profile") or {}).get("token")] + \
                [e.get("token") for e in (self.v.get("servers") or {}).values()]:
            if tok:
                log.add_secret(tok)                  # redacted from the log from the start
        if not self.v.get("client_id"):
            self.v["client_id"] = "batoplex-" + uuid.uuid4().hex
            changed = True
        if changed:
            self.save()

    # auth.json version 2: account_token, profile, home_server, and
    # servers {machineId: {name, token, url, owned, conn}}. Version 1 names still read and
    # write: they map to the account token and the home server entry.
    V1 = {"user_token": None, "server_token": "token", "server_url": "url",
          "server_name": "name"}

    def migrate(self):
        v = self.v
        v1 = ("user_token", "server_token", "server_url", "server_name", "server_id")
        if v.get("auth_version") == 2 and not any(k in v for k in v1):
            return False
        # Version 1 keys next to version 2 ones: an older build signed in again after a
        # rollback. Its keys are the newer ones, so they win.
        if "user_token" in v:
            v["account_token"] = v.pop("user_token")
        if any(k in v for k in ("server_token", "server_url", "server_name", "server_id")):
            sid = v.pop("server_id", None) or "legacy"
            entry = {"token": v.pop("server_token", None), "url": v.pop("server_url", None),
                     "name": v.pop("server_name", None)}
            v["servers"] = {sid: {k: x for k, x in entry.items() if x is not None}}
            v["home_server"] = sid
        v["auth_version"] = 2
        return True

    def home(self):
        return (self.v.get("servers") or {}).get(self.v.get("home_server")) or {}

    def get(self, k, default=None):
        if k == "user_token":
            return self.v.get("account_token", default)
        if k == "server_id":
            h = self.v.get("home_server")
            return default if h in (None, "legacy") else h
        if k in self.V1:
            return self.home().get(self.V1[k], default)
        return self.v.get(k, default)

    def set(self, **kw):
        for k, val in kw.items():
            if k == "user_token":
                self.v["account_token"] = val
            elif k == "server_id":
                servers = self.v.setdefault("servers", {})
                old = self.v.get("home_server")
                if old is not None and old != val and old in servers:
                    servers[val] = servers.pop(old)
                servers.setdefault(val, {})
                self.v["home_server"] = val
            elif k in self.V1:
                h = self.v.setdefault("home_server", "legacy")
                self.v.setdefault("servers", {}).setdefault(h, {})[self.V1[k]] = val
            else:
                self.v[k] = val
        self.save()

    def clear_login(self):
        for k in ("user_token", "server_token", "server_url", "server_name", "server_id",
                  "account_token", "profile", "servers", "home_server", "home_users_n",
                  "account_uuid"):
            self.v.pop(k, None)
        self.save()
        import shutil
        for d in ("avatars", "watchlist"):           # The Watchlist cache goes
            shutil.rmtree(os.path.join(data_dir(), "cache", d), ignore_errors=True)

    def save(self):
        """True when written. A full or read-only disk: False, the values stay in memory."""
        try:
            atomic_write(self.path, json.dumps(self.v), mode=0o600)   # a new file each time
        except OSError as e:
            L.warning("auth.json not saved: %s", e)
            return False
        return True
