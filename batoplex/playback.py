"""Playback session controller: the state machine between the UI and mpv.

No pygame and no sockets here. mpv comes from a factory, the clock and the worker runner are
injected, so tests drive every transition with a fake mpv and a fake clock.

Three separate pieces of state:
  session   IDLE, STARTING, PLAYING, UP_NEXT, FINISHING, ERROR
  facts     mirrors of mpv properties (paused, time, duration, eof, loaded)
  overlay   owned by overlay.py (later steps)

Rules (audit items 4, 5, 7, 18):
  - The controller takes every action in every state; nothing is dropped.
  - Each start has a generation number and its own mpv object and socket. A late result from
    a cancelled start only cleans up its own objects.
  - Leaving playback never waits for the network: the UI gets the local result at once and
    the final report runs in the background.
  - Each file is finalized once (stopped report, scrobble at 90 %).
"""
import collections
import copy
import os
import threading
import time
import uuid

from . import aspect, config, inputs, log, tracks
from .tokens import TOAST_HOLD
from .model import Queue
from .mpv import Cancelled, Mpv, safe_vo

L = log.get("playback")

IDLE, STARTING, PLAYING, UP_NEXT, FINISHING, ERROR = (
    "idle", "starting", "playing", "up_next", "finishing", "error")

START_TIMEOUT_S = 20.0
REPORT_EVERY_S = 10.0
WATCHED_AT = 0.90

# Playback controls.
SEEK_BACK_S, SEEK_FWD_S = -10, 30
SCRUB_STEP_S = 30            # per step while held; doubles after 2 s and again after 4 s
HOLD_S = 0.4                 # a press held this long starts scrubbing
SCRUB_EVERY_S = 0.25
KB_COMMIT_S = 0.4            # keyboard has no release event: seek after this quiet time
BAR_HIDE_S = 5.0
MINI_S = 2.0
TOAST_S = TOAST_HOLD / 1000.0   # the UI toast hold
ERROR_COPY = "Can't play this item."   # the cause goes to the log, not the screen
CHAPTERLESS_JUMP_S = 300     # LT/RT when the file has no chapters
REWIND_AFTER_PAUSE_S = 10    # Rewind on Resume applies after a pause at least this long
# Transcode
TC_SEEK_DEBOUNCE_S = 0.6     # seek taps add up, then one restart of the stream
TC_PING_S = 30               # keep a paused transcode alive
STALL_LIMIT_S = 8.0          # rule 6: buffering this long within STALL_WINDOW_S
STALL_WINDOW_S = 60.0
# Up Next (Plex Player Experience): passout protection only after a long video, and a prompt
# with no answer stops playback after this time.
PASSOUT_MIN_VIDEO_S = 20 * 60
PASSOUT_ANSWER_S = 300
# Live TV: a channel session is live. No seek, no watch state, no Up Next.
CH_WAIT_S = 0.15             # Up/Down: only the channel of the last press loads
BANNER_CHANGE_S = 4.0        # the info banner after a channel change
BANNER_A_S = 5.0             # ... after A
CH_SKIPS = 4                 # items that do not play, in a row, before the channel gives up
PREFETCH_S = 2.0             # on a channel this long: play_info of the channels next to it
NEXT_EARLY_S = 30.0          # this long before the end of an item: play_info of the next one
PREFETCH_TTL_S = 300.0       # a fetched-ahead play_info is used up to this age
PREFETCH_MAX = 4             # results kept (two neighbours, the next item, one spare)
PREFETCH_RETRY_S = 60.0      # a fetch ahead that failed waits this long before it tries again
# Stock Plex: Select with the controls hidden opens them
# with Pause focused and does not pause; Back stops playback (Xbox "B: stops playback").

PLAYPAUSE, PLAY, PAUSE, STOP, NEXT, PREV = "playpause", "play", "pause", "stop", "next", "prev"

# The keyboard and USB remotes go to mpv (it has focus). Each key sends a script-message to
# the app, so keyboard and pad take one path through action(). Arrows repeat while held.
# Space is Options/Menu, as in the UI: air-mouse remotes send Space for their Options key.
_KEYS = [
    ("ESC", "back"), ("BS", "back"), ("q", "back"), ("Alt+F4", "quit"),
    ("ENTER", "ok"), ("KP_ENTER", "ok"), ("SPACE", "menu"), ("m", "menu"), ("MENU", "menu"),
    ("LEFT", "left"), ("RIGHT", "right"), ("UP", "up"), ("DOWN", "down"),
    ("x", "x"), ("y", "y"), ("s", "y"), ("a", "x"),
    ("PGUP", "lb"), ("PGDWN", "rb"), ("[", "lt"), ("]", "rt"),
    ("p", PLAYPAUSE), ("PLAYPAUSE", PLAYPAUSE), ("PLAY", PLAY), ("PAUSE", PAUSE),
    ("PLAYONLY", PLAY), ("PAUSEONLY", PAUSE), ("STOP", STOP), ("NEXT", NEXT), ("PREV", PREV),
    ("FORWARD", "right"), ("REWIND", "left"),
]
INPUT_CONF = "".join("%s %sscript-message bp %s\n" % (
    k, "repeatable " if a in ("left", "right", "up", "down") else "", a) for k, a in _KEYS)


def _make_overlay(cmd, settings):
    from .overlay import OverlayRenderer
    return OverlayRenderer(cmd, settings)


# Night Audio: the downmix to stereo here, with the centre (dialogue) channel at full level and
# no LFE; then a compressor with makeup gain; then a limiter so nothing clips. ochl is the
# FFmpeg 7 name of the output layout option.
NIGHT_AF = ("lavfi=[aresample=ochl=stereo:clev=1.0:slev=0.5:lfe_mix_level=0],"
            "lavfi=[acompressor=threshold=-24dB:ratio=4:attack=5:release=250:makeup=8dB],"
            "lavfi=[alimiter=limit=-1dB:level=disabled]")


def _sub_scale(settings):
    try:
        return float(settings["sub_scale"])
    except (KeyError, TypeError, ValueError):
        return config.DEFAULTS["sub_scale"]


def video_args(binary, settings, input_conf, version, safe_mode=False, vo="x11"):
    v = version or (0, 0)
    # Sizes are mpv "scaled pixels" (720-line reference). 480 lines -> x 2/3.
    safe = settings["safe"]          # live: follows calibration changes
    bottom = int(safe["bottom"] * 1.5) + 12
    side = int(safe["left"] * 1.5)
    a = [
        binary, "--no-config", "--idle=yes", "--force-window=immediate",
        "--input-conf=" + input_conf,
        "--no-input-default-bindings", "--no-osc", "--msg-level=all=error",
        "--fs", "--keepaspect=yes", "--panscan=0", "--video-unscaled=no",
        "--keep-open=yes", "--ytdl=no", "--title=Batoplex",
        "--hwdec=auto-safe", "--audio-channels=stereo",
        "--deinterlace=" + ("auto" if v >= (0, 38) else "no"),
        "--osd-font=Barlow SemiBold", "--osd-font-size=40", "--osd-bar-h=4",
        "--osd-margin-x=%d" % side, "--osd-margin-y=%d" % bottom,
        "--sub-font=Barlow SemiBold", "--sub-font-size=%d" % int(50 * _sub_scale(settings)),
        "--sub-border-size=3", "--sub-margin-y=%d" % bottom, "--sub-margin-x=%d" % side,
        "--sub-auto=no", "--cursor-autohide=always", "--x11-bypass-compositor=yes",
    ]
    if v >= (0, 37):
        a.append("--sub-fonts-dir=" + config.FONT_DIR)
    try:
        night = settings["night_audio"]
    except KeyError:
        night = False
    if night:
        a.append("--af=" + NIGHT_AF)
    if config.low_ram():
        # Under 2 GB only: mpv's default demuxer cache (150 MiB + 50 MiB back) can push a
        # small box into swap. Unknown or 2 GB and up: mpv defaults, unchanged.
        a += ["--demuxer-max-bytes=48MiB", "--demuxer-max-back-bytes=16MiB"]
    a += list(settings["mpv_extra"])
    if safe_mode:
        a += ["--vo=" + vo, "--hwdec=no"]
    return a


def _thread_runner(fn, done, fail):
    """Default worker: run fn on a thread; done/fail are called on that thread and must
    only post into the controller inbox (they do)."""
    def work():
        try:
            r = fn()
        except Exception as e:
            fail(e)
            return
        done(r)
    threading.Thread(target=work, daemon=True).start()


def build_queue(provider, item):
    """Season order: this episode and the rest of its season, then the first episode of the
    next season (not Specials). Movies and anything else: a queue of one."""
    if item.type != "episode" or not item.season_id:
        return Queue([item])
    eps = provider.episodes(item.season_id)
    ids = [e.id for e in eps]
    if item.id not in ids:
        return Queue([item])
    items, index = list(eps), ids.index(item.id)        # the whole season: Previous works too
    if item.show_id and item.season_index:
        try:
            later = sorted((x for x in provider.seasons(item.show_id)
                            if x.index and x.index > item.season_index), key=lambda x: x.index)
            nxt = provider.episodes(later[0].id) if later else []
            if nxt:
                items.append(nxt[0])
        except Exception as e:            # the rest of this season still plays on
            L.info("next season lookup failed: %s", e)
    return Queue(items, index)


def _quality_labels():
    """The playback menu's Quality choices: Original, then the targets best first (576p
    labels on a PAL screen)."""
    from .plex import quality_choices
    return [lbl for k, lbl in quality_choices() if k != "auto"]


def _step_down(cur, provider, src_kbps=0):
    """Rule 6: the next target below cur ("" = Original): from Original the Automatic pick
    of the last measurement, else 1.5 Mbps, and never a target at or above the file's own
    bitrate (no gain, only a restart); None when there is no lower target."""
    from . import plex
    ids = list(plex.QUALITY_KBPS)
    labels = [plex.quality(k)["label"] for k in ids]
    if not cur:
        got = plex._MEASURED.get(getattr(provider, "base", None))
        pick = plex.auto_quality(got[0])["id"] if got and got[0] else "480p"
        i = ids.index(pick)
        if src_kbps:
            while i < len(ids) and plex.QUALITY_KBPS[ids[i]] >= src_kbps:
                i += 1
        return labels[i] if i < len(ids) else None
    i = labels.index(cur) if cur in labels else len(labels) - 1
    return labels[i + 1] if i + 1 < len(labels) else None


class Session:
    """One start: its generation, mpv, cancel flag. Never shared between starts."""

    def __init__(self, gen, item, provider, resume):
        self.gen = gen
        self.item = item
        self.provider = provider
        self.resume = resume
        self.cancel = threading.Event()
        self.user_quality = False     # a Quality menu choice: rule 6 stays off for this start
        self.restart_busy = False     # a quality restart waits for play_info
        self.mpv = None           # set on the UI thread once mpv is connected
        self.spawned = None       # set by the worker as soon as the process exists
        self.pi = None
        self.full = None
        self.plex_session = str(uuid.uuid4())
        self.finalized = False
        self.sub = self.audio = None   # the chosen tracks (set once the file loads)
        self.opts = {}                 # play_info options: mode, retry
        self.base_s = 0.0              # transcode: the stream offset; mpv time 0 is this
        self.tc_tried = False          # rule 5 or 6 already moved this file to transcode
        self.tc_stopped = False
        self.stalls = []               # rule 6: [(start, end)] of paused-for-cache
        self.stall_at = None
        self.last_ping = 0.0
        self.channel = None            # Live TV: the lineup.Tune this session plays


class Prefetch:
    """play_info results fetched ahead for a channel session: the channels
    next to this one and the next item. Direct play only: a transcode is not started in
    advance. A hit is the same tape child (channel, airing start, child, entry), younger than
    PREFETCH_TTL_S, from the same provider and the same lineup version (gen); the offset is
    the one of the tune that takes it."""

    def __init__(self, clock):
        self.clock = clock
        self.items = collections.OrderedDict()   # key -> (fetched at, prov, gen, (pi, full))
        self.failed = {}                         # key -> failed at
        self.busy = None                         # the key of the fetch on its way

    @staticmethod
    def key(t):
        return (t.cid, t.a_start, t.c, t.entry.key)

    def _fresh(self, v, prov, gen):
        return self.clock() - v[0] <= PREFETCH_TTL_S and v[1] is prov and v[2] == gen

    def wanted(self, t, prov, gen):
        """True when tune t has no usable result and no recent failure."""
        k = self.key(t)
        v = self.items.get(k)
        if v is not None and self._fresh(v, prov, gen):
            return False
        f = self.failed.get(k)
        return f is None or self.clock() - f >= PREFETCH_RETRY_S

    def put(self, t, prov, gen, r):
        """Keep result r = (pi, full); False for a transcode (not kept)."""
        if r[0].mode == "transcode":
            return False
        k = self.key(t)
        self.items.pop(k, None)
        self.items[k] = (self.clock(), prov, gen, r)
        while len(self.items) > PREFETCH_MAX:
            self.items.popitem(last=False)
        self.failed.pop(k, None)
        return True

    def fail(self, t):
        self.failed[self.key(t)] = self.clock()

    def take(self, t, prov, gen):
        """(pi, full) for tune t, with its offset, or None. A result is used once."""
        v = self.items.pop(self.key(t), None)
        if v is None or not self._fresh(v, prov, gen):
            return None
        pi = copy.copy(v[3][0])
        pi.start_s = max(0.0, float(t.at))
        return pi, v[3][1]


def _refusal(e):
    """Screen copy for a server that will not convert, else None. The refusal with no
    code is written for the screen (plex.py); other causes stay in the log only."""
    if not isinstance(e, getattr(plex_mod(), "TranscodeRefused", ())):
        return None
    screen = getattr(e, "screen", False) or getattr(e, "code", None) is None
    return str(e) if screen else "The server can't convert this video"


def _no_state(s):
    """An extra: plays from the start, direct only, and tells the server
    nothing: no timeline, no scrobble, no files for the UI."""
    return s is not None and bool(s.item.extra.get("no_state"))


def _play_info(prov, item, resume, opts):
    """Options go only to a provider that can transcode (test fakes cannot)."""
    if hasattr(prov, "transcode_choice"):
        return prov.play_info(item, resume=resume, opts=opts)
    return prov.play_info(item, resume=resume)


