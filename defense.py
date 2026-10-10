"""The defensive alignment (which fielder stands where) and the shift choice.

Defense holds
  - seven Fielders
  - a position -> Fielder assignment (freely swappable)
  - the alignment (defensive shift)

expected_defensive_efficiency() is a *reference number*, not the best answer.
The player decides for themselves, using the batter's handedness, tendencies, and the pitch calls.
"""

from fielders import (
    POSITION_REQUIREMENTS,
    POSITIONS,
    make_default_fielders,
    position_fit,
    weakest_fielder,
)

# The available shifts (key: internal name / value: display name)
ALIGNMENTS = {
    "standard": "Standard",
    "pull": "Shade pull",
    "oppo": "Shade opposite",
    "infield_in": "Infield in",
    "no_doubles": "No doubles",
    "dp": "Double-play depth",
    "bunt": "Bunt defense",
}

_CORNERS = ("1B", "3B")

_INFIELD = ("1B", "2B", "3B", "SS")


class Defense:
    def __init__(self, fielders=None, rng=None):
        # Without an rng, fielder abilities are not tied to the random seed (so --seed cannot
        # reproduce them). generate_random_situation must always pass the game's rng.
        self.fielders = fielders if fielders is not None else make_default_fielders(rng)
        self.alignment = "standard"
        # Start by placing players in POSITIONS order. The player swaps them later.
        self.assignment = {pos: self.fielders[i] for i, pos in enumerate(POSITIONS)}

    def fielder_at(self, position):
        return self.assignment[position]

    def position_of(self, fielder):
        for pos, f in self.assignment.items():
            if f is fielder:
                return pos
        return None

    def swap(self, pos_a, pos_b):
        """Swap the fielders at two positions."""
        self.assignment[pos_a], self.assignment[pos_b] = (
            self.assignment[pos_b], self.assignment[pos_a])

    def set_alignment(self, key):
        if key not in ALIGNMENTS:
            raise ValueError(f"unknown alignment: {key}")
        self.alignment = key

    def describe_alignment(self):
        return ALIGNMENTS[self.alignment]

    def weakest(self):
        return weakest_fielder(self.fielders)

    def expected_defensive_efficiency(self):
        """A 0-100 reference number: the average fit, weighted toward positions that see more balls."""
        total_share = sum(POSITION_REQUIREMENTS[p]["ball_share"] for p in POSITIONS)
        score = sum(
            POSITION_REQUIREMENTS[p]["ball_share"] * position_fit(self.assignment[p], p)
            for p in POSITIONS
        )
        return score / total_share

    def alignment_out_adjust(self, position, batted_ball):
        """Plus or minus (change in out probability) depending on how well the chosen shift fits this ball.

        This is the part that says "if the shift and the pitch calls click, more outs; if not, more hits".
        """
        a = self.alignment
        direction = batted_ball.direction
        ball_type = batted_ball.ball_type
        infield = position in _INFIELD

        if a == "standard":
            return 0.0

        if a == "pull":                       # shade toward the pull side
            if direction == "pull":
                return 0.08                   # the ball came where we were shaded
            if direction == "oppo":
                return -0.07                  # beaten the other way, a hole opens up
            return 0.0

        if a == "oppo":                       # shade the opposite way (mirror of pull)
            if direction == "oppo":
                return 0.08
            if direction == "pull":
                return -0.07
            return 0.0

        if a == "infield_in":                 # infield in
            if infield and ball_type == "ground":
                return 0.10                   # playing in, they get to it faster
            if ball_type == "line":
                return -0.08                  # a ball over their heads gets through for a hit
            return 0.0

        if a == "no_doubles":                 # outfield deep to cut off extra bases
            if not infield and ball_type in ("line", "fly"):
                return 0.06                   # deep balls handled in front
            if not infield and ball_type == "ground":
                return -0.06                  # a big gap opens in front of the outfielders
            return 0.0

        if a == "dp":                         # double-play depth (middle infielders toward the bag)
            if position in ("SS", "2B") and ball_type == "ground":
                return 0.07
            if position in ("1B", "3B") and ball_type == "ground":
                return -0.05                  # the gaps between 3B-SS and 1B-2B widen
            return 0.0

        if a == "bunt":                       # bunt defense (1B and 3B crash in)
            if position in _CORNERS and ball_type == "ground":
                if batted_ball.hardness == "soft":
                    return 0.14                # a bunt or soft roller is fielded quickly
                if batted_ball.hardness == "hard":
                    return -0.12               # too close in to react to a hard ball
                return -0.03                   # a normal ball also finds less range
            return 0.0

        return 0.0


def make_default_defense(rng=None):
    return Defense(make_default_fielders(rng))
