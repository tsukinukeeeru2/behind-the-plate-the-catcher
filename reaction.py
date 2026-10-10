"""Expresses the batter's reaction as "a face + a short line".

Philosophy (carried over from the previous version):
  What comes back is NOT the batter's true state itself.
  It looks only at the result of one pitch from judge.py (timing, whether he swung, how he took it),
  decides an "honest category", and then
    - with probability REACTION_MISLEAD_RATE returns the "opposite impression"
    - with probability REACTION_REVEAL_RATE returns the honest category
    - otherwise returns a low-information category (neutral / uncertain / frustrated)

How faces avoid handing you "the answer" directly:
  1. Each category has several face candidates and several lines (it changes every time)
  2. The same internal state can scatter across several categories
  3. Neutral / mislead categories are mixed in
  4. A face alone can never pin down the internal state 100%

Return value:
    {"face": "(・_・;)", "ascii_face": ":-S",
     "text": "Briefly re-set his timing",
     "category": "uncertain", "kind": "reveal"}

This file owns "showing facial expressions". No faces are written in judge.py / batter.py.
"""

import random
import sys

import ansi
from constants import REACTION_MISLEAD_RATE, REACTION_REVEAL_RATE, zone_x, zone_y

# category -> display color (colors the face)
_CAT_COLOR = {
    "jammed": "bred", "out_front": "bred", "frustrated": "bred", "surprised": "bmagenta",
    "confident": "bgreen", "locked_in": "bgreen",
    "comfortable": "byellow", "defensive": "bcyan",
    "uncertain": "bcyan", "neutral": "grey",
}

# --- Facial-expression categories, trimmed to ten ---
# kaomoji : emoticons readable in Windows Terminal and the like (first choice)
# ascii   : pure-ASCII substitutes (for when the environment garbles them)
# texts   : short reaction lines for the category (several)
FACE_TABLE = {
    "jammed": {  # jammed
        "kaomoji": ["(-_-;)", "(>_<)", "(๑•́ㅿ•̀)"],
        "ascii": [">_<", "-_-;", "x_x"],
        "texts": ["He looked a little jammed", "Jammed and late on it", "Got the bat on it but looked cramped"],
    },
    "out_front": {  # lunged out front
        "kaomoji": ["(´・ω・｀)", "(＞ｍ＜)", "( ˘•ω•˘ )"],
        "ascii": [":-/", "x_x", ":-("],
        "texts": ["He lunged out front", "Timing broke down, way out in front", "His upper body flew forward"],
    },
    "confident": {  # confident / right on it
        "kaomoji": ["(￣ー￣)", "(｀・ω・´)", "( ¯꒳¯ )"],
        "ascii": ["^_~", ">:)", "-_-b"],
        "texts": ["He looks settled in the box", "Held the bat like he owns the plate", "Gave a small nod and reset"],
    },
    "comfortable": {  # a comfortable take
        "kaomoji": ["(⌒,_ゝ⌒)", "(´ー｀)", "( ˘ω˘ )"],
        "ascii": [":-)", "=)", "^_^"],
        "texts": ["Let the outside pitch go with ease", "Read the borderline pitch easily", "Took one without forcing it"],
    },
    "uncertain": {  # unsure
        "kaomoji": ["(・_・;)", "(◎_◎;)", "(・・;)"],
        "ascii": [":-S", "o_O", "?_?"],
        "texts": ["Briefly re-set his timing", "He looked unsure", "Stepped out of the box to take a breath"],
    },
    "surprised": {  # caught off guard
        "kaomoji": ["(ﾟДﾟ)", "(°口°)", "Σ(・□・;)"],
        "ascii": [":-O", "O_o", "!_!"],
        "texts": ["He looked caught off guard", "Recoiled in surprise", "Reacted a beat late"],
    },
    "frustrated": {  # annoyed
        "kaomoji": ["(-\"-)", "(￢_￢)", "(#`д´)"],
        "ascii": [">:(", "-.-", "`_`"],
        "texts": ["Took an irritated swing", "Tilted his head, puzzled", "Seemed to mutter under his breath"],
    },
    "locked_in": {  # a full, confident swing
        "kaomoji": ["(๑•̀ㅂ•́)و", "(ｷﾘｯ)", "( •̀ω•́ )"],
        "ascii": ["\\o/", ">_>b", "=D"],
        "texts": ["Ripped right through the swing", "A powerful, committed cut", "Followed all the way through"],
    },
    "defensive": {  # fighting off pitches
        "kaomoji": ["(；・∀・)", "(・∀・;)", "( ；´Д｀)"],
        "ascii": [";-)", ":-|", ";_;"],
        "texts": ["Hung in and fouled it off", "Battling and narrowing it down", "Backed into protect mode"],
    },
    "neutral": {  # blank / low information
        "kaomoji": ["(´・_・`)", "(・_・)", "( ᐛ )"],
        "ascii": [":-|", "._.", "-_-"],
        "texts": ["Set his stance without changing expression", "Stepped back in calmly", "Nothing visible changed"],
    },
}

