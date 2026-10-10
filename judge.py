"""The core pitch-calling logic (judging one pitch).

The flow (keep the order from #5):

    Pitch Call          the catcher's request (pitch type / target location / intent)
      ↓  pitcher.execute_pitch()
    Actual Pitch        the real location and the quality of the pitch. It drifts on a miss
      ↓  batter.predict_guess() / strategy.evaluate_sequencing()
    Batter Reaction     did his guess hit? -> timing -> swing / take -> whiff / foul / fair
      ↓  batted_ball / fielding
    Result              if fair, the batted ball and the defense decide out or hit

The catcher's call moves the probabilities, but the result is decided by the pile-up of
  batter ability, pitcher ability, quality, the actual location, the batter's guess, the defense, and the dice.
The catcher alone cannot control 100% of it.

The "_analysis" in the return value is only for the post-game review. It is never shown on the live screen.
"""

import random

from baserunning import resolve as resolve_baserunning
from batted_ball import generate_batted_ball
from constants import COURSE_PROFILE, zone_overlap, zone_x, zone_y
from fielding import resolve_batted_ball
from pitch_data import family_of, get_pitch, guess_class_of, velocity_of
from strategy import (
    catcher_decision_quality,
    evaluate_sequencing,
    intent_effects,
)


def _decide_timing(guess_class, guess_strength, actual_class, seq, history, pitch_type, rng, loc_adj=0.0):
    """Decide the timing from the batter's guess and the actual pitch class.

    loc_adj: when he is also waiting for a spot, a pitch there is easier to time and a pitch elsewhere a little harder."""
    shift = seq["timing_shift"] + loc_adj        # negative if the setup threw him off (= more likely to mistime)

    if guess_class == actual_class:
        on_time_chance = 0.5 + 0.4 * guess_strength + shift
        if rng.random() < max(0.05, on_time_chance):
            timing = "on_time"
        else:
            timing = rng.choice(["early", "late"])
    else:
        if guess_class == "fastball" and actual_class == "offspeed":
            missed = "early"          # sitting fastball, got off-speed
        else:
            missed = "late"           # sitting off-speed, got a fastball
        if rng.random() < 0.25 * (1 - guess_strength) + shift * 0.5:
            timing = "on_time"
        else:
            timing = missed

    # A big speed gap from the previous pitch can also cause a mistime
    last = history.last()
    if timing == "on_time" and last is not None and last.get("velocity") is not None:
        gap = velocity_of(pitch_type) - last["velocity"]
        if abs(gap) >= 8 and rng.random() < 0.30:
            timing = "late" if gap > 0 else "early"
    return timing


def _history_whiff_adjust(history, pitch_type, course, batter):
    """Change in whiff probability from the pitch history and the batter's hidden traits.

    Weak / strong zones work "partially if close" through zone_overlap (9-zone aware).
    """
    adjust = 0.0
    if pitch_type == batter.weak_pitch:
        adjust += 0.12
    adjust += 0.08 * zone_overlap(course, batter.weak_course)
    adjust -= 0.08 * zone_overlap(course, batter.hot_course)
    if history.same_type_streak() >= 2:
        adjust -= 0.05
    return adjust


def _contact_quality_bias(history, pitch_type, course, batter):
    """The "hittability built from the calls and matchups". The higher it is, the harder the ball is hit.

    It does not decide hit or out directly; it moves how *hard* the ball is hit.
    """
    bias = 0.0
    bias += (batter.power - 0.5) * 0.30                 # the batter's power
    bias += COURSE_PROFILE[course]["contact_bonus"]     # how juicy / tough the location is
    if history.same_course_streak() >= 3:
        bias += 0.06
    last = history.last()
    if (last is not None and last["swung"]
            and last["pitch_type"] == pitch_type and last["timing"] != "on_time"):
        bias += 0.06
    bias += 0.14 * zone_overlap(course, batter.hot_course)     # the hidden hot zone
    if pitch_type == batter.weak_pitch:
        bias -= 0.12
    bias -= 0.09 * zone_overlap(course, batter.weak_course)    # the hidden weak zone
    return bias


# How the receive rhythm game (Web version only) result changes the framing probability.
# With frame_timing=None (CLI, a pitch that is not borderline, tests) it is 0.0 = same as before.
_FRAME_TIMING_BONUS = {"perfect": 0.22, "good": 0.06, "miss": -0.10}


def _frame_chance(actual_course, intent, state, frame_timing=None):
    """The chance of making a take that missed the zone look like a "strike".

    - Corners (edge both ways) are the easiest to steal / an edge on one side only is middling
    - With the "freeze" intent (going for a called strike) it goes up a little
    - On a 3-ball take the umpire is more careful -> it goes down a little
    - frame_timing is the Web receive rhythm game's result. Perfect raises it a lot, miss lowers it.
    """
    both_edge = zone_x(actual_course) != "mid" and zone_y(actual_course) != "mid"
    p = 0.16 if both_edge else 0.09
    if intent == "freeze":
        p += 0.06
    if state.balls >= 3:
        p -= 0.05
    p += _FRAME_TIMING_BONUS.get(frame_timing, 0.0)
    return max(0.0, min(0.9, p))


