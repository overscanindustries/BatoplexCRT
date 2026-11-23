"""Shared data model for providers (Plex; Jellyfin later)."""
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class Item:
    id: str
    type: str                     # movie | show | season | episode | library | collection
                                  # | artist | album | track (music)
                                  # | playlist | person | clip
    title: str
    year: Optional[int] = None
    duration_ms: int = 0
    view_offset_ms: int = 0
    watched: bool = False
    content_rating: str = ""
    summary: str = ""
    poster: Optional[str] = None  # provider image refs
    art: Optional[str] = None
    still: Optional[str] = None
    show_title: str = ""
    show_id: Optional[str] = None
    season_id: Optional[str] = None
    season_index: Optional[int] = None
    index: Optional[int] = None   # episode number or season number
    child_count: int = 0          # seasons of a show
    leaf_count: int = 0
    viewed_leaf_count: int = 0
    cast: List[str] = field(default_factory=list)
    media_note: str = ""
    section_id: Optional[int] = None
    server_id: Optional[str] = None   # Plex machineIdentifier; with section_id it keys pins
    theme: Optional[str] = None       # theme music ref (show, or the episode's show)
    artist: str = ""                  # music: the artist title (album, track)
    album: str = ""                   # music: the album title (track)
    artist_id: Optional[str] = None
    album_id: Optional[str] = None
    disc: Optional[int] = None        # music: the disc number of a track
    ratings: list = field(default_factory=list)    # plex.Rating badges
    extra: dict = field(default_factory=dict)

    @property
    def progress(self):
        if self.duration_ms and self.view_offset_ms and not self.watched:
            return max(0.0, min(1.0, self.view_offset_ms / self.duration_ms))
        return 0.0

    @property
    def hero_title(self):
        return self.show_title if self.type == "episode" and self.show_title else self.title


@dataclass
class Row:
    key: str
    title: str
    items: List[Item]
    total: int = 0
    loader: Optional[Callable[[int, int], List[Item]]] = None  # (start, size) -> items
    focus: int = 0
    loading: bool = False

    def __post_init__(self):
        self.total = max(self.total, len(self.items))


@dataclass
class Track:
    """One audio or subtitle stream of a media part."""
    id: int                       # Plex stream id (write-back, menu identity)
    kind: str                     # "audio" | "sub"
    index: Optional[int] = None   # source stream index (ffprobe), embedded only
    key: Optional[str] = None     # external subtitle path on the server
    language: str = ""
    language_code: str = ""       # ISO 639-2, e.g. "eng"
    codec: str = ""
    title: str = ""
    forced: bool = False
    sdh: bool = False
    channels: int = 0
    selected: bool = False
    display_title: str = ""

    @property
    def external(self):
        return bool(self.key)


@dataclass
class Marker:
    type: str                     # "intro" | "credits"
    start_ms: int
    end_ms: int
    final: bool = False           # credits that run to the end of the file


@dataclass
class Queue:
    """What plays next in one playback session: season order, or a fixed list (a playlist or
    the local video queue) that is never built again and ignores Auto Play."""
    items: List[Item]
    index: int = 0
    fixed: bool = False

    @property
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    @property
    def next(self):
        return self.items[self.index + 1] if self.index + 1 < len(self.items) else None

    @property
    def prev(self):
        return self.items[self.index - 1] if self.index > 0 else None


@dataclass
class PlayInfo:
    url: str
    headers: dict
    start_s: float = 0.0
    duration_ms: int = 0
    audio_index: Optional[int] = None      # source stream index (ffprobe index)
    audio_pos: Optional[int] = None        # 1-based position among audio tracks
    sub_index: Optional[int] = None
    sub_pos: Optional[int] = None
    sub_url: Optional[str] = None          # external subtitle
    title: str = ""
    part_id: Optional[int] = None
    tracks: List[Track] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    mode: str = "direct"                   # "direct" | "transcode"
    media_index: int = 0
    base_url: str = ""                     # server base, for external subtitle URLs
    # Transcode. The stream starts at offset_s; mpv time 0 is offset_s.
    offset_s: float = 0.0
    quality: str = ""                      # "" (original) | a target label: "8 Mbps 480p", ...
    conn: str = ""                         # "local" | "remote" | "relay"
    url_at: Optional[Callable[[float], str]] = None   # seconds -> a new start URL
    session: str = ""                      # the transcode session (= the timeline session)
    reason: str = ""                       # why it transcodes: menu, retry, setting, bandwidth
    notice: str = ""                       # a toast for the start (conversion refused)
    # Music: the Plex loudness as mpv's fallback gain, and the peak limit.
    gain_db: Optional[float] = None        # None: no replaygain-fallback option
    gain_max_db: Optional[float] = None    # the highest gain this file's peak permits


class NoProvider:
    """The provider before sign-in and after sign-out: no server, no items, no network."""
    name = "none"
    server_id = "none"
    local_images = True       # nothing to fetch: the image cache uses no network

    def __init__(self, settings=None):
        self.settings = settings

    def home_rows(self):
        return []

    def libraries(self, cached=False):
        return []

    def library_rows(self, lib):
        return []

    def search(self, query, limit=12):
        return []

    def section_type(self, lib):
        return "movie"

    def unplayed_filter(self, lib):
        return {"unwatched": 1}

    def recommended(self, lib):
        return []

    def library_meta(self, lib):
        return {"sorts": [], "filters": []}

    def library_page(self, lib, sort, filters, start, size=48):
        return [], 0

    def first_characters(self, lib, filters=None):
        return []

    def collections(self, lib, start, size=48):
        return [], 0

    def collection_items(self, coll, start, size=48):
        return [], 0

    def filter_values(self, key):
        return []

    def details(self, item):
        return item

    def seasons(self, show_id):
        return []

    def episodes(self, season_id):
        return []

    def play_info(self, item, resume=True, opts=None):
        raise RuntimeError("not signed in")

    def timeline(self, item, state, time_ms, duration_ms, session_id):
        pass

    def scrobble(self, item):
        pass

    def unscrobble(self, item):
        pass

    def image(self, ref, w, h):
        return None
