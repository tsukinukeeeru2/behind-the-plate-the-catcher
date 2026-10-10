"""A replay viewer that pulls the highlights out of one saved game.

    python replay.py                       # the newest game in logs/
    python replay.py logs/game_XXXX.json   # a specific game

It reuses, as is, the classification strategy.build_analysis() already has --
good calls / risky calls / unlucky / lucky / extra-base hits / defense fit / fooled his guess --
and just re-sorts it in the order things happened (pitch_number). It adds no judging logic.
"""

import glob
import json
import os
import sys

import ansi
from strategy import build_analysis

_TAGS = [
    ("good_calls", "◎ Good call", "bgreen"),
    ("risky_calls", "△ Risky call", "byellow"),
    ("unlucky", "★ Unlucky (good decision)", "bcyan"),
    ("lucky", "★ Lucky (loose decision)", "byellow"),
    ("extra_base", "◇ Gave up extra bases", "bred"),
    ("defense_moments", "Defense fit", "bgreen"),
    ("fooled_moments", "Fooled his guess", "bcyan"),
]


def _latest_log():
    paths = sorted(glob.glob("logs/game_*.json"))
    return paths[-1] if paths else None


def _collect_highlights(analysis):
    """Group into pitch_number -> [(tag text, color), ...]."""
    tags = {}
    for key, label, color in _TAGS:
        for item in analysis[key]:
            tags.setdefault(item[0], []).append((label, color))
    return tags


def render_replay(log):
    situation = log.get("situation", {})
    final = log.get("final", {})
    pitch_log = log.get("pitches", [])
    by_number = {p["pitch_number"]: p for p in pitch_log}
    analysis = build_analysis(pitch_log)
    highlights = _collect_highlights(analysis)

    lines = [
        ansi.bold(ansi.cyan(f"=== Replay: {situation.get('inning', '?')} ===")),
        f"Start {situation.get('start', ['?', '?'])[0]} - {situation.get('start', ['?', '?'])[1]}"
        f"   ->   Final {final.get('our', '?')} - {final.get('opp', '?')}"
        f" ({final.get('result', '?')} / {final.get('status', '?')})",
        "",
        ansi.bold(f"Highlights ({len(highlights)} pitches)"),
    ]
    if not highlights:
        lines.append(ansi.dim("  Nothing stood out (a quiet half inning)"))
    for num in sorted(highlights):
        entry = by_number.get(num)
        if entry is None:
            continue
        tag_text = " / ".join(ansi.paint(label, color) for label, color in highlights[num])
        call_label = entry.get("call_label_text", entry["call_label"])
        lines.append(f"  Pitch {num:>2}  {call_label:<28} -> {entry['result']:<9}"
                     f"  decision_quality={entry['decision_quality']:+.2f}")
        lines.append(f"            {tag_text}")
        if entry.get("sequence_label") and entry["sequence_label"] != "Nothing notable":
            lines.append(ansi.dim(f"            Setup: {entry['sequence_label']}"))

    reads = log.get("reads")
    if reads and reads.get("rows"):
        lines.append("")
        lines.append(ansi.magenta(f"Catcher's notes graded: {reads['correct']} / {reads['graded']} right"))
        lines.extend(f"  {row.strip()}" for row in reads["rows"])

    lines.append("")
    lines.append(ansi.bold(f"Decision quality (average): {analysis['decision_quality_label']}"))
    return "\n".join(lines)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_log()
    if path is None or not os.path.exists(path):
        print("No log found. Play a game with python main.py first "
              "(or pass a path: python replay.py <logs/game_....json>).")
        return
    with open(path, encoding="utf-8") as fp:
        log = json.load(fp)
    print(ansi.dim(f"({path})"))
    print(render_replay(log))


if __name__ == "__main__":
    main()
