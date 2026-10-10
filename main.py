"""Entry point (CLI). A game where you, the catcher, hold a late-inning half inning together.

    python main.py
    python main.py --lineup lineups/sample_team.json   # swap the lineup (#C)

It only runs the show:
    creating and holding the situation  -> match_state.py
    lineup / pitcher                    -> lineup.py / pitcher.py
    judging one pitch + surrounding     -> engine.py (the Game Engine that calls judge.py internally)
    pitch intents and analysis          -> strategy.py
    building the screen (CLI)           -> ui.py / ballpark_view.py / reaction.py

webapp.py (the Web version) calls the same engine.py. The judging logic is never implemented twice.
"""

import argparse
import os
import random
import sys

# On Windows, redirecting output with something like `python main.py > log.txt` falls back to
# cp932 outside a terminal, and box-drawing characters and kaomoji raise UnicodeEncodeError.
# Replace anything that cannot be shown with "?" so the game keeps going.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import ansi
from ballpark_view import render_field
from defense import ALIGNMENTS
from engine import begin_at_bat, maybe_resolve_sign_steal, resolve_one_pitch, save_game_log
from qte import catcher_return_ball
from fielders import POSITIONS
from lineup import load_lineup_file
from match_state import generate_random_situation
from pitch_data import repertoire_options
from strategy import PITCH_INTENTS
from ui import (
    render_analysis,
    render_dashboard,
    render_fielder_table,
    render_inning_result,
    render_intro,
    render_play_result,
    render_situation_card,
    render_spray,
)


