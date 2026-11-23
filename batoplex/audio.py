"""AudioPlayer: a second mpv with no video, for show theme music and music.

One process, one owner at a time: "theme" or "music". While music owns it,
the theme calls do nothing. Music keeps a two-entry mpv playlist [current, next] so a track
change at the end of a file has no gap; MusicController drives it.

The process starts on first use, in a worker thread, and
then stays idle. All commands and fades run on the UI thread from tick(). Before a video
starts the theme stops at once, so the audio device is free for the video mpv.
"""
import os
import threading
import time

from . import log, loudness, sounds
from .mpv import Mpv

L = log.get("audio")

FADE_IN_S, FADE_OUT_S = 1.0, 0.3
OPTS_AFTER_INDEX = (0, 38)          # mpv 0.38 put an index argument before the options map


def header_value(headers):
    """The http-header-fields text. A comma splits the option list, so a header with a comma
    is left out (Plex tokens have none). Only the header name goes in the log."""
    out = []
    for k, v in (headers or {}).items():
        if "," in str(k) or "," in str(v):
            L.info("header %s has a comma: left out", k)
            continue
        out.append("%s: %s" % (k, v))
    return ",".join(out)


def _thread_runner(fn, done, fail):
    def work():
        try:
            r = fn()
        except Exception as e:
            fail(e)
            return
        done(r)
    threading.Thread(target=work, daemon=True).start()


