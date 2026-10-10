"""The JSON the 3D stadium page (stadium.py) reads: build_state(session) turns the shared game session into plain data.

This file only READS the game. The one thing worth a note: the hidden decision-quality number is never sent.
What the player gets after each pitch is a one-line plain-language evaluation (good / fair / risky) and its reason,
the same words the post-game review uses.
"""

import random
import re

import playview
from constants import COURSE_SHORT, zone_x, zone_y
from pitch_data import family_of, pitch_name, repertoire_options, velocity_of
import swing as swing_rules
from recap import build_recap
from strategy import PITCH_INTENTS, intent_label

# A rough break for each pitch: (drop, sweep). drop 0 = none .. 1 = big drop; sweep: - slides toward third base,
# + runs in toward first base (a guide for a right-handed pitcher). Shared by the classic page's little arrows and
# the 3D pitch paths.
PITCH_MOVE = {
    "four_seam": (0.10, 0.05), "two_seam": (0.30, 0.35), "sinker": (0.44, 0.42),
    "cutter": (0.24, -0.32), "slider": (0.50, -0.55), "sweeper": (0.52, -0.92),
    "curveball": (0.88, -0.30), "changeup": (0.58, 0.40), "splitter": (0.80, 0.10),
}

# How forgiving the catch is, by difficulty. The catch is one press of Space, judged by timing alone (seconds from the moment the
# ball reaches the mitt). [perfect, good] limits: inside "perfect" is a clean catch, inside "good" is a catch (barely), the rest is
# a miss (too early or too late). Not pressing at all is a miss.
CATCH_RULES = {
    "beginner": {"time": [0.16, 0.32]},
    "normal": {"time": [0.12, 0.24]},
    "expert": {"time": [0.06, 0.13]},
}
_BLOCK_KINDS = ("wild_block", "d3_block", "d3_throw", "steal_throw", "field_bunt")
_TIMING_LABEL = {"early": "EARLY", "on_time": "ON TIME", "late": "LATE"}


def swing_info(seq, result, timing, swung):
    """Where and when the bat went, for the pitch-by-pitch read. It comes straight from the judged swing: the timing the engine
    decided (early / on time / late) and whether it was a miss, a foul or contact. Only the small vertical wobble is seeded
    (by the pitch number) so the same pitch always draws the same picture.

    off    : seconds the bat was early (-) or late (+) at the plate
    ahead  : metres the bat's sweet spot was ahead of (+) or behind (-) the ball along the swing
    dy     : metres the bat was above (+) or below (-) the ball
    """
    if not swung:
        return {"swung": False, "timing": None, "label": "TOOK IT", "off": 0.0, "ahead": 0.0, "dy": 0.0, "vert": ""}
    rnd = random.Random(seq * 7919 + 3)
    sign = {"early": -1.0, "late": 1.0}.get(timing, rnd.choice([-1.0, 1.0]) if result in ("Whiff", "Foul") else 0.0)
    if result == "Whiff":
        off = sign * (rnd.uniform(0.07, 0.11) if timing in ("early", "late") else rnd.uniform(0.015, 0.03))
        dy = rnd.choice([-1.0, 1.0]) * (rnd.uniform(0.0, 0.05) if timing in ("early", "late") else rnd.uniform(0.08, 0.15))
    elif result == "Foul":
        off = sign * (0.04 if timing in ("early", "late") else 0.015)
        dy = rnd.choice([-1.0, 1.0]) * rnd.uniform(0.02, 0.06)
    else:
        off = sign * (0.03 if timing in ("early", "late") else 0.0)
        dy = rnd.choice([-1.0, 1.0]) * rnd.uniform(0.0, 0.025)
    vert = "OVER THE TOP" if dy > 0.07 else ("UNDER IT" if dy < -0.07 else "")
    return {"swung": True, "timing": timing, "label": _TIMING_LABEL.get(timing, "ON TIME"), "off": round(off, 3),
            "ahead": round(-off * 3.2, 3), "dy": round(dy, 3), "vert": vert}


