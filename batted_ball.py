"""Builds the "contents" of a ball that is hit fair.

A batted ball is described by four things:
    direction : "pull" / "center" / "oppo"  as seen from the batter
                (the actual left/right spot is decided in fielding.py using batter handedness)
    ball_type : "ground" / "line" / "fly"
    hardness  : "soft" / "medium" / "hard"
    distance  : "infield" / "shallow" / "deep"

This is not a physics simulation but a probability model you can read and follow.
It just nudges each probability by "batter tendencies x pitch x location x timing".
"""

import random
from dataclasses import dataclass

from constants import zone_x, zone_y

DIRECTIONS = ["pull", "center", "oppo"]
BALL_TYPES = ["ground", "line", "fly"]
HARDNESS_LEVELS = ["soft", "medium", "hard"]

_DIRECTION_TEXT = {"pull": "to the pull side", "center": "up the middle", "oppo": "to the opposite field"}
_HARDNESS_TEXT = {"soft": "Soft", "medium": "Solid", "hard": "Hard"}
_TYPE_TEXT = {"ground": "grounder", "line": "line drive", "fly": "fly ball"}


@dataclass
class BattedBall:
    """The data for one fair ball. Output of a probability model, not a physics simulation."""

    direction: str          # "pull" / "center" / "oppo" (as seen from the batter)
    ball_type: str          # "ground" / "line" / "fly"
    hardness: str           # "soft" / "medium" / "hard"
    hardness_score: float   # continuous 0-1 value (the strength before rounding)
    distance: str           # "infield" / "shallow" / "deep"
    bonus: float = 0.0      # extra carry for a well-struck power swing (two-player mode only; 0 for everything else)

    def describe(self):
        return (f"{_HARDNESS_TEXT[self.hardness]} {_TYPE_TEXT[self.ball_type]} "
                f"{_DIRECTION_TEXT[self.direction]}")


def _weighted_pick(rng, pairs):
    """Weighted draw from [(value, weight), ...]. Negative weights are rounded up to 0."""
    values = [v for v, _ in pairs]
    weights = [max(0.0, w) for _, w in pairs]
    total = sum(weights)
    if total <= 0:
        return rng.choice(values)
    r = rng.random() * total
    upto = 0.0
    for value, weight in zip(values, weights):
        upto += weight
        if r <= upto:
            return value
    return values[-1]


def generate_batted_ball(batter, pitch, course, timing, contact_bias=0.0, rng=None):
    """`pitch` is the dict returned by pitch_data.get_pitch().

    contact_bias : the "hittable-ness" judge.py computes from hidden tendencies, pitch
                   history, and matchups. The higher it is, the harder the ball is hit.
    """
    rng = rng or random

    # --- 1. Direction (pull / center / oppo) ---
    p_pull = 0.30 + 0.40 * batter.pull     # pull hitters pull more
    p_oppo = 0.32 - 0.20 * batter.pull
    p_center = max(0.05, 1.0 - p_pull - p_oppo)
    if timing == "early":            # hit out front -> pulled
        p_pull += 0.16
        p_oppo -= 0.10
    elif timing == "late":           # jammed -> opposite field
        p_oppo += 0.16
        p_pull -= 0.10
    if zone_x(course) == "in":
        p_pull += 0.10
    elif zone_x(course) == "out":
        p_oppo += 0.10
    direction = _weighted_pick(rng, [("pull", p_pull), ("center", p_center), ("oppo", p_oppo)])

    # --- 2. Ball type (ground / line / fly) ---
    gb = 0.5 * pitch["groundball_rate"] + 0.5 * batter.gb_tendency
    p_ground = gb
    p_fly = (1 - gb) * 0.55
    p_line = (1 - gb) * 0.45
    if timing == "on_time":
        p_line += 0.12
        p_ground -= 0.06
        p_fly -= 0.06
    else:                            # mis-hits become grounders or weak fly balls
        p_ground += 0.08
        p_line -= 0.06
    if zone_y(course) == "lo":
        p_ground += 0.10
        p_fly -= 0.08
    elif zone_y(course) == "hi":
        p_fly += 0.10
        p_ground -= 0.08
    ball_type = _weighted_pick(rng, [("ground", p_ground), ("line", p_line), ("fly", p_fly)])

    # --- 3. How hard it is hit ---
    score = pitch["contact_quality"]
    if timing == "on_time":
        score += 0.18
    else:
        score -= 0.12
    if batter.coarse_type == "power":
        score += 0.10
    elif batter.coarse_type == "weak":
        score -= 0.05
    score += contact_bias
    score += rng.uniform(-0.08, 0.08)
    score = max(0.0, min(1.0, score))
    if score >= 0.62:
        hardness = "hard"
    elif score >= 0.38:
        hardness = "medium"
    else:
        hardness = "soft"

    # --- 4. Distance zone ---
    if ball_type == "ground":
        distance = "infield" if rng.random() < 0.80 else "shallow"
    elif ball_type == "line":
        if rng.random() < 0.30:
            distance = "infield"
        else:
            distance = "deep" if hardness == "hard" else "shallow"
    else:  # fly
        if hardness == "hard":
            distance = "deep" if rng.random() < 0.70 else "shallow"
        elif hardness == "soft":
            distance = "infield" if rng.random() < 0.35 else "shallow"
        else:
            distance = "shallow" if rng.random() < 0.60 else "deep"

    return BattedBall(direction, ball_type, hardness, round(score, 3), distance)
