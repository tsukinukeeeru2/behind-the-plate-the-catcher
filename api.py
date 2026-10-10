"""The JSON the 3D stadium page (stadium.py) reads: build_state(session) turns the shared game session into plain data.

This file only READS the game. The one thing worth a note: the hidden decision-quality number is never sent.
What the player gets after each pitch is a one-line plain-language evaluation (good / fair / risky) and its reason,
the same words the post-game review uses.
"""

import re

import playview
from constants import COURSE_SHORT
from pitch_data import family_of, pitch_name, repertoire_options, velocity_of
from strategy import PITCH_INTENTS, intent_label

# A rough break for each pitch: (drop, sweep). drop 0 = none .. 1 = big drop; sweep: - slides toward third base,
# + runs in toward first base (a guide for a right-handed pitcher). Shared by the classic page's little arrows and
# the 3D pitch paths.
PITCH_MOVE = {
    "four_seam": (0.10, 0.05), "two_seam": (0.30, 0.35), "sinker": (0.44, 0.42),
    "cutter": (0.24, -0.32), "slider": (0.50, -0.55), "sweeper": (0.52, -0.92),
    "curveball": (0.88, -0.30), "changeup": (0.58, 0.40), "splitter": (0.80, 0.10),
}

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


def _batter_json(b):
    return {"name": b.name, "bats": b.bats, "type": b.type_label(), "avg": b.avg, "obp": b.obp, "slg": b.slg,
            "ops": b.ops, "scouting": b.scouting_note()}


def _outcome_json(session):
    o, call = session.last_outcome, session.last_call
    if not o or not call:
        return None
    bb = o.get("batted_ball")
    play = o.get("play")
    fielding = o.get("fielding")
    grade, title, reason = evaluate_call(o)
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
    }
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
    nxt = lu.batters[(lu.index + 1) % 9]
    repertoire = [{"key": k, "name": name, "velocity": velocity_of(k), "family": family_of(k),
                   "move": list(PITCH_MOVE.get(k, (0.35, 0.0)))}
                  for k, name in repertoire_options(st.pitcher).items()]
    reaction = session.reaction or None
    run_txt, status = st.result_summary() if phase == "over" else ("", "")
    history = [{"n": e["pitch_number"], "pitch": pitch_name(e["pitch_type"]),
                "course": COURSE_SHORT[e["actual_course"]], "intent": intent_label(e["intent"]),
                "result": e["result"], "framed": bool(e.get("framed"))} for e in st.pitch_log[-8:][::-1]]
    return dict(base, phase=phase,
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
                can_walk=not all(st.runners),
                can_change=st.can_change_pitcher())
