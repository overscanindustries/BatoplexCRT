"""Audio and subtitle streams: parse, choose, label, remember.

Pure functions, no pygame and no network, so they are tested without a server.
Choice order for subtitles: the per-show memory on this box, then the stream Plex marks
selected, then a forced stream in the audio language, then off. Plex data cannot tell
"the user chose Off" from "nothing chosen", so only the local memory can store Off.
"""
import json
import os

from . import config, log
from .model import Marker, Track

L = log.get("tracks")

AUDIO, SUB = "audio", "sub"

_LANG = {
    "eng": "English", "jpn": "Japanese", "spa": "Spanish", "fre": "French", "fra": "French",
    "ger": "German", "deu": "German", "ita": "Italian", "por": "Portuguese", "rus": "Russian",
    "chi": "Chinese", "zho": "Chinese", "kor": "Korean", "dut": "Dutch", "nld": "Dutch",
    "swe": "Swedish", "nor": "Norwegian", "dan": "Danish", "fin": "Finnish", "pol": "Polish",
    "hin": "Hindi", "ara": "Arabic", "tur": "Turkish", "gre": "Greek", "ell": "Greek",
    "heb": "Hebrew", "tha": "Thai", "vie": "Vietnamese", "ind": "Indonesian", "cze": "Czech",
    "ces": "Czech", "hun": "Hungarian", "ukr": "Ukrainian", "rum": "Romanian", "ron": "Romanian",
}
_CH = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


# ---------------------------------------------------------------- parse

def parse(part):
    """Plex Part dict -> list of Track (audio and subtitles, in Plex order)."""
    out = []
    for s in (part or {}).get("Stream") or []:
        st = s.get("streamType")
        if st not in (2, 3):
            continue
        out.append(Track(
            id=int(s.get("id") or 0), kind=AUDIO if st == 2 else SUB,
            index=s.get("index") if not s.get("key") else None, key=s.get("key"),
            language=s.get("language") or "", language_code=s.get("languageCode") or "",
            codec=(s.get("codec") or "").lower(), title=s.get("title") or "",
            forced=bool(s.get("forced")), sdh=bool(s.get("hearingImpaired")),
            channels=int(s.get("channels") or 0), selected=bool(s.get("selected")),
            display_title=s.get("displayTitle") or s.get("extendedDisplayTitle") or ""))
    return out


def parse_markers(md):
    out = []
    for m in (md or {}).get("Marker") or []:
        t = m.get("type")
        if t not in ("intro", "credits"):
            continue
        out.append(Marker(t, int(m.get("startTimeOffset") or 0), int(m.get("endTimeOffset") or 0),
                          bool(m.get("final"))))
    return sorted(out, key=lambda m: m.start_ms)


def audio(tracks):
    return [t for t in tracks if t.kind == AUDIO]


def subs(tracks):
    return [t for t in tracks if t.kind == SUB]


# ---------------------------------------------------------------- labels

def lang_name(t):
    if t.language:
        return t.language
    return _LANG.get(t.language_code, t.language_code.upper() if t.language_code else "")


def _codec(t):
    c = t.codec
    return {"subrip": "SRT", "srt": "SRT", "ass": "ASS", "ssa": "SSA", "pgs": "PGS",
            "hdmv_pgs_subtitle": "PGS", "dvd_subtitle": "VOBSUB", "vobsub": "VOBSUB",
            "mov_text": "TX3G", "eac3": "EAC3", "ac3": "AC3", "dca": "DTS", "dts": "DTS",
            "truehd": "TrueHD", "aac": "AAC", "mp3": "MP3", "flac": "FLAC", "opus": "Opus",
            "vorbis": "Vorbis", "pcm": "PCM", "alac": "ALAC"}.get(c, c.upper())


def _extra_title(t, name):
    """The stream title only when it adds something ("Commentary"), not "English" again."""
    ti = t.title.strip()
    if not ti or ti.lower() in (name.lower(), "forced", "sdh", "full", "default"):
        return ""
    return ti


