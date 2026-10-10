"""Pitch "intent", "setups" (the previous pitch sets up the next), and "quality of the catcher's decision".

What this file returns is a **small adjustment** added to the probabilities in judge.py,
**notes** for the post-game analysis, and an internal-only **decision_quality**.

Guiding principles:
  - No flat bonuses like "this call gives +20%".
  - An effect only appears when the batter's guess / previous pitch / pitch history / count /
    handedness / timing / actual location all line up. Each rule is worth +-0.02 to 0.06, and the total is clamped.
  - decision_quality never decides the result. It is just an internal measure of "quality of the decision".
"""

from constants import zone_overlap, zone_x, zone_y
from pitch_data import family_of, velocity_of

# The "intent" the catcher picks on every pitch
PITCH_INTENTS = {
    "strike": "Get ahead (throw a strike)",
    "chase": "Chase (get him to swing at a ball)",
    "weak_contact": "Induce weak contact",
    "waste": "Waste pitch (set up the next one)",
    "freeze": "Freeze him (steal a called strike)",
    "pitchout": "Pitchout (miss big to go after the runner)",
}

_ADJ_KEYS = ("timing_shift", "swing_adj", "whiff_adj", "contact_adj", "walk_adj")


def intent_label(intent):
    """The short name of an intent: the text before the parenthesis, e.g. "Get ahead"."""
    return PITCH_INTENTS[intent].split(" (")[0]


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# 1. Effects of the intent (plus or minus by count and matchup)
# ---------------------------------------------------------------------------
def intent_effects(intent, balls, strikes, batter, family):
    adj = dict.fromkeys(_ADJ_KEYS, 0.0)
    dq = 0.0

    if intent == "strike":
        adj["swing_adj"] += 0.03
        adj["whiff_adj"] -= 0.04
        dq += 0.10 if (balls >= strikes) else -0.05
        if strikes == 2:
            adj["contact_adj"] += 0.04         # getting it over with two strikes can get hit hard
            dq -= 0.05

    elif intent == "chase":
        adj["swing_adj"] -= 0.05               # it is outside the zone, so he usually lets it go
        if strikes == 2:
            adj["whiff_adj"] += 0.06
            adj["swing_adj"] += 0.08 * batter.aggression
            dq += 0.15
        else:
            adj["walk_adj"] += 0.05
            dq -= 0.20 if balls >= 3 else -0.05

    elif intent == "weak_contact":
        adj["contact_adj"] -= 0.05
        adj["whiff_adj"] -= 0.02
        dq += 0.06
        if family != "fastball":
            dq -= 0.03                         # "induce weak contact" does not mesh well with a breaking ball

    elif intent == "waste":
        adj["swing_adj"] -= 0.10
        if strikes == 2 and balls <= 1:
            dq += 0.10                         # a waste pitch after getting ahead makes sense
        else:
            dq -= 0.15                         # a waste pitch in a tough count is a wasted pitch

    elif intent == "freeze":
        adj["swing_adj"] -= 0.06
        if strikes < 2:
            dq += 0.08

    elif intent == "pitchout":
        # Almost surely a ball. It is a pitch aimed at the runner, so the batter barely matters.
        adj["swing_adj"] -= 0.40
        adj["walk_adj"] += 0.30
        # Quality of the decision: worth it if the runner is going / otherwise it costs you one pitch
        dq += -0.20 if balls >= 2 else -0.05

    return adj, dq


