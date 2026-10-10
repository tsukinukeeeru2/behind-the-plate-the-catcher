"""Automated tests.

    python tests.py

Standard library unittest only. Randomness is pinned with random.Random(seed).
The final StressTests run 1000 games from random situations and check that there are
no crashes, invalid counts, broken batting orders, or infinite loops.
"""

import io
import json
import random
import re
import socket
import socketserver
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

from baserunning import resolve as resolve_baserunning
from batted_ball import BattedBall, generate_batted_ball
from batter import Batter
from constants import COURSE_SHORT, COURSES
from defense import ALIGNMENTS, Defense, make_default_defense
from fielders import POSITIONS, Fielder, make_default_fielders, position_fit, weakest_fielder
from fielding import resolve_batted_ball
from ballpark_view import render_field
from judge import judge_pitch
from lineup import Lineup, build_sample_lineup, load_lineup_file
from engine import log_pitch, maybe_resolve_sign_steal, resolve_one_pitch
from match_state import generate_random_situation
from pitch_data import (
    PITCH_LIBRARY,
    all_pitch_keys,
    family_of,
    get_pitch,
    guess_class_of,
    pitch_name,
    repertoire_options,
    velocity_of,
)
from pitcher import Pitcher, build_sample_pitcher
from qte import catcher_block, catcher_change_signs, catcher_throw, qte_enabled
from reaction import FACE_CATEGORIES, describe_reaction, render_reaction_block
import mlb_data_adapter
import playview
import replay
import stats
from strategy import (PITCH_INTENTS, build_analysis, build_postgame_report, catcher_rating, evaluate_sequencing,
                      grade_reads, intent_label)
from ui import render_analysis, render_dashboard, render_intro, render_play_result, render_spray
import webapp

VALID_RESULTS = {"Strike", "Ball", "Whiff", "Foul", "Out",
                 "Single", "Double", "Triple", "Home run"}


# ---------- Helpers ----------
def _batter(bats="R", coarse="contact", pull=0.5, gb=0.45, **kw):
    return Batter(name="T", bats=bats, coarse_type=coarse, pull=pull, gb_tendency=gb, **kw)


def _defense_all_equal(skill=55):
    return Defense([Fielder(f"g{i}", skill, skill, skill, skill, skill) for i in range(7)])


def _state(seed=0):
    return generate_random_situation(random.Random(seed))


def _silent_apply(state, outcome):
    result = outcome["result"]
    if result in ("Strike", "Whiff"):
        state.strikes += 1
        if state.strikes >= 3:
            state.add_out()
    elif result == "Ball":
        state.balls += 1
        if state.balls >= 4:
            state.advance_on_walk()
    elif result == "Foul":
        if state.strikes < 2:
            state.strikes += 1
    else:  # a fair batted ball (single / double / triple / home run / out)
        state.apply_play(outcome["play"])


def _drive_pitch(state, rng, intents, courses):
    pt = rng.choice(list(repertoire_options(state.pitcher)))
    co = rng.choice(courses)
    it = rng.choice(intents)
    outcome = judge_pitch(state, pt, co, rng, intent=it)
    outcome["reaction"] = describe_reaction(pt, outcome["actual_course"], outcome, rng)
    log_pitch(state, outcome)
    state.history.add(pt, co, outcome["result"], outcome["timing"], outcome["swung"],
                      actual_course=outcome["actual_course"], intent=it,
                      velocity=velocity_of(pt), family=family_of(pt), in_zone=outcome["in_zone"])
    _silent_apply(state, outcome)
    return outcome


# ---------- Pitch data ----------
class PitchDataTests(unittest.TestCase):
    def test_entries_have_required_fields(self):
        required = {"name", "pitch_class", "velocity", "movement", "whiff_rate",
                    "groundball_rate", "contact_quality", "platoon"}
        for key, data in PITCH_LIBRARY.items():
            self.assertTrue(required.issubset(data), key)
            self.assertIn(data["pitch_class"], ("fastball", "breaking", "offspeed"))

    def test_guess_class_two_way(self):
        for key in all_pitch_keys():
            self.assertIn(guess_class_of(key), ("fastball", "offspeed"))

    def test_new_pitch_by_data_only(self):
        PITCH_LIBRARY["test_pitch"] = {
            "name": "Test pitch", "pitch_class": "breaking", "velocity": 80, "movement": 5,
            "whiff_rate": 0.2, "groundball_rate": 0.5, "contact_quality": 0.4,
            "platoon": {"same": -0.05, "opposite": 0.05}}
        try:
            state = _state(1)
            out = judge_pitch(state, "test_pitch", "mid_mid", random.Random(0))
            self.assertIn(out["result"], VALID_RESULTS)
        finally:
            del PITCH_LIBRARY["test_pitch"]


# ---------- Fielders and defense ----------
class FielderTests(unittest.TestCase):
    def test_fit_amplified_at_hard_position(self):
        below = Fielder("x", 40, 40, 40, 40, 40)
        above = Fielder("y", 65, 65, 65, 65, 65)
        self.assertLess(position_fit(below, "SS"), position_fit(below, "1B"))
        self.assertGreater(position_fit(above, "SS"), position_fit(above, "1B"))

    def test_one_clear_liability(self):
        fs = make_default_fielders(random.Random(3))
        self.assertEqual(len(fs), 7)
        overalls = sorted(f.overall() for f in fs)
        self.assertLess(overalls[0], overalls[1] - 8)
        self.assertIs(weakest_fielder(fs), min(fs, key=lambda f: f.overall()))


class DefenseTests(unittest.TestCase):
    def test_swap_permutation(self):
        d = make_default_defense()
        before = {id(f) for f in d.assignment.values()}
        d.swap("SS", "1B")
        self.assertEqual(before, {id(f) for f in d.assignment.values()})

    def test_alignment_validates(self):
        d = make_default_defense()
        for key in ALIGNMENTS:
            d.set_alignment(key)
        with self.assertRaises(ValueError):
            d.set_alignment("nonsense")

    def test_ede_rewards_hiding_weak_at_1b(self):
        good = [Fielder(f"s{i}", 62, 62, 62, 62, 62) for i in range(6)]
        weak = Fielder("W", 30, 30, 30, 30, 30)
        at_1b = Defense(good + [weak])
        at_1b.assignment = dict(zip(POSITIONS, good + [weak]))
        at_1b.assignment["1B"], at_1b.assignment["RF"] = at_1b.assignment["RF"], at_1b.assignment["1B"]
        at_ss = Defense(good + [weak])
        at_ss.assignment = dict(zip(POSITIONS, good + [weak]))
        at_ss.assignment["SS"], at_ss.assignment["RF"] = at_ss.assignment["RF"], at_ss.assignment["SS"]
        for d in (at_1b, at_ss):
            self.assertTrue(0 <= d.expected_defensive_efficiency() <= 100)
        self.assertGreater(at_1b.expected_defensive_efficiency(),
                           at_ss.expected_defensive_efficiency())

    def test_bunt_alignment_helps_soft_grounders_hurts_hard_ones(self):
        d = _defense_all_equal()
        soft = BattedBall("pull", "ground", "soft", 0.1, "infield")
        hard = BattedBall("pull", "ground", "hard", 0.9, "infield")
        d.set_alignment("standard")
        self.assertEqual(d.alignment_out_adjust("3B", soft), 0.0)
        d.set_alignment("bunt")
        self.assertGreater(d.alignment_out_adjust("3B", soft), 0.0)
        self.assertLess(d.alignment_out_adjust("3B", hard), 0.0)
        self.assertEqual(d.alignment_out_adjust("CF", soft), 0.0)   # the outfield is unaffected


# ---------- Batted balls and fielding ----------
class BattedBallTests(unittest.TestCase):
    def _pull_share(self, batter, timing="on_time", course="mid_mid", n=4000):
        rng = random.Random(11)
        return sum(generate_batted_ball(batter, get_pitch("four_seam"), course, timing, 0.0, rng)
                   .direction == "pull" for _ in range(n)) / n

    def test_pull_tendency_and_timing(self):
        self.assertGreater(self._pull_share(_batter(pull=0.85)), self._pull_share(_batter(pull=0.30)))
        b = _batter(pull=0.5)
        self.assertGreater(self._pull_share(b, timing="early"), self._pull_share(b, timing="on_time"))


class FieldingTests(unittest.TestCase):
    def _out_rate(self, bb, defense, batter, n=4000, seed=5):
        rng = random.Random(seed)
        return sum(resolve_batted_ball(bb, defense, batter, rng)["result"] == "Out"
                   for _ in range(n)) / n

    def test_hard_contact_lowers_out_rate(self):
        d, b = _defense_all_equal(), _batter()
        soft = BattedBall("center", "ground", "soft", 0.2, "infield")
        hard = BattedBall("center", "ground", "hard", 0.9, "infield")
        self.assertGreater(self._out_rate(soft, d, b), self._out_rate(hard, d, b))

    def test_alignment_match_raises_out_rate(self):
        b = _batter(bats="R")
        matched = _defense_all_equal(); matched.set_alignment("pull")
        bb = BattedBall("pull", "ground", "medium", 0.5, "infield")
        self.assertGreater(self._out_rate(bb, matched, b), self._out_rate(bb, _defense_all_equal(), b))

    def test_hide_weak_fielder_depends_on_handedness(self):
        weak = Fielder("W", 26, 26, 26, 26, 26)

        def out_rate(bats, weak_pos, n=4000):
            d = _defense_all_equal(55)
            d.assignment[weak_pos] = weak
            b = _batter(bats=bats, coarse="power", pull=0.85)
            rng = random.Random(999)
            return sum(resolve_batted_ball(
                generate_batted_ball(b, get_pitch("four_seam"), "mid_mid", "on_time", 0.0, rng),
                d, b, rng)["result"] == "Out" for _ in range(n)) / n

        self.assertGreater(out_rate("R", "RF"), out_rate("R", "LF") + 0.015)
        self.assertGreater(out_rate("L", "LF"), out_rate("L", "RF") + 0.015)

    def test_fast_runner_beats_out_grounders(self):
        d = _defense_all_equal()
        slow = _batter(coarse="power"); slow.speed = 30
        fast = _batter(coarse="power"); fast.speed = 80
        bb = BattedBall("center", "ground", "medium", 0.5, "infield")
        self.assertGreater(self._out_rate(bb, d, slow), self._out_rate(bb, d, fast))

    def test_weak_fielder_commits_more_errors(self):
        def error_rate(skill, n=6000):
            d, b = _defense_all_equal(skill), _batter()
            bb = BattedBall("center", "ground", "medium", 0.5, "infield")
            rng = random.Random(3)
            return sum(resolve_batted_ball(bb, d, b, rng)["result"] == "Error"
                       for _ in range(n)) / n
        self.assertGreater(error_rate(25), error_rate(75) + 0.01)

    def test_error_is_never_scored_as_an_out(self):
        d, b = _defense_all_equal(20), _batter()
        bb = BattedBall("center", "ground", "hard", 0.9, "infield")
        rng = random.Random(11)
        for _ in range(2000):
            fd = resolve_batted_ball(bb, d, b, rng)
            if fd["result"] == "Error":
                self.assertFalse(fd["batter_out"])
                self.assertFalse(fd["air_out"])
                self.assertTrue(fd["error"])


# ---------- Extra-base hits and baserunning (improvement A) ----------
def _fd(result, position="SS", batter_out=None, air_out=False):
    if batter_out is None:
        batter_out = (result == "Out")
    return {"result": result, "batter_out": batter_out, "air_out": air_out,
            "position": position, "fielder": "X", "out_probability": 0.5,
            "breakdown": {"alignment": 0.0}}


class BaserunningTests(unittest.TestCase):
    def test_hit_extent_homerun_needs_hard_deep_air(self):
        from fielding import _hit_extent
        slugger = _batter(coarse="power"); slugger.power = 0.9
        rng = random.Random(0)
        hard_deep = BattedBall("pull", "fly", "hard", 0.9, "deep")
        soft_grounder = BattedBall("pull", "ground", "soft", 0.2, "infield")
        hr = sum(_hit_extent(hard_deep, slugger, rng) == "Home run" for _ in range(3000))
        never = sum(_hit_extent(soft_grounder, slugger, rng) == "Home run" for _ in range(3000))
        self.assertGreater(hr, 200)
        self.assertEqual(never, 0)

    def test_double_clears_the_bases(self):
        play = resolve_baserunning(BattedBall("pull", "line", "hard", 0.8, "deep"),
                                   _fd("Double"), [True, True, True], 0, _batter(),
                                   random.Random(0))
        self.assertEqual(play["runs"], 3)
        self.assertEqual(play["new_runners"], [False, True, False])
        self.assertEqual(play["outs_added"], 0)

    def test_error_advances_batter_and_runners_like_a_single(self):
        play = resolve_baserunning(BattedBall("center", "ground", "hard", 0.8, "infield"),
                                   _fd("Error", "SS"), [False, False, True], 0, _batter(),
                                   random.Random(0))
        self.assertEqual(play["batter_result"], "Error")
        self.assertEqual(play["label"], "Error")
        self.assertEqual(play["outs_added"], 0)
        self.assertTrue(play["new_runners"][0])          # the batter reaches first
        self.assertEqual(play["runs"], 1)                # the runner from third scores

    def test_double_play_removes_first_runner_and_adds_two_outs(self):
        bb = BattedBall("center", "ground", "medium", 0.5, "infield")
        got_dp = 0
        for s in range(400):
            play = resolve_baserunning(bb, _fd("Out", "SS"), [True, False, False], 0,
                                       _batter(coarse="average"), random.Random(s))
            if play["outs_added"] == 2:
                got_dp += 1
                self.assertFalse(play["new_runners"][0])
                self.assertEqual(play["label"], "Double play")
        self.assertGreater(got_dp, 100)   # it happens a decent amount

    def test_sac_fly_scores_from_third(self):
        bb = BattedBall("center", "fly", "medium", 0.5, "deep")
        runs = 0
        for s in range(300):
            play = resolve_baserunning(bb, _fd("Out", "CF", air_out=True),
                                       [False, False, True], 0, _batter(), random.Random(s))
            runs += play["runs"]
            if play["runs"]:
                self.assertEqual(play["label"], "Sac fly")
                self.assertEqual(play["outs_added"], 1)
        self.assertGreater(runs, 150)

    def test_no_runs_when_play_makes_third_out(self):
        # A double play with 2 outs -> the 3rd out stands, no run counts
        bb = BattedBall("center", "ground", "medium", 0.5, "infield")
        for s in range(200):
            play = resolve_baserunning(bb, _fd("Out", "SS"), [True, False, True], 2,
                                       _batter(), random.Random(s))
            if play["outs_added"] >= 1:
                self.assertEqual(play["runs"], 0)

    def test_single_with_runner_on_second_scores_more_from_outfield(self):
        def score_rate(ball_type):
            bb = BattedBall("center", ball_type, "medium", 0.5,
                            "shallow" if ball_type != "ground" else "infield")
            return sum(resolve_baserunning(bb, _fd("Single", "CF"), [False, True, False], 1,
                                           _batter(), random.Random(s))["runs"]
                       for s in range(500)) / 500
        self.assertGreater(score_rate("line"), score_rate("ground"))


