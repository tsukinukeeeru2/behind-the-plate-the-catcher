"""Turns the catcher's "muscle memory" plays into rhythm / QTE mini-games.

Standard library only (time / sys / os). When the terminal is not interactive (piped) or
PITCHSIM_QTE=0, they are skipped automatically and the probability model takes over
-- so tests and demos never break.

Each mini-game returns "perfect" / "good" / "miss", and catcher_throw() / catcher_block()
roll that up into a "skill adjustment" from -0.25 to +0.25.
The adjustment never decides the result; it only shifts the odds of catching a stealer
or blocking a pitch.
"""

import os
import sys
import time

import ansi

_LABEL = {"perfect": ansi.green("PERFECT!"), "good": ansi.yellow("GOOD"), "miss": ansi.red("MISS")}
_BONUS = {"perfect": 0.20, "good": 0.05, "miss": -0.18}


def qte_enabled():
    flag = os.environ.get("PITCHSIM_QTE")
    if flag == "0":
        return False
    if flag == "1":
        return True
    return bool(getattr(sys.stdin, "isatty", lambda: False)()
               and getattr(sys.stdout, "isatty", lambda: False)())


def _read():
    try:
        return input()
    except (EOFError, KeyboardInterrupt):
        return None


def _grade(value, perfect, good):
    return "perfect" if value <= perfect else ("good" if value <= good else "miss")


_DIR_ORDER = ["in", "mid", "out"]
_DIR_KEY = {"1": "in", "2": "mid", "3": "out"}
_DIR_TEXT = {"in": "In (left)", "mid": "Middle (center)", "out": "Out (right)"}


# --- Pure scoring helpers the Web version uses too (no print, no input) ---
def bonus_from_verdicts(*verdicts):
    """Sum perfect/good/miss into a bonus between -0.25 and 0.25."""
    total = sum(_BONUS.get(v, 0.0) for v in verdicts)
    return max(-0.25, min(0.25, total))


def direction_verdict(picked, correct):
    """Guessing the block direction: same = perfect / next to it = good / opposite = miss."""
    if picked == correct:
        return "perfect"
    if (picked in _DIR_ORDER and correct in _DIR_ORDER
            and abs(_DIR_ORDER.index(picked) - _DIR_ORDER.index(correct)) == 1):
        return "good"
    return "miss"


# --- Direction (shift your body toward the side the ball in the dirt came from: 1=in 2=middle 3=out) ---
def direction_check(correct_dir):
    print("  " + ansi.bold("→ Which way do you go?  1=in  2=middle  3=out"))
    s = _read()
    picked = _DIR_KEY.get((s or "").strip()[:1]) if s is not None else None
    if picked == correct_dir:
        print(f"  {_DIR_TEXT[correct_dir]} … {_LABEL['perfect']}")
        return "perfect"
    if picked in _DIR_ORDER and abs(_DIR_ORDER.index(picked) - _DIR_ORDER.index(correct_dir)) == 1:
        print(f"  Half a step off ({_DIR_TEXT[picked]}) … {_LABEL['good']}")
        return "good"
    print(f"  Wrong way (it was {_DIR_TEXT[correct_dir]}) … {_LABEL['miss']}")
    return "miss"


# --- A single reaction (1: react the instant you notice) ---
def reaction_check(cue, perfect=0.42, good=0.9):
    print("  " + cue)
    print("  " + ansi.bold("→ Now! Enter!"))
    t0 = time.time()
    if _read() is None:
        return "miss"
    dt = time.time() - t0
    g = _grade(dt, perfect, good)
    print(f"  Reaction {dt * 1000:.0f}ms … {_LABEL[g]}")
    return g


# --- Mashing (2: get the transfer done fast) ---
def mash_check(cue, need=10, limit=2.4):
    print("  " + cue)
    print("  " + ansi.bold(f"→ Mash one key, then Enter ({need} hits within {limit:.0f}s)"))
    t0 = time.time()
    s = _read()
    if s is None:
        return "miss"
    dt = time.time() - t0
    n = len(s.strip())
    if n >= need and dt <= limit:
        g = "perfect"
    elif n >= int(need * 0.6):
        g = "good"
    else:
        g = "miss"
    print(f"  {n} hits / {dt:.1f}s … {_LABEL[g]}")
    return g