# ---------------------------------------------------------------------------
# 2. Setups (the previous pitch sets up the next. #6 #7)
#    Each rule returns a small adjustment + a note only "when its conditions line up".
# ---------------------------------------------------------------------------
def _seq_rules(history, call, actual, batter, guess, state):
    pt = call["pitch_type"]
    fam = family_of(pt)
    velo = velocity_of(pt)
    actual_course = actual["actual_course"]
    ax, ay = zone_x(actual_course), zone_y(actual_course)
    is_meatball = actual_course == "mid_mid"
    last = history.last()
    records = history.records

    out = []   # (adjust_dict, note, dq_delta)

    # --- First pitch ---
    if last is None:
        if guess["class"] == "fastball" and fam != "fastball":
            out.append(({"whiff_adj": 0.04, "contact_adj": -0.04},
                        "First-pitch off-speed threw off a fastball-sitter", 0.10))
        if is_meatball and guess["class"] == "fastball":
            out.append(({"contact_adj": 0.06}, "Left a first pitch in the middle for him to hit", -0.12))
        return out

    lam_c = last["actual_course"]
    lam_x, lam_y = zone_x(lam_c), zone_y(lam_c)
    lam_v = last.get("velocity")
    lam_f = last.get("family")

    # --- The ladder (low -> high / high -> low) ---
    if lam_y == "lo" and ay == "hi" and fam == "fastball":
        out.append(({"whiff_adj": 0.05, "timing_shift": -0.05},
                    "Low-to-high chase fastball (ladder)", 0.08))
    if lam_y == "hi" and ay == "lo" and fam in ("breaking", "offspeed"):
        out.append(({"whiff_adj": 0.04, "contact_adj": -0.05},
                    "Low breaking ball after showing high", 0.08))

    # --- Speed gap (change of pace from the previous pitch) ---
    if lam_v is not None:
        gap = abs(velo - lam_v)
        if gap >= 8:
            out.append(({"whiff_adj": 0.03, "timing_shift": -0.03},
                        "Big speed gap from the previous pitch", 0.05))
            if lam_f is not None and lam_f != fam:
                out.append(({"whiff_adj": 0.02}, "Changed both pitch type and speed", 0.03))
        elif gap <= 3 and lam_f == fam:
            out.append(({"contact_adj": 0.04},
                        "Similar speed and family in a row, easy to read", -0.08))

    # --- Working in and out ---
    if {lam_x, ax} == {"in", "out"}:
        out.append(({"swing_adj": -0.03, "whiff_adj": 0.02},
                    "Worked him in and out", 0.05))

    # --- Fastball -> changeup ---
    if lam_f == "fastball" and fam == "offspeed":
        out.append(({"whiff_adj": 0.05, "timing_shift": -0.04},
                    "Changeup off a fastball", 0.07))

    # --- A breaking ball in the zone -> a breaking ball out of the zone ---
    if lam_f == "breaking" and last.get("in_zone") and fam == "breaking" and ay != "mid":
        out.append(({"whiff_adj": 0.05, "swing_adj": 0.03},
                    "Breaking ball out of the zone after one in the zone", 0.07))

    # --- An inside fastball to a jammed batter ---
    if records[-1].get("timing") == "late" and ax == "in" and fam == "fastball":
        out.append(({"whiff_adj": 0.04, "contact_adj": -0.05},
                    "Inside fastball to a jammed hitter", 0.08))

    # --- Off-speed / an outside pitch to a batter out front ---
    if records[-1].get("timing") == "early" and (fam != "fastball" or ax == "out"):
        out.append(({"whiff_adj": 0.04},
                    "Off-speed / outside pitch to a hitter out front", 0.06))

    # --- Pitcher vs the opposite-handed batter ---
    if state.pitcher.throws != batter.bats and (fam == "offspeed" or ax == "out"):
        out.append(({"contact_adj": -0.03},
                    "Pitch running away from the opposite-side batter (platoon)", 0.04))

    # --- The same (pitch type, location) repeated within an at-bat ---
    same_combo = sum(1 for r in records
                     if r["pitch_type"] == pt and r["actual_course"] == actual_course)
    if same_combo >= 1:
        out.append(({"whiff_adj": -0.06, "contact_adj": 0.06},
                    "Showed the same pitch in the same spot too often", -0.15))

    # --- Three or more in a row from the same family ---
    if history.same_type_streak() >= 3:
        out.append(({"contact_adj": 0.05},
                    "Same family repeated, easy for him to adjust", -0.10))

    # --- A middle pitch after getting to two strikes ---
    if strikes_is_two(state) and is_meatball:
        out.append(({"contact_adj": 0.08},
                    "Left it over the middle after getting two strikes", -0.20))

    return out