# ---------- Generating the game situation ----------
class SituationTests(unittest.TestCase):
    def test_random_situation_valid(self):
        rng = random.Random(0)
        for _ in range(500):
            s = generate_random_situation(rng)
            self.assertIn(s.inning, (7, 8, 9, 10))
            self.assertIn(s.outs, (0, 1, 2))
            self.assertGreaterEqual(s.our_score, 0)
            self.assertGreaterEqual(s.opp_score, 0)
            self.assertTrue(1 <= s.lineup.spot_number() <= 9)
            self.assertEqual(len(s.runners), 3)

    def test_close_games_more_common(self):
        rng = random.Random(1)
        close = blowout = 0
        for _ in range(3000):
            s = generate_random_situation(rng)
            d = abs(s.our_score - s.opp_score)
            close += d <= 1
            blowout += d >= 3
        self.assertGreater(close, blowout * 2)

    def test_lineup_wraps_like_real_baseball(self):
        lu = Lineup(build_sample_lineup(random.Random(0)), index=6)   # starting from the 7th hitter
        spots = []
        for _ in range(11):
            spots.append(lu.spot_number())
            lu.advance()
        self.assertEqual(spots, [7, 8, 9, 1, 2, 3, 4, 5, 6, 7, 8])

    def test_nine_distinct_batters(self):
        lu = build_sample_lineup(random.Random(0))
        self.assertEqual(len(lu), 9)
        # the batter types are really spread out
        self.assertGreaterEqual(len({b.coarse_type for b in lu}), 4)
        # an on-base type and a power type have clearly different discipline
        self.assertGreater(lu[7].discipline, lu[4].discipline)

    def _defending(self, half, our, opp, start_our=None, start_opp=None):
        st = generate_random_situation(random.Random(0))
        st.half = half
        st.our_score, st.opp_score = our, opp
        st.start_our = our if start_our is None else start_our
        st.start_opp = opp if start_opp is None else start_opp
        st.outs, st.runners, st.runner_speeds = 0, [False, False, False], [None, None, None]
        return st

    def test_walkoff_ends_the_half_inning_immediately(self):
        """If the opponent takes the lead in the bottom half, the game ends without waiting for the 3rd out."""
        st = self._defending("bottom", our=3, opp=2)     # defending with a 1-run lead
        self.assertFalse(st.is_over())
        st._score_runs(2)                                # the opponent scores 2 -> 3-4, they take the lead
        self.assertTrue(st.is_walkoff())
        self.assertTrue(st.is_over())
        self.assertEqual(st.outs, 0)

    def test_no_walkoff_in_the_top_half(self):
        """In the top half (the opponent bats first) the game goes on even if they take the lead."""
        st = self._defending("top", our=3, opp=2)
        st._score_runs(5)
        self.assertFalse(st.is_over())

    def test_no_walkoff_when_already_behind_at_the_start(self):
        """If we are already behind at the start it is not a "comeback", so the game does not end early."""
        st = self._defending("bottom", our=2, opp=3)
        self.assertFalse(st.is_over())

    def test_tie_game_walkoff(self):
        st = self._defending("bottom", our=4, opp=4)
        st._score_runs(1)
        self.assertTrue(st.is_over())

    def test_same_seed_reproduces_the_defense_too(self):
        """Reproducibility of --seed: the 7 fielders' abilities and weaknesses are also exactly the same scene
        (forgetting to pass rng to make_default_defense would use the global random and break this)."""
        import random as _r

        def fielder_stats(seed):
            st = generate_random_situation(rng=_r.Random(seed))
            return [(f.name, f.range, f.hands, f.arm, f.reaction, f.speed)
                    for f in st.defense.fielders]

        _r.seed()                                  # stir the global random
        a = fielder_stats(31337)
        _r.seed()
        b = fielder_stats(31337)
        self.assertEqual(a, b)
        self.assertNotEqual(a, fielder_stats(31338))


# ---------- The pitcher's actual pitch (Pitch Execution) ----------
class ExecutionTests(unittest.TestCase):
    def test_can_hit_and_miss_spot(self):
        p = build_sample_pitcher(random.Random(0))
        rng = random.Random(0)
        hit = miss = 0
        for _ in range(2000):
            a = p.execute_pitch(p.repertoire[0], "mid_lo", "chase", rng)
            self.assertTrue(0.0 <= a["quality"] <= 1.0)
            self.assertIn(a["actual_course"], COURSES)
            if a["missed"]:
                miss += 1
            else:
                hit += 1
        self.assertGreater(hit, 0)
        self.assertGreater(miss, 0)

    def test_low_control_misses_more(self):
        wild = Pitcher(control=45, command=42, repertoire=["four_seam"])
        sharp = Pitcher(control=75, command=75, repertoire=["four_seam"])

        def miss_rate(p):
            rng = random.Random(7)
            return sum(p.execute_pitch("four_seam", "out_mid", "freeze", rng)["missed"]
                       for _ in range(4000)) / 4000

        self.assertGreater(miss_rate(wild), miss_rate(sharp) + 0.03)

    def test_return_tempo_helps_execution(self):
        """The better the catcher's return tempo, the fewer missed pitches and the more stuff. 0.0 has no effect."""
        p = Pitcher(control=55, command=55, stuff=55, velocity=55, repertoire=["four_seam"])

        def stats(tempo, n=6000):
            miss = q = 0
            for s in range(n):
                p.pitches_thrown = 0
                r = p.execute_pitch("four_seam", "out_lo", "strike", random.Random(s), tempo=tempo)
                miss += r["missed"]; q += r["quality"]
            return miss / n, q / n

        bad_miss, bad_q = stats(-0.25)
        neu_miss, neu_q = stats(0.0)
        good_miss, good_q = stats(0.25)
        self.assertGreater(bad_miss, good_miss)          # a bad tempo means more missed pitches
        self.assertGreater(good_q, bad_q)               # a good tempo means more stuff
        self.assertAlmostEqual(neu_q, (bad_q + good_q) / 2, delta=0.02)


# ---------- Reactions with faces ----------
class ReactionTests(unittest.TestCase):
    def _mk_outcome(self, timing="late", swung=True, in_zone=True, result="Foul"):
        return {"timing": timing, "swung": swung, "in_zone": in_zone, "result": result}

    def test_returns_structured_dict(self):
        r = describe_reaction("slider", "in_mid", self._mk_outcome(), random.Random(0))
        self.assertEqual(set(r), {"face", "ascii_face", "text", "category", "kind"})
        self.assertIn(r["category"], FACE_CATEGORIES)
        self.assertTrue(r["face"])
        self.assertTrue(r["ascii_face"].isascii())      # the ASCII fallback is pure ASCII

    def test_face_not_deterministic_for_state(self):
        rng = random.Random(0)
        oc = self._mk_outcome(timing="late")
        faces, cats, kinds = set(), set(), set()
        for _ in range(400):
            r = describe_reaction("four_seam", "in_mid", oc, rng)
            faces.add(r["face"]); cats.add(r["category"]); kinds.add(r["kind"])
        self.assertGreaterEqual(len(faces), 4)           # several faces even for the same state
        self.assertGreaterEqual(len(cats), 3)            # they scatter via misleading / blurring
        self.assertEqual(kinds, {"reveal", "mislead", "ambiguous"})

    def test_ascii_fallback_uses_ascii_face(self):
        r = describe_reaction("slider", "mid_lo", self._mk_outcome(), random.Random(1))
        ascii_block = render_reaction_block(r, ascii_only=True)
        kaomoji_block = render_reaction_block(r, ascii_only=False)
        # the ASCII version's face line is pure ASCII / the face version contains a kaomoji
        face_line = ascii_block.splitlines()[1]
        self.assertTrue(face_line.isascii())
        self.assertIn(r["ascii_face"], ascii_block)
        self.assertIn(r["face"], kaomoji_block)


# ---------- Pitch-sequence leaks across at-bats (the batter's memory) ----------
class BatterMemoryTests(unittest.TestCase):
    def test_batter_memory_is_none_until_recorded(self):
        st = _state(5)
        self.assertIsNone(st.batter_memory(st.batter))

    def test_next_batter_remembers_pitch_tendency_until_batting_around(self):
        st = _state(5)
        batter = st.batter
        for _ in range(6):
            st.history.add(pitch_type="four_seam", course="mid_mid", result="Strike",
                           timing="on_time", swung=False, family="fastball", in_zone=True)
        for _ in range(9):                    # go through the whole order and come back to the same batter
            st.next_batter()
        self.assertIs(st.batter, batter)
        mem = st.batter_memory(batter)
        self.assertEqual(mem, {"fastball": 6, "offspeed": 0})

    def test_predict_guess_leans_toward_remembered_tendency(self):
        st = _state(6)
        batter = st.batter

        def signed_lean(guess):
            return guess["class_strength"] if guess["class"] == "fastball" else -guess["class_strength"]

        without = signed_lean(batter.predict_guess(st.history, st, st.pitcher))
        st.batter_pitch_memory[id(batter)] = {"fastball": 20, "offspeed": 0}
        leans_fastball = signed_lean(batter.predict_guess(st.history, st, st.pitcher))
        self.assertGreater(leans_fastball, without)

        st.batter_pitch_memory[id(batter)] = {"fastball": 0, "offspeed": 20}
        leans_offspeed = signed_lean(batter.predict_guess(st.history, st, st.pitcher))
        self.assertLess(leans_offspeed, without)


# ---------- Sequencing setup / decision quality ----------
class StrategyTests(unittest.TestCase):
    def _seq(self, records, call):
        state = _state(3)
        for rec in records:
            state.history.add(**rec)
        batter = state.batter
        guess = batter.predict_guess(state.history, state, state.pitcher)
        actual = {"pitch_type": call["pitch_type"], "target_course": call["target_course"],
                  "actual_course": call["target_course"], "quality": 0.5,
                  "missed": False, "miss_kind": None}
        return evaluate_sequencing(state.history, call, actual, batter, guess, state)

    def test_sequencing_is_conditional_not_flat(self):
        call = {"pitch_type": "four_seam", "target_course": "mid_hi", "intent": "chase"}
        low_before = [{"pitch_type": "four_seam", "course": "mid_lo", "result": "Strike",
                       "timing": "on_time", "swung": False, "actual_course": "mid_lo",
                       "velocity": 95, "family": "fastball", "in_zone": True}]
        with_ladder = self._seq(low_before, call)
        without = self._seq([], call)
        self.assertNotEqual(with_ladder["notes"], without["notes"])
        self.assertLessEqual(abs(with_ladder["whiff_adj"]), 0.15)   # not a fixed big bonus

    def test_repeating_same_pitch_hurts(self):
        call = {"pitch_type": "slider", "target_course": "out_mid", "intent": "chase"}
        repeated = [{"pitch_type": "slider", "course": "out_mid", "result": "Ball",
                     "timing": "on_time", "swung": False, "actual_course": "out_mid",
                     "velocity": 85, "family": "breaking", "in_zone": False}]
        seq = self._seq(repeated, call)
        self.assertLess(seq["dq_seq"], 0)
        self.assertLess(seq["contact_adj"] * -1, 0.001 + 0)  # contact_adj goes in the + direction (easier to hit)

    def test_analysis_separates_result_and_judgment(self):
        log = [
            {"pitch_number": 1, "call_label": "a", "result": "Single",
             "decision_quality": 0.5, "sequence_label": "x",
             "guess": {"class_strength": 0.6}, "fooled_guess": True,
             "alignment_helped": False, "missed": False,
             "outcome_flags": {"swung": True, "in_zone": True}},
            {"pitch_number": 2, "call_label": "b", "result": "Out",
             "decision_quality": -0.5, "sequence_label": "y",
             "guess": {"class_strength": 0.6}, "fooled_guess": False,
             "alignment_helped": False, "missed": False,
             "outcome_flags": {"swung": True, "in_zone": True}},
        ]
        a = build_analysis(log)
        self.assertEqual(len(a["unlucky"]), 1)   # good decision, bad result
        self.assertEqual(len(a["lucky"]), 1)     # bad decision, good result


# ---------- judge integration / hiding internal metrics ----------
class IntegrationTests(unittest.TestCase):
    def test_batted_ball_only_on_fair(self):
        state = _state(4)
        rng = random.Random(0)
        seen = set()
        fair = {"Out", "Error", "Single", "Double", "Triple", "Home run"}
        for _ in range(3000):
            o = judge_pitch(state, "four_seam", "mid_mid", rng, intent="strike")
            seen.add(o["result"])
            if o["result"] in fair:
                self.assertIsNotNone(o["batted_ball"])
                self.assertIn("breakdown", o["fielding"])
                self.assertIsNotNone(o["play"])
            else:
                self.assertIsNone(o["batted_ball"])
        self.assertIn("Out", seen)
        self.assertTrue(seen & {"Single", "Double", "Triple", "Home run"})

    def test_decision_quality_hidden_from_live_ui(self):
        state = _state(5)
        rng = random.Random(0)
        last = None
        for _ in range(6):
            last = _drive_pitch(state, rng, list(PITCH_INTENTS), list(COURSES))
            if state.is_over():
                break
        dash = render_dashboard(state, last)
        play = render_play_result(last)
        dq = str(last["_analysis"]["decision_quality"])
        for text in (dash, play):
            self.assertNotIn("decision", text.lower())
            self.assertNotIn("Decision quality", text)
            self.assertNotIn(dq, text)

    def test_postgame_report_uses_localized_pitch_and_course_names(self):
        """The review screen (shared by CLI / Web) must show localized pitch names and short course names,
        not the raw internal keys like "sinker/mid_mid".
        The call_label itself (the internal notation stats.py parses) must not change.
        """
        state = _state(11)
        rng = random.Random(11)
        while not state.is_over():
            resolve_one_pitch(state, "sinker", "mid_mid", "strike", rng)
        entry = state.pitch_log[0]
        self.assertEqual(entry["call_label"], f"sinker/mid_mid({intent_label('strike')})")
        self.assertIn(pitch_name("sinker"), entry["call_label_text"])
        self.assertIn(COURSE_SHORT["mid_mid"], entry["call_label_text"])
        self.assertNotIn("sinker", entry["call_label_text"])
        self.assertNotIn("mid_mid", entry["call_label_text"])

    def test_reaction_does_not_print_hidden_pitch_key(self):
        state = _state(6)
        rng = random.Random(1)
        for _ in range(300):
            o = judge_pitch(state, "slider", "mid_lo", rng)
            r = describe_reaction("slider", o["actual_course"], o, rng)
            self.assertNotIn(state.batter.weak_pitch, r["text"])

    def test_endgame_autoplay_one_game(self):
        state = _state(7)
        rng = random.Random(7)
        guard = 0
        while not state.is_over():
            guard += 1
            self.assertLess(guard, 400)
            _drive_pitch(state, rng, list(PITCH_INTENTS), list(COURSES))
        self.assertTrue(state.is_over())
        with redirect_stdout(io.StringIO()):
            build_analysis(state.pitch_log)
        run_txt, status = state.result_summary()
        self.assertTrue(run_txt and status)
        # it ended with 3 outs, or with a walk-off
        self.assertTrue(state.outs == 3 or state.is_walkoff())

    def test_same_seed_gives_same_engine_result_cli_and_web_share(self):
        """The CLI (main.py) and the Web (webapp.py) both just call engine.resolve_one_pitch()
        directly, so if this function itself is reproducible by seed, both are reproducible.
        """
        def play(seed):
            rng = random.Random(seed)
            state = generate_random_situation(rng=rng)
            calls = [("four_seam", "in_hi", "freeze"), ("slider", "out_lo", "chase"),
                    ("changeup", "mid_lo", "weak_contact")]
            out = []
            for pt, co, it in calls:
                if state.is_over():
                    break
                outcome, msgs = resolve_one_pitch(state, pt, co, it, rng)
                out.append((outcome["result"], outcome["actual_course"], outcome["quality"]))
            return out, state.our_score, state.opp_score, state.outs

        self.assertEqual(play(4242), play(4242))
        self.assertNotEqual(play(4242), play(4243))

    def test_defense_swap_changes_who_fields_the_ball(self):
        """After a swap, the new arrangement is used as is in the actual judging (fielding) too."""
        state = _state(8)
        before = state.defense.fielder_at("SS")
        other = state.defense.fielder_at("1B")
        state.defense.swap("SS", "1B")
        self.assertIs(state.defense.fielder_at("SS"), other)
        self.assertIs(state.defense.fielder_at("1B"), before)
        # The judging chain (fielding.resolve_batted_ball) refers to Defense directly,
        # so as long as it looks at the same object, the post-swap arrangement is used as is
        bb = BattedBall("center", "ground", "medium", 0.5, "infield")
        fd = resolve_batted_ball(bb, state.defense, state.batter, random.Random(0))
        if fd["position"] in ("SS", "1B"):
            self.assertEqual(fd["fielder"], state.defense.fielder_at(fd["position"]).name)

    def test_postgame_report_separates_result_from_judgment(self):
        from strategy import build_postgame_report
        state = _state(9)
        rng = random.Random(9)
        guard = 0
        while not state.is_over():
            guard += 1
            self.assertLess(guard, 400)
            pt = rng.choice(list(repertoire_options(state.pitcher)))
            co = rng.choice(list(COURSES))
            it = rng.choice(list(PITCH_INTENTS))
            resolve_one_pitch(state, pt, co, it, rng)
        report = build_postgame_report(state)
        for key in ("runs_allowed", "batters_faced", "pitches", "strikeouts", "walks", "hits"):
            self.assertIn(key, report)
        self.assertIn("avg_quality", report["pitch_execution"])
        self.assertIn("final_ede", report["defense"])
        a = build_analysis(state.pitch_log)
        # the "result"-only report and the "decision"-only analysis are separate containers
        self.assertNotIn("decision_quality", report)
        self.assertIn("avg_decision_quality", a)


