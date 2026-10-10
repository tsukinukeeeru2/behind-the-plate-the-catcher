"""Turns a ball hit fair into "which fielder" and "can he handle it (out / hit)".

out probability =  base_out_probability             the raw value set by ball type and distance
                 + defender_skill_adjustment        the fielder's overall rating (50 is average)
                 + position_fit_adjustment          is he placed well? x position difficulty
                 + defensive_alignment_adjustment   does the shift match the ball?
                 + batted_ball_adjustment           the harder it is hit, the less likely an out

The returned `breakdown` shows how much each line moved the out rate.
"""

import random

from fielders import position_fit

# The "raw" out probability by ball type x distance.
BASE_OUT_PROB = {
    ("ground", "infield"): 0.76,
    ("ground", "shallow"): 0.35,   # a grounder that almost got through the infield
    ("line", "infield"): 0.42,     # a sharp liner (catchable if right at someone)
    ("line", "shallow"): 0.30,
    ("line", "deep"): 0.40,
    ("fly", "infield"): 0.92,      # infield fly
    ("fly", "shallow"): 0.78,
    ("fly", "deep"): 0.74,
}

# Adjustment by how hard the ball is hit
HARDNESS_ADJUST = {"soft": +0.10, "medium": -0.02, "hard": -0.13}

# Base probability of an error (letting a catchable ball reach via a drop or a bad throw)
ERROR_BASE = 0.045


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _hit_extent(bb, batter, rng):
    """When it is a hit, decide whether it is a single / double / triple / home run.

    Probabilities move with the ball's hardness, type, distance, direction, and the batter's power / speed.
    """
    power = getattr(batter, "power", 0.5)
    speed = getattr(batter, "speed", 50)
    t, d, h = bb.ball_type, bb.distance, bb.hardness

    # Home run: only a hard-hit fly ball / line drive that carried deep
    if h == "hard" and d == "deep" and t in ("fly", "line"):
        hr_p = 0.10 + 0.30 * power
        if bb.direction == "pull":
            hr_p += 0.08
        elif bb.direction == "oppo":
            hr_p -= 0.05
        if t == "line":
            hr_p *= 0.5                      # a line drive rarely leaves the park
        if rng.random() < max(0.0, hr_p):
            return "Home run"

    # Triple: a ball into a deep gap + a fast runner
    if d == "deep" and t in ("line", "fly"):
        tri_p = 0.05 + (speed - 50) / 100 * 0.25
        if bb.direction == "center":
            tri_p += 0.04
        if rng.random() < max(0.0, tri_p):
            return "Triple"

    # Double: a deep ball / a shallow liner / a hard grounder down the line
    if d == "deep":
        dbl_p = 0.55
    elif t == "line" and d == "shallow":
        dbl_p = 0.15
    elif t == "ground" and h == "hard" and bb.direction == "pull":
        dbl_p = 0.20
    else:
        dbl_p = 0.0
    if rng.random() < dbl_p:
        return "Double"

    return "Single"


def _field_side(direction, bats):
    """Convert the batter-relative direction (pull/center/oppo) into the real side (left/center/right).

    A right-handed batter's pull = toward left field / a left-handed batter's pull = toward right field.
    This is the core of "where you hide a weak fielder depends on whether the batter hits righty or lefty".
    """
    if direction == "center":
        return "center"
    pull_is_left = (bats == "R")
    if direction == "pull":
        return "left" if pull_is_left else "right"
    return "right" if pull_is_left else "left"   # oppo


def _error_chance(overall, fit, hardness):
    """The chance a ball that should have been an out becomes an error.

    It drops as overall rating and position fit rise, and rises with harder-hit balls.
    """
    chance = ERROR_BASE
    chance -= (overall - 50) / 100 * 0.10
    chance -= (fit - overall) / 100 * 0.04
    if hardness == "hard":
        chance += 0.02
    elif hardness == "soft":
        chance -= 0.015
    return _clamp(chance, 0.01, 0.15)


def responsible_position(batted_ball, bats, rng=None):
    """The position of the fielder who gets to the ball first."""
    rng = rng or random
    side = _field_side(batted_ball.direction, bats)

    reaches_outfield = (
        batted_ball.ball_type == "fly"
        or (batted_ball.ball_type == "line" and batted_ball.distance != "infield")
    )
    if reaches_outfield:
        return {"left": "LF", "center": "CF", "right": "RF"}[side]

    # Infield grounder / infield liner / pop-up
    if side == "center":
        return rng.choice(["SS", "2B"])          # up the middle
    if side == "left":
        return "3B" if rng.random() < 0.4 else "SS"
    return "1B" if rng.random() < 0.4 else "2B"   # right


def resolve_batted_ball(batted_ball, defense, batter, rng=None):
    """From the ball + the defensive alignment + the batter, decide out or hit."""
    rng = rng or random

    position = responsible_position(batted_ball, batter.bats, rng)
    fielder = defense.fielder_at(position)

    base = BASE_OUT_PROB.get((batted_ball.ball_type, batted_ball.distance), 0.45)

    overall = fielder.overall()
    fit = position_fit(fielder, position)

    # The overall rating itself (the gap from the average of 50)
    defender_skill_adjustment = _clamp((overall - 50) / 100, -0.15, 0.15) * 0.7
    # "Is he placed well" = the gap between fit and overall. The harder the position, the wider the gap.
    position_fit_adjustment = _clamp((fit - overall) / 100, -0.25, 0.25) * 1.0
    # Does the shift match the ball?
    defensive_alignment_adjustment = defense.alignment_out_adjust(position, batted_ball)
    # The harder the ball is hit, the harder it is to turn into an out
    batted_ball_adjustment = HARDNESS_ADJUST[batted_ball.hardness]

    # A fast batter can beat out an infield grounder for a hit
    speed_adjustment = 0.0
    if batted_ball.ball_type == "ground" and position in ("1B", "2B", "3B", "SS"):
        speed_adjustment = -(getattr(batter, "speed", 50) - 50) / 100 * 0.14

    out_p = (base
             + defender_skill_adjustment
             + position_fit_adjustment
             + defensive_alignment_adjustment
             + batted_ball_adjustment
             + speed_adjustment)
    out_p = _clamp(out_p, 0.03, 0.97)

    error_p = _error_chance(overall, fit, batted_ball.hardness)
    is_out = rng.random() < out_p
    is_error = is_out and rng.random() < error_p
    if is_error:
        is_out = False
        batter_result = "Error"
    elif is_out:
        batter_result = "Out"
    else:
        batter_result = _hit_extent(batted_ball, batter, rng)

    return {
        "result": batter_result,               # "Out" / "Error" / "Single" / "Double" / "Triple" / "Home run"
        "batter_out": is_out,
        "error": is_error,
        "air_out": is_out and batted_ball.ball_type in ("fly", "line"),
        "position": position,
        "fielder": fielder.name,
        "out_probability": round(out_p, 3),
        "breakdown": {
            "base": round(base, 3),
            "defender_skill": round(defender_skill_adjustment, 3),
            "position_fit": round(position_fit_adjustment, 3),
            "alignment": round(defensive_alignment_adjustment, 3),
            "batted_ball": round(batted_ball_adjustment, 3),
            "runner_speed": round(speed_adjustment, 3),
            "error_chance": round(error_p, 3),
        },
    }