# --- Rhythm (3: the throw / the 1-2-3 block) ---
def rhythm_check(cue, beats=3, interval=0.55, perfect=0.35, good=0.75):
    print("  " + cue)
    print("  " + ansi.dim(f"{beats} beats at a steady rhythm. Hit Enter the moment a number appears."))
    time.sleep(0.6)
    latencies = []
    for i in range(1, beats + 1):
        time.sleep(interval)
        print("   " + ansi.bold(ansi.cyan(f"▶ {i}")), flush=True)
        t0 = time.time()
        if _read() is None:
            return "miss"
        latencies.append(time.time() - t0)
    good_beats = sum(1 for x in latencies if x <= good)
    avg = sum(latencies) / len(latencies)
    if good_beats == beats and avg <= perfect:
        g = "perfect"
    elif good_beats >= beats - 1:
        g = "good"
    else:
        g = "miss"
    print("  Rhythm " + "/".join(f"{x * 1000:.0f}" for x in latencies) + f"ms … {_LABEL[g]}")
    return g


# --- Combinations ---
def catcher_return_ball(rng=None):
    """Send the caught ball back to the pitcher on rhythm (no mashing, three beats).

    A steady tempo, neither rushed nor slow, gets the pitcher going.
    Returns: tempo_bonus (-0.25 to 0.25; bigger means a better pitcher tempo).
    """
    if not qte_enabled():
        return 0.0
    print(ansi.bold(ansi.cyan("\n♪ Return it to the pitcher on rhythm -- Enter on a steady 1-2-3")))
    g = rhythm_check("", beats=3, interval=0.6, perfect=0.32, good=0.72)
    return {"perfect": 0.22, "good": 0.06, "miss": -0.16}[g]


def catcher_throw(rng=None, base="second", title="He's going! Throw him out"):
    """The three stages of throwing out a runner (shared by the steal throw to second and the dropped-third throw to first).

    base  : where the throw goes, for display ("second" / "first")
    Returns: throw_bonus (-0.25 to 0.25; bigger means a runner is easier to nab).
    """
    if not qte_enabled():
        return 0.0
    print(ansi.bold(ansi.yellow(f"\n═══ {title} ═══")))
    g1 = reaction_check("① Notice the break", perfect=0.42, good=0.95)
    g2 = mash_check("② Transfer from the glove", need=10, limit=2.4)
    g3 = rhythm_check(f"③ Throw straight to {base}", beats=1, interval=0.5, perfect=0.35, good=0.7)
    total = _BONUS[g1] + _BONUS[g2] + _BONUS[g3]
    return max(-0.25, min(0.25, total))


def catcher_change_signs(rng=None):
    """Change the signs quickly when the runner on second is about to steal them.

    Returns: sign_bonus (-0.25 to 0.25; bigger means you protect the signs).
    """
    if not qte_enabled():
        return 0.0
    print(ansi.bold(ansi.yellow("\n═══ The runner on second is peeking at your signs! Change them ═══")))
    g1 = mash_check("① Flash your fingers fast to change the signs", need=8, limit=2.0)
    g2 = reaction_check("② Give the new signs", perfect=0.40, good=0.9)
    total = _BONUS[g1] + _BONUS[g2]
    return max(-0.25, min(0.25, total))


def catcher_field_bunt(rng=None):
    """A bunt: mask off and charge -> barehand it -> throw, in three stages.

    Returns: field_bonus (-0.25 to 0.25; bigger means the lead runner is easier to force and bunt hits are prevented).
    Like the steal throw and the block, it never decides the result; it only shifts the odds.
    """
    if not qte_enabled():
        return 0.0
    print(ansi.bold(ansi.yellow("\n═══ Bunt! Mask off, charge! ═══")))
    g1 = reaction_check("① Break the instant he bunts", perfect=0.40, good=0.9)
    g2 = mash_check("② Barehand it and transfer", need=8, limit=2.0)
    g3 = rhythm_check("③ Throw straight to the lead base", beats=1, interval=0.5, perfect=0.35, good=0.7)
    return max(-0.25, min(0.25, _BONUS[g1] + _BONUS[g2] + _BONUS[g3]))


def catcher_block(correct_dir="mid", wild=False, rng=None):
    """Block a ball in the dirt / a wild pitch with your body.

    correct_dir : the side the ball came from, "in" / "mid" / "out" (the zone_x of the actual location)
    wild        : True if it is a pitcher's wild pitch (a big miss that is not down in the dirt) -- only changes the text
    Returns: block_bonus (-0.25 to 0.25; bigger means fewer passed balls).
    """
    if not qte_enabled():
        return 0.0
    head = "Wild pitch! Come out and stop it" if wild else "One in the dirt! Block it with your body"
    print(ansi.bold(ansi.yellow(f"\n═══ {head} ═══")))
    gd = direction_check(correct_dir)
    gr = rhythm_check("Drop your body on a 1-2-3 rhythm", beats=3, interval=0.5)
    return max(-0.25, min(0.25, _BONUS[gd] + _BONUS[gr]))