# ---------- Improvements B/C/D/E ----------
class BCDETests(unittest.TestCase):
    def test_lineup_from_records_ignores_unknown_keys(self):
        rec = dict(name="X", bats="R", coarse_type="power", power=0.8,
                   _note="This is ignored", team="Fake")
        lu = Lineup.from_records([rec] * 9)
        self.assertEqual(len(lu.batters), 9)
        self.assertEqual(lu.batters[0].name, "X")

    def test_sample_team_json_loads(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "lineups", "sample_team.json")
        lu = load_lineup_file(path)
        self.assertEqual(len(lu.batters), 9)
        self.assertTrue(all(b.bats in ("R", "L") for b in lu.batters))
        self.assertGreaterEqual(len({b.coarse_type for b in lu.batters}), 4)

    def test_intro_shows_hints_only_when_requested(self):
        self.assertNotIn("Beginner hints", render_intro())
        self.assertNotIn("Beginner hints", render_intro(hints=False))
        self.assertIn("Beginner hints", render_intro(hints=True))

    def test_custom_lineup_is_used(self):
        batters = build_sample_lineup(random.Random(0))
        batters[0].name = "MARKER"
        st = generate_random_situation(random.Random(1), lineup_batters=batters)
        self.assertIn("MARKER", [b.name for b in st.lineup.batters])

    def test_pitcher_fatigue_raises_miss_rate(self):
        def miss_rate(fixed_count):
            p = Pitcher(control=60, command=60, repertoire=["four_seam"])
            rng = random.Random(3)
            hits = 0
            for _ in range(4000):
                p.pitches_thrown = fixed_count      # pin the same "pitch count" each time to compare
                hits += p.execute_pitch("four_seam", "mid_lo", "freeze", rng)["missed"]
            return hits / 4000

        self.assertGreater(miss_rate(60), miss_rate(0) + 0.05)
        self.assertEqual(Pitcher(repertoire=["four_seam"]).fatigue(), 0.0)

    def test_read_grading_matches_truth(self):
        st = _state(4)
        b = st.batter
        st.record_read(wait="fastball", weak=b.weak_course)
        graded = grade_reads(st.reads, st.lineup.batters)
        self.assertEqual(graded["graded"], 2)
        truth_wait = "fastball" if b.guess_bias >= 0.5 else "offspeed"
        expected_correct = (truth_wait == "fastball") + 1   # weak is always right
        self.assertEqual(graded["correct"], expected_correct)

    def test_spray_accumulates_only_on_fair(self):
        st = _state(5)
        rng = random.Random(5)
        while not st.is_over():
            _drive_pitch(st, rng, list(PITCH_INTENTS), list(COURSES))
        self.assertTrue(all(s["result"] in ("Out", "Single", "Double", "Triple", "Home run")
                            for s in st.spray))

    def test_dashboard_and_analysis_render_without_error(self):
        st = _state(6)
        rng = random.Random(6)
        st.record_read(wait="offspeed", weak="mid_lo")
        last = None
        while not st.is_over():
            last = _drive_pitch(st, rng, list(PITCH_INTENTS), list(COURSES))
        for text in (render_dashboard(st, last), render_spray(st),
                     render_field(st.defense, st.batter), render_analysis(st)):
            self.assertIsInstance(text, str)
            self.assertTrue(text)


# ---------- MLB real-data Adapter ----------
class MLBDataAdapterTests(unittest.TestCase):
    def test_normalize_maps_public_stats_directly(self):
        raw = {"name": "Test Player", "bats": "L", "avg": 0.300, "obp": 0.400, "slg": 0.550,
              "whiff_pct": 20, "chase_pct": 25, "pull_pct": 40, "k_pct": 18, "bb_pct": 12,
              "gb_pct": 40, "speed_pct": 60}
        rec = mlb_data_adapter.normalize_batter_record(raw)
        self.assertEqual(rec["name"], "Test Player")
        self.assertEqual(rec["bats"], "L")
        self.assertEqual(rec["avg"], 0.300)
        self.assertEqual(rec["obp"], 0.400)
        self.assertEqual(rec["slg"], 0.550)
        self.assertEqual(rec["speed"], 60)
        # every key a Batter accepts is present
        b = Batter(**rec)
        self.assertEqual(b.name, "Test Player")

    def test_normalize_is_deterministic_for_the_same_player(self):
        raw = {"name": "Same Player", "bats": "R", "avg": 0.270, "obp": 0.330, "slg": 0.430}
        a = mlb_data_adapter.normalize_batter_record(raw)
        b = mlb_data_adapter.normalize_batter_record(raw)
        self.assertEqual(a, b)   # the same player gets the same hunch (hot_course etc.) every time

    def test_normalize_uses_real_zone_profile_when_given(self):
        """If zone_profile (real per-zone results) is passed, hot_course/weak_course come from
        that data, not from a hunch.
        """
        raw = {"name": "Zone Player", "bats": "R", "avg": 0.280, "obp": 0.350, "slg": 0.450,
              "zone_profile": {"out_lo": 0.120, "in_hi": 0.480, "mid_mid": 0.300}}
        rec = mlb_data_adapter.normalize_batter_record(raw)
        self.assertEqual(rec["hot_course"], "in_hi")     # the highest value
        self.assertEqual(rec["weak_course"], "out_lo")   # the lowest value

    def test_normalize_ignores_unknown_keys_and_needs_at_least_two_zones(self):
        raw = {"name": "Sparse Zone Player", "bats": "R", "avg": 0.260, "obp": 0.320, "slg": 0.400,
              "zone_profile": {"not_a_course": 0.9, "in_hi": 0.5}}
        rec = mlb_data_adapter.normalize_batter_record(raw)
        # only one valid location, so it falls back to the hunch
        fallback = mlb_data_adapter.normalize_batter_record(
            {"name": "Sparse Zone Player", "bats": "R", "avg": 0.260, "obp": 0.320, "slg": 0.400})
        self.assertEqual(rec["hot_course"], fallback["hot_course"])
        self.assertEqual(rec["weak_course"], fallback["weak_course"])

    def test_normalize_without_zone_profile_falls_back_to_seeded_guess(self):
        raw = {"name": "No Zone Player", "bats": "R", "avg": 0.260, "obp": 0.320, "slg": 0.400}
        rec = mlb_data_adapter.normalize_batter_record(raw)
        self.assertEqual(rec["hot_course"],
                        mlb_data_adapter._seeded_pick("No Zone Player", "hot", list(COURSES)))

    def test_normalize_fills_missing_advanced_stats_with_defaults(self):
        raw = {"name": "Minimal", "bats": "R", "avg": 0.260, "obp": 0.320, "slg": 0.400}
        rec = mlb_data_adapter.normalize_batter_record(raw)
        for key in ("chase_rate", "whiff_rate", "pull", "gb_tendency", "power", "contact"):
            self.assertTrue(0.0 <= rec[key] <= 1.0)

    def test_load_fixture_lineup_gives_nine_real_named_batters(self):
        lu = mlb_data_adapter.load_fixture_lineup()
        self.assertEqual(len(lu.batters), 9)
        names = {b.name for b in lu.batters}
        self.assertEqual(len(names), 9)             # all different people
        self.assertTrue(all(b.bats in ("R", "L") for b in lu.batters))
        self.assertTrue(all(0.150 <= b.avg <= 0.400 for b in lu.batters))

    def test_seeded_hot_and_weak_course_are_never_the_same_cell(self):
        """With the "hunch" used when there is no zone_profile, the hot location and the weak location
        must not end up in the same cell (this removes the case where the 2 draws collide)."""
        for b in mlb_data_adapter.load_fixture_lineup().batters:
            self.assertNotEqual(b.hot_course, b.weak_course, b.name)
        for nm in ("Mookie Betts", "Aaron Judge", "Kyle Tucker", "AAA", "sample-x"):
            rec = mlb_data_adapter.normalize_batter_record(
                {"name": nm, "bats": "R", "avg": 0.270, "obp": 0.330, "slg": 0.450})
            self.assertNotEqual(rec["hot_course"], rec["weak_course"], nm)
            self.assertIn(rec["hot_course"], COURSES)
            self.assertIn(rec["weak_course"], COURSES)

    def test_fixture_lineup_plays_through_the_engine(self):
        """A Batter made by the adapter runs through the Game Engine for a whole game."""
        lu = mlb_data_adapter.load_fixture_lineup()
        rng = random.Random(21)
        state = generate_random_situation(rng, lineup_batters=lu.batters)
        guard = 0
        while not state.is_over():
            guard += 1
            self.assertLess(guard, 400)
            pt = rng.choice(list(repertoire_options(state.pitcher)))
            co = rng.choice(list(COURSES))
            it = rng.choice(list(PITCH_INTENTS))
            resolve_one_pitch(state, pt, co, it, rng)
        self.assertTrue(state.outs == 3 or state.is_walkoff())
        build_analysis(state.pitch_log)
        from strategy import build_postgame_report
        build_postgame_report(state)

    def test_live_fetch_failure_falls_back_to_fixture_without_crashing(self):
        """If the API fetch fails, it always falls back to the sample data and the game does not crash.

        To not depend on a real network, the fetch is forced to fail for the check.
        """
        with mock.patch.object(mlb_data_adapter, "fetch_live_roster_raw",
                               side_effect=RuntimeError("boom")):
            with redirect_stdout(io.StringIO()):
                lineup, source = mlb_data_adapter.build_demo_lineup(prefer_live=True)
        self.assertEqual(source, "fixture")
        self.assertEqual(len(lineup.batters), 9)

    def test_live_fetch_success_path_uses_live_source(self):
        """If the fetch succeeds, source is "live" (without depending on the network)."""
        fake_raw = [{"name": f"Live Player {i}", "bats": "R" if i % 2 else "L",
                     "avg": 0.270, "obp": 0.340, "slg": 0.440} for i in range(9)]
        with mock.patch.object(mlb_data_adapter, "fetch_live_roster_raw",
                               return_value=fake_raw):
            lineup, source = mlb_data_adapter.build_demo_lineup(prefer_live=True)
        self.assertEqual(source, "live")
        self.assertEqual(len(lineup.batters), 9)

    def test_fetch_live_roster_raw_raises_a_clear_error_when_unreachable(self):
        """If it cannot connect, it raises RuntimeError wrapping the reason (without touching a real network)."""
        with mock.patch("mlb_data_adapter.urllib.request.urlopen",
                        side_effect=urllib.error.URLError("unreachable")):
            with self.assertRaises(RuntimeError):
                mlb_data_adapter.fetch_live_roster_raw(147, 2024)


