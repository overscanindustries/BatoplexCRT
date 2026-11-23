"""Plex Home avatars (the profile picker). plex.tv gives each Home user a `thumb` URL; it is
public, so it is fetched with no token and no client headers. plex.tv redirects it to an
image host, so this module has its own opener: it follows a redirect to another host, but
never to a weaker scheme (https stays https). The bytes stay on disk, so an offline launch
still shows the avatar. Only a small PNG or JPEG is kept and decoded (with no size cap, a tiny file
could ask for a huge decode). pygame work is on the UI thread only."""
import hashlib
import io
import os
import struct
import urllib.error
import urllib.parse
import urllib.request

import pygame

from . import config, log, plex

L = log.get("avatars")
MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 1024                         # plex.tv avatars are a few hundred px


class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        a = urllib.parse.urlsplit(req.full_url).scheme
        b = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl)).scheme
        if b not in ("http", "https") or (a == "https" and b != "https"):
            raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoDowngrade)


def image_size(data):
    """(w, h) from a PNG or JPEG header, or None for anything else (no GIF: its screen size
    need not be the frame size)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 <= len(data):
            if data[i] != 0xFF:
                return None
            m = data[i + 1]
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            n = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + n
    return None


def _check(data):
    size = image_size(data)
    if size is None:
        raise plex.PlexError("avatar: not a PNG or JPEG")
    if not (0 < size[0] <= MAX_SIDE and 0 < size[1] <= MAX_SIDE):
        raise plex.PlexError("avatar: %dx%d is too large" % size)


def _path(url):
    d = os.path.join(config.data_dir(), "cache", "avatars")
    return d, os.path.join(d, hashlib.sha1(url.encode()).hexdigest())


def fetch(url):
    """The image bytes of an avatar: the disk copy, else plex.tv. Runs on a worker."""
    d, path = _path(url)
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        pass
    req = urllib.request.Request(url, headers={"Accept": "image/*"})
    try:
        with _opener.open(req, timeout=8) as r:
            data = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise plex.PlexError("avatar: %s" % e)
    if len(data) > MAX_BYTES:
        raise plex.PlexError("avatar: too large")
    _check(data)                                     # no portal page or bomb is kept
    try:
        os.makedirs(d, exist_ok=True)
        config.atomic_write(path, data, fsync=False)     # a cache: fetched again if lost
    except OSError as e:
        L.info("avatar not kept on disk: %s", e)
    return data


def round_image(data, d):
    """A d x d round cut of the image bytes (centre square, scaled), or None."""
    try:
        _check(data)                                 # a disk copy too
        img = pygame.image.load(io.BytesIO(data))
        w, h = img.get_size()
        if not min(w, h) or max(w, h) > MAX_SIDE:
            return None
        return round_surface(img, d)
    except Exception as e:
        L.info("avatar unreadable: %s", e)
        return None


def round_surface(img, d):
    """A d x d round cut of a Surface (centre square, scaled). UI thread only (pygame)."""
    w, h = img.get_size()
    k = min(w, h)
    sq = pygame.Surface((k, k), 0, 32)              # smoothscale needs 24 or 32 bits
    sq.blit(img, (0, 0), ((w - k) // 2, (h - k) // 2, k, k))
    out = pygame.Surface((d, d), pygame.SRCALPHA)
    out.blit(pygame.transform.smoothscale(sq, (d, d)), (0, 0))
    mask = pygame.Surface((d, d), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255, 255), (d // 2, d // 2), d // 2)
    out.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return out
