"""Pitch types defined as *data*.

To add a new pitch, add one entry to PITCH_LIBRARY. No new `if` statements are
needed in judge.py -- judge.py only reads the numbers from here.

What each field means:
    name            : display name
    pitch_class     : rough class: "fastball" / "breaking" / "offspeed"
                      (a batter sits on either a fastball or an off-speed pitch, so
                       everything that is not a fastball counts as offspeed -> guess_class_of)
    velocity        : approximate speed (mph). Faster = harder to square up
    movement        : approximate break (0 = straight, bigger = bends / drops more)
    whiff_rate      : base swing-and-miss rate when the batter swings
    groundball_rate : how likely a ball in play is a grounder (0-1)
    contact_quality : how likely a ball in play is hit hard (0-1, lower = more weak contact)
    platoon         : matchup effect. same = batter on the pitcher's side / opposite = other side.
                      More negative = more effective against that batter (harder to hit)

To swap in real MLB numbers later, overwrite this one file with Statcast's
per-pitch Whiff% / GB% / velocity.
"""

PITCH_LIBRARY = {
    "four_seam": {
        "name": "Four-seam",
        "pitch_class": "fastball",
        "velocity": 95, "movement": 2,
        "whiff_rate": 0.11, "groundball_rate": 0.36, "contact_quality": 0.58,
        "platoon": {"same": 0.00, "opposite": 0.02},
    },
    "two_seam": {
        "name": "Two-seam",
        "pitch_class": "fastball",
        "velocity": 93, "movement": 4,
        "whiff_rate": 0.08, "groundball_rate": 0.52, "contact_quality": 0.50,
        "platoon": {"same": -0.03, "opposite": 0.03},
    },
    "sinker": {
        "name": "Sinker",
        "pitch_class": "fastball",
        "velocity": 93, "movement": 5,
        "whiff_rate": 0.07, "groundball_rate": 0.58, "contact_quality": 0.47,
        "platoon": {"same": -0.04, "opposite": 0.03},
    },
    "cutter": {
        "name": "Cutter",
        "pitch_class": "fastball",
        "velocity": 90, "movement": 4,
        "whiff_rate": 0.13, "groundball_rate": 0.45, "contact_quality": 0.50,
        "platoon": {"same": -0.05, "opposite": 0.02},
    },
    "slider": {
        "name": "Slider",
        "pitch_class": "breaking",
        "velocity": 85, "movement": 6,
        "whiff_rate": 0.20, "groundball_rate": 0.47, "contact_quality": 0.44,
        "platoon": {"same": -0.07, "opposite": 0.05},
    },
    "sweeper": {
        "name": "Sweeper",
        "pitch_class": "breaking",
        "velocity": 82, "movement": 9,
        "whiff_rate": 0.22, "groundball_rate": 0.43, "contact_quality": 0.42,
        "platoon": {"same": -0.09, "opposite": 0.07},
    },
    "curveball": {
        "name": "Curveball",
        "pitch_class": "breaking",
        "velocity": 78, "movement": 8,
        "whiff_rate": 0.17, "groundball_rate": 0.45, "contact_quality": 0.43,
        "platoon": {"same": -0.04, "opposite": 0.04},
    },
    "changeup": {
        "name": "Changeup",
        "pitch_class": "offspeed",
        "velocity": 84, "movement": 6,
        "whiff_rate": 0.19, "groundball_rate": 0.50, "contact_quality": 0.44,
        "platoon": {"same": 0.05, "opposite": -0.08},
    },
    "splitter": {
        "name": "Splitter",
        "pitch_class": "offspeed",
        "velocity": 86, "movement": 7,
        "whiff_rate": 0.25, "groundball_rate": 0.55, "contact_quality": 0.40,
        "platoon": {"same": 0.02, "opposite": -0.05},
    },
}

# A pitcher's repertoire. Swap this to give each pitcher a different set of pitches.
#   e.g. {"name": "The Heater", "throws": "R", "repertoire": ["four_seam", "slider", "changeup"]}
DEFAULT_PITCHER = {
    "name": "Our pitcher",
    "throws": "R",
    "repertoire": ["four_seam", "sinker", "cutter", "slider", "curveball", "changeup"],
}


def all_pitch_keys():
    """Every pitch key in the library."""
    return list(PITCH_LIBRARY)


def get_pitch(key):
    """Return the pitch data (a dict)."""
    return PITCH_LIBRARY[key]


def pitch_name(key):
    """Return the display name."""
    return PITCH_LIBRARY[key]["name"]


def guess_class_of(key):
    """A batter sits on a fastball or an off-speed pitch. Anything not fastball is offspeed."""
    return "fastball" if PITCH_LIBRARY[key]["pitch_class"] == "fastball" else "offspeed"


def family_of(key):
    """Return the 3-way class "fastball" / "breaking" / "offspeed" as is (used for batter splits)."""
    return PITCH_LIBRARY[key]["pitch_class"]


def velocity_of(key):
    return PITCH_LIBRARY[key]["velocity"]


def options_for_keys(keys):
    """Turn a list of pitch keys into an ordered {key: display name} dict (for menus)."""
    return {key: PITCH_LIBRARY[key]["name"] for key in keys}


def repertoire_options(pitcher):
    """Return a pitcher's repertoire (dict or Pitcher object) as {key: display name}."""
    keys = pitcher["repertoire"] if isinstance(pitcher, dict) else pitcher.repertoire
    return options_for_keys(keys)


def platoon_adjust(key, pitcher_throws, batter_bats):
    """Matchup adjustment. Negative = harder for that batter to hit."""
    same = (pitcher_throws == batter_bats)
    return PITCH_LIBRARY[key]["platoon"]["same" if same else "opposite"]


# Alias kept for existing code and tests (key -> display name). Same content as PITCH_LIBRARY.
PITCH_TYPES = {key: value["name"] for key, value in PITCH_LIBRARY.items()}