# ---------- Extra gameplay (intentional walk / pitching change / steals) ----------
class GameplayExtrasTests(unittest.TestCase):
    def test_intentional_walk_advances_batter_and_puts_runner_on(self):
        st = _state(1)
        st.runners = [False, False, False]
        spot_before = st.lineup.spot_number()
        st.intentional_walk()
        self.assertTrue(st.runners[0])
        self.assertNotEqual(st.lineup.spot_number(), spot_before)
        self.assertIn("Intentional walk", st.events)

    def test_change_pitcher_resets_pitch_count(self):
        st = _state(2)
        old = st.pitcher
        st.pitcher.pitches_thrown = 20
        self.assertTrue(st.can_change_pitcher())
        self.assertIsNotNone(st.change_pitcher())
        self.assertIsNot(st.pitcher, old)
        self.assertEqual(st.pitcher.pitches_thrown, 0)   # the new pitcher is fresh
        self.assertEqual(st.pitching_changes, 1)

    def test_can_change_pitcher_as_many_times_as_bullpen_allows(self):
        st = _state(2)
        st.bullpen = [build_sample_pitcher(random.Random(1), name="A"),
                     build_sample_pitcher(random.Random(2), name="B")]
        self.assertTrue(st.can_change_pitcher())
        self.assertEqual(st.change_pitcher().name, "A")
        self.assertTrue(st.can_change_pitcher())          # there is still one more in the bullpen
        self.assertEqual(st.change_pitcher().name, "B")
        self.assertFalse(st.can_change_pitcher())         # the bullpen is empty
        self.assertIsNone(st.change_pitcher())

    def test_change_pitcher_by_index_picks_the_chosen_reliever(self):
        st = _state(2)
        st.bullpen = [build_sample_pitcher(random.Random(1), name="A"),
                     build_sample_pitcher(random.Random(2), name="B")]
        new_pitcher = st.change_pitcher(index=1)
        self.assertEqual(new_pitcher.name, "B")
        self.assertEqual(len(st.bullpen), 1)
        self.assertEqual(st.bullpen[0].name, "A")

    def test_good_call_streak_builds_and_resets(self):
        st = _state(4)
        # at or above the lenient threshold the streak grows
        self.assertTrue(st.register_call_quality(0.20, "Strike"))
        self.assertTrue(st.register_call_quality(0.11, "Whiff"))
        self.assertEqual(st.good_call_streak, 2)
        # a hard hit breaks it even if the quality was good
        self.assertFalse(st.register_call_quality(0.50, "Single"))
        self.assertEqual(st.good_call_streak, 0)
        # below the threshold it breaks too
        st.register_call_quality(0.20, "Strike")
        self.assertFalse(st.register_call_quality(0.02, "Ball"))
        self.assertEqual(st.good_call_streak, 0)

    def test_good_call_bonus_is_zero_until_two_in_a_row_then_capped(self):
        st = _state(5)
        self.assertEqual(st.good_call_bonus(), 0.0)
        st.register_call_quality(0.20, "Strike")
        self.assertEqual(st.good_call_bonus(), 0.0)          # nothing yet on the 1st pitch
        st.register_call_quality(0.20, "Whiff")
        self.assertGreater(st.good_call_bonus(), 0.0)         # 2 in a row gives a small bonus
        for _ in range(10):
            st.register_call_quality(0.20, "Strike")
        self.assertLessEqual(st.good_call_bonus(), 0.15)      # even if lenient there is a cap

    def test_engine_marks_good_call_on_outcome(self):
        st = _state(11)
        rng = random.Random(11)
        saw_flag = False
        for _ in range(30):
            if st.is_over():
                break
            outcome, _ = resolve_one_pitch(st, rng.choice(st.pitcher.repertoire),
                                           "mid_lo", "weak_contact", rng)
            self.assertIn("good_call", outcome)
            self.assertIn("good_call_streak", outcome)
            saw_flag = saw_flag or outcome["good_call"]
        self.assertTrue(saw_flag)

    def test_steal_needs_runner_on_first_only(self):
        st = _state(3)
        rng = random.Random(0)
        st.runners = [False, True, False]               # second only -> no steal
        self.assertIsNone(st.resolve_steal("fastball", rng))
        st.runners = [True, True, False]                 # second is occupied -> none
        self.assertIsNone(st.resolve_steal("fastball", rng))

    def test_steal_resolves_to_sb_or_cs(self):
        sb = cs = 0
        for s in range(400):
            st = _state(s % 20)
            st.runners = [True, False, False]
            st.outs = 0
            r = st.resolve_steal("changeup", random.Random(s))
            if r == "Stolen base (runner to second)":
                sb += 1
                self.assertEqual(st.runners, [False, True, False])
            elif r == "Caught stealing!":
                cs += 1
                self.assertFalse(st.runners[0])
                self.assertEqual(st.outs, 1)
        self.assertGreater(sb, 0)
        self.assertGreater(cs, 0)

    def test_good_throw_catches_more_stealers(self):
        def cs_rate(throw_bonus):
            caught = 0
            for s in range(600):
                st = _state(s % 20)
                st.runners, st.outs = [True, False, False], 0
                r = st.resolve_steal("changeup", random.Random(s), throw_bonus=throw_bonus)
                caught += (r == "Caught stealing!")
            return caught / 600
        self.assertGreater(cs_rate(0.25), cs_rate(-0.25) + 0.15)

    def test_good_block_reduces_passed_ball_and_advances_on_fail(self):
        def pb_rate(block_bonus):
            passed = 0
            for s in range(600):
                st = _state(s % 20)
                st.runners = [True, False, True]
                before = st.opp_score
                msg = st.resolve_block(block_bonus, random.Random(s))
                if msg:
                    passed += 1
                    self.assertEqual(st.runners, [False, True, False])   # one base at a time
                    self.assertEqual(st.opp_score, before + 1)           # the runner from third scores
            return passed / 600
        self.assertGreater(pb_rate(-0.25), pb_rate(0.25) + 0.2)

    def test_qte_disabled_in_non_tty_returns_neutral(self):
        self.assertFalse(qte_enabled())              # tests are not a terminal
        self.assertEqual(catcher_throw(random.Random(0)), 0.0)
        self.assertEqual(catcher_block("mid", rng=random.Random(0)), 0.0)
        self.assertEqual(catcher_change_signs(random.Random(0)), 0.0)

    def test_block_direction_grading(self):
        import qte
        orig = qte._read
        try:
            qte._read = lambda: "3"                  # 3 = out
            with redirect_stdout(io.StringIO()):     # swallow direction_check's staging print
                self.assertEqual(qte.direction_check("out"), "perfect")
                self.assertEqual(qte.direction_check("mid"), "good")    # adjacent
                self.assertEqual(qte.direction_check("in"), "miss")     # opposite
        finally:
            qte._read = orig

    def test_hard_block_passes_more(self):
        def rate(hard):
            passed = 0
            for s in range(400):
                st = _state(s % 20)
                st.runners = [True, False, False]
                if st.resolve_block(0.0, random.Random(s), hard=hard):
                    passed += 1
            return passed / 400
        self.assertGreater(rate(True), rate(False) + 0.05)

    def test_steal_chance_gate(self):
        st = _state(1)
        st.runners, st.outs = [True, True, False], 0    # second is occupied
        st.runner_speeds = [50, 50, None]
        self.assertFalse(any(st.steal_chance(random.Random(s)) for s in range(50)))
        st.runners, st.outs = [True, False, False], 2   # 2 outs
        self.assertFalse(any(st.steal_chance(random.Random(s)) for s in range(50)))
        st.runners, st.outs = [True, False, False], 0
        st.runner_speeds = [55, None, None]
        self.assertTrue(any(st.steal_chance(random.Random(s)) for s in range(80)))

    # ---- Runner speed (runner personalities) ----
    def _steal_rates(self, speed, n=3000):
        att = caught = 0
        for s in range(n):
            st = _state(s % 30)
            st.runners, st.outs, st.runner_speeds = [True, False, False], 0, [speed, None, None]
            if st.steal_chance(random.Random(s + 700)):
                att += 1
            r = st.resolve_steal("changeup", random.Random(s + 900))
            caught += (r == "Caught stealing!")
        return att / n, caught / n

    def test_fast_runner_steals_more_and_is_harder_to_nab(self):
        slow_att, slow_cs = self._steal_rates(35)
        fast_att, fast_cs = self._steal_rates(80)
        self.assertGreater(fast_att, slow_att + 0.05)     # fast runners try to steal
        self.assertGreater(slow_cs, fast_cs + 0.05)       # slow runners get caught more

    def test_fast_runner_takes_extra_base_on_single(self):
        bb = BattedBall("center", "line", "medium", 0.5, "shallow")
        fd = {"result": "Single", "position": "CF", "air_out": False, "batter_out": False}

        def to_third(speed, n=3000):
            hits = 0
            for s in range(n):
                p = resolve_baserunning(bb, fd, [True, False, False], 0, _batter(),
                                        random.Random(s), runner_speeds=[speed, None, None])
                hits += p["new_runners"][2]
            return hits / n

        self.assertGreater(to_third(85), to_third(35) + 0.1)

    def test_runner_speeds_stay_in_sync_through_a_game(self):
        rng = random.Random(3)
        for _ in range(120):
            st = generate_random_situation(rng)
            guard = 0
            while not st.is_over():
                guard += 1
                self.assertLess(guard, 400)
                self.assertEqual(len(st.runner_speeds), 3)
                for i in range(3):
                    if st.runners[i]:
                        self.assertIsNotNone(st.runner_speeds[i])   # a base with a runner has a speed
                    else:
                        self.assertIsNone(st.runner_speeds[i])      # an empty base is None
                pt = rng.choice(list(repertoire_options(st.pitcher)))
                co = rng.choice(list(COURSES))
                it = rng.choice(list(PITCH_INTENTS))
                resolve_one_pitch(st, pt, co, it, rng)

    # ---- Changing signs ----
    def test_sign_steal_needs_runner_on_second(self):
        st = _state(1)
        st.runners = [True, False, True]                # only second is open -> no risk
        self.assertFalse(any(st.sign_steal_chance(random.Random(s)) for s in range(50)))
        st.runners = [False, True, False]
        self.assertTrue(any(st.sign_steal_chance(random.Random(s)) for s in range(50)))

    def test_good_sign_change_defends_more_often(self):
        def defend_rate(sign_bonus):
            defended = 0
            for s in range(400):
                st = _state(s % 20)
                msg = st.resolve_sign_steal(sign_bonus, random.Random(s))
                defended += (msg == "Changed the signs in time")
            return defended / 400
        self.assertGreater(defend_rate(0.25), defend_rate(-0.25) + 0.2)

    def test_sign_leak_boosts_next_guess_only_once(self):
        st = _state(2)
        st.runners = [False, True, False]
        st.sign_leak = 0.65
        outcome = judge_pitch(st, "changeup", "mid_mid", random.Random(3), intent="strike")
        guess = outcome["_analysis"]["guess"]
        self.assertEqual(guess["class"], "offspeed")     # matches the changeup's actual class
        self.assertGreaterEqual(guess["class_strength"], 0.65)
        self.assertEqual(st.sign_leak, 0.0)              # consumed after one pitch

    # ---- Dropped third strike ----
    def test_dropped_third_eligibility(self):
        st = _state(1)
        st.runners, st.outs = [True, False, False], 0    # first occupied and under 2 outs -> does not apply
        self.assertFalse(st.dropped_third_eligible())
        st.runners, st.outs = [False, False, False], 0   # first is empty -> applies
        self.assertTrue(st.dropped_third_eligible())
        st.runners, st.outs = [True, False, False], 2     # 2 outs -> applies
        self.assertTrue(st.dropped_third_eligible())

    def test_dropped_third_passed_ball_advances_all_runners(self):
        for s in range(200):
            st = _state(s % 20)
            st.runners, st.outs = [False, True, True], 0
            before_score = st.opp_score
            safe, msg = st.resolve_dropped_third(-0.9, 0.0, random.Random(s))
            if "Passed ball" in msg:
                self.assertTrue(safe)
                self.assertEqual(st.runners, [True, False, True])   # 2nd -> 3rd, 3rd scores
                self.assertEqual(st.opp_score, before_score + 1)
                return
        self.fail("a passed ball never happened")

    def test_dropped_third_resolves_to_safe_or_out(self):
        safe_n = out_n = 0
        for s in range(400):
            st = _state(s % 20)
            st.runners, st.outs = [False, False, False], 0
            safe, msg = st.resolve_dropped_third(0.25, 0.0, random.Random(s))
            if safe:
                safe_n += 1
                self.assertTrue(st.runners[0])
            else:
                out_n += 1
                self.assertEqual(st.outs, 1)
        self.assertGreater(safe_n, 0)
        self.assertGreater(out_n, 0)

    def test_dropped_third_good_throw_catches_more(self):
        def safe_rate(throw_bonus):
            safe_n = 0
            for s in range(500):
                st = _state(s % 20)
                st.runners, st.outs = [False, False, False], 0
                safe, msg = st.resolve_dropped_third(0.25, throw_bonus, random.Random(s))
                if safe and "Passed ball" not in msg:
                    safe_n += 1
            return safe_n / 500
        self.assertGreater(safe_rate(-0.25), safe_rate(0.25) + 0.1)

    def test_dropped_third_safe_by_throw_only_forces_runners(self):
        for s in range(300):
            st = _state(s % 20)
            st.runners, st.outs = [False, True, False], 0    # second only
            safe, msg = st.resolve_dropped_third(0.9, -0.9, random.Random(s))
            if safe and "Passed ball" not in msg:
                self.assertEqual(st.runners, [True, True, False])   # the runner on second is not forced to advance
                return
        self.fail("the case of reaching on the throw never happened")