def strikes_is_two(state):
    return state.strikes == 2


def evaluate_sequencing(history, call, actual, batter, guess, state):
    """Return the combined setup effect."""
    total = dict.fromkeys(_ADJ_KEYS, 0.0)
    dq_seq = 0.0
    notes = []
    label_bits = []

    for adjust, note, dq_delta in _seq_rules(history, call, actual, batter, guess, state):
        for key, value in adjust.items():
            total[key] += value
        dq_seq += dq_delta
        notes.append(note)
        label_bits.append(note)

    # Clamp so the effect cannot get out of hand (no flat bonuses)
    for key in ("swing_adj", "whiff_adj", "contact_adj", "walk_adj"):
        total[key] = _clamp(total[key], -0.15, 0.15)
    total["timing_shift"] = _clamp(total["timing_shift"], -0.10, 0.10)
    dq_seq = _clamp(dq_seq, -0.25, 0.25)

    total["notes"] = notes
    total["dq_seq"] = dq_seq
    total["sequence_label"] = " / ".join(label_bits) if label_bits else "Nothing notable"
    return total


# ---------------------------------------------------------------------------
# 3. Catcher Decision Quality (an internal measure. NEVER shown during play. #9)
# ---------------------------------------------------------------------------
def _intent_count_fit(intent, balls, strikes):
    if intent == "chase":
        return 0.15 if strikes == 2 else (-0.25 if balls >= 3 else -0.05)
    if intent == "strike":
        return 0.10 if (balls >= strikes) else -0.05
    if intent == "freeze":
        return 0.08 if strikes < 2 else 0.0
    if intent == "weak_contact":
        return 0.05
    if intent == "waste":
        return 0.10 if (strikes == 2 and balls <= 1) else -0.15
    if intent == "pitchout":
        return -0.10 if balls >= 2 else -0.02   # if the runner goes, engine.py rewards it separately
    return 0.0


def _defense_synergy(defense, batter):
    align = defense.alignment
    if align == "pull" and batter.pull > 0.6:
        return 0.05
    if align == "oppo" and batter.pull < 0.4:
        return 0.05
    if align in ("pull", "oppo"):
        return -0.03
    return 0.0


def catcher_decision_quality(call, actual, batter, guess, guess_class2, timing,
                             history, state, seq):
    """An internal score (-1 to +1) for how good the decision was. It does not decide the result."""
    actual_course = actual["actual_course"]

    dq = 0.0
    # Did you fool his guess (fastball / off-speed)?
    fooled = (guess["class"] != guess_class2)
    dq += (0.30 if fooled else -0.30) * guess["class_strength"]
    # Location (the batter's hidden zones. This is the truth used to judge the "quality of the decision" afterward)
    dq += 0.20 * zone_overlap(actual_course, batter.weak_course)
    dq -= 0.25 * zone_overlap(actual_course, batter.hot_course)
    if actual_course == "mid_mid":
        dq -= 0.12
    # Did you throw off his timing?
    if timing != "on_time":
        dq += 0.15
    # Overshowing a pitch
    if history.same_type_streak() >= 3:
        dq -= 0.15
    # How the intent fits the count
    dq += _intent_count_fit(call["intent"], state.balls, state.strikes)
    # The process of the setup
    dq += _clamp(0.6 * seq["dq_seq"], -0.15, 0.15)
    # How well it fits the defensive alignment
    dq += _defense_synergy(state.defense, batter)

    return _clamp(dq, -1.0, 1.0)


# ---------------------------------------------------------------------------
# 4. Post-game analysis (#10). Tallies result and decision separately.
# ---------------------------------------------------------------------------
_HIT_RESULTS = ("Single", "Double", "Triple", "Home run")
_EXTRA_BASE = ("Double", "Triple", "Home run")


def _result_is_bad(result):
    return result in _HIT_RESULTS or result in ("Walk", "Error")


def _result_is_good(result, outcome):
    if result in ("Whiff", "Out"):
        return True
    if result == "Strike" and not outcome["swung"] and outcome["in_zone"]:
        return True   # got a called strike
    return False