class PlaybackController:
    def __init__(self, settings, binary=None, clock=time.monotonic, run=_thread_runner,
                 make_mpv=None, memory=None, profile=None, make_overlay=_make_overlay):
        self.settings = settings
        self.binary = binary or settings["mpv"]
        self.clock = clock
        self.run = run
        self.make_mpv = make_mpv or (lambda: Mpv(self.binary, "mpv"))
        probe = self.make_mpv() if make_mpv is None else None
        self.version = probe.version if probe else (0, 41)
        self._available = probe.available if probe else True
        self.memory = memory
        self.profile = profile
        self.make_overlay = make_overlay
        self.is_held = lambda a: False         # the app sets inputs.is_held
        self.sleep = None                      # the app sets its sleep.SleepTimer
        self.image = None                      # the app sets (ref, w, h) -> surface or None
        self.provider_for = None               # the app: item -> its server's provider or None
        self.fixed_fails = 0                   # fixed queue: items in a row that did not play
        self.notices = collections.deque()     # toasts for the app (the overlay can be gone)
        self.live = None                       # Live TV: the lineup.Surfer of a channel session
        self.guide_view = None                 # the app: channel id -> guide.PictureGuide
        self.ch = None                         # Live TV: the channel session state
        self.files, self.queue, self.last_input = [], None, 0.0
        self.inbox = collections.deque()     # (gen, fn) from worker threads
        self.gen = 0
        self.attempt = 0                     # unique socket path for every spawn, retries too
        self.finals = []                     # threading.Event per background final report
        self.s = None                        # current Session
        self.session = IDLE
        self.safe_mode = False
        self.message = ""                    # ERROR text for the UI (plain copy)
        self.cause = ""                      # the raw ERROR cause (log and result only)
        self.results = collections.deque()   # finished sessions for the app
        self.input_conf = os.path.join(config.data_dir(), "mpv-input.conf")
        try:
            with open(self.input_conf, "w") as f:
                f.write(INPUT_CONF)
        except OSError:
            pass
        self._reset_facts()

    @property
    def available(self):
        return self._available

    def _reset_facts(self):
        self.paused = False
        self.paused_at = 0.0
        self.user_paused = False             # the pause came from the user (not keep-open)
        self.time_s = 0.0
        self.duration_s = 0.0
        self.loaded = False
        self.eof = False
        self.started_at = self.clock()
        self.last_report = -1e9
        self.report_pause = None
        self.user_stop = False
        self.chapters = 0
        self.chapter = None
        self.chapter_list = []               # [(start_s, title)] from mpv
        self.ov = None
        self.bar = False
        self.bar_until = 0.0
        self.bar_row = "seek"
        self.bar_btn = 0
        self.mini_until = 0.0
        self.toast = None                    # (text, until)
        self.scrub = None
        self.card = False                    # start card shown as an overlay
        self._margin = None
        self.upnext = None                   # the Up Next card while session == UP_NEXT
        self.upnext_done = False             # the user went back to the video in the credits
        self.shrunk = False
        self.switching = False               # next file requested; old-file events ignored
        self.queue_stale = False             # the queue ends here: build it again on load
        self.skip = None                     # {"mk", "label", "until"} while the button shows
        self.panel = None                    # track panel: {"tab": "sub"|"audio", "focus", "top"}
        self.pmenu = None                    # playback menu: {"rows": [(key, label)], "focus"}
        self.marker_seen = set()             # markers already entered in this file
        self.prev_time = None                # time-pos before the latest change (natural entry)
        self.pos_fresh = False               # time_s came from mpv (not set by us after a seek)
        self.tc_seek = None                  # transcode: {"t", "at"} a restart waits for taps
        self.reapply = False                 # direct restart: apply s.audio/s.sub on load
        self.aspect = "letterbox"            # Aspect of this file (aspect.MODES)
        self.vparams = None                  # mpv video-params of this file
        self.crop = None                     # (w, h, x, y) bars cut by video-crop
        self.cropjob = None                  # {"state", "at", "tries", "wait"} bar detection
        # Bars found in this file as (x, y) parts of the frame; (0, 0) when it has none.
        # Kept for the whole file: a quality change or a new Aspect does not look again.
        self.bars = None
        self.moved_at = None                 # when the picture of this load started to move

    # ------------------------------------------------------------ inbox
    def _post(self, gen, fn):
        self.inbox.append((gen, fn))

    def _drain(self):
        while self.inbox:
            gen, fn = self.inbox.popleft()
            fn(gen == self.gen)

    # ------------------------------------------------------------ start
    def play(self, item, provider, resume=True, queue=None):
        """Begin a session. Returns at once; the UI shows the start card. queue: a fixed queue
        (a playlist or the local video queue) whose current item is item."""
        if self.session not in (IDLE, ERROR):
            L.info("play ignored in state %s", self.session)
            return
        self.live = self.ch = None
        self._begin(Session(self.gen + 1, item, provider, resume), queue)

    def play_channel(self, tune, provider, surfer):
        """A Live TV channel session: tune.item from tune.at s,
        no resume point, no watch state. surfer: the lineup.Surfer for Up/Down and the banner."""
        if self.session not in (IDLE, ERROR):
            return
        self.live = surfer
        self.ch = {"want": None, "due": 0.0, "busy": False, "banner": None, "until": 0.0,
                   "paused": False, "bad": set(), "skips": 0, "guide": False, "target": None,
                   "pf": Prefetch(self.clock), "pf_next": 0.0, "settled": None, "dir": 1}
        s = Session(self.gen + 1, tune.item, provider, False)
        s.channel, s.opts = tune, {"at_s": tune.at}
        surfer.note(tune.cid)
        self._begin(s, None)
        self._banner(tune.cid, BANNER_CHANGE_S)

    def _begin(self, s, queue):
        self.safe_mode = False               # a new play request: hardware decode again
        self.files = []                      # finished files of this session, for the UI
        self.queue = queue
        self.fixed_fails = 0
        self.last_input = self.clock()
        self.gen += 1
        self.s = s
        self.session = STARTING
        self.message = ""
        self._reset_facts()
        self._spawn(s)

    def _spawn(self, s):
        gen = s.gen
        safe = self.safe_mode
        self.attempt += 1
        sock = "/tmp/batoplex-mpv-%d-%d-%d.sock" % (os.getpid(), gen, self.attempt)

        def work():
            if s.pi is None:
                s.pi, s.full = _play_info(s.provider, s.item, s.resume,
                                          dict(s.opts, session=s.plex_session))
            if s.cancel.is_set():
                raise Cancelled()
            m = self.make_mpv()
            s.spawned = m
            m.start(video_args(self.binary, self.settings, self.input_conf, self.version, safe,
                               safe_vo(self.binary)), sock, cancel=s.cancel)
            return m

        def done(m):
            self._post(gen, lambda current: self._started(s, m) if current
                       else self._discard(s))

        def fail(e):
            self._post(gen, lambda current: self._start_failed(s, e) if current
                       else self._discard(s))
        self.run(work, done, fail)

    def _discard(self, s):
        """A cancelled or replaced start finished late: clean up its own mpv only."""
        for m in {id(x): x for x in (s.mpv, s.spawned) if x is not None}.values():
            threading.Thread(target=m.kill, daemon=True).start()
        stop_tc = self._tc_stopper(s)        # a transcode that never got its final report
        if stop_tc:
            self.run(stop_tc, lambda r: None, lambda e: L.info("transcode stop failed: %s", e))

    def _start_failed(self, s, e):
        if isinstance(e, Cancelled):
            return
        if s.spawned is not None and not self.safe_mode and s.pi is not None:
            L.info("mpv start failed (%s); retry with --vo=%s --hwdec=no", e,
                   safe_vo(self.binary))
            self._discard(s)
            s.tc_stopped = False
            self.safe_mode = True
            self._drop_overlay()
            s.mpv = s.spawned = None
            self._spawn(s)
            return
        self._error(str(e) if s.pi is None else "mpv: %s" % e, copy=_refusal(e))

    def _started(self, s, m):
        s.mpv = m
        pi = s.pi
        self.duration_s = pi.duration_ms / 1000.0
        self.ov = self.make_overlay(m.cmd, self.settings) if self.make_overlay else None
        m.observe("time-pos", "pause", "duration", "eof-reached", "chapters", "chapter",
                  "chapter-list", "paused-for-cache", "video-params")
        self._load(s, m)

    def _load(self, s, m):
        """Open the file of session s in mpv m (a new mpv, or the same one for Up Next)."""
        pi = s.pi
        self.card = True
        self.duration_s = pi.duration_ms / 1000.0
        if pi.headers:
            m.set("http-header-fields", ",".join("%s: %s" % kv for kv in pi.headers.items()))
        # A transcode keeps the source's pixel aspect on a picture it scaled to square
        # pixels: give mpv the source shape. Direct play: the file's own (-1). Set on every
        # load, because the option stays on the mpv for the next file.
        tc_aspect = pi.mode == "transcode" and getattr(pi, "src_aspect", 0) > 0
        m.set("video-aspect-override", "%.6f" % pi.src_aspect if tc_aspect else "-1")
        if pi.mode == "transcode":
            # The stream starts at offset_s; mpv counts from 0.
            s.base_s = pi.offset_s
            m.set("start", "none")
            m.set("rebase-start-time", "yes")
        else:
            s.base_s = 0.0
            m.set("start", "%.1f" % pi.start_s if pi.start_s > 1 else "none")
        if pi.notice:
            self.say(pi.notice, 4.0)
        m.set("force-media-title", pi.title or s.item.title)
        m.set("pause", False)                # keep-open pauses at the end of the last file
        m.set("aid", "auto")                 # aid is also an option: never carry it over
        self._aspect_reset(s, m)
        if not self.shrunk:                  # a restart under Up Next keeps the small box
            self._set_video_box(m, self._video_box())
        m.cmd("loadfile", pi.url, "replace")
        L.info("play %s (%s) start=%.0fs %s%s", s.item.title, s.item.id, pi.start_s, pi.mode,
               " %s (%s)" % (pi.quality, pi.reason) if pi.mode == "transcode" else "")

    # ------------------------------------------------------------ per frame
    def tick(self):
        self._drain()
        s = self.s
        if s is None or self.session in (IDLE, ERROR):
            return
        m = s.mpv
        if m is not None:
            for ev in m.events():
                self._event(s, ev)
                if self.s is not s:
                    return
        m = s.mpv              # an event can replace it (a retry spawns a new mpv)
        if self.session == STARTING and not self.loaded:
            if self.clock() - self.started_at > START_TIMEOUT_S:
                self._error("The video did not start.")
                return
            if m is not None and not m.alive:
                self._lost(s)
                return
        if self.session == PLAYING:
            if m is None or not m.alive:
                self._lost(s)
                return
            self._scrub_tick()
            self._tc_tick(s)
            if self.s is not s:
                return
            self._report_tick()
            self._crop_tick(s)
            self._end_pause_check(s)
            if self.session != PLAYING:
                return
            if s.channel is not None:
                self._channel_tick(s)
                if self.s is not s:
                    return
            else:
                self._credits_check(s)
                if self.session == PLAYING:
                    self._marker_tick(s)
        elif self.session == UP_NEXT:
            if m is None or not m.alive:
                self._finish(s)
                return
            if not self.eof:
                self._report_tick()          # the credits still play in the small video
            self._upnext_tick(s)
        if self.s is s:
            try:
                self._render()
            except Exception:
                # A drawing fault must never stop playback; log it once per session.
                if not getattr(s, "render_failed", False):
                    s.render_failed = True
                    L.exception("overlay render failed")

    def _event(self, s, ev):
        name = ev.get("event")
        if self.switching:
            # After loadfile replace, events of the old file are still queued: drop them
            # until mpv starts the new file.
            if name in ("start-file", "file-loaded"):
                self.switching = False
            elif name not in ("client-message", "shutdown", "ipc-closed"):
                return
        if name == "property-change":
            p, d = ev.get("name"), ev.get("data")
            if p == "time-pos" and d is not None:
                if self.moved_at is None and float(d) > 0:
                    self.moved_at = self.clock()
                # prev_time: the previous position mpv reported, or None after a local seek
                if self.tc_seek is not None:
                    return                   # a transcode seek waits: keep its target
                self.prev_time = self.time_s if self.pos_fresh else None
                self.time_s, self.pos_fresh = float(d) + s.base_s, True
            elif p == "paused-for-cache" and d is not None:
                self._stall(s, bool(d))
            elif p == "duration" and d and s.pi is not None and s.pi.mode == "transcode":
                pass                         # the stream length from offset: keep Plex's
            elif p == "duration" and d:
                # A live or growing stream reports the length read so far: never go below
                # the server's duration, or every stop would count as watched.
                plex = s.pi.duration_ms / 1000.0 if s.pi else 0.0
                self.duration_s = max(float(d), plex)
            elif p == "pause" and d is not None:
                if d and not self.paused:
                    self.paused_at = self.clock()
                self.paused = bool(d)
            elif p == "eof-reached" and d:
                if self.session == UP_NEXT:
                    self.eof = True          # the credits ended under the card
                    if self.upnext and self.upnext["focus"] == "video":
                        self.upnext["focus"] = "next"
                else:
                    self._eof(s)
            elif p == "chapters":
                self.chapters = int(d or 0)
            elif p == "chapter":
                self.chapter = d
            elif p == "video-params" and isinstance(d, dict):
                self.vparams = d
                self._crop_sync(s)                   # a new stream size: the same bars
            elif p == "chapter-list":
                self.chapter_list = [(float(c.get("time") or 0), c.get("title") or "")
                                     for c in (d or []) if isinstance(c, dict)]
        elif name == "file-loaded":
            self.loaded = True
            self.card = False
            if s.mpv is not None:
                # keep-open pauses mpv at the end of the old file, and the change of pause can
                # come while old-file events are dropped: put mpv in the app's state.
                s.mpv.set("pause", self.paused)
            if self.reapply:
                self.reapply = False
                self._reapply_tracks(s)      # a restart in direct mode: the user's tracks
            if self.session == STARTING:
                self.session = PLAYING
                self.fixed_fails = 0             # a fixed queue: the failures in a row end
                if s.channel is not None and self.ch is not None:
                    self.ch["skips"] = 0         # CH_SKIPS counts items in a row
                    if self.ch["settled"] is None:
                        self.ch["settled"] = self.clock()   # PREFETCH_S counts from here
                self._select_tracks(s)
                if self.queue is None or self.queue_stale:
                    self.queue_stale = False
                    self._build_queue(s)          # extends the old queue when it lands
        elif name == "end-file":
            reason = ev.get("reason")
            if reason == "eof":
                self._eof(s)
            elif reason == "error":
                err = ev.get("file_error") or "playback error"
                L.info("mpv end-file error: %s", err)
                if s.channel is not None and self.loaded and self._channel_skip(s, err):
                    pass
                elif self.loaded:
                    if not self._fixed_fail(s, self.queue.index if self.queue else 0, err):
                        self._finish(s, error=err)
                elif not self.safe_mode:
                    L.info("file failed to load; retry with --vo=%s --hwdec=no",
                           safe_vo(self.binary))
                    self._discard(s)
                    s.tc_stopped = False         # the retry starts the same transcode again
                    self.safe_mode = True
                    self._drop_overlay()
                    s.mpv = s.spawned = None
                    self.started_at = self.clock()
                    self._spawn(s)
                elif not self._retry_transcode(s):
                    self._error("Can't play this file: %s" % err)
        elif name == "client-message":
            args = ev.get("args") or []
            if len(args) == 2 and args[0] == "bp":
                self.action(args[1], False, src="kb")
        elif name in ("shutdown", "ipc-closed"):
            self._lost(s)

    def _lost(self, s):
        """mpv went away by itself (keyboard quit, crash, window closed)."""
        if self.session in (PLAYING, UP_NEXT) or (self.loaded and self.session == STARTING):
            self._finish(s, error=None if (self.user_stop or self.loaded) else "mpv stopped")
        elif self.session == STARTING:
            if not self.safe_mode and s.pi is not None:
                L.info("mpv ended before the file loaded; retry with --vo=%s --hwdec=no",
                       safe_vo(self.binary))
                self._discard(s)
                s.tc_stopped = False
                self.safe_mode = True
                self._drop_overlay()
                s.mpv = s.spawned = None
                self.started_at = self.clock()
                self._spawn(s)
            elif not self._retry_transcode(s):
                self._error("mpv stopped before playback.")

    def _eof(self, s):
        if self.session != PLAYING:
            return
        if s.channel is not None:
            self._channel_next(s)
            return
        self.eof = True
        # A stream that drops reports eof too: only a real end (or the credits) goes on.
        if self._next_item() is not None and self._near_end(s):
            self._open_upnext(s)
        else:
            self._finish(s)

    def _end_pause_check(self, s):
        """keep-open pauses at the end of a file. mpv does not always say eof-reached then (a
        movie with an external subtitle, seen in e2e run 15): a pause the user did not ask
        for, at the end, half a second on, is the end of the file."""
        d = self.duration_s
        if (self.paused and not self.user_paused and not self.eof and d > 1 and self.loaded
                and self.time_s >= d - 1.0 and self.clock() - self.paused_at >= 0.5
                and self.tc_seek is None and not self.scrub):
            L.info("paused at the end: end of file")
            self._eof(s)

    def _near_end(self, s):
        dur = self.duration_s or (s.pi.duration_ms / 1000.0 if s.pi else 0)
        mk = self._credits_marker(s)
        return (dur <= 0 or self.time_s / dur >= WATCHED_AT
                or (mk is not None and self.time_s >= mk.start_ms / 1000.0 - 1))

    # ------------------------------------------------------------ queue and Up Next
    def _build_queue(self, s):
        if s.channel is not None:
            return                            # a channel has no queue: the tape is next
        it = s.full or s.item
        if it.type != "episode":
            self.queue = Queue([it])
            return
        prov = s.provider
        self.run(lambda: build_queue(prov, it),
                 lambda q: self._post(s.gen, lambda cur: self._set_queue(q) if cur else None),
                 lambda e: L.info("queue for %s failed: %s", it.id, e))

    def _set_queue(self, q):
        """A new queue: keep what came before (Previous), and keep the bar focus on the same
        button when Previous / Next appear."""
        old = self.queue
        if old is not None and q.items and old.current is not None \
                and old.current.id == q.items[q.index].id:
            prefix = old.items[:old.index]
            q = Queue(prefix + q.items, len(prefix) + q.index)
        key = None
        if self.bar and self.bar_row == "buttons":
            b = self._bar_buttons()
            key = b[min(self.bar_btn, len(b) - 1)][0]
        self.queue = q
        if key is not None:
            keys = [k for k, _l in self._bar_buttons()]
            self.bar_btn = keys.index(key) if key in keys else 0

    def _fixed(self):
        """A playlist or the local video queue: no Up Next card, Auto Play does not apply."""
        return self.queue is not None and self.queue.fixed

    def _item_prov(self, s, item):
        """The provider of a queue item. A fixed queue can hold items of several servers (a
        playlist entry with a sourceURI): each goes to its own server, or None when that
        server is not connected (no home fallback)."""
        if self._fixed() and self.provider_for is not None:
            return self.provider_for(item)
        return s.provider

    def _next_item(self):
        if self.queue is None or (not self.settings["auto_play"] and not self.queue.fixed):
            return None
        if self.sleep is not None and self.sleep.at_end:
            return None                       # Sleep Timer: End of Episode
        return self.queue.next

    def _credits_marker(self, s):
        """The final credits: a marker flagged final, else the last credits marker only when it
        starts in the second half (a mid-file credits marker is not the end)."""
        cr = [mk for mk in (s.pi.markers if s.pi else []) if mk.type == "credits"]
        fin = [mk for mk in cr if mk.final]
        if fin:
            return fin[0]
        dur_ms = (self.duration_s * 1000) or (s.pi.duration_ms if s.pi else 0)
        if cr and dur_ms and cr[-1].start_ms >= dur_ms * 0.5:
            return cr[-1]
        return None

    # ------------------------------------------------------------ Skip Intro / Skip Credits
    def _marker_tick(self, s):
        """Plex Player Experience: Disabled / Manually (button, 8 s or while the bar is open) /
        Automatically (skip on natural entry, not after a seek back into the marker)."""
        if not self.pos_fresh:
            return                                  # wait for mpv's position after a load/seek
        now, t = self.clock(), self.time_s
        cur = None
        for mk in (s.pi.markers if s.pi else []):
            if mk.start_ms / 1000.0 <= t < mk.end_ms / 1000.0 - 0.5:
                cur = mk
                break
        if self.skip is not None and self.skip["mk"] is not cur:
            self.skip = None                        # left the marker (or moved to another)
        if cur is None:
            return
        key = (cur.type, cur.start_ms)
        mode = self.settings["skip_intro" if cur.type == "intro" else "skip_credits"]
        if mode == "disabled" or (cur.type == "credits" and cur is self._credits_marker(s)
                                  and self.settings["up_next"] == "credits" and not self._fixed()
                                  and self._next_item() is not None):
            return                                  # Up Next owns the final credits
        label = "Skip Intro" if cur.type == "intro" else "Skip Credits"
        text_mode = self.ov is not None and self.ov.text_mode
        if key not in self.marker_seen:
            self.marker_seen.add(key)
            start, p = cur.start_ms / 1000.0, self.prev_time
            if p is None:
                natural = start <= 1.0 and t < 1.5           # the file starts in the marker
            else:
                natural = 0 <= start - p < 2.0 and 0 <= t - p < 2.0 and not self.scrub
            if mode == "auto" and natural:
                self._do_skip(s, s.mpv, cur)
                return
            if not text_mode:                       # no button to show: A keeps its meaning
                self.skip = {"mk": cur, "label": label, "until": now + 8.0}
        elif self.skip is None:
            if self.bar and not text_mode:          # Plex: the button shows with the controls
                self.skip = {"mk": cur, "label": label, "until": now}
        elif now >= self.skip["until"] and not self.bar:
            self.skip = None

    def _do_skip(self, s, m, mk=None):
        mk = mk or self.skip["mk"]
        self.skip = None
        self.scrub = None                           # a scrub commit must not undo the skip
        end = mk.end_ms / 1000.0
        if mk.type == "credits" and mk.final:
            end = max(end, (self.duration_s or end) - 0.5)   # final credits: go to the end
        self._seek(s, m, end, absolute=True, arg="%.1f" % end)
        self.time_s = end
        self.pos_fresh = False             # our value, not mpv's
        self.say("Skipped intro" if mk.type == "intro" else "Skipped credits")
        self._report_now()

    def _credits_check(self, s):
        """Up Next "During Final Credits": open the card when the final credits start."""
        if (self.upnext_done or self.scrub or self.tc_seek is not None or self._fixed()
                or self.settings["up_next"] != "credits"
                or self._next_item() is None):
            return
        mk = self._credits_marker(s)
        if mk and mk.start_ms / 1000.0 <= self.time_s < mk.end_ms / 1000.0 - 1:
            self._open_upnext(s)

    def _open_upnext(self, s, at=None, check_still=True):
        """The Up Next state. A fixed queue: no card, no shrink, no countdown;
        only the "Still watching?" prompt shows. at: the queue position to play
        (a fixed queue skips a failed item)."""
        now = self.clock()
        q = self.queue
        if at is None:
            at = q.index + 1
        nxt = q.items[at]
        hours = self.settings["passout_protection_h"]
        still = check_still and bool(hours) and now - self.last_input > hours * 3600 \
            and self.duration_s > PASSOUT_MIN_VIDEO_S
        silent = self._fixed() and not still
        u = {"item": nxt, "at": at, "opened": now,
             "until": now + (0 if silent else self.settings["autoplay_countdown_s"]),
             "still": still, "silent": silent, "pi": None, "err": None, "go": silent,
             "focus": "continue" if still else "next"}
        self.upnext = u
        self.session = UP_NEXT
        self.panel = self.skip = self.pmenu = None
        if self.bar:
            self._hide_bar()
        self.scrub = None
        if not silent:
            self._shrink(True)
        L.info("up next: %s%s", nxt.id, " (still watching?)" if still else "")
        self._upnext_fetch(s, u)

    def _upnext_fetch(self, s, u):
        nxt = u["item"]
        prov = u["prov"] = self._item_prov(s, nxt)
        u["sess"] = sess = str(uuid.uuid4())     # the next file's session, for its decision

        def got(r):
            self._post(s.gen, lambda cur: u.__setitem__("pi", r) if cur else None)

        def failed(e):
            self._post(s.gen, lambda cur: u.__setitem__("err", str(e)) if cur else None)
        self.run(lambda: _play_info(_need(prov), nxt, True, {"session": sess}), got, failed)

    def _upnext_tick(self, s):
        u, now = self.upnext, self.clock()
        if u is None:
            return
        if self._fixed():
            if u["still"]:
                if now - u["opened"] > PASSOUT_ANSWER_S:
                    L.info("no answer to 'still watching': stop")
                    self._finish(s)
            elif u["err"]:
                if not self._fixed_fail(s, u["at"], u["err"]):
                    self._finish(s)
            elif u["go"] and u["pi"] is not None:
                self._play_next(s)
            return
        if u["err"]:
            L.info("up next failed: %s", u["err"])
            if self.eof:
                self._finish(s)
            else:                             # the credits still play: back to them
                self.upnext, self.upnext_done = None, True
                self._shrink(False)
                self.session = PLAYING
        elif u["still"]:
            if now - u["opened"] > PASSOUT_ANSWER_S:
                L.info("no answer to 'still watching': stop")
                self._finish(s)
        elif (u["go"] or now >= u["until"]) and u["pi"] is not None:
            self._play_next(s)

    def _upnext_action(self, s, a):
        u = self.upnext
        if a in (inputs.BACK, inputs.QUIT, STOP):
            self.user_stop = True
            self._finish(s)
            return
        if u.get("silent"):
            return                            # a fixed queue: the next item is on its way
        if u["still"]:
            if a in (inputs.UP, inputs.DOWN):
                u["focus"] = "stop" if a == inputs.DOWN else "continue"
            elif a in (inputs.OK, PLAY, PLAYPAUSE):
                if u["focus"] == "stop":
                    self.user_stop = True
                    self._finish(s)
                else:
                    self.last_input = self.clock()
                    u["still"], u["go"] = False, True
            return
        if a in (inputs.LEFT, inputs.UP) and not self.eof:
            u["focus"] = "video"
        elif a in (inputs.RIGHT, inputs.DOWN):
            u["focus"] = "next"
        elif a == NEXT:
            u["go"] = True
        elif a in (inputs.OK, PLAY, PLAYPAUSE):
            if u["focus"] == "video" and not self.eof:
                # Plex: select the small video to go back to full screen (credits keep playing).
                self.upnext, self.upnext_done = None, True
                self._shrink(False)
                self.session = PLAYING
            else:
                u["go"] = True

    def _play_next(self, s):
        """Load the next queue item in the same mpv: no return to the UI between episodes."""
        u = self.upnext
        d = u.get("at", self.queue.index + 1) - self.queue.index if self.queue else 1
        self._switch_to(s, u["item"], u["pi"], d, u.get("sess"), u.get("prov"))

    def _jump(self, s, d):
        """Next / Previous episode from the bar, the menu or a remote key."""
        q = self.queue
        it = (q.next if d > 0 else q.prev) if q is not None else None
        if it is None:
            return False
        prov, gen = self._item_prov(s, it), s.gen
        sess = str(uuid.uuid4())
        if self._fixed():
            self.say("Next: %s" % it.title if d > 0 else "Previous: %s" % it.title)
        else:
            self.say("Next episode…" if d > 0 else "Previous episode…")

        def got(r):
            self._post(gen, lambda cur: self._switch_to(s, it, r, d, sess, prov)
                       if cur and self.s is s and self.session == PLAYING else None)

        def failed(e):
            self._post(gen, lambda cur: self.say("Can't play %s" % it.title) if cur else None)
        self.run(lambda: _play_info(_need(prov), it, True, {"session": sess}), got, failed)
        return True

    def _switch_to(self, s, item, pi_full, d, sess=None, prov=None):
        """Load item in the same mpv; the current file gets its final report first. prov: the
        item's own server (a fixed queue), else the session's."""
        m = s.mpv
        if not s.finalized:
            self._finalize(s, self._result(s))
        res = self._result(s)
        if not res.get("never_loaded"):
            self.files.append(self._clean(res))
        if self.bar:
            self._hide_bar()                  # also puts the subtitle margin back
        self._sub_margin(False)
        keep = (self.ov, self.files, self.queue, self.last_input)
        self.gen += 1
        s2 = Session(self.gen, item, prov or s.provider, True)
        if sess:
            s2.plex_session = sess               # the session its transcode decision used
        s2.pi, s2.full = pi_full
        s2.mpv = s2.spawned = m
        self.s, self.session, self.message = s2, STARTING, ""
        self._reset_facts()
        self.ov, self.files, self.queue, self.last_input = keep
        if self.queue is not None:
            self.queue.index = max(0, min(len(self.queue.items) - 1, self.queue.index + d))
        self.shrunk = True                    # force the restore below
        self._shrink(False)
        self.queue_stale = (self.queue is None or self.queue.next is None) and not self._fixed()
        self.switching = True
        self._load(s2, m)

    def _shrink(self, on):
        """The video moves into the top-left box of the Up Next layout, or back."""
        if on == self.shrunk:
            return
        self.shrunk = on
        s = self.s
        if s is None or s.mpv is None:
            return
        from .overlay import upnext_video_box
        if on:
            x, y, w, h = upnext_video_box(self.settings["safe"])
            r = {"left": x / 641.0, "top": y / 480.0,
                 "right": 1 - (x + w) / 641.0, "bottom": 1 - (y + h) / 480.0}
        else:
            r = self._video_box()
        self._set_video_box(s.mpv, r)
        self._aspect_apply(s)                # all of the picture in the small box

    def _video_box(self):
        """The video area as mpv margin ratios: inside the Screen Margins once the user set
        them (the part of the tube that shows), else the whole screen."""
        if not self._margins_on():
            return {"left": 0, "top": 0, "right": 0, "bottom": 0}
        safe = self.settings["safe"]
        # The calibration brackets end at x = 640 - right, so the area is 640 - l - r wide.
        return {"left": safe["left"] / 641.0, "top": safe["top"] / 480.0,
                "right": (safe["right"] + 1) / 641.0, "bottom": safe["bottom"] / 480.0}

    def _margins_on(self):
        try:
            return bool(self.settings["video_margins"])
        except KeyError:
            return False

    @staticmethod
    def _set_video_box(m, r):
        for k, v in r.items():
            m.set("video-margin-ratio-" + k, round(v, 4))

    def _area_aspect(self):
        """Width / height of the video area (square pixels on the 4:3 tube)."""
        if not self._margins_on():
            return aspect.SCREEN
        safe = self.settings["safe"]
        w = 640.0 - safe["left"] - safe["right"]
        h = 480.0 - safe["top"] - safe["bottom"]
        return w / h if w > 0 and h > 0 else aspect.SCREEN

    # ------------------------------------------------------------ aspect
    def _aspect_key(self, s):
        """Memory key: the show for an episode, else the item (a movie)."""
        it = s.full or s.item
        prof = self.profile or getattr(self.settings, "profile", None)
        if it.type == "episode" and it.show_id:
            return tracks.Memory.key(prof, it.server_id, it.show_id)
        return tracks.Memory.key(prof, it.server_id, "item:%s" % it.id) if it.id else None

    def _aspect_reset(self, s, m):
        """A new file: the saved Aspect, no crop; bar detection when the mode wants it."""
        key = self._aspect_key(s)
        saved = self.memory.get(key).get("aspect") if (self.memory is not None and key) else None
        try:
            default = self.settings["aspect"]
        except KeyError:
            default = "letterbox"
        self.aspect = aspect.valid(saved or default)
        self._crop_filter_off(m)
        self.vparams, self.crop, self.cropjob, self.moved_at = None, None, None, None
        if s.pi is not None and s.pi.url and m is not None:
            m.set("video-crop", "")
            m.set("panscan", 0.0)
        self._crop_plan()

    @staticmethod
    def _crop_filter_off(m):
        """The flag lives on the mpv object: _reset_facts (a same-mpv switch) cannot lose it."""
        if m is not None and getattr(m, "bpx_crop_on", False):
            m.cmd("vf", "remove", "@" + aspect.DETECT_LABEL)
            m.bpx_crop_on = False

    def _crop_plan(self):
        """Look for bars once per file."""
        if self.aspect != "letterbox" and self.bars is None and self.cropjob is None:
            self.cropjob = {"state": "wait", "at": None, "tries": 0}

    def _crop_sync(self, s):
        """Cut the known bars for this stream size (not in Letterbox), then fit the picture."""
        m = s.mpv
        if m is None:
            return
        vp = self.vparams or {}
        want = None
        if self.aspect != "letterbox":
            want = aspect.from_frac(self.bars, int(vp.get("w") or 0), int(vp.get("h") or 0))
        if want != self.crop:
            self.crop = want
            m.set("video-crop", aspect.crop_arg(want))
        self._aspect_apply(s)

    def _aspect_apply(self, s):
        m = s.mpv
        if m is None:
            return
        vp = self.vparams or {}
        a = float(vp.get("aspect") or 0)
        if a and self.crop and vp.get("w") and vp.get("h"):
            cw, ch, _x, _y = self.crop
            a = a * (cw / float(vp["w"])) / (ch / float(vp["h"]))
        ps = 0.0 if self.shrunk else aspect.panscan(self.aspect, a, self._area_aspect())
        m.set("panscan", ps)

    def set_aspect(self, mode):
        """The Playback menu: a new Display Mode for this show or movie, at once."""
        s = self.s
        if s is None:
            return
        self.aspect = aspect.valid(mode)
        key = self._aspect_key(s)
        if self.memory is not None and key:
            self.memory.set(key, "aspect", self.aspect)
        if self.aspect == "letterbox" and s.mpv is not None:
            self._crop_filter_off(s.mpv)
            self.cropjob = None
        self._crop_plan()
        self._crop_sync(s)

    def _crop_tick(self, s):
        """Bar detection: wait until the picture moves, add cropdetect, read the box after
        DETECT_RUN_S, remove the filter, cut the bars. A filter error ends it quietly."""
        job, m = self.cropjob, s.mpv
        if job is None or m is None or self.paused or self.shrunk or not self.loaded:
            return
        now = self.clock()
        if job["state"] == "wait":
            if job["at"] is None:
                if not (self.time_s > 0 and self.pos_fresh) or self.moved_at is None:
                    return
                # DETECT_WAIT_S after the picture starts to move (no black start frames);
                # at once for a new Aspect later in the file.
                job["at"] = max(now, self.moved_at + aspect.DETECT_WAIT_S)
            if now < job["at"]:
                return
            gen = self.gen

            def added(err, _d, job=job):
                if gen != self.gen or self.cropjob is not job:
                    return
                if err != "success":
                    L.info("bar detection not available: %s", err)
                    m.bpx_crop_on = False
                    self.cropjob = None
                    return
                job["state"], job["at"] = "run", self.clock() + aspect.DETECT_RUN_S
            job["state"] = "adding"
            m.bpx_crop_on = True
            m.cmd("vf", "add", aspect.DETECT_FILTER, cb=added)
        elif job["state"] == "run" and now >= job["at"]:
            gen = self.gen
            vp = dict(self.vparams or {})

            def got(err, meta, job=job, vp=vp):
                if gen != self.gen or self.cropjob is not job or s.mpv is None:
                    return
                self._crop_filter_off(s.mpv)
                c = aspect.crop_from_meta(meta if err == "success" else None,
                                          int(vp.get("w") or 0), int(vp.get("h") or 0),
                                          float(vp.get("aspect") or 0))
                job["tries"] += 1
                no_box = (err != "success" or not isinstance(meta, dict) or aspect.blank(meta)
                          or "lavfi.cropdetect.x1" not in meta)
                if c is None and no_box and job["tries"] < aspect.DETECT_TRIES:
                    job["state"], job["at"] = "wait", self.clock() + aspect.DETECT_RETRY_S
                    return
                self.cropjob = None
                if c is not None:
                    self.bars = aspect.to_frac(c, int(vp["w"]), int(vp["h"]))
                    L.info("bars found: %s", aspect.crop_arg(c))
                elif not no_box:
                    self.bars = (0.0, 0.0)           # a clear picture with no bars
                self._crop_sync(s)
            job["state"] = "reading"
            m.cmd("get_property", "vf-metadata/" + aspect.DETECT_LABEL, cb=got)

    # ------------------------------------------------------------ tracks
    def _mem_key(self, s):
        it = s.full or s.item
        if it.type != "episode" or not it.show_id:
            return None
        # Per Plex Home user: the app keeps the profile on the settings
        prof = self.profile or getattr(self.settings, "profile", None)
        return tracks.Memory.key(prof, it.server_id, it.show_id)

    def _select_tracks(self, s):
        pi, m = s.pi, s.mpv
        if not pi.tracks and pi.mode == "transcode":
            return                            # the server chose the tracks of the stream
        if not pi.tracks:
            # A provider without stream data: use the old index fields.
            if pi.sub_url:
                m.cmd("sub-add", pi.sub_url, "select")
            elif pi.sub_pos:
                m.set("sid", pi.sub_pos)
            else:
                m.set("sid", "no")
            return
        key = self._mem_key(s)
        mem = self.memory.get(key) if (self.memory and key) else {}
        au = tracks.choose_audio(pi.tracks, mem)
        sub, why = tracks.choose_sub(pi.tracks, au, mem)
        s.audio, s.sub = au, sub
        L.info("tracks: audio=%s sub=%s (%s)", tracks.label(au) if au else "default",
               tracks.label(sub), why)
        if pi.mode == "transcode":
            # The stream already has Plex's tracks. A different choice: PUT it, then restart
            # (the PUT finishes before the new decision).
            plex_au = next((t for t in tracks.audio(pi.tracks) if t.selected), None)
            plex_sub = next((t for t in tracks.subs(pi.tracks) if t.selected), None)
            sub_differs = (sub is None) != (plex_sub is None) or (
                sub is not None and plex_sub is not None and sub.id != plex_sub.id)
            calls = [self._stream_call(s, "sub", sub) if sub_differs else None,
                     self._stream_call(s, "audio", au) if au is not None and au is not plex_au
                     else None]
            calls = [c for c in calls if c]
            if calls:
                self._restart(s, self._tc_opts(s), pre=lambda: [c() for c in calls])
            return
        # Memory or the forced rule changed what Plex had: tell Plex, so other clients agree.
        if why in ("memory", "forced"):
            self._write_back(s, "sub", sub)
        plex_au = next((t for t in tracks.audio(pi.tracks) if t.selected), None)
        if au is not None and au is not plex_au:
            self._write_back(s, "audio", au)

        def apply(err, tl):
            if self.s is not s or not m.alive:
                return
            tl = tl or []
            if au is not None:
                aid = self._mpv_track_id(tl, au, pi.tracks)
                if aid:
                    m.set("aid", aid)
            self._apply_sub(s, sub, tl)
        m.cmd("get_property", "track-list", cb=apply)

    def _mpv_track_id(self, tl, t, all_tracks):
        kind = "audio" if t.kind == tracks.AUDIO else "sub"
        if t.index is not None:
            for x in tl:
                if x.get("type") == kind and x.get("ff-index") == t.index and not x.get("external"):
                    return x.get("id")
        pos = tracks.mpv_id(t, all_tracks)
        same = [x for x in tl if x.get("type") == kind and not x.get("external")]
        if pos and 0 < pos <= len(same):
            return same[pos - 1].get("id")
        return None

    def _apply_sub(self, s, sub, tl):
        m = s.mpv
        if sub is None:
            m.set("sid", "no")
        elif sub.external:
            m.cmd("sub-add", s.pi.base_url + sub.key, "select", tracks.label(sub),
                  sub.language_code or "")
        else:
            sid = self._mpv_track_id(tl, sub, s.pi.tracks)
            m.set("sid", sid if sid else "no")

    # ------------------------------------------------------------ actions
    def action(self, a, is_repeat=False, src="pad"):
        """Every pad action while a session is not IDLE comes here."""
        s = self.s
        if self.ch is not None and s is not None and self._channel_action(s, a, is_repeat):
            return                            # before the STARTING filter
        if self.session == STARTING:
            if a in (inputs.BACK, inputs.QUIT):
                self.cancel()
            return
        if self.session == ERROR:
            if a in (inputs.OK, inputs.BACK, inputs.QUIT):
                self._end(self._result(s, error=self.cause or self.message))
            return
        if s is not None:
            self.last_input = self.clock()     # passout protection counts any input
        if self.session == UP_NEXT and s is not None and not is_repeat:
            self._upnext_action(s, a)
            return
        if self.session != PLAYING or s is None or s.mpv is None:
            return
        self._play_action(s, s.mpv, a, is_repeat, src)

    # ------------------------------------------------------------ playing: controls
    def _bar_buttons(self):
        b = [("play", "Play") if self.paused else ("pause", "Pause")]
        q = self.queue
        if q is not None and len(q.items) > 1:          # episodes: Previous / Next
            if q.prev is not None:
                b.append(("prev", "Previous"))
            if q.next is not None:
                b.append(("next", "Next" if q.fixed else "Next Episode"))
        return b + [("subs", "Audio & Subtitles"), ("stop", "Stop")]

    def _show_bar(self, row=None):
        self.bar = True
        self.bar_until = self.clock() + BAR_HIDE_S
        if row:
            self.bar_row = row
        self._sub_margin(True)

    def _hide_bar(self):
        self.bar = False
        self.bar_row, self.bar_btn = "seek", 0
        self._sub_margin(False)

    def _sub_margin(self, up):
        """Lift text subtitles above the bar while it is open (mpv scaled pixels: x 1.5)."""
        s = self.s
        if s is None or s.mpv is None:
            return
        from .overlay import BAR_H
        safe = self.settings["safe"]
        base = int(safe["bottom"] * 1.5) + 12
        want = int((BAR_H + safe["bottom"]) * 1.5) + 12 if up else base
        if getattr(self, "_margin", None) != want:
            self._margin = want
            s.mpv.set("sub-margin-y", want)

    def say(self, msg, secs=TOAST_S):
        self.toast = (msg, self.clock() + secs)
        if self.ov is not None and self.ov.text_mode and self.s and self.s.mpv:
            self.s.mpv.cmd("show-text", msg, int(secs * 1000))

    def _toggle_pause(self, s, m):
        now = self.clock()
        if self.ch is not None and not self.paused:
            self.ch["paused"] = True           # behind the tape until the next change
        if self.paused and s.channel is None:
            back = self.settings["rewind_on_resume_s"]
            if back and now - self.paused_at >= REWIND_AFTER_PAUSE_S:
                self._seek(s, m, -back)
                self.time_s = self._clamp(self.time_s - back)
                self.pos_fresh = False             # our value, not mpv's
        else:
            self.paused_at = now
        self.user_paused = not self.paused
        self.paused = not self.paused          # the property change confirms it
        m.set("pause", self.paused)            # set, not cycle: mpv and the app cannot differ
        self._report_now()

    def _stop(self, s):
        self.user_stop = True
        self._finish(s)

    def _play_action(self, s, m, a, rep, src):
        now = self.clock()
        if self.panel is not None:
            self._panel_action(s, a, rep, src)
            return
        if self.pmenu is not None:
            self._pmenu_action(s, a, rep)
            return
        if a in (inputs.LEFT, inputs.RIGHT):
            if self.bar and self.bar_row == "buttons":
                if not rep:
                    n = len(self._bar_buttons())
                    self.bar_btn = max(0, min(n - 1, self.bar_btn + (1 if a == inputs.RIGHT else -1)))
                    self.bar_until = now + BAR_HIDE_S
                return
            self._seek_input(s, m, a, rep, src)
            return
        if rep:
            return                              # only directions repeat
        if a == inputs.OK and self.skip is not None and not (self.bar and self.bar_row == "buttons"):
            self._do_skip(s, m)                     # the skip button has focus when it shows
            return
        if a == inputs.OK:
            if not self.bar:
                self.bar_btn = 0                    # Pause (or Play) has focus
                self._show_bar("buttons")
            elif self.bar_row == "buttons":
                self._activate(s, m)
            else:
                self._toggle_pause(s, m)
                self._show_bar()
        elif a == inputs.BACK:
            if self.bar:
                self._hide_bar()
            else:
                self._stop(s)
        elif a in (inputs.QUIT, STOP):
            self._stop(s)
        elif a in (inputs.UP, inputs.DOWN):
            if not self.bar:
                self._show_bar("seek")
            elif a == inputs.DOWN and self.bar_row == "seek":
                self._show_bar("buttons")
            elif a == inputs.UP and self.bar_row == "buttons":
                self._show_bar("seek")
            else:
                self._show_bar()
        elif a == inputs.Y:
            self._cycle_track(s, m, "sub")
        elif a == inputs.X:
            self._cycle_track(s, m, "audio")
        elif a in (PREV, NEXT) and (s.full or s.item).type == "episode" and self.queue is None:
            pass                                    # the episode list is still loading
        elif a in (PREV, NEXT) and self._jump(s, -1 if a == PREV else 1):
            pass                                    # remote keys: episode when there is one
        elif a in (inputs.LT, inputs.RT, PREV, NEXT):
            self._chapter(s, m, -1 if a in (inputs.LT, PREV) else 1)
        elif a == inputs.MENU:
            if self.ov is not None and self.ov.text_mode:   # no overlay: no invisible menu
                self._hide_bar() if self.bar else self._show_bar()
            else:
                self._open_pmenu(s)
        elif a == PLAYPAUSE:
            self._toggle_pause(s, m)
            self._show_bar()
        elif a in (PLAY, PAUSE):
            want = a == PAUSE
            if self.paused != want:
                self._toggle_pause(s, m)
            self._show_bar()

    def _activate(self, s, m):
        btns = self._bar_buttons()
        key = btns[min(self.bar_btn, len(btns) - 1)][0]
        self.bar_until = self.clock() + BAR_HIDE_S
        if key in ("play", "pause"):
            self._toggle_pause(s, m)
        elif key == "subs":
            self._open_panel(s, m, "sub")
        elif key in ("prev", "next"):
            self._jump(s, -1 if key == "prev" else 1)
        elif key == "stop":
            self._stop(s)

    def _cycle_track(self, s, m, kind):
        """Y / X: step to the next track."""
        pi = s.pi
        if pi is not None and pi.tracks:
            lst = tracks.subs(pi.tracks) if kind == "sub" else tracks.audio(pi.tracks)
            cur = s.sub if kind == "sub" else s.audio
            opts = ([None] if kind == "sub" else []) + lst
            if not opts or (kind == "audio" and len(opts) < 2):
                self.say("No subtitles" if kind == "sub" else "No other audio")
                return
            i = next((k for k, t in enumerate(opts) if t is cur or (t and cur and t.id == cur.id)), -1)
            self.choose_track(kind, opts[(i + 1) % len(opts)])
            return
        m.cmd("cycle", "sub" if kind == "sub" else "audio")
        prop = "sub" if kind == "sub" else "audio"

        def told(err, tr):
            if tr:
                name = " ".join(x for x in (tr.get("lang") or "", tr.get("title") or "") if x)
                self.say("%s: %s" % ("Subtitles" if kind == "sub" else "Audio", name or "On"))
            else:
                self.say("Subtitles: None" if kind == "sub" else "Audio: Off")
        m.cmd("get_property", "current-tracks/" + prop, cb=told)

    # ------------------------------------------------------------ playback menu (Start)
    def _pmenu_rows(self, s):
        rows = [("guide", "Guide"), ("last", "Last Channel")] if s.channel is not None else []
        if s.pi is not None and s.pi.tracks:
            rows += [("subs", "Subtitles: %s" % tracks.label(s.sub)),
                     ("audio", "Audio: %s" % (tracks.label(s.audio) if s.audio else "Default"))]
        else:
            rows += [("subs", "Subtitles"), ("audio", "Audio")]
        if s.pi is not None and hasattr(s.provider, "transcode_choice") and not _no_state(s):
            rows.append(("quality", "Quality: %s" % (s.pi.quality or "Original")))
        rows.append(("aspect", "Display Mode: %s" % aspect.LABEL[self.aspect]))
        if self.sleep is not None:
            rows.append(("sleep", "Sleep Timer: %s" % self.sleep.label(self._end_name(s))))
        if s.channel is None:
            rows.append(("start", "Play from Start"))
        q = self.queue
        if q is not None and q.prev is not None:          # The bar's order
            rows.append(("prev", "Previous" if q.fixed else "Previous Episode"))
        if q is not None and q.next is not None:
            rows.append(("next", "Next" if q.fixed else "Next Episode"))
        rows.append(("stop", "Stop"))
        return rows

    @staticmethod
    def _end_name(s):
        it = s.full or s.item
        return "End of Episode" if it.type == "episode" else "End of Video"

    def stop_now(self):
        """Sleep Timer: stop the video as the Stop button does (progress is kept)."""
        s = self.s
        if s is None:
            return
        if self.session == STARTING:
            self.cancel()
        elif self.session in (PLAYING, UP_NEXT):
            self.panel = self.pmenu = None
            self._stop(s)

    def _open_pmenu(self, s):
        self._hide_bar()
        self.mini_until = 0.0
        self.pmenu = {"rows": self._pmenu_rows(s), "focus": 0}

    def _open_quality(self, s):
        """Quality: the list of Original and the targets, the current one checked."""
        labels = _quality_labels()
        cur = s.pi.quality if s.pi.mode == "transcode" and s.pi.quality in labels else "Original"
        i = labels.index(cur)
        self.pmenu = {"rows": [("q", lbl) for lbl in labels], "focus": i, "checked": i,
                      "title": "Quality"}

    def _open_aspect(self):
        """Display Mode: Letterbox, 14:9 and Zoom, the current one checked."""
        i = [k for k, _l in aspect.MODES].index(self.aspect)
        self.pmenu = {"rows": list(aspect.MODES), "focus": i, "checked": i,
                      "title": "Display Mode"}

    def _back_to_pmenu(self, s, key):
        self._open_pmenu(s)
        keys = [k for k, _l in self.pmenu["rows"]]
        self.pmenu["focus"] = keys.index(key) if key in keys else 0

    def _aspect_list_action(self, s, a):
        p = self.pmenu
        if a == inputs.BACK:
            self._back_to_pmenu(s, "aspect")
        elif a == inputs.MENU:
            self.pmenu = None
        elif a in (inputs.QUIT, STOP):
            self.pmenu = None
            self._stop(s)
        elif a in (PLAYPAUSE, PLAY, PAUSE):
            if a == PLAYPAUSE or self.paused != (a == PAUSE):
                self._toggle_pause(s, s.mpv)
        elif a == inputs.OK:
            self.pmenu = None
            self.set_aspect(p["rows"][p["focus"]][0])

    def _quality_action(self, s, a):
        p = self.pmenu
        if a == inputs.BACK:                  # back to the playback menu, on Quality
            self._back_to_pmenu(s, "quality")
        elif a == inputs.MENU:
            self.pmenu = None
        elif a in (inputs.QUIT, STOP):
            self.pmenu = None
            self._stop(s)
        elif a in (PLAYPAUSE, PLAY, PAUSE):
            if a == PLAYPAUSE or self.paused != (a == PAUSE):
                self._toggle_pause(s, s.mpv)
        elif a == inputs.OK:
            label = p["rows"][p["focus"]][1]
            self.pmenu = None
            if p["focus"] == p["checked"]:
                return
            s.user_quality = True                 # rule 6 stops now, not after play_info
            if label == "Original":
                self._restart(s, {"mode": "direct"}, "Quality: Original")
            else:
                self._restart(s, {"mode": "transcode", "quality": label}, "Quality: " + label)

    def _pmenu_action(self, s, a, rep):
        p = self.pmenu
        if a in (inputs.UP, inputs.DOWN):
            p["focus"] = max(0, min(len(p["rows"]) - 1, p["focus"] + (1 if a == inputs.DOWN else -1)))
            return
        if p.get("title") == "Quality":
            if not rep:
                self._quality_action(s, a)
            return
        if p.get("title") == "Display Mode":
            if not rep:
                self._aspect_list_action(s, a)
            return
        key = p["rows"][p["focus"]][0]
        if key == "aspect":
            # Only A opens the list: a stray Left or Right must not change a saved mode.
            if a == inputs.OK and not rep:
                self._open_aspect()
            if a in (inputs.OK, inputs.LEFT, inputs.RIGHT):
                return
        if key == "sleep" and a in (inputs.OK, inputs.LEFT, inputs.RIGHT) and not rep:
            # Left and right change the Sleep Timer in place: the menu stays open.
            self.sleep.step(-1 if a == inputs.LEFT else 1)
            p["rows"] = self._pmenu_rows(s)
            return
        if rep:
            return
        if a in (inputs.BACK, inputs.MENU):
            self.pmenu = None
        elif a in (inputs.QUIT, STOP):
            self.pmenu = None
            self._stop(s)
        elif a in (PLAYPAUSE, PLAY, PAUSE):
            if a == PLAYPAUSE or self.paused != (a == PAUSE):
                self._toggle_pause(s, s.mpv)
        elif a == inputs.OK:
            key = p["rows"][p["focus"]][0]
            self.pmenu = None
            m = s.mpv
            if key in ("subs", "audio"):
                self._open_panel(s, m, "sub" if key == "subs" else "audio")
            elif key == "start":
                self.marker_seen, self.upnext_done, self.skip = set(), False, None
                self._seek(s, m, 0.0, absolute=True, arg="0")
                self.time_s, self.pos_fresh = 0.0, False
                self.mini_until = self.clock() + MINI_S
                self._report_now()
            elif key == "quality":
                self._open_quality(s)
            elif key in ("next", "prev"):
                self._jump(s, 1 if key == "next" else -1)
            elif key == "guide":
                self.ch["guide"] = True
                self._stop(s)
            elif key == "last":
                self._channel_want(s, self.live.last(s.channel.cid))
            elif key == "stop":
                self._stop(s)

    # ------------------------------------------------------------ track panel
    PANEL_ROWS = 6

    def _panel_opts(self, s, kind):
        pi = s.pi
        if kind == "sub":
            return [None] + tracks.subs(pi.tracks)
        return tracks.audio(pi.tracks)

    def _open_panel(self, s, m, kind):
        """Audio & Subtitles: a list on the right with Subtitles / Audio tabs."""
        if s.pi is None or not s.pi.tracks:
            self._cycle_track(s, m, kind)            # no stream data: cycle in mpv
            return
        self._hide_bar()
        self.mini_until = 0.0
        self.panel = {"tab": kind}
        self._panel_tab(s, kind)

    def _panel_tab(self, s, kind):
        opts = self._panel_opts(s, kind)
        cur = s.sub if kind == "sub" else s.audio
        i = next((k for k, t in enumerate(opts) if t is cur or (t and cur and t.id == cur.id)), 0)
        self.panel.update(tab=kind, focus=i, top=max(0, min(i - 2, len(opts) - self.PANEL_ROWS)))

    def _panel_action(self, s, a, rep, src="pad"):
        p = self.panel
        if a in (PLAYPAUSE, PLAY, PAUSE):           # media keys still work under the panel
            if a == PLAYPAUSE or self.paused != (a == PAUSE):
                self._toggle_pause(s, s.mpv)
            return
        opts = self._panel_opts(s, p["tab"])
        if a in (inputs.UP, inputs.DOWN):
            n = max(0, min(len(opts) - 1, p["focus"] + (1 if a == inputs.DOWN else -1)))
            p["focus"] = n
            if n < p["top"]:
                p["top"] = n
            elif n >= p["top"] + self.PANEL_ROWS:
                p["top"] = n - self.PANEL_ROWS + 1
            return
        if rep:
            return
        if a in (inputs.LEFT, inputs.RIGHT, inputs.LB, inputs.RB):
            now = self.clock()
            if src == "kb" and a in (inputs.LEFT, inputs.RIGHT):
                # A held remote arrow repeats at 40 Hz: one tab switch per burst.
                last, p["kb_tab"] = p.get("kb_tab", -9.0), now
                if now - last < KB_COMMIT_S:
                    return
            self._panel_tab(s, "audio" if p["tab"] == "sub" else "sub")
        elif a == inputs.Y:
            self._panel_tab(s, "sub")
        elif a == inputs.X:
            self._panel_tab(s, "audio")
        elif a == inputs.OK and opts:
            kind, t = p["tab"], opts[p["focus"]]
            self.panel = None
            self.choose_track(kind, t)
        elif a in (inputs.BACK, inputs.MENU, inputs.QUIT, STOP):
            self.panel = None
            if a in (inputs.QUIT, STOP):
                self._stop(s)

    def _panel_model(self, s):
        p = self.panel
        opts = self._panel_opts(s, p["tab"])
        cur = s.sub if p["tab"] == "sub" else s.audio
        rows = []
        for t in opts[p["top"]:p["top"] + self.PANEL_ROWS]:
            on = t is cur or (t is not None and cur is not None and t.id == cur.id)
            rows.append((tracks.label(t), on))
        return {"tab": p["tab"], "rows": rows, "focus": p["focus"] - p["top"],
                "more_up": p["top"] > 0, "more_down": p["top"] + self.PANEL_ROWS < len(opts)}

    def choose_track(self, kind, t):
        """A user choice of a subtitle (t None = off) or audio track: apply it in mpv,
        confirm with a toast, write it back to Plex and remember it for the show."""
        s = self.s
        if s is None or s.mpv is None or self.session != PLAYING:
            return
        m, pi = s.mpv, s.pi
        if pi.mode == "transcode":
            # The server burns subtitles and picks the audio: PUT, then restart (4.3).
            if kind == "sub":
                s.sub = t
            else:
                s.audio = t
            self.say("%s: %s" % ("Subtitles" if kind == "sub" else "Audio", tracks.label(t)))
            fn = self._stream_call(s, kind, t)
            self._restart(s, self._tc_opts(s), pre=fn)
            key = self._mem_key(s)
            if self.memory is not None and key:
                self.memory.set(key, kind, tracks.pref_of(t, kind))
            return
        if kind == "sub":
            s.sub = t

            def apply(err, tl):
                if self.s is s and m.alive:
                    self._apply_sub(s, t, tl or [])
            self.say("Subtitles: %s" % tracks.label(t))
        else:
            s.audio = t

            def apply(err, tl):
                if self.s is s and m.alive:
                    aid = self._mpv_track_id(tl or [], t, pi.tracks)
                    if aid:
                        m.set("aid", aid)
            self.say("Audio: %s" % tracks.label(t))
        m.cmd("get_property", "track-list", cb=apply)
        self._write_back(s, kind, t)
        key = self._mem_key(s)
        if self.memory is not None and key:
            self.memory.set(key, kind, tracks.pref_of(t, kind))

    def _stream_call(self, s, kind, t):
        """The PUT of a track choice on the part (allParts=1), as a function, or None. A
        channel writes nothing to the server."""
        pi, prov = s.pi, s.provider
        if s.channel is not None or pi is None or not pi.part_id or not hasattr(prov, "set_streams"):
            return None
        if kind == "sub":
            return lambda: prov.set_streams(pi.part_id, sub_id=t.id if t else 0)
        if t is None:
            return None
        return lambda: prov.set_streams(pi.part_id, audio_id=t.id)

    def _write_back(self, s, kind, t):
        """PUT the choice on the part. Background; errors go to the log."""
        fn = self._stream_call(s, kind, t)
        if fn is not None:
            self.run(fn, lambda r: None, lambda e: L.info("stream write-back failed: %s", e))

    def _chapter(self, s, m, d):
        if self.chapters > 1:
            m.cmd("add", "chapter", d)
            cur = self.chapter if self.chapter is not None else 0
            n = max(0, min(self.chapters - 1, cur + d))
            title = self.chapter_list[n][1] if n < len(self.chapter_list) else ""
            # Files often name chapters "Chapter 04": show the name only when it adds something.
            generic = not title or title.lower().startswith("chapter") or title.isdigit()
            self.say("Chapter %d" % (n + 1) if generic else "Chapter %d • %s" % (n + 1, title))
        else:
            self._seek(s, m, d * CHAPTERLESS_JUMP_S)
            self.time_s = self._clamp(self.time_s + d * CHAPTERLESS_JUMP_S)
            self.pos_fresh = False             # our value, not mpv's
            self.mini_until = self.clock() + MINI_S
            self.say("No chapters: %s 5 min" % ("back" if d < 0 else "ahead"))
        self._report_now()

    # ------------------------------------------------------------ transcode
    def _seek(self, s, m, t, absolute=False, arg=None):
        """Every seek. Direct: mpv seeks. Transcode: a new stream from the target; taps add
        up for TC_SEEK_DEBOUNCE_S, then one restart."""
        if s is None or s.pi is None or s.pi.mode != "transcode":
            if absolute:
                m.cmd("seek", arg if arg is not None else "%.1f" % t, "absolute")
            else:
                m.cmd("seek", t)
            return
        target = self._clamp(t if absolute else self.time_s + t)
        wait = 0.0 if absolute else TC_SEEK_DEBOUNCE_S
        self.tc_seek = {"t": target, "at": self.clock() + wait}

    def _tc_opts(self, s):
        """Options that give the same transcode again (relay keeps 720 kbps)."""
        pi = s.pi
        o = {"mode": "transcode", "quality": pi.quality, "reason": pi.reason}
        for k in ("retry", "auto"):
            if s.opts.get(k):
                o[k] = True
        return o

    def _tc_stopper(self, s):
        """A function that stops the transcode of s on the server, once; None if none."""
        if s.pi is None or s.pi.mode != "transcode" or s.tc_stopped \
                or not hasattr(s.provider, "stop_transcode"):
            return None
        s.tc_stopped = True
        prov, sess = s.provider, s.plex_session
        return lambda: prov.stop_transcode(sess)

    def _tc_tick(self, s):
        now = self.clock()
        pi = s.pi
        if self.tc_seek and now >= self.tc_seek["at"] and not self.scrub:
            t, self.tc_seek = self.tc_seek["t"], None
            self._tc_load(s, t)
        if pi is None:
            return
        if pi.mode == "transcode" and self.paused and now - s.last_ping >= TC_PING_S:
            s.last_ping = now
            prov, sess = s.provider, s.plex_session
            self.run(lambda: prov.ping_transcode(sess), lambda r: None,
                     lambda e: L.info("transcode ping failed: %s", e))
        if self._stall_total(s, now) >= STALL_LIMIT_S:
            nxt = self._may_auto_switch(s)
            if nxt:
                s.tc_tried = True
                s.stalls, s.stall_at = [], None      # a new window for the new target
                L.info("buffering %.0fs within %.0fs: switch to %s", STALL_LIMIT_S,
                       STALL_WINDOW_S, nxt)
                self._restart(s, {"mode": "transcode", "quality": nxt, "auto": True},
                              "Switched to " + nxt)

    def _tc_load(self, s, t):
        """The same transcode from a new offset."""
        m, pi = s.mpv, s.pi
        t = float(int(max(0.0, t)))
        s.base_s = pi.offset_s = t
        self.switching = True
        m.cmd("loadfile", pi.url_at(t), "replace")
        self.time_s, self.pos_fresh = t, False
        L.info("transcode restart at %.0fs", t)
        self._report_now()

    def _stall(self, s, on):
        now = self.clock()
        if on and self.session == PLAYING and self.loaded:
            if s.stall_at is None:
                s.stall_at = now
        elif not on and s.stall_at is not None:
            s.stalls = [x for x in s.stalls if x[1] > now - STALL_WINDOW_S] + [(s.stall_at, now)]
            s.stall_at = None

    def _stall_total(self, s, now):
        lo = now - STALL_WINDOW_S
        n = sum(min(b, now) - max(a, lo) for a, b in s.stalls if b > lo)
        if s.stall_at is not None:
            n += now - max(s.stall_at, lo)
        return n

    def _may_auto_switch(self, s):
        """Rule 6: remote, automatic quality, no menu choice: the next lower target (direct
        play steps down once per file), or None."""
        pi = s.pi
        if getattr(s, "user_quality", False) or getattr(s, "restart_busy", False):  # menu wins; one at a time
            return None
        if (_no_state(s) or pi.conn not in ("remote", "relay")
                or not hasattr(s.provider, "transcode_choice")
                or self.settings["quality_remote"] != "auto"):
            return None
        if pi.mode == "transcode":
            if s.opts.get("mode") is not None and not s.opts.get("auto"):
                return None
            return _step_down(pi.quality, s.provider)
        if s.tc_tried or s.opts.get("mode") is not None:
            return None
        return _step_down("", s.provider, getattr(pi, "src_kbps", 0))

    def _retry_transcode(self, s):
        """Rule 5: direct play failed to load twice (normal, safe mode); one transcode try."""
        if s.tc_tried or s.pi is None or s.pi.mode != "direct" or _no_state(s) \
                or not hasattr(s.provider, "transcode_choice"):
            return False
        s.tc_tried = True
        L.info("direct play failed twice; one try with conversion")
        self._discard(s)
        self._drop_overlay()
        s.mpv = s.spawned = None
        keep = {"at_s": s.opts["at_s"]} if "at_s" in s.opts else {}   # The tune offset
        s.pi, s.opts = None, dict(keep, mode="transcode", retry=True)
        self.started_at = self.clock()
        self._spawn(s)
        return True

    def _restart(self, s, opts, toast=None, pre=None):
        """The same file again at the current position, by new play_info options: the Quality
        row, rule 6, or a track change while it transcodes (the PUT goes first)."""
        prov, old, gen = s.provider, s.pi, s.gen
        s.restart_n = getattr(s, "restart_n", 0) + 1
        n = s.restart_n
        s.restart_busy = True
        stop_old = old is not None and old.mode == "transcode" and opts.get("mode") == "direct"

        def work():
            if pre:
                pre()
            r = _play_info(prov, s.item, True, dict(opts, session=s.plex_session))
            if stop_old and n == s.restart_n:   # a newer restart may use this session now
                _quiet(lambda: prov.stop_transcode(s.plex_session))
            return r

        def done():
            if n == s.restart_n:
                s.restart_busy = False

        def got(r):
            def apply(cur):
                done()
                if cur:
                    self._restarted(s, n, r, opts, toast)
            self._post(gen, apply)

        def failed(e):
            def tell(cur):
                done()
                if cur and self.s is s and n == s.restart_n:
                    L.info("quality change failed: %s", e)
                    self.say(_refusal(e) or "Can't change quality", 4.0)
            self._post(gen, tell)
        self.run(work, got, failed)

    def _restarted(self, s, n, r, opts, toast):
        if self.s is not s or n != s.restart_n or self.session != PLAYING or s.mpv is None:
            return
        pi, _full = r
        t = self.time_s
        if pi.mode == "transcode":
            pi.offset_s = float(int(max(0.0, t)))
            pi.url = pi.url_at(pi.offset_s)
        else:
            pi.start_s = t
        s.pi, s.opts = pi, {k: v for k, v in opts.items() if k in ("mode", "quality", "reason",
                                                                     "retry", "auto")}
        s.tc_stopped = False
        s.stalls, s.stall_at = [], None       # the old file's stalls do not count
        self.tc_seek = None
        self.switching = True
        self.reapply = pi.mode == "direct" and bool(pi.tracks)
        paused = self.paused
        self._load(s, s.mpv)
        if paused:
            s.mpv.set("pause", True)         # _load un-pauses; the property change is dropped
        self.card = False
        self.time_s, self.pos_fresh = t, False
        if toast:
            self.say(toast)
        self._report_now()

    def _reapply_tracks(self, s):
        m, pi, au, sub = s.mpv, s.pi, s.audio, s.sub

        def apply(err, tl):
            if self.s is not s or not m.alive:
                return
            tl = tl or []
            if au is not None:
                aid = self._mpv_track_id(tl, au, pi.tracks)
                if aid:
                    m.set("aid", aid)
            self._apply_sub(s, sub, tl)
        m.cmd("get_property", "track-list", cb=apply)

    # ------------------------------------------------------------ playing: seek and scrub
    def _clamp(self, t):
        d = self.duration_s or 0
        return max(0.0, min(t, d - 1)) if d > 1 else max(0.0, t)

    def _seek_input(self, s, m, a, rep, src):
        now = self.clock()
        step = SEEK_BACK_S if a == inputs.LEFT else SEEK_FWD_S
        sc = self.scrub
        if src == "pad":
            if rep:
                return                          # holding is handled in _scrub_tick
            if sc and sc["dir"] != a:
                self._commit_scrub(m)
            self._seek(s, m, step)
            self.time_s = self._clamp(self.time_s + step)
            self.pos_fresh = False             # our value, not mpv's
            self.scrub = {"dir": a, "src": "pad", "t0": now, "next": now + HOLD_S, "target": None,
                          "last": now}
        else:
            if sc and sc["dir"] == a and sc["src"] == "kb" and now - sc["last"] < KB_COMMIT_S:
                # mpv repeats keys at 40 Hz: step at the pad scrub rate, report on commit only.
                sc["last"] = now
                if now >= sc["next"]:
                    if sc["target"] is None:
                        sc["target"] = self.time_s
                    sc["target"] = self._clamp(sc["target"] + self._scrub_step(a, now - sc["t0"]))
                    sc["next"] = now + SCRUB_EVERY_S
                self.mini_until = now + MINI_S
                if self.bar:
                    self.bar_until = now + BAR_HIDE_S
                return
            else:
                if sc:
                    self._commit_scrub(m)
                self._seek(s, m, step)
                self.time_s = self._clamp(self.time_s + step)
                self.pos_fresh = False             # our value, not mpv's
                self.scrub = {"dir": a, "src": "kb", "t0": now, "next": now + HOLD_S,
                              "target": None, "last": now}
        self.mini_until = now + MINI_S
        if self.bar:
            self.bar_until = now + BAR_HIDE_S
        self._report_now()

    def _scrub_step(self, a, held):
        k = 1 if held < 2.0 else (2 if held < 4.0 else (4 if held < 6.0 else 8))   # Plex x2..x8
        return (-1 if a == inputs.LEFT else 1) * SCRUB_STEP_S * k

    def _scrub_tick(self):
        sc = self.scrub
        if not sc:
            return
        now = self.clock()
        m = self.s.mpv
        if sc["src"] == "pad":
            if self.is_held(sc["dir"]):
                if now >= sc["next"]:
                    if sc["target"] is None:
                        sc["target"] = self.time_s
                    sc["target"] = self._clamp(sc["target"] + self._scrub_step(sc["dir"], now - sc["t0"]))
                    sc["next"] = now + SCRUB_EVERY_S
                self.mini_until = now + MINI_S
                if self.bar:
                    self.bar_until = now + BAR_HIDE_S
            else:
                self._commit_scrub(m)
        elif now - sc["last"] > KB_COMMIT_S:
            self._commit_scrub(m)

    def _commit_scrub(self, m):
        sc, self.scrub = self.scrub, None
        if sc and sc["target"] is not None:
            self._seek(self.s, m, sc["target"], absolute=True, arg="%.1f" % sc["target"])
            self.time_s = sc["target"]
            self.pos_fresh = False             # our value, not mpv's
            self.mini_until = self.clock() + MINI_S
            self._report_now()

    # ------------------------------------------------------------ playing: overlay
    def _bar_model(self, s):
        markers = [(mk.start_ms / 1000.0, mk.end_ms / 1000.0) for mk in (s.pi.markers if s.pi else [])]
        return {"title": (s.pi.title if s.pi else "") or (s.full or s.item).hero_title,
                "buttons": self._bar_buttons(), "focus_row": self.bar_row, "focus_btn": self.bar_btn,
                "pos_s": self.time_s, "dur_s": self.duration_s,
                "scrub_s": self.scrub["target"] if self.scrub else None, "markers": markers,
                "chapters": [t for t, _ in self.chapter_list if t > 0.5]}

    def _render(self):
        ov, s = self.ov, self.s
        if ov is None or s is None:
            return
        from . import overlay as O
        now = self.clock()
        safe = self.settings["safe"]
        # toast (also the stop prompt)
        if self.toast and now < self.toast[1] and not ov.text_mode:
            msg = self.toast[0]
            x, y, w, h = O.toast_rect(msg, safe)
            ov.show(O.TOAST, x, y, w, h, lambda surf, msg=msg: O.draw_toast(surf, msg), sig=msg)
        else:
            self.toast = None if (self.toast and now >= self.toast[1]) else self.toast
            ov.hide(O.TOAST)
        self._render_banner(ov, O, s, safe, now)
        self._render_guide(ov, O)
        # start card (a channel shows its banner instead)
        if self.card and self.session == STARTING and s.channel is None:
            title = (s.full or s.item).hero_title
            x, y, w, h = O.card_rect(safe)
            ov.show(O.PANEL, x, y, w, h, lambda surf, t=title: O.draw_card(surf, t), sig=("card", title))
        elif self.session == UP_NEXT and self.upnext is not None:
            if self.upnext.get("silent"):
                ov.hide(O.PANEL)              # a fixed queue: the next item starts, no card
            else:
                self._render_upnext(ov, O, s, safe, now)
        elif self.pmenu is not None and self.session == PLAYING:
            if self.sleep is not None and isinstance(self.sleep.mode, int) \
                    and self.pmenu.get("title") is None:
                # The minutes count down. Keep the focus on its row: the queue can land now
                # and add Next / Previous Episode above Stop.
                key = self.pmenu["rows"][self.pmenu["focus"]][0]
                self.pmenu["rows"] = self._pmenu_rows(s)
                keys = [k for k, _l in self.pmenu["rows"]]
                self.pmenu["focus"] = keys.index(key) if key in keys else 0
            rows = [lbl for _k, lbl in self.pmenu["rows"]]
            fx = self.pmenu["focus"]
            fit = O.menu_fit(safe)
            top = max(0, min(self.pmenu.get("top", 0), fx, len(rows) - fit))
            top = max(top, fx - fit + 1)
            self.pmenu["top"] = top
            x, y, w, h = O.menu_rect(len(rows), safe)
            title, chk = self.pmenu.get("title") or "Playback", self.pmenu.get("checked")
            ov.show(O.PANEL, x, y, w, h,
                    lambda surf, r=rows, f=fx, t=top, n=fit, ti=title, c=chk: O.draw_menu(
                        surf, r, f, top=t, fit=n, title=ti, checked=c),
                    sig=("pmenu", title, tuple(rows), fx, top, chk, tuple(safe.values())))
        elif self.panel is not None and self.session == PLAYING:
            pm = self._panel_model(s)
            x, y, w, h = O.panel_rect(len(pm["rows"]), safe)
            ov.show(O.PANEL, x, y, w, h, lambda surf, pm=pm: O.draw_panel(surf, pm),
                    sig=("panel", repr(pm), tuple(safe.values())))
        elif not self.card:
            ov.hide(O.PANEL)
        if self.session != UP_NEXT or self.upnext is None or self.upnext["focus"] != "video":
            for i in range(4):
                ov.hide(O.FRAME0 + i)
        if self.skip is not None and self.session == PLAYING and not ov.text_mode \
                and self.panel is None and self.pmenu is None:
            lbl = self.skip["label"]
            x, y, w, h = O.skip_rect(lbl, safe, self.bar)
            ov.show(O.SKIP, x, y, w, h, lambda surf, l=lbl: O.draw_skip(surf, l),
                    sig=("skip", lbl, self.bar, tuple(safe.values())))
        else:
            ov.hide(O.SKIP)
        if self.session != PLAYING:
            ov.hide(O.BAR)
            return
        # bar or mini bar (same overlay id)
        if self.bar and not self.paused and now > self.bar_until and not self.scrub:
            self._hide_bar()
        m = self._bar_model(s)
        sig_t = int(m["scrub_s"] if m["scrub_s"] is not None else self.time_s)
        if self.bar:
            top = O.bar_top(safe)
            sig = ("bar", sig_t, self.paused, self.bar_row, self.bar_btn, int(self.duration_s),
                   m["title"], tuple(safe.values()), len(m["chapters"]))
            ov.show(O.BAR, 0, top, 641, 480 - top, lambda surf, m=m: O.draw_bar(surf, m, safe), sig=sig)
        elif now < self.mini_until or self.scrub:
            top = O.mini_top(safe)
            sig = ("mini", sig_t, int(self.duration_s), tuple(safe.values()), len(m["chapters"]))
            ov.show(O.BAR, 0, top, 641, 480 - top, lambda surf, m=m: O.draw_mini(surf, m, safe), sig=sig)
        else:
            ov.hide(O.BAR)

    def _render_upnext(self, ov, O, s, safe, now):
        u = self.upnext
        nxt = u["item"]
        full = u["pi"][1] if u["pi"] else nxt
        from .screens import ep_code
        img = None
        if self.image is not None and not u["still"]:
            ref = full.still if full.type == "episode" else (full.art or full.poster)
            iw, ih = O.upnext_thumb_size(safe)
            img = self.image(ref, iw, ih) if ref else None
        count = None if (u["still"] or u["go"]) else max(0, int(u["until"] - now + 0.999))
        m = {"show": full.show_title if full.type == "episode" else full.title,
             "line": ep_code(full, sep=" ") if full.type == "episode" else "",
             "title": full.title if full.type == "episode" else "",
             "count": count, "still": u["still"], "focus": u["focus"], "img": img}
        if u["still"]:
            cur = s.full or s.item
            m["show"] = cur.hero_title
            m["line"] = ep_code(cur, sep=" ") if cur.type == "episode" else ""
        x, y, w, h = O.upnext_card_rect(safe, still=u["still"])
        sig = ("upnext", nxt.id, count, u["still"], u["focus"], img is not None, tuple(safe.values()))
        ov.show(O.PANEL, x, y, w, h, lambda surf, m=m: O.draw_upnext(surf, m), sig=sig)
        if u["focus"] == "video":
            for i, r in enumerate(O.upnext_frame_rects(safe)):
                ov.show(O.FRAME0 + i, *r, O.draw_frame_edge, sig=("frame", r))

    def cancel(self):
        """B during STARTING: back to the UI at once; the worker cleans up later."""
        s = self.s
        if s is None or self.session != STARTING:
            return
        s.cancel.set()
        L.info("start of %s cancelled", s.item.id)
        self._discard(s)
        self.gen += 1                         # late callbacks are now stale
        self._end({"item_id": s.item.id, "server_id": s.item.server_id,
                   "position_ms": s.item.view_offset_ms, "watched": False, "error": None,
                   "never_loaded": True, "user_stop": True})

    # ------------------------------------------------------------ reports
    def _report(self, state, s=None, t_s=None, d_s=None):
        s = s or self.s
        if s is None or s.pi is None or _no_state(s) or s.channel is not None:
            return                            # an extra or a channel sends no timeline
        t_ms = int((self.time_s if t_s is None else t_s) * 1000)
        d_ms = int((d_s if d_s is not None else (self.duration_s or s.pi.duration_ms / 1000.0)) * 1000)
        item, prov, sess = s.full or s.item, s.provider, s.plex_session

        def send():
            if not s.finalized:               # never after the 'stopped' report
                prov.timeline(item, state, t_ms, d_ms, sess)
        if s.finalized:
            return
        self.run(send, lambda r: None, lambda e: L.info("timeline %s failed: %s", state, e))

    def _report_now(self):
        self.last_report = -1e9

    def _report_tick(self):
        if not self.loaded or self.time_s <= 0:
            return
        state = "paused" if self.paused else "playing"
        now = self.clock()
        if self.paused != self.report_pause:
            self.report_pause = self.paused
        elif now - self.last_report < REPORT_EVERY_S:
            return
        self.last_report = now
        self._report(state)

    # ------------------------------------------------------------ end
    def _result(self, s, error=None):
        if s is None:
            return {"item_id": None, "position_ms": 0, "watched": False, "error": error}
        if not self.loaded:
            # Nothing played: keep the resume point the screen already shows.
            return {"item_id": s.item.id, "server_id": s.item.server_id,
                    "position_ms": s.item.view_offset_ms,
                    "watched": False, "error": error, "never_loaded": True,
                    "user_stop": self.user_stop}
        dur = self.duration_s or (s.pi.duration_ms / 1000.0)
        # mpv also reports eof when the stream drops: trust it only near the end.
        eof = self.eof and (dur <= 0 or self.time_s / dur >= WATCHED_AT)
        pos = dur if eof else self.time_s
        mk = self._credits_marker(s)
        credits = mk is not None and self.time_s >= mk.start_ms / 1000.0 - 1   # Plex: the end
        done = eof or credits or (dur > 0 and pos / dur >= WATCHED_AT)
        return {"item_id": s.item.id, "server_id": s.item.server_id,
                "position_ms": 0 if done else int(pos * 1000),
                "watched": done, "error": error, "_pos": pos, "_dur": dur}

    def _finalize(self, s, res, foreground=False):
        """Final report for one file, once. Background unless the app is exiting. A channel:
        no report and no scrobble, only the transcode stop."""
        if s.channel is not None and not s.finalized:
            s.finalized = True
            stop_tc = self._tc_stopper(s)
            if stop_tc:
                self.run(stop_tc, lambda r: None, lambda e: L.info("transcode stop failed: %s", e))
            return
        if s.finalized or not self.loaded or s.pi is None:
            return
        s.finalized = True
        if _no_state(s):
            L.info("extra %s ended: no report", s.item.id)
            return
        item, prov, sess = s.full or s.item, s.provider, s.plex_session
        t_ms, d_ms, watched = int(res["_pos"] * 1000), int(res["_dur"] * 1000), res["watched"]
        stop_tc = self._tc_stopper(s)

        def report():
            prov.timeline(item, "stopped", t_ms, d_ms, sess)
            if watched:
                prov.scrobble(item)

        def send():
            # The 'stopped' report first, then the transcode stop, each one always.
            try:
                report()
            finally:
                if stop_tc:
                    stop_tc()
        L.info("stopped %s at %.0f/%.0fs watched=%s", item.id, res["_pos"], res["_dur"], watched)
        if foreground:
            # App exit: both inside the 1.5 s budget, side by side.
            ts = [threading.Thread(target=lambda f=f: _quiet(f), daemon=True)
                  for f in (report, stop_tc) if f]
            end = time.monotonic() + 1.5
            for t in ts:
                t.start()
            for t in ts:
                t.join(max(0.0, end - time.monotonic()))
        else:
            ev = threading.Event()
            self.finals = [e for e in self.finals if not e.is_set()] + [ev]
            self.run(send, lambda r: ev.set(),
                     lambda e: (L.info("final report failed: %s", e), ev.set()))

    def _finish(self, s, error=None):
        if self.session in (FINISHING, IDLE):
            return
        self.session = FINISHING
        res = self._result(s, error)
        self._finalize(s, res)
        self._discard(s)
        self._end(res)

    def _error(self, msg, copy=None):
        """Close mpv first so the pygame message is visible, then wait for A or B. A fixed
        queue first tries the next item."""
        s = self.s
        if s is not None and self.queue is not None and \
                self._fixed_fail(s, self.queue.index, msg):
            return
        if s is not None and s.channel is not None and self.ch is not None \
                and self._channel_skip(s, msg):
            return
        L.info("playback error: %s", msg)
        if s is not None:
            s.cancel.set()
            self.gen += 1                     # a late worker result must not start mpv now
            if self.loaded:
                self._finalize(s, self._result(s))
            self._discard(s)
            s.mpv = s.spawned = None
        self._drop_overlay()
        self.message, self.cause = copy or ERROR_COPY, msg
        self.session = ERROR

    def _fixed_fail(self, s, at, err):
        """A fixed queue: the item at queue position `at` cannot play. Toast, then the item
        after it; two failures in a row end the session (False: the caller goes on to its own
        stop path). A file that never loaded gets no report and no scrobble."""
        q = self.queue
        if not self._fixed() or s is None:
            return False
        if self.sleep is not None and self.sleep.at_end:
            return False                      # Sleep Timer: End of Video ends the list here
        self.fixed_fails += 1
        bad = q.items[at] if 0 <= at < len(q.items) else s.item
        L.info("queue item %s failed (%d in a row): %s", bad.id, self.fixed_fails, err)
        j = at + 1
        if self.fixed_fails >= 2 or j >= len(q.items):
            return False
        msg = "Can't play %s" % bad.title
        self.notices.append(msg)
        m = s.mpv
        if self.loaded and m is not None and m.alive and self.session in (PLAYING, UP_NEXT):
            # mpv is fine (the next file of an Up Next failed, or this file broke while it
            # played): the item after it loads in the same mpv.
            self.say(msg, 3.0)
            if self.session == UP_NEXT and self.upnext is not None:
                self.upnext.update(item=q.items[j], at=j, pi=None, err=None, go=True)
                self._upnext_fetch(s, self.upnext)
            else:
                self._open_upnext(s, at=j, check_still=False)
            return True
        # Nothing loaded (a start failure, a time-out, a file that does not open): a new mpv.
        s.cancel.set()
        self.gen += 1
        if self.loaded:
            res = self._result(s)
            self._finalize(s, res)
            self.files.append(self._clean(res))
        self._discard(s)
        s.mpv = s.spawned = None
        self._drop_overlay()
        nxt = q.items[j]
        keep = (self.files, self.queue, self.last_input)
        s2 = Session(self.gen, nxt, self._item_prov(s, nxt) or _Gone(), True)
        self.s, self.session, self.message = s2, STARTING, ""
        self._reset_facts()
        self.files, self.queue, self.last_input = keep
        q.index = j
        self.safe_mode = False               # a new item: hardware decode again
        self._spawn(s2)
        return True

    def _drop_overlay(self):
        if self.ov is not None:
            self.ov.cleanup()
            self.ov = None

    @staticmethod
    def _clean(res):
        return {k: v for k, v in res.items() if not k.startswith("_")}

    def _end(self, res):
        self._drop_overlay()
        res = self._clean(res)
        if self.s is not None:
            # The occurrence that played last (a playlist entry).
            res.setdefault("entry", self.s.item.extra.get("occ"))
        q = self.queue
        # The last item of a fixed queue played to its end (the local queue empties then).
        res["queue_end"] = bool(q is not None and q.fixed and q.next is None and res.get("watched"))
        # Every file this session finished, in order, so the UI updates each one.
        files = list(getattr(self, "files", []))
        if _no_state(self.s):
            files = []                      # an extra changes no played mark on any screen
        elif res.get("item_id") and not res.get("never_loaded"):
            files.append({k: res.get(k) for k in ("item_id", "server_id", "position_ms", "watched")})
        res["files"] = files
        if self.ch is not None:               # a channel: no watch state
            ch = self.s.channel if self.s is not None else None
            res.update(files=[], watched=False, channel=ch.cid if ch else None,
                       guide=self.ch["guide"])
            self.ch = self.live = None
        self.session = IDLE
        self.s = None
        self.results.append(res)

    # ------------------------------------------------------------ Live TV
    def _banner(self, cid, secs):
        if self.ch is not None:
            self.ch["banner"], self.ch["until"] = cid, self.clock() + secs

    def _banner_on(self):
        return self.ch is not None and self.ch["banner"] is not None \
            and self.clock() < self.ch["until"]

    def _channel_want(self, s, cid):
        """Up/Down/LB: the banner of the target at once (from the tape, no request); the load
        after CH_WAIT_S with no other press."""
        if cid is None:
            return
        self.ch["want"], self.ch["due"] = cid, self.clock() + CH_WAIT_S
        self._banner(cid, BANNER_CHANGE_S)

    def _channel_action(self, s, a, rep):
        """True when a channel control took the press (an open menu or track panel
        has the press first)."""
        if self.session not in (STARTING, PLAYING) or self.panel is not None \
                or self.pmenu is not None:
            return False
        if self.ch.get("gv") is not None:
            return self._guide_action(s, a, rep)
        cur = self.ch["want"] or self.ch["target"] or s.channel.cid
        if a in (inputs.UP, inputs.DOWN, NEXT, PREV):
            self.ch["dir"] = 1 if a in (inputs.UP, NEXT) else -1   # the neighbour fetched first
            self._channel_want(s, self.live.step(cur, self.ch["dir"]))
            return True
        if a == inputs.LB and not rep:
            self._channel_want(s, self.live.last(s.channel.cid))
            return True
        if self.session == STARTING:
            if a == inputs.BACK and not rep and self._banner_on():
                self.ch["banner"], self.ch["want"] = None, None
                return True                   # the first B closes the banner
            return False                      # B cancels; nothing else until a picture
        if rep:
            return a in (inputs.LEFT, inputs.RIGHT)
        if a == inputs.RB:
            if self.guide_view is not None and self.ov is not None and not self.ov.text_mode:
                self.ch["gv"] = self.guide_view(s.channel.cid)   # over the picture
                L.info("guide over the picture")
                self.ch["banner"] = None
                if self.bar:
                    self._hide_bar()
            else:
                self.ch["guide"] = True       # RB opens the guide
                self._stop(s)
        elif a in (inputs.LEFT, inputs.RIGHT):
            self._banner(s.channel.cid, BANNER_A_S)   # live: no seek
        elif a in (inputs.LT, inputs.RT):
            pass
        elif a == inputs.OK:
            if self._banner_on() and self.ch["banner"] == s.channel.cid:
                self._toggle_pause(s, s.mpv)
            self._banner(s.channel.cid, BANNER_A_S)
        elif a == inputs.BACK:
            if self._banner_on():
                self.ch["banner"], self.ch["want"] = None, None
            else:
                self._stop(s)                 # the guide opens on this channel
        else:
            return False
        return True

    def _guide_action(self, s, a, rep):
        """The guide over the picture has every press but Quit and Menu (they close it and go
        on as usual). A on a programme that is on tunes its channel at once."""
        ch = self.ch
        if a in (inputs.QUIT, inputs.MENU):
            ch["gv"] = None
            return False
        if rep and a not in (inputs.UP, inputs.DOWN, inputs.LEFT, inputs.RIGHT):
            return True
        r = ch["gv"].handle(a)
        if r is None:
            return True
        if r[0] == "toast":
            self.say(r[1])
            return True
        ch["gv"] = None
        if r[0] == "tune" and r[1] != (ch["want"] or ch["target"] or s.channel.cid):
            self._channel_want(s, r[1])
            ch["due"] = self.clock()          # one press: no wait for more presses
        return True

    def _channel_tick(self, s):
        ch = self.ch
        if ch["want"] is None or ch["busy"] or self.clock() < ch["due"]:
            self._prefetch_tick(s)
            return
        cid, ch["want"] = ch["want"], None
        if cid == s.channel.cid:
            return
        t = self.live.tune(cid)
        if t is None:
            self.say("Can't tune channel %s" % cid_number(self.live, cid))
            self._banner(s.channel.cid, BANNER_CHANGE_S)
            return
        ch["target"] = cid
        self._channel_fetch(s, t, change=True)

    def _lineup_gen(self):
        return getattr(getattr(self.live, "lineup", None), "version", 0)

    def _prefetch_tick(self, s):
        """Fetch ahead, one request at a time and only while no tune is on its way: the next
        item NEXT_EARLY_S before the end, then, after PREFETCH_S on this channel, the channel
        in the last direction and then the other one."""
        ch = self.ch
        pf, now = ch["pf"], self.clock()
        if pf.busy is not None or ch["busy"] or ch["want"] is not None or not self.loaded \
                or now < ch["pf_next"] or s.pi is None:
            return
        ch["pf_next"] = now + 1.0
        todo = []
        if self.duration_s and self.duration_s - self.time_s <= NEXT_EARLY_S:
            todo.append(self.live.next_tune(s.channel, ch["bad"], behind=False))
        if ch["settled"] is not None and now - ch["settled"] >= PREFETCH_S:
            cur = s.channel.cid
            for d in (ch["dir"], -ch["dir"]):
                cid = self.live.step(cur, d)
                if cid is not None and cid != cur:
                    todo.append(self.live.tune(cid))
        gen = self._lineup_gen()
        for t in todo:
            if t is None:
                continue
            prov = self.live.provider(t)
            if prov is not None and pf.wanted(t, prov, gen):
                self._prefetch(t, prov, gen)
                return

    def _prefetch(self, t, prov, gen):
        ch, pf = self.ch, self.ch["pf"]
        sess = str(uuid.uuid4())
        pf.busy = pf.key(t)

        def got(r):
            def put(_cur):
                tc = r[0].mode == "transcode"
                if self.ch is not ch or not pf.put(t, prov, gen, r):
                    if self.ch is ch:
                        pf.busy = None
                        pf.fail(t)            # not kept: wait PREFETCH_RETRY_S (no storm)
                    if tc:                    # never leave a server decision open
                        self.run(lambda: _quiet(lambda: prov.stop_transcode(sess)),
                                 lambda _r: None, lambda _e: None)
                    return
                pf.busy = None
            self._post(self.gen, put)

        def failed(e):
            def mark(_cur):
                if self.ch is ch:
                    pf.busy = None
                    pf.fail(t)
            L.info("fetch ahead failed: %s", e)
            self._post(self.gen, mark)
        self.run(lambda: _play_info(prov, t.item, False, {"session": sess, "at_s": t.at}),
                 got, failed)

    def _channel_fetch(self, s, t, change):
        """play_info of tune t on a worker; the old picture stays until it answers.
        A result fetched ahead loads at once."""
        ch, gen = self.ch, s.gen
        prov = self.live.provider(t)
        sess = str(uuid.uuid4())
        ch["busy"] = True
        idle = dict(busy=False, target=None) if change else dict(busy=False)
        hit = ch["pf"].take(t, prov, self._lineup_gen()) if prov is not None else None
        if hit is not None:
            L.info("channel tune: fetched ahead")
            self._post(gen, lambda cur: self._channel_switch(s, t, hit, sess, prov, change)
                       if cur and self.s is s else ch.update(idle))
            return

        def got(r):
            self._post(gen, lambda cur: self._channel_switch(s, t, r, sess, prov, change)
                       if cur and self.s is s else ch.update(idle))

        def failed(e):
            self._post(gen, lambda cur: self._channel_failed(s, t, e, change)
                       if cur and self.s is s else ch.update(idle))
        self.run(lambda: _play_info(_need(prov), t.item, False, {"session": sess, "at_s": t.at}),
                 got, failed)

    def _channel_switch(self, s, t, r, sess, prov, change):
        """Load tune t in the same mpv (as _switch_to, with no report and no file result)."""
        ch = self.ch
        ch["busy"] = False
        if change:
            ch["target"] = None
            if ch["want"] is not None and ch["want"] != t.cid:
                if r[0].mode == "transcode":  # a newer press: tune that one, not this one
                    self.run(lambda: _quiet(lambda: prov.stop_transcode(sess)),
                             lambda _r: None, lambda _e: None)
                return
        m = s.mpv
        if m is None or not m.alive:
            return
        self._finalize(s, None)
        if self.bar:
            self._hide_bar()
        keep = (self.ov, self.last_input)
        self.gen += 1
        s2 = Session(self.gen, t.item, prov, False)
        s2.channel, s2.plex_session, s2.opts = t, sess, {"at_s": t.at}
        s2.pi, s2.full = r
        s2.mpv = s2.spawned = m
        self.s, self.session, self.message = s2, STARTING, ""
        self._reset_facts()
        self.ov, self.last_input = keep
        self.files, self.queue = [], None
        self.switching = True
        if change:
            ch["paused"], ch["bad"], ch["skips"] = False, set(), 0
            ch["owed"] = None                 # the old channel's end or error no longer matters
            ch["settled"] = None              # neighbours: PREFETCH_S after the new picture
            self.live.note(t.cid)
        self._load(s2, m)

    def _channel_failed(self, s, t, e, change):
        self.ch["busy"] = False
        L.info("channel tune failed: %s", e)
        if change:                            # The old channel stays
            self.ch["target"] = None
            self.say("Server not available" if _down(e) else
                     "Can't tune channel %s" % cid_number(self.live, t.cid))
            self._banner(s.channel.cid, BANNER_CHANGE_S)
            owed, self.ch["owed"] = self.ch.get("owed"), None
            if owed == "skip" and not self._channel_skip(s, e):
                self._finish(s, error=str(e))  # the old item died during the tune
            elif owed == "next":
                self._channel_next(s)
            return
        self.ch["bad"].add(t.entry.key)       # the next item failed: the one after it
        self.ch["skips"] += 1
        nt = self.live.next_tune(t, self.ch["bad"], behind=False) \
            if self.ch["skips"] < CH_SKIPS else None
        if nt is None:
            self._finish(s, error=str(e))
        else:
            self._channel_fetch(s, nt, change=False)

    def _channel_next(self, s):
        """End of an item: the next tape item in the same mpv."""
        if self.ch["busy"]:
            self.ch["owed"] = "next"          # run it if the tune on its way fails
            return
        if self.sleep is not None and self.sleep.at_end:
            self._finish(s)                   # Sleep Timer: End of Episode
            return
        nt = self.live.next_tune(s.channel, self.ch["bad"], behind=not self.ch["paused"])
        if nt is None:
            self._finish(s)
        else:
            self._channel_fetch(s, nt, change=False)

    def _channel_skip(self, s, err):
        """An item that does not play: the next playable one."""
        ch = self.ch
        if ch["busy"]:
            ch["owed"] = "skip"               # a tune is on its way; if it fails, skip then
            return True
        self.started_at = self.clock()        # START_TIMEOUT_S counts again
        ch["bad"].add(s.channel.entry.key)
        ch["skips"] += 1
        nt = self.live.next_tune(s.channel, ch["bad"], behind=False) \
            if ch["skips"] < CH_SKIPS else None
        if nt is None:
            return False
        m = s.mpv
        if m is not None and m.alive and self.session in (PLAYING, STARTING) and s.pi is not None:
            self._channel_fetch(s, nt, change=False)
            return True
        s.cancel.set()                        # nothing to keep: a new start
        self.gen += 1
        self._discard(s)
        s.mpv = s.spawned = None
        self._drop_overlay()
        s2 = Session(self.gen, nt.item, self.live.provider(nt) or _Gone(), False)
        s2.channel, s2.opts = nt, {"at_s": nt.at}
        self.s, self.session, self.message = s2, STARTING, ""
        self._reset_facts()
        self.safe_mode = False               # a new item: hardware decode again
        self._spawn(s2)
        return True

    def _render_guide(self, ov, O):
        gv = self.ch.get("gv") if self.ch is not None else None
        if gv is None or ov.text_mode:
            if gv is not None:
                self.ch["gv"] = None          # an overlay that cannot draw: no hidden guide
            ov.hide(O.GUIDE)
            return
        gv.update()
        y = gv.top_y()
        ov.show(O.GUIDE, 0, y, 641, 480 - y, gv.draw_over, sig=gv.sig())

    def _render_banner(self, ov, O, s, safe, now):
        ch = self.ch
        if ch is None or ov.text_mode or not self._banner_on() or self.pmenu is not None \
                or self.panel is not None or ch.get("gv") is not None:
            ov.hide(O.BANNER)
            return
        cid = ch["banner"]
        if cid == s.channel.cid and ch["want"] is None:
            m = self.live.banner(cid, s.channel, pos_s=self.time_s if self.loaded else None,
                                 dur_s=self.duration_s or None)
            if m is not None and self.paused and self.loaded:
                m = dict(m, left="Paused")    # behind the clock until the next change
        else:
            m = self.live.banner(cid)
        if m is None:
            ov.hide(O.BANNER)
            return
        x, y, w, h = O.banner_rect(safe)
        sig = ("banner", tuple(sorted((k, v if k != "frac" else int(v * 100))
                                      for k, v in m.items())), tuple(safe.values()))
        ov.show(O.BANNER, x, y, w, h, lambda surf, m=m: O.draw_banner(surf, m), sig=sig)

    def pop_result(self):
        return self.results.popleft() if self.results else None

    def shutdown(self):
        """App exit (SIGTERM, Alt+F4): one final report in the foreground, then kill mpv."""
        s = self.s
        end = time.monotonic() + 1.5
        if s is not None:
            s.cancel.set()
            if self.session in (PLAYING, UP_NEXT) and self.loaded:
                self._finalize(s, self._result(s), foreground=True)
            stop_tc = self._tc_stopper(s)    # started, not loaded: stop it inside the budget
            if stop_tc:
                t = threading.Thread(target=lambda: _quiet(stop_tc), daemon=True)
                t.start()
                t.join(max(0.0, end - time.monotonic()))
        # A stop just before exit: give its final report the rest of the budget.
        for ev in self.finals:
            ev.wait(max(0.0, end - time.monotonic()))
        self._drop_overlay()
        if s is not None:
            m = s.mpv or s.spawned
            if m is not None:
                m.kill(wait=1.0)
        self.session = IDLE
        self.s = None


def cid_number(surfer, cid):
    ch = surfer.lineup.channel(cid) if surfer is not None else None
    return "%02d" % ch.number if ch is not None else ""


def plex_mod():
    from . import plex
    return plex


class _Gone:
    """The provider of a queue item whose server is not connected."""

    def play_info(self, item, resume=True):
        raise plex_mod().PlexError("the server of this item is not connected")


def _need(prov):
    if prov is None:
        raise plex_mod().PlexError("the server of this item is not connected")
    return prov


def _down(e):
    """A server that does not answer or is not connected, not a bad item."""
    t = str(e)
    return isinstance(e, plex_mod().PlexError) and ("network" in t or "not connected" in t
                                                     or "no reachable" in t)


def _quiet(fn):
    try:
        fn()
    except Exception as e:
        L.info("final report failed: %s", e)
