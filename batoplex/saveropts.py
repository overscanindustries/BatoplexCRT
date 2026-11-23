"""Screen saver types and their options. No pygame here: config.py reads it.

SAVERS: type -> (label, options). An option is (key, label, choices, default, when); choices are
(value, label); when is None or (other key, value or tuple of values) (the row shows only
then). Every type also
has During Music (MUSIC), except the types in NO_MUSIC_ROW. Only changed values are saved, in
settings["saver_opts"][type].
"""

ON_OFF = [(False, "Off"), (True, "On")]          # right turns on
SPEED = [("slow", "Slow"), ("normal", "Normal"), ("fast", "Fast")]
SIZE = [("small", "Small"), ("medium", "Medium"), ("large", "Large")]

# The visualizer when the engine runs, else the album art.
MUSIC = ("music", "During Music", [("vis", "Visualizer"), ("album", "Album Art"),
                                   ("keep", "Keep Running")], "vis", None)

SAVERS = {
    "bounce": ("Bounce", [
        ("art", "Art", [("posters", "Posters"), ("dvd", "DVD Video Logo")], "posters", None),
        ("logo", "Logo", [("classic", "Classic"), ("mark", "Official"), ("oval", "Oval")],
         "classic", ("art", "dvd")),
        ("count", "Count", [(n, str(n)) for n in range(1, 6)], 1, None),
        ("colors", "Color Change at Walls", ON_OFF, True, ("art", "dvd")),
        ("speed", "Speed", SPEED, "normal", None),
        ("size", "Size", SIZE, "medium", None),
    ]),
    "art": ("Entertainment", []),
    "rain": ("Digital Rain", [
        ("size", "Glyph Size", SIZE, "medium", None),
        ("color", "Color", [("green", "Green"), ("amber", "Amber"), ("white", "White"),
                            ("accent", "Theme Accent")], "green", None),
        ("speed", "Speed", SPEED, "normal", None),
        ("density", "Density", [("low", "Low"), ("normal", "Normal"), ("high", "High")],
         "normal", None),
        ("glyphs", "Glyph Set", [("kana", "Katakana"), ("ascii", "ASCII"), ("binary", "Binary")],
         "kana", None),
    ]),
    "polygons": ("Polygons", []),
    "stars": ("Starfield", []),
    "pong": ("Pong", []),
    "fire": ("Fireplace", []),
    "tunnel": ("Tunnel", []),
    "aquarium": ("Aquarium", [
        ("tank", "Tank", [("mixed", "Mixed"), ("reef", "Reef"), ("fresh", "Freshwater")],
         "mixed", None),
        ("fish", "Fish", [("few", "Few"), ("normal", "Normal"), ("many", "Many")], "normal", None),
        ("frogs", "Frogs", [(0, "None"), (1, "1"), (2, "2"), (3, "3")], 2,
         ("tank", ("mixed", "fresh"))),
        ("turtles", "Turtles", ON_OFF, True, None),
        ("crabs", "Crabs & Snails", ON_OFF, True, None),
        ("activity", "Activity", [("calm", "Calm"), ("normal", "Normal"), ("lively", "Lively")],
         "normal", None),
        ("feed", "Feeding Time", [("off", "Off"), ("rare", "Rarely"), ("normal", "Normal"),
                                  ("often", "Often")], "normal", None),
        ("big", "Sharks & Rays", ON_OFF, True, ("tank", ("mixed", "reef"))),
        ("bursts", "Bubble Bursts", ON_OFF, True, None),
        ("moon", "Moonlight", [("off", "Off"), ("20", "Every 20 Minutes"), ("always", "Always")],
         "20", None),
        ("pan", "Pan Speed", SPEED, "normal", None),
    ]),
    "flying": ("Flying Objects", [
        ("objects", "Objects", [("toasters", "Toasters"), ("tvs", "CRT TVs"), ("mixed", "Mixed")],
         "toasters", None),
    ]),
    "city": ("Night City", []),
    "maze": ("3D Maze", []),
    "shuffle": ("Shuffle", [
        ("every", "Change Every", [(5, "5 Minutes"), (10, "10 Minutes"), (30, "30 Minutes")],
         10, None),
    ]),
    "weather": ("Weather", [
        ("place", "Location", [("auto", "Automatic"), ("custom", "ZIP or City")], "auto", None),
        ("units", "Units", [("auto", "Automatic"), ("f", "°F"), ("c", "°C")], "auto", None),
    ]),
}
NO_MUSIC_ROW = ("weather", "shuffle")   # Weather: its own playlist; Shuffle: each saver's own
# The types drawn by a module in batoplex/savers/ (class Saver). Bounce, Entertainment and
# Digital Rain have their own code in saver.py.
MODULES = {"polygons": "polygons", "stars": "stars", "pong": "pong", "fire": "fire",
           "tunnel": "tunnel", "weather": "weather", "aquarium": "aquarium", "flying": "flying",
           "city": "city", "maze": "maze"}