_WAIT_TEXT = {"fastball": "Fastball", "offspeed": "Off-speed"}


def grade_reads(reads, batters):
    """Grade the catcher's notes (#B) against the hidden truth.

    reads   : {id(batter): {"spot", "wait", "weak"}}
    batters : the nine batters in the lineup (Batter)
    """
    by_id = {id(b): b for b in batters}
    rows, graded, correct = [], 0, 0
    for bid, note in reads.items():
        batter = by_id.get(bid)
        if batter is None:
            continue
        parts = [f"#{note.get('spot', '?')} {getattr(batter, 'name', '')}".strip()]
        if note.get("wait"):
            truth = "fastball" if batter.guess_bias >= 0.5 else "offspeed"
            hit = note["wait"] == truth
            graded += 1
            correct += hit
            mark = "◎ Right" if hit else "× Wrong"
            parts.append(f"Looking for: {_WAIT_TEXT[note['wait']]} -> {mark} (actually {_WAIT_TEXT[truth]})")
        if note.get("weak"):
            overlap = zone_overlap(note["weak"], batter.weak_course)
            graded += 1
            correct += (overlap >= 1.0)
            mark = "◎ Right" if overlap >= 1.0 else ("△ Close (same row or column)" if overlap >= 0.5 else "× Wrong")
            parts.append(f"Weak zone: {note['weak']} -> {mark} (actually {batter.weak_course})")
        rows.append("  " + " / ".join(parts))
    return {"rows": rows, "graded": graded, "correct": correct}


def build_analysis(pitch_log):
    """Tally pitch_log (a list of dicts, one per pitch) into easy-to-read groups."""
    total = len(pitch_log)
    good_calls, risky_calls = [], []
    fooled_moments, read_moments = [], []
    defense_moments, misfire_moments = [], []
    unlucky, lucky, extra_base = [], [], []

    dq_values = []
    for entry in pitch_log:
        dq = entry["decision_quality"]
        dq_values.append(dq)
        num = entry["pitch_number"]
        label = entry.get("call_label_text", entry["call_label"])   # for display; falls back to the internal form
        result = entry["result"]

        if dq >= 0.25:
            good_calls.append((num, label, entry["sequence_label"]))
        if dq <= -0.25:
            risky_calls.append((num, label, entry["sequence_label"]))

        g = entry["guess"]
        if entry["fooled_guess"] and g["class_strength"] >= 0.4:
            fooled_moments.append((num, label))
        if (not entry["fooled_guess"]) and g["class_strength"] >= 0.5:
            read_moments.append((num, label))

        if entry.get("alignment_helped"):
            defense_moments.append((num, label))
        if entry["missed"] and _result_is_bad(result):
            misfire_moments.append((num, label))

        if dq >= 0.25 and _result_is_bad(result):
            unlucky.append((num, label, result))
        if dq <= -0.25 and _result_is_good(result, entry["outcome_flags"]):
            lucky.append((num, label, result))
        if result in _EXTRA_BASE:
            extra_base.append((num, label, result))

    avg_dq = sum(dq_values) / total if total else 0.0
    if avg_dq >= 0.15:
        dq_label = "Good (kept batters off balance)"
    elif avg_dq >= 0.0:
        dq_label = "Slightly good"
    elif avg_dq >= -0.15:
        dq_label = "Slightly loose"
    else:
        dq_label = "Loose (too many predictable, get-it-over pitches)"

    return {
        "total_pitches": total,
        "good_calls": good_calls,
        "risky_calls": risky_calls,
        "fooled_moments": fooled_moments,
        "read_moments": read_moments,
        "defense_moments": defense_moments,
        "misfire_moments": misfire_moments,
        "unlucky": unlucky,      # good decision, bad result
        "lucky": lucky,          # bad decision, saved by the result
        "extra_base": extra_base,   # extra-base hits allowed
        "avg_decision_quality": round(avg_dq, 3),
        "decision_quality_label": dq_label,
    }


