"""The Batter class and the logic that guesses what pitch he is sitting on.

How information is split:
  Public (fine to show on the scoreboard): name / bats / OPS, AVG, OBP, SLG / batter type
  Hidden (never shown to the player):
      power / contact / discipline / chase_rate / whiff_rate / aggression
      pull, center, oppo tendency / gb_tendency
      vs_fastball / vs_breaking / vs_offspeed (strength by pitch family)
      hot_course (strength) / weak_course (weakness) / weak_pitch
      guess_bias (what he tends to look for) / two_strike_ability / pressure_tolerance / speed

judge.py uses the hidden values to move probabilities, but reaction.py and ui.py only
ever touch public information.
"""

import random
from dataclasses import dataclass, field

from pitch_data import all_pitch_keys, guess_class_of

HANDEDNESS = {"R": "Bats R", "L": "Bats L"}

# Batter types (public labels). Separate from the lineup archetypes; just for rough display.
COARSE_TYPES = {"power": "Slugger", "contact": "Contact hitter", "patient": "On-base type",
                "free_swinger": "Free swinger", "average": "Average", "weak": "Light hitter"}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


@dataclass
class Batter:
    """One batter. A @dataclass because its main job is holding data.

    Field names match the constructor arguments used by lineup.py, CSV/JSON files, and
    the tests exactly. Only `ops` is computed automatically from obp + slg, so it is a
    `field(init=False)` filled in __post_init__.

    Public (fine to show on the scoreboard): name / bats / OPS, AVG, OBP, SLG / batter type
    Hidden (never shown to the player):
        power / contact / discipline / chase_rate / whiff_rate / aggression
        pull, gb_tendency (batted-ball direction tendencies)
        vs_fastball / vs_breaking / vs_offspeed (strength by pitch family)
        hot_course (strength) / weak_course (weakness) / weak_pitch
        guess_bias (what he tends to look for) / two_strike_ability / pressure_tolerance / speed

    judge.py uses the hidden values to move probabilities, but reaction.py and ui.py only
    ever touch public information.
    """

    name: str = "Batter"
    bats: str = "R"
    coarse_type: str = "average"
    avg: float = 0.255
    obp: float = 0.320
    slg: float = 0.410
    power: float = 0.5
    contact: float = 0.5
    discipline: float = 0.5
    chase_rate: float = 0.30
    whiff_rate: float = 0.24
    aggression: float = 0.5
    pull: float = 0.40             # pull tendency. batted_ball.py decides the batted-ball direction
    gb_tendency: float = 0.45
    vs_fastball: float = 0.5
    vs_breaking: float = 0.5
    vs_offspeed: float = 0.5
    hot_course: str = "mid_mid"     # a 9-zone course key (constants.COURSES).
    weak_course: str = "mid_lo"     # data files are expected to override these, but the defaults are valid too
    weak_pitch: str = "slider"
    guess_bias: float = 0.55
    two_strike_ability: float = 0.5
    pressure_tolerance: float = 0.5
    speed: int = 50
    ops: float = field(init=False)

    def __post_init__(self):
        self.ops = round(self.obp + self.slg, 3)

    # ---------- How public information is shown ----------
    def public_hand_name(self):
        return HANDEDNESS[self.bats]

    def type_label(self):
        return COARSE_TYPES.get(self.coarse_type, self.coarse_type)

    def public_line(self):
        return f"{self.bats} / OPS {self.ops:.3f}"

    def scouting_note(self):
        """A hint you can read off the batter type. It does not match the real hidden values."""
        return {
            "power": "Dangerous pull-side power",
            "contact": "Sprays it to all fields",
            "patient": "Works the count, hard to chase",
            "free_swinger": "Swings early in the count",
            "average": "No standout tendency",
            "weak": "Rarely hits it hard",
        }.get(self.coarse_type, "Tendency unknown")

    def family_strength(self, family):
        return {"fastball": self.vs_fastball,
                "breaking": self.vs_breaking,
                "offspeed": self.vs_offspeed}[family]

    # ---------- Guessing what he is sitting on (#8) ----------
    def predict_guess(self, history, state, pitcher):
        """What this batter is looking for right now. Never shown to the player.

        Returns:
            {"class": "fastball"|"offspeed", "class_strength": 0..1,
             "location": "inside"|"outside"|"middle"|"any", "loc_strength": 0..1}

        Inputs: the count / the batter's personality (guess_bias, discipline) / pitch history /
                repeated pitches / the pitcher's pitch mix / the previous pitch /
                memory across at-bats (when the order turns over).
        """
        lean_fastball = self.guess_bias   # closer to 1.0 = more fastball-looking

        # 1. He plays the odds on what he has been shown in the pitch history
        recent = history.recent_class_counts(4)
        seen = recent["fastball"] + recent["offspeed"]
        if seen > 0:
            lean_fastball = 0.5 * lean_fastball + 0.5 * (recent["fastball"] / seen)
        else:
            # No history -> faintly reflect the fastball share of the pitcher's repertoire
            reps = pitcher.repertoire
            fb = sum(1 for k in reps if guess_class_of(k) == "fastball")
            lean_fastball = 0.6 * lean_fastball + 0.4 * (fb / max(1, len(reps)))

        # 1.5 Memory across at-bats (order turns over): he vaguely remembers the pitches shown last time
        memory = getattr(state, "batter_memory", None)
        mem = memory(self) if memory else None
        if mem:
            mem_total = mem["fastball"] + mem["offspeed"]
            if mem_total > 0:
                lean_fastball = 0.75 * lean_fastball + 0.25 * (mem["fastball"] / mem_total)

        # 2. The count
        if state.balls > state.strikes:
            lean_fastball += 0.15          # a hitter's count: look fastball
        if state.strikes == 2:
            lean_fastball = 0.5 + (lean_fastball - 0.5) * 0.4   # cannot narrow it down

        # 3. Same pitch type repeated -> he locks in hard on that pitch
        streak_class = None
        last = history.last()
        if history.same_type_streak() >= 2 and last is not None:
            streak_class = last["pitch_class"]
            if streak_class == "fastball":
                lean_fastball = min(1.0, lean_fastball + 0.20)
            else:
                lean_fastball = max(0.0, lean_fastball - 0.20)

        # 4. After a fastball strike, he is more likely to think about a breaking ball next
        if last is not None and last["pitch_class"] == "fastball" and last["swung"] is False \
                and last.get("in_zone"):
            lean_fastball -= 0.08

        lean_fastball = _clamp(lean_fastball, 0.0, 1.0)
        if lean_fastball >= 0.5:
            guess_class = "fastball"
            class_strength = (lean_fastball - 0.5) * 2
        else:
            guess_class = "offspeed"
            class_strength = (0.5 - lean_fastball) * 2

        # The lower his discipline, the more he "sits on one pitch" -> raise the strength
        class_strength = _clamp(class_strength * (1.2 - 0.4 * self.discipline), 0.0, 1.0)

        # 5. Location guess (in a hitter's count he plays the odds on his hot zone)
        if state.strikes == 2:
            location, loc_strength = "any", 0.0
        elif state.balls > state.strikes:
            location, loc_strength = self.hot_course, round(0.4 + 0.4 * class_strength, 2)
        else:
            location, loc_strength = "any", 0.15

        return {"class": guess_class, "class_strength": round(class_strength, 2),
                "location": location, "loc_strength": loc_strength}