class AudioPlayer:
    def __init__(self, binary="mpv", clock=time.monotonic, run=_thread_runner, make_mpv=None,
                 extra=None):
        self.binary = binary
        self.extra = list(extra or [])      # e.g. --ao=null in a scripted test run
        self.owner = None                   # None | "theme" | "music"
        self.on_music = None                # callback(event dict) while music owns mpv
        self.want_music = None              # (PlayInfo, next PlayInfo) to open once mpv is up
        self.cur_limit = None               # peak limit (dB) of the entry that plays
        self.next_limit = None              # the same for the next entry
        self.music_paused = False
        self.mlist = 0                      # music entries in the mpv playlist (0..2)
        self.pending = None                 # the mpv that is still starting
        self.clock = clock
        self.run = run
        self.make_mpv = make_mpv or (lambda: Mpv(binary, "audio"))
        self.m = None
        self.starting = False
        self.failed = False
        self.want = None          # (url, headers, volume) to play once mpv is up
        self.playing = None       # url now playing
        self.vol = 0.0            # current volume
        self.target = 0.0
        self.fade_from = 0.0
        self.fade_t0 = 0.0
        self.fade_s = 0.0
        self.stop_after_fade = False
        self.inbox = []
        self.sock = "/tmp/batoplex-audio-%d.sock" % os.getpid()

    # ------------------------------------------------------------ control
    def play(self, url, headers=None, volume=50):
        """Play url once, fading in. A second call with the same url does nothing."""
        if self.failed or self.owner == "music":
            return
        if self.playing == url:
            return
        sounds.close()                      # before mpv opens the device
        self.owner = "theme"
        self.want = (url, headers or {}, volume)
        if self.m is None:
            self._start()
        else:
            self._apply()

    def stop(self, fade=True):
        """Fade out and stop. fade=False stops at once (a video is about to start)."""
        if self.owner == "music":
            return
        self.want = None
        if self.m is None or self.playing is None:
            return
        if not fade:
            self.m.set("volume", 0)
            self.m.cmd("stop")
            self.playing, self.vol, self.fade_s = None, 0.0, 0.0
            return
        self._fade(0.0, FADE_OUT_S)
        self.stop_after_fade = True

    def tick(self):
        while self.inbox:
            self.inbox.pop(0)()
        m = self.m
        if m is None:
            return
        for ev in m.events():
            if ev.get("event") in ("shutdown", "ipc-closed"):
                self.m, self.playing = None, None
                if self.owner == "music" and self.on_music:
                    self.owner, self.mlist = None, 0
                    self.on_music({"event": "lost"})
                return
            if self.owner == "music":
                if self.on_music:
                    self.on_music(ev)
                continue
            if ev.get("event") == "end-file" and ev.get("reason") in ("eof", "error"):
                self.playing = None
        if self.fade_s:
            k = min(1.0, (self.clock() - self.fade_t0) / self.fade_s)
            v = self.fade_from + (self.target - self.fade_from) * k
            if int(v) != int(self.vol):
                m.set("volume", int(v))
            self.vol = v
            if k >= 1.0:
                self.fade_s = 0.0
                if self.stop_after_fade:
                    self.stop_after_fade = False
                    m.cmd("stop")
                    self.playing = None

    def shutdown(self):
        """App exit: reap the audio mpv, also one that is still starting."""
        m, self.m = self.m, None
        p, self.pending = self.pending, None
        for x in (m, p):
            if x is not None:
                try:
                    x.kill(wait=1.0)
                except Exception as e:          # not fatal at exit
                    L.info("audio kill failed: %s", e)

    # ------------------------------------------------------------ music
    def music_open(self, pi, next_pi=None):
        """Take the player for music and play pi (a PlayInfo) now, with next_pi queued after
        it. Each entry carries its own fallback gain. The headers are per
        entry too, but mpv's playlist prefetch opens next_pi with pi's options (seen on a
        live mpv), so next_pi must always be on pi's server: MusicController sets no next entry
        across servers."""
        if self.failed:
            return False
        sounds.close()
        if self.owner == "theme" or self.playing:
            self.want, self.fade_s, self.stop_after_fade = None, 0.0, False
            if self.m is not None:
                self.m.cmd("stop")
            self.playing = None
        self.owner = "music"
        self.want_music = (pi, next_pi)
        self.music_paused = False
        if self.m is None:
            self._start()
        else:
            self._apply_music()
        return True

    def music_next(self, pi):
        """Replace the entry after the current one with pi, a PlayInfo (None: remove it)."""
        m = self.m
        if self.owner != "music":
            return
        if self.want_music is not None:            # mpv still starts: change what it opens
            self.want_music = (self.want_music[0], pi)
            return
        if m is None:
            return
        if self.mlist >= 2:
            m.cmd("playlist-remove", 1)
            self.mlist = 1
        if pi:
            self._load_music(pi, "append")
            self.mlist = 2

    def music_drop_first(self):
        """After mpv moved to entry 1 (the track ended): remove entry 0, so the playlist is
        [current] again; the caller then sets the next entry."""
        if self.owner == "music" and self.m is not None and self.mlist >= 2:
            self.m.cmd("playlist-remove", 0)
            self.mlist = 1
            self.cur_limit = self.next_limit

    def music_pause(self, on):
        if self.owner != "music":
            return
        self.music_paused = bool(on)               # kept for an mpv that still starts
        if self.m is not None and self.want_music is None:
            self.m.set("pause", bool(on))

    def music_seek(self, s, absolute=False):
        if self.owner == "music" and self.m is not None:
            self.m.cmd("seek", s, "absolute" if absolute else "relative")

    def music_close(self):
        """End music: stop at once and give the player back (themes work again)."""
        if self.owner != "music":
            return
        self.owner, self.want_music, self.mlist = None, None, 0
        self.music_paused = False
        if self.m is not None:
            self.m.set("pause", False)
            self.m.cmd("stop")

    # ------------------------------------------------------------ internals
    def _fade(self, target, secs):
        self.fade_from, self.target = self.vol, float(target)
        self.fade_t0, self.fade_s = self.clock(), secs
        self.stop_after_fade = False

    def _apply(self):
        url, headers, volume = self.want
        self.want = None
        m = self.m
        m.set("volume", 0)
        self.vol = 0.0
        m.set("http-header-fields", ",".join("%s: %s" % kv for kv in headers.items()))
        m.cmd("loadfile", url, "replace")
        self.playing = url
        self._fade(volume, FADE_IN_S)

    def _apply_music(self):
        pi, npi = self.want_music
        self.want_music = None
        m = self.m
        m.set("pause", self.music_paused)
        m.set("volume", 100)
        self.vol = 100.0
        # Clear the global header first. A per-file header gives the global value back when
        # its entry ends, and a theme's token must never reach a radio server.
        m.set("http-header-fields", "")
        self._load_music(pi, "replace")
        self.mlist = 1
        if npi:
            self._load_music(npi, "append")
            self.mlist = 2

    def _load_music(self, pi, flag):
        """loadfile for one music entry, with its own headers and fallback gain as per-file
        options. Every option value is a string: mpv refuses other JSON types."""
        m = self.m
        g = pi.gain_db
        if flag == "replace":
            self.cur_limit = pi.gain_max_db
        else:
            g = loudness.gapless_gain(g, self.cur_limit)
            self.next_limit = pi.gain_max_db
        hdr = header_value(pi.headers)
        v = getattr(m, "version", None)
        if not v or tuple(v) == (0, 0):
            # Unknown version: the options argument form is unknown. The headers go global
            # (both entries are on one server); no gain option.
            m.set("http-header-fields", hdr)
            m.cmd("loadfile", pi.url, flag)
            return
        opts = {"http-header-fields": hdr}
        if g is not None:
            opts["replaygain-fallback"] = "%.2f" % g
        if tuple(v) >= OPTS_AFTER_INDEX:
            m.cmd("loadfile", pi.url, flag, -1, opts)
        else:
            m.cmd("loadfile", pi.url, flag, opts)

    def _start(self):
        if self.starting:
            return
        self.starting = True
        m = self.make_mpv()
        self.pending = m
        args = [self.binary, "--no-config", "--no-video", "--idle=yes", "--force-window=no",
                "--audio-display=no", "--msg-level=all=error", "--volume=0", "--keep-open=no",
                "--ytdl=no", "--no-terminal", "--gapless-audio=weak", "--prefetch-playlist=yes",
                "--replaygain=album"] + self.extra

        def work():
            m.start(args, self.sock)
            return m

        def done(mm):
            self.inbox.append(lambda: self._ready(mm))

        def fail(e):
            self.inbox.append(lambda: self._fail(e))
        self.run(work, done, fail)

    def _ready(self, m):
        if self.pending is not m:
            return                              # shut down while it started
        self.starting, self.m, self.pending = False, m, None
        m.observe("time-pos", "duration", "pause", "playlist-pos", "paused-for-cache",
                  "metadata/by-key/icy-title")                # radio: the song now
        if self.want_music is not None:
            self._apply_music()
        elif self.want is not None:
            self._apply()

    def _fail(self, e):
        self.starting, self.failed, self.pending = False, True, None
        if self.owner == "music" and self.on_music:
            self.owner = None
            self.on_music({"event": "lost"})
        L.info("audio player did not start: %s", e)