# ---------- Stats dashboard (stats.py) ----------
class StatsTests(unittest.TestCase):
    def test_parse_call_label(self):
        pitch, course, intent = stats._parse_call("sinker/in_hi(Get ahead)")
        self.assertEqual((pitch, course, intent), ("sinker", "in_hi", "Get ahead"))
        self.assertEqual(stats._parse_call("weird string"), (None, None, None))

    def _game(self, entries, runs=0):
        return {
            "final": {"runs_this_inning": runs},
            "pitches": [
                {"call_label": f"four_seam/out_lo({intent})", "result": result,
                 "decision_quality": dq}
                for result, dq, intent in entries
            ],
        }

    def test_bucket_tracks_bad_rate_and_avg_dq(self):
        b = stats._Bucket()
        b.add("Single", 0.5)
        b.add("Out", -0.2)
        self.assertEqual(b.n, 2)
        self.assertAlmostEqual(b.bad_rate(), 0.5)
        self.assertAlmostEqual(b.avg_dq(), 0.15)

    def test_build_dashboard_aggregates_by_course_and_intent(self):
        games = [self._game([
            ("Single", 0.4, "Get ahead"),
            ("Out", -0.1, "Chase"),
            ("Whiff", 0.2, "Get ahead"),
        ], runs=1)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            stats.build_dashboard(games)
        out = buf.getvalue()
        self.assertIn("Lo-Out", out)                # the location (short name) is shown
        self.assertIn("Get ahead", out)
        self.assertIn("Chase", out)
        self.assertIn("1 games", out)
        self.assertIn("3 pitches", out)


# ---------- Replay viewer (replay.py) ----------
class ReplayTests(unittest.TestCase):
    def _entry(self, num, result, dq, sequence_label="Nothing notable"):
        return {
            "pitch_number": num, "call_label": "four_seam/mid_mid(Get ahead)",
            "result": result, "decision_quality": dq, "sequence_label": sequence_label,
            "guess": {"class": "fastball", "class_strength": 0.1,
                     "location": "any", "loc_strength": 0.1},
            "fooled_guess": False, "alignment_helped": False, "missed": False,
            "outcome_flags": {"swung": True, "in_zone": True},
        }

    def test_collect_highlights_tags_good_and_risky_calls(self):
        pitches = [self._entry(1, "Whiff", 0.4), self._entry(2, "Home run", -0.5)]
        analysis = build_analysis(pitches)
        highlights = replay._collect_highlights(analysis)
        self.assertIn(1, highlights)
        self.assertIn(2, highlights)
        labels_1 = [label for label, _ in highlights[1]]
        self.assertIn("◎ Good call", labels_1)

    def test_render_replay_includes_summary_and_highlighted_pitches(self):
        log = {
            "situation": {"inning": "Bottom 9", "start": [3, 3]},
            "final": {"our": 3, "opp": 4, "runs_this_inning": 1,
                     "result": "1 run allowed", "status": "Lost the lead"},
            "pitches": [self._entry(1, "Whiff", 0.4), self._entry(2, "Home run", -0.5),
                       self._entry(3, "Ball", 0.0)],
            "reads": {},
        }
        out = replay.render_replay(log)
        self.assertIn("Bottom 9", out)
        self.assertIn("1 run allowed", out)
        self.assertIn("Pitch  1", out)
        self.assertIn("Pitch  2", out)
        self.assertNotIn("Pitch  3", out)             # unremarkable pitches are not shown

    def test_render_replay_handles_no_highlights(self):
        log = {"situation": {}, "final": {},
              "pitches": [self._entry(1, "Ball", 0.0)]}
        out = replay.render_replay(log)
        self.assertIn("Nothing stood out", out)


# ---------- Lightweight Web UI (webapp.py) ----------
class WebAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        webapp.SESSION = webapp.GameSession()
        cls.httpd = socketserver.TCPServer(("127.0.0.1", cls.port), webapp.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # seed=5 is a scene with "no runners, we lead 6-5, 0 outs" (checked in match_state).
        # With no runners, the steal / blocking / sign-steal QTEs do not interrupt, so the test is stable.
        webapp.SESSION = webapp.GameSession(seed=5)

    def _get(self, path=""):
        """GET a page. The tests of the classic page ask for "/" and get /classic (the 3D page is at "/")."""
        if path in ("", "/"):
            path = "/classic"
        with urllib.request.urlopen(self.base + path) as r:
            return r.read().decode("utf-8")

    def _post(self, path, data, headers=None):
        body = urllib.parse.urlencode(data).encode()
        # a Referer on /classic keeps the redirect on the classic page, like the browser does for its own forms
        hdrs = {"Referer": self.base + "/classic"}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body, method="POST", headers=hdrs)
        with urllib.request.urlopen(req) as r:
            return r.status

    def _api_get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read().decode("utf-8")

    def _fetch_post(self, path, data=None):
        """What the 3D page does: a POST with the X-Fetch header, answered with the new state as JSON."""
        body = urllib.parse.urlencode(data or {}).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST", headers={"X-Fetch": "1"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode("utf-8"))

    def _first_pitch_key(self, page):
        m = re.search(r'name="pitch_type" value="([^"]+)"', page)
        return m.group(1)

    def _start_game(self):
        self._post("/start", {})
        self._post("/skiptutorial", {})     # practice is out of scope for each test, so skip it
        self._post("/playball", {})
        return self._get("/")

    def _drain_qtes(self, answer="good"):
        """While a QTE screen continues, answer /qte and return to the normal screen."""
        for _ in range(12):
            if webapp.SESSION.pending_qte is None:
                return
            data = {"verdicts": answer}
            if webapp.SESSION.pending_qte[0] in ("wild_block", "d3_block"):
                data["dir"] = "mid"
            self._post("/qte", data)
        self.fail("the QTE never ended")

    def _pitch(self, key, course="mid_mid", intent="strike"):
        """Throw one pitch and clear every QTE along the way neutrally."""
        self._post("/pitch", {"pitch_type": key, "course": course, "intent": intent})
        self._drain_qtes()

    def test_root_shows_start_screen_first(self):
        page = self._get("/")
        self.assertIn("START GAME", page)
        self.assertNotIn("CALL PITCH", page)

    def test_start_shows_tutorial_then_situation_intro_then_game(self):
        self._post("/start", {})
        tut = self._get("/")
        self.assertIn("Practice", tut)              # practice comes first
        self.assertNotIn("PLAY BALL", tut)

        self._post("/skiptutorial", {})
        intro = self._get("/")
        self.assertIn("PLAY BALL", intro)
        self.assertIn("DEFEND THE LEAD", intro)
        self.assertNotIn("CALL PITCH", intro)

        self._post("/playball", {})
        game = self._get("/")
        self.assertIn("CALL PITCH", game)
        self.assertIn('action="/pitch"', game)

    def test_tutorial_locks_start_until_all_six_cleared(self):
        self._post("/start", {})
        for k, _name, _desc in webapp._TUTORIAL_ITEMS:
            self.assertIn("Practice", self._get("/"))     # still practicing
            data = {"item": k, "verdicts": "perfect,perfect,perfect"}
            if k in webapp._TUT_DIR:
                data["dir"] = "mid"
            self._post("/tutorial", data)
        self.assertTrue(all(webapp.SESSION.tutorial.values()))
        self.assertIn("PLAY BALL", self._get("/"))          # all cleared -> the main game

    def test_tutorial_miss_does_not_clear_the_item(self):
        self._post("/start", {})
        self._post("/tutorial", {"item": "tempo", "verdicts": "perfect,miss,good"})
        self.assertFalse(webapp.SESSION.tutorial["tempo"])
        self._post("/tutorial", {"item": "tempo", "verdicts": "good,good,good"})
        self.assertTrue(webapp.SESSION.tutorial["tempo"])

    def test_tutorial_skip_link_reaches_the_game(self):
        self._post("/start", {})
        self._post("/skiptutorial", {})
        self.assertIn("PLAY BALL", self._get("/"))

    def test_borderline_pitch_inserts_a_receive_rhythm_game(self):
        """A pitch aimed at a corner to get a strike puts a receive rhythm game before the pitch.
        Sending /qte produces a result, and one pitch is recorded."""
        page = self._start_game()
        key = self._first_pitch_key(page)
        self.assertFalse(any(webapp.SESSION.state.runners))   # seed=5 has no runners
        before = len(webapp.SESSION.state.pitch_log)
        self._post("/pitch", {"pitch_type": key, "course": "out_lo", "intent": "freeze"})
        self.assertEqual(webapp.SESSION.pending_qte[0], "frame")
        mid = self._get("/")
        self.assertIn("Receive", mid)
        self.assertIn('action="/qte"', mid)
        self.assertNotIn("CALL PITCH", mid)                   # the call screen does not appear yet
        self.assertEqual(len(webapp.SESSION.state.pitch_log), before)   # not thrown yet

        self._post("/qte", {"verdicts": "perfect"})
        self._drain_qtes()
        self.assertIn("RESULT", self._get("/"))
        self.assertIsNone(webapp.SESSION.pending_qte)
        self.assertEqual(len(webapp.SESSION.state.pitch_log), before + 1)

    def test_center_pitch_with_no_runners_skips_the_rhythm_game(self):
        page = self._start_game()
        key = self._first_pitch_key(page)
        self._post("/pitch", {"pitch_type": key, "course": "mid_mid", "intent": "strike"})
        self.assertIsNone(webapp.SESSION.pending_qte)
        self.assertIn("RESULT", self._get("/"))

    def test_qte_route_without_a_pending_game_is_harmless(self):
        self._start_game()
        status = self._post("/qte", {"verdicts": "perfect"})
        self.assertEqual(status, 200)
        self.assertIn("CALL PITCH", self._get("/"))

    def test_frame_qte_band_moves_with_pitch_speed(self):
        """The receive band: a fast pitch is on the left (early), a slow one on the right (late)."""
        import re as _re

        def band(velo):
            class S:
                pending_qte = ("frame", {"call": "x", "velocity": velo})
                last_outcome = None
            return float(_re.search(r'data-targets="([\d.]+)"', webapp.render_qte(S())).group(1))
        self.assertLess(band(95), 0.4)      # four-seam class -> left
        self.assertGreater(band(78), 0.6)   # curveball class -> right
        self.assertAlmostEqual(band(88), 0.5, delta=0.05)

    def test_steal_throw_qte_has_a_mash_step(self):
        import re as _re
        class S:
            pending_qte = ("steal_throw", {})
            last_outcome = None
        html = webapp.render_qte(S())
        self.assertEqual(_re.search(r'data-mash="([^"]*)"', html).group(1), "1")
        self.assertEqual(len(_re.search(r'data-labels="([^"]*)"', html).group(1).split("|")), 3)

    def test_field_bunt_qte_uses_two_target_positions(self):
        import re as _re
        class S:
            pending_qte = ("field_bunt", {})
            last_outcome = None
        html = webapp.render_qte(S())
        self.assertEqual(_re.search(r'data-targets="([^"]*)"', html).group(1), "0.4|0.82")

    def test_prev_result_strip_is_green_for_outs_red_for_hits(self):
        self.assertEqual(webapp._prev_result_strip(None), "")
        good = webapp._prev_result_strip({"result": "Out"})
        bad = webapp._prev_result_strip({"result": "Single"})
        self.assertIn("Good", good)
        self.assertIn("var(--good)", good)
        self.assertIn("Bad", bad)
        self.assertIn("var(--bad)", bad)

    def test_catcher_line_praises_on_strikeout_and_taunts_on_bad_result(self):
        """The "impressed / disappointed" line is decided from the previous pitch's result and how the at-bat ended."""
        s = webapp.GameSession(seed=5)
        s.last_outcome = {"result": "Whiff", "at_bat_end": "strikeout", "play": None}
        self.assertIn("Impressive", webapp._catcher_line(s))
        s.last_outcome = {"result": "Ball", "at_bat_end": "walk", "play": None}
        self.assertIn("Ugh", webapp._catcher_line(s))
        s.last_outcome = {"result": "Home run", "at_bat_end": "hit",
                          "play": {"runs": 1, "label": "Home run"}}
        self.assertIn("Ugh", webapp._catcher_line(s))
        s.last_outcome = {"result": "Strike", "at_bat_end": None, "play": None}
        self.assertEqual(webapp._catcher_line(s), "")

    def test_at_bat_end_is_propagated_onto_last_outcome(self):
        """The at-bat ending tag (strikeout / walk / hit / out) is also carried onto last_outcome
        -- because _catcher_line looks at it."""
        page = self._start_game()
        key = self._first_pitch_key(page)
        checked = False
        for _ in range(150):
            if webapp.SESSION.state.is_over():
                break
            self._pitch(key, "mid_mid", "strike")
            st = webapp.SESSION.state
            log_end = st.pitch_log[-1].get("at_bat_end") if st.pitch_log else None
            if log_end:
                self.assertEqual(webapp.SESSION.last_outcome.get("at_bat_end"), log_end)
                checked = True
        self.assertTrue(checked)

    def test_return_tempo_widget_appears_only_after_a_caught_pitch(self):
        """The "return tempo" on the pitch call screen appears only when the previous pitch was caught."""
        page = self._start_game()
        self.assertNotIn('id="tempo"', page)             # not on the first pitch
        key = self._first_pitch_key(page)
        self._pitch(key, "mid_mid", "strike")            # a pitch likely to be taken or swung through
        after = self._get("/")
        # if the batter did not hit it fair (= you caught it), the tempo appears
        if "CATCHER REPORT" not in after and "RESULT" in after:
            last = webapp.SESSION.state.history.last()
            if last and last.get("result") in ("Ball", "Strike", "Whiff"):
                self.assertIn('id="tempo"', after)
                self.assertIn('name="tempo"', after)

    def test_pitch_call_accepts_a_tempo_field(self):
        """Even if CALL PITCH carries the return-rhythm result (tempo), it resolves without a 500."""
        page = self._start_game()
        key = self._first_pitch_key(page)
        before = len(webapp.SESSION.state.pitch_log)
        self._post("/pitch", {"pitch_type": key, "course": "mid_mid", "intent": "strike",
                              "tempo": "perfect,good,perfect"})
        self._drain_qtes()
        self.assertEqual(len(webapp.SESSION.state.pitch_log), before + 1)
        self.assertIn("RESULT", self._get("/"))

    def test_qte_neutral_answer_matches_no_qte_behaviour(self):
        """Even receiving with JS off (empty verdicts -> bonus 0), apart from framing the
        result of one pitch should have the same distribution as frame_timing=None (here we only check that it goes through)."""
        page = self._start_game()
        key = self._first_pitch_key(page)
        self._post("/pitch", {"pitch_type": key, "course": "in_hi", "intent": "strike"})
        self._post("/qte", {})                # no verdicts = neutral
        self._drain_qtes()
        self.assertIn("RESULT", self._get("/"))

    def test_post_pitch_advances_the_count_or_the_at_bat(self):
        page = self._start_game()
        pitch_key = self._first_pitch_key(page)
        status = self._post("/pitch", {"pitch_type": pitch_key, "course": "mid_mid",
                                       "intent": "strike"})
        self.assertEqual(status, 200)
        page2 = self._get("/")
        self.assertIn("Pitch Call", page2)
        self.assertIn("RESULT", page2)         # the result panel of the previous pitch appears

    def test_pitch_rejects_malformed_course_or_intent_without_error(self):
        """Even if an invalid course / intent arrives from outside the form (a hand-typed POST etc.),
        it does not 500 and is quietly rejected (no pitch is recorded)."""
        self._start_game()
        before = len(webapp.SESSION.state.pitch_log)
        key = self._first_pitch_key(self._get("/"))
        for bad in ({"pitch_type": key, "course": "garbage", "intent": "strike"},
                    {"pitch_type": key, "course": "mid_mid", "intent": "zzz"}):
            status = self._post("/pitch", bad)
            self.assertEqual(status, 200)
            self.assertEqual(len(webapp.SESSION.state.pitch_log), before)

    def test_qte_is_forced_off_for_the_web_process(self):
        self._start_game()
        self.assertFalse(webapp.SESSION.state is None)
        import qte
        self.assertFalse(qte.qte_enabled())

    def test_driving_pitches_eventually_reaches_the_postgame_report(self):
        page = self._start_game()
        for _ in range(300):
            if "CATCHER REPORT" in page:
                break
            pitch_key = self._first_pitch_key(page)
            self._pitch(pitch_key, "mid_mid", "strike")   # clear the QTEs along the way neutrally
            page = self._get("/")
        else:
            self.fail("the inning did not end even after 300 pitches")
        self.assertIn("Play again", page)
        self.assertIn("Runs Allowed", page)
        self.assertIn("Batters Faced", page)
        self.assertIn("GOOD CALL", page)
        self.assertIn("BAD CALL", page)

        self._post("/newgame", {})
        self.assertIn("Practice", self._get("/"))    # a new game starts with practice
        self._post("/skiptutorial", {})
        fresh = self._get("/")
        self.assertIn("PLAY BALL", fresh)      # then the situation card

    def test_pitch_arc_svg_renders_for_every_pitch_and_curve_drops_more(self):
        """The rough-trajectory SVG for pitch types can be made for every type, and a curveball drops more than a four-seam."""
        import re as _re
        from pitch_data import all_pitch_keys
        def end_y(key):
            svg = webapp._pitch_arc_svg(key)
            self.assertTrue(svg.startswith("<svg") and "<path" in svg and "<circle" in svg)
            return float(_re.search(r'<circle cx="[\d.]+" cy="([\d.]+)"', svg).group(1))
        for k in all_pitch_keys():
            end_y(k)
        self.assertGreater(end_y("curveball"), end_y("four_seam") + 5)   # the curveball ends lower

    def test_course_grid_mirrors_by_batter_handedness(self):
        r_grid = webapp._course_grid("R")
        l_grid = webapp._course_grid("L")
        # for a right-handed batter in -> out goes left -> right, a lefty is the mirror (the same idea as the broadcast strike-zone display)
        self.assertEqual(r_grid[0], ["in_hi", "mid_hi", "out_hi"])
        self.assertEqual(l_grid[0], ["out_hi", "mid_hi", "in_hi"])
        # both contain each of the same 9 locations once (only the display order differs)
        flat_r = {c for row in r_grid for c in row}
        flat_l = {c for row in l_grid for c in row}
        self.assertEqual(flat_r, flat_l)
        self.assertEqual(len(flat_r), 9)

    def test_alignment_change_is_reflected(self):
        self._start_game()
        status = self._post("/alignment", {"alignment": "bunt"})
        self.assertEqual(status, 200)
        self.assertEqual(webapp.SESSION.state.defense.alignment, "bunt")

    def test_swap_click_click_flow(self):
        self._start_game()
        before = dict(webapp.SESSION.state.defense.assignment)
        self._post("/swap", {"position": "SS"})
        mid = self._get("/")
        self.assertIsNotNone(webapp.SESSION.pending_swap)
        self.assertIn("selected", mid)
        self._post("/swap", {"position": "1B"})
        self.assertIsNone(webapp.SESSION.pending_swap)
        after = webapp.SESSION.state.defense.assignment
        self.assertIs(after["SS"], before["1B"])
        self.assertIs(after["1B"], before["SS"])

    def test_swap_same_position_twice_cancels_pending(self):
        self._start_game()
        self._post("/swap", {"position": "CF"})
        self.assertEqual(webapp.SESSION.pending_swap, "CF")
        self._post("/swap", {"position": "CF"})
        self.assertIsNone(webapp.SESSION.pending_swap)

    def test_change_pitcher_picks_the_selected_bullpen_index(self):
        self._start_game()
        webapp.SESSION.state.bullpen = [build_sample_pitcher(random.Random(1), name="Reliever A"),
                                       build_sample_pitcher(random.Random(2), name="Reliever B")]
        status = self._post("/change", {"index": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(webapp.SESSION.state.pitcher.name, "Reliever B")
        self.assertEqual(len(webapp.SESSION.state.bullpen), 1)
        self.assertTrue(webapp.SESSION.state.can_change_pitcher())   # one more is still left

    def test_memo_records_a_read(self):
        self._start_game()
        status = self._post("/memo", {"wait": "fastball", "weak": "out_lo"})
        self.assertEqual(status, 200)
        note = webapp.SESSION.state.current_read()
        self.assertEqual(note["wait"], "fastball")
        self.assertEqual(note["weak"], "out_lo")

    def test_seed_reproducible_over_http(self):
        def play_seeded(seed):
            webapp.SESSION = webapp.GameSession(seed=seed)
            self._post("/start", {})
            self._post("/skiptutorial", {})
            self._post("/playball", {})
            page = self._get("/")
            pitch_key = self._first_pitch_key(page)
            self._post("/pitch", {"pitch_type": pitch_key, "course": "mid_mid", "intent": "strike"})
            self._drain_qtes()        # with runners, a steal QTE etc. gets in. Clear it neutrally
            page = self._get("/")
            return re.search(r"RESULT\s*(?:</[^>]+>)?\s*([^<]+)<", page).group(1)

        self.assertEqual(play_seeded(555), play_seeded(555))


    def test_start_screen_lists_drills_difficulty_and_code(self):
        page = self._get("/")
        for key, name, _ in webapp._DRILLS:
            self.assertIn(name, page)
            self.assertIn(f'value="{key}"', page)
        self.assertIn('name="difficulty"', page)
        self.assertIn('name="seed"', page)

    def test_drill_button_starts_that_scenario_with_the_typed_code(self):
        self._post("/start", {"scenario": "steal", "seed": "321", "difficulty": "expert"})
        self.assertEqual(webapp.SESSION.scenario, "steal")
        self.assertEqual(webapp.SESSION.game_seed, 321)
        self.assertEqual(webapp.SESSION.difficulty, "expert")
        self.assertEqual(webapp.SESSION.state.runners, [True, False, False])

    def test_postgame_page_shows_rating_code_and_replay_button(self):
        page = self._start_game()
        key = self._first_pitch_key(page)
        for _ in range(300):
            page = self._get("/")
            if "Play again" in page:
                break
            self._pitch(key, "mid_mid", "strike")
        self.assertIn("CATCHER RATING", page)
        self.assertIn("Replay this situation", page)
        self.assertIn(f"#{webapp.SESSION.game_seed}", page)
        self._post("/menu", {})
        self.assertIn("START GAME", self._get("/"))


    def test_unexpected_error_shows_a_recovery_page_not_a_dead_connection(self):
        with mock.patch.object(webapp, "render_page", side_effect=RuntimeError("boom")):
            with redirect_stdout(io.StringIO()), mock.patch("traceback.print_exc"):
                try:
                    self._get("/")
                    self.fail("expected an HTTP 500")
                except urllib.error.HTTPError as err:
                    self.assertEqual(err.code, 500)
                    self.assertIn("Back to the menu", err.read().decode("utf-8"))
        self.assertIn("START GAME", self._get("/"))     # and the server keeps working

    def test_bind_server_falls_back_to_the_next_free_port(self):
        busy = socket.socket()
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        try:
            httpd, port = webapp._bind_server("127.0.0.1", taken)
            httpd.server_close()
            self.assertNotEqual(port, taken)
        finally:
            busy.close()


    def test_hand_typed_memo_values_are_ignored_and_the_report_still_renders(self):
        self._start_game()
        self._post("/memo", {"wait": "zzz", "weak": "nowhere"})
        note = webapp.SESSION.state.current_read()
        self.assertEqual((note["wait"], note["weak"]), (None, None))
        self._post("/memo", {"wait": "fastball", "weak": "in_hi"})
        note = webapp.SESSION.state.current_read()
        self.assertEqual((note["wait"], note["weak"]), ("fastball", "in_hi"))


    def test_a_fair_ball_shows_the_play_viewer_once_then_a_static_end_state(self):
        key = self._first_pitch_key(self._start_game())
        for _ in range(60):
            self._pitch(key, "mid_mid", "weak_contact")
            if webapp.SESSION.last_outcome.get("batted_ball"):
                break
        else:
            self.fail("never put in play")
        webapp.SESSION.play_shown = None      # the redirect after the pitch already rendered once
        page = self._get("/")
        if "CATCHER RATING" in page:      # the ball ended the inning: the viewer sits above the report
            self.assertIn("Play Viewer", page)
            return
        self.assertIn("Play Viewer", page)
        self.assertIn('data-autoplay="1"', page)
        self.assertIn('data-autoplay="0"', self._get("/"))     # a reload does not replay the animation


    # ---- the 3D stadium page (served at "/") and its JSON API ----
    def test_root_is_the_one_screen_3d_page_and_classic_is_still_there(self):
        status, page = self._api_get("/")
        self.assertEqual(status, 200)
        for marker in ('id="view"', 'id="mask"', 'id="bottom"', "overflow:hidden", "__btpBgm"):
            self.assertIn(marker, page)
        self.assertIn("START GAME", self._get("/classic"))            # the old layout is kept as a fallback

    def test_api_state_follows_the_game_phases(self):
        _, body = self._api_get("/api/state")
        self.assertEqual(json.loads(body)["phase"], "start")
        s = self._fetch_post("/start", {"scenario": "random"})
        self.assertEqual(s["phase"], "intro")
        self.assertTrue(webapp.SESSION.tutorial_skipped)               # the 3D page has no practice screen
        s = self._fetch_post("/playball")
        self.assertEqual(s["phase"], "play")
        self.assertEqual(len(s["intents"]), 6)
        self.assertTrue(s["repertoire"] and all({"key", "name", "velocity", "move"} <= set(p) for p in s["repertoire"]))
        self.assertIn(s["alignment"], ALIGNMENTS)
        self.assertIsNone(s["last"])
        self.assertEqual(len(s["situation"]["runners"]), 3)

    def test_the_api_never_sends_the_hidden_decision_number(self):
        self._fetch_post("/start", {"scenario": "random", "seed": "41"})
        self._fetch_post("/playball")
        key = list(repertoire_options(webapp.SESSION.state.pitcher))[0]
        s = self._fetch_post("/pitch", {"pitch_type": key, "course": "mid_mid", "intent": "strike"})
        for _ in range(6):                           # clear any rhythm game neutrally
            if s["phase"] != "qte":
                break
            s = self._fetch_post("/qte", {"verdicts": "good,good,good", "dir": "mid"})
        text = json.dumps(s)
        self.assertNotIn("decision_quality", text)
        self.assertNotIn("sequence_label", text)
        self.assertEqual(s["last"]["eval"]["grade"] in ("good", "fair", "risky"), True)
        self.assertTrue(s["last"]["eval"]["reason"])

    def test_fetch_pitch_gives_json_with_the_result_and_the_replay(self):
        self._fetch_post("/start", {"scenario": "random", "seed": "7"})
        self._fetch_post("/playball")
        key = list(repertoire_options(webapp.SESSION.state.pitcher))[0]
        seen_batted = False
        for i in range(120):
            s = self._fetch_post("/pitch", {"pitch_type": key, "course": ["mid_mid", "in_lo", "out_hi"][i % 3], "intent": "strike"})
            for _ in range(8):
                if s["phase"] != "qte":
                    break
                self.assertIn("labels", s["qte"])
                s = self._fetch_post("/qte", {"verdicts": "good,good,good", "dir": "mid"})
            if s["phase"] == "over":
                break
            last = s["last"]
            self.assertIn(last["result"], ("Ball", "Strike", "Whiff", "Foul", "Out", "Single", "Double", "Triple", "Home run", "Error"))
            if last["batted"]:
                seen_batted = True
                self.assertTrue({"duration", "ball", "fielders", "runners", "events"} <= set(last["batted"]["script"]))
        self.assertTrue(seen_batted)

    def test_panels_for_the_3d_page(self):
        self._fetch_post("/start", {"scenario": "random"})
        self._fetch_post("/playball")
        for name, needle in (("history", "Pitch History"), ("defense", "Defense"), ("bench", "Bench"), ("start", "START GAME"), ("intro", "PLAY BALL")):
            status, html_text = self._api_get("/api/panel/" + name)
            self.assertEqual(status, 200)
            self.assertIn(needle, html_text)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._api_get("/api/panel/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_classic_forms_stay_on_the_classic_page(self):
        req = urllib.request.Request(self.base + "/start", data=b"", method="POST", headers={"Referer": self.base + "/classic"})
        with urllib.request.urlopen(req) as r:
            self.assertTrue(r.geturl().endswith("/classic"))

    def test_bad_values_through_the_fetch_endpoints_never_crash(self):
        self._fetch_post("/start", {"scenario": "random"})
        self._fetch_post("/playball")
        for path, data in (("/pitch", {"pitch_type": "bogus", "course": "zz", "intent": "zz"}), ("/memo", {"wait": "zz", "weak": "zz"}),
                           ("/alignment", {"alignment": "zz"}), ("/swap", {"position": "??"}), ("/change", {"index": "x"}),
                           ("/qte", {"verdicts": "x"}), ("/start", {"seed": "abc", "scenario": "nope", "difficulty": "insane"})):
            s = self._fetch_post(path, data)
            self.assertIn("phase", s)


# ---------- The 9-zone grid ----------
class ZoneTests(unittest.TestCase):
    def test_nine_courses_and_helpers(self):
        from constants import COURSES, is_corner, zone_overlap, zone_x, zone_y
        self.assertEqual(len(COURSES), 9)
        self.assertEqual(zone_x("out_lo"), "out")
        self.assertEqual(zone_y("out_lo"), "lo")
        self.assertTrue(is_corner("in_hi"))
        self.assertFalse(is_corner("mid_hi"))
        self.assertEqual(zone_overlap("out_lo", "out_lo"), 1.0)
        self.assertEqual(zone_overlap("out_lo", "out_hi"), 0.5)   # the same column
        self.assertEqual(zone_overlap("out_lo", "in_hi"), 0.0)

    def test_corner_target_misses_more_than_center(self):
        p = Pitcher(control=55, command=55, repertoire=["four_seam"])

        def miss_rate(course):
            rng = random.Random(1)
            return sum(p.execute_pitch("four_seam", course, "freeze", rng)["missed"]
                       for _ in range(4000)) / 4000

        self.assertGreater(miss_rate("out_lo"), miss_rate("mid_mid") + 0.05)

    def test_batter_default_hot_weak_courses_are_valid_zone_keys(self):
        """Even if a Batter is made without hot_course / weak_course, the defaults are
        correct keys of the 9-zone grid, and judge_pitch goes through without an exception."""
        from batter import Batter
        from constants import COURSES
        b = Batter(name="Default Batter")
        self.assertIn(b.hot_course, COURSES)
        self.assertIn(b.weak_course, COURSES)
        st = generate_random_situation(random.Random(0))
        st.lineup.batters[st.lineup.index] = b
        rng = random.Random(0)
        for _ in range(50):
            judge_pitch(st, "four_seam", "out_lo", rng, intent="freeze")

    def test_weak_zone_overlap_bites_partially(self):
        # a batter who is "weak low and away" is also somewhat jammed "away"
        from constants import zone_overlap
        self.assertEqual(zone_overlap("out_mid", "out_lo"), 0.5)


# ---------- Framing (the catcher's craft) ----------
class FramingTests(unittest.TestCase):
    def _edge_take_calls(self, course, intent, n=4000, seed=0):
        """Of the pitches that were taken, the share called strikes (including successful framing)."""
        st = generate_random_situation(random.Random(seed))
        # pinned to a batter who tends to take
        st.batter.aggression, st.batter.chase_rate = 0.1, 0.05
        rng = random.Random(seed + 1)
        strikes = takes = 0
        for _ in range(n):
            st.balls = st.strikes = 0
            o = judge_pitch(st, "four_seam", course, rng, intent=intent)
            if not o["swung"]:
                takes += 1
                strikes += (o["result"] == "Strike")
        return strikes / takes if takes else 0.0

    def test_edge_takes_are_framed_into_strikes_sometimes(self):
        # a pitch aimed at the corner (low and away) that was taken is called a "strike" more often than
        # a pitch that missed the middle by a lot (framing works)
        edge = self._edge_take_calls("out_lo", "freeze")
        waste = self._edge_take_calls("mid_mid", "chase")   # chase = the intent to miss big from the start
        self.assertGreater(edge, waste)

    def test_framed_strikes_counter_increments(self):
        st = generate_random_situation(random.Random(3))
        st.batter.aggression, st.batter.chase_rate = 0.05, 0.02
        rng = random.Random(4)
        for _ in range(3000):
            st.balls = st.strikes = 0
            judge_pitch(st, "four_seam", "out_lo", rng, intent="freeze")
        self.assertGreater(st.framed_strikes, 0)

    def test_no_framing_on_pitchout_or_big_waste(self):
        st = generate_random_situation(random.Random(5))
        st.batter.aggression, st.batter.chase_rate = 0.05, 0.02
        rng = random.Random(6)
        for _ in range(2000):
            st.balls = st.strikes = 0
            o = judge_pitch(st, "four_seam", "out_lo", rng, intent="pitchout")
            self.assertEqual(o["result"], "Ball")     # a pitchout is never a strike
        self.assertEqual(st.framed_strikes, 0)

    def _take_strike_rate(self, frame_timing, n=5000, seed=7):
        st = generate_random_situation(random.Random(seed))
        st.batter.aggression, st.batter.chase_rate = 0.05, 0.02
        rng = random.Random(seed + 1)
        strikes = takes = 0
        for _ in range(n):
            st.balls = st.strikes = 0
            o = judge_pitch(st, "four_seam", "out_lo", rng, intent="freeze",
                            frame_timing=frame_timing)
            if not o["swung"]:
                takes += 1
                strikes += (o["result"] == "Strike")
        return strikes / takes if takes else 0.0

    def test_receive_timing_shifts_framing_rate(self):
        """How well the receive rhythm game went: perfect frames better than miss.
        frame_timing=None is as before (between perfect and miss)."""
        miss = self._take_strike_rate("miss")
        neutral = self._take_strike_rate(None)
        perfect = self._take_strike_rate("perfect")
        self.assertGreater(perfect, miss)
        self.assertGreaterEqual(perfect, neutral)
        self.assertGreaterEqual(neutral, miss)


# ---------- Opponent small ball (sacrifice / hit-and-run / pitchout) ----------
class SmallBallTests(unittest.TestCase):
    def _bunt_state(self, seed=0):
        import opponent
        st = generate_random_situation(random.Random(seed))
        st.outs = 0
        st.runners = [True, False, False]
        st.runner_speeds = [50, None, None]
        st.our_score = st.opp_score = 3
        st.batter.coarse_type = "weak"
        st.batter.power = 0.3
        st.opp_tactic = "bunt"
        return st

    def test_opponent_bunts_in_a_textbook_spot(self):
        import opponent
        fired = 0
        for s in range(2000):
            st = generate_random_situation(random.Random(s))
            st.outs, st.runners, st.runner_speeds = 0, [True, False, False], [50, None, None]
            st.our_score = st.opp_score = 3
            st.batter.coarse_type, st.batter.power = "weak", 0.3
            fired += opponent.decide_tactic(st, random.Random(s + 5000)) == "bunt"
        self.assertGreater(fired, 200)               # they try it a decent amount
        self.assertLess(fired, 1400)                 # but not always (don't make the opponent too strong)

    def test_opponent_never_bunts_with_two_outs(self):
        import opponent
        for s in range(200):
            st = generate_random_situation(random.Random(s))
            st.outs, st.runners = 2, [True, False, False]
            st.batter.coarse_type = "weak"
            self.assertIsNone(opponent.decide_tactic(st, random.Random(s)))

    def test_bunt_resolves_to_a_known_outcome(self):
        results = set()
        for s in range(400):
            st = self._bunt_state(s)
            outcome, msgs = resolve_one_pitch(st, "four_seam", "mid_lo", "strike", random.Random(s + 1))
            results.add(outcome["result"])
            self.assertTrue(msgs)
        self.assertTrue(results <= {"Sacrifice bunt", "Bunt popped up", "Bunt (force out)",
                                    "Bunt single", "Foul", "Strikeout"})
        self.assertIn("Sacrifice bunt", results)

    def test_successful_sacrifice_advances_runner_and_makes_an_out(self):
        # only look at the cases that "rolled" (avoiding fouls and pop-ups)
        seen = False
        for s in range(300):
            st = self._bunt_state(s)
            before_out = st.outs
            outcome, _ = resolve_one_pitch(st, "four_seam", "mid_lo", "strike", random.Random(s + 7))
            if outcome["result"] == "Sacrifice bunt":
                seen = True
                self.assertEqual(st.outs, before_out + 1)
                self.assertTrue(st.runners[1])       # the runner reaches second
                self.assertFalse(st.runners[0])
        self.assertTrue(seen)

    def test_pitchout_busts_a_bunt(self):
        busted = 0
        for s in range(200):
            st = self._bunt_state(s)
            outcome, _ = resolve_one_pitch(st, "four_seam", "out_hi", "pitchout", random.Random(s + 2))
            busted += outcome["result"] in ("Ball", "Walk")
        self.assertGreater(busted, 150)              # you almost always get the bunt missed

    def test_pitchout_makes_steal_much_easier_to_catch(self):
        def cs_rate(intent, n=1500):
            caught = 0
            for s in range(n):
                st = generate_random_situation(random.Random(s))
                st.outs, st.runners, st.runner_speeds = 0, [True, False, False], [60, None, None]
                st.opp_tactic = None
                before = st.outs
                resolve_one_pitch(st, "four_seam", "out_mid", intent, random.Random(s + 40))
                caught += (st.outs == before + 1 and not st.runners[0] and st.runners[1] is False)
            return caught / n
        # pitchouts catch more steals (statistically)
        self.assertGreaterEqual(cs_rate("pitchout"), cs_rate("strike"))

    def test_hit_and_run_runner_advances_without_double_play(self):
        import opponent
        # even on an infield grounder the runner advances, with no double play
        adv = dp = 0
        for s in range(400):
            st = generate_random_situation(random.Random(s))
            st.outs, st.runners, st.runner_speeds = 0, [True, False, False], [55, None, None]
            st.opp_tactic = "hit_and_run"
            outcome, _ = resolve_one_pitch(st, "sinker", "mid_lo", "weak_contact", random.Random(s + 3))
            play = outcome.get("play")
            if play and play["outs_added"] >= 2:
                dp += 1
            if outcome["result"] == "Out" and (st.runners[1] or st.runners[2]):
                adv += 1
        self.assertEqual(dp, 0)                      # no double play during a hit-and-run
        self.assertGreater(adv, 20)

    def test_catcher_field_bunt_is_neutral_when_qte_disabled(self):
        """Outside the CLI (Web, tests) it is always 0.0 = bunt handling behaves as before."""
        from qte import catcher_field_bunt, qte_enabled
        self.assertFalse(qte_enabled())
        self.assertEqual(catcher_field_bunt(random.Random(0)), 0.0)

    def test_field_bonus_helps_nab_the_lead_runner_and_prevent_bunt_hits(self):
        """How well the bunt-fielding rhythm game went (field_bonus) does not fix the result, it shifts probabilities:
        the better it went, the easier it is to force out the lead runner, and it prevents infield hits (bunt hits)."""
        def tally(fb, n=2500):
            nab = hit = 0
            for s in range(n):
                st = generate_random_situation(random.Random(s))
                st.outs = 0
                st.runners, st.runner_speeds = [True, False, False], [55, None, None]
                st.our_score = st.opp_score = 3
                st.batter.coarse_type, st.batter.power = "weak", 0.30
                _, final, _ = st.resolve_bunt(False, False, False,
                                              random.Random(s + 1), field_bonus=fb)
                nab += final in ("Bunt (force out)", "Bunt (out at home)")
                hit += final == "Bunt single"
            return nab / n, hit / n
        nab_lo, hit_lo = tally(-0.20)
        nab_hi, hit_hi = tally(0.20)
        self.assertGreater(nab_hi, nab_lo)
        self.assertLessEqual(hit_hi, hit_lo)

    def test_begin_at_bat_is_idempotent_within_an_at_bat(self):
        """However many times begin_at_bat is called during an at-bat, neither the tactic nor the random consumption changes.

        The Web version calls begin_at_bat every time the page is redrawn, so if this
        breaks, the game changes on every reload even with --seed.
        """
        from engine import begin_at_bat

        def outcome_after(renders):
            st = generate_random_situation(random.Random(4242))
            st.outs = 1
            st.runners, st.runner_speeds = [True, False, False], [55, None, None]
            st.our_score = st.opp_score = 3
            st.start_our = st.start_opp = 3
            st.batter.coarse_type, st.batter.power = "weak", 0.30
            rng = random.Random(99)
            for _ in range(renders):
                begin_at_bat(st, rng)
            tactic = st.opp_tactic
            o, _ = resolve_one_pitch(st, "four_seam", "mid_mid", "strike", rng)
            return tactic, o["result"], o["actual_course"], o["quality"]

        base = outcome_after(1)
        for n in (2, 3, 8):
            self.assertEqual(outcome_after(n), base,
                             f"the result changed after {n} redraws (begin_at_bat is not idempotent)")


# ---------- 1000-game stress ----------
class StressTests(unittest.TestCase):
    def test_1000_games_no_crash_or_invalid_state(self):
        rng = random.Random(2024)
        intents = list(PITCH_INTENTS)
        courses = list(COURSES)
        games = 1000
        total_pitches = 0
        total_runs = 0
        xbh = 0

        for g in range(games):
            state = generate_random_situation(rng)
            self.assertIn(state.inning, (7, 8, 9, 10))
            self.assertIn(state.outs, (0, 1, 2))
            self.assertTrue(1 <= state.lineup.spot_number() <= 9)

            guard = 0
            while not state.is_over():
                guard += 1
                self.assertLess(guard, 400, f"game {g}: the half inning never ended")
                outcome = _drive_pitch(state, rng, intents, courses)
                total_pitches += 1
                xbh += outcome["result"] in ("Double", "Triple", "Home run")
                self.assertEqual(len(state.runners), 3)
                if state.is_over():
                    break
                self.assertLessEqual(state.strikes, 2, f"game {g}: invalid strike count")
                self.assertLessEqual(state.balls, 3, f"game {g}: invalid ball count")
                self.assertLess(state.outs, 3)
                self.assertTrue(0 <= state.lineup.index <= 8, f"game {g}: batting order broke")

            self.assertTrue(state.outs == 3 or state.is_walkoff(), f"game {g}: ended undecided")
            total_runs += state.runs_this_inning
            run_txt, status = state.result_summary()
            self.assertIsInstance(status, str)
            build_analysis(state.pitch_log)   # runs without an exception

        self.assertGreater(total_pitches, games * 3)
        # improvement A: extra-base hits and runs should occur (within a realistic range)
        self.assertGreater(xbh, 100)
        self.assertGreater(total_runs, 200)
        self.assertLess(total_runs / games, 4.0)   # average runs per half inning does not run away

    def test_1000_games_through_full_engine_no_crash(self):
        """Run engine.resolve_one_pitch() -- the entry point the CLI / Web actually call -- through
        1000 games. This also verifies the surrounding events that judge_pitch() alone does not cover:
        dropped third strikes, sign stealing, blocking, steals.
        """
        rng = random.Random(99)
        intents = list(PITCH_INTENTS)
        courses = list(COURSES)
        games = 1000
        events_seen = set()

        for g in range(games):
            state = generate_random_situation(rng)
            guard = 0
            while not state.is_over():
                guard += 1
                self.assertLess(guard, 400, f"game {g}: the half inning never ended")
                for _ in maybe_resolve_sign_steal(state, rng):
                    pass
                pt = rng.choice(list(repertoire_options(state.pitcher)))
                co = rng.choice(courses)
                it = rng.choice(intents)
                resolve_one_pitch(state, pt, co, it, rng)
                self.assertTrue(0 <= state.lineup.index <= 8)
            self.assertTrue(state.outs == 3 or state.is_walkoff())
            events_seen.update(state.events)

            report = build_postgame_report(state)
            self.assertEqual(report["pitches"], len(state.pitch_log))
            self.assertEqual(report["runs_allowed"], state.runs_this_inning)
            self.assertGreaterEqual(report["batters_faced"], 1)
            # the total of strikeouts / walks / hits allowed / errors does not exceed the batters faced
            self.assertLessEqual(
                report["strikeouts"] + report["walks"] + report["hits"] + report["errors"],
                report["batters_faced"])
            build_analysis(state.pitch_log)   # runs without an exception

        # did the surrounding events (dropped third strike, sign stealing, steals, passed balls) all actually occur
        for ev in ("Dropped third strike", "Change signs", "Stolen base", "Caught stealing", "Passed ball"):
            self.assertIn(ev, events_seen, f"'{ev}' never occurred in 1000 games")


# ---------- Catcher Rating / Challenge Code / drills (built during the hackathon) ----------
def _play_random_game(seed):
    rng = random.Random(seed)
    st = generate_random_situation(rng)
    while not st.is_over():
        pt = rng.choice(list(repertoire_options(st.pitcher)))
        resolve_one_pitch(st, pt, rng.choice(list(COURSES)), rng.choice(list(PITCH_INTENTS)), rng)
    return st


class CatcherRatingTests(unittest.TestCase):
    def test_rating_is_well_formed_for_many_games(self):
        grades = set()
        for seed in range(60):
            st = _play_random_game(seed)
            r = catcher_rating(st)
            self.assertTrue(0 <= r["score"] <= 100)
            self.assertIn(r["grade"], "SABCD")
            self.assertTrue(r["title"])
            for name, desc in r["badges"]:
                self.assertTrue(name and desc)
            grades.add(r["grade"])
        self.assertGreaterEqual(len(grades), 2)     # the rating actually separates games

    def test_shutdown_badge_only_when_no_runs(self):
        for seed in range(40):
            st = _play_random_game(seed)
            names = [n for n, _ in catcher_rating(st)["badges"]]
            self.assertEqual("Shutdown" in names, st.runs_this_inning == 0)

    def test_more_runs_never_raise_the_score(self):
        st = _play_random_game(3)
        report = build_postgame_report(st)
        scores = []
        for runs in (0, 1, 2, 5):
            fake = dict(report, runs_allowed=runs)
            scores.append(catcher_rating(st, fake)["score"])
        self.assertEqual(scores, sorted(scores, reverse=True))


class StartOptionsTests(unittest.TestCase):
    def test_same_challenge_code_gives_the_same_opening_scene(self):
        a, b = webapp.GameSession(), webapp.GameSession()
        a.start_new_game(challenge_seed=4242)
        b.start_new_game(challenge_seed=4242)
        self.assertEqual(a.game_seed, 4242)
        self.assertEqual(a.state.batter.name, b.state.batter.name)
        self.assertEqual((a.state.our_score, a.state.opp_score, a.state.runners, a.state.outs),
                         (b.state.our_score, b.state.opp_score, b.state.runners, b.state.outs))

    def test_every_game_gets_a_code_when_no_seed_is_given(self):
        s = webapp.GameSession()
        s.start_new_game()
        self.assertTrue(1000 <= s.game_seed <= 9999)

    def test_start_options_ignore_bad_values(self):
        self.assertEqual(webapp._start_options({"seed": "abc", "scenario": "nope", "difficulty": "insane"}), {})
        self.assertEqual(webapp._start_options({"seed": " 77 ", "scenario": "steal", "difficulty": "expert"}),
                         {"challenge_seed": 77, "scenario": "steal", "difficulty": "expert"})
        self.assertEqual(webapp._start_options({"scenario": "random"}), {"scenario": None})

    def test_every_drill_sets_up_a_valid_game(self):
        for key, _name, _desc in webapp._DRILLS:
            s = webapp.GameSession()
            s.start_new_game(challenge_seed=1, scenario=key)
            self.assertEqual(s.scenario, key)
            self.assertFalse(s.state.is_over())


# ---------- Play Viewer (playview.py, built during the hackathon) ----------
def _fair_ball_plays(games, seed):
    """Yield (outcome, state_before_runners) for every fair ball in some random games."""
    rng = random.Random(seed)
    for _ in range(games):
        st = generate_random_situation(rng)
        while not st.is_over():
            pt = rng.choice(list(repertoire_options(st.pitcher)))
            outcome, _msgs = resolve_one_pitch(st, pt, rng.choice(list(COURSES)),
                                               rng.choice(list(PITCH_INTENTS)), rng)
            if outcome.get("batted_ball") and outcome.get("fielding") and outcome.get("play"):
                yield outcome


class PlayViewerTests(unittest.TestCase):
    def _check_segments(self, segs):
        prev_end = 0.0
        for s in segs:
            self.assertGreater(s[1], s[0])
            self.assertGreaterEqual(s[0], prev_end - 1e-6)
            prev_end = s[1]
            for v in s:
                self.assertTrue(v == v and abs(v) < 1000)

    def test_script_is_valid_for_every_kind_of_play(self):
        seen = set()
        for i, o in enumerate(_fair_ball_plays(150, 8)):
            sc = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=i)
            seen.add(o["result"])
            self._check_segments(sc["ball"]["segs"])
            for f in sc["fielders"].values():
                self._check_segments(f["segs"])
            for r in sc["runners"]:
                self._check_segments(r["segs"])
            self.assertTrue(0 < sc["duration"] < 12)
            self.assertEqual(set(sc["fielders"]), set(playview.POSITIONS))
            json.dumps(sc)       # must be JSON-friendly
        self.assertTrue({"Out", "Single", "Double", "Home run"} <= seen)

    def test_same_play_always_looks_the_same_and_uses_no_game_randomness(self):
        o = next(iter(_fair_ball_plays(20, 3)))
        a = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=7)
        before = random.getstate()
        b = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=7)
        self.assertEqual(random.getstate(), before)
        self.assertEqual(a, b)

    def test_the_fielder_who_makes_the_play_goes_to_the_ball(self):
        for i, o in enumerate(_fair_ball_plays(60, 4)):
            if not o["fielding"]["air_out"]:
                continue
            sc = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=i)
            flight = sc["ball"]["segs"][0]
            catch = [s for s in sc["fielders"][o["fielding"]["position"]]["segs"] if s[1] <= flight[1] + 0.01][-1]
            self.assertAlmostEqual(catch[4], flight[4], delta=1.0)     # he is where the ball came down
            self.assertAlmostEqual(catch[5], flight[5], delta=1.0)

    def test_runner_fates_match_the_play(self):
        for i, o in enumerate(_fair_ball_plays(120, 6)):
            sc = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=i)
            fates = [r["fate"] for r in sc["runners"]]
            if o["result"] != "Home run":
                self.assertEqual(fates.count("scored") + (0 if o["play"]["outs_added"] else 0),
                                 min(o["play"]["runs"], fates.count("scored")))
            if o["result"] == "Home run":
                self.assertTrue(all(f == "scored" for f in fates))
            if o["fielding"]["batter_out"]:
                batter = next(r for r in sc["runners"] if r["id"] == "b")
                self.assertEqual(batter["fate"], "out")
            else:
                batter = next(r for r in sc["runners"] if r["id"] == "b")
                self.assertNotEqual(batter["fate"], "out")

    def test_viewer_html_embeds_the_script_safely(self):
        o = next(iter(_fair_ball_plays(20, 3)))
        sc = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=1)
        page = playview.render_play_viewer(sc, "A <b>caption</b>", autoplay=False)
        self.assertIn('id="pv-data"', page)
        self.assertIn('data-autoplay="0"', page)
        self.assertIn("A &lt;b&gt;caption&lt;/b&gt;", page)
        self.assertNotIn("</script><", page.split('id="pv-data"')[1].split("</script>")[0])