def human_swing_info(h):
    """The swing of a human batter (two-player mode), as the page draws it: the numbers are the ones he really produced."""
    if not h or not h.get("swung"):
        return {"swung": False, "timing": None, "label": "TOOK IT", "off": 0.0, "ahead": 0.0, "dy": 0.0, "vert": "", "human": True}
    dy = h["dy"]
    vert = "OVER THE TOP" if dy > 0.07 else ("UNDER IT" if dy < -0.07 else "")
    return {"swung": True, "timing": h["timing"], "label": h["timing_label"], "off": h["off"], "ahead": round(-h["off"] * 3.2, 3),
            "dy": dy, "dx": h["dx"], "vert": vert, "human": True, "centre": h["centre"], "mode": h["mode"], "grade": h.get("grade"), "guess_fit": h.get("guess_fit"),
            "contact": h["contact"], "pos": swing_rules.position_words(h["dx"], dy)}


def batter_reads(state, limit=6):
    """Short facts about the current batter from what has actually been observed (never a claim about his plan).

    Looks at the last few pitches thrown to him this game. A fact is only stated when there are at least two observations behind it.
    Returns (facts, the pitches used).
    """
    name = state.batter.name
    used = [e for e in reversed(state.pitch_log) if e.get("batter") == name][:limit]
    used.reverse()
    swings = [e for e in used if e["outcome_flags"]["swung"]]
    facts = []
    late = [e for e in swings if e.get("timing") == "late"]
    early = [e for e in swings if e.get("timing") == "early"]

    def families(es):
        return {family_of(e["pitch_type"]) for e in es}
    if len(late) >= 2:
        fam = families(late)
        tail = " (all against fastballs)" if fam == {"fastball"} else (" (all against off-speed)" if "fastball" not in fam else "")
        facts.append(f"Late on {len(late)} of his last {len(swings)} swings{tail}")
    if len(early) >= 2:
        fam = families(early)
        tail = " (all against off-speed)" if "fastball" not in fam else ""
        facts.append(f"Early on {len(early)} of his last {len(swings)} swings{tail}")
    missed = [e for e in swings if e["result"] in ("Whiff", "Foul")]
    for key, label, pick in (("lo", "low", lambda c: zone_y(c) == "lo"), ("hi", "high", lambda c: zone_y(c) == "hi"),
                             ("in", "inside", lambda c: zone_x(c) == "in"), ("out", "away", lambda c: zone_x(c) == "out")):
        hits = [e for e in missed if pick(e["actual_course"])]
        if len(hits) >= 2:
            facts.append(f"Missing or fouling off pitches {label} ({len(hits)})")
            break
    chased = [e for e in swings if not e["outcome_flags"]["in_zone"]]
    if len(chased) >= 2:
        facts.append(f"Swung at {len(chased)} pitches off the plate")
    took = [e for e in used if not e["outcome_flags"]["swung"] and e["result"] == "Strike"]
    if len(took) >= 2:
        facts.append(f"Took {len(took)} strikes without swinging")
    return facts[:3], used

_TAGS = re.compile(r"<[^>]+>")
GOOD_CALL, FAIR_CALL, RISKY_CALL = "good", "fair", "risky"


def _plain(html_text):
    return _TAGS.sub("", html_text).replace("&nbsp;", " ")


def evaluate_call(outcome):
    """The evaluation shown right after a pitch: ("good" | "fair" | "risky", short title, reason)."""
    an = outcome.get("_analysis") or {}
    dq = an.get("decision_quality", 0.0)
    reasons = []
    label = an.get("sequence_label")
    if label and label != "Nothing notable":
        reasons.append(label.split(" / ")[0])          # the most important note only; the post-game review has them all
    if an.get("fooled_guess"):
        reasons.append("He was sitting on something else")
    if an.get("alignment_helped"):
        reasons.append("Your defensive shift paid off")
    if outcome.get("framed"):
        reasons.append("You framed it into a strike")
    if dq >= 0.25:
        grade, title = GOOD_CALL, "Good call"
    elif dq <= -0.25:
        grade, title = RISKY_CALL, "Risky call"
    else:
        grade, title = FAIR_CALL, "Fair call"
    if not reasons:
        reasons.append({GOOD_CALL: "It fit the count and the batter",
                        FAIR_CALL: "Nothing special either way",
                        RISKY_CALL: "It was easy to read for this count"}[grade])
    return grade, title, " · ".join(reasons[:2])