def _pressure_swing(batter, state):
    """In a big spot, a low pressure_tolerance makes his eye waver."""
    high_leverage = (state.outs >= 1 and any(state.runners)) or abs(state.score_diff) <= 1
    if not high_leverage:
        return 0.0
    return (0.5 - batter.pressure_tolerance) * 0.12   # if he is weak he swings more / wavers more


def prepare_pitch(state, pitch_type, target_course="middle", rng=None, intent="strike", tempo=0.0):
    """Steps 1-5 of judging a pitch: what the pitcher really threw, what the batter guesses, and whether it is in the zone.

    The one-player game runs this and the swing back to back inside judge_pitch(). The two-player game runs it first, so the page knows
    where the ball will cross the plate while the human batter is still choosing when to swing, then hands the result back
    to judge_pitch(prepared=...). The random numbers are drawn in the same order either way.
    """
    rng = rng or random
    pitcher = state.pitcher
    batter = state.batter
    history = state.history
    family = family_of(pitch_type)
    guess_class2 = guess_class_of(pitch_type)          # two-way (fastball / off-speed)

    # 1. EXECUTION: request -> actual
    actual = pitcher.execute_pitch(pitch_type, target_course, intent, rng, tempo=tempo)
    actual_course = actual["actual_course"]
    quality = actual["quality"]
    course_p = COURSE_PROFILE[actual_course]

    # 2. GUESS (the batter's guess. Hidden information)
    guess = batter.predict_guess(history, state, pitcher)
    # If the signs were stolen (#B), for this one pitch only his guess locks onto the real pitch
    if getattr(state, "sign_leak", 0.0) > 0:
        guess = dict(guess, **{"class": guess_class2,
                               "class_strength": max(guess["class_strength"], state.sign_leak)})
        state.sign_leak = 0.0

    # 3. SEQUENCING (the previous pitch sets up the next)
    call = {"pitch_type": pitch_type, "target_course": target_course, "intent": intent}
    seq = evaluate_sequencing(history, call, actual, batter, guess, state)
    intent_adj, intent_dq = intent_effects(intent, state.balls, state.strikes, batter, family)

    # 4. TIMING
    loc_adj = 0.0
    if guess["location"] != "any":
        loc_adj = 0.32 * guess["loc_strength"] * (2 * zone_overlap(guess["location"], actual_course) - 1)
    timing = _decide_timing(guess["class"], guess["class_strength"], guess_class2,
                            seq, history, pitch_type, rng, loc_adj=loc_adj)

    # 5. IN-ZONE (the actual location's strike rate x quality x intent)
    zone_chance = course_p["strike_rate"]
    if intent in ("chase", "waste"):
        zone_chance *= 0.45
    elif intent == "freeze":
        zone_chance = min(0.95, zone_chance + 0.10)
    zone_chance += (quality - 0.5) * 0.15
    # walk_adj (the intent / setup pushes toward "more likely a ball") -> lowers the zone probability
    zone_chance -= (intent_adj["walk_adj"] + seq["walk_adj"])
    in_zone = rng.random() < max(0.03, min(0.97, zone_chance))
    return {"pitch_type": pitch_type, "target_course": target_course, "intent": intent, "actual": actual, "guess": guess,
            "call": call, "seq": seq, "intent_adj": intent_adj, "intent_dq": intent_dq, "timing": timing, "in_zone": in_zone}