FACE_CATEGORIES = list(FACE_TABLE)

# The "opposite impression" table used for a MISLEAD
_OPPOSITE = {
    "jammed": "confident",
    "out_front": "comfortable",
    "confident": "uncertain",
    "comfortable": "surprised",
    "uncertain": "confident",
    "surprised": "confident",
    "frustrated": "comfortable",
    "locked_in": "defensive",
    "defensive": "confident",
    "neutral": "neutral",
}

# Low-information "blur" categories
_AMBIGUOUS = ["neutral", "uncertain", "frustrated"]


def _true_category(course, outcome):
    """The "honest category" matching the internal truth (timing, how he took it)."""
    timing = outcome["timing"]
    swung = outcome["swung"]
    in_zone = outcome["in_zone"]
    result = outcome["result"]

    if not swung:
        if not in_zone and (zone_x(course) == "out" or zone_y(course) != "mid"):
            return "comfortable"
        if zone_x(course) == "in":
            return "surprised"
        return "neutral"

    if timing == "late":
        return "jammed"
    if timing == "early":
        return "out_front"

    # timing == "on_time" and he swung
    if result == "Whiff":
        return "locked_in"
    if result == "Foul":
        return "defensive"
    if result in ("Single", "Double", "Triple", "Home run"):
        return "confident"
    return "locked_in"   # an out (even a well-hit out, etc.)


def _stdout_supports_kaomoji():
    """Whether the current output can show kaomoji (non-ASCII) without garbling."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "（´・ω・｀）".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def describe_reaction(pitch_type, course, outcome, rng=None):
    """One pitch's result -> a reaction dict (face + line + category).

    pitch_type is unused for now; it is accepted so a future version can vary the gestures by pitch.
    `course` is expected to be the actual course (actual_course).
    """
    rng = rng or random

    true_cat = _true_category(course, outcome)

    roll = rng.random()
    if roll < REACTION_MISLEAD_RATE:
        category, kind = _OPPOSITE[true_cat], "mislead"
    elif roll < REACTION_MISLEAD_RATE + REACTION_REVEAL_RATE:
        category, kind = true_cat, "reveal"
    else:
        category, kind = rng.choice(_AMBIGUOUS), "ambiguous"

    entry = FACE_TABLE[category]
    return {
        "face": rng.choice(entry["kaomoji"]),
        "ascii_face": rng.choice(entry["ascii"]),
        "text": rng.choice(entry["texts"]),
        "category": category,
        "kind": kind,
    }


def render_reaction_block(reaction, ascii_only=None):
    """For the CLI. Lays it out so the face jumps out at you.

        BATTER REACTION
            (・_・;)
          Briefly re-set his timing
    """
    if ascii_only is None:
        ascii_only = not _stdout_supports_kaomoji()
    face = reaction["ascii_face"] if ascii_only else reaction["face"]
    color = _CAT_COLOR.get(reaction["category"], "white")
    head = ansi.dim("BATTER REACTION")
    return f"{head}\n    {ansi.paint(face, 'bold', color)}\n  {ansi.paint(reaction['text'], color)}"