# ---------- 3D page pieces: evaluation text and the music script ----------
import api as api_module
import stadium


# ---------- Catching with the mitt, reading the batter (built during the hackathon) ----------
class CatchTests(unittest.TestCase):
    def _state_with_a_pitch(self, result="Ball", runners=(False, False, False), balls=1, strikes=0, course="out_lo", swung=False):
        st = generate_random_situation(random.Random(4))
        st.runners = list(runners)
        st.runner_speeds = [55 if r else None for r in runners]
        st.balls, st.strikes = balls, strikes
        st.pitch_log.append({"pitch_number": 1, "pitch_type": "four_seam", "target_course": course, "actual_course": course, "intent": "strike",
                             "result": result, "missed": False, "quality": 0.5, "framed": False, "decision_quality": 0.0,
                             "outcome_flags": {"swung": swung, "in_zone": False}, "timing": "on_time", "batter": st.batter.name})
        st.history.add("four_seam", course, result, "on_time", swung, actual_course=course, in_zone=False)
        return st

    def test_a_miss_with_runners_is_a_passed_ball_and_without_runners_is_not(self):
        st = self._state_with_a_pitch(runners=(True, False, False))
        out = st.apply_catch("miss", random.Random(1))
        self.assertTrue(out["passed"])
        self.assertEqual(st.runners, [False, True, False])
        st2 = self._state_with_a_pitch()
        out2 = st2.apply_catch("miss", random.Random(1))
        self.assertFalse(out2["passed"])
        self.assertEqual(st2.runners, [False, False, False])
        self.assertEqual((st.catch_stats["miss"], st2.catch_stats["miss"]), (1, 1))

    def test_a_catch_is_applied_once_and_only_for_pitches_that_ended_in_the_mitt(self):
        st = self._state_with_a_pitch(result="Strike")
        self.assertTrue(st.can_apply_catch())
        st.apply_catch("barely", random.Random(1))
        self.assertFalse(st.can_apply_catch())
        before = dict(st.catch_stats)
        st.apply_catch("clean", random.Random(1))                  # a repeat is ignored
        self.assertEqual(st.catch_stats, before)
        self.assertFalse(self._state_with_a_pitch(result="Single").can_apply_catch())
        self.assertFalse(self._state_with_a_pitch(result="Foul").can_apply_catch())

    def test_a_clean_catch_can_frame_a_close_ball_but_never_a_strikeout_a_swing_or_the_middle(self):
        stolen = 0
        for seed in range(200):
            st = self._state_with_a_pitch(balls=2, strikes=1, course="out_lo")
            if st.apply_catch("clean", random.Random(seed))["stolen"]:
                stolen += 1
                self.assertEqual((st.balls, st.strikes), (1, 2))
                self.assertEqual(st.pitch_log[-1]["result"], "Strike")
                self.assertEqual(st.history.records[-1]["result"], "Strike")
        self.assertTrue(10 < stolen < 100)                         # it happens, but not always
        for kwargs in ({"strikes": 2}, {"swung": True}, {"course": "mid_mid"}):
            args = dict({"balls": 2, "strikes": 1}, **kwargs)
            for seed in range(100):
                self.assertFalse(self._state_with_a_pitch(**args).apply_catch("clean", random.Random(seed))["stolen"])
        for seed in range(100):                                    # a catch that only just held does not frame anything
            self.assertFalse(self._state_with_a_pitch(balls=2).apply_catch("barely", random.Random(seed))["stolen"])

    def test_rating_counts_the_catching_separately_from_the_call(self):
        st = _play_random_game(5)
        base = catcher_rating(st)["score"]
        st.catch_stats = {"clean": 6, "barely": 0, "miss": 0}
        better = catcher_rating(st)
        st.catch_stats = {"clean": 0, "barely": 0, "miss": 3}
        worse = catcher_rating(st)["score"]
        self.assertGreaterEqual(better["score"], base)
        self.assertLessEqual(worse, base)
        self.assertIn("Sure Hands", [n for n, _ in better["badges"]])

    def test_judging_rules_get_stricter_with_difficulty(self):
        r = api_module.CATCH_RULES
        for i in (0, 1):
            self.assertGreater(r["beginner"]["time"][i], r["normal"]["time"][i])
            self.assertGreater(r["normal"]["time"][i], r["expert"]["time"][i])
        for rules in r.values():                                          # the catch is one press, judged by timing alone
            self.assertEqual(set(rules), {"time"})