def _duel_reason(o):
    """Two-player mode: the reason line states only what happened (the human's swing and where the pitch went), never his thoughts."""
    h = o.get("human") or {}
    if o.get("framed"):
        return "You framed it into a strike"
    if not h.get("swung"):
        return "He took it" + (" (in the zone)" if o.get("in_zone") else " (off the plate)")
    return {"miss": "He swung and missed", "foul": "He got a piece of it", "fair": "He put it in play"}[h["contact"]]


def _batter_json(b):
    return {"name": b.name, "bats": b.bats, "type": b.type_label(), "avg": b.avg, "obp": b.obp, "slg": b.slg,
            "ops": b.ops, "scouting": b.scouting_note()}


def is_catchable(session):
    """True while the pitch ended in the mitt, no other catching game covered it, and the catch has not been made yet."""
    return bool(session.state.can_apply_catch() and session.last_catch is None
                and not any(k in _BLOCK_KINDS for k in session.pitch_qtes))


def _outcome_json(session):
    o, call = session.last_outcome, session.last_call
    if not o or not call:
        return None
    bb = o.get("batted_ball")
    play = o.get("play")
    fielding = o.get("fielding")
    grade, title, reason = evaluate_call(o)
    if o.get("human") is not None:
        reason = _duel_reason(o)
    out = {
        "seq": session.event_seq,
        "pitch_type": call["pitch_type"], "pitch_name": pitch_name(call["pitch_type"]),
        "target_course": call["target_course"], "intent": call["intent"],
        "intent_label": intent_label(call["intent"]),
        "actual_course": o["actual_course"], "result": o["result"], "missed": o["missed"],
        "quality": o["quality"], "swung": bool(o.get("swung")), "timing": o.get("timing"),
        "in_zone": bool(o.get("in_zone")), "framed": bool(o.get("framed")),
        "at_bat_end": o.get("at_bat_end"), "good_call": bool(o.get("good_call")),
        "eval": {"grade": grade, "title": title, "reason": reason},
        "batted": None,
        "swing": (human_swing_info(o["human"]) if o.get("human") is not None
                  else swing_info(session.event_seq, o["result"], o.get("timing"), bool(o.get("swung")))),
        "duel": o.get("human") is not None,
        "qtes": list(session.pitch_qtes),
        "catch": session.last_catch,
        "guess": ({"choice": o["human"].get("guess"), "fit": o["human"].get("guess_fit")} if o.get("human") else None),
    }
    out["recap"] = build_recap(session, o, dict(call, intent_label=out["intent_label"]), out["swing"], session.last_catch)
    # the mitt game applies when the pitch ended in the mitt and no other catching game (a block, a throw) already covered it
    out["catchable"] = is_catchable(session)
    if bb is not None and fielding and play:
        key = len(session.state.pitch_log)
        out["batted"] = {
            "describe": bb.describe(), "ball_type": bb.ball_type, "hardness": bb.hardness,
            "position": fielding["position"], "fielder": fielding["fielder"],
            "label": play["label"], "detail": play["detail"], "runs": play["runs"],
            "script": playview.build_play_script(bb, fielding, play, seed_key=key),
        }
    return out


def _qte_json(session, specs):
    if session.pending_qte is None:
        return None
    kind, payload = session.pending_qte
    spec = specs[kind]
    targets = list(spec.get("targets", []))
    if kind == "frame":
        v = payload.get("velocity", 90)
        targets = [0.24 if v >= 92 else (0.74 if v <= 84 else 0.5)]
    return {"kind": kind, "title": spec["title"], "jp": spec.get("jp", ""), "hint": _plain(spec["hint"]),
            "labels": spec["labels"], "direction": bool(spec["direction"]), "mash": spec.get("mash", []),
            "targets": targets, "call": payload.get("call", "")}


