"""MusicController: the music queue and its reports.

No pygame. It drives AudioPlayer's music API and reads its events. The queue is local and
belongs to one server. mpv holds [current, next], so the end of a track goes on to the next one
with no gap; a skip (Next, Previous, a jump) loads the new track at once.

Reports use the video player's calls: provider.timeline(item, state, time_ms, duration_ms,
session_id) and provider.scrobble(item). A completed track scrobbles; a skipped one scrobbles
at 90 % or more. Worker results carry the session number, and a result for an old session is
dropped (sign-out, profile switch, a video start).
"""
import random
import threading
import time
import uuid

from . import log

L = log.get("music")

REPORT_EVERY_S = 10.0
PAUSE_END_S = 600.0
PREV_RESTART_S = 3.0
REFILL_AT = 3
ADVANCE_WAIT_S = 2.0                         # eof with a next entry: wait for playlist-pos 1
RETRY_S = (2.0, 5.0, 10.0)                   # radio: a dropped stream opens again
LIVE_OK_S = 10.0                             # radio: this long playing resets the retries
SCROBBLE_AT = 0.9
MAX_ERRORS = 3
REPEATS = ("off", "all", "one")


def _thread_runner(fn, done, fail):
    def work():
        try:
            r = fn()
        except Exception as e:
            fail(e)
            return
        done(r)
    threading.Thread(target=work, daemon=True).start()


def _find(seq, x):
    """The index of x in seq by identity (Items compare equal by value), or None."""
    return next((i for i, y in enumerate(seq) if y is x), None)


