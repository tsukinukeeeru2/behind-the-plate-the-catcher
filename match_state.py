"""The game situation (MatchState) and generate_random_situation(), which builds a late-inning scene.

One play of this game = "get three outs in one late-inning half inning".

MatchState holds exactly the attributes judge.py expects:
  batter / pitcher / defense / history / balls / strikes / outs / runners / score_diff
(judge.py never imports MatchState directly).
"""

import random

from constants import zone_x, zone_y
from defense import make_default_defense
from lineup import Lineup, build_sample_lineup
from pitch_history import PitchHistory
from pitcher import build_sample_pitcher

INNING_CHOICES = [7, 8, 9, 10]          # 10 = extra innings
INNING_WEIGHTS = [3, 3, 3, 1]
DIFF_CHOICES = [-3, -2, -1, 0, 1, 2, 3]  # our score - their score
DIFF_WEIGHTS = [1, 2, 4, 5, 4, 2, 1]     # close games (-1/0/+1) come up most
OUT_CHOICES = [0, 1, 2]
OUT_WEIGHTS = [5, 3, 2]


class MatchState:
    def __init__(self, inning, is_extra, half, we_are, our_score, opp_score,
                 outs, runners, lineup, pitcher, defense, runner_speeds=None):
        self.inning = inning
        self.is_extra = is_extra
        self.half = half                 # "top" / "bottom"
        self.we_are = we_are             # the fielding side is us. "HOME" / "AWAY"
        self.our_score = our_score
        self.opp_score = opp_score
        self.start_our = our_score        # the starting score (kept for the final verdict)
        self.start_opp = opp_score
        self.outs = outs
        self.runners = list(runners)
        # Each runner's speed (about 20-90; None on an empty base).
        # It affects how often runners steal, how well they succeed, first-to-third on a single,
        # and how easy a double play is.
        self.runner_speeds = list(runner_speeds) if runner_speeds else [None, None, None]
        self.balls = 0
        self.strikes = 0
        self.lineup = lineup
        self.pitcher = pitcher
        self.defense = defense
        self.history = PitchHistory()
        self.runs_this_inning = 0
        self.pitch_log = []              # one dict per pitch. Used for the post-game analysis
        self.spray = []                  # a record of fair balls (#B spray chart)
        self.reads = {}                  # catcher's notes id(batter) -> {"spot","wait","weak"}
        self.bullpen = []                # relievers available (several; you choose who comes in, not first-in-line)
        self.pitching_changes = 0
        self.events = []                 # a log of mid-at-bat events such as steals
        self.sign_leak = 0.0             # for one pitch after the signs are stolen, strengthens the batter's guess (#B)
        self.batter_pitch_memory = {}    # id(batter) -> {"fastball","offspeed"}. Memory across at-bats
        self.batters_faced = 1           # for the Postgame Report. The first batter counts as one
        self.opp_tactic = None           # the opposing bench's tactic: None / "bunt" / "hit_and_run" (#C small ball)
        self.tactic_decided = False       # whether this at-bat's tactic is decided (makes begin_at_bat idempotent)
        self.walkoffs = True              # False in an online match (both halves start 0-0 and only count the runs)
        self.run_cap = None               # an online match ends a half at this many runs (keeps it short); None = three outs only
        self.framed_strikes = 0          # how many times framing made a "ball" look like a "strike" (#catcher's craft)
        self.good_call_streak = 0        # how many "good calls" in a row (a lenient test; the number itself is never shown)
        self.catch_stats = {"clean": 0, "barely": 0, "miss": 0}   # the catcher's own mitt work (the 3D page's catch game)
        self.catch_applied_for = 0       # the pitch number whose catch has already been applied (a repeat is ignored)

    # ---- Used by judge.py ----
    @property
    def batter(self):
        return self.lineup.current()

    # ---- The catcher's notes (#B) ----
    def record_read(self, wait=None, weak=None):
        note = self.reads.setdefault(id(self.batter), {"spot": self.lineup.spot_number(),
                                                       "wait": None, "weak": None})
        if wait is not None:
            note["wait"] = wait
        if weak is not None:
            note["weak"] = weak

    def current_read(self):
        return self.reads.get(id(self.batter))

    # ---- The "good call" bonus (#praise). The threshold is lenient. ----
    # The post-game analysis counts a good call at decision_quality >= 0.25, but the
    # in-game reward is looser (>= GOOD_CALL_DQ). The number itself is NEVER shown on screen.
    GOOD_CALL_DQ = 0.10
    _BAD_CALL_RESULTS = ("Single", "Double", "Triple", "Home run", "Walk", "Error")

    def good_call_bonus(self):
        """When good calls stack up two or more in a row, the pitcher gets going a little on the next pitch (up to +0.15)."""
        if self.good_call_streak >= 2:
            return min(0.15, 0.05 * self.good_call_streak)
        return 0.0

    def register_call_quality(self, decision_quality, result):
        """Update the good-call streak on every pitch and return whether this pitch was a good one."""
        good = (decision_quality >= self.GOOD_CALL_DQ
                and result not in self._BAD_CALL_RESULTS)
        self.good_call_streak = self.good_call_streak + 1 if good else 0
        return good

    def spray_add(self, batted_ball, result):
        self.spray.append({
            "direction": batted_ball.direction,
            "ball_type": batted_ball.ball_type,
            "distance": batted_ball.distance,
            "bats": self.batter.bats,
            "result": result,
        })

    # ---- Pitching changes (#game feel) ----
    def can_change_pitcher(self):
        return bool(self.bullpen)

    def change_pitcher(self, index=0):
        """Bring in a reliever (bullpen[index]). You can change as many times as there are arms in the bullpen.

        A pitcher who leaves does not come back in this game (like the real rules), so the
        old pitcher is discarded. The pitch count resets, and the new pitcher's pitches and
        form are unknown until he throws (#D). Returns the new pitcher (None if no change was possible).
        """
        if not self.can_change_pitcher() or not (0 <= index < len(self.bullpen)):
            return None
        self.pitcher = self.bullpen.pop(index)
        self.pitching_changes += 1
        return self.pitcher

    # ---- Intentional walk (#game feel) ----
    def intentional_walk(self):
        """Issue an intentional walk (with a bases-loaded forced run). On to the next batter."""
        self.events.append("Intentional walk")
        self.advance_on_walk()

    # ---- Runner speed (#runner personalities) ----
    def runner_speed_at(self, base):
        """The speed of the runner on that base. 50 (average) if empty / unknown."""
        s = self.runner_speeds[base] if 0 <= base < 3 else None
        return 50 if s is None else s

    def lead_runner_fast(self):
        """Whether the runner on first is fast enough to be a "stolen-base threat" (a cue for the catcher to be ready)."""
        return bool(self.runners[0]) and self.runner_speed_at(0) >= 70

    # ---- Stolen bases (#game feel, a light model) ----
    def steal_chance(self, rng):
        """Whether the runner goes on this pitch (runner on first only, second empty, fewer than 2 outs).

        The faster the runner, the more often he goes: speed 50 -> about 12% / 80 -> about 24% / 35 -> about 6%.
        """
        if not (self.runners[0] and not self.runners[1]) or self.outs >= 2:
            return False
        p = max(0.02, 0.12 + (self.runner_speed_at(0) - 50) / 250)
        return rng.random() < p

    def resolve_steal(self, pitch_family, rng, throw_bonus=0.0):
        """Decide whether the steal works (call it when steal_chance was True).

        safe_p (chance he is safe) = 0.56 + a runner-speed adjustment ((speed-50)/240)
          - on a fastball the catcher can nab him more easily (-0.12) / on a breaking ball or one in the dirt he cannot get there (+0.10)
          - throw_bonus is how well the catcher throws (the QTE result). Bigger = easier to nab him.
        Returns an event string or None. State is changed here.
        """
        if not (self.runners[0] and not self.runners[1]) or self.outs >= 2:
            return None
        safe_p = 0.56 + (self.runner_speed_at(0) - 50) / 240
        safe_p += -0.12 if pitch_family == "fastball" else 0.10
        safe_p -= throw_bonus
        if rng.random() < max(0.05, min(0.95, safe_p)):
            self.runners = [False, True, self.runners[2]]
            self.runner_speeds = [None, self.runner_speeds[0], self.runner_speeds[2]]
            self.events.append("Stolen base")
            return "Stolen base (runner to second)"
        self.runners[0] = False
        self.runner_speeds[0] = None
        self.outs += 1                               # the batter stays. The runner is out
        self.events.append("Caught stealing")
        return "Caught stealing!"

    # ---- Blocking a ball in the dirt / a wild pitch (#game feel) ----
    def resolve_block(self, block_bonus, rng, hard=False):
        """Whether you stopped it. If you miss, the runners move up one base (a passed ball).

        pass_p (chance of a passed ball) = 0.45 - block_bonus (+ 0.12 if hard)
          block_bonus is how well the catcher blocks (the QTE result). Bigger = more stops.
          hard is for pitches that make you move a lot, like "low into a corner".
        Returns an event string or None.
        """
        if not any(self.runners):
            return None
        pass_p = max(0.03, min(0.9, 0.45 - block_bonus + (0.12 if hard else 0.0)))
        if rng.random() >= pass_p:
            return None                              # blocked it
        if self.runners[2]:
            self._score_runs(1)
        self.runners = [False, self.runners[0], self.runners[1]]
        self.runner_speeds = [None, self.runner_speeds[0], self.runner_speeds[1]]
        self.events.append("Passed ball")
        return "Passed ball! The runners move up"

    # ---- The catcher's own catch (the mitt game on the 3D page) ----
    CATCHABLE = ("Ball", "Strike", "Whiff")

    def can_apply_catch(self):
        """True if the last pitch ended in the mitt (not hit, not fouled) and its catch has not been applied yet."""
        if not self.pitch_log or self.catch_applied_for >= len(self.pitch_log):
            return False
        return self.pitch_log[-1]["result"] in self.CATCHABLE

    def apply_catch(self, quality, rng):
        """Apply how the catcher caught the last pitch: "clean" / "barely" / "miss". Returns a dict describing what changed.

        - miss: the ball gets away. With runners on, it is a passed ball and they move up a base.
        - clean: a ball that only just missed the zone can be framed into a strike (never a strikeout).
        - barely: you held on. Nothing changes.
        The count of clean / barely / miss catches is kept for the post-game review.
        This is deliberately separate from the quality of the pitch call (decision quality): a great call can still be dropped.
        """
        out = {"passed": False, "stolen": False, "message": None}
        if quality not in self.catch_stats or not self.can_apply_catch():
            return out
        self.catch_applied_for = len(self.pitch_log)
        self.catch_stats[quality] += 1
        entry = self.pitch_log[-1]
        if quality == "miss":
            if any(self.runners):
                msg = self.resolve_block(-1.0, rng)             # a passed ball: the runners move up
                out["passed"], out["message"] = bool(msg), msg
            else:
                out["message"] = "Dropped it, but nobody was on"
        elif quality == "clean" and entry["result"] == "Ball" and not entry.get("at_bat_end"):
            flags = entry.get("outcome_flags", {})
            edge = zone_x(entry["actual_course"]) != "mid" or zone_y(entry["actual_course"]) != "mid"
            both_edge = zone_x(entry["actual_course"]) != "mid" and zone_y(entry["actual_course"]) != "mid"
            if (not flags.get("swung") and not flags.get("in_zone") and edge and self.balls >= 1 and self.strikes < 2
                    and rng.random() < (0.30 if both_edge else 0.18)):
                self.balls -= 1
                self.strikes += 1
                entry["result"], entry["framed"] = "Strike", True
                self.framed_strikes += 1
                if self.history.records:
                    self.history.records[-1]["result"] = "Strike"
                out["stolen"], out["message"] = True, "You stole a strike with a clean catch!"
        return out

    # ---- Changing the signs (#game feel, rhythm) ----
    def sign_steal_chance(self, rng):
        """There is a risk of the signs being stolen only when there is a runner on second."""
        if not self.runners[1]:
            return False
        return rng.random() < 0.15

    def resolve_sign_steal(self, sign_bonus, rng):
        """The response when the signs are being stolen (call it when sign_steal_chance was True).

        defend_p (chance you protect the signs) = 0.55 + sign_bonus (how well you did the sign-change QTE).
        If you fail, for the next pitch only the batter's guess matches the real pitch strongly (sign_leak).
        judge.py reads this, overwrites the guess, and resets it to 0 after one pitch.
        Returns an event string.
        """
        self.events.append("Change signs")
        defend_p = max(0.10, min(0.95, 0.55 + sign_bonus))
        if rng.random() < defend_p:
            return "Changed the signs in time"
        self.sign_leak = 0.65
        return "The runner on second stole the signs! Watch out"

    # ---- The opponent's small ball: the sacrifice bunt (#C) ----
    def resolve_bunt(self, pitched_out, high_heat, charging, rng, field_bonus=0.0):
        """Resolve a bunt (a sacrifice bunt). The engine calls this when opp_tactic == "bunt".

        pitched_out : the catcher called a pitchout (busting the bunt)
        high_heat   : a high fastball (hard to bunt)
        charging    : the defense is in "bunt defense" with first and third charging
        field_bonus : how well the catcher's bunt-fielding rhythm game (qte.catcher_field_bunt) went.
                      -0.25 to 0.25. Bigger = easier to force the lead runner, prevent bunt hits, and
                      sometimes nail the runner on a squeeze at the plate. Outside the CLI (Web, tests)
                      it is always 0.0, and then behavior is exactly the same as before.
        Returns: (messages: list[str], final_result: str, at_bat_end: str|None)
        """
        self.events.append("Bunt")
        r, sp = self.runners, self.runner_speeds
        bsp = self.batter.speed

        if pitched_out:
            self.balls += 1
            if self.balls >= 4:
                self.advance_on_walk()
                return ["Busted the bunt with a pitchout! A walk-level ball four", ">>> Walk"], "Walk", "walk"
            self.opp_tactic = None                     # once it breaks down, he goes back to normal hitting
            return ["Busted the bunt! (Ball)"], "Ball", None

        pop_p = 0.10 + (0.16 if high_heat else 0.0) + (0.06 if charging else 0.0)
        foul_p = 0.22
        roll = rng.random()

        if roll < pop_p:
            self.add_out()                            # the infield handles a little pop-up
            return ["The bunt pops up! Easy out"], "Bunt popped up", "out"

        if roll < pop_p + foul_p:
            if self.strikes >= 2:                     # a failed third-strike bunt = strikeout
                self.add_out()
                return ["Bunt fouled off with two strikes ... strikeout on the failed bunt"], "Strikeout", "strikeout"
            self.strikes += 1
            return ["Bunt foul (strike)"], "Foul", None

        # It rolled fair. The runner on third tries to score on a squeeze.
        squeeze = bool(r[2])
        # With good fielding (the rhythm game), you can sometimes nail the squeeze runner at the plate.
        # It only happens when field_bonus > 0.0 = Web/tests (always 0.0) are unchanged.
        if squeeze and field_bonus > 0.0 and rng.random() < min(0.33, field_bonus * 1.2):
            self.runners = [True, bool(r[0]), bool(r[1])]      # the batter is at first (fielder's choice)
            self.runner_speeds = [bsp, sp[0], sp[1]]
            self.outs += 1
            if self.outs < 3:
                self.next_batter()
            return ["Charged, grabbed it, and fired home! Nailed the runner on the squeeze!"], "Bunt (out at home)", "out"

        scored = 1 if squeeze else 0
        if scored:
            self._score_runs(1)

        force_p = max(0.02, (0.16 if charging else 0.05) + field_bonus * 0.45)
        single_p = max(0.0, 0.03 + (bsp - 50) / 300.0 - field_bonus * 0.40)
        r2 = rng.random()

        if r2 < force_p and r[0]:
            # The defense forces the lead runner (the batter is safe at first = fielder's choice)
            self.runners = [True, bool(r[1]), False]
            self.runner_speeds = [bsp, sp[1], None]
            self.outs += 1
            if self.outs < 3:
                self.next_batter()
            return ["The defense forced the lead runner! Batter at first"], "Bunt (force out)", "out"

        if r2 < force_p + single_p:
            self.runners = [True, bool(r[0]), bool(r[1])]
            self.runner_speeds = [bsp, sp[0], sp[1]]
            self.next_batter()
            return ["Safe! An infield hit (bunt single)"], "Bunt single", "hit"

        # A normal sacrifice: the batter is out, the runners each move up one base
        self.runners = [False, bool(r[0]), bool(r[1])]
        self.runner_speeds = [None, sp[0], sp[1]]
        self.add_out()
        detail = "Squeeze works!" if scored else "Sacrifice bunt. Runner moved over"
        return [detail], "Sacrifice bunt", "out"

    # ---- The opponent's small ball: the hit-and-run (#C) ----
    def hit_and_run_runner_go(self, pitched_out, contact, rng, throw_bonus=0.0):
        """Decide the outcome after the runner on first breaks on a hit-and-run (the engine calls this).

        pitched_out : the catcher read it and called a pitchout -> the runner is hung out to dry, very likely thrown out
        contact     : the batter put it in play fair (then the judge side moves the runners, so this is not called)
        Returns an event string or None.
        """
        if not self.runners[0]:
            return None
        if pitched_out:
            # The runner is completely exposed. Reuse resolve_steal with a big throw bonus.
            return self.resolve_steal("fastball", rng, throw_bonus=0.35 + throw_bonus)
        # The batter swung (whiff / foul / weak out), so the catcher cannot throw easily -> the runner advances almost for free
        self.runners = [False, True, self.runners[2]]
        self.runner_speeds = [None, self.runner_speeds[0], self.runner_speeds[2]]
        self.events.append("Hit and run")
        return "Hit and run, the runner goes to second"

    # ---- Dropped third strike (#game feel) ----
    def dropped_third_eligible(self):
        """First base is open, or there are two outs -> a dropped third strike can happen (like the official rule)."""
        return (not self.runners[0]) or self.outs >= 2

    def resolve_dropped_third(self, block_bonus, throw_bonus, rng, hard=False):
        """A dropped third strike. Called when the catcher lets a strikeout in the dirt get by / cannot hold it
        (assuming dropped_third_eligible is True).

        block_bonus : how well you block / throw_bonus : how well you throw to first
          (both -0.25 to 0.25. From the CLI/Web QTE, or 0.0 when non-interactive)

        1) pass_p (the chance it gets completely by you) uses the same formula as resolve_block. On a passed ball
           the batter reaches first unconditionally and the other runners move up one (a runner on third scores) --
           treated the same as an ordinary passed ball.
        2) If it is not a passed ball, you throw to first. safe_p depends on the batter's speed and how well you throw.
           If he is safe the batter takes first (forced advances only). If he is out it is the same as a normal strikeout.
        Returns: (safe: bool, message: str).
        """
        self.events.append("Dropped third strike")
        pass_p = max(0.05, min(0.9, 0.45 - block_bonus + (0.12 if hard else 0.0)))
        if rng.random() < pass_p:
            if self.runners[2]:
                self._score_runs(1)
            self.runners = [True, self.runners[0], self.runners[1]]
            self.runner_speeds = [self.batter.speed, self.runner_speeds[0], self.runner_speeds[1]]
            self.next_batter()
            return True, "Passed ball! The dropped third strike lets the batter reach first"

        speed = self.batter.speed
        safe_p = 0.28 + (speed - 50) / 100 * 0.40 - throw_bonus
        if rng.random() < max(0.04, min(0.9, safe_p)):
            self.advance_on_walk()
            return True, "The throw to first was late, so he reaches on the dropped third strike!"

        self.outs += 1
        if self.outs < 3:
            self.next_batter()
        return False, "Threw him out at first! Strikeout"

    @property
    def score_diff(self):
        return self.our_score - self.opp_score

    # ---- The count / changing batters ----
    def reset_count(self):
        self.balls = 0
        self.strikes = 0

    def next_batter(self):
        self._remember_batter_history()
        self.batters_faced += 1
        self.reset_count()
        self.history.clear()
        self.lineup.advance()
        self.opp_tactic = None           # the tactic is decided anew for each batter
        self.tactic_decided = False

    def _remember_batter_history(self):
        """Before moving to the next batter, remember across at-bats the tendency of pitches shown to the current one.

        When the order turns over and we face the same batter again, predict_guess() reads this
        and faintly reflects a memory like "he was shown a lot of fastballs last time".
        """
        counts = self.history.recent_class_counts(len(self.history.records))
        if counts["fastball"] + counts["offspeed"] == 0:
            return
        mem = self.batter_pitch_memory.setdefault(id(self.batter), {"fastball": 0, "offspeed": 0})
        mem["fastball"] += counts["fastball"]
        mem["offspeed"] += counts["offspeed"]

    def batter_memory(self, batter):
        """In this half inning, the breakdown of pitches shown to this batter in past at-bats (None if none)."""
        return self.batter_pitch_memory.get(id(batter))

    def add_out(self):
        self.outs += 1
        if self.outs < 3:
            self.next_batter()

    # ---- Runs allowed (the opponent is batting) ----
    def _score_runs(self, n):
        self.opp_score += n
        self.runs_this_inning += n

    def apply_play(self, play):
        """Apply the result of baserunning.resolve() (baserunning after a fair ball) in one go."""
        self.runners = list(play["new_runners"])
        self.runner_speeds = list(play.get("new_runner_speeds", [None, None, None]))
        self._score_runs(play["runs"])
        self.outs = min(3, self.outs + play["outs_added"])
        if not self.is_over():
            self.next_batter()

    def advance_on_walk(self):
        """A walk / intentional walk / hit by pitch. Only forced runners move up (speed travels with them)."""
        r, sp, b = self.runners, self.runner_speeds, self.batter.speed
        if r[0] and r[1] and r[2]:
            self._score_runs(1)                        # bases loaded, a forced run (the runner on third scores)
            self.runners = [True, True, True]
            self.runner_speeds = [b, sp[0], sp[1]]
        elif r[0] and r[1]:
            self.runners = [True, True, True]
            self.runner_speeds = [b, sp[0], sp[1]]
        elif r[0]:
            self.runners = [True, True, bool(r[2])]
            self.runner_speeds = [b, sp[0], sp[2]]
        else:
            self.runners = [True, bool(r[1]), bool(r[2])]
            self.runner_speeds = [b, sp[1], sp[2]]
        self.next_batter()

    # ---- End-of-game checks ----
    def is_walkoff(self):
        """Whether the opponent took the lead in the bottom half (a walk-off).

        If we were fielding while ahead or tied and the opponent (the home team batting)
        goes ahead, the game ends right there without waiting for three outs. It does not apply
        to a scene that already started behind (there is no "before" to be overtaken).
        """
        return (self.walkoffs and self.half == "bottom"
                and (self.start_our - self.start_opp) >= 0
                and self.opp_score > self.our_score)

    def is_over(self):
        return self.outs >= 3 or self.is_walkoff() or bool(self.run_cap and self.runs_this_inning >= self.run_cap)

    def game_over(self):
        return self.is_over()

    # ---- Display and tallies ----
    def half_label(self):
        return "Top" if self.half == "top" else "Bottom"

    def inning_label(self):
        if self.is_extra:
            return f"{self.half_label()} {self.inning} (extras)"
        return f"{self.half_label()} {self.inning}"

    def runners_text(self):
        labels = ["1B", "2B", "3B"]
        parts = []
        for i in range(3):
            if not self.runners[i]:
                continue
            tag = "▶" if self.runner_speed_at(i) >= 70 else ""   # ▶ = a speedster (steal / advance threat)
            parts.append(labels[i] + tag)
        return "None" if not parts else ", ".join(parts)

    def result_summary(self):
        runs = self.runs_this_inning
        run_txt = ("No runs allowed" if runs == 0
                   else ("1 run allowed" if runs == 1 else f"{runs} runs allowed (multiple)"))

        start_lead = self.start_our - self.start_opp
        end_lead = self.our_score - self.opp_score
        if start_lead > 0:
            if end_lead > 0:
                status = "Lead held"
            elif end_lead == 0:
                status = "Tie game, lead lost"
            else:
                status = "Overtaken"
        elif start_lead == 0:
            status = "Tie held" if end_lead == 0 else ("Fell behind" if end_lead < 0 else "Went ahead")
        else:  # started the inning behind
            if end_lead >= 0:
                status = "Caught up / went ahead"
            elif end_lead > start_lead:
                status = "Fell further behind" if runs else "Margin unchanged"
            else:
                status = "Margin unchanged" if runs == 0 else "Fell further behind"
        return run_txt, status


