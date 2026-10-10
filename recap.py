"""The one-pitch recap shown after every pitch: what happened (facts, from the judged data) and, separately, a tip.

Everything in "facts" is read straight from the pitch that was judged: where it was aimed, where it crossed, what the swing was, what the ball
did, how the catch went. Nothing is guessed about what a batter was thinking; a human batter's own "looking for" choice is only shown once
the pitch is over. "tips" are advice for the next call, each one resting on facts that are on the screen, and are kept apart from the facts.
The catch and the call are separate lines: a perfect call can still be dropped, and a bad call can still be caught cleanly.
"""

from constants import zone_x, zone_y
from pitch_data import family_of, guess_class_of, pitch_name, velocity_of

COURSE_WORDS = {
    "in_hi": "High-In", "mid_hi": "High", "out_hi": "High-Out", "in_mid": "In", "mid_mid": "Middle",
    "out_mid": "Out", "in_lo": "Low-In", "mid_lo": "Low", "out_lo": "Low-Out",
}
_CLASS_WORD = {"fastball": "a fastball", "offspeed": "off-speed"}


def _streak(log, key):
    """How many pitches in a row (ending with the last one in `log`) were the same pitch type."""
    n = 0
    for e in reversed(log):
        if e[key] != log[-1][key]:
            break
        n += 1
    return n


def build_recap(session, o, call, swing, catch):
    """o = the judged outcome, call = the catcher's call, swing = the swing as the page shows it (api.swing_info / human_swing_info),
    catch = the catcher's catch (session.last_catch) or None. Returns {"facts": [...], "tips": [...]}: each fact is {"k", "v", "tone"}."""
    state = session.state
    log = list(state.pitch_log) if state is not None else []
    facts, tips = [], []
    pt = o["pitch_type"]
    mph = velocity_of(pt)
    facts.append({"k": "PITCH", "v": "%s, %d mph, %s" % (pitch_name(pt), mph, call["intent_label"] if "intent_label" in call else call["intent"].replace("_", " ")), "tone": ""})
    aimed, went = COURSE_WORDS.get(o["target_course"], o["target_course"]), COURSE_WORDS.get(o["actual_course"], o["actual_course"])
    zone = "in the zone" if o.get("in_zone") else "off the plate"
    facts.append({"k": "LOCATION", "v": "Aimed %s, it crossed %s (%s)" % (aimed, went, zone), "tone": ""})

    # the swing
    human = o.get("human")
    if not o.get("swung"):
        facts.append({"k": "SWING", "v": "He took it", "tone": ""})
    elif human is not None and human.get("swung"):
        off = human["off"]
        ms = "%d ms %s" % (abs(round(off * 1000)), "early" if off < 0 else "late")
        how = {"miss": "no contact", "foul": "off the edge of the bat", "fair": "centred %d%%" % round(100 * human.get("centre", 0))}[human["contact"]]
        facts.append({"k": "SWING", "v": "%s: %s, %s" % (human["timing_label"], ms, how), "tone": human.get("grade", "")})
    else:                                                  # the game's own batter: only the judged category is real data
        word = {"early": "early", "late": "late", "on_time": "on time"}.get(o.get("timing"), "")
        facts.append({"k": "SWING", "v": "He swung %s" % word if word else "He swung", "tone": ""})
    if human is not None and human.get("guess"):
        g = human["guess"]
        looked = "either" if g == "either" else _CLASS_WORD.get(g, g)
        got = _CLASS_WORD[guess_class_of(pt)]
        fit = {"right": "he guessed right", "wrong": "he guessed wrong", "either": "ready for both"}.get(human.get("guess_fit"), "")
        facts.append({"k": "LOOKING FOR", "v": ("Either pitch" if g == "either" else "Looking for %s" % looked) + ", got %s (%s)" % (got, fit), "tone": ""})

    # the result
    res = o["result"]
    bb = o.get("batted_ball")
    if bb is not None and o.get("play"):
        facts.append({"k": "RESULT", "v": "%s: %s" % (o["play"]["label"], bb.describe().lower()), "tone": "bad" if res in ("Single", "Double", "Triple", "Home run", "Error") else "good"})
    else:
        facts.append({"k": "RESULT", "v": {"Strike": "Strike", "Ball": "Ball", "Whiff": "Swing and miss", "Foul": "Foul ball"}.get(res, res), "tone": ""})

    # how it fits the pitches before it (facts about the sequence)
    prev = log[-2] if len(log) >= 2 else None
    if prev is not None and log[-1].get("batter") == prev.get("batter"):
        dv = mph - velocity_of(prev["pitch_type"])
        run = _streak([e for e in log if e.get("batter") == log[-1].get("batter")], "pitch_type")
        bits = []
        if run >= 2:
            bits.append("%s %d times in a row" % (pitch_name(pt), run))
        elif prev["pitch_type"] != pt:
            bits.append("after %s" % pitch_name(prev["pitch_type"]))
        if abs(dv) >= 6:
            bits.append("%+d mph from the last pitch" % dv)
        if prev["actual_course"] == o["actual_course"]:
            bits.append("same spot as the last pitch")
        if bits:
            facts.append({"k": "SEQUENCE", "v": ", ".join(bits), "tone": ""})
    else:
        run = 1

    # the catch (separate from the call)
    if catch:
        q = catch.get("quality")
        text = {"clean": "Clean catch", "barely": "Barely caught", "miss": "Missed"}.get(q, q)
        if catch.get("dt_ms") is not None:
            text += " (%s%d ms)" % ("-" if catch["dt_ms"] < 0 else "+", abs(catch["dt_ms"]))
        if catch.get("stolen"):
            text += ", and it stole a strike"
        if catch.get("passed"):
            text += ", a passed ball"
        facts.append({"k": "CATCH", "v": text, "tone": {"clean": "good", "barely": "warn", "miss": "bad"}.get(q, "")})

    # tips: advice for the next call, never a claim about his mind; each rests on a fact above
    contact = res in ("Single", "Double", "Triple", "Home run", "Out", "Foul") and o.get("swung")
    if run >= 3 and contact:
        tips.append("%s %d times in a row and he is making contact. Change the speed or the spot." % (pitch_name(pt), run))
    elif res == "Whiff":
        tips.append("That look got a swing and miss. Showing it again right away is a risk: change the speed or the spot first.")
    elif res in ("Single", "Double", "Triple", "Home run") and zone_x(o["actual_course"]) == "mid" and zone_y(o["actual_course"]) == "mid":
        tips.append("It crossed the middle of the zone and was hit. Aim for a corner next time.")
    elif res == "Ball" and not o.get("in_zone") and o["intent"] in ("strike", "freeze", "weak_contact") and o["target_course"] != o["actual_course"]:
        tips.append("You wanted a strike but it ended up %s. Give the pitcher more room by aiming away from the edge." % went.lower())
    if human is not None and human.get("swung") and human.get("guess_fit") == "wrong" and human["contact"] != "miss" and len(tips) < 2:
        tips.append("He guessed wrong and still got wood on it: his window is only a little smaller, so the pitch still has to be good.")
    return {"facts": facts, "tips": tips[:2]}
