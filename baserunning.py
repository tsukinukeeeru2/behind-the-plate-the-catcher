"""Resolves the baserunning after a ball is hit fair.

judge.py calls it *without changing state* and gets a plan (a dict) back.
What actually updates the game situation is match_state.apply_play().

Return value:
    {
      "batter_result": "Out"/"Error"/"Single"/"Double"/"Triple"/"Home run",
      "outs_added":  int,   # outs added on this play, counting batter outs and double plays
      "runs":        int,   # runs scored on this play
      "new_runners": [bool, bool, bool],       # first, second, third after the play
      "new_runner_speeds": [int|None, ...],    # in the same order, the speed of the runner on each base
      "label":       str,   # for display ("Single", "Double play", "Sac fly", etc.)
      "detail":      str,   # extra ("Bases cleared", "Tag-up", etc.)
    }

Runner speed (runner_speeds) is around 20-90. A fast runner
  - is more likely to go from first all the way to third
  - is more likely to score from second on a single
  - is harder to double up (judge/steal use it too)
Not a physics simulation; a probability model you can read.
"""

import random


def _finish(result, outs_added, runs, new_runners, new_speeds, label, detail, outs_before):
    # If this play makes the third out, runs in that inning do not count (a simplification)
    if outs_before + outs_added >= 3:
        runs = 0
        detail = detail or "Side retired"
    return {
        "batter_result": result,
        "outs_added": outs_added,
        "runs": runs,
        "new_runners": list(new_runners),
        "new_runner_speeds": list(new_speeds),
        "label": label,
        "detail": detail,
    }


def _runs_text(n):
    return f"{n} run" if n == 1 else f"{n} runs"


def resolve(batted_ball, fielding, runners, outs, batter, rng=None, runner_speeds=None,
            runner_moving=False):
    """runner_moving=True means "on a hit-and-run the runner on first has already broken".
    There is no double play, and even on an infield grounder the runner always moves up one base (no force play)."""
    rng = rng or random
    result = fielding["result"]           # "Out" / "Error" / "Single" / "Double" / "Triple" / "Home run"
    r1, r2, r3 = runners
    speeds = list(runner_speeds) if runner_speeds else [None, None, None]
    s1, s2, s3 = speeds
    on = sum(1 for r in runners if r)
    to_outfield = batted_ball.ball_type in ("line", "fly")
    bsp = getattr(batter, "speed", 50)

    def spd(s):   # a runner's speed (average 50 if unknown) as an offset centered on 0.0
        return ((s if s is not None else 50) - 50) / 100.0

    # ---------------- Extra-base hits and singles ----------------
    if result == "Home run":
        runs = 1 + on
        detail = "Solo" if on == 0 else f"{runs}-run"
        return _finish("Home run", 0, runs, [False] * 3, [None] * 3, "Home run", detail, outs)

    if result == "Triple":
        return _finish("Triple", 0, on, [False, False, True], [None, None, bsp],
                       "Triple", "Runners score" if on else "", outs)

    if result == "Double":
        detail = "Bases cleared" if on >= 2 else ("1 run" if on == 1 else "")
        return _finish("Double", 0, on, [False, True, False], [None, bsp, None],
                       "Double", detail, outs)

    if result in ("Single", "Error"):
        new = [False, False, False]
        new_sp = [None, None, None]
        runs = 0
        if r3:
            runs += 1                                       # the runner on third scores
        if r2:
            # Runner on second: a ball to the outfield + speed move his scoring odds (base 0.55, up to +0.30 if fast)
            if to_outfield and rng.random() < 0.55 + spd(s2) * 0.6:
                runs += 1
            else:
                new[2], new_sp[2] = True, s2
        if r1 and not new[2]:
            # Runner on first: a ball to the outfield + speed gets him to third (base 0.30).
            # On a hit-and-run he has already broken, so he almost always reaches third.
            reach = 0.90 if runner_moving else (0.30 + spd(s1) * 0.6 if to_outfield else 0.0)
            if rng.random() < reach:
                new[2], new_sp[2] = True, s1
            else:
                new[1], new_sp[1] = True, s1
        elif r1:
            new[1], new_sp[1] = True, s1
        new[0], new_sp[0] = True, bsp                       # the batter reaches first
        detail = _runs_text(runs) if runs else ""
        return _finish(result, 0, runs, new, new_sp, result, detail, outs)

    # ---------------- Outs ----------------
    ball_type = batted_ball.ball_type
    infield = fielding["position"] in ("1B", "2B", "3B", "SS")

    # Double play: runner on first & fewer than 2 outs & an infield grounder (none on a hit-and-run, since the runner is going)
    if r1 and outs < 2 and ball_type == "ground" and infield and not runner_moving:
        dp_p = 0.55 - (bsp - 50) / 100 * 0.30    # the batter's legs
        dp_p -= spd(s1) * 0.40                   # the runner on first's legs (hard to get at second if he is fast)
        if batted_ball.hardness == "soft":
            dp_p -= 0.15                         # a dribbler is too slow to turn two
        elif batted_ball.hardness == "hard":
            dp_p -= 0.05                         # too hard can mean it gets through or is tough to handle
        if rng.random() < max(0.05, dp_p):
            return _finish("Out", 2, 0, [False, r2, r3], [None, s2, s3],
                           "Double play", "Turned two", outs)
        # Double play broken up: only the batter is out. The runner on first goes to second; if bases are full, the lead runner is forced home
        new = [False, True, bool(r2 or r3)]
        new_sp = [None, s1, s2 if r2 else (s3 if r3 else None)]
        runs = 1 if (r2 and r3) else 0
        return _finish("Out", 1, runs, new, new_sp, "Out", "Double play broken up", outs)

    # Sac fly: runner on third & fewer than 2 outs & a fly-ball out
    if fielding["air_out"] and r3 and outs < 2 and ball_type == "fly":
        deep = batted_ball.distance == "deep"
        if rng.random() < (0.85 if deep else 0.35) + spd(s3) * 0.4:
            new = [False, r2, False]
            new_sp = [None, s2, None]
            if deep and r2 and rng.random() < 0.40 + spd(s2) * 0.5:
                new, new_sp = [False, False, True], [None, None, s2]   # the runner on second tags up too
            return _finish("Out", 1, 1, new, new_sp, "Sac fly", "Tag-up", outs)

    # An ordinary out. On an infield grounder there is a "productive out" where runners move up one base
    # (on a hit-and-run the runners are going, so they almost always move up)
    new = list(runners)
    new_sp = list(speeds)
    runs = 0
    detail = ""
    advance_p = 0.90 if runner_moving else 0.45
    if ball_type == "ground" and outs < 2 and rng.random() < advance_p:
        if new[2]:
            runs += 1
        new = [False, bool(new[0]), bool(new[1])]
        new_sp = [None, speeds[0], speeds[1]]
        detail = "Productive out" + (" (hit and run)" if runner_moving else "")
    return _finish("Out", 1, runs, new, new_sp, "Out", detail, outs)
