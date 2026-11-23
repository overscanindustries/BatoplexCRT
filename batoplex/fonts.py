"""Font cache. Barlow / Barlow Condensed (SIL OFL 1.1), greyscale anti-aliasing, no hinting."""
import os
import threading

import pygame

from . import config, tokens

_cache = {}
_lock = threading.Lock()


def get(key, size):
    k = (key, size)
    with _lock:
        f = _cache.get(k)
        if f is None:
            path = os.path.join(config.FONT_DIR, tokens.FONT_FILES[key])
            f = pygame.font.Font(path, size)
            _cache[k] = f
        return f


def style(name):
    key, size, line, upper = tokens.TYPE[name]
    return get(key, size), line, upper