def label(t):
    """Menu and toast text for a track. None means subtitles off."""
    if t is None:
        return "None"
    name = lang_name(t)
    if not name:
        return t.display_title or "Track %d" % ((t.index or 0) + 1)
    bits = [name]
    if t.kind == SUB:
        if t.forced:
            bits.append("Forced")
        if t.sdh:
            bits.append("SDH")
        if t.external:
            bits.append("External")
        extra = _extra_title(t, name)
        if extra:
            bits.append(extra)
        s = " • ".join(bits)
        c = _codec(t)
        return "%s (%s)" % (s, c) if c else s
    ch = _CH.get(t.channels, "%d ch" % t.channels if t.channels else "")
    if ch:
        bits.append(ch)
    if t.codec:
        bits.append(_codec(t))
    extra = _extra_title(t, name)
    if extra:
        bits.append(extra)
    return " • ".join(bits)


# ---------------------------------------------------------------- choice

def _match(cands, pref):
    """Best track for a remembered preference: language, then forced, then SDH."""
    lang = pref.get("lang")
    same = [t for t in cands if lang and t.language_code == lang]
    if not same:
        return None
    for want in ((pref.get("forced", False), pref.get("sdh", False)),
                 (pref.get("forced", False), None)):
        hit = [t for t in same if t.forced == want[0] and (want[1] is None or t.sdh == want[1])]
        if hit:
            return hit[0]
    return same[0]


def choose_audio(tracks, memory=None):
    """Audio track to play, or None for the mpv default.
    Order: the Plex selection, then this box's memory for the show."""
    au = audio(tracks)
    sel = next((t for t in au if t.selected), None)
    if sel:
        return sel
    if memory and memory.get("audio"):
        hit = _match(au, memory["audio"])
        if hit:
            return hit
    return None


def choose_sub(tracks, audio_track=None, memory=None):
    """Returns (track or None, reason). Reason: "plex", "memory", "forced", "off".
    Order (like stock Plex): the Plex selection, then this box's memory for the
    show, then a forced track in the audio language, then off. Memory "off" stops the
    forced step only."""
    sb = subs(tracks)
    sel = next((t for t in sb if t.selected), None)
    if sel:
        return sel, "plex"
    m = (memory or {}).get("sub")
    if m:
        if m.get("off"):
            return None, "memory"
        hit = _match(sb, m)
        if hit:
            return hit, "memory"
    lang = audio_track.language_code if audio_track else ""
    if not lang:
        au = audio(tracks)
        lang = au[0].language_code if au else ""
    forced = [t for t in sb if t.forced and (not lang or t.language_code == lang)]
    forced.sort(key=lambda t: t.sdh)       # prefer not SDH
    if forced:
        return forced[0], "forced"
    return None, "off"


def pref_of(t, kind):
    """Memory record for a user choice (None = subtitles off)."""
    if kind == SUB:
        if t is None:
            return {"off": True}
        return {"off": False, "lang": t.language_code, "forced": t.forced, "sdh": t.sdh}
    return {"lang": t.language_code, "title": t.title}


# ---------------------------------------------------------------- mpv mapping

def mpv_id(t, tracks):
    """mpv track id for an embedded track: its position among tracks of its kind,
    in source order (mpv numbers each kind from 1 in file order)."""
    same = sorted([x for x in tracks if x.kind == t.kind and not x.external and x.index is not None],
                  key=lambda x: x.index)
    for n, x in enumerate(same, 1):
        if x.id == t.id:
            return n
    return None


# ---------------------------------------------------------------- memory

class Memory:
    """Per-show track choices on this box: data/track_prefs.json.
    Key: profile/server/show. Movies are not stored (Plex keeps the part choice)."""

    def __init__(self, path):
        self.path = path
        self.data = config.load_dict(path, "track_prefs.json") or {}

    @staticmethod
    def key(profile, server_id, show_id):
        return "%s/%s/%s" % (profile or "default", server_id or "-", show_id)

    def get(self, key):
        return self.data.get(key) or {}

    def set(self, key, kind, pref):
        rec = dict(self.data.get(key) or {})
        rec[kind] = pref
        self.data[key] = rec
        self._save()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            config.atomic_write(self.path, json.dumps(self.data, indent=1, sort_keys=True))
        except Exception:
            L.exception("track memory not written")      # not fatal: the choice still plays
