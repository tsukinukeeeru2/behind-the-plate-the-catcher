"""A stats dashboard that tallies logs from many games (logs/game_*.json).

    python stats.py                  # everything under logs/
    python stats.py logs/game_2026*.json

It never touches the game logic. It just parses the call_label in pitch_log
("pitch/location(intent)") and reports tendencies by location and by intent.
"""

import glob
import json
import re
import sys

import ansi
from constants import COURSE_SHORT

_CALL_RE = re.compile(r"^(?P<pitch>[^/]+)/(?P<course>[^(]+)\((?P<intent>.+)\)$")

# Kept in line with the classification in strategy.py (a batter reaching base = a bad result for the defense)
_BAD_RESULTS = {"Single", "Double", "Triple", "Home run", "Walk", "Error"}
_GOOD_RESULTS = {"Out", "Whiff", "Strikeout"}


def _load_logs(patterns):
    paths = []
    for pat in patterns:
        paths.extend(sorted(glob.glob(pat)))
    games = []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fp:
                games.append(json.load(fp))
        except (OSError, json.JSONDecodeError) as err:
            print(f"(Could not read {path}: {err})")
    return games


def _parse_call(call_label):
    m = _CALL_RE.match(call_label)
    if not m:
        return None, None, None
    return m.group("pitch"), m.group("course"), m.group("intent")


class _Bucket:
    """The tally for one slice (location / intent)."""

    def __init__(self):
        self.n = 0
        self.bad = 0
        self.good = 0
        self.dq_sum = 0.0

    def add(self, result, dq):
        self.n += 1
        self.dq_sum += dq
        if result in _BAD_RESULTS:
            self.bad += 1
        elif result in _GOOD_RESULTS:
            self.good += 1

    def avg_dq(self):
        return self.dq_sum / self.n if self.n else 0.0

    def bad_rate(self):
        return self.bad / self.n if self.n else 0.0


def _print_table(title, rows, label_width=10):
    print(ansi.bold(title))
    header = f"  {'':<{label_width}} {'Pitches':>7} {'Bad %':>8} {'Avg dq':>8}"
    print(ansi.dim(header))
    for label, bucket in rows:
        if bucket.n == 0:
            continue
        rate = bucket.bad_rate()
        color = "bred" if rate >= 0.35 else ("byellow" if rate >= 0.20 else "bgreen")
        print(f"  {label:<{label_width}} {bucket.n:>7} "
              f"{ansi.paint(f'{rate * 100:5.1f}%', color):>8} {bucket.avg_dq():>8.3f}")


def build_dashboard(games):
    by_course, by_intent = {}, {}
    total_pitches = 0
    total_runs = 0
    dq_sum = 0.0

    for g in games:
        total_runs += g.get("final", {}).get("runs_this_inning", 0)
        for entry in g.get("pitches", []):
            pitch, course, intent = _parse_call(entry["call_label"])
            result = entry["result"]
            dq = entry["decision_quality"]
            total_pitches += 1
            dq_sum += dq
            if course:
                by_course.setdefault(course, _Bucket()).add(result, dq)
            if intent:
                by_intent.setdefault(intent, _Bucket()).add(result, dq)

    n_games = len(games)
    print(ansi.bold(ansi.cyan(f"=== Stats dashboard ({n_games} games / {total_pitches} pitches) ===")))
    if n_games:
        print(f"  Average runs allowed: {total_runs / n_games:.2f} per half inning"
              f"   Average decision_quality: {(dq_sum / total_pitches if total_pitches else 0):.3f}")
    print()

    course_rows = sorted(by_course.items(), key=lambda kv: -kv[1].n)
    _print_table("By location (redder = more damage)",
                 [(COURSE_SHORT.get(c, c), b) for c, b in course_rows])
    print()
    intent_rows = sorted(by_intent.items(), key=lambda kv: -kv[1].n)
    _print_table("By intent", intent_rows, label_width=14)


def main():
    patterns = sys.argv[1:] or ["logs/game_*.json"]
    games = _load_logs(patterns)
    if not games:
        print("No logs found (logs/game_*.json). Play a few games with python main.py first.")
        return
    build_dashboard(games)


if __name__ == "__main__":
    main()
