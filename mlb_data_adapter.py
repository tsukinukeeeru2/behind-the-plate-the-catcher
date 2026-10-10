"""An Adapter that turns real data (MLB Stats API / Baseball Savant-style data) into
the Game Engine's Batter.

    MLB Stats API / Savant (raw data, any shape)
              ↓
    mlb_data_adapter.py (this file)
              ↓
    dicts (records) with the same keys as lineup._BATTER_FIELDS
              ↓
    Lineup.from_records(records) -> Batter objects -> Game Engine

The Game Engine side (judge.py and below) only knows the shape of a Batter object.
Even if the external data format (the API response shape) changes, this file is the only
one to fix -- that is the "keep it swappable" boundary.

What it can and cannot do (an important simplification):
    Public stats such as avg / obp / slg / bats can be used from the raw data as is.
    But the game's own hidden parameters -- discipline, aggression, weak_pitch, guess_bias,
    two_strike_ability, pressure_tolerance -- cannot be pinned down from publicly available
    stats alone. So it either "estimates" a reasonably close value from public stats, or,
    for what cannot be determined, rolls it deterministically from the player's name
    (= the same player always gets the same value). It is only a hunch rolled with random
    numbers and does not match the player's real tendencies.

About hot_course / weak_course (strong / weak locations):
    If the raw data includes a "zone_profile" (results for each of the 9 locations, higher =
    stronger), _hot_weak_from_zone_profile() computes hot_course / weak_course from real data.
    Take the per-zone results Baseball Savant publishes on a player page, convert them by hand
    into this game's 9-zone notation (the keys of constants.COURSES), pass them as zone_profile,
    and you get real strong / weak locations. Without a zone_profile it falls back to the
    deterministic "hunch" from the player's name (the same treatment as the existing
    SAMPLE_LINEUP of fictional players).
    This adapter does NOT fetch Baseball Savant's zone data automatically
    -- Savant's zone numbers (1-14, from the catcher's view) change the meaning of
    "inside / outside" depending on the batter's handedness, so converting them correctly
    into this game's "always from the batter's view" notation needs checking against real data,
    and that is not attempted here. Rather than implement an unverified conversion and call it
    "real data fetching", this only provides a hook that uses a given zone_profile correctly, plus tests.
"""

import json
import random
import urllib.error
import urllib.request

from constants import COURSES
from lineup import Lineup
from pitch_data import all_pitch_keys

_SAMPLE_FIXTURE = "mlb_data/sample_lineup_raw.json"

# MLB Stats API (public, free, no API key): https://statsapi.mlb.com/
# Example team IDs: 119 = Los Angeles Dodgers, 147 = New York Yankees, and so on.
_MLB_STATS_API_TIMEOUT = 6.0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _pct(raw, key, default):
    """Accept either a 0-100 percentage or a 0-1 fraction."""
    v = raw.get(key)
    if v is None:
        return default
    return v / 100.0 if v > 1.0 else v


def _seeded_pick(name, key, choices):
    """Deterministically pick one from the player name + key (the same player gets the same value every time).

    A "hunch" for values that public stats alone cannot decide, like a weak location.
    It is not a real hot / cold zone (stated in the README).
    """
    return random.Random(f"{name}:{key}").choice(choices)


def _hot_weak_from_zone_profile(zone_profile):
    """Decide hot / weak from per-zone results for the 9 locations (higher = the batter is stronger there).

    zone_profile is a {course_key: number} dict (it may cover only some of the 9 keys of COURSES).
    It is meant to be built from real data like Baseball Savant -- pass keys that match this game's
    9-zone notation (constants.COURSES) (the way "inside / outside" changes with the batter's
    handedness is already aligned to this game's notation).
    If fewer than 2 values are valid it returns None, and the caller falls back to the "hunch".
    """
    valid = {c: v for c, v in (zone_profile or {}).items() if c in COURSES and v is not None}
    if len(valid) < 2:
        return None
    hot_course = max(valid, key=valid.get)
    weak_course = min(valid, key=valid.get)
    if hot_course == weak_course:
        return None
    return hot_course, weak_course