def build_state(session, qte_specs):
    """Everything the 3D page needs to draw one frame of the game and its HUD."""
    st = session.state
    base = {"seq": session.event_seq, "difficulty": session.difficulty, "game_seed": session.game_seed,
            "scenario": session.scenario, "started": bool(session.started)}
    if not session.started or st is None:
        return dict(base, phase="start")

    if session.pending_qte is not None:
        phase = "qte"
    elif st.is_over():
        phase = "over"
    elif session.showing_intro:
        phase = "intro"
    else:
        phase = "play"

    lu = st.lineup
    reads_facts, reads_used = batter_reads(st)
    nxt = lu.batters[(lu.index + 1) % 9]
    repertoire = [{"key": k, "name": name, "velocity": velocity_of(k), "family": family_of(k),
                   "move": list(PITCH_MOVE.get(k, (0.35, 0.0)))}
                  for k, name in repertoire_options(st.pitcher).items()]
    reaction = session.reaction or None
    run_txt, status = st.result_summary() if phase == "over" else ("", "")
    history = [{"n": e["pitch_number"], "pitch": pitch_name(e["pitch_type"]),
                "course": COURSE_SHORT[e["actual_course"]], "intent": intent_label(e["intent"]),
                "result": e["result"], "framed": bool(e.get("framed"))} for e in st.pitch_log[-8:][::-1]]
    plan = None
    if getattr(session, "plan", None):
        pl = session.plan["prepared"]
        pt = session.plan["pitch_type"]
        plan = {"seq": session.plan["seq"], "pitch_type": pt, "velocity": velocity_of(pt), "family": family_of(pt),
                "move": list(PITCH_MOVE.get(pt, (0.35, 0.0))),            # what the ball does in the air (the page needs it to fly the same pitch on both screens)
                "actual_course": pl["actual"]["actual_course"], "in_zone": bool(pl["in_zone"]),
                "missed": bool(pl["actual"]["missed"])}
    return dict(base, phase=phase, mode=getattr(session, "mode", "solo"), plan=plan,
                situation={
                    "inning": st.inning_label(), "our": st.our_score, "opp": st.opp_score,
                    "start_our": st.start_our, "start_opp": st.start_opp,
                    "outs": st.outs, "balls": st.balls, "strikes": st.strikes,
                    "runners": list(st.runners), "runners_text": st.runners_text(),
                    "fast_runner": bool(st.lead_runner_fast() and not st.runners[1]),
                },
                batter=dict(_batter_json(lu.current()), spot=lu.spot_number(),
                            next={"name": nxt.name, "bats": nxt.bats, "ops": nxt.ops}),
                pitcher={"throws": st.pitcher.throws, "pitches": st.pitcher.pitches_thrown,
                         "fatigue": st.pitcher.fatigue_level()},
                repertoire=repertoire,
                intents=[{"key": k, "label": intent_label(k), "desc": v} for k, v in PITCH_INTENTS.items()],
                reaction=({"face": reaction.get("face", ""), "text": reaction.get("text", ""),
                           "category": reaction.get("category", "neutral")} if reaction else None),
                messages=list(session.messages),
                last=_outcome_json(session),
                history=history,
                qte=_qte_json(session, qte_specs),
                over={"run_txt": run_txt, "status": status},
                alignment=st.defense.alignment,
                reads=reads_facts,
                pitch_map=[{"n": e["pitch_number"], "pitch": pitch_name(e["pitch_type"]), "course": e["actual_course"], "result": e["result"],
                            "swung": e["outcome_flags"]["swung"]} for e in reads_used],
                catch_stats=dict(st.catch_stats),
                catch_rules=CATCH_RULES.get(session.difficulty, CATCH_RULES["normal"]),
                can_walk=not all(st.runners),
                can_change=st.can_change_pitcher())
