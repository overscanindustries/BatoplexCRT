"""Menu sounds: the Hearth pack (move, select, back, error), only when the
theme has sounds and the profile turned "Menu Sounds" on (default Off).

The mixer opens on the first sound and closes (mixer.quit) before any mpv starts, so mpv never
competes for the audio device. No sound while a video session runs, while music is active or
while theme music plays. A mixer failure turns sounds off for the session (one log line).
"""
import os
import threading

from . import config, log

L = log.get("sounds")

EVENTS = ("move", "select", "back", "error")
VOLUME = 0.35
SOUND_DIR = os.path.join(config.APP_DIR, "sounds")

_lock = threading.Lock()       # mpv starts on worker threads; the UI thread plays
_open = False
_failed = False
_cache = {}
_channel = None


def wanted(settings, theme_sounds, busy):
    """True when a menu sound may play: the theme has a pack, the setting is on, and no video,
    music or theme music runs (busy)."""
    return bool(theme_sounds) and bool(settings["menu_sounds"]) and not busy


def play(pack, event):
    """Play one event of a pack; a new sound replaces the old one. Never raises."""
    global _open, _failed, _channel
    if event not in EVENTS:
        return False
    with _lock:
        if _failed:
            return False
        try:
            import pygame
            if not _open:
                pygame.mixer.init(48000, -16, 1, 512)
                pygame.mixer.set_num_channels(1)
                _open = True
                _cache.clear()
                _channel = pygame.mixer.Channel(0)
            snd = _cache.get((pack, event))
            if snd is None:
                snd = pygame.mixer.Sound(os.path.join(SOUND_DIR, pack, event + ".wav"))
                snd.set_volume(VOLUME)
                _cache[(pack, event)] = snd
            _channel.play(snd)
            return True
        except Exception as e:             # no audio device, a missing file: sounds off
            _failed = True
            L.info("menu sounds off for this session: %s", e)
            _close()
            return False


def _close():
    global _open, _channel
    if not _open:
        return
    _open, _channel = False, None
    _cache.clear()
    try:
        import pygame
        pygame.mixer.quit()
    except Exception as e:                 # not fatal: mpv starts anyway
        L.info("mixer close failed: %s", e)


def close():
    """Before any mpv start: give the audio device back."""
    with _lock:
        _close()


def is_open():
    return _open