def choose(prompt, options):
    """Have the player pick one from `options` (a dict of key -> display name) and return the chosen key."""
    keys = list(options.keys())
    print(prompt)
    for i, key in enumerate(keys, start=1):
        print(f"  {i}: {options[key]}")
    while True:
        answer = input("Enter a number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        print("Please enter a valid number.")


_COURSE_GRID = ["in_hi", "mid_hi", "out_hi",
                "in_mid", "mid_mid", "out_mid",
                "in_lo", "mid_lo", "out_lo"]


def choose_course(prompt="Target location (3x3, by number):", allow_unknown=False):
    print(prompt)
    print("   1 Hi-In   2 Hi    3 Hi-Out")
    print("   4 In      5 Mid   6 Out")
    print("   7 Lo-In   8 Lo    9 Lo-Out")
    if allow_unknown:
        print("   0 Not sure")
    lo = 0 if allow_unknown else 1
    while True:
        a = input("Enter a number: ").strip()
        if a.isdigit() and lo <= int(a) <= 9:
            return None if a == "0" else _COURSE_GRID[int(a) - 1]
        print("Please enter a valid number.")


def choose_action(state):
    extras = ""
    if not all(state.runners):          # you can walk him unless the bases are loaded (only a bases-loaded walk forces in a run)
        extras += "    [w] Intentional walk"
    if state.can_change_pitcher():
        extras += "    [p] Pitching change"
    print(f"[Enter] Pitch    [d] Defense    [m] Note a read{extras}    [q] Quit")
    answer = input("> ").strip().lower()
    return {"d": "defense", "m": "memo", "w": "walk", "p": "change", "q": "quit"}.get(answer, "pitch")


def defense_menu(defense):
    position_options = {pos: pos for pos in POSITIONS}
    while True:
        print()
        print(render_field(defense))
        print(render_fielder_table(defense))
        print(" 1: Swap players   2: Change the shift   0: Back")
        cmd = input("> ").strip()
        if cmd == "1":
            a = choose("Position of the player to move:", position_options)
            b = choose("Position to swap with:", position_options)
            defense.swap(a, b)
            print(f"-> Swapped {a} and {b}.")
        elif cmd == "2":
            key = choose("Shift:", ALIGNMENTS)
            defense.set_alignment(key)
            print(f"-> Shift set to '{ALIGNMENTS[key]}'.")
        elif cmd == "0":
            return


def change_pitcher_menu(state):
    """If there are several relievers, let the player choose (with only one, he comes in automatically)."""
    if len(state.bullpen) == 1:
        index = 0
    else:
        options = {str(i): f"{p.name} ({'RHP' if p.throws == 'R' else 'LHP'})"
                   for i, p in enumerate(state.bullpen)}
        index = int(choose("Pitching change: pick the pitcher to send in:", options))
    new_pitcher = state.change_pitcher(index)
    if new_pitcher:
        print(f">>> Pitching change. {new_pitcher.name} ({'RHP' if new_pitcher.throws == 'R' else 'LHP'}) "
              "takes the mound. The pitch count resets. His pitches and form are unknown until he throws.")


def memo_menu(state):
    """Record your "read" on this batter (#B). It is graded against the truth after the game."""
    print(f"\nYour read on batter #{state.lineup.spot_number()}")
    wait = choose("What is he looking for?", {"fastball": "Fastball", "offspeed": "Off-speed",
                                              "unknown": "Not sure"})
    weak = choose_course("Where does he look weak?", allow_unknown=True)
    state.record_read(wait=None if wait == "unknown" else wait, weak=weak)
    print("-> Noted (it will be graded after the game).")


def play_one_pitch(state, rng):
    tactic_msg = begin_at_bat(state, rng)
    if tactic_msg:
        print(ansi.bold(ansi.yellow(f">>> {tactic_msg} (counter with a pitchout or a high fastball)")))
    for msg in maybe_resolve_sign_steal(state, rng):
        print(msg)

    # If you caught the previous pitch, return it to the pitcher on rhythm (no mashing, three beats)
    tempo = 0.0
    last = state.history.last()
    if last is not None and last.get("result") in ("Ball", "Strike", "Whiff"):
        tempo = catcher_return_ball(rng)

    pitch_type = choose("Pitch type:", repertoire_options(state.pitcher))
    course = choose_course()
    intent = choose("Intent:", PITCH_INTENTS)

    outcome, messages = resolve_one_pitch(state, pitch_type, course, intent, rng, tempo=tempo)

    print()
    print(render_play_result(outcome))
    for msg in messages:
        print(msg)
    return outcome


_DIFFICULTIES = ("beginner", "normal", "expert")


def _parse_args():
    p = argparse.ArgumentParser(description="Behind the Plate -- a catcher's game")
    p.add_argument("--lineup", help="opposing lineup file (.json / .csv)")
    p.add_argument("--difficulty", choices=_DIFFICULTIES,
                   default=os.environ.get("PITCHSIM_DIFFICULTY", "normal"),
                   help="beginner = more hints / expert = hide the spray chart (default: normal)")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed. Makes scene generation and pitch judging reproducible")
    p.add_argument("--mlb-demo", action="store_true",
                   help="use a sample lineup of real players (via mlb_data_adapter)")
    p.add_argument("--mlb-live", action="store_true",
                   help="use with --mlb-demo. Try to fetch from the MLB Stats API "
                        "(falls back to sample data automatically on failure)")
    return p.parse_args()


def main():
    args = _parse_args()
    rng = random.Random(args.seed) if args.seed is not None else random
    batters = None
    if args.mlb_demo:
        from mlb_data_adapter import build_demo_lineup
        lineup, source = build_demo_lineup(prefer_live=args.mlb_live)
        batters = lineup.batters
        print(f"Loaded the MLB sample lineup ({'MLB Stats API' if source == 'live' else 'sample data'})")
    elif args.lineup:
        batters = load_lineup_file(args.lineup).batters
        print(f"Loaded the lineup file: {args.lineup}")

    state = generate_random_situation(rng=rng, lineup_batters=batters)
    print(render_intro(hints=(args.difficulty == "beginner")))
    print()
    print(render_situation_card(state))

    last_outcome = None
    while not state.is_over():
        print()
        print(render_dashboard(state, last_outcome))
        if args.difficulty != "expert":            # experts read without the spray chart
            print(render_spray(state))
        print(render_field(state.defense, state.batter))

        action = choose_action(state)
        if action == "quit":
            print("Quitting.")
            return
        if action == "defense":
            defense_menu(state.defense)
            continue
        if action == "memo":
            memo_menu(state)
            continue
        if action == "walk":
            print(">>> Intentional walk. Putting the batter on.")
            state.intentional_walk()
            last_outcome = None
            continue
        if action == "change":
            change_pitcher_menu(state)
            continue

        last_outcome = play_one_pitch(state, rng)

    print()
    print(render_inning_result(state))
    print()
    print(render_analysis(state))
    try:
        path = save_game_log(state)
        print("\n" + f"Log saved: {path}")
    except OSError as err:
        print(f"(Failed to save the log: {err})")


if __name__ == "__main__":
    main()