def label(kind):
    return SAVERS[kind][0] if kind in SAVERS else kind


def options(kind):
    """All options of a type, During Music last."""
    opts = list(SAVERS[kind][1]) if kind in SAVERS else []
    if kind not in NO_MUSIC_ROW:
        opts.append(MUSIC)
    return opts


def option(kind, key):
    for o in options(kind):
        if o[0] == key:
            return o
    raise KeyError((kind, key))


def get(settings, kind, key):
    """The value of an option: the saved one, else the default. A saved value that is no
    longer a choice (a hand edit, an older build) gives the default."""
    o = option(kind, key)
    saved = ((settings["saver_opts"] or {}).get(kind) or {})
    v = saved.get(key, o[3])
    return v if any(v == c[0] and type(v) is type(c[0]) for c in o[2]) else o[3]   # 1.0, 0


def put(settings, kind, key, value):
    """Set an option. Only values that differ from the default are kept."""
    o = option(kind, key)
    opts = dict(settings["saver_opts"] or {})
    mine = dict(opts.get(kind) or {})
    if value == o[3]:
        mine.pop(key, None)
    else:
        mine[key] = value
    if mine:
        opts[kind] = mine
    else:
        opts.pop(kind, None)
    settings["saver_opts"] = opts


def visible(settings, kind):
    """The options that show for the current values (a `when` hides a row)."""
    return [o for o in options(kind) if shows(settings, kind, o)]


def shows(settings, kind, o):
    if o[4] is None:
        return True
    v, want = get(settings, kind, o[4][0]), o[4][1]
    return v in want if isinstance(want, tuple) else v == want


def all_of(settings, kind):
    """{key: value} for every option of the type, and its extra values."""
    d = {o[0]: get(settings, kind, o[0]) for o in options(kind)}
    for k in EXTRA.get(kind, ()):
        d[k] = extra(settings, kind, k)
    return d


# Values that are not pick lists (the Weather place and playlist): kept beside the options.
EXTRA = {"weather": ("where", "playlist"), "shuffle": ("set",),
         "aquarium": ("sp_reef", "sp_fresh", "sp_mixed")}
# Shuffle's default set: every type but Entertainment (it needs pinned art) and Weather
# (its network and music).
SHUFFLE_OUT = ("shuffle", "art", "weather")


def shuffle_set(settings):
    """The marked types of Shuffle (the default set when none is saved; never empty)."""
    s = [k for k in (extra(settings, "shuffle", "set") or []) if k in SAVERS and k != "shuffle"]
    return s or [k for k in SAVERS if k not in SHUFFLE_OUT]


def extra(settings, kind, key):
    return ((settings["saver_opts"] or {}).get(kind) or {}).get(key)


def put_extra(settings, kind, key, value):
    opts = dict(settings["saver_opts"] or {})
    mine = dict(opts.get(kind) or {})
    if value is None:
        mine.pop(key, None)
    else:
        mine[key] = value
    if mine:
        opts[kind] = mine
    else:
        opts.pop(kind, None)
    settings["saver_opts"] = opts