# --------- Quick generator (for tests and quick checks) ---------
def create_random_batter(rng=None):
    rng = rng or random
    coarse = rng.choice(["power", "contact", "average"])
    bats = "R" if rng.random() < 0.6 else "L"
    obp = round(rng.uniform(0.290, 0.370), 3)
    slg = round(rng.uniform(0.360, 0.520), 3)
    return Batter(
        name="Batter", bats=bats, coarse_type=coarse,
        avg=round(rng.uniform(0.230, 0.300), 3), obp=obp, slg=slg,
        power=rng.uniform(0.3, 0.8), contact=rng.uniform(0.3, 0.8),
        discipline=rng.uniform(0.3, 0.7), chase_rate=rng.uniform(0.20, 0.40),
        whiff_rate=rng.uniform(0.15, 0.32), aggression=rng.uniform(0.4, 0.7),
        pull=round(_clamp(rng.gauss(0.45, 0.1), 0.15, 0.85), 2),
        gb_tendency=round(_clamp(rng.gauss(0.45, 0.08), 0.2, 0.7), 2),
        vs_fastball=rng.uniform(0.35, 0.65), vs_breaking=rng.uniform(0.35, 0.65),
        vs_offspeed=rng.uniform(0.35, 0.65),
        hot_course=rng.choice(list_courses()), weak_course=rng.choice(list_courses()),
        weak_pitch=rng.choice(all_pitch_keys()),
        guess_bias=round(rng.uniform(0.35, 0.75), 2),
        two_strike_ability=rng.uniform(0.3, 0.7), pressure_tolerance=rng.uniform(0.3, 0.7),
        speed=rng.randint(35, 70),
    )


def list_courses():
    from constants import COURSES
    return list(COURSES)


def random_course(rng):
    return rng.choice(list_courses())
