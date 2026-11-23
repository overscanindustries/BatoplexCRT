"""Plex loudness as the mpv fallback gain. Pure functions, no I/O.

mpv uses the per-file `replaygain-fallback` only for a file with no ReplayGain tags, so a tag
gain and a Plex gain never add. Album mode: albumGain first, then gain, then -18 - loudness.
The gain never passes what the peak permits; with no peak there is no boost.
"""
import math

PLEX_OFFSET_DB = 0.0            # added to a Plex gain/albumGain; box check 2 can change it
TARGET_LUFS = -18.0             # ReplayGain 2 level
MIN_DB, MAX_DB = -24.0, 12.0
FIELDS = ("gain", "albumGain", "peak", "albumPeak", "loudness")


def _num(v):
    """A finite float, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _floor2(g):
    """Round down to 0.01 dB, so a rounded gain never goes above its limit. The tiny epsilon
    keeps 1.01 as 1.01 (1.01 * 100 is 100.99999...)."""
    return math.floor(g * 100 + 1e-9) / 100


def limit_db(loud):
    """The highest gain the peak permits: -20 log10(peak). No valid peak: 0 dB (no boost).
    The album peak first (album mode); it is never less than the track peak."""
    loud = loud or {}
    for k in ("albumPeak", "peak"):
        p = _num(loud.get(k))
        if p is not None and p > 0:
            return -20.0 * math.log10(p)
    return 0.0


def gain_db(loud):
    """The fallback gain in dB for one track, or None (no data: no option)."""
    loud = loud or {}
    g = _num(loud.get("albumGain"))
    if g is None:
        g = _num(loud.get("gain"))
    if g is not None:
        g += PLEX_OFFSET_DB
    else:
        lu = _num(loud.get("loudness"))
        if lu is None:
            return None
        g = TARGET_LUFS - lu            # already at the target: no offset
    g = max(MIN_DB, min(MAX_DB, g))
    return _floor2(min(g, limit_db(loud)))      # the peak limit last


def gapless_gain(g, prev_limit):
    """mpv applies the gain at the audio output, so at a gapless change the new gain can touch
    the buffered tail of the previous track. Keep the next entry's gain also under the previous
    entry's peak limit, so that tail cannot clip. Inside an album both limits are the
    same album peak, so this changes nothing there."""
    if g is None:
        return None
    lim = 0.0 if prev_limit is None else prev_limit
    return g if g <= lim else _floor2(lim)
