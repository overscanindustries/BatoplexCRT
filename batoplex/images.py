"""Artwork loader. Worker threads fetch each image at its exact display size; the UI thread
decodes it, applies the gain, and keeps an in-memory LRU. Raw bytes go to a disk cache."""
import collections
import hashlib
import io
import os
import shutil
import threading
import time

import pygame

from . import config, log

L = log.get("images")

MB = 1024 * 1024
QUEUE_MAX = 64                 # requests kept; the oldest is dropped (asked again when seen)
DISK_MAX = 1024 * MB           # disk cache, pruned oldest first at start
DISK_MAX_TIGHT = 256 * MB      # when the file system has less than 4 GB free
DISK_FREE_LOW = 4096 * MB
PRUNE_DELAY_S = 60             # the disk prune starts this long after the cache


def mem_budget():
    """Bytes of decoded artwork kept: 32 MB on a box with less than 2 GB, else 96 MB."""
    return 32 * MB if config.low_ram() else 96 * MB


def surf_bytes(s):
    return s.get_width() * s.get_height() * s.get_bytesize()


def prune(cache_dir, limit=None):
    """Delete the oldest files (mtime) in the image cache until it is below the limit.
    limit None: DISK_MAX, or DISK_MAX_TIGHT when the disk is low. Worker thread only.
    Returns the bytes removed."""
    if limit is None:
        try:
            free = shutil.disk_usage(cache_dir).free
        except OSError:
            free = DISK_FREE_LOW
        limit = DISK_MAX_TIGHT if free < DISK_FREE_LOW else DISK_MAX
    files, total, now = [], 0, time.time()
    for root, _dirs, names in os.walk(cache_dir):
        for n in names:
            p = os.path.join(root, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if n.endswith(".tmp"):
                if now - st.st_mtime > 600:      # left by a crash mid-write
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                continue
            if n.endswith(".img"):
                files.append((st.st_mtime, st.st_size, p))
                total += st.st_size
    removed = 0
    if total > limit:
        files.sort()
        for _m, size, p in files:
            if total <= limit:
                break
            try:
                os.remove(p)
            except OSError:
                continue
            total -= size
            removed += size
        L.info("image cache pruned: %d MB removed, %d MB kept", removed // MB, total // MB)
    return removed


def apply_gain(surf, gain):
    if gain >= 0.999:
        return surf
    v = int(round(255 * gain))
    out = surf.copy()
    out.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
    return out


def fit(surf, w, h):
    """Resample to exactly w x h (cover crop, centred). smoothscale is an area filter when
    it reduces, so posters and backdrops are not aliased."""
    sw, sh = surf.get_size()
    if (sw, sh) == (w, h):
        return surf
    scale = max(w / sw, h / sh)
    tw, th = max(w, int(round(sw * scale))), max(h, int(round(sh * scale)))
    if surf.get_bitsize() < 24:
        tmp = pygame.Surface((sw, sh), 0, 24)
        tmp.blit(surf, (0, 0))
        surf = tmp
    s = pygame.transform.smoothscale(surf, (tw, th))
    return s.subsurface(((tw - w) // 2, (th - h) // 2, w, h)).copy()


class ImageCache:
    """Worker threads only fetch bytes (disk cache or network). pygame is not safe across
    threads: three workers that decoded and scaled at once corrupted memory on the box
    (segfaults in unrelated code). So every pygame call happens in pump(), on the UI thread,
    with a time budget for each frame."""

    def __init__(self, provider, cache_dir, workers=3, max_items=160, max_bytes=None,
                 fmt=None, prune_disk=True):
        self.provider = provider
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.mem = collections.OrderedDict()
        self.max = max_items             # a ceiling; the byte budget is the usual limit
        self.max_bytes = max_bytes if max_bytes is not None else mem_budget()
        self.mem_bytes = 0
        self.fmt = fmt                   # the canvas: decoded images take its pixel format
        self.pending = set()
        self.failed = {}             # key -> time of failure; retried after 60 s
        self.sizes = {}              # ref -> {(w, h)} asked for (forget() clears the disk)
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.q = collections.deque()   # newest request at the right: the focused item wins
        # (key, bytes or None for a local image, disk path, True when read from the disk)
        self.ready = collections.deque()
        self.done = False
        self.version = 0             # bumps when a new image lands (render can redraw)
        self.local = getattr(provider, "local_images", False)
        for _ in range(workers):
            threading.Thread(target=self._work, daemon=True).start()
        if prune_disk and not self.local:
            self._pruner = threading.Timer(PRUNE_DELAY_S, self._prune)   # not at launch
            self._pruner.daemon = True
            self._pruner.start()

    def _prune(self):
        try:
            prune(self.cache_dir)
        except Exception:
            L.exception("image cache prune failed")

    def close(self):
        if getattr(self, "_pruner", None) is not None:
            self._pruner.cancel()
        with self.cv:
            self.done = True
            self.cv.notify_all()

    def _request(self, k):
        """Under the lock. Newest first; past QUEUE_MAX the oldest request is dropped and
        leaves pending, so a later get() asks for it again."""
        self.q.append(k)
        if len(self.q) > QUEUE_MAX:
            self.pending.discard(self.q.popleft())
        self.cv.notify()

    def key(self, ref, w, h, gain):
        return (ref, w, h, round(gain, 2))

    def get(self, ref, w, h, gain=1.0, request=True):
        """Return a Surface or None (and queue it)."""
        if not ref:
            return None
        k = self.key(ref, w, h, gain)
        with self.lock:
            self.sizes.setdefault(ref, set()).add((w, h))
            s = self.mem.get(k)
            if s is not None:
                self.mem.move_to_end(k)
                return s
            if k in self.failed and time.time() - self.failed[k] < 60:
                return None
            if request and k not in self.pending:
                self.pending.add(k)
                if self.local:
                    self.ready.append((k, None, None, False))
                else:
                    self._request(k)
        return None

    def forget(self, ref):
        """Drop an image whose ref stays the same while its picture changed (a playlist
        composite after an edit): memory, failures and disk."""
        with self.lock:
            keys = [k for k in list(self.mem) + list(self.failed) if k[0] == ref]
            for k in keys:
                s = self.mem.pop(k, None)
                if s is not None:
                    self.mem_bytes -= surf_bytes(s)
                self.failed.pop(k, None)
            self.version += 1
            sizes = self.sizes.pop(ref, set()) | {(k[1], k[2]) for k in keys}
        for w, h in sizes:
            try:
                os.remove(self._disk(ref, w, h))
            except OSError:
                pass

    def prefetch(self, ref, w, h, gain=1.0):
        self.get(ref, w, h, gain, request=True)

    def _disk(self, ref, w, h):
        name = hashlib.sha1(("%s|%s|%dx%d" % (self.provider.name, ref, w, h)).encode()).hexdigest()
        return os.path.join(self.cache_dir, name[:2], name + ".img")

    def _work(self):
        """Fetch bytes only. No pygame here."""
        while True:
            with self.cv:
                while not self.q and not self.done:
                    self.cv.wait()
                if self.done:
                    return
                k = self.q.pop()
            ref, w, h, gain = k
            data, path, cached = None, None, False
            try:
                path = self._disk(ref, w, h)
                if os.path.exists(path):
                    try:
                        with open(path, "rb") as f:
                            data = f.read()
                    except FileNotFoundError:
                        data = b""               # the prune removed it meanwhile: a miss
                    if data:
                        cached = True
                        try:
                            os.utime(path)       # recently used: the prune keeps it
                        except OSError:
                            pass
                    else:
                        data = None              # an empty file: fetch it again
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
                if data is None:
                    data = self.provider.image(ref, w, h)
                    if data:
                        os.makedirs(os.path.dirname(path), exist_ok=True)
                        config.atomic_write(path, data, fsync=False)    # a cache
            except Exception as e:  # network
                L.info("image failed %s: %s", ref and str(ref)[:60], e)
            if data:
                self.ready.append((k, data, path, cached))
            else:
                self._land(k, None)

    def _land(self, k, surf, retry=False):
        """retry: a damaged disk file was deleted; the next get() fetches it again."""
        with self.lock:
            self.pending.discard(k)
            if surf is None:
                if not retry:
                    self.failed[k] = time.time()
            else:
                old = self.mem.pop(k, None)
                if old is not None:
                    self.mem_bytes -= surf_bytes(old)
                self.mem[k] = surf
                self.mem_bytes += surf_bytes(surf)
                self.version += 1
                while len(self.mem) > 1 and (len(self.mem) > self.max
                                             or self.mem_bytes > self.max_bytes):
                    _k, s = self.mem.popitem(last=False)
                    self.mem_bytes -= surf_bytes(s)

    def _convert(self, surf):
        """The canvas pixel format, so each blit is a plain copy. Same pixels: the canvas
        is where they go. An image with alpha keeps its alpha. A canvas below 24 bits:
        the image stays as decoded (a conversion would lower its quality)."""
        if self.fmt is None:
            return surf
        try:
            if self.fmt.get_bitsize() < 24:
                return surf
        except (AttributeError, pygame.error):
            return surf
        try:
            if surf.get_flags() & pygame.SRCALPHA:
                return surf.convert_alpha() if pygame.display.get_surface() else surf
            return surf.convert(self.fmt)
        except pygame.error:
            return surf

    def pump(self, budget_s=0.006):
        """UI thread: decode, fit and grade the fetched images, within the frame budget."""
        end = time.perf_counter() + budget_s
        while self.ready and time.perf_counter() < end:
            k, data, path, cached = self.ready.popleft()
            ref, w, h, gain = k
            surf = None
            try:
                if data is None:
                    surf = self.provider.image(ref, w, h)      # local drawn artwork
                else:
                    surf = pygame.image.load(io.BytesIO(data), "x.jpg")
                if surf is not None:
                    surf = self._convert(apply_gain(fit(surf, w, h), gain))
            except Exception as e:  # decode
                L.info("image failed %s: %s", ref and str(ref)[:60], e)
                surf = None
                if path:                     # a damaged file is not kept
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self._land(k, surf, retry=surf is None and cached)
