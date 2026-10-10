"""Shared constants that never change during a game.

Pitch locations and the adjustment values used by the judge live here.
If you want to rebalance the numbers, look at this file first.

Larger data sets live in their own files:
  - Pitch data               : pitch_data.py
  - Fielder position fit     : fielders.py
  - Defensive alignments     : defense.py
  - Base out probabilities   : fielding.py
"""

# The 3x3 strike zone (9 cells).
#   Keys are "<horizontal>_<vertical>".  horizontal: in/mid/out   vertical: hi/mid/lo
COURSES = {
    "in_hi": "Up & In", "mid_hi": "Up", "out_hi": "Up & Away",
    "in_mid": "Inside", "mid_mid": "Middle", "out_mid": "Away",
    "in_lo": "Down & In", "mid_lo": "Down", "out_lo": "Down & Away",
}
CENTER_COURSE = "mid_mid"

# Short labels used in tables and the strike-zone grid.
COURSE_SHORT = {
    "in_hi": "Hi-In", "mid_hi": "Hi", "out_hi": "Hi-Out",
    "in_mid": "In", "mid_mid": "Mid", "out_mid": "Out",
    "in_lo": "Lo-In", "mid_lo": "Lo", "out_lo": "Lo-Out",
}


def zone_x(course):
    """Horizontal position: "in" / "mid" / "out"."""
    return course.split("_")[0]


def zone_y(course):
    """Vertical position: "hi" / "mid" / "lo"."""
    return course.split("_")[1]


def is_corner(course):
    """True for a corner cell (neither horizontally nor vertically centered)."""
    return zone_x(course) != "mid" and zone_y(course) != "mid"


def zone_overlap(a, b):
    """How close two locations are. Same = 1.0 / same row or column = 0.5 / else 0.0.

    A batter who is weak "down and away" can also be jammed a little with a pitch
    "away" or "down" -- this number expresses that.
    """
    if a == b:
        return 1.0
    if zone_x(a) == zone_x(b) or zone_y(a) == zone_y(b):
        return 0.5
    return 0.0


# Neighbors of each cell: where a pitch drifts when the pitcher misses his spot
# (see pitcher.py). Missing a corner usually means the ball "catches the middle".
COURSE_NEIGHBORS = {
    "in_hi": ["mid_hi", "in_mid", "mid_mid"],
    "mid_hi": ["mid_mid", "in_hi", "out_hi"],
    "out_hi": ["mid_hi", "out_mid", "mid_mid"],
    "in_mid": ["mid_mid", "in_hi", "in_lo"],
    "mid_mid": ["in_mid", "out_mid", "mid_hi", "mid_lo"],
    "out_mid": ["mid_mid", "out_hi", "out_lo"],
    "in_lo": ["mid_lo", "in_mid", "mid_mid"],
    "mid_lo": ["mid_mid", "in_lo", "out_lo"],
    "out_lo": ["mid_lo", "out_mid", "mid_mid"],
}

# Per-cell adjustments
#   strike_rate  : chance the pitch ends up in the zone when aimed at this cell (lower at corners)
#   contact_bonus: how easy the pitch is to hit (the middle is the juiciest, corners are negative)
COURSE_PROFILE = {
    "in_hi": {"strike_rate": 0.42, "contact_bonus": -0.02},
    "mid_hi": {"strike_rate": 0.60, "contact_bonus": 0.05},
    "out_hi": {"strike_rate": 0.40, "contact_bonus": -0.08},
    "in_mid": {"strike_rate": 0.60, "contact_bonus": 0.02},
    "mid_mid": {"strike_rate": 0.82, "contact_bonus": 0.22},
    "out_mid": {"strike_rate": 0.55, "contact_bonus": -0.08},
    "in_lo": {"strike_rate": 0.40, "contact_bonus": -0.06},
    "mid_lo": {"strike_rate": 0.56, "contact_bonus": -0.05},
    "out_lo": {"strike_rate": 0.38, "contact_bonus": -0.12},
}

# --- Constants for the batter-reaction feature (reaction.py) ---

# How often each kind of reaction is shown (see reaction.py)
REACTION_MISLEAD_RATE = 0.15   # chance the face gives the opposite impression of the truth
REACTION_REVEAL_RATE = 0.60    # chance (when not misleading) the face shows the honest reaction