def normalize_batter_record(raw):
    """Convert one player's MLB-style raw data (a dict) into a shape Batter can accept.

    The raw data only has to have name / bats / avg / obp / slg.
    whiff_pct, chase_pct, pull_pct, k_pct, bb_pct, gb_pct, speed_pct are used if present
    (column names close to Savant's leaderboards). If missing, they fall back to league-average-like defaults.
    """
    name = raw["name"]
    bats = raw["bats"]
    avg = float(raw["avg"])
    obp = float(raw["obp"])
    slg = float(raw["slg"])

    whiff = _pct(raw, "whiff_pct", 0.24)
    chase = _pct(raw, "chase_pct", 0.30)
    pull = _pct(raw, "pull_pct", 0.40)
    k_rate = _pct(raw, "k_pct", 0.22)
    bb_rate = _pct(raw, "bb_pct", 0.08)
    gb = _pct(raw, "gb_pct", 0.45)
    speed = raw.get("speed_pct", 50)

    power = _clamp((slg - 0.320) / 0.420, 0.15, 0.95)
    contact = _clamp(1.0 - (whiff - 0.10) / 0.35, 0.15, 0.92)
    discipline = _clamp(0.5 + (bb_rate - 0.08) * 3.0 - (chase - 0.28) * 1.5, 0.15, 0.90)
    aggression = _clamp(0.5 + (chase - 0.28) * 1.2 - (bb_rate - 0.08) * 1.5, 0.20, 0.85)
    two_strike_ability = _clamp(0.6 - (k_rate - 0.22) * 1.5, 0.20, 0.85)

    if slg >= 0.520 and power >= 0.55:
        coarse_type = "power"
    elif obp - slg >= 0.05:
        coarse_type = "patient"
    elif chase >= 0.34 and bb_rate <= 0.06:
        coarse_type = "free_swinger"
    elif contact >= 0.65 and power < 0.55:
        coarse_type = "contact"
    else:
        coarse_type = "average"

    zone_pick = _hot_weak_from_zone_profile(raw.get("zone_profile"))
    if zone_pick is not None:
        hot_course, weak_course = zone_pick
    else:
        hot_course = _seeded_pick(name, "hot", list(COURSES))
        weak_course = _seeded_pick(name, "weak", list(COURSES))
        # If the two hunch draws happen to pick the same location, it becomes "strength = weakness".
        # Only in that case, redraw deterministically from the remaining locations.
        if weak_course == hot_course:
            weak_course = _seeded_pick(name, "weak_alt",
                                      [c for c in COURSES if c != hot_course])

    return {
        "name": name, "bats": bats, "coarse_type": coarse_type,
        "avg": round(avg, 3), "obp": round(obp, 3), "slg": round(slg, 3),
        "power": round(power, 2), "contact": round(contact, 2),
        "discipline": round(discipline, 2), "chase_rate": round(_clamp(chase, 0.10, 0.50), 2),
        "whiff_rate": round(_clamp(whiff, 0.06, 0.45), 2), "aggression": round(aggression, 2),
        "pull": round(_clamp(pull, 0.20, 0.75), 2), "gb_tendency": round(_clamp(gb, 0.25, 0.65), 2),
        # Per-pitch-family results (vs_fastball etc.) are not in this simple data, so neutral values
        "vs_fastball": 0.5, "vs_breaking": 0.5, "vs_offspeed": 0.5,
        "hot_course": hot_course,
        "weak_course": weak_course,
        "weak_pitch": _seeded_pick(name, "weak_pitch", all_pitch_keys()),
        "guess_bias": round(_clamp(0.5 + (chase - 0.28) * 0.6, 0.30, 0.80), 2),
        "two_strike_ability": round(two_strike_ability, 2),
        "pressure_tolerance": 0.5,
        "speed": int(_clamp(speed, 20, 90)),
    }


def load_fixture_lineup(path=_SAMPLE_FIXTURE):
    """Read the bundled sample raw data (mlb_data/sample_lineup_raw.json) and build a
    Lineup. It always works, even with no internet connection.
    """
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    records = [normalize_batter_record(b) for b in data["batters"]]
    return Lineup.from_records(records)


def fetch_live_roster_raw(team_id, season):
    """Fetch batter stats from the MLB Stats API (using only the standard library's urllib).

    On success it returns a list of raw data that normalize_batter_record() can read.
    If it fails (no network, the API format changed, etc.) it raises an exception
    (RuntimeError) wrapping the cause -- the caller (build_demo_lineup) catches it
    and falls back to the sample data.
    """
    url = (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
          f"?rosterType=active&season={season}")
    try:
        with urllib.request.urlopen(url, timeout=_MLB_STATS_API_TIMEOUT) as resp:
            roster = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        raise RuntimeError(f"Could not connect to the MLB Stats API (roster): {err}") from err

    raw_batters = []
    for entry in roster.get("roster", []):
        pos = entry.get("position", {}).get("abbreviation")
        if pos in ("P", "TWP"):
            continue
        person = entry["person"]
        stat_url = (f"https://statsapi.mlb.com/api/v1/people/{person['id']}/stats"
                   f"?stats=season&season={season}&group=hitting")
        try:
            with urllib.request.urlopen(stat_url, timeout=_MLB_STATS_API_TIMEOUT) as resp:
                stat_data = json.loads(resp.read().decode("utf-8"))
            splits = stat_data["stats"][0]["splits"]
            if not splits:
                continue
            s = splits[0]["stat"]
            bats = person.get("batSide", {}).get("code", "R")
            if bats not in ("R", "L"):
                bats = "R"   # this game does not support switch hitters ("S"). Simplified: treat as R
            raw_batters.append({
                "name": person["fullName"], "bats": bats,
                "avg": float(s["avg"]), "obp": float(s["obp"]), "slg": float(s["slg"]),
            })
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError,
                KeyError, IndexError, ValueError):
            continue   # skip only this player (do not fail the whole API call)
        if len(raw_batters) >= 9:
            break

    if len(raw_batters) < 9:
        raise RuntimeError(f"Only {len(raw_batters)} batters could be fetched (9 are needed)")
    return raw_batters[:9]


def build_demo_lineup(prefer_live=False, team_id=147, season=2024):
    """Build one Lineup for the MLB DATA DEMO.

    With prefer_live=True it actually tries the MLB Stats API. If that fails
    (network down, API format change, fewer than nine batters -- whatever the reason)
    it automatically falls back to the bundled sample data -- the Web / CLI itself
    never crashes even if this demo fails.
    """
    if prefer_live:
        try:
            raw = fetch_live_roster_raw(team_id, season)
            records = [normalize_batter_record(b) for b in raw]
            return Lineup.from_records(records), "live"
        except Exception as err:   # fall back to the sample whatever the reason
            print(f"(Could not use the MLB Stats API, so using the sample data: {err})")
    return load_fixture_lineup(), "fixture"