def judge_pitch(state, pitch_type, target_course="middle", rng=None, intent="strike",
                frame_timing=None, tempo=0.0, prepared=None, human=None):
    """Judge one pitch. human = None: the batter is the game's (a dice roll). human = a swing from swing.judge_swing() (or
    {"swung": False}): the human batter's own input decides whether he swings and what the swing does."""
    rng = rng or random

    pitcher = state.pitcher
    batter = state.batter
    history = state.history
    family = family_of(pitch_type)
    guess_class2 = guess_class_of(pitch_type)          # two-way (fastball / off-speed)
    fam_strength = batter.family_strength(family)      # the batter's strength against that family

    pre = prepared or prepare_pitch(state, pitch_type, target_course, rng, intent, tempo)
    actual, guess, call, seq = pre["actual"], pre["guess"], pre["call"], pre["seq"]
    intent_adj, intent_dq, timing, in_zone = pre["intent_adj"], pre["intent_dq"], pre["timing"], pre["in_zone"]
    actual_course = actual["actual_course"]
    quality = actual["quality"]
    if human is not None and human.get("swung"):
        timing = human["timing"]                           # the human's own timing, not a roll

    dq_total = catcher_decision_quality(call, actual, batter, guess, guess_class2, timing,
                                        history, state, seq) + intent_dq
    dq_total = max(-1.0, min(1.0, dq_total))

    analysis = {
        "guess": guess,
        "decision_quality": round(dq_total, 3),
        "sequence_label": seq["sequence_label"],
        "sequence_notes": seq["notes"],
        "fooled_guess": guess["class"] != guess_class2,
        "alignment_helped": False,
    }

    def _pack(result, swung, batted_ball=None, fielding=None, play=None, framed=False):
        out = {
            "result": result, "timing": timing, "swung": swung, "in_zone": in_zone,
            "pitch_type": pitch_type, "target_course": target_course,
            "actual_course": actual_course, "intent": intent,
            "missed": actual["missed"], "quality": round(quality, 2),
            "batted_ball": batted_ball, "fielding": fielding, "play": play, "framed": framed,
            "_analysis": analysis,
        }
        if human is not None:
            out["human"] = {k: v for k, v in human.items() if k != "batted"}
        return out

    # A pitchout is a pitch thrown well off the plate to go after a runner. Almost surely a ball, and the batter is not involved.
    if intent == "pitchout":
        in_zone = False
        return _pack("Ball", swung=False)

    def _fair_ball(batted_ball):
        """A ball in play: the defense, then the baserunners. (Shared by the dice batter and the human batter.)"""
        fielding = resolve_batted_ball(batted_ball, state.defense, batter, rng=rng)
        play = resolve_baserunning(batted_ball, fielding, state.runners, state.outs, batter, rng,
                                   runner_speeds=getattr(state, "runner_speeds", None),
                                   runner_moving=(getattr(state, "opp_tactic", None) == "hit_and_run"))
        # Facts the Play Viewer (playview.py) needs to replay this ball. Display-only: nothing reads them for judging.
        fielding["alignment"] = state.defense.alignment
        fielding["bats"] = batter.bats
        play["runners_before"] = list(state.runners)
        analysis["alignment_helped"] = (
            fielding["breakdown"]["alignment"] > 0 and fielding["result"] == "Out")
        return _pack(play["batter_result"], True, batted_ball, fielding, play)

    # 6. SWING
    if human is not None:
        # a human batter: he swings (or not) himself, and the cursor and the click decide the rest
        swung = bool(human.get("swung"))
        if swung:
            if human["contact"] == "miss":
                return _pack("Whiff", True)
            if human["contact"] == "foul":
                return _pack("Foul", True)
            return _fair_ball(human["batted"])
    else:
        swing_chance = (0.60 + 0.22 * (batter.aggression - 0.5)) if in_zone else batter.chase_rate
        if state.strikes == 2:
            swing_chance += 0.15 * (0.6 + 0.4 * (1 - batter.two_strike_ability))
        if guess["class"] == guess_class2:
            swing_chance += 0.10 * guess["class_strength"]
        else:
            swing_chance -= 0.10 * guess["class_strength"]
        if guess["location"] != "any":
            swing_chance += 0.06 * guess["loc_strength"] * zone_overlap(guess["location"], actual_course)
        swing_chance += intent_adj["swing_adj"] + seq["swing_adj"]
        swing_chance += _pressure_swing(batter, state)
        swung = rng.random() < max(0.02, min(0.98, swing_chance))

    if not swung:
        if in_zone:
            return _pack("Strike", swung)
        # --- Framing (the catcher's craft) ---
        # Only on a take that barely missed the zone, you can sometimes quietly catch it and make it look like a "strike".
        # It works better the closer to an edge / corner you asked, and does not work on a ball far off the middle.
        edge = zone_x(actual_course) != "mid" or zone_y(actual_course) != "mid"
        clearly_out = intent in ("chase", "waste", "pitchout")   # intents that miss big on purpose
        if (edge and not clearly_out
                and rng.random() < _frame_chance(actual_course, intent, state, frame_timing)):
            state.framed_strikes = getattr(state, "framed_strikes", 0) + 1
            return _pack("Strike", swung, framed=True)
        return _pack("Ball", swung)

    # 7. WHIFF
    whiff_chance = get_pitch(pitch_type)["whiff_rate"] * (0.7 + 0.6 * quality)
    if not in_zone:
        whiff_chance += 0.14
    if timing != "on_time":
        whiff_chance += 0.12
    whiff_chance += (batter.whiff_rate - 0.24) * 0.6
    whiff_chance -= (fam_strength - 0.5) * 0.15
    whiff_chance += _history_whiff_adjust(history, pitch_type, actual_course, batter)
    whiff_chance += seq["whiff_adj"] + intent_adj["whiff_adj"]
    if rng.random() < max(0.02, min(0.95, whiff_chance)):
        return _pack("Whiff", swung)

    # 8. FOUL
    foul_chance = 0.30
    if timing != "on_time":
        foul_chance += 0.13
    if state.strikes == 2:
        foul_chance += 0.06 * batter.two_strike_ability
    if rng.random() < foul_chance:
        return _pack("Foul", swung)

    # 9. FAIR -> batted ball -> defense
    contact_bias = _contact_quality_bias(history, pitch_type, actual_course, batter)
    contact_bias += (quality - 0.5) * -0.30
    contact_bias += (fam_strength - 0.5) * 0.20
    contact_bias += seq["contact_adj"] + intent_adj["contact_adj"]
    batted_ball = generate_batted_ball(batter, get_pitch(pitch_type), actual_course,
                                       timing, contact_bias=contact_bias, rng=rng)
    return _fair_ball(batted_ball)