class MusicController:
    def __init__(self, audio, clock=time.monotonic, run=_thread_runner, post=None, toast=None,
                 rng=random):
        self.audio = audio
        self.clock, self.run, self.rng = clock, run, rng
        self.post = post or (lambda fn: fn())   # deliver a worker result on the UI thread
        self.toast = toast or (lambda msg: None)
        audio.on_music = self._event
        self.sid = 0                    # session number: drops late worker results
        self.provider = None
        self.items, self.order = [], []
        self.index = 0
        self.state = "idle"             # idle | loading | playing | paused
        self.pos_s, self.dur_s = 0.0, 0.0
        self.shuffle, self.repeat = False, "off"
        self.sleep_end = lambda: False       # the app: Sleep Timer is at End of Track
        self.stamp = lambda: None            # the app: its source generation (src_gen)
        self.on_played = lambda t, stamp: None   # the app: a scrobble went through
        self.version = 0
        self.mark_version = 0
        self.session_id = None
        self.errors = 0
        self.next_item = None           # the queue Item mpv holds as its next entry
        self.last_report = 0.0
        self.paused_at = None
        self.cache_wait = False
        self.refill, self.refilling = None, False
        self.servers = set()
        # Radio: the queue is the user's stations; no reports, no next entry,
        # Next/Previous change station and wrap, a drop opens the station again.
        self.live = False
        self.icy = ""
        self.retry_at = None
        self.at_end = False             # paused while mpv was idle at the end (pause)
        self.adv_by = None              # eof with a next entry set: mpv must move to it by then
        self.gone = None                # a removed station that plays on

    # ------------------------------------------------------------ state
    @property
    def active(self):
        return self.state != "idle"

    @property
    def track(self):
        return self.items[self.index] if self.active and 0 <= self.index < len(self.items) else None

    def _changed(self, tick=False):
        """version: any change (Now Playing, Up Next). mark_version: what the top bar mark
        shows (state, track, title), not the position, so other screens do not redraw in
        full every second while music plays."""
        self.version += 1
        if not tick:
            self.mark_version += 1

    @property
    def now_title(self):
        """The text for the top-bar mark: the song (radio: the ICY title, else the station)."""
        t = self.track
        if t is None:
            return ""
        if self.live:
            from .radio import split_title
            return split_title(self.icy)[0] or t.title
        return t.title

    def play_live(self, stations, index):
        """Radio: play stations[index]; the station list is the queue (play() sees the
        station type and sets live)."""
        self.play(stations, index, None)

    # ------------------------------------------------------------ session
    def play(self, items, index, provider, shuffle=False, refill=None, quiet=False):
        """A new session: the queue is items, from items[index]. refill() -> more tracks, on a
        worker: a radio station asks for more when 3 or fewer are left.
        quiet: no timeline reports and no scrobbles (the Weather screen saver's playlist:
        nobody chose these tracks)."""
        if self.active:
            self.stop()
        if not items:
            return
        self.quiet = quiet
        self.refill, self.refilling = refill, False
        self.live = bool(items) and items[0].type == "station"
        self.icy, self.retry_at, self.gone = "", None, None
        self.sid += 1
        self.provider = provider
        self.servers = {i.server_id for i in items}   # Play Next / Add to Queue take only these
        self.items = list(items)
        self.order = list(items)                 # the order with shuffle off
        self.index = max(0, min(index, len(items) - 1))
        self.shuffle, self.errors = False, 0
        self.session_id = str(uuid.uuid4())
        if shuffle:
            self.rng.shuffle(self.items)
            self.index, self.shuffle = 0, True
        L.info("music: %d tracks from %d%s", len(items), self.index + 1,
               " (shuffle)" if shuffle else "")
        self._load(self.index)

    def stop(self, report=True, wait_s=0.0):
        """End the session. wait_s: wait up to this long for the last report (exit, and a
        video that starts next)."""
        if not self.active:
            return
        t, pos, prov, sess = self.track, self.pos_s, self.provider, self.session_id
        self.sid += 1
        self.state, self.next_item, self.paused_at = "idle", None, None
        self.live, self.retry_at, self.gone, self.adv_by = False, None, None, None
        self.at_end = False
        self.audio.music_close()
        self._changed()
        L.info("music stopped")
        if report and t is not None:
            self._report(t, "stopped", pos, prov=prov, sess=sess, wait_s=wait_s)

    # ------------------------------------------------------------ controls
    def toggle(self):
        if self.state == "playing":
            self.pause()
        elif self.state == "paused":
            self.resume()

    def pause(self):
        if self.state != "playing":
            return
        if self.adv_by is not None:          # mpv is idle at the end: hold here, and Play
            self.adv_by, self.at_end = None, True   # opens the next track (resume)
        self.audio.music_pause(True)
        self.state, self.paused_at = "paused", self.clock()
        self._report(self.track, "paused", self.pos_s)
        self._changed()

    def resume(self):
        if self.state != "paused":
            return
        if self.at_end:                      # paused at an idle end (pause): go on now
            self.at_end, self.next_item = False, None
            self.state = "playing"
            self._finish()
            return
        if self.live:                        # radio: Play opens the stream again, live
            self.sid += 1
            self._load(self.index)
            return
        self.audio.music_pause(False)
        self.state, self.paused_at = "playing", None
        self._report(self.track, "playing", self.pos_s)
        self._changed()

    def next(self):
        if not self.active or (self.live and len(self.items) < 2):
            return
        ni = self._next_index(manual=True)
        if ni is None:
            self.stop()
            return
        self._skip(ni)

    def prev(self):
        if not self.active:
            return
        if self.live:
            if len(self.items) > 1:
                self._skip((self.index - 1) % len(self.items))
            return
        if self.adv_by is not None or self.at_end:   # mpv is idle at the end: open it again
            self.adv_by, self.at_end = None, False
            self._skip(self.index)
            return
        if self.pos_s > PREV_RESTART_S or self.index == 0:
            self.audio.music_seek(0, absolute=True)
            self.pos_s = 0.0
            self._changed()
            return
        self._skip(self.index - 1)

    def jump(self, i):
        if self.active and 0 <= i < len(self.items) and i != self.index:
            self._skip(i)

    def seek(self, delta_s):
        if self.active and self.state != "loading":
            self.audio.music_seek(delta_s)
            self.pos_s = max(0.0, min(self.dur_s or 1e9, self.pos_s + delta_s))
            self._changed()

    def set_shuffle(self, on):
        if not self.active or on == self.shuffle:
            return
        cur = self.items[self.index]
        if on:
            rest = self.items[:self.index] + self.items[self.index + 1:]
            self.rng.shuffle(rest)
            self.items, self.index = [cur] + rest, 0
        else:
            self.items = list(self.order)
            self.index = next((i for i, t in enumerate(self.items) if t is cur), 0)
        self.shuffle = on
        self._refresh_next()
        self._maybe_refill()                  # Shuffle Off can leave no track after this
        self._changed()

    def cycle_repeat(self):
        self.repeat = REPEATS[(REPEATS.index(self.repeat) + 1) % len(REPEATS)]
        if self.active:
            self._refresh_next()
        self._changed()
        return self.repeat

    def play_next(self, items):
        if not self.active or not items:
            return
        at = self.index + 1
        self.items[at:at] = list(items)
        cur = _find(self.order, self.items[self.index])
        cur = len(self.order) - 1 if cur is None else cur
        self.order[cur + 1:cur + 1] = list(items)
        self._refresh_next()
        self._changed()

    def add(self, items):
        if not self.active or not items:
            return
        self.items += list(items)
        self.order += list(items)
        self._refresh_next()                  # the last track gets a next one
        self._changed()

    def drop_stations(self, urls):
        """Radio: these station URLs left radio.m3u; they leave the live
        queue too. The station that plays plays on until the user changes station."""
        if not self.live or not urls:
            return
        cur = self.track
        self.items = [t for t in self.items if t is cur or t.extra.get("url") not in urls]
        self.order = [t for t in self.order if _find(self.items, t) is not None]
        self.index = _find(self.items, cur) or 0
        if cur is not None and cur.extra.get("url") in urls:
            self.gone = cur
        self._changed()

    def remove(self, i):
        if not self.active or not 0 <= i < len(self.items) or i == self.index:
            return
        t = self.items.pop(i)
        k = _find(self.order, t)
        if k is not None:
            del self.order[k]
        if i < self.index:
            self.index -= 1
        self._refresh_next()
        self._maybe_refill()
        self._changed()

    # ------------------------------------------------------------ tick
    def tick(self):
        if not self.active:
            return
        now = self.clock()
        if self.retry_at is not None and now >= self.retry_at:
            self.retry_at = None
            self.sid += 1
            self._load(self.index)
        if self.adv_by is not None and now >= self.adv_by:
            # The next entry came after mpv had already reached the end and gone idle (a
            # track shorter than its next lookup): mpv will not move to it, so open it now.
            self.adv_by, self.next_item = None, None
            L.info("music: next entry came too late: open it")
            self._finish()
        if self.state == "playing" and now - self.last_report >= REPORT_EVERY_S:
            self._report(self.track, "playing", self.pos_s)
        if self.state == "paused" and self.paused_at is not None and \
                now - self.paused_at >= PAUSE_END_S:
            L.info("music paused for %d s: stop", PAUSE_END_S)
            self.stop()

    # ------------------------------------------------------------ internals
    def _next_index(self, manual=False):
        if not manual and self.sleep_end():
            return None                      # Sleep Timer: End of Track
        if self.live:                        # no next entry; Next wraps to the first station
            return (self.index + 1) % len(self.items) if manual and len(self.items) > 1 else None
        if self.repeat == "one" and not manual:
            return self.index
        if self.index + 1 < len(self.items):
            return self.index + 1
        if self.repeat == "all" and self.items:
            return 0
        return None

    def _info(self, t):
        if t.type == "station":
            from .model import PlayInfo
            return PlayInfo(url=t.extra["url"], headers={}, duration_ms=0)   # no Plex header
        return self.provider.track_info(t)

    def _maybe_refill(self):
        if not self.refill or self.refilling or len(self.items) - self.index - 1 > REFILL_AT:
            return
        # Bound to the play session, not to sid: a skip must not lose the refill.
        self.refilling, sess, fn = True, self.session_id, self.refill

        def done(tracks):
            if not self.active or self.session_id != sess:
                return
            self.refilling = False
            if tracks:
                self.add(tracks)
            else:
                self.refill = None               # nothing more: the queue ends

        def fail(e):
            if not self.active or self.session_id != sess:
                return
            L.info("music: station refill failed: %s", e)
            self.refilling, self.refill = False, None
        self.run(fn, lambda r: self.post(lambda: done(r)), lambda e: self.post(lambda: fail(e)))

    def _load(self, i):
        """Open items[i] now. The track info comes from a worker. The next entry follows from
        its own worker (_refresh_next), so its lookup never delays or fails this track."""
        self.index, self.state, self.pos_s = i, "loading", 0.0
        self.retry_at, self.adv_by, self.at_end = None, None, False
        self.dur_s = (self.items[i].duration_ms or 0) / 1000.0
        self._maybe_refill()
        self.paused_at, self.cache_wait = None, False
        sid = self.sid
        cur = self.items[i]
        self._changed()

        def done(pi):
            if sid != self.sid:
                return
            self.next_item = None
            if not self.audio.music_open(pi):
                self._fail_session()
                return
            if not self.dur_s and pi.duration_ms:
                self.dur_s = pi.duration_ms / 1000.0
            self.state = "playing"
            self.last_report = self.clock()
            self._report(cur, "playing", 0.0)
            self._refresh_next()
            self._changed()

        def fail(e):
            if sid != self.sid:
                return
            L.info("music: track info failed: %s", e)
            self._error()
        self.run(lambda: self._info(cur), lambda r: self.post(lambda: done(r)),
                 lambda e: self.post(lambda: fail(e)))

    def sleep_changed(self):
        """Sleep Timer End of Track turned on or off: mpv's next entry goes or comes back."""
        if self.active:
            self._refresh_next()

    def _want_next(self):
        ni = self._next_index()
        return self.items[ni] if ni is not None else None

    def _refresh_next(self):
        """Set mpv's next entry after a queue change. The entry follows the Item, not its
        index: remove, Play Next and Shuffle move items under a fixed index.
        Until the new entry is set, mpv has none, so the end of the track goes to _finish.
        A next track on another server gets no entry: _finish then opens it with its own
        headers. The cost: no gapless change between two servers."""
        t = self._want_next()
        if t is not None and t is self.next_item:
            return
        self.next_item = None
        self.audio.music_next(None)
        cur = self.track
        # Keep the same-server rule: mpv's playlist prefetch opens the next entry with the
        # CURRENT entry's per-file options, so a cross-server next entry would send this
        # server's token to the other server (checked on live mpv).
        if t is None or self.state == "loading" or cur is None or t.server_id != cur.server_id:
            return                           # (while a track loads, _load sets the entry)
        sid = self.sid

        def done(pi):
            if sid != self.sid or self.state == "loading" or self._want_next() is not t:
                return
            self.next_item = t
            self.audio.music_next(pi)
        self.run(lambda: self._info(t), lambda pi: self.post(lambda: done(pi)),
                 lambda e: L.info("music: next track info failed: %s", e))

    def _skip(self, i):
        """A skip: the old track is not complete (scrobble only at 90 %)."""
        old, pos = self.track, self.pos_s
        if self.live:
            self.retry_at, self.errors = None, 0
            if self.gone is not None and self.gone is old and len(self.items) > 1:
                to = self.items[i]
                self.items = [t for t in self.items if t is not old]
                self.order = [t for t in self.order if t is not old]
                self.gone, i = None, _find(self.items, to)
        self._report(old, "stopped", pos)
        dur_ms = (old.duration_ms or self.dur_s * 1000) if old is not None else 0
        if dur_ms and pos * 1000 >= SCROBBLE_AT * dur_ms:
            self._scrobble(old)
        self.sid += 1                       # drop the old session's pending results
        self._load(i)

    def _advance(self):
        """mpv went on to its next entry at the end of a track: a completion."""
        self.adv_by = None
        old = self.track
        if old is not None:
            self._report(old, "stopped", self.dur_s or self.pos_s)
            self._scrobble(old)
        nxt, self.next_item = self.next_item, None
        ni = self._next_index()
        if nxt is None or ni is None or self.items[ni] is not nxt:
            ni = _find(self.items, nxt)
        if ni is None:                       # mpv played an entry the queue no longer has
            self._skip_to_next()
            return
        self.audio.music_drop_first()
        self.index, self.pos_s, self.errors = ni, 0.0, 0
        self.dur_s = (self.items[ni].duration_ms or 0) / 1000.0
        self._maybe_refill()
        self.last_report = self.clock()
        self._report(self.items[ni], "playing", 0.0)
        self._refresh_next()
        self._changed()

    def _finish(self):
        """A track ended and mpv had no next entry: the end of the queue, or a queue change
        whose next entry was not set yet (then the next track opens now)."""
        t = self.track
        if t is not None:
            self._scrobble(t)
        if self._next_index() is None:
            self.stop()
            return
        self._report(t, "stopped", self.dur_s or self.pos_s)
        self._skip_to_next()

    def _skip_to_next(self):
        ni = self._next_index()
        if ni is None:
            self.stop()
            return
        self.sid += 1
        self._load(ni)

    def _drop(self):
        """Radio: the stream failed or ended. Open it again after 2, 5, then 10 s."""
        self.errors += 1
        if self.errors > len(RETRY_S):
            self.stop(report=False)
            self.toast("Can't play this station")
            return
        self.state, self.icy, self.cache_wait = "loading", "", False
        self.retry_at = self.clock() + RETRY_S[self.errors - 1]
        self._changed()

    def _error(self):
        if self.live:
            self._drop()
            return
        self.errors += 1
        if self.errors >= MAX_ERRORS:
            self._fail_session()
            return
        ni = self._next_index(manual=True)
        if ni is None:
            self.stop()
            return
        self.sid += 1
        self._load(ni)

    def _fail_session(self):
        self.stop(report=False)
        self.toast("Can't play this music")

    def _event(self, ev):
        """An AudioPlayer event while music owns the player."""
        name = ev.get("event")
        if name == "lost":
            if self.active:
                self._fail_session()
            return
        if self.live and self.retry_at is not None and name == "property-change" and \
                ev.get("name") == "time-pos" and ev.get("data") is not None:
            # A retry waits, but mpv plays (it went on to the next .pls mirror): cancel it.
            self.retry_at, self.state = None, "playing"
            self._changed()
        if not self.active or self.state == "loading":
            if name == "property-change" and ev.get("name") == "playlist-pos":
                pass
            elif name != "end-file":
                return
        if name == "property-change":
            p, d = ev.get("name"), ev.get("data")
            if p == "metadata/by-key/icy-title":
                if self.live and (d or "") != self.icy:
                    self.icy = d or ""
                    self._changed()
            elif p == "time-pos" and d is not None:
                old = int(self.pos_s)
                self.pos_s = float(d)
                if self.live and self.pos_s >= LIVE_OK_S:
                    self.errors = 0              # the stream plays: a later drop retries again
                if int(self.pos_s) != old:
                    self._changed(tick=True)
            elif p == "duration" and d:
                self.dur_s = float(d)
            elif p == "playlist-pos" and d == 1 and self.state in ("playing", "paused") \
                    and not self.live:               # a .pls mirror list is not our queue
                self._advance()
            elif p == "paused-for-cache" and d is not None:
                self.cache_wait = bool(d)
                self._changed()
        elif name == "end-file":
            reason = ev.get("reason")
            if self.live:
                if reason in ("eof", "error") and self.retry_at is None and \
                        self.state in ("loading", "playing"):
                    L.info("radio: the stream stopped (%s)", reason)
                    self._drop()
                return
            if reason == "error" and self.state in ("loading", "playing"):
                L.info("music: a track failed to play")
                self._error()
            elif reason == "eof" and self.state in ("playing", "paused"):
                if self.next_item is None:
                    self._finish()
                else:                        # gapless: playlist-pos 1 follows at once
                    self.adv_by = self.clock() + ADVANCE_WAIT_S

    def _scrobble(self, t):
        if self.live or getattr(self, "quiet", False):
            return                           # radio makes no reports
        prov, stamp = self.provider, self.stamp()
        # A scrobble that went through tells the app (Home goes stale); the
        # stamp lets the app drop a reply from an earlier profile or server.
        self.run(lambda: prov.scrobble(t), lambda r: self.post(lambda: self.on_played(t, stamp)),
                 lambda e: L.info("music scrobble failed: %s", e))

    def _report(self, t, state, pos_s, prov=None, sess=None, wait_s=0.0):
        if t is None or t.type == "station" or getattr(self, "quiet", False):
            return
        prov, sess = prov or self.provider, sess or self.session_id
        if state == "playing":
            self.last_report = self.clock()
        ms, dur = int(pos_s * 1000), t.duration_ms or int(self.dur_s * 1000)

        def send():
            return prov.timeline(t, state, ms, dur, sess)
        if wait_s:
            th = threading.Thread(target=self._safe, args=(send,), daemon=True)
            th.start()
            th.join(wait_s)
            return
        self.run(send, lambda r: None, lambda e: L.info("music report failed: %s", e))

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception as e:
            L.info("music report failed: %s", e)
