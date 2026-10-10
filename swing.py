"""A human batter's swing (two-player mode): where the cursor was and when he clicked decide what the swing did.

The batter moves a round "meat cursor" over the strike zone with the mouse and clicks to swing. This file turns that one click into a
result the rest of the game already understands: a miss, a foul, or a BattedBall (direction, ball type, hardness, distance) that the
normal fielding and baserunning rules then play out. Nothing here is drawn; the browser only reports three numbers:

    dx, dy : where the cursor's centre was, relative to where the ball crossed the plate (metres; +dy = cursor above the ball)
    off    : seconds the bat arrived after (+) or before (-) the ball

The rules, all in plain sight:
  * the ball must touch the cursor's circle (radius + the ball's own radius) and the bat must be within TIME_MAX of the ball: else it is a miss;
  * how centred (dist) and how well timed (off) the contact was give its quality: square = hard, off-centre or mistimed = soft, edge = foul;
  * early contact pulls the ball, late contact sends it to the opposite field (the game turns pull/oppo into left/right for each batter);
  * the cursor above the ball's centre meets the top of the ball and drives it down (grounder), below the centre it gets under it and lifts it
    (fly ball), on the centre is a line drive;
  * a power swing has a smaller cursor and a tighter timing window, and pays back with harder, deeper contact.
"""

import math
import random

from batted_ball import BattedBall

BALL_R = 0.037                      # the ball's radius (m)
TIME_MAX = 0.17                     # beyond this many seconds early / late the bat cannot touch the ball
TIME_GOOD = 0.07                    # inside this it is called "Good", outside it "Early" / "Late"
MODES = {
    "normal": {"radius": 0.15, "window": 1.0, "power": 0.0, "label": "NORMAL"},
    "power": {"radius": 0.085, "window": 0.85, "power": 0.12, "label": "POWER"},
    # the standard swing of the online game: no cursor (the page aims for you), one press of Space, a wide window for the timing
    "auto": {"radius": 0.17, "window": 1.0, "power": 0.0, "label": "AUTO", "tmax": 0.20, "good": 0.09, "perfect": 0.04},
}
AUTO_AIM = 0.35                     # the automatic aim sits this fraction of the way from the zone's centre to the ball (the page applies it)


def mode_of(name):
    return MODES.get(name, MODES["normal"])


def timing_word(off, m=None):
    """The batter's timing as the game's three words (and the one printed on screen).

    Returns (timing, label, grade): timing is early / on_time / late (what the game logic uses), label is what is shown
    (EARLY / GOOD / LATE, and PERFECT in the standard swing), grade is perfect / good / early / late.
    """
    good = (m or {}).get("good", TIME_GOOD)
    perfect = (m or {}).get("perfect")
    if perfect is not None and abs(off) <= perfect:
        return "on_time", "PERFECT", "perfect"
    if off < -good:
        return "early", "EARLY", "early"
    if off > good:
        return "late", "LATE", "late"
    return "on_time", "GOOD", "good"


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def position_words(dx, dy):
    """Where the bat was relative to the ball, in words for the screen (centimetres)."""
    if math.hypot(dx, dy) < 0.015:
        return "Dead centre"
    v = ("%d cm %s" % (round(abs(dy) * 100), "above" if dy > 0 else "below")) if abs(dy) >= 0.012 else ""
    h = ("%d cm to the %s" % (round(abs(dx) * 100), "right" if dx > 0 else "left")) if abs(dx) >= 0.012 else ""
    if v and h:
        return "%s and %s of the ball" % (v, h)
    return (v + " the ball") if v else (h + " of the ball")


def judge_swing(dx, dy, off, mode, batter, rng=None):
    """Decide one swing. Returns a dict:

        contact : "miss" | "foul" | "fair"
        batted  : a BattedBall when contact == "fair", else None
        timing / timing_label : early / on_time / late and the word shown
        off, dx, dy, dist, centre (0 = edge .. 1 = dead centre), mode
    """
    rng = rng or random
    m = mode_of(mode)
    dist = math.hypot(dx, dy)
    reach = m["radius"] + BALL_R
    te = abs(off) / (m.get("tmax", TIME_MAX) * m["window"])   # 0 = perfectly timed .. 1 = at the limit
    e = dist / reach                                       # 0 = dead centre .. 1 = on the rim
    timing, label, grade = timing_word(off, m)
    info = {"timing": timing, "timing_label": label, "grade": grade, "off": round(off, 3), "dx": round(dx, 3), "dy": round(dy, 3),
            "dist": round(dist, 3), "centre": round(_clamp(1.0 - e, 0.0, 1.0), 2), "mode": "power" if m is MODES["power"] else ("auto" if m is MODES["auto"] else "normal"),
            "contact": "miss", "batted": None}
    if te >= 1.0 or e > 1.0:
        return info
    # a mistimed or edge-of-the-cursor contact is a foul more often than not
    mishit = 0.62 * te + 0.52 * e + rng.gauss(0.0, 0.05)
    if mishit > 0.45:
        info["contact"] = "foul"
        return info
    quality = _clamp(1.0 - 0.55 * e - 0.55 * te, 0.0, 1.0)
    score = 0.08 + 0.85 * quality ** 1.5 + m["power"] + (getattr(batter, "power", 0.5) - 0.5) * 0.16 + rng.uniform(-0.03, 0.03)
    score = _clamp(score, 0.0, 1.0)
    hardness = "hard" if score >= 0.62 else ("medium" if score >= 0.38 else "soft")

    theta = -off / 0.10 + rng.gauss(0.0, 0.15)             # early (off < 0) pulls, late goes the other way
    direction = "pull" if theta > 0.30 else ("oppo" if theta < -0.30 else "center")

    v = -dy / reach + m["power"] * 0.8 + rng.gauss(0.0, 0.06)   # lift: + = the cursor was below the ball's centre (under it); a power swing is an uppercut
    if v < -0.30:
        ball_type = "ground"
    elif v < 0.18:
        ball_type = "line"
    else:
        ball_type = "fly"
    popup = v > 0.70

    if ball_type == "ground":
        distance = "shallow" if (hardness == "hard" and rng.random() < 0.35) else "infield"
    elif ball_type == "line":
        if score < 0.30:
            distance = "infield"
        elif score < 0.52:
            distance = "shallow"
        else:
            distance = "deep"
    else:
        if popup:
            distance = "infield"
        elif hardness == "hard":
            distance = "deep"
        elif hardness == "medium":
            distance = "deep" if score > 0.52 else "shallow"
        else:
            distance = "shallow" if score > 0.25 else "infield"

    bb = BattedBall(direction, ball_type, hardness, round(score, 3), distance)
    bb.bonus = 0.5 * max(0.0, quality - 0.6) if m["power"] else 0.0       # a well-struck power swing carries
    info["contact"] = "fair"
    info["batted"] = bb
    return info


def public_info(info):
    """The part of a judged swing the page may see (no objects)."""
    return {k: v for k, v in info.items() if k != "batted"}
