"""The Game Engine -- the logic that CLI (main.py) and Web (webapp.py) share to
"judge one pitch and advance the state".

    Game Engine (engine.py)
        ↑            ↑
      main.py     webapp.py
      (CLI)         (Web)

This file never print()s and never input()s. The display layer (CLI or Web) just shows
the (outcome, list of messages) that resolve_one_pitch() returns in its own way
(print / HTML). It is the boundary that avoids implementing the logic twice.

    Pitch Call (pitch type, location, intent)
      ↓  judge.judge_pitch()
    Actual Pitch -> batter reaction -> (if fair) batted ball, defense, baserunning
      ↓
    Surrounding events: dropped third strike / blocking / steals / stolen signs
      ↓
    apply_result()  -- settles the count, outs, and runners
"""

import datetime as _dt
import json
import os

import opponent
from constants import COURSE_SHORT, is_corner, zone_x, zone_y
from judge import judge_pitch
from pitch_data import family_of, pitch_name, velocity_of
from qte import catcher_block, catcher_change_signs, catcher_field_bunt, catcher_throw
from reaction import describe_reaction
from strategy import PITCH_INTENTS, grade_reads, intent_label

FAIR_HITS = ("Single", "Double", "Triple", "Home run")

_NEUTRAL_ANALYSIS = {
    "guess": {"class": "fastball", "class_strength": 0.0}, "decision_quality": 0.0,
    "sequence_label": "Nothing notable", "sequence_notes": [],
    "fooled_guess": False, "alignment_helped": False,
}


def begin_at_bat(state, rng):
    """Call once before the first pitch of an at-bat. Decides the opposing bench's tactic (#C) and returns the line the catcher can see.

    A bunt stance is visible, but a hit-and-run is not (it returns an empty string).
    """
    # Idempotent: draw random numbers only once per at-bat. The Web version calls
    # begin_at_bat every time it re-renders the page, so without the flag every GET would
    # make decide_tactic consume random numbers and break --seed reproducibility
    # (even whether a tactic appears could change).
    if (state.history.records or state.opp_tactic is not None
            or getattr(state, "tactic_decided", False)):
        return ""
    state.tactic_decided = True
    state.opp_tactic = opponent.decide_tactic(state, rng)
    return opponent.describe(state.opp_tactic) if state.opp_tactic else ""


def apply_result(state, outcome, on_event=print):
    """Apply one pitch's result to the count and the situation. Strikeouts, walks, and baserunning are settled here.

    on_event : a callback that receives each event string (default: print).
              A display layer other than the CLI (webapp.py, etc.) can reuse this logic
              as is by passing a function that just appends to a list of messages.

    When an at-bat ends, it adds at_bat_end ("strikeout"/"walk"/"hit"/"error"/"out") to the
    last entry of pitch_log that log_pitch() just pushed. It is only a tally tag for the Postgame
    Report (#Batters Faced / Strikeouts / Walks / Hits) and has no effect on the judging itself.
    """
    result = outcome["result"]
    pa_end = None
    if result in ("Strike", "Whiff"):
        state.strikes += 1
        if state.strikes >= 3:
            on_event(">>> Strikeout!")
            state.add_out()
            pa_end = "strikeout"
    elif result == "Ball":
        state.balls += 1
        if state.balls >= 4:
            on_event(">>> Walk")
            state.advance_on_walk()
            pa_end = "walk"
    elif result == "Foul":
        if state.strikes < 2:
            state.strikes += 1
    elif result in FAIR_HITS or result in ("Out", "Error"):
        play = outcome["play"]
        if result in FAIR_HITS:
            extra = f" ({play['detail']})" if play["detail"] else ""
            on_event(f">>> They hit a {play['label'].lower()}!{extra}")
        elif result == "Error":
            extra = f" ({play['detail']})" if play["detail"] else ""
            on_event(f">>> An error by our fielder! He reaches base{extra}")
        else:
            note = play["label"] if play["label"] != "Out" else play["detail"]
            on_event(">>> Got him out!" + (f" ({note})" if note else ""))
        if play["runs"]:
            on_event(f">>> We gave up {play['runs']} run{'s' if play['runs'] != 1 else ''}...")
        state.spray_add(outcome["batted_ball"], result)
        state.apply_play(play)
        pa_end = "hit" if result in FAIR_HITS else ("error" if result == "Error" else "out")

    if pa_end and state.pitch_log:
        state.pitch_log[-1]["at_bat_end"] = pa_end


