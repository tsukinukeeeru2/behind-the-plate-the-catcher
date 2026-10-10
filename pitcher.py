"""The pitcher and the "actual pitch" (Pitch Execution).

Even when the catcher calls "slider, down and away", it does not always go there.

    Pitch Call (request)  ->  execute_pitch()  ->  Actual Pitch (what really happened)

Pitcher abilities:
    control  : how rarely he yanks or sails a pitch (higher = fewer misses)
    command  : precision on a single spot (matters more the closer to a corner)
    velocity : speed
    stuff    : raw quality of the pitch (adds whiffs and mis-hits)
    quality  : how good each pitch type is today (0-100)

This is the part that makes "a good call can still get hit if he misses his spot, and a
bad call can still work if the pitch is nasty".
"""

import random
from dataclasses import dataclass, field

from constants import CENTER_COURSE, COURSE_NEIGHBORS, is_corner
from pitch_data import DEFAULT_PITCHER

# The result of an actual pitch
#   actual_course : where the ball really went
#   quality       : 0.0-1.0. How good the pitch is (higher = harder to hit)
#   missed        : whether he missed the called location
#   miss_kind     : "leak" (caught the middle) / "spray" (scattered) / None


@dataclass
class Pitcher:
    """One pitcher. Mostly holds ability values, but it also has behavior --
    execute_pitch() and fatigue() -- so it is a regular @dataclass (not frozen, because
    pitches_thrown is a mutable field that grows with every pitch).
    """

    name: str = "Pitcher"
    throws: str = "R"
    control: float = 55
    command: float = 55
    velocity: float = 55
    stuff: float = 55
    repertoire: list = None
    pitch_quality: dict = None
    pitches_thrown: int = field(default=0, init=False)   # pitch count (#D fatigue)

    def __post_init__(self):
        self.repertoire = list(self.repertoire or DEFAULT_PITCHER["repertoire"])
        # Per-pitch quality. If not given, start from `stuff`.
        self.pitch_quality = dict(self.pitch_quality or {})
        for key in self.repertoire:
            self.pitch_quality.setdefault(key, self.stuff)

    # ---- Fatigue (#D) ----
    def fatigue(self):
        """A fatigue value from 0.0 to 0.12 that grows as the pitch count climbs. Kicks in around pitch 16."""
        return min(0.12, max(0.0, self.pitches_thrown - 16) * 0.006)

    def fatigue_level(self):
        f = self.fatigue()
        return 2 if f >= 0.08 else (1 if f >= 0.03 else 0)

    def execute_pitch(self, pitch_type, target_course, intent, rng=None, tempo=0.0):
        """`tempo` is how well the catcher's return-throw rhythm went (qte.catcher_return_ball), -0.25 to 0.25.
        A better tempo gets the pitcher going: slightly fewer misses and slightly more stuff. 0.0 changes nothing."""
        rng = rng or random
        self.pitches_thrown += 1
        tired = self.fatigue()

        demands_precision = intent in ("chase", "freeze", "weak_contact")
        aiming_corner = is_corner(target_course)
        aiming_edge = target_course != CENTER_COURSE

        # 1. Chance of missing the spot
        miss_p = 0.32 - self.control / 400 - self.command / 400
        if demands_precision and aiming_edge:
            miss_p += 0.08
        if aiming_corner:
            miss_p += 0.06          # a corner (edge both ways) is harder still
        if intent == "waste":
            miss_p -= 0.12          # a pitch meant to miss big "misses as planned" more easily
        miss_p += tired             # when tired, control gets sloppy
        miss_p -= tempo * 0.10      # a good return-throw tempo cuts misses slightly
        miss_p = max(0.05, min(0.65, miss_p))
        missed = rng.random() < miss_p

        if not missed:
            actual_course = target_course
            miss_kind = None
        elif aiming_edge:
            # Missed while aiming at an edge -> 60% catch the middle (leak), the rest scatter to a neighbor
            if rng.random() < 0.60:
                actual_course, miss_kind = CENTER_COURSE, "leak"
            else:
                actual_course, miss_kind = rng.choice(COURSE_NEIGHBORS[target_course]), "spray"
        else:
            actual_course, miss_kind = rng.choice(COURSE_NEIGHBORS[CENTER_COURSE]), "spray"

        # 2. Quality of the pitch
        quality = self.pitch_quality.get(pitch_type, self.stuff) / 100.0
        quality += (self.velocity - 50) / 500.0
        quality += rng.uniform(-0.14, 0.14)
        if missed:
            quality -= 0.07        # a miss tends to be a worse pitch too (but not always)
        quality -= tired * 0.5     # tired arms lose stuff too
        quality += tempo * 0.10    # a settled tempo adds a little stuff
        quality = max(0.03, min(0.99, quality))

        return {
            "pitch_type": pitch_type,
            "target_course": target_course,
            "actual_course": actual_course,
            "quality": quality,
            "missed": missed,
            "miss_kind": miss_kind,
        }


def build_sample_pitcher(rng=None, name="Reliever"):
    """Build one late-inning reliever."""
    rng = rng or random
    throws = "R" if rng.random() < 0.7 else "L"
    repertoire = rng.choice([
        ["four_seam", "slider", "changeup"],
        ["four_seam", "sinker", "slider", "curveball"],
        ["four_seam", "cutter", "sweeper", "splitter"],
        ["sinker", "cutter", "slider", "changeup"],
    ])
    return Pitcher(
        name=name, throws=throws,
        control=rng.randint(45, 72), command=rng.randint(42, 70),
        velocity=rng.randint(45, 78), stuff=rng.randint(45, 75),
        repertoire=repertoire,
        pitch_quality={k: rng.randint(40, 78) for k in repertoire},
    )