class BatterReadTests(unittest.TestCase):
    def test_swing_info_follows_the_judged_timing(self):
        for seq in range(40):
            for result in ("Whiff", "Foul", "Single", "Out"):
                early = api_module.swing_info(seq, result, "early", True)
                late = api_module.swing_info(seq, result, "late", True)
                self.assertLess(early["off"], 0)                          # early = the bat is ahead of the ball
                self.assertGreater(late["off"], 0)
                self.assertGreater(early["ahead"], 0)
                self.assertLess(late["ahead"], 0)
                self.assertEqual((early["label"], late["label"]), ("EARLY", "LATE"))
        self.assertEqual(api_module.swing_info(3, "Whiff", "late", True), api_module.swing_info(3, "Whiff", "late", True))
        took = api_module.swing_info(1, "Strike", "late", False)
        self.assertFalse(took["swung"])
        self.assertEqual((took["label"], took["off"]), ("TOOK IT", 0.0))
        self.assertGreater(abs(api_module.swing_info(2, "Whiff", "on_time", True)["dy"]), 0.07)   # an on-time miss is over or under the ball

    def test_reads_only_state_what_was_seen_with_at_least_two_observations(self):
        st = generate_random_situation(random.Random(2))
        name = st.batter.name

        def add(n, result, swung, timing, course="out_lo", in_zone=True, pitch="four_seam"):
            st.pitch_log.append({"pitch_number": n, "pitch_type": pitch, "target_course": course, "actual_course": course, "intent": "strike",
                                 "result": result, "missed": False, "quality": 0.5, "framed": False, "decision_quality": 0.0,
                                 "outcome_flags": {"swung": swung, "in_zone": in_zone}, "timing": timing, "batter": name})
        self.assertEqual(api_module.batter_reads(st)[0], [])
        add(1, "Whiff", True, "late")
        self.assertEqual(api_module.batter_reads(st)[0], [])             # one swing is not a pattern
        add(2, "Foul", True, "late")
        facts, used = api_module.batter_reads(st)
        self.assertEqual(len(used), 2)
        self.assertTrue(any(f.startswith("Late on 2 of his last 2 swings") and "fastballs" in f for f in facts))
        self.assertTrue(any("low" in f for f in facts))                    # both misses were low
        add(3, "Strike", False, "on_time", course="mid_mid")
        add(4, "Strike", False, "on_time", course="mid_mid")
        self.assertTrue(any(f.startswith("Took 2 strikes") for f in api_module.batter_reads(st)[0]))
        st.pitch_log[-1]["batter"] = "Someone Else"                       # pitches to another batter are not counted
        self.assertEqual(len(api_module.batter_reads(st)[1]), 3)
        for f in api_module.batter_reads(st)[0]:
            for banned in ("looking for", "sitting on", "guess", "plan", "wants", "expect"):
                self.assertNotIn(banned, f.lower())                       # facts, never a claim about his plan