def log_pitch(state, outcome):
    """Record one pitch for the post-game analysis."""
    an = outcome["_analysis"]
    state.pitch_log.append({
        "pitch_number": len(state.pitch_log) + 1,
        "pitch_type": outcome["pitch_type"],
        "target_course": outcome["target_course"],
        "actual_course": outcome["actual_course"],
        "intent": outcome["intent"],
        "call_label": f"{outcome['pitch_type']}/{outcome['actual_course']}"
                      f"({intent_label(outcome['intent'])})",
        # Only for the review screen (shared by CLI/Web). Raw internal keys read badly,
        # like "sinker/mid_mid", so this swaps in the pitch name and the short
        # location label. call_label itself is a fixed format that stats.py parses with a
        # regular expression, so it is not changed.
        "call_label_text": f"{pitch_name(outcome['pitch_type'])}/{COURSE_SHORT[outcome['actual_course']]}"
                           f"({intent_label(outcome['intent'])})",
        "result": outcome["result"],
        "decision_quality": an["decision_quality"],
        "sequence_label": an["sequence_label"],
        "guess": an["guess"],
        "fooled_guess": an["fooled_guess"],
        "alignment_helped": an["alignment_helped"],
        "missed": outcome["missed"],
        "quality": round(outcome["quality"], 2),
        "framed": outcome.get("framed", False),
        "outcome_flags": {"swung": outcome["swung"], "in_zone": outcome["in_zone"]},
    })


