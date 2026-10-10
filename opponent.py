"""The opposing bench's small-ball tactics: sacrifice bunt / hit-and-run.

decide_tactic() is called exactly once before the first pitch of each at-bat. It returns:
    None            ... the batter just hits away
    "bunt"          ... a sacrifice bunt. The batter shows bunt = the catcher can see it
    "hit_and_run"   ... hit-and-run. The runner breaks and the batter must swing = the catcher cannot see it

The catcher can counter with a pitchout, a high fastball (hard to bunt), and so on --
engine.py and match_state.py resolve that guessing game.

The game is always a close late-inning situation, so this only decides whether the
opponent "plays it by the book" given the situation (score, outs, runners) and the
batter type. To keep the opponent from getting too strong, probabilities are kept
modest (35% at most).
"""


def _weak_hitter(batter):
    return (batter.coarse_type in ("weak", "patient")
            or getattr(batter, "power", 0.5) < 0.42)


def _contact_hitter(batter):
    return (getattr(batter, "contact", 0.5) >= 0.60
            and getattr(batter, "whiff_rate", 0.24) <= 0.22)


def decide_tactic(state, rng):
    """Return the tactic the opposing bench calls (None / "bunt" / "hit_and_run")."""
    if state.outs >= 2:
        return None
    r1, r2, r3 = state.runners
    forceable = r1 or r2                     # there is a runner who can be moved over
    if not forceable:
        return None

    # Score difference from our (the fielding team's) point of view. Within 2 runs = "go for one run".
    close = abs(state.score_diff) <= 2
    one_run_game = -2 <= state.score_diff <= 1   # tied to 1 behind (we are fielding) = opponent plays it safe

    batter = state.batter

    # --- Sacrifice: weak hitter + runner on 1st (or 1st & 2nd) + close game ---
    if _weak_hitter(batter) and r1 and not r3 and close:
        p = 0.35 if one_run_game else 0.18
        if rng.random() < p:
            return "bunt"

    # --- Hit-and-run: contact hitter + runner on 1st only + close game ---
    if _contact_hitter(batter) and r1 and not r2 and not r3 and close:
        if rng.random() < 0.20:
            return "hit_and_run"

    return None


def describe(tactic):
    return {
        "bunt": "The batter squares to bunt",
        "hit_and_run": "",   # the catcher cannot see a hit-and-run
    }.get(tactic, "")