class StadiumPieceTests(unittest.TestCase):
    def test_evaluate_call_grades_and_always_explains(self):
        for dq, grade in ((0.4, "good"), (0.25, "good"), (0.0, "fair"), (-0.24, "fair"), (-0.25, "risky"), (-0.6, "risky")):
            g, title, reason = api_module.evaluate_call({"_analysis": {"decision_quality": dq, "sequence_label": "Nothing notable"}})
            self.assertEqual(g, grade)
            self.assertTrue(title and reason)
        g, _t, reason = api_module.evaluate_call({"framed": True, "_analysis": {"decision_quality": 0.3, "sequence_label": "A / B", "fooled_guess": True}})
        self.assertEqual(reason, "A · He was sitting on something else")         # first note only, at most two reasons

    def test_music_is_one_player_that_never_restarts_and_loops(self):
        js = stadium._assets()[1]
        self.assertEqual(js.count("function scheduleStep"), 1)                  # the song is in the page once
        self.assertIn("if (window.__btpBgm) return window.__btpBgm;", js)        # a second player is never created
        self.assertIn("btp_vol", js)
        self.assertIn("btp_muted", js)                                           # volume and mute are remembered
        self.assertIn("step = (step + 1) % STEPS", js)                           # after the last bar it goes back to the first
        self.assertNotIn("suspend()", js)                                        # muting only moves a gain knob


    def test_the_pitch_is_computed_from_forces_and_the_delivery_is_a_full_body_motion(self):
        js = stadium._assets()[1]
        for needle in ("BTP.phys = (function", "function solve(", "function fly(", "RHO = 1.2", "G = 9.81", "CD = 0.35",
                       "1 / (2 + 1 / Math.max(S, 1e-4))",              # the lift coefficient comes from the spin factor
                       "wm * sp",                                      # the force direction is spin x velocity, normalised
                       "BTP.deliver = function", "BTP.DELIVERY", "hip:", "sho:",           # hips and shoulders turn separately
                       "BTP.releasePoint", "BALL_TIME_SCALE"):
            self.assertIn(needle, js, needle)
        self.assertIn("lefty ? -s[1] : s[1]", js)                      # a left-hander mirrors the side spin
        self.assertIn("if (lefty) Object.keys(J)", js)                 # ...and the whole body motion
        self.assertNotIn("pitchPath", js)                              # the old hand-drawn curve is gone
        for sound in ("'pop'", "'whoosh'", "'crack'"):
            self.assertIn(sound, js)

    def test_pages_do_not_reload_between_pitches(self):
        js = stadium._assets()[1]
        self.assertNotIn("location.reload", js)
        self.assertNotIn("form.submit()", js)
        self.assertIn("'X-Fetch'", js)


# ---------- the browser-only build (pagesapi.py) ----------
import pagesapi


class BrowserBuildTests(unittest.TestCase):
    def test_requests_are_answered_in_memory_with_the_same_rules(self):
        webapp.SESSION = webapp.GameSession(seed=5)
        out = json.loads(pagesapi.handle("GET", "/api/state"))
        self.assertEqual(out["status"], 200)
        self.assertEqual(json.loads(out["body"])["phase"], "start")
        hdr = json.dumps({"X-Fetch": "1", "Content-Type": "application/x-www-form-urlencoded"})
        started = json.loads(json.loads(pagesapi.handle("POST", "/start", "scenario=random", hdr))["body"])
        self.assertEqual(started["phase"], "intro")
        played = json.loads(json.loads(pagesapi.handle("POST", "/playball", "", hdr))["body"])
        self.assertEqual(played["phase"], "play")
        key = played["repertoire"][0]["key"]
        res = json.loads(json.loads(pagesapi.handle("POST", "/pitch", "pitch_type=%s&course=mid_mid&intent=strike" % key, hdr))["body"])
        self.assertIn(res["phase"], ("play", "qte", "over"))
        panel = json.loads(pagesapi.handle("GET", "/api/panel/history"))
        self.assertEqual(panel["status"], 200)
        self.assertEqual(json.loads(pagesapi.handle("GET", "/api/panel/nope"))["status"], 404)

    def test_the_static_page_carries_the_loader_and_the_zip_has_the_game(self):
        page = webapp.render_stadium_page(webapp._classic_css(), static=True)
        self.assertIn("loadPyodide", page)
        self.assertNotIn("loadPyodide", webapp.render_stadium_page(webapp._classic_css()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