def save_game_log(state):
    """Save the game log to logs/ as JSON (#E)."""
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", _dt.datetime.now().strftime("game_%Y%m%d_%H%M%S.json"))
    run_txt, status = state.result_summary()
    reads = grade_reads(state.reads, state.lineup.batters)
    with open(path, "w", encoding="utf-8") as fp:
        json.dump({
            "situation": {"inning": state.inning_label(),
                          "start": [state.start_our, state.start_opp]},
            "final": {"our": state.our_score, "opp": state.opp_score,
                      "runs_this_inning": state.runs_this_inning,
                      "result": run_txt, "status": status},
            "pitches": state.pitch_log,
            "spray": state.spray,
            "events": state.events,
            "reads": {"correct": reads["correct"], "graded": reads["graded"],
                      "rows": reads["rows"]},
        }, fp, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# The bridge for QTEs (rhythm games)
#
# Judging one pitch is done by a generator called resolve_pitch_events(). Wherever a
# catcher rhythm play is needed it yields ("kind", payload), and the caller sends back the answer:
#   frame        -> "perfect" / "good" / "miss" / None
#   anything else -> bonus (float, -0.25 to 0.25)
# via .send(). At the end it returns (outcome, messages).
#
#   CLI (main.py) / tests : resolve_one_pitch() calls qte.* right away to answer
#                           (a real rhythm input on a terminal, 0.0 / None when non-interactive)
#   Web (webapp.py)       : drives the generator across HTTP requests and returns
#                           the browser timing UI's result as the answer
# The judging logic itself is unchanged -- only how QTEs are called was made swappable by
# turning it into a yield.
# ---------------------------------------------------------------------------


def _cli_answer(kind, payload, rng, frame_timing):
    """For the CLI / tests: answer a yielded QTE by calling qte.* immediately."""
    if kind == "frame":
        return frame_timing                      # the explicit value if given, otherwise None = probability only
    if kind == "change_signs":
        return catcher_change_signs(rng)
    if kind == "steal_throw":
        return catcher_throw(rng)
    if kind == "d3_throw":
        return catcher_throw(rng, base="first", title="Got it! Throw to first")
    if kind in ("wild_block", "d3_block"):
        return catcher_block(payload["dir"], wild=payload["wild"], rng=rng)
    if kind == "field_bunt":
        return catcher_field_bunt(rng)
    return 0.0


def _drive(gen, answer_fn):
    """Run a generator to the end, answering each yield with answer_fn(kind, payload)."""
    answer = None
    try:
        while True:
            kind, payload = gen.send(answer)
            answer = answer_fn(kind, payload)
    except StopIteration as stop:
        return stop.value


def _sign_steal_events(state, rng):
    """The risk of the runner on second stealing the signs (any time during an at-bat). Returns messages."""
    messages = []
    if state.sign_steal_chance(rng):
        messages.append(">>> The runner on second seems to be peeking at the signs...")
        sign_bonus = yield ("change_signs", {})
        messages.append(f">>> {state.resolve_sign_steal(sign_bonus, rng)}")
    return messages


def maybe_resolve_sign_steal(state, rng):
    """Handle only the sign stealing on its own and return messages (a CLI helper, for tests)."""
    return _drive(_sign_steal_events(state, rng),
                  lambda k, p: _cli_answer(k, p, rng, None))


def resolve_one_pitch(state, pitch_type, course, intent, rng, frame_timing=None, tempo=0.0):
    """Judge one pitch, update the state, and return (outcome, list of messages).

    The QTEs for a dropped third strike, blocking, steals, and bunt fielding are real rhythm
    input on a terminal, and handled automatically by the probability model when non-interactive.
    Passing frame_timing fixes the receive timing (framing) to that value. tempo is how well
    the catcher's return-throw rhythm went (-0.25 to 0.25). The better it is, the more the pitcher gets going.
    """
    return _drive(resolve_pitch_events(state, pitch_type, course, intent, rng, tempo=tempo),
                  lambda k, p: _cli_answer(k, p, rng, frame_timing))


def resolve_pitch_flow(state, pitch_type, course, intent, rng, tempo=0.0):
    """For the Web: a generator that drives "stolen signs -> judging one pitch" as one continuous flow."""
    pre = yield from _sign_steal_events(state, rng)
    outcome, msgs = yield from resolve_pitch_events(state, pitch_type, course, intent, rng,
                                                   tempo=tempo)
    return outcome, pre + msgs


def _call_label(pitch_type, course, intent):
    return (f"{pitch_name(pitch_type)} / {COURSE_SHORT[course]}"
            f" / {intent_label(intent)}")


def resolve_pitch_events(state, pitch_type, course, intent, rng, tempo=0.0):
    """Judge one pitch plus the surrounding events (a generator). It yields at each QTE point."""
    messages = []

    # If the opponent is bunting, go to the bunt-specific resolution instead of the normal at-bat judging
    if state.opp_tactic == "bunt":
        return (yield from _resolve_bunt_events(state, pitch_type, course, intent, rng, messages))

    # A borderline pitch (aiming at a corner to get a strike) gets a receive timing before the pitch.
    # Passing velocity lets the Web side make a fast pitch received early (left) and a slow one late (right).
    frame_timing = None
    if is_corner(course) and intent in ("strike", "freeze", "weak_contact"):
        frame_timing = yield ("frame", {"call": _call_label(pitch_type, course, intent),
                                        "velocity": velocity_of(pitch_type)})

    # When good calls stack up, the pitcher gets going a little on the next pitch (lenient, up to +0.15).
    tempo += state.good_call_bonus()

    outcome = judge_pitch(state, pitch_type, course, rng, intent=intent,
                          frame_timing=frame_timing, tempo=tempo)
    outcome["reaction"] = describe_reaction(pitch_type, outcome["actual_course"], outcome, rng)

    log_pitch(state, outcome)

    # Update whether this pitch was a "good call" (the number is not shown; a line only when the streak builds).
    good_call = state.register_call_quality(outcome["_analysis"]["decision_quality"],
                                           outcome["result"])
    outcome["good_call"] = good_call
    outcome["good_call_streak"] = state.good_call_streak
    if good_call:
        s = state.good_call_streak
        if s == 2:
            messages.append(">>> Good calls are stacking up. Keep it going (call bonus)")
        elif s == 4:
            messages.append(">>> Total control. The pitcher is in a groove (call bonus)")
        elif s >= 6 and s % 3 == 0:
            messages.append(">>> Dominant game-calling. The battery owns this game (call bonus)")
    state.history.add(pitch_type, course, outcome["result"], outcome["timing"],
                      outcome["swung"], actual_course=outcome["actual_course"],
                      intent=intent, velocity=velocity_of(pitch_type),
                      family=family_of(pitch_type), in_zone=outcome["in_zone"])

    fam = family_of(pitch_type)
    result = outcome["result"]
    ac = outcome["actual_course"]

    # A strikeout in the dirt + a dropped third strike is possible (first base open or two outs) -> check the dropped third
    if (result == "Whiff" and state.strikes == 2 and outcome["missed"]
            and state.dropped_third_eligible()):
        low_dirt = zone_y(ac) == "lo" and not outcome["in_zone"]
        if low_dirt or rng.random() < 0.30:
            why = "first base is open" if not state.runners[0] else "two outs"
            messages.append(f">>> Strike three in the dirt! A chance for a dropped third strike ({why})")
            hard = is_corner(ac)
            block_bonus = yield ("d3_block", {"dir": zone_x(ac), "wild": not low_dirt})
            throw_bonus = yield ("d3_throw", {})
            _, msg = state.resolve_dropped_third(block_bonus, throw_bonus, rng, hard=hard)
            messages.append(f">>> {msg}")
            # Whether he gets on or is thrown out, it counts as a "strikeout" in the batter's line (the real baseball rule)
            if state.pitch_log:
                state.pitch_log[-1]["at_bat_end"] = "strikeout"
            return outcome, messages

    # Blocking (a direction button + timing). On a pitch that was not hit fair:
    #   - a low breaking ball (breaking / offspeed) is always a bit in the dirt -> block every time
    #   - other misses also happen sometimes if there is a runner (a low wild pitch always)
    low_break = (fam in ("breaking", "offspeed") and zone_y(ac) == "lo"
                 and result in ("Ball", "Whiff"))
    low_dirt = zone_y(ac) == "lo" and not outcome["in_zone"]
    wild_case = (any(state.runners) and outcome["missed"] and result in ("Ball", "Whiff")
                 and (low_dirt or rng.random() < 0.30))
    if low_break or wild_case:
        hard = is_corner(ac)
        block_bonus = yield ("wild_block", {"dir": zone_x(ac), "wild": not (low_break or low_dirt)})
        msg = state.resolve_block(block_bonus, rng, hard=hard)
        if msg:
            messages.append(f">>> {msg}")

    # Hit-and-run: even on a pitch the batter does not put in play, the runner on first is going
    if (state.opp_tactic == "hit_and_run" and state.runners[0]
            and result in ("Ball", "Strike", "Whiff", "Foul")):
        pitched_out = intent == "pitchout"
        tb = (yield ("steal_throw", {})) if pitched_out else 0.0
        ev = state.hit_and_run_runner_go(pitched_out, contact=False, rng=rng, throw_bonus=tb)
        if ev:
            prefix = ">>> You read the hit-and-run! " if pitched_out else ">>> "
            messages.append(prefix + ev)

    # On a pitch with no ball in play -> a runner may try to steal (the throw rhythm)
    elif result in ("Ball", "Strike", "Whiff") and state.steal_chance(rng):
        messages.append(">>> The runner is going!")
        throw_bonus = yield ("steal_throw", {})
        if intent == "pitchout":
            throw_bonus += 0.35                      # a pitchout is a pitch for throwing out the runner
            messages.append(">>> The pitchout is right on target!")
        ev = state.resolve_steal(fam, rng, throw_bonus=throw_bonus)
        if ev:
            messages.append(f">>> {ev}")

    if state.is_over():
        return outcome, messages

    apply_result(state, outcome, on_event=messages.append)
    return outcome, messages


def _resolve_bunt_events(state, pitch_type, course, intent, rng, messages):
    """Resolve the opponent's sacrifice bunt (a generator. It yields the bunt-fielding rhythm)."""
    actual = state.pitcher.execute_pitch(pitch_type, course, intent, rng)
    ac = actual["actual_course"]
    fam = family_of(pitch_type)
    pitched_out = intent == "pitchout"
    high_heat = fam == "fastball" and zone_y(ac) == "hi"
    charging = state.defense.alignment == "bunt"

    field_bonus = 0.0 if pitched_out else (yield ("field_bunt", {}))

    msgs, final, pa_end = state.resolve_bunt(pitched_out, high_heat, charging, rng,
                                            field_bonus=field_bonus)
    messages += [m if m.startswith(">>>") else f">>> {m}" for m in msgs]

    outcome = {
        "result": final, "timing": "on_time", "swung": not pitched_out, "in_zone": False,
        "pitch_type": pitch_type, "target_course": course, "actual_course": ac,
        "intent": intent, "missed": actual["missed"], "quality": round(actual["quality"], 2),
        "batted_ball": None, "fielding": None, "play": None, "reaction": None,
        "_analysis": dict(_NEUTRAL_ANALYSIS),
    }
    log_pitch(state, outcome)
    state.register_call_quality(outcome["_analysis"]["decision_quality"], final)
    outcome["good_call"] = False
    outcome["good_call_streak"] = state.good_call_streak
    state.history.add(pitch_type, course, final, "on_time", not pitched_out,
                      actual_course=ac, intent=intent, velocity=velocity_of(pitch_type),
                      family=fam, in_zone=False)
    if state.pitch_log and pa_end:
        state.pitch_log[-1]["at_bat_end"] = pa_end
    return outcome, messages
