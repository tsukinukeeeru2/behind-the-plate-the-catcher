"""The seven fielders (everyone except the pitcher and catcher) and the position-fit math.

Key ideas:
  - Each fielder has five abilities: range / hands / arm / reaction / speed (about 20-80).
  - Each position has its own "weights" and its own "difficulty".
  - position_fit(fielder, position) returns the effective defensive value of putting that
    fielder at that position. The same player rates differently at SS and at 1B -- that
    is the heart of it.

"Where do you hide your worst fielder?" becomes a real decision because
  1. hard positions (like SS) amplify ability gaps (difficulty_gain)
  2. positions that see more balls matter more to the whole defense (ball_share)
"""

import random
from dataclasses import dataclass

INFIELD_POSITIONS = ["3B", "SS", "2B", "1B"]
OUTFIELD_POSITIONS = ["LF", "CF", "RF"]
POSITIONS = INFIELD_POSITIONS + OUTFIELD_POSITIONS

ATTRS = ["range", "hands", "arm", "reaction", "speed"]


@dataclass
class Fielder:
    """One fielder. A data class that just holds the five abilities."""

    name: str
    range: float      # range (reach from the first step)
    hands: float      # how reliably he catches the ball (glove)
    arm: float        # throwing power and accuracy
    reaction: float   # how fast he reacts to the ball
    speed: float      # running speed

    def overall(self):
        """Plain average of the five abilities: a rough "is he good overall" number."""
        return sum(getattr(self, a) for a in ATTRS) / len(ATTRS)


# weights sum to 1.0. difficulty_gain > 1 means "ability gaps matter more".
# ball_share is roughly what share of fair balls come here (a weight for the EDE number).
POSITION_REQUIREMENTS = {
    "SS": {"weights": {"range": 0.34, "hands": 0.20, "arm": 0.24, "reaction": 0.16, "speed": 0.06},
           "difficulty_gain": 1.35, "ball_share": 0.17},
    "2B": {"weights": {"range": 0.30, "hands": 0.24, "arm": 0.12, "reaction": 0.24, "speed": 0.10},
           "difficulty_gain": 1.15, "ball_share": 0.15},
    "3B": {"weights": {"range": 0.20, "hands": 0.24, "arm": 0.34, "reaction": 0.20, "speed": 0.02},
           "difficulty_gain": 1.10, "ball_share": 0.13},
    "1B": {"weights": {"range": 0.12, "hands": 0.46, "arm": 0.10, "reaction": 0.20, "speed": 0.12},
           "difficulty_gain": 0.75, "ball_share": 0.12},
    "CF": {"weights": {"range": 0.40, "hands": 0.14, "arm": 0.14, "reaction": 0.12, "speed": 0.20},
           "difficulty_gain": 1.20, "ball_share": 0.13},
    "LF": {"weights": {"range": 0.30, "hands": 0.16, "arm": 0.14, "reaction": 0.12, "speed": 0.28},
           "difficulty_gain": 0.85, "ball_share": 0.09},
    "RF": {"weights": {"range": 0.28, "hands": 0.16, "arm": 0.26, "reaction": 0.12, "speed": 0.18},
           "difficulty_gain": 0.90, "ball_share": 0.09},
}


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def position_fit(fielder, position):
    """Effective defensive value (1-99) of putting this fielder at this position.

      1. Take the weighted sum of the abilities this position demands (raw).
      2. Using 50 as the baseline, difficulty_gain stretches or shrinks the distance from average
         -> at a hard position both bad and good fielders are amplified.
    """
    req = POSITION_REQUIREMENTS[position]
    raw = sum(getattr(fielder, attr) * w for attr, w in req["weights"].items())
    fit = 50 + (raw - 50) * req["difficulty_gain"]
    return _clamp(fit, 1.0, 99.0)


def _roll(rng, mean, spread):
    return _clamp(rng.gauss(mean, spread), 20, 80)


def make_default_fielders(rng=None):
    """Build seven fielders with randomly rolled abilities.

    Exactly one of them is a clear "liability" with low defensive ability.
    You can tell who it is from the ability table -- "where to put him" is the player's call.
    """
    rng = rng or random
    names = ["A", "B", "C", "D", "E", "F", "G"]
    weak_index = rng.randrange(len(names))
    fielders = []
    for i, name in enumerate(names):
        if i == weak_index:
            mean, spread = 34, 4      # one clearly poor fielder
        else:
            mean, spread = 58, 7      # six average fielders
        fielders.append(Fielder(
            name,
            _roll(rng, mean, spread), _roll(rng, mean, spread),
            _roll(rng, mean, spread), _roll(rng, mean, spread), _roll(rng, mean, spread),
        ))
    return fielders


def weakest_fielder(fielders):
    """Return the fielder with the lowest overall ability (for the ▼ marker on screen)."""
    return min(fielders, key=lambda f: f.overall())