# Aquarium species: (key, label, tank). Each tank keeps its own list (sp_<tank>); none saved:
# the default list. Mixed lists all of them; its default is the Mixed tank as it was.
AQ_SPECIES = [
    ("clown", "Clownfish", "reef"), ("yellowtang", "Yellow Tang", "reef"),
    ("idol", "Moorish Idol", "reef"), ("lion", "Lionfish", "reef"),
    ("parrot", "Parrotfish", "reef"), ("puffer", "Pufferfish", "reef"),
    ("butterfly", "Butterflyfish", "reef"), ("mandarin", "Mandarinfish", "reef"),
    ("seahorse", "Seahorse", "reef"), ("jelly", "Jellyfish", "reef"),
    ("goldfish", "Goldfish", "fresh"), ("angel", "Angelfish", "fresh"),
    ("discus", "Discus", "fresh"), ("betta", "Betta", "fresh"),
    ("tetra", "Neon Tetra", "fresh"), ("guppy", "Guppy", "fresh"),
    ("pleco", "Pleco", "fresh"), ("cory", "Cory Catfish", "fresh"),
]
AQ_MIXED = ("clown", "yellowtang", "idol", "lion", "puffer", "seahorse", "jelly", "goldfish",
            "angel", "discus", "betta", "tetra", "guppy", "cory")


def aq_choices(tank):
    """The species the list of a tank shows, in order."""
    return [k for k, _l, t in AQ_SPECIES if tank == "mixed" or t == tank]


def aq_default(tank):
    return list(AQ_MIXED) if tank == "mixed" else aq_choices(tank)


def aq_species(settings, tank):
    """(the species on for a tank, True when it is the default list). Never empty."""
    return aq_from(tank, extra(settings, "aquarium", "sp_" + tank))


def aq_from(tank, saved):
    """aq_species() from the saved list (None: the default)."""
    ok = aq_choices(tank)
    s = [k for k in ok if k in saved] if isinstance(saved, list) else []
    return (s, False) if s else (aq_default(tank), True)


def put_aq_species(settings, tank, keys):
    """Keep the list; the default list is not saved."""
    keys = [k for k in aq_choices(tank) if k in keys]
    same = sorted(keys) == sorted(aq_default(tank))
    put_extra(settings, "aquarium", "sp_" + tank, None if same or not keys else keys)


# The help line of each Screen Saver page row, by "<type>.<key>", then "<key>"
# (an option row's key without its "opt:" prefix). 52 characters at most.
HELP = {
    "min": "How long the screen sits idle before it starts",
    "type": "Pick what the screen saver shows",
    "preview": "See it full screen now. Press any button to stop.",
    "aquarium.preview": "See it now. Left and Right show the rare moments.",
    "music": "What shows when it starts while music plays",
    "set": "Choose the screen savers Shuffle uses",
    "species": "Choose the fish in this tank",
    "where": "Type a ZIP code or a city name",
    "playlist": "A music playlist to play with the weather",
    "speed": "How fast it moves",
    "bounce.art": "Bounce your posters or a DVD Video logo",
    "bounce.logo": "The style of the DVD Video logo",
    "bounce.count": "How many bounce at one time",
    "bounce.colors": "Change the logo color each time it hits a wall",
    "bounce.size": "How big the posters or logos are",
    "rain.size": "How big the falling characters are",
    "rain.color": "The color of the characters",
    "rain.density": "How many columns fall at one time",
    "rain.glyphs": "Which characters fall",
    "aquarium.tank": "A reef, a freshwater tank, or both mixed",
    "aquarium.fish": "How many fish swim in the tank",
    "aquarium.frogs": "How many frogs live in the tank",
    "aquarium.turtles": "Show turtles in the tank",
    "aquarium.crabs": "Show crabs and snails on the sand",
    "aquarium.activity": "How busy the fish are",
    "aquarium.feed": "How often food drops into the tank",
    "aquarium.big": "Let a shark or a ray swim past at times",
    "aquarium.bursts": "Let bursts of bubbles rise at times",
    "aquarium.moon": "Dim the tank to moonlight at times, or always",
    "aquarium.pan": "How fast the view moves along the tank",
    "flying.objects": "Which objects fly across the screen",
    "shuffle.every": "How long each screen saver shows",
    "weather.place": "Find your location, or set a ZIP or city",
    "weather.units": "Show temperatures in °F or °C",
}


def help_for(kind, key):
    """The help line of a Screen Saver page row ("opt:" stripped), or ""."""
    if key.startswith("opt:"):
        key = key[4:]
    return HELP.get("%s.%s" % (kind, key)) or HELP.get(key, "")