def build_postgame_report(state):
    """The tally for the Catcher Report after three outs (#Postgame Report).

    On top of the "quality of decisions" analysis from build_analysis(), it adds
    at-a-glance result numbers (runs / batters faced / pitches / strikeouts / walks / hits) and
    summaries of Pitch Execution (how the pitcher executed) and Defense, which are separate axes from
    the quality of decisions (decision_quality). A pure tally function that never touches the judging logic.
    """
    log = state.pitch_log
    strikeouts = sum(1 for p in log if p.get("at_bat_end") == "strikeout")
    walks = sum(1 for p in log if p.get("at_bat_end") == "walk") + state.events.count("Intentional walk")
    hits = sum(1 for p in log if p.get("at_bat_end") == "hit")
    errors = sum(1 for p in log if p.get("at_bat_end") == "error")

    missed_pitches = sum(1 for p in log if p["missed"])
    avg_quality = sum(p["quality"] for p in log) / len(log) if log else 0.0

    return {
        "runs_allowed": state.runs_this_inning,
        "batters_faced": state.batters_faced,
        "pitches": len(log),
        "strikeouts": strikeouts,
        "walks": walks,
        "hits": hits,
        "errors": errors,
        "framed_strikes": getattr(state, "framed_strikes", 0),
        "pitch_execution": {
            "missed_pitches": missed_pitches,
            "missed_rate": round(missed_pitches / len(log), 3) if log else 0.0,
            "avg_quality": round(avg_quality, 3),
        },
        "defense": {
            "alignment_helped": sum(1 for p in log if p["alignment_helped"]),
            "errors": errors,
            "final_ede": round(state.defense.expected_defensive_efficiency(), 1),
        },
    }


_RATING_TITLES = (
    (85, "S", "Field General"),
    (70, "A", "Veteran Backstop"),
    (55, "B", "Reliable Receiver"),
    (40, "C", "Rookie Backstop"),
    (0, "D", "Back to the Bullpen"),
)


def catcher_rating(state, report=None, analysis=None):
    """Turn a finished half inning into a Catcher Rating (S to D), a title, and a few badges.

    Points out of 100: runs allowed (40) + decision quality (30) + strikeouts (12)
    + framing (8) + holding the lead (10). A pure tally that never touches the judging logic.
    """
    report = report or build_postgame_report(state)
    analysis = analysis or build_analysis(state.pitch_log)

    runs = report["runs_allowed"]
    runs_pts = {0: 40, 1: 22, 2: 8}.get(runs, 0)
    dq = analysis["avg_decision_quality"]
    dq_unit = max(-1.0, min(1.0, dq / 0.25))
    dq_pts = 30 * dq_unit if dq_unit >= 0 else 15 * dq_unit
    k_pts = 4 * min(report["strikeouts"], 3)
    frame_pts = 2 * min(report["framed_strikes"], 4)
    start_lead = state.start_our - state.start_opp
    end_lead = state.our_score - state.opp_score
    lead_pts = 10 if end_lead >= min(start_lead, 0) else 0

    score = max(0, min(100, round(runs_pts + dq_pts + k_pts + frame_pts + lead_pts)))
    grade, title = next((g, t) for floor, g, t in _RATING_TITLES if score >= floor)

    badges = []
    if runs == 0:
        badges.append(("Shutdown", "Not a single run allowed"))
    if report["strikeouts"] >= 2:
        badges.append(("Strikeout Artist", f"{report['strikeouts']} strikeouts"))
    if report["framed_strikes"] >= 2:
        badges.append(("Pickpocket", f"{report['framed_strikes']} strikes stolen by framing"))
    if len(analysis["fooled_moments"]) >= 3:
        badges.append(("Mind Games", f"Fooled the batter's guess {len(analysis['fooled_moments'])} times"))
    if state.events.count("Caught stealing") >= 1:
        badges.append(("Cannon Arm", "Threw out a base stealer"))
    if len(analysis["unlucky"]) >= 2:
        badges.append(("Hard Luck", "Good calls that did not get the result they deserved"))
    return {"score": score, "grade": grade, "title": title, "badges": badges}