def _weighted(rng, choices, weights):
    return rng.choices(choices, weights=weights, k=1)[0]


def generate_random_situation(rng=None, lineup_batters=None):
    """Build a different "late-inning scene" every time.

    lineup_batters : pass nine Batters to use that lineup (#C swapping in real data).
                     If omitted, the nine fictional batters from build_sample_lineup() are used.
    """
    rng = rng or random

    inning = _weighted(rng, INNING_CHOICES, INNING_WEIGHTS)
    is_extra = inning >= 10
    half = rng.choice(["top", "bottom"])
    we_are = "HOME" if half == "top" else "AWAY"   # we are the ones fielding

    diff = _weighted(rng, DIFF_CHOICES, DIFF_WEIGHTS)
    opp_base = rng.randint(1, 5)
    our_score = opp_base + diff
    opp_score = opp_base
    if our_score < 0:                # lift both so neither goes below 0
        opp_score -= our_score
        our_score = 0

    outs = _weighted(rng, OUT_CHOICES, OUT_WEIGHTS)
    on_base_p = 0.30 if outs == 0 else 0.38
    runners = [rng.random() < on_base_p for _ in range(3)]
    # The speed (35-85) of runners on base. Once in a while a real burner (a steal threat) shows up.
    runner_speeds = [rng.randint(35, 85) if runners[i] else None for i in range(3)]

    start_index = rng.randrange(9)
    batters = lineup_batters if lineup_batters is not None else build_sample_lineup(rng)
    lineup = Lineup(list(batters), index=start_index)
    pitcher = build_sample_pitcher(rng, name="Reliever 1")
    defense = make_default_defense(rng)   # fielder abilities ride the seed too (--seed reproducibility)

    state = MatchState(inning, is_extra, half, we_are, our_score, opp_score,
                       outs, runners, lineup, pitcher, defense, runner_speeds=runner_speeds)
    bullpen_size = _weighted(rng, [1, 2, 3], [2, 5, 3])   # one to three arms wait in the bullpen
    state.bullpen = [build_sample_pitcher(rng, name=f"Reliever {i + 2}")
                     for i in range(bullpen_size)]
    return state
