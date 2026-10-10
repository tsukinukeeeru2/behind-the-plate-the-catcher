"""A browser-playable Web UI (standard library only, nothing extra to install).

    python webapp.py                 # starts at http://127.0.0.1:8765
    python webapp.py --port 9000
    python webapp.py --lineup lineups/sample_team.json
    python webapp.py --seed 12345    # make scene generation and pitch judging reproducible
    python webapp.py --host 0.0.0.0  # also reachable from other devices on the same Wi-Fi

It is just the "show runner" that calls the same engine.py as main.py (CLI). It never touches the game logic
(judge.py and below) -- in keeping with this project's consistent policy of never
implementing the logic twice.

    Game Engine (engine.py)
        ↑            ↑
      main.py     webapp.py
      (CLI)         (Web)

It is built mostly from plain form submits, putting stability and ease of automated testing
ahead of looks (see README "Web UI"). The only exception is the catcher's rhythm games
(receive = framing / the steal throw / blocking a ball in the dirt / dropped third strike /
changing signs / bunt fielding. See render_qte), which use a little JS for timing input
-- and even with JS off, a button submit moves on neutrally (bonus 0) (<noscript>).

How it works: the generator in engine.resolve_pitch_flow() yields ("kind",
payload) at each QTE point, and webapp drives it across HTTP requests
(a live generator is kept in SESSION.pitch_gen and .send() is called on every /qte POST).
PITCHSIM_QTE=0 is kept as is (to silence the CLI print/input of qte.*), but
the Web shows its own rhythm UI through the generator, so QTEs DO happen.
"""

import argparse
import html
import http.server
import os
import random
import re
import socketserver
import threading
import traceback
import urllib.parse
import webbrowser

os.environ["PITCHSIM_QTE"] = "0"

from constants import COURSE_SHORT
from defense import ALIGNMENTS
import qte
from engine import begin_at_bat, resolve_pitch_flow, save_game_log
from fielders import POSITIONS, position_fit, weakest_fielder
from lineup import load_lineup_file
from match_state import generate_random_situation
import playview
from pitch_data import pitch_name, repertoire_options, velocity_of
from strategy import (PITCH_INTENTS, build_analysis, build_postgame_report, catcher_rating, grade_reads,
                      intent_label)

_COURSE_ROWS = ["hi", "mid", "lo"]


def _course_cols(bats):
    """The order from the batter's view, inside -> outside. Right-handed batter = in/mid/out, left-handed = out/mid/in
    (the real left/right from the catcher's and pitcher's side mirror, the same idea as a broadcast strike-zone display)."""
    return ["in", "mid", "out"] if bats == "R" else ["out", "mid", "in"]


def _course_grid(bats):
    return [[f"{c}_{r}" for c in _course_cols(bats)] for r in _COURSE_ROWS]


# A "rough break" for each pitch. In a small frame from the catcher's view, it just draws, with one arrow, where the ball comes in and
# how it bends and drops (rough is fine -- it only has to read at a glance).
#   drop  : how much it drops (0 = no drop ... 1 = a big drop)
#   sweep : lateral break (- = slides toward third base / + = runs in toward first base. A guide for a right-handed pitcher)
_PITCH_MOVE = {
    "four_seam": (0.10, 0.05), "two_seam": (0.30, 0.35), "sinker": (0.44, 0.42),
    "cutter": (0.24, -0.32), "slider": (0.50, -0.55), "sweeper": (0.52, -0.92),
    "curveball": (0.88, -0.30), "changeup": (0.58, 0.40), "splitter": (0.80, 0.10),
}


def _pitch_arc_svg(key, size=34):
    """A small SVG that draws a pitch's movement as one arrow from the catcher's view (a rough trajectory image)."""
    drop, sweep = _PITCH_MOVE.get(key, (0.35, 0.0))
    x0, y0 = 20.0, 3.0
    x1 = 20.0 + sweep * 12.0
    y1 = 12.0 + drop * 23.0
    cx = 20.0 + sweep * 2.5              # make the break show up "late"
    cy = y0 + (y1 - y0) * 0.32
    return (f'<svg class="parc" width="{size}" height="{size}" viewBox="0 0 40 40" aria-hidden="true">'
            f'<rect x="6" y="4" width="28" height="30" rx="2" class="pz"/>'
            f'<path d="M{x0:.1f} {y0:.1f} Q{cx:.1f} {cy:.1f} {x1:.1f} {y1:.1f}" class="pl"/>'
            f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="2.6" class="pb"/></svg>')


_SCENARIOS = ("steal", "block", "dropped3", "bunt", "signs")
_DIFFICULTIES = ("beginner", "normal", "expert")
_KEEP = object()   # "leave the current setting alone" marker for start_new_game()


def _apply_scenario(state, name):
    """For testing: set up the opening scene so a specific rhythm game is likely to appear (--scenario).
    The game's probabilities are untouched -- it only gets you to "throw the right pitch in this scene and it appears"."""
    if name == "steal":
        # A fast runner on first only. He steals on a take / whiff / ball
        state.runners, state.runner_speeds, state.outs = [True, False, False], [85, None, None], 0
    elif name == "block":
        # Runners on first and third (second is left empty to avoid sign stealing). Throw a few pitches missing
        # big low (down-in / down-away) and one ends up in the dirt -> blocking
        state.runners, state.runner_speeds, state.outs = [True, False, True], [55, None, 60], 1
    elif name == "dropped3":
        # First base open + two outs. A swinging strikeout on a low pitch in the dirt is a dropped third strike
        state.runners, state.runner_speeds, state.outs = [False, False, True], [None, None, 55], 2
    elif name == "bunt":
        # A runner on first, a close game, a weak hitter. The opponent bunts (the bunt-fielding rhythm game from the first pitch)
        state.runners, state.runner_speeds, state.outs = [True, False, False], [60, None, None], 0
        state.our_score = state.opp_score = max(state.our_score, state.opp_score)
        state.start_our = state.start_opp = state.our_score
        b = state.lineup.current()
        b.coarse_type, b.power = "weak", 0.30
        state.opp_tactic, state.tactic_decided = "bunt", True
    elif name == "signs":
        # A runner on second -> the signs may get stolen before the pitch (rare. Throw a few pitches)
        state.runners, state.runner_speeds, state.outs = [False, True, False], [None, 55, None], 0


# The six rhythm games to clear once each in the pre-game practice. (key, display name, short description)
_TUTORIAL_ITEMS = [
    ("tempo", "Return tempo", "Return the caught ball to the pitcher on rhythm. Space three times in the middle of the green band."),
    ("frame", "Receive (framing)", "Quietly catch a borderline take and make it look like a strike. Once, in the middle."),
    ("wild_block", "Blocking", "Stop the ball in the dirt with your body. First the direction (in / middle / out), then the timing once."),
    ("steal_throw", "Steal throw", "Throw the runner out at second. Transfer, then the throw: two steps."),
    ("field_bunt", "Bunt fielding", "Charge and grab it, then throw: two steps."),
    ("change_signs", "Change signs", "Switch the signs before the runner on second can peek. Once."),
]
_TUT_DIR = {"wild_block": "mid"}   # in practice the ball comes "middle"


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _start_options(form):
    """Read the start-screen / play-again form into start_new_game() arguments (bad values are ignored)."""
    opts = {}
    seed = (form.get("seed") or "").strip()
    if seed.isdigit():
        opts["challenge_seed"] = int(seed)
    scenario = form.get("scenario")
    if scenario == "random":
        opts["scenario"] = None
    elif scenario in _SCENARIOS:
        opts["scenario"] = scenario
    if form.get("difficulty") in _DIFFICULTIES:
        opts["difficulty"] = form["difficulty"]
    return opts


class GameSession:
    """One process = one game (meant for one person playing locally. Simultaneous access is not considered)."""

    def __init__(self, lineup_batters=None, seed=None, difficulty="normal", scenario=None):
        self.lineup_batters = lineup_batters
        self.seed = seed
        self.difficulty = difficulty
        self.scenario = scenario   # the opening scene for drills (--scenario or the start-screen buttons)
        self.game_seed = seed      # the code of the game being played (shown at the end so it can be shared)
        self.rng = random.Random(seed) if seed is not None else random
        self.state = None
        self.started = False
        self.showing_intro = False
        self.messages = []
        self.reaction = None
        self.last_batter_bats = "R"
        self.last_call = None      # {"pitch_type","course","intent"}
        self.last_outcome = None
        self.pending_swap = None
        # State while a rhythm game is running: the live generator and the "QTE to show now" (kind, payload)
        self.pitch_gen = None
        self.pending_qte = None
        # Pre-game practice: clear each of the six rhythms once with GOOD or better
        self.tutorial = {k: False for k, _, _ in _TUTORIAL_ITEMS}
        self.tutorial_skipped = False
        self.log_path = None
        self.play_shown = None     # which pitch's Play Viewer has already auto-played

    def start_new_game(self, challenge_seed=None, scenario=_KEEP, difficulty=None):
        """Start a game. Every game gets a seed (its Challenge Code): the one typed on the start screen,
        else --seed, else a fresh random one. The same code gives the same opening scene."""
        if challenge_seed is not None:
            self.game_seed = challenge_seed
        elif self.seed is not None:
            self.game_seed = self.seed
        else:
            self.game_seed = random.randrange(1000, 10000)
        if scenario is not _KEEP:
            self.scenario = scenario
        if difficulty in _DIFFICULTIES:
            self.difficulty = difficulty
        rng = random.Random(self.game_seed)
        self.rng = rng
        self.state = generate_random_situation(rng=rng, lineup_batters=self.lineup_batters)
        if self.scenario:
            _apply_scenario(self.state, self.scenario)
        self.started = True
        self.showing_intro = True
        self.messages = []
        self.reaction = None
        self.last_call = None
        self.last_outcome = None
        self.pending_swap = None
        self.pitch_gen = None
        self.pending_qte = None
        self.tutorial = {k: False for k, _, _ in _TUTORIAL_ITEMS}
        self.tutorial_skipped = False
        self.log_path = None
        self.play_shown = None

    def finish_if_over(self):
        if self.state is not None and self.state.is_over() and self.log_path is None:
            try:
                self.log_path = save_game_log(self.state)
            except OSError:
                self.log_path = ""


SESSION = GameSession()


def _e(text):
    return html.escape(str(text))


_NAME_PREFIX = re.compile(r"^\d+\s+")


def _bname(batter):
    """The batter's display name. The batting-order number at the front of the design data (such as "3 Carter") is
    shown separately in the order badge, so it is dropped from the name to avoid duplication."""
    return _e(_NAME_PREFIX.sub("", batter.name))


# --- Plain-language explanations of terms for people who do not know baseball (shown on hover / tap) ---
def _term(label, tip):
    """A term with a dotted underline. An explanation appears when you hover or tap."""
    return f'<span class="term" tabindex="0" data-tip="{_e(tip)}">{_e(label)}</span>'


def _help(tip):
    """A "?" badge placed next to a heading. Hover / tap for an explanation."""
    return (f'<span class="term badge" tabindex="0" role="note" '
            f'aria-label="Term explanation: {_e(tip)}" data-tip="{_e(tip)}">?</span>')


_TIP = {
    "pitch_type": "The kind of pitch. There are fast pitches (four-seam = the straight fastball, etc.) and pitches that bend or drop (slider, curveball, etc.). "
                  "The number is the approximate speed (mph). Alternating fast and slow pitches throws off the batter's timing.",
    "course": "Where you aim. The 3x3 grid stands for the strike zone and a bit outside it. Middle = easy to hit, "
              "corners = borderline (harder to hit, but a miss can catch the middle). Left and right are as seen from the catcher.",
    "intent": "What this one pitch is for. Get ahead = go for a strike / Chase = get him to swing at a ball / "
              "Induce weak contact = get a soft hit / Waste pitch = a throwaway that sets up the next / "
              "Freeze him = go for a called strike on the edge / Pitchout = miss big to throw out a runner.",
    "count": "The count. Four B (balls) is a walk to first. Three S (strikes) is a strikeout = an out. "
             "Three OUT ends the half inning. The pitcher has the edge when strikes come first.",
    "runners": "The opposing runners on base. 1B = first, 2B = second, 3B = third. "
               "A runner on third can score even on a fly ball or an infield grounder (= a run), so he is especially dangerous. ▶ means fast.",
    "alignment": "The defensive shift: moving fielders based on the batter's tendencies. "
                 "Shade pull / shade opposite / infield in (cut off the run one step in) / no doubles / bunt defense, and so on. "
                 "If you read it right there are more outs; if he goes the other way it becomes a hit.",
    "fit": "Position fit. A guide (0-99) to a fielder's defensive value when used at that position. "
           "The same player changes by where he plays. The harder the position (shortstop = SS, etc.), the bigger the gap.",
    "ede": "A guide (reference number) to how well the whole defense fits together. Higher means a steadier defense, roughly.",
    "ops": "On-base plus slugging. A rough measure of a batter's overall strength. Above .800 is a dangerous hitter, below .700 is not scary.",
    "reaction": "The batter's reaction (face). It is not always the truth (about 60% honest, about 15% opposite, the rest blurred). "
                "Do not trust a single pitch too much.",
    "framing": "Framing. A catcher's skill: quietly catching a take that barely missed the zone so it looks like "
               "a strike. It works better the closer to a borderline corner you asked.",
    "quality": "Pitch quality (0-100). Higher = a sharper pitch that is harder to hit. It changes every pitch with the pitcher's form and the pitch type.",
}


# ---------------------------------------------------------------------------
# The outer page frame (all the CSS is gathered here)
# ---------------------------------------------------------------------------
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b111c">
<title>Behind the Plate — A Catcher's Game</title>
<style>
:root {{
  --bg:#0b111c; --bg-2:#0f1826; --panel:#141f30; --panel-2:#1b2a40;
  --line:#27374f; --line-2:#324660;
  --ink:#e9eef7; --muted:#93a1b8; --faint:#5f6f88;
  --accent:#3fa9ff; --accent-ink:#06121f;
  --good:#2fd08a; --bad:#ff6060; --warn:#f4b942;
  --grass:#2f8046; --dirt:#b07b3e;
  --r:14px; --r-sm:9px;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 12px 30px rgba(0,0,0,.28);
}}
* {{ box-sizing:border-box; }}
html {{ -webkit-text-size-adjust:100%; font-size:17.5px; }}
@media (max-width:520px) {{ html {{ font-size:16px; }} }}
body {{
  margin:0; padding:0 0 4em; background:var(--bg); color:var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN",
               "Yu Gothic UI", "Segoe UI", "Noto Sans JP", system-ui, sans-serif;
  font-variant-numeric: tabular-nums; line-height:1.5;
  background-image:
    radial-gradient(1100px 500px at 78% -8%, rgba(63,169,255,.10), transparent 60%),
    radial-gradient(900px 500px at -6% 108%, rgba(47,208,138,.07), transparent 60%);
  background-attachment:fixed;
}}
.wrap {{ max-width:1120px; margin:0 auto; padding:0 18px; }}
a {{ color:var(--accent); }}
h1, h2, h3, h4 {{ margin:0; }}
button:focus-visible, input:focus-visible + label, select:focus-visible, a:focus-visible {{
  outline:2px solid var(--accent); outline-offset:2px;
}}

/* --- Brand bar --- */
.brand {{ display:flex; align-items:baseline; gap:.6em; padding:.85em 0 .55em; }}
.brand .mark {{ font-weight:800; letter-spacing:.16em; font-size:.92em;
  color:var(--ink); text-transform:uppercase; }}
.brand .mark .dot {{ color:var(--accent); }}
.brand .sub {{ color:var(--faint); font-size:.72em; letter-spacing:.14em; text-transform:uppercase; }}

/* --- Scoreboard strip (sticky) --- */
.scorebar {{
  position:sticky; top:0; z-index:20;
  background:linear-gradient(180deg, #0c1622, #0a121c);
  border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow), inset 0 1px 0 rgba(255,255,255,.03);
  padding:.55em .9em; margin-bottom:1.1em;
}}
.sb-row {{ display:flex; align-items:center; gap:.75em 1.15em; flex-wrap:wrap; }}
.sb-team {{ display:flex; align-items:baseline; gap:.4em; }}
.sb-team .abbr {{ font-size:.68em; letter-spacing:.14em; color:var(--muted); text-transform:uppercase; }}
.sb-team .run {{ font-size:1.5em; font-weight:800; line-height:1; }}
.sb-team.lead .run {{ color:var(--good); }}
.sb-inning {{ font-weight:800; letter-spacing:.05em; color:#9fd4ff; padding:0 .1em; }}
.sb-sep {{ width:1px; align-self:stretch; background:var(--line); }}
.sb-grp {{ display:flex; align-items:center; gap:.5em; font-size:.82em; color:var(--muted); }}
.sb-grp b {{ color:var(--ink); }}
.pip {{ display:inline-block; width:.62em; height:.62em; border-radius:50%; margin:0 1px;
        background:transparent; border:1.5px solid var(--faint); vertical-align:middle; }}
.pip.on {{ border-color:transparent; }}
.pip.b.on {{ background:var(--warn); }} .pip.s.on {{ background:var(--accent); }}
.pip.o.on {{ background:var(--bad); }}
.warnpill {{ font-size:.72em; font-weight:800; letter-spacing:.06em; color:#fff;
  background:var(--bad); border-radius:1em; padding:.12em .6em;
  animation:pulse 1.3s ease-in-out infinite; }}
@keyframes pulse {{ 0%,100%{{ opacity:1; }} 50%{{ opacity:.45; }} }}
.mini-diamond {{ vertical-align:middle; }}
.mini-diamond .base {{ fill:#22344c; stroke:var(--line-2); stroke-width:2; }}
.mini-diamond .base.on {{ fill:var(--warn); stroke:var(--warn); }}
.mini-diamond .base.fast {{ fill:var(--accent); stroke:var(--accent); }}

/* --- Cards --- */
.card {{
  background:linear-gradient(180deg, var(--panel), var(--bg-2));
  border:1px solid var(--line); border-radius:var(--r);
  padding:1.05em 1.15em; margin-bottom:1.1em; box-shadow:var(--shadow);
}}
.card > h2, .card > h3 {{
  font-size:.72em; color:var(--muted); font-weight:700; letter-spacing:.14em;
  text-transform:uppercase; margin-bottom:.7em;
}}
.card > h2 {{ font-size:.9em; color:var(--ink); }}
.grid2 {{ display:grid; grid-template-columns:1.08fr 1fr; gap:1.1em; align-items:start; }}
.grid2.gcols {{ grid-template-columns:1fr 1.02fr; }}
.eyebrow {{ font-size:.7em; letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
  font-weight:700; margin:.9em 0 .35em; }}
.eyebrow:first-child {{ margin-top:0; }}

/* --- Field diagram --- */
.field {{ position:relative; width:100%; aspect-ratio:1.7; border-radius:12px; overflow:hidden;
  border:1px solid var(--line);
  background:
    radial-gradient(130% 100% at 50% 120%, #4aa862 0%, #388f52 45%, #2c7a43 74%, #256b3b 100%); }}
.field svg {{ position:absolute; inset:0; width:100%; height:100%; }}
.field .align-badge {{ position:absolute; left:8px; top:8px; font-size:.66em; font-weight:800;
  letter-spacing:.08em; text-transform:uppercase; color:#eaf6ee;
  background:rgba(6,16,10,.6); border:1px solid rgba(255,255,255,.18);
  border-radius:1em; padding:.2em .7em; }}
.posbtn {{ position:absolute; transform:translate(-50%,-50%); }}
.posbtn form {{ margin:0; }}
.posbtn button {{
  width:66px; padding:.34em .2em .3em; border-radius:10px;
  border:1px solid rgba(255,255,255,.28); background:rgba(9,18,12,.72);
  color:#eef6f0; font-size:.72em; line-height:1.28; cursor:pointer; text-align:center;
  backdrop-filter:blur(2px); box-shadow:0 4px 12px rgba(0,0,0,.35);
  transition:transform .12s, box-shadow .12s, border-color .12s;
  border-top:2px solid var(--tier, #6fd39a);
}}
.posbtn button:hover {{ transform:translateY(-2px); box-shadow:0 8px 18px rgba(0,0,0,.45); }}
.posbtn button b {{ display:block; font-size:1.08em; letter-spacing:.04em; }}
.posbtn button .fit {{ color:#bfe8cd; font-size:.9em; }}
.posbtn.selected button {{ border-color:#ffd35a; box-shadow:0 0 0 2px #ffd35a, 0 8px 18px rgba(0,0,0,.5); }}
.posbtn.weak button {{ border-top-color:var(--bad); box-shadow:0 0 0 1.5px rgba(255,96,96,.7); }}
.field .homeplate {{ position:absolute; left:50%; bottom:3%; transform:translateX(-50%);
  color:#eef6f0; font-size:.7em; text-align:center; letter-spacing:.06em; }}
.field-legend {{ font-size:.78em; color:var(--muted); margin-top:.55em; }}
@media (max-width:520px) {{ .posbtn button {{ width:52px; padding:.26em .12em; font-size:.64em; }} }}
@media (max-width:380px) {{ .posbtn button {{ width:44px; font-size:.58em; }} }}

/* --- Batter card --- */
.batter-head {{ display:flex; align-items:center; gap:.7em; }}
.batter-num {{ width:2.1em; height:2.1em; flex:none; border-radius:50%; display:grid; place-items:center;
  background:var(--panel-2); border:1px solid var(--line-2); font-weight:800; }}
.batter-head .name {{ font-size:1.18em; font-weight:800; letter-spacing:.02em; }}
.hand {{ font-size:.66em; font-weight:800; letter-spacing:.08em; padding:.15em .5em; border-radius:.5em;
  background:var(--panel-2); border:1px solid var(--line-2); color:var(--muted); }}
.hand.L {{ color:#8fd0ff; border-color:#2b5b82; }}
.hand.R {{ color:#ffcf8f; border-color:#7a5a2c; }}
.tag {{ display:inline-block; font-size:.7em; padding:.12em .55em; border-radius:1em;
  background:var(--panel-2); border:1px solid var(--line-2); color:var(--muted); }}
.slash {{ display:flex; gap:1.1em; font-size:.82em; color:var(--muted); margin:.6em 0 .1em; }}
.slash b {{ color:var(--ink); font-weight:700; }}
.scout {{ font-size:.85em; color:var(--muted); }}
.myread {{ font-size:.82em; color:#e59ad8; margin-top:.35em; }}
.batter-mini {{ display:flex; justify-content:space-between; gap:.6em; font-size:.8em; color:var(--muted);
  padding:.42em .1em; border-top:1px dashed var(--line); }}
.batter-mini:first-of-type {{ margin-top:.5em; }}

/* --- Reaction (face) --- */
.reaction {{ text-align:center; padding:.7em 0 .3em; }}
.reaction .face {{ font-size:2.5em; font-weight:800; line-height:1.1;
  text-shadow:0 2px 12px rgba(0,0,0,.4); }}
.reaction .text {{ color:var(--muted); margin-top:.25em; font-size:.92em; }}
.rc-jammed .face, .rc-out_front .face, .rc-frustrated .face {{ color:var(--bad); }}
.rc-surprised .face {{ color:#c58bff; }}
.rc-confident .face, .rc-locked_in .face {{ color:var(--good); }}
.rc-comfortable .face {{ color:var(--warn); }}
.rc-defensive .face, .rc-uncertain .face {{ color:var(--accent); }}
.rc-neutral .face {{ color:var(--muted); }}

/* --- Strike zone --- */
.zonewrap {{ display:flex; gap:1.5em; flex-wrap:wrap; align-items:flex-start; }}
.zone-shadow {{ display:inline-block; padding:12px; border:1px dashed var(--line-2); border-radius:12px;
  background:radial-gradient(120% 120% at 50% 50%, rgba(63,169,255,.06), transparent 72%); }}
.zone {{ display:grid; grid-template-columns:repeat(3,58px); grid-template-rows:repeat(3,58px); gap:5px; }}
.zone .cell {{ position:relative; border:1px solid var(--line-2); border-radius:7px;
  background:var(--panel-2); }}
.zone label {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  font-size:.76em; color:var(--muted); cursor:pointer; border-radius:7px;
  transition:background .1s, color .1s; }}
.zone label:hover {{ background:rgba(63,169,255,.14); color:var(--ink); }}
.zone input {{ position:absolute; opacity:0; pointer-events:none; }}
.zone input:checked + label {{ background:var(--accent); color:var(--accent-ink); font-weight:800; }}
.marker {{ position:absolute; width:23px; height:23px; border-radius:50%; display:flex;
  align-items:center; justify-content:center; font-size:.7em; font-weight:800; pointer-events:none; }}
.marker.target {{ top:3px; left:3px; background:transparent; color:#bcd8f2;
  border:2px solid var(--accent); box-shadow:0 0 0 1px rgba(0,0,0,.3); }}
.marker.actual {{ bottom:3px; right:3px; background:var(--bad); color:#fff;
  border:2px solid var(--bg); }}
.marker.actual.ontarget {{ background:var(--good); }}
.zone-key {{ font-size:.78em; color:var(--muted); }}
.zone-key .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin:0 .3em 0 .1em;
  vertical-align:middle; }}

/* --- Chips (pitch type / intent) --- */
.callgrid {{ display:flex; flex-wrap:wrap; gap:1.4em 1.8em; align-items:flex-start; margin-top:.3em; }}
.callgrid-zone {{ flex:0 0 auto; }}
.callgrid-intent {{ flex:1 1 200px; min-width:200px; }}
.chiprow {{ display:flex; flex-wrap:wrap; gap:.45em; }}
.chiprow input {{ position:absolute; opacity:0; pointer-events:none; }}
.chiprow label {{ border:1px solid var(--line-2); border-radius:999px; padding:.42em .9em;
  font-size:.86em; cursor:pointer; background:var(--panel-2); color:var(--ink);
  transition:background .1s, border-color .1s, transform .1s; user-select:none; }}
.chiprow label:hover {{ border-color:var(--accent); }}
.chiprow label .v {{ color:var(--muted); font-size:.86em; margin-left:.35em; }}
.chiprow input:checked + label {{ background:var(--accent); color:var(--accent-ink); border-color:var(--accent);
  font-weight:800; }}
.chiprow input:checked + label .v {{ color:var(--accent-ink); opacity:.75; }}

/* --- Pitch trajectory image (rough) --- */
.parc {{ vertical-align:middle; margin-right:.35em; }}
.parc .pz {{ fill:none; stroke:var(--line-2); stroke-width:1; }}
.parc .pl {{ fill:none; stroke:var(--muted); stroke-width:2.2; stroke-linecap:round; }}
.parc .pb {{ fill:var(--muted); }}
.chiprow input:checked + label .parc .pl,
.chiprow input:checked + label .parc .pb {{ stroke:var(--accent-ink); fill:var(--accent-ink); }}
.chiprow input:checked + label .parc .pz {{ stroke:rgba(6,18,31,.4); }}

/* --- Term tooltips (for people who do not know baseball. Explained on hover / tap) --- */
.term {{ border-bottom:1px dotted var(--muted); cursor:help; position:relative; outline:none; }}
.term.badge {{ border:1px solid var(--line-2); border-radius:50%; display:inline-flex;
  align-items:center; justify-content:center; width:1.4em; height:1.4em; font-size:.72em;
  font-weight:800; color:var(--muted); background:var(--panel-2); vertical-align:middle;
  margin-left:.35em; line-height:1; }}
.term.badge:hover, .term:focus.badge {{ color:var(--accent); border-color:var(--accent); }}
.term::after {{
  content:attr(data-tip); position:absolute; left:0; top:calc(100% + .45em); z-index:60;
  width:min(20em, 74vw); white-space:normal; text-align:left;
  background:#0c1826; color:var(--ink); border:1px solid var(--line-2); border-radius:9px;
  padding:.6em .75em; font-size:.8rem; font-weight:400; line-height:1.5; letter-spacing:0;
  text-transform:none; box-shadow:0 10px 30px rgba(0,0,0,.55);
  opacity:0; visibility:hidden; transform:translateY(-3px);
  transition:opacity .12s ease, transform .12s ease; pointer-events:none;
}}
.term:hover::after, .term:focus::after {{ opacity:1; visibility:visible; transform:translateY(0); }}
@media (max-width:560px) {{ .term::after {{ left:auto; right:0; }} }}

/* --- Receive rhythm game (framing a borderline pitch) --- */
.rg-wrap {{ text-align:center; padding:.4em 0 .2em; }}
.rg-call {{ color:var(--muted); font-size:.95em; margin:.2em 0 .1em; }}
.rg-call b {{ color:var(--ink); }}
.rg-track {{ position:relative; height:48px; max-width:440px; margin:1.2em auto .4em;
  border-radius:10px; background:linear-gradient(90deg,#18243c,#24344f,#18243c);
  border:1px solid var(--line-2); overflow:hidden; cursor:pointer;
  touch-action:manipulation; user-select:none; }}
.rg-target {{ position:absolute; top:0; bottom:0; left:41%; width:18%;
  background:rgba(47,208,138,.20);
  border-left:2px solid var(--good); border-right:2px solid var(--good); }}
.rg-target .core {{ position:absolute; top:0; bottom:0; left:50%; width:2px;
  margin-left:-1px; background:var(--good); opacity:.8; }}
.rg-marker {{ position:absolute; top:-3px; bottom:-3px; width:5px; margin-left:-2.5px; left:0;
  background:var(--accent); border-radius:3px; box-shadow:0 0 14px var(--accent); }}
.rg-hint {{ color:var(--muted); font-size:.9em; margin:.55em 0; }}
.rg-key {{ display:inline-block; border:1px solid var(--line-2); border-radius:6px;
  padding:.02em .55em; font-weight:800; color:var(--ink); background:var(--panel-2); }}
.rg-verdict {{ font-size:1.45em; font-weight:800; letter-spacing:.06em; min-height:1.4em; margin:.35em 0; }}
.rg-verdict.perfect {{ color:var(--good); }}
.rg-verdict.good {{ color:var(--warn); }}
.rg-verdict.miss {{ color:var(--bad); }}
.qte-dirs {{ display:flex; gap:.7em; justify-content:center; margin:.9em 0 .2em; }}
.qte-dirs button {{ font-weight:800; padding:.7em 1.5em; font-size:1.05em; }}
.qte-announce {{ text-align:center; font-size:2em; font-weight:800; letter-spacing:.1em;
  min-height:1.35em; margin:.25em 0; color:var(--accent);
  opacity:0; transform:scale(.75); transition:opacity .12s ease, transform .14s ease; }}
.qte-announce.show {{ opacity:1; transform:scale(1); }}
.qte-label {{ text-align:center; font-weight:800; font-size:1.2em; margin:.35em 0 0;
  color:var(--ink); min-height:1.4em; }}
.catcher-line {{ font-size:1.15em; font-weight:800; letter-spacing:.02em; margin:.5em 0;
  padding:.4em .7em; border-radius:.5em; border-left:4px solid; }}
.catcher-line.ok {{ color:var(--good); border-left-color:var(--good); background:rgba(47,208,138,.10); }}
.catcher-line.ng {{ color:var(--bad); border-left-color:var(--bad); background:rgba(255,96,96,.10); }}
.good-call-line {{ margin:.4em 0; font-size:.92em; }}
.gc-badge {{ display:inline-block; font-weight:800; font-size:.9em; color:var(--good);
  border:1px solid var(--good); border-radius:.4em; padding:.05em .5em; margin-right:.4em;
  background:rgba(47,208,138,.10); }}
.tut-list {{ list-style:none; padding:0; margin:.7em 0 .3em; text-align:left; }}
.tut-list li {{ padding:.4em .1em; border-bottom:1px solid var(--line); font-size:.92em; }}
.tut-list li:last-child {{ border-bottom:none; }}
.tut-list li.done {{ color:var(--good); }}
.tut-list li.now {{ color:var(--accent); font-weight:700; }}
.prev-strip {{ font-size:.85em; font-weight:700; margin:.1em 0 .4em; }}
/* --- Return tempo (at the very top of the pitch-call screen. The same format as the other rhythm games, three times) --- */
.tempo-box {{ border:1px solid var(--accent); border-radius:10px; padding:.75em .95em;
  margin-bottom:1em; background:rgba(63,169,255,.08); }}
.tempo-head {{ display:flex; align-items:baseline; gap:.9em; flex-wrap:wrap; }}
.tempo-react {{ font-size:.95em; }}
#tempo-track {{ margin:.6em auto .3em; }}
#tempo-label {{ font-weight:800; color:var(--accent); }}

/* --- Buttons --- */
button, .btn {{
  padding:.55em 1.05em; border-radius:var(--r-sm); border:1px solid var(--line-2);
  background:var(--panel-2); cursor:pointer; font-size:.9em; color:var(--ink);
  transition:background .12s, border-color .12s, transform .1s; font-family:inherit;
}}
button:hover {{ border-color:var(--accent); }}
button:active {{ transform:translateY(1px); }}
button.primary {{ background:linear-gradient(180deg, #4fb2ff, #2f97ec); color:var(--accent-ink);
  border-color:transparent; font-weight:800; letter-spacing:.03em;
  box-shadow:0 6px 18px rgba(63,169,255,.28); }}
button.primary:hover {{ filter:brightness(1.06); }}
button.big {{ font-size:1.05em; padding:.8em 1.9em; }}
select {{ padding:.5em .7em; border-radius:var(--r-sm); border:1px solid var(--line-2);
  background:var(--panel-2); color:var(--ink); font-family:inherit; font-size:.9em; }}
.form-actions {{ display:flex; flex-wrap:wrap; gap:.6em; align-items:center; }}

/* --- The last pitch --- */
.resultline {{ display:inline-block; font-size:1.2em; font-weight:800; letter-spacing:.03em;
  padding:.28em .85em; border-radius:.6em; margin:.5em 0;
  background:var(--panel-2); border:1px solid var(--line-2); border-left-width:4px; }}
.resultline .eyebrow {{ display:inline; font-size:.55em; opacity:.7; letter-spacing:.14em; }}
.resultline.r-out {{ color:var(--good); border-left-color:var(--good); background:rgba(47,208,138,.10); }}
.resultline.r-hit {{ color:var(--bad); border-left-color:var(--bad); background:rgba(255,96,96,.10); }}
.resultline.r-ball {{ color:var(--warn); border-left-color:var(--warn); background:rgba(244,185,66,.10); }}
.resultline.r-strike {{ color:var(--accent); border-left-color:var(--accent); background:rgba(63,169,255,.10); }}
.callline {{ font-size:.9em; color:var(--muted); margin:.15em 0; }}
.callline b {{ color:var(--ink); }}
.msglist {{ margin-top:.7em; border-top:1px solid var(--line); padding-top:.5em; }}
.msg {{ padding:.14em 0; font-size:.9em; color:var(--muted); }}
.msg::before {{ content:"▸ "; color:var(--faint); }}
.msg.bad {{ color:var(--bad); }}
.msg.good {{ color:var(--good); }}

/* --- Pitch history --- */
.tablescroll {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}
table.history {{ width:100%; min-width:340px; border-collapse:collapse; font-size:.86em; }}
table.history th {{ text-align:left; color:var(--muted); font-weight:700; padding:.3em .5em;
  border-bottom:1px solid var(--line); white-space:nowrap; letter-spacing:.03em; }}
table.history td {{ padding:.36em .5em; border-bottom:1px solid var(--line); white-space:nowrap; }}
table.history tr:last-child td {{ border-bottom:none; }}
.rdot {{ display:inline-block; width:.55em; height:.55em; border-radius:50%; margin-right:.45em;
  vertical-align:middle; }}
.res-strike {{ color:var(--accent); }} .res-strike .rdot {{ background:var(--accent); }}
.res-ball {{ color:var(--warn); }} .res-ball .rdot {{ background:var(--warn); }}
.res-hit {{ color:var(--bad); font-weight:700; }} .res-hit .rdot {{ background:var(--bad); }}
.res-out {{ color:var(--good); }} .res-out .rdot {{ background:var(--good); }}
.framed-mark {{ color:var(--accent); font-size:.82em; }}

/* --- Spray chart --- */
pre.spray {{ background:var(--bg); border:1px solid var(--line); color:#9fe3b6;
  padding:.7em .9em; border-radius:8px; font-size:.9em; line-height:1.35; overflow-x:auto; }}

/* --- Hints / tactic warnings --- */
.hint {{ background:rgba(244,185,66,.09); border:1px solid #6a5320; border-radius:10px;
  padding:.75em 1em; font-size:.88em; color:#f0d9a6; margin-bottom:1.1em; }}
.tactic-alert {{ background:rgba(255,96,96,.10); border:1px solid #7a2b2b; border-radius:12px;
  padding:.8em 1em; margin-bottom:1.1em; color:#ffc9c9; font-weight:600; }}
.tactic-alert b {{ color:#fff; }}

/* --- Stat tiles / meters --- */
.report-hero {{ text-align:center; padding:1.4em 1em 1.2em; }}
.report-hero .headline {{ font-size:1.5em; font-weight:800; letter-spacing:.02em; margin:.15em 0; }}
.report-hero .score {{ font-size:1.05em; color:var(--muted); }}
.rating-card {{ display:flex; gap:1.1em; align-items:center; }}
.rating-card .grade {{ font-size:3.6em; font-weight:900; line-height:1; width:1.3em; text-align:center;
  border:3px solid currentColor; border-radius:14px; padding:.1em 0; }}
.g-S {{ color:#ffd35a; }} .g-A {{ color:var(--good); }} .g-B {{ color:var(--accent); }}
.g-C {{ color:var(--muted); }} .g-D {{ color:var(--bad); }}
.badge-chip {{ display:inline-block; font-size:.78em; font-weight:700; margin:.15em .3em .15em 0; padding:.15em .7em;
  border-radius:999px; border:1px solid var(--accent); color:var(--accent); }}
.again {{ display:flex; gap:.6em; flex-wrap:wrap; align-items:center; margin-top:1em; }}
.again form {{ margin:0; }}
.report-stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(96px,1fr)); gap:.65em; }}
.stat {{ background:var(--panel-2); border:1px solid var(--line-2); border-radius:10px;
  padding:.7em .5em; text-align:center; }}
.stat .n {{ font-size:1.7em; font-weight:800; line-height:1; }}
.stat .label {{ font-size:.68em; color:var(--muted); letter-spacing:.06em; text-transform:uppercase;
  margin-top:.3em; }}
.meter {{ height:8px; border-radius:5px; background:var(--panel-2); border:1px solid var(--line-2);
  overflow:hidden; margin:.35em 0 .1em; }}
.meter > span {{ display:block; height:100%; background:linear-gradient(90deg,#2f97ec,#3fd0a0); }}
.meter.bad > span {{ background:linear-gradient(90deg,#f4b942,#ff6060); }}
.kv {{ display:flex; justify-content:space-between; font-size:.85em; color:var(--muted); }}
.kv b {{ color:var(--ink); }}
.split2 {{ display:grid; grid-template-columns:1fr 1fr; gap:.8em; }}
.split2 .box {{ background:var(--panel-2); border:1px solid var(--line-2); border-radius:10px; padding:.7em .8em; }}
.split2 .box .cap {{ font-size:.7em; letter-spacing:.08em; text-transform:uppercase; font-weight:800; }}
.split2 .box.unlucky .cap {{ color:var(--good); }}
.split2 .box.lucky .cap {{ color:var(--bad); }}
.split2 .box p {{ font-size:.85em; margin:.3em 0; }}
.calllist p {{ font-size:.88em; margin:.28em 0; color:var(--ink); }}
.calllist p .num {{ color:var(--muted); }}

.dim {{ color:var(--faint); }}
.center {{ text-align:center; }}

@media (max-width:720px) {{
  .grid2, .grid2.gcols, .split2 {{ grid-template-columns:1fr; }}
  .callgrid {{ gap:1.1em; }}
  .callgrid-zone, .callgrid-intent {{ flex:1 1 100%; min-width:0; }}
  .zone {{ grid-template-columns:repeat(3,52px); grid-template-rows:repeat(3,52px); }}
  .sb-team .run {{ font-size:1.3em; }}
}}
.startopts {{ display:flex; gap:1em; justify-content:center; flex-wrap:wrap; margin-top:1em;
  font-size:.85em; color:var(--muted); }}
.startopts input, .startopts select {{ background:var(--panel-2); color:var(--ink); border:1px solid var(--line-2);
  border-radius:8px; padding:.35em .6em; font:inherit; }}
.drills {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px,1fr)); gap:.6em; text-align:left; }}
.drill {{ display:flex; flex-direction:column; gap:.25em; align-items:flex-start; padding:.7em .8em; }}
.drill .dim {{ font-size:.78em; font-weight:400; }}
.bgm-btn {{ margin-left:auto; font-size:.72em; letter-spacing:.06em; padding:.3em .8em;
  border-radius:999px; color:var(--muted); }}
</style></head><body>
<div class="wrap">
  <div class="brand">
    <span class="mark">BEHIND THE <span class="dot">·</span> PLATE</span>
    <span class="sub">A catcher's game</span>
    <button type="button" id="bgm-btn" class="bgm-btn" title="Music (original)">♪ Music ON</button>
  </div>
{body}
</div>
<script>{bgm}</script>
</body></html>"""


# Synthesize the BGM with Web Audio. An original trumpet + drums piece (no copyrighted material).
# D natural minor, quarter = 162, a 16-bar loop. Bars 8-11 are the rousing "hook".
# The page reloads on every form submit, so the start time is kept in localStorage, and
# playback restarts from the correct position in the song (a tiny gap on every pitch).
_BGM_JS = """
window.CCBGM = (function () {
  // An original trumpet + drums piece (no copyrighted material). D natural minor, quarter = 162, a 16-bar loop.
  // Bars 8-11 are the "hook": higher notes + harmony + 16th-note drums for a big lift.
  var A = null, running = false, sched = null, step = 0, nextT = 0, master = null;
  var BPM = 162, SPB = 60 / BPM / 4;
  var N = {
    A4:440.00, C5:523.25, D5:587.33, E5:659.25, F5:698.46, G5:783.99,
    A5:880.00, Bb5:932.33, C6:1046.50, D6:1174.66, E6:1318.51
  };
  var H3b = {D6:'Bb5', C6:'A5', E6:'C6', Bb5:'G5', A5:'F5', G5:'E5', F5:'D5'}; // harmony a third below
  // --- Melody (one cell = a 16th note, 0 = rest) ---
  var MEL = [
    'D5',0,'D5',0, 'F5',0,'D5',0, 'A5',0,0,0, 'A5',0,'G5',0,        // m1
    'F5',0,0,0, 'E5',0,'F5',0, 'D5',0,0,0, 0,0,0,0,                 // m2
    'D5',0,'D5',0, 'F5',0,'A5',0, 'C6',0,0,0, 'C6',0,'Bb5',0,       // m3
    'A5',0,0,0, 'G5',0,'A5',0, 'F5',0,0,0, 0,0,'A5','C6',           // m4
    'D6',0,'A5',0, 'F5',0,'A5',0, 'D6',0,'A5',0, 'F5',0,0,0,        // m5
    'C6',0,'A5',0, 'Bb5',0,'C6',0, 'A5',0,0,0, 0,'G5','A5','Bb5',   // m6
    'C6',0,'C6',0, 'A5',0,'C6',0, 'D6',0,0,0, 'D6',0,'C6',0,        // m7
    'Bb5',0,'C6',0, 'D6',0,'E6',0, 'D6',0,'C6',0, 'A5','C6','D6','E6', // m8 (into the hook)
    'E6',0,0,0, 'D6',0,'C6',0, 'D6',0,0,0, 0,0,0,0,                 // m9  hook
    'C6',0,0,0, 'D6',0,'A5',0, 'Bb5',0,0,0, 0,'A5','Bb5','C6',      // m10 hook
    'D6',0,0,0, 'E6',0,'D6',0, 'C6',0,'A5',0, 0,0,0,0,              // m11 hook
    'Bb5',0,'A5',0, 'G5',0,'A5',0, 'D6',0,0,0, 'C6',0,'Bb5',0,      // m12 hook
    'A5',0,0,0, 'F5',0,'A5',0, 'D5',0,'F5',0, 'A5',0,0,0,           // m13
    'G5',0,'F5',0, 'E5',0,'F5',0, 'D5',0,0,0, 0,0,'E5',0,           // m14
    'F5',0,0,0, 'E5',0,'D5',0, 'E5',0,'F5',0, 'G5',0,'A5',0,        // m15
    'Bb5',0,'A5',0, 'G5',0,'F5',0, 'E5',0,0,0, 0,'A4','C5','D5'     // m16
  ];
  var STEPS = MEL.length, LOOP = SPB * STEPS;                      // 16 bars = 256 cells
  function osc(freq, t, dur, type, gain, slideTo) {
    var o = A.createOscillator(), g = A.createGain();
    o.type = type; o.frequency.setValueAtTime(freq, t);
    if (slideTo) o.frequency.exponentialRampToValueAtTime(slideTo, t + dur * 0.6);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(gain, t + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.04);
  }
  function noise(t, dur, type, freq, gain) {
    var len = Math.max(1, Math.floor(A.sampleRate * dur));
    var b = A.createBuffer(1, len, A.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    var n = A.createBufferSource(); n.buffer = b;
    var f = A.createBiquadFilter(); f.type = type; f.frequency.value = freq; f.Q.value = 1;
    var g = A.createGain(); g.gain.setValueAtTime(gain, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + dur);
  }
  function taiko(t, f0, gain) {                          // big drum: boom
    osc(f0, t, 0.34, 'sine', gain, f0 * 0.4);
    osc(f0 * 1.5, t, 0.12, 'triangle', gain * 0.45, f0 * 0.7);
    noise(t, 0.06, 'lowpass', 160, gain * 0.5);
  }
  function snare(t, gain) { noise(t, 0.10, 'bandpass', 1900, gain); noise(t, 0.09, 'highpass', 3600, gain * 0.55); }
  function hat(t, gain) { noise(t, 0.03, 'highpass', 9000, gain); }
  function crash(t) { noise(t, 1.0, 'highpass', 4200, 0.20); noise(t, 0.4, 'bandpass', 7000, 0.12); }
  function trumpet(t, dur, freq, gain) {                 // trumpet (unison)
    osc(freq, t, dur, 'sawtooth', gain, freq * 1.004);
    osc(freq * 1.006, t, dur, 'sawtooth', gain * 0.7);
    osc(freq, t, dur * 0.85, 'square', gain * 0.28);
    osc(freq * 2, t, dur * 0.4, 'triangle', gain * 0.18);
  }
  function scheduleStep(s, t) {
    var bar = Math.floor(s / 16), k = s % 16;
    var hook = (bar >= 8 && bar < 12);                    // the hook (the big lift)
    var fill = (bar === 7 || bar === 15);                 // fills before the hook / before the loop

    // --- Drums ---
    if (k === 0 || k === 8) taiko(t, 100, 0.68);          // big drum on beats 1 and 3
    if (hook && (k === 4 || k === 12)) taiko(t, 116, 0.44); // the hook goes nearly four-on-the-floor
    if (k === 4 || k === 12) snare(t, 0.28);              // snare on beats 2 and 4
    else if (k % 2 === 1) snare(t, hook ? 0.09 : 0.03);   // 16ths (louder in the hook)
    if (k % 2 === 0) hat(t, hook ? 0.045 : 0.03);         // hi-hat on 8ths
    if (hook && k % 2 === 1) hat(t, 0.02);                // 16th hi-hats in the hook
    if (k === 0 && (bar === 0 || bar === 8)) crash(t);    // loop start / hook start
    if (fill && k >= 10) snare(t, 0.06 + (k - 10) * 0.02); // fill
    if (bar === 15 && k === 14) taiko(t, 84, 0.85);       // the closing hit

    // --- Melody (trumpet) ---
    var m = MEL[s];
    if (m) {
      var held = (MEL[(s + 1) % STEPS] === 0 && MEL[(s + 2) % STEPS] === 0);
      var dur = SPB * (held ? 3.4 : 1.9);
      trumpet(t, dur, N[m], hook ? 0.17 : 0.15);
      osc(N[m] / 2, t, dur, 'sawtooth', 0.055);           // an octave below for body
      if (hook && held && H3b[m]) trumpet(t, dur, N[H3b[m]], 0.06); // a third-below harmony on held notes in the hook
    }
  }
  function tick() {
    if (!A) return;
    while (nextT < A.currentTime + 0.12) {
      scheduleStep(step, nextT); nextT += SPB; step = (step + 1) % STEPS;
    }
  }
  function startAt(phaseSec) {
    step = Math.floor(((phaseSec % LOOP) + LOOP) % LOOP / SPB) % STEPS;
    nextT = A.currentTime + 0.06; running = true;
    if (sched) clearInterval(sched);
    sched = setInterval(tick, 25);
  }
  // On by default. It is silent only when the user turned it off themselves ('0').
  function want() { try { return localStorage.getItem('cc_bgm') !== '0'; } catch (e) { return true; } }
  function startEpoch() {
    var now = Date.now();
    try { return parseInt(localStorage.getItem('cc_bgm_start') || now, 10); } catch (e) { return now; }
  }
  function ensureAudio() {
    if (!A) {
      A = new (window.AudioContext || window.webkitAudioContext)();
      master = A.createGain(); master.gain.value = 0.32; master.connect(A.destination);
    }
  }
  return {
    running: function () { return running; },
    toggle: function () {
      if (running) { this.stop(); return; }
      ensureAudio(); A.resume();
      try {
        if (localStorage.getItem('cc_bgm') === '0' || !localStorage.getItem('cc_bgm_start'))
          localStorage.setItem('cc_bgm_start', String(Date.now()));
        localStorage.setItem('cc_bgm', '1');
      } catch (e) {}
      startAt((Date.now() - startEpoch()) / 1000);
    },
    stop: function () {
      running = false; if (sched) { clearInterval(sched); sched = null; }
      try { localStorage.setItem('cc_bgm', '0'); } catch (e) {}
      if (A) A.suspend();
    },
    resumeIfWanted: function () {
      if (!want() || running) return;
      ensureAudio();
      try { if (!localStorage.getItem('cc_bgm_start')) localStorage.setItem('cc_bgm_start', String(Date.now())); } catch (e) {}
      A.resume().then(function () { startAt((Date.now() - startEpoch()) / 1000); }).catch(function () {});
    }
  };
})();
(function () {
  var btn = document.getElementById('bgm-btn'); if (!btn) return;
  function label() {
    var on = true; try { on = localStorage.getItem('cc_bgm') !== '0'; } catch (e) {}
    btn.textContent = on ? '♪ Music ON' : '♪ Music OFF';
    btn.style.color = on ? 'var(--accent)' : 'var(--muted)';
  }
  label();
  btn.addEventListener('click', function () { window.CCBGM.toggle(); label(); });
  // Play from the start. If autoplay is blocked, begin on the first interaction (click / key / tap).
  window.CCBGM.resumeIfWanted();
  var evs = ['pointerdown', 'keydown', 'touchstart'];
  function kick() {
    window.CCBGM.resumeIfWanted();
    evs.forEach(function (e) { document.removeEventListener(e, kick, true); });
  }
  evs.forEach(function (e) { document.addEventListener(e, kick, true); });
})();
"""


def _page(body):
    return _PAGE.format(body=body, bgm=_BGM_JS)


# ---------------------------------------------------------------------------
# START screen
# ---------------------------------------------------------------------------
_DRILLS = (
    ("steal", "Throw Him Out", "A speedster on first is going. Win the throw to second."),
    ("block", "Dirt Ball Drill", "Runners on the corners. Anything in the dirt must be blocked."),
    ("dropped3", "Dropped Third", "Two outs, first base open. Do not let a strikeout turn into a rally."),
    ("bunt", "Bunt Defense", "Close game, runner on first. They are laying one down."),
    ("signs", "Sign Stealer", "A runner on second is peeking. Change the signs in time."),
)
_DIFFICULTY_LABELS = (("beginner", "Beginner (hints on)"), ("normal", "Normal"),
                      ("expert", "Expert (no spray chart)"))


def render_start_screen(session):
    seed_note = (f'<p class="dim">seed = {session.seed} / reproducible mode</p>'
                 if session.seed is not None else "")
    drills = "".join(
        f'<button type="submit" name="scenario" value="{k}" class="drill">'
        f'<b>{_e(name)}</b><span class="dim">{_e(desc)}</span></button>'
        for k, name, desc in _DRILLS)
    diff_opts = "".join(
        f'<option value="{k}"{" selected" if k == session.difficulty else ""}>{_e(label)}</option>'
        for k, label in _DIFFICULTY_LABELS)
    return f"""
<div class="card center" style="padding:2.2em 1.2em;">
  <p class="eyebrow center" style="margin-bottom:.6em;">Late-inning · Defensive half · 3 outs to survive</p>
  <p style="font-size:1.25em; font-weight:800;">Late in the game, you are the catcher. Hold the inning.</p>
  <p class="dim" style="max-width:34em; margin:.6em auto 0;">
    Pick the pitch type, the target location (3×3), and your intent. One game = get three outs.
    No answers are shown during play. Read the batter's reactions, the pitch history, and the batter info yourself.</p>
  {seed_note}
  <form method="post" action="/start" style="margin-top:1.4em;">
    <button class="primary big" type="submit" name="scenario" value="random">START GAME</button>
    <div class="startopts">
      <label>Difficulty <select name="difficulty">{diff_opts}</select></label>
      <label>Challenge code <input type="text" name="seed" inputmode="numeric" maxlength="6" size="7"
        placeholder="random" aria-label="Challenge code (optional)"></label>
    </div>
    <p class="eyebrow center" style="margin:1.6em 0 .6em;">Or run a drill</p>
    <div class="drills">{drills}</div>
  </form>
  <p class="dim" style="font-size:.8em; margin-top:1.2em;">
    Every game has a Challenge Code. Share it and a friend gets the exact same opening situation.</p>
</div>"""


def render_situation_intro(state):
    b = state.batter
    hand = "RHP" if state.pitcher.throws == "R" else "LHP"
    lead = "We lead" if state.our_score > state.opp_score else ("They lead" if state.opp_score > state.our_score else "")
    lead_txt = f"{lead} by {abs(state.our_score - state.opp_score)}" if lead else "Tied game"
    return f"""
<div class="card center" style="padding:2.2em 1.2em;">
  <p class="eyebrow center">{_e(state.inning_label())} · {_e(lead_txt)}</p>
  <div style="font-size:2.2em; font-weight:800; letter-spacing:.02em; margin:.25em 0;">
    <span class="dim" style="font-size:.5em; letter-spacing:.14em;">US</span>
    {state.our_score} <span class="dim" style="font-size:.6em;">–</span> {state.opp_score}
    <span class="dim" style="font-size:.5em; letter-spacing:.14em;">THEM</span></div>
  <div style="margin:.6em 0;">{_base_diamond_svg(state, size=64)}</div>
  <p>{state.outs} OUT &nbsp; {_term("Runners", _TIP["runners"])}: {_e(state.runners_text())}</p>
  <p class="dim">Pitcher: Reliever ({hand})</p>
  <p style="margin-top:1.1em;">BATTER #{state.lineup.spot_number()} <b>{_bname(b)}</b>
     <span class="hand {b.bats}">{_e(b.bats)}</span> {_e(b.public_line())}
     <span class="tag">{_e(b.type_label())}</span></p>
  <p style="font-weight:800; letter-spacing:.14em; margin-top:1.3em; color:var(--accent);">DEFEND THE LEAD.</p>
  <form method="post" action="/playball" style="margin-top:.8em;">
    <button class="primary big" type="submit">PLAY BALL</button>
  </form>
</div>"""


# ---------------------------------------------------------------------------
# Scoreboard strip
# ---------------------------------------------------------------------------
def _pip_row(filled, total, kind):
    return "".join(
        f'<span class="pip {kind}{" on" if i < filled else ""}"></span>' for i in range(total))


# Base order: 2B (top) / 3B (left) / 1B (right) / home (bottom). Runner index: 0=1B 1=2B 2=3B.
_DIAMOND_POINTS = {0: "right", 1: "top", 2: "left"}


def _base_diamond_svg(state, size=22):
    def base(idx, cx, cy):
        cls = "base"
        if state.runners[idx]:
            cls += " fast" if state.runner_speed_at(idx) >= 70 else " on"
        return (f'<rect class="{cls}" x="{cx-5}" y="{cy-5}" width="10" height="10" '
                f'rx="1.5" transform="rotate(45 {cx} {cy})"/>')
    return (f'<svg class="mini-diamond" width="{size}" height="{size*0.8:.0f}" viewBox="0 0 44 36">'
            f'{base(1,22,9)}{base(2,9,20)}{base(0,35,20)}'
            f'<path d="M22 31 l-4 -4 h8 z" fill="#556" />'
            f'</svg>')


def render_scorebar(state):
    hand = "R" if state.pitcher.throws == "R" else "L"
    our_lead = "lead" if state.our_score > state.opp_score else ""
    opp_lead = "lead" if state.opp_score > state.our_score else ""
    tire = ""
    lvl = state.pitcher.fatigue_level()
    if lvl == 1:
        tire = ' <span style="color:var(--warn);">⚠ tired</span>'
    elif lvl == 2:
        tire = ' <span style="color:var(--bad);">⚠⚠ gassed</span>'
    steal = ('<span class="warnpill">⚡STEAL</span>'
             if state.lead_runner_fast() and not state.runners[1] else "")
    return f"""
<div class="scorebar"><div class="sb-row">
  <span class="sb-team {our_lead}"><span class="abbr">US</span><span class="run">{state.our_score}</span></span>
  <span class="sb-inning">{_e(state.inning_label())}</span>
  <span class="sb-team {opp_lead}"><span class="run">{state.opp_score}</span><span class="abbr">THEM</span></span>
  <span class="sb-sep"></span>
  <span class="sb-grp">{_base_diamond_svg(state)} {_e(state.runners_text())} {steal}</span>
  <span class="sb-sep"></span>
  <span class="sb-grp">B {_pip_row(state.balls, 4, 'b')} S {_pip_row(state.strikes, 3, 's')}
      OUT {_pip_row(state.outs, 3, 'o')}{_help(_TIP["count"])}</span>
  <span class="sb-sep"></span>
  <span class="sb-grp">Pitcher <b>{hand}HP</b> &nbsp; Pitches <b>{state.pitcher.pitches_thrown}</b>{tire}</span>
</div></div>"""


# ---------------------------------------------------------------------------
# The field diagram (defensive alignment. Click -> click to swap)
# ---------------------------------------------------------------------------
_FIELD_XY = {
    "CF": (50, 16), "LF": (23, 33), "RF": (77, 33),
    "SS": (38, 57), "2B": (62, 57),
    "3B": (25, 78), "1B": (75, 78),
}

# A broadcast-style bird's-eye field. The grass is a CSS gradient; the infield dirt, bases, foul lines, and mound are SVG.
# The viewBox matches the .field aspect-ratio (1.7) and stretches to fill with preserveAspectRatio=none.
_FIELD_SVG = """
<svg viewBox="0 0 100 59" preserveAspectRatio="none">
  <defs><radialGradient id="dirt" cx="50%" cy="88%" r="72%">
    <stop offset="0%" stop-color="#cd934f"/><stop offset="100%" stop-color="#a2703a"/>
  </radialGradient></defs>
  <path d="M50 55 L90 26 A48 48 0 0 0 10 26 Z" fill="#3f9a58" opacity="0.30"/>
  <polygon points="50,55 24,33 50,11 76,33" fill="url(#dirt)"/>
  <polygon points="50,55 24,33 50,11 76,33" fill="none" stroke="#f5eeda" stroke-width="0.4" opacity="0.55"/>
  <line x1="50" y1="55" x2="4" y2="18" stroke="#f5eeda" stroke-width="0.4" opacity="0.5"/>
  <line x1="50" y1="55" x2="96" y2="18" stroke="#f5eeda" stroke-width="0.4" opacity="0.5"/>
  <circle cx="50" cy="37" r="3.6" fill="#b47a3e" stroke="#f5eeda" stroke-width="0.25"/>
  <g fill="#f8f2e2">
    <rect x="48.6" y="9.6" width="2.8" height="2.8" transform="rotate(45 50 11)"/>
    <rect x="74.6" y="31.6" width="2.8" height="2.8" transform="rotate(45 76 33)"/>
    <rect x="22.6" y="31.6" width="2.8" height="2.8" transform="rotate(45 24 33)"/>
    <path d="M50 53.5 l-2 -2 h4 z"/>
  </g>
</svg>
"""


def _fit_tier(fit):
    return "#3fd0a0" if fit >= 58 else ("#f4b942" if fit >= 46 else "#ff7a7a")


def render_field_diagram(session):
    state = session.state
    defense = state.defense
    weak = weakest_fielder(defense.fielders)
    cells = []
    for pos in POSITIONS:
        x, y = _FIELD_XY[pos]
        f = defense.fielder_at(pos)
        fit = position_fit(f, pos)
        cls = "posbtn"
        if session.pending_swap == pos:
            cls += " selected"
        if f is weak:
            cls += " weak"
        cells.append(f"""
<div class="{cls}" style="left:{x}%; top:{y}%; --tier:{_fit_tier(fit)};">
  <form method="post" action="/swap">
    <input type="hidden" name="position" value="{pos}">
    <button type="submit" title="Play {_e(f.name)} at {pos}">
      <b>{pos}</b>{_e(f.name)}<br><span class="fit">Fit {fit:.0f}{' ▼' if f is weak else ''}</span>
    </button>
  </form>
</div>""")
    cancel = ""
    if session.pending_swap:
        cancel = (f'<p style="margin-top:.5em;"><span class="tag">{session.pending_swap} selected '
                 f'-> click where to swap</span> '
                 '<form method="post" action="/cancel_swap" style="display:inline;">'
                 '<button type="submit">Cancel</button></form></p>')
    align_opts = "".join(
        f'<option value="{k}"{" selected" if k == defense.alignment else ""}>{v}</option>'
        for k, v in ALIGNMENTS.items())
    ede = defense.expected_defensive_efficiency()
    return f"""
<div class="card">
  <h3>Defense</h3>
  <div class="field">
    <div class="align-badge">{_e(defense.describe_alignment())}</div>
    {_FIELD_SVG}{''.join(cells)}
    <div class="homeplate">⌂ HOME · You (C)</div>
  </div>
  <p class="field-legend">The number = {_term("fit", _TIP["fit"])} at that position (0-99). The color on top marks the fit band. ▼ = the weakest fielder.
    Click a position, then the one to swap with. &nbsp; {_term("EDE", _TIP["ede"])} (reference) {ede:.0f}/100</p>
  {cancel}
  <form method="post" action="/alignment" class="form-actions" style="margin-top:.5em;">
    <select name="alignment">{align_opts}</select>
    <button type="submit">Change shift</button>{_help(_TIP["alignment"])}
  </form>
</div>"""


# ---------------------------------------------------------------------------
# Batter card (current / NEXT / ON DECK)
# ---------------------------------------------------------------------------
def render_batter_card(state):
    lu = state.lineup
    cur = lu.current()
    nxt = lu.batters[(lu.index + 1) % 9]
    deck = lu.batters[(lu.index + 2) % 9]
    read = state.current_read()
    read_html = ""
    if read and (read["wait"] or read["weak"]):
        bits = []
        if read["wait"]:
            bits.append("Fastball" if read["wait"] == "fastball" else "Off-speed")
        if read["weak"]:
            bits.append(f"weak={_e(COURSE_SHORT.get(read['weak'], read['weak']))}")
        read_html = f'<div class="myread">Your read: {" / ".join(bits)}</div>'
    return f"""
<div class="card">
  <h3>At Bat</h3>
  <div class="batter-head">
    <span class="batter-num">{lu.spot_number()}</span>
    <span class="name">{_bname(cur)}</span>
    <span class="hand {cur.bats}">{_e(cur.bats)}</span>
    <span class="tag">{_e(cur.type_label())}</span>
  </div>
  <div class="slash">
    <span>AVG <b>{cur.avg:.3f}</b></span><span>OBP <b>{cur.obp:.3f}</b></span>
    <span>SLG <b>{cur.slg:.3f}</b></span>
    <span>{_term("OPS", _TIP["ops"])} <b>{cur.ops:.3f}</b></span>
  </div>
  <div class="scout">Scouting: {_e(cur.scouting_note())}</div>
  {read_html}
  <div class="batter-mini"><span>NEXT · #{lu.batters.index(nxt) + 1} {_bname(nxt)} ({nxt.bats})</span>
    <span>OPS {nxt.ops:.3f}</span></div>
  <div class="batter-mini"><span>ON DECK · #{lu.batters.index(deck) + 1} {_bname(deck)} ({deck.bats})</span>
    <span>OPS {deck.ops:.3f}</span></div>
</div>"""


# ---------------------------------------------------------------------------
# Reaction (the face)
# ---------------------------------------------------------------------------
def render_reaction(reaction):
    if reaction is None:
        return ('<div class="card"><h3>Batter Reaction</h3>'
                '<p class="dim">It shows up here after the first pitch.</p></div>')
    return f"""
<div class="card">
  <h3>Batter Reaction{_help(_TIP["reaction"])}</h3>
  <div class="reaction rc-{_e(reaction.get('category', 'neutral'))}">
    <div class="face">{_e(reaction['face'])}</div>
    <div class="text">“{_e(reaction['text'])}”</div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# Pitch Call -> Actual Pitch (Target / Actual on the strike zone)
# ---------------------------------------------------------------------------
def _zone_markers(bats, target_course, actual_course):
    grid = _course_grid(bats)
    cells = []
    for row in grid:
        for course in row:
            marks = ""
            if course == target_course:
                marks += '<span class="marker target">T</span>'
            if course == actual_course:
                cls = "marker actual" + (" ontarget" if actual_course == target_course else "")
                marks += f'<span class="{cls}">A</span>'
            cells.append(f'<div class="cell">{marks}</div>')
    return "".join(cells)


_RESULT_KIND = {
    "Out": "r-out", "Whiff": "r-out", "Strikeout": "r-out",
    "Sacrifice bunt": "r-out", "Bunt (force out)": "r-out", "Bunt (out at home)": "r-out",
    "Bunt popped up": "r-out",
    "Single": "r-hit", "Double": "r-hit", "Triple": "r-hit", "Home run": "r-hit", "Error": "r-hit",
    "Walk": "r-hit", "Bunt single": "r-hit",
    "Ball": "r-ball",
}


def _result_kind(result):
    return _RESULT_KIND.get(result, "r-strike")


_PRAISE = "Now THAT is a catcher. Impressive."
_TAUNT = "Ugh. Is that all you've got, catcher?"


def _catcher_line(session):
    """A one-liner shown only when the calling really clicked or you got hit hard.
    Because the policy is to never show decision_quality during play, it judges only by the visible result."""
    o = session.last_outcome
    if not o:
        return ""
    res = o.get("result", "")
    end = o.get("at_bat_end")
    play = o.get("play") if isinstance(o.get("play"), dict) else {}
    runs = play.get("runs", 0)
    label = play.get("label", "")
    # Hard hit / a run allowed / a bases-loaded walk -> disappointed
    if res in ("Home run", "Double", "Triple") or runs >= 1 or end == "walk":
        return f'<p class="catcher-line ng">{_TAUNT}</p>'
    # Strikeout / a framed strike / a double play -> impressed
    if end == "strikeout" or o.get("framed") or label == "Double play":
        return f'<p class="catcher-line ok">{_PRAISE}</p>'
    return ""


_GOOD_CALL_LINES = (
    "Nice game-calling. You kept him guessing",
    "Good pitch. The battery has the upper hand",
    "A calm call. The pitcher looks comfortable",
    "A well thought-out pitch. Good read",
)


def _good_call_line(session):
    """A light word of praise for a good call + making the bonus visible (the Web Result card).
    The big moments are handled by _catcher_line (impressed), so this stays clear of overlapping it."""
    o = session.last_outcome
    if not o or not o.get("good_call"):
        return ""
    n = len(getattr(session.state, "pitch_log", []) or [1])
    line = _GOOD_CALL_LINES[n % len(_GOOD_CALL_LINES)]
    streak = o.get("good_call_streak", 0)
    badge = (f'<span class="gc-badge">◯ Good call x{streak} · bonus</span>'
             if streak >= 2 else '<span class="gc-badge">◯ Good call</span>')
    return f'<p class="good-call-line">{badge} <span class="dim">{line}</span></p>'


def render_pitch_result(session):
    if session.last_call is None:
        return ""
    call = session.last_call
    outcome = session.last_outcome
    zone = _zone_markers(session.last_batter_bats, call["target_course"], outcome["actual_course"])
    if outcome["missed"]:
        miss_note = ' <span class="msg bad" style="font-size:.85em;">missed spot</span>'
    else:
        miss_note = ' <span class="dim" style="font-size:.85em;">as called</span>'
    framed = (f' <span class="framed-mark">⌂→ {_term("Framing", _TIP["framing"])}</span>'
              if outcome.get("framed") else "")
    bb = outcome.get("batted_ball")
    bb_line = f'<p class="callline">Batted ball &nbsp;<b>{_e(bb.describe())}</b></p>' if bb else ""
    play = outcome.get("play")
    play_line = ""
    if play and (play["label"] != outcome["result"] or play["detail"] or play["runs"]):
        bits = [play["label"]] if play["label"] != outcome["result"] else []
        if play["detail"]:
            bits.append(play["detail"])
        if play["runs"]:
            bits.append(f'{play["runs"]} run{"s" if play["runs"] != 1 else ""} allowed')
        play_line = f'<p class="callline">Baserunning &nbsp;<b>{_e(" / ".join(bits))}</b></p>'
    msgs_html = "".join(f'<div class="msg{" bad" if _looks_bad(m) else ""}">{_e(m)}</div>'
                        for m in session.messages)
    return f"""
<div class="card">
  <h3>Last Pitch</h3>
  <div class="zonewrap">
    <div class="zone-shadow"><div class="zone">{zone}</div></div>
    <div style="flex:1; min-width:12em;">
      <p class="callline">{_pitch_arc_svg(call['pitch_type'], size=40)}
         <b>{_e(pitch_name(call['pitch_type']))}</b>
         <span class="dim" style="font-size:.85em;">movement (rough)</span></p>
      <p class="callline">CALL <b>{_e(pitch_name(call['pitch_type']))} / {_e(COURSE_SHORT[call['target_course']])}</b>
         / {_e(intent_label(call['intent']))}</p>
      <p class="callline">ACTUAL <b>{_e(pitch_name(call['pitch_type']))} / {_e(COURSE_SHORT[outcome['actual_course']])}</b>{miss_note}
         <span class="dim" style="font-size:.85em;">{_term("quality", _TIP["quality"])} {int(outcome['quality']*100)}</span></p>
      <p class="resultline {_result_kind(outcome['result'])}"><span class="eyebrow" style="margin:0;">RESULT</span> {_e(outcome['result'])}{framed}</p>
      {_catcher_line(session)}
      {_good_call_line(session)}
      {bb_line}{play_line}
    </div>
  </div>
  {render_reaction(session.reaction)}
  {f'<div class="msglist">{msgs_html}</div>' if msgs_html else ''}
</div>"""


_BAD_HINTS = ("They hit", "Passed ball", "error", "Walk", "reaches on the dropped third", "We gave up", "Stolen base", "stole the signs")


def _looks_bad(msg):
    return any(h in msg for h in _BAD_HINTS)


# ---------------------------------------------------------------------------
# The catcher's rhythm games (Web version. The only screens in webapp.py that use JavaScript)
#
# The generator in engine.resolve_pitch_flow() yields ("kind", payload),
# and here a small timing game is shown for each kind. The result (a list of perfect/good/miss
# + the direction) is POSTed to /qte, run through the engine's scoring (qte.bonus_from_verdicts and so on),
# and returned with gen.send(). With JS off, a button submit still moves on neutrally (bonus 0).
# ---------------------------------------------------------------------------
_QTE_SPEC = {
    "frame": {
        "title": "Receive (Framing)", "jp": "RECEIVE!", "tip": "framing", "btn": "Catch it",
        "hint": "Press <span class=\"rg-key\">Space</span> the moment the moving line reaches the middle of the <b style=\"color:var(--good);\">green band</b> "
                "(tapping the band works too). "
                "The more exact the catch, the easier it is to make even a ball look like a strike.",
        "labels": ["Catch it"], "direction": False,
    },
    "change_signs": {
        "title": "Change Signs", "jp": "CHANGE THE SIGNS!",
        "tip": "reaction", "btn": "Change signs",
        "hint": "The runner on second is peeking at your signs. Press "
                "<span class=\"rg-key\">Space</span> on time to switch the signs.",
        "labels": ["Change signs"], "direction": False,
    },
    "steal_throw": {
        "title": "Throw Down", "jp": "STEAL!!", "tip": None, "btn": "Throw",
        "hint": "He's going! ① Transfer → ② Mash fast → ③ Throw to second.",
        "labels": ["① Transfer", "② Mash!", "③ Throw to second"], "direction": False,
        "mash": [1],
    },
    "d3_throw": {
        "title": "Throw to First", "jp": "TO FIRST!", "tip": None, "btn": "Throw",
        "hint": "You picked up the strikeout in the dirt. ① Pick up and grip → ② Throw to first.",
        "labels": ["① Pick up and grip", "② Throw to first"], "direction": False,
    },
    "wild_block": {
        "title": "Block the Ball in the Dirt", "jp": "IN THE DIRT!", "tip": None, "btn": "Block",
        "hint": "First get your body to the side the ball went (in / middle / out). "
                "Then press <span class=\"rg-key\">Space</span> to drop your body.",
        "labels": ["Drop your body"], "direction": True,
    },
    "d3_block": {
        "title": "Block the Dropped Third", "jp": "DROPPED THIRD!", "tip": None, "btn": "Block",
        "hint": "Get your body over (in / middle / out), then <span class=\"rg-key\">Space</span> to block it.",
        "labels": ["Drop your body"], "direction": True,
    },
    "field_bunt": {
        "title": "Field the Bunt", "jp": "BUNT!!", "tip": None, "btn": "Field it",
        "hint": "A bunt! ① Charge and grab it (a little early = the band is on the left) → ② Throw (hold it a beat = the band is at the far right).",
        "labels": ["① Charge and grab it", "② Throw"], "direction": False,
        "targets": [0.40, 0.82],
    },
    # Tutorial only (in the main game the return tempo is embedded in the pitch-call screen)
    "tempo": {
        "title": "Return Tempo", "jp": "RETURN!", "tip": None, "btn": "Return",
        "hint": "Press when the moving line is in the middle of the <b style=\"color:var(--good);\">green band</b>: "
                "<span class=\"rg-key\">Space</span> (tapping the band works). Three times at a steady rhythm.",
        "labels": ["①", "②", "③"], "direction": False,
    },
}

_QTE_JS = """
(function () {
  var root = document.getElementById('qte'); if (!root) return;
  var nbars = parseInt(root.dataset.bars, 10) || 1;
  var labels = (root.dataset.labels || '').split('|');
  var wantDir = root.dataset.direction === '1';
  var jp = root.dataset.jp || '';
  var targets = (root.dataset.targets || '').split('|').map(function (x) { return parseFloat(x); });
  var mash = {};
  (root.dataset.mash || '').split(',').forEach(function (x) { if (x !== '') mash[parseInt(x, 10)] = 1; });
  var track = document.getElementById('rg-track');
  var marker = document.getElementById('rg-marker');
  var band = document.getElementById('rg-target');
  var vEl = document.getElementById('rg-verdict');
  var labEl = document.getElementById('qte-label');
  var annEl = document.getElementById('qte-announce');
  var dirsEl = document.getElementById('qte-dirs');
  var fV = document.getElementById('f-verdicts');
  var fD = document.getElementById('f-dir');
  var form = document.getElementById('rg-form');
  var IDLE = 0, SWEEP = 1, MASH = 2;
  var verdicts = [], bar = 0, done = false, phase = IDLE, period = 1150, start = 0;
  var mashN = 0, mashEnd = 0;

  function announce(text, ms) {
    if (!annEl) return;
    annEl.textContent = text; annEl.className = 'qte-announce show';
    setTimeout(function () { annEl.className = 'qte-announce'; }, ms || 750);
  }
  function center() { var c = targets[bar]; return (isNaN(c) ? 0.5 : c); }
  function frac(now) { var t = ((now - start) % (period * 2)) / period; return t <= 1 ? t : 2 - t; }
  function place(c, w) { if (band) { band.style.left = (Math.max(0, Math.min(1 - w, c - w / 2)) * 100) + '%'; band.style.width = (w * 100) + '%'; } }
  function tick(now) { if (phase !== SWEEP) return; marker.style.left = (frac(now) * 100) + '%'; requestAnimationFrame(tick); }
  function mashTick(now) {
    if (phase !== MASH) return;
    place(0.5, Math.max(0.03, (mashEnd - now) / 1800));   // the band shrinks = time left
    requestAnimationFrame(mashTick);
  }
  function recordVerdict(v, txt) {
    verdicts.push(v); phase = IDLE;
    vEl.textContent = txt || (v.toUpperCase() + (v === 'perfect' ? '!' : '')); vEl.className = 'rg-verdict ' + v;
    bar++;
  }
  function beginStep() {
    if (bar >= nbars) return finish();
    vEl.textContent = ''; vEl.className = 'rg-verdict'; phase = IDLE;
    var lbl = labels[bar] || ('Timing ' + (bar + 1));
    labEl.textContent = lbl;                 // show "what to do next" in a bold label
    if (mash[bar]) { announce('MASH!! GO', 900); setTimeout(startMash, 950); }
    else { setTimeout(startSweep, bar === 0 ? 120 : 620); }   // pause briefly, then start the sweep
  }
  function startSweep() {
    place(center(), 0.18); marker.style.left = '0%';
    start = performance.now(); phase = SWEEP; requestAnimationFrame(tick);
  }
  function startMash() {
    place(0.5, 1.0); mashN = 0; marker.style.left = '0%';
    labEl.textContent = labels[bar] + '  0';
    phase = MASH; mashEnd = performance.now() + 1800;
    requestAnimationFrame(mashTick);
    setTimeout(endMash, 1800);
  }
  function endMash() {
    if (phase !== MASH) return;
    place(0.5, 0.18);
    var v = mashN >= 12 ? 'perfect' : (mashN >= 6 ? 'good' : 'miss');
    recordVerdict(v, mashN + ' hits -> ' + v.toUpperCase());
    announce('MASH OVER', 700);
    setTimeout(beginStep, 1150);                          // pause clearly before the next step
  }
  function hit() {
    if (phase === MASH) {
      mashN++; marker.style.left = Math.min(100, mashN / 12 * 100) + '%';
      labEl.textContent = labels[bar] + '  ' + mashN; return;
    }
    if (phase !== SWEEP) return;
    var d = Math.abs(frac(performance.now()) - center());
    recordVerdict(d <= 0.05 ? 'perfect' : (d <= 0.14 ? 'good' : 'miss'));
    setTimeout(beginStep, 600);
  }
  function finish() {
    if (done) return; done = true;
    while (verdicts.length < nbars) verdicts.push('miss');
    fV.value = verdicts.join(',');
    setTimeout(function () { form.submit(); }, 450);
  }
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space' || e.code === 'Enter') { e.preventDefault(); hit(); }
  });
  track.addEventListener('click', hit);

  // First show a big "STEAL!!" or similar, then begin
  if (jp) announce(jp, 1000);
  setTimeout(function () {
    if (wantDir && dirsEl) {
      dirsEl.hidden = false;
      announce('PICK A SIDE', 700);
      Array.prototype.forEach.call(dirsEl.querySelectorAll('button'), function (b) {
        b.addEventListener('click', function () { fD.value = b.dataset.dir; dirsEl.hidden = true; beginStep(); });
      });
    } else { beginStep(); }
  }, jp ? 1050 : 0);
  setTimeout(finish, 15000);
})();
"""


def _rhythm_widget(spec, action, call_html="", extra_hidden="", header_extra="", targets=None):
    """The timing-bar widget shared by QTEs and the tutorial.
    targets: the center (0..1) of each beat's band. Omitted = spec["targets"] or all centered (0.5)."""
    tip = _help(_TIP[spec["tip"]]) if spec.get("tip") else ""
    tgt = targets if targets is not None else spec.get("targets", [])
    mash = spec.get("mash", [])
    dir_html = ""
    if spec["direction"]:
        dir_html = ('<div id="qte-dirs" class="qte-dirs" hidden>'
                    '<button type="button" data-dir="in">◀ In</button>'
                    '<button type="button" data-dir="mid">● Middle</button>'
                    '<button type="button" data-dir="out">Out ▶</button></div>')
    return f"""
<div class="card rg-wrap">
  <h3>{_e(spec["title"])}{tip}</h3>
  {header_extra}
  {call_html}
  <p class="rg-hint">{spec["hint"]}</p>
  <div id="qte" data-bars="{len(spec['labels'])}" data-direction="{1 if spec['direction'] else 0}"
       data-jp="{_e(spec.get('jp', ''))}"
       data-labels="{_e('|'.join(spec['labels']))}"
       data-targets="{_e('|'.join(str(t) for t in tgt))}"
       data-mash="{_e(','.join(str(m) for m in mash))}">
    <div id="qte-announce" class="qte-announce"></div>
    {dir_html}
    <div id="qte-label" class="qte-label"></div>
    <div id="rg-track" class="rg-track" tabindex="0" aria-label="Press Space in time with the band">
      <div class="rg-target" id="rg-target"><div class="core"></div></div>
      <div id="rg-marker" class="rg-marker"></div>
    </div>
    <div id="rg-verdict" class="rg-verdict"></div>
  </div>
  <form id="rg-form" method="post" action="{action}">
    <input type="hidden" name="verdicts" id="f-verdicts" value="">
    <input type="hidden" name="dir" id="f-dir" value="">
    {extra_hidden}
    <noscript><p class="rg-hint">JavaScript is off. Press "{_e(spec["btn"])}" to move on.</p></noscript>
    <button type="submit" class="primary big">{_e(spec["btn"])}</button>
  </form>
</div>
<script>{_QTE_JS}</script>"""


def _prev_result_strip(outcome):
    """A quick green (good) / red (bad) for the previous pitch's result."""
    if not outcome:
        return ""
    k = _result_kind(outcome.get("result", ""))
    if k in ("r-out", "r-strike"):
        col, mk = "var(--good)", "◯ Good"
    elif k in ("r-hit", "r-ball"):
        col, mk = "var(--bad)", "✕ Bad"
    else:
        col, mk = "var(--muted)", "—"
    return (f'<p class="prev-strip" style="color:{col};">Last pitch &nbsp;'
            f'<b>{_e(outcome.get("result", ""))}</b> {mk}</p>')


def render_qte(session):
    kind, payload = session.pending_qte
    spec = _QTE_SPEC[kind]
    call = payload.get("call")
    call_html = f'<p class="rg-call"><b>{_e(call)}</b></p>' if call else ""
    targets = None
    if kind == "frame":
        v = payload.get("velocity", 90)
        # a fast pitch is received early (band on the left), a slow one late (band on the right)
        targets = [0.24 if v >= 92 else (0.74 if v <= 84 else 0.5)]
    return _rhythm_widget(spec, "/qte", call_html=call_html, targets=targets,
                          header_extra=_prev_result_strip(session.last_outcome))


def _tutorial_complete(session):
    return session.tutorial_skipped or all(session.tutorial.values())


def _tutorial_ok(item, form):
    """Whether one practice attempt is a "clear". It is a clear if every beat is GOOD or better."""
    vs = [v for v in (form.get("verdicts", "") or "").split(",") if v in ("perfect", "good", "miss")]
    if not vs:
        return False
    if item in _TUT_DIR and form.get("dir"):
        vs = [qte.direction_verdict(form.get("dir"), _TUT_DIR[item])] + vs
    return "miss" not in vs


def render_tutorial(session):
    todo = [k for k, _, _ in _TUTORIAL_ITEMS if not session.tutorial[k]]
    done_n = len(_TUTORIAL_ITEMS) - len(todo)
    names = {k: n for k, n, _ in _TUTORIAL_ITEMS}
    rows = "".join(
        f'<li class="{"done" if session.tutorial[k] else ("now" if todo and k == todo[0] else "")}">'
        f'{"✓" if session.tutorial[k] else ("▶" if todo and k == todo[0] else "·")} {_e(name)}'
        f'<span class="dim"> — {_e(desc)}</span></li>'
        for k, name, desc in _TUTORIAL_ITEMS)
    skip = ('<form method="post" action="/skiptutorial" style="margin-top:.8em;">'
            '<button type="submit">Skip and start the game</button></form>')
    checklist = (
        f'<div class="card"><h3>Practice <span class="dim">({done_n} / '
        f'{len(_TUTORIAL_ITEMS)} cleared)</span></h3>'
        '<p class="dim" style="font-size:.88em; margin-bottom:.4em;">'
        'Clear each of the six rhythms once with <b>GOOD or better</b>. If a MISS slips in, try the same item again.</p>'
        f'<ul class="tut-list">{rows}</ul>{skip}</div>')
    if not todo:
        return checklist
    item = todo[0]
    spec = _QTE_SPEC[item]
    hdr = f'<p class="rg-call">Practice &nbsp;<b>{_e(names[item])}</b></p>'
    if item in _TUT_DIR:
        hdr += '<p class="rg-hint">In practice the ball comes <b>middle</b>.</p>'
    widget = _rhythm_widget(spec, "/tutorial",
                            extra_hidden=f'<input type="hidden" name="item" value="{item}">',
                            header_extra=hdr)
    # Put the rhythm widget first (the page returns to the top on every submit, so keep it where it is always visible)
    return widget + checklist


def _qte_answer(kind, payload, form):
    """Convert the submitted verdict list (+ direction) into the answer the engine expects."""
    verdicts = [v for v in (form.get("verdicts", "") or "").split(",")
                if v in ("perfect", "good", "miss")]
    if kind == "frame":
        return verdicts[0] if verdicts else "good"
    parts = list(verdicts)
    if kind in ("wild_block", "d3_block") and form.get("dir"):
        parts = [qte.direction_verdict(form.get("dir"), payload.get("dir"))] + parts
    return qte.bonus_from_verdicts(*parts) if parts else 0.0


def _tempo_bonus(form):
    """Turn the return rhythm (3 beats) sent with the pitch call into a tempo (-0.25 to 0.25)."""
    vs = [v for v in (form.get("tempo", "") or "").split(",") if v in ("perfect", "good", "miss")]
    return qte.bonus_from_verdicts(*vs) if vs else 0.0


# The "return tempo" widget embedded in the pitch-call screen. Shown only when you caught the previous pitch.
_TEMPO_JS = """
(function () {
  var box = document.getElementById('tempo'); if (!box) return;
  var track = document.getElementById('tempo-track');
  var marker = document.getElementById('tempo-marker');
  var vEl = document.getElementById('tempo-verdict');
  var labEl = document.getElementById('tempo-label');
  var fld = document.getElementById('f-tempo');
  var msg = document.getElementById('tempo-msg');
  var nbars = 3, bar = 0, vs = [], done = false, running = false, started = false;
  var period = 1050, start = 0;
  function frac(now) { var t = ((now - start) % (period * 2)) / period; return t <= 1 ? t : 2 - t; }
  function tick(now) { if (!running) return; marker.style.left = (frac(now) * 100) + '%'; requestAnimationFrame(tick); }
  function startBar() {
    if (bar >= nbars) return finish();
    labEl.textContent = '(' + (bar + 1) + ' / ' + nbars + ')';
    vEl.textContent = ''; vEl.className = 'rg-verdict';
    start = performance.now(); running = true; requestAnimationFrame(tick);
  }
  function hit() {
    if (!running || done) return; running = false;
    var d = Math.abs(frac(performance.now()) - 0.5) / 0.5;
    var v = d <= 0.06 ? 'perfect' : (d <= 0.18 ? 'good' : 'miss');
    vs.push(v); vEl.textContent = v.toUpperCase() + (v === 'perfect' ? '!' : ''); vEl.className = 'rg-verdict ' + v;
    bar++; setTimeout(startBar, 430);
  }
  function finish() {
    if (done) return; done = true; fld.value = vs.join(','); labEl.textContent = '';
    var p = vs.filter(function (x) { return x === 'perfect'; }).length, m = vs.indexOf('miss') >= 0;
    msg.textContent = p >= 2 ? 'Great tempo - the pitcher is in a groove' : (m ? 'Tempo broke down' : 'Decent tempo');
    msg.style.color = p >= 2 ? 'var(--good)' : (m ? 'var(--bad)' : 'var(--muted)');
  }
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space' && !done) { e.preventDefault(); hit(); }
  });
  track.addEventListener('click', hit);
  function begin() { if (started) return; started = true; setTimeout(startBar, 700); }
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) { if (en.isIntersecting) { begin(); io.disconnect(); } });
    }, { threshold: 0.6 });
    io.observe(box);
  } else { begin(); }
})();
"""


def _tempo_widget(state, reaction=None, prev_outcome=None):
    """(widget HTML, script HTML). Both are empty strings if there is nothing to return.
    The same "stop the moving line in the middle of the green band" format as the other rhythm games, three times."""
    last = state.history.last()
    if last is None or last.get("result") not in ("Ball", "Strike", "Whiff"):
        return "", ""
    rx = ""
    if reaction:
        rx = (f'<span class="tempo-react">{_e(reaction.get("face", ""))}'
              f' <span class="dim">“{_e(reaction.get("text", ""))}”</span></span>')
    html = (
        '<div id="tempo" class="tempo-box">'
        '<div class="tempo-head">'
        '<span class="eyebrow" style="margin:0;">Return Tempo</span>'
        f'{rx}</div>'
        f'{_prev_result_strip(prev_outcome)}'
        '<p class="dim" style="font-size:.82em; margin:.15em 0 .5em;">'
        'Press when the moving line is in the middle of the <b style="color:var(--good);">green band</b>: '
        '<span class="rg-key">Space</span> (tapping the band works). Three times, at a steady tempo.'
        ' <span id="tempo-label"></span></p>'
        '<div id="tempo-track" class="rg-track" tabindex="0" aria-label="Press Space in time with the band">'
        '<div class="rg-target"><div class="core"></div></div>'
        '<div id="tempo-marker" class="rg-marker"></div></div>'
        '<div id="tempo-verdict" class="rg-verdict" style="min-height:1.2em; font-size:1.1em;"></div>'
        '<p id="tempo-msg" class="rg-hint" style="margin:.15em 0 0; min-height:1.1em;"></p>'
        '<input type="hidden" name="tempo" id="f-tempo" value="">'
        '</div>')
    return html, f"<script>{_TEMPO_JS}</script>"


# ---------------------------------------------------------------------------
# The pitch call (pitch type / location / intent)
# ---------------------------------------------------------------------------
def render_pitch_call_form(state, reaction=None, prev_outcome=None):
    pitch_opts = "".join(
        f'<input type="radio" name="pitch_type" value="{_e(k)}" id="pt_{k}"'
        f'{" checked" if i == 0 else ""}>'
        f'<label for="pt_{k}">{_pitch_arc_svg(k)}{_e(v)}<span class="v">{velocity_of(k)}</span></label>'
        for i, (k, v) in enumerate(repertoire_options(state.pitcher).items()))
    intent_opts = "".join(
        f'<input type="radio" name="intent" value="{k}" id="it_{k}"'
        f'{" checked" if k == "strike" else ""}>'
        f'<label for="it_{k}">{_e(intent_label(k))}</label>'
        for k, v in PITCH_INTENTS.items())
    grid = _course_grid(state.batter.bats)
    course_cells = ""
    for row in grid:
        for c in row:
            checked = " checked" if c == "mid_mid" else ""
            course_cells += (f'<div class="cell"><input type="radio" name="course" value="{c}" '
                            f'id="c_{c}"{checked}><label for="c_{c}">{_e(COURSE_SHORT[c])}</label></div>')
    side_hint = "Inside ◀ ▶ Away" if state.batter.bats == "R" else "Away ◀ ▶ Inside"
    tempo_html, tempo_js = _tempo_widget(state, reaction, prev_outcome)
    return f"""
<div class="card">
  <h3>Pitch Call</h3>
  <form method="post" action="/pitch">
    {tempo_html}
    <p class="eyebrow">Pitch Type{_help(_TIP["pitch_type"])}
       <span class="dim" style="font-weight:400;">(numbers are speed)</span></p>
    <div class="chiprow">{pitch_opts}</div>
    <div class="callgrid">
      <div class="callgrid-zone">
        <p class="eyebrow">Target Location{_help(_TIP["course"])}
           <span class="dim" style="font-weight:400;">({_e(side_hint)} · Batter {state.batter.bats} · catcher's view)</span></p>
        <div class="zone-shadow"><div class="zone">{course_cells}</div></div>
      </div>
      <div class="callgrid-intent">
        <p class="eyebrow">Pitch Intent{_help(_TIP["intent"])}</p>
        <div class="chiprow">{intent_opts}</div>
        <p style="margin:1.1em 0 0;"><button class="primary big" type="submit">CALL PITCH</button></p>
        <p class="hint" style="margin:.8em 0 0;">
          Chase and waste pitches are likely to be balls, and a pitchout is almost surely a ball.
          Choose by looking at the count and the runners.</p>
      </div>
    </div>
  </form>
</div>{tempo_js}"""


# ---------------------------------------------------------------------------
# Spray chart (#B. Hidden at expert difficulty)
# ---------------------------------------------------------------------------
_SPRAY_GLYPH = {"Out": "·", "Single": "o", "Error": "e",
               "Double": "O", "Triple": "O", "Home run": "H"}


def render_spray_chart(state):
    w, h = 21, 5
    grid = [[" "] * w for _ in range(h)]
    for entry in state.spray:
        d, bats = entry["direction"], entry["bats"]
        if d == "center":
            side = "center"
        elif (d == "pull" and bats == "R") or (d == "oppo" and bats == "L"):
            side = "left"
        else:
            side = "right"
        col = {"left": 4, "center": 10, "right": 16}[side]
        row = {"deep": 0, "shallow": 2, "infield": 4}[entry["distance"]]
        g = _SPRAY_GLYPH.get(entry["result"], "?")
        for dc in range(2):
            c = col + dc
            if 0 <= row < h and 0 <= c < w and grid[row][c] == " ":
                grid[row][c] = g
                break
    grid[h - 1][w // 2] = "⌂"
    text = "\n".join("".join(r) for r in grid)
    if not state.spray:
        chart = ('<p class="hint" style="margin:.2em 0 0;">'
                 'When a ball is hit fair it is recorded here, so you can see at a glance where it went on the field.</p>')
    else:
        chart = (f'<p class="dim" style="margin:.2em 0 .4em;">'
                 f'· out &nbsp; o single &nbsp; e error &nbsp; O extra-base &nbsp; H home run</p>'
                 f'<pre class="spray">{_e(text)}</pre>')
    return (f'<div class="card"><h3>Spray Chart (this game)</h3>{chart}</div>')


# ---------------------------------------------------------------------------
# Pitch history
# ---------------------------------------------------------------------------
_RESULT_CSS = {
    "Strike": "res-strike", "Whiff": "res-strike", "Foul": "res-strike",
    "Ball": "res-ball",
    "Out": "res-out", "Strikeout": "res-out", "Sacrifice bunt": "res-out",
    "Bunt (force out)": "res-out", "Bunt (out at home)": "res-out", "Bunt popped up": "res-out",
    "Single": "res-hit", "Double": "res-hit", "Triple": "res-hit", "Home run": "res-hit",
    "Error": "res-hit", "Walk": "res-hit", "Bunt single": "res-hit",
}


def render_pitch_history(state, limit=8):
    entries = state.pitch_log[-limit:][::-1]
    if not entries:
        rows = '<tr><td colspan="5" class="dim">No pitches yet</td></tr>'
    else:
        rows = "".join(
            f"<tr><td class=\"dim\">{e['pitch_number']}</td>"
            f"<td>{_e(pitch_name(e['pitch_type']))}</td>"
            f"<td>{_e(COURSE_SHORT[e['actual_course']])}</td>"
            f"<td>{_e(intent_label(e['intent']))}</td>"
            f"<td class=\"{_RESULT_CSS.get(e['result'], '')}\"><span class=\"rdot\"></span>{_e(e['result'])}"
            f"{'<span class=\"framed-mark\"> ⌂→</span>' if e.get('framed') else ''}</td></tr>"
            for e in entries)
    return f"""
<div class="card">
  <h3>Pitch History — last {limit} pitches, newest first</h3>
  <div class="tablescroll"><table class="history">
    <tr><th>#</th><th>Pitch</th><th>Location</th><th>Intent</th><th>Result</th></tr>
    {rows}
  </table></div>
</div>"""


# ---------------------------------------------------------------------------
# Side actions (intentional walk, pitching change, catcher's notes, quit)
# ---------------------------------------------------------------------------
def render_side_actions(state, difficulty):
    extras = ""
    if not all(state.runners):          # you can walk him unless bases are loaded (only a bases-loaded walk forces in a run)
        extras += '<form method="post" action="/walk" style="display:inline">'\
                 '<button>Intentional walk</button></form> '
    if state.can_change_pitcher():
        if len(state.bullpen) == 1:
            extras += ('<form method="post" action="/change" style="display:inline">'
                      '<input type="hidden" name="index" value="0">'
                      f'<button>Pitching change ({_e(state.bullpen[0].name)})</button></form> ')
        else:
            pitcher_opts = "".join(
                f'<option value="{i}">{_e(p.name)} ({"R" if p.throws == "R" else "L"}HP)</option>'
                for i, p in enumerate(state.bullpen))
            extras += ('<form method="post" action="/change" style="display:inline">'
                      f'<select name="index">{pitcher_opts}</select>'
                      '<button>Pitching change</button></form> ')
    course_opts = "".join(f'<option value="{c}">{_e(v)}</option>' for c, v in COURSE_SHORT.items())
    hint = ""
    if difficulty == "beginner":
        hint = ('<div class="hint"><b>Hint</b> A face is not always the truth (60% honest / 15% opposite / 25% blurred). '
               'Do not trust a single pitch too much.</div>')
    return f"""
{hint}
<div class="card">
  <h3>Catcher's Notes (graded after the game)</h3>
  <form method="post" action="/memo" class="form-actions">
    <select name="wait"><option value="unknown">Looking for: not sure</option>
      <option value="fastball">Fastball</option><option value="offspeed">Off-speed</option></select>
    <select name="weak"><option value="">Weak zone: not sure</option>{course_opts}</select>
    <button type="submit">Save note</button>
  </form>
</div>
<div class="card">
  <h3>Bench</h3>
  <div class="form-actions">
    {extras}
    <form method="post" action="/newgame" style="display:inline;">
      <button>Quit and start over</button></form>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# The main game screen
# ---------------------------------------------------------------------------
def render_last_play(session):
    """The Play Viewer card for the last fair ball (empty if the last pitch was not put in play).
    It auto-plays once; later page loads (changing the shift, saving a note) show the end of the play."""
    o = session.last_outcome
    if not o or not o.get("batted_ball") or not o.get("fielding") or not o.get("play"):
        return ""
    key = len(session.state.pitch_log)
    autoplay = session.play_shown != key
    session.play_shown = key
    try:
        script = playview.build_play_script(o["batted_ball"], o["fielding"], o["play"], seed_key=key)
    except Exception:   # noqa: BLE001 -- a drawing problem must never stop the game
        traceback.print_exc()
        return ""
    f = o["fielding"]
    caption = f"{o['batted_ball'].describe()} · {f['position']} {f['fielder']} · {o['result']}"
    return playview.render_play_viewer(script, caption, autoplay=autoplay)


def render_game_page(session):
    state = session.state
    tactic_msg = begin_at_bat(state, session.rng)
    body = [render_scorebar(state)]
    if tactic_msg:
        body.append(f'<div class="tactic-alert">⚠ <b>{_e(tactic_msg)}</b>'
                    f' — counter with a pitchout or a high fastball</div>')
    # Put the pitch call at the very top (so "return tempo + next pitch" is within reach without scrolling).
    # Below it, the material to read: the defense | the batter + the last pitch -> pitch history -> spray chart -> bench.
    body.append(render_last_play(session))
    body.append(render_pitch_call_form(state, session.reaction, session.last_outcome))
    body.append('<div class="grid2 gcols">')
    body.append('<div>' + render_field_diagram(session) + '</div>')
    body.append('<div>' + render_batter_card(state)
               + (render_pitch_result(session) if session.last_call else render_reaction(None))
               + '</div>')
    body.append('</div>')
    body.append(render_pitch_history(state))
    if session.difficulty != "expert":
        body.append(render_spray_chart(state))
    body.append(render_side_actions(state, session.difficulty))
    return "".join(body)


# ---------------------------------------------------------------------------
# Postgame Report(Catcher Report)
# ---------------------------------------------------------------------------
def render_postgame_report(state, log_path, game_seed=None, difficulty="normal"):
    report = build_postgame_report(state)
    a = build_analysis(state.pitch_log)
    rating = catcher_rating(state, report, a)
    badges_html = "".join(
        f'<span class="badge-chip" title="{_e(desc)}">{_e(name)}</span>' for name, desc in rating["badges"])
    code_note = (f'<p class="dim" style="font-size:.85em;">Challenge Code <b>#{game_seed}</b></p>'
                 if game_seed is not None else "")
    again_seed = (f'<input type="hidden" name="seed" value="{game_seed}">' if game_seed is not None else "")
    reads = grade_reads(state.reads, state.lineup.batters)
    run_txt, status = state.result_summary()

    def stat(n, label):
        return f'<div class="stat"><div class="n">{n}</div><div class="label">{label}</div></div>'

    stats_html = "".join([
        stat(report["runs_allowed"], "Runs Allowed"),
        stat(report["batters_faced"], "Batters Faced"),
        stat(report["pitches"], "Pitches"),
        stat(report["strikeouts"], "Strikeouts"),
        stat(report["walks"], "Walks"),
        stat(report["hits"], "Hits"),
    ])

    def fmt(items, limit=5, cls="calllist"):
        if not items:
            return '<p class="dim">None</p>'
        rows = "".join(
            f'<p><span class="num">Pitch {n}</span> &nbsp;{_e(label)}'
            + (f' → {_e(item[2])}' if len(item) > 2 else '') + '</p>'
            for item in items[:limit] for n, label in [(item[0], item[1])])
        more = f'<p class="dim">...and {len(items) - limit} more</p>' if len(items) > limit else ""
        return f'<div class="{cls}">{rows}{more}</div>'

    reads_html = ""
    if reads["rows"]:
        reads_html = (f'<div class="card"><h3>Your reads graded</h3>'
                     f'<p><b>{reads["correct"]} / {reads["graded"]}</b> right</p>'
                     + "".join(f'<p class="dim" style="font-size:.85em;">{_e(r.strip())}</p>'
                               for r in reads["rows"])
                     + '</div>')

    pe = report["pitch_execution"]
    de = report["defense"]
    runc = "var(--good)" if report["runs_allowed"] == 0 else "var(--bad)"
    dq_bad = "bad" if "loose" in a["decision_quality_label"].lower() else ""
    dq_pct = {"Good (kept batters off balance)": 92, "Slightly good": 68,
              "Slightly loose": 40, "Loose (too many predictable, get-it-over pitches)": 18}.get(
                  a["decision_quality_label"], 50)
    q_pct = int(pe["avg_quality"] * 100)
    m_pct = int(pe["missed_rate"] * 100)

    return f"""
<div class="card report-hero">
  <h2 style="letter-spacing:.14em; color:var(--muted); font-size:.8em;">CATCHER REPORT</h2>
  <p class="headline" style="color:{runc};">{_e(run_txt)} / {_e(status)}</p>
  <p class="score">Us <b>{state.our_score}</b> – <b>{state.opp_score}</b> Them
     <span class="dim">(started {state.start_our}–{state.start_opp})</span></p>
</div>
<div class="card rating-card">
  <div class="grade g-{rating['grade']}">{rating['grade']}</div>
  <div>
    <p class="eyebrow" style="margin:0;">CATCHER RATING · {rating['score']} / 100</p>
    <p style="font-size:1.25em; font-weight:800; margin:.1em 0 .4em;">{_e(rating['title'])}</p>
    <div>{badges_html or '<span class="dim">No badges this time. Win the next one.</span>'}</div>
    {code_note}
  </div>
</div>

<div class="card"><h3>The Numbers</h3><div class="report-stats">{stats_html}</div></div>

<div class="grid2">
  <div class="card"><h3>Decision Quality</h3>
    <div class="kv"><span>Average</span><b>{_e(a['decision_quality_label'])}</b></div>
    <div class="meter {dq_bad}"><span style="width:{dq_pct}%;"></span></div>
    <p class="dim" style="font-size:.82em; margin:.5em 0 .7em;">Result and decision are different things. Below are the pitches where "result ≠ decision".</p>
    <div class="split2">
      <div class="box unlucky"><div class="cap">GOOD CALL / bad result (unlucky)</div>{fmt(a['unlucky'], 3)}</div>
      <div class="box lucky"><div class="cap">BAD CALL / good result (lucky)</div>{fmt(a['lucky'], 3)}</div>
    </div>
  </div>
  <div>
    <div class="card"><h3>Pitch Execution</h3>
      <div class="kv"><span>Miss rate</span><b>{pe['missed_pitches']} / {report['pitches']} ({m_pct}%)</b></div>
      <div class="meter bad"><span style="width:{m_pct}%;"></span></div>
      <div class="kv" style="margin-top:.5em;"><span>Average quality</span><b>{pe['avg_quality']:.2f}</b></div>
      <div class="meter"><span style="width:{q_pct}%;"></span></div>
      <p class="kv" style="margin-top:.5em;"><span>Strikes stolen by framing</span>
         <b style="color:var(--accent);">{report['framed_strikes']}</b></p>
    </div>
    <div class="card"><h3>Defense</h3>
      <p class="kv"><span>Shift fit moments</span><b>{de['alignment_helped']}</b></p>
      <p class="kv"><span>Errors</span><b>{de['errors']}</b></p>
      <p class="kv"><span>Final EDE (reference)</span><b>{de['final_ede']} / 100</b></p>
      <div class="meter"><span style="width:{de['final_ede']:.0f}%;"></span></div>
    </div>
  </div>
</div>

<div class="grid2">
  <div class="card"><h3>◎ Good calls</h3>{fmt(a['good_calls'])}</div>
  <div class="card"><h3>△ Risky calls</h3>{fmt(a['risky_calls'])}</div>
</div>
<div class="card"><h3>◇ Extra-base hits allowed</h3>{fmt(a['extra_base'])}</div>
{reads_html}
<div class="card dim" style="font-size:.85em;">{'Log: ' + _e(log_path) if log_path else ''}
  <p style="margin-top:.4em;">* No answers were shown during play. This review is shown only after three outs.</p></div>
<div class="again">
  <form method="post" action="/newgame"><input type="hidden" name="difficulty" value="{difficulty}">
    <button class="primary big" type="submit">Play again</button></form>
  <form method="post" action="/newgame"><input type="hidden" name="difficulty" value="{difficulty}">{again_seed}
    <button type="submit">Replay this situation</button></form>
  <form method="post" action="/menu"><button type="submit">Menu</button></form>
</div>"""


def render_page(session):
    if not session.started:
        return render_start_screen(session)
    if not _tutorial_complete(session):
        return render_tutorial(session)
    if session.showing_intro:
        return render_situation_intro(session.state)
    state = session.state
    session.finish_if_over()
    if state.is_over():
        return render_last_play(session) + render_postgame_report(
            state, session.log_path, session.game_seed, session.difficulty)
    if session.pending_qte is not None:
        return render_scorebar(state) + render_qte(session)
    return render_game_page(session)


def _advance_pitch_gen(answer):
    """Advance the in-progress resolve_pitch_flow generator by one step.
    If the next QTE arrives, push it to pending_qte. When it finishes, store the result in SESSION."""
    gen = SESSION.pitch_gen
    if gen is None:
        return
    try:
        SESSION.pending_qte = gen.send(answer)          # (kind, payload)
    except StopIteration as stop:
        SESSION.pitch_gen = None
        SESSION.pending_qte = None
        outcome, messages = stop.value
        SESSION.messages = messages
        SESSION.reaction = outcome.get("reaction")
        # The end of an at-bat (strikeout/walk/hit/out) is attached by the engine to the last entry of pitch_log,
        # so copy it onto outcome too, for _catcher_line, which looks at the result of the last pitch.
        if SESSION.state is not None and SESSION.state.pitch_log:
            outcome.setdefault("at_bat_end",
                               SESSION.state.pitch_log[-1].get("at_bat_end"))
        SESSION.last_outcome = outcome


# ---------------------------------------------------------------------------
# HTTP server (standard library only)
# ---------------------------------------------------------------------------
_ERROR_BODY = """
<div class="card center" style="padding:2em 1.2em;">
  <p style="font-size:1.2em; font-weight:800;">Something went wrong on our side.</p>
  <p class="dim">Sorry about that. Your game could not continue, but you can start another one.</p>
  <form method="post" action="/menu"><button class="primary big" type="submit">Back to the menu</button></form>
</div>"""

_LOCK = threading.Lock()   # the game is one shared SESSION, so handle one request at a time


class Handler(http.server.BaseHTTPRequestHandler):
    timeout = 20   # drop idle / half-open connections (browsers open speculative ones) instead of waiting forever

    def log_message(self, fmt, *args):
        pass

    def _send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path="/"):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def _read_form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = urllib.parse.parse_qs(body)
        return {k: v[0] for k, v in parsed.items()}

    def do_GET(self):
        self._guarded(self._handle_get)

    def do_POST(self):
        self._guarded(self._handle_post)

    def _guarded(self, handler):
        """One request at a time. If anything unexpected happens, show a recovery page instead of a dead connection."""
        with _LOCK:
            try:
                handler()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:   # noqa: BLE001 -- the game must never leave the player on a blank page
                traceback.print_exc()
                SESSION.pitch_gen = SESSION.pending_qte = None
                try:
                    self._send_html(_page(_ERROR_BODY), status=500)
                except OSError:
                    pass

    def _handle_get(self):
        if self.path not in ("/", ""):
            self._send_html("not found", status=404)
            return
        self._send_html(_page(render_page(SESSION)))

    def _handle_post(self):
        form = self._read_form()

        if self.path == "/start":
            SESSION.start_new_game(**_start_options(form))
            self._redirect()
            return

        if not SESSION.started:
            self._redirect()
            return

        if self.path == "/tutorial":
            item = form.get("item")
            if item in SESSION.tutorial and _tutorial_ok(item, form):
                SESSION.tutorial[item] = True
            self._redirect()
            return

        if self.path == "/skiptutorial":
            SESSION.tutorial_skipped = True
            self._redirect()
            return

        if self.path == "/playball":
            SESSION.showing_intro = False
            self._redirect()
            return

        if self.path == "/newgame":
            SESSION.start_new_game(**_start_options(form))
            self._redirect()
            return

        if self.path == "/menu":
            SESSION.started = False
            SESSION.pitch_gen = SESSION.pending_qte = None
            self._redirect()
            return

        state = SESSION.state
        if SESSION.showing_intro or state.is_over():
            self._redirect()
            return

        # While a rhythm game is running, accept nothing except /qte and new game (prevents mixed-up actions)
        if SESSION.pending_qte is not None and self.path not in ("/qte", "/newgame", "/start"):
            self._redirect()
            return

        if self.path == "/pitch":
            pitch_type = form.get("pitch_type")
            course = form.get("course", "mid_mid")
            intent = form.get("intent", "strike")
            # Reject invalid values (e.g. a hand-typed POST) instead of returning a 500
            if (pitch_type not in repertoire_options(state.pitcher)
                    or course not in COURSE_SHORT or intent not in PITCH_INTENTS):
                self._redirect()
                return
            SESSION.last_batter_bats = state.batter.bats
            SESSION.last_call = {"pitch_type": pitch_type, "target_course": course, "intent": intent}
            # Start judging one pitch with the engine generator. If a QTE is needed, it is pushed to pending_qte.
            SESSION.pitch_gen = resolve_pitch_flow(state, pitch_type, course, intent, SESSION.rng,
                                                   tempo=_tempo_bonus(form))
            _advance_pitch_gen(None)
            self._redirect()
            return

        if self.path == "/qte":
            if SESSION.pending_qte is None:
                self._redirect()
                return
            kind, payload = SESSION.pending_qte
            _advance_pitch_gen(_qte_answer(kind, payload, form))
            self._redirect()
            return

        if self.path == "/walk":
            state.intentional_walk()
            SESSION.messages, SESSION.reaction, SESSION.last_call = [">>> Intentional walk. Putting the batter on."], None, None
            self._redirect()
            return

        if self.path == "/change":
            SESSION.reaction, SESSION.last_call = None, None
            try:
                index = int(form.get("index", "0"))
            except ValueError:
                index = 0
            new_pitcher = state.change_pitcher(index)
            if new_pitcher:
                SESSION.messages = [f">>> Pitching change. {new_pitcher.name} takes the mound. "
                                   "The pitch count resets. His pitches and form are unknown until he throws."]
            self._redirect()
            return

        if self.path == "/alignment":
            key = form.get("alignment")
            if key in ALIGNMENTS:
                state.defense.set_alignment(key)
            self._redirect()
            return

        if self.path == "/swap":
            pos = form.get("position")
            if pos in POSITIONS:
                if SESSION.pending_swap is None:
                    SESSION.pending_swap = pos
                elif SESSION.pending_swap == pos:
                    SESSION.pending_swap = None
                else:
                    state.defense.swap(SESSION.pending_swap, pos)
                    SESSION.pending_swap = None
            self._redirect()
            return

        if self.path == "/cancel_swap":
            SESSION.pending_swap = None
            self._redirect()
            return

        if self.path == "/memo":
            wait = form.get("wait")
            weak = form.get("weak") or None
            # Ignore anything that is not a real option (e.g. a hand-typed POST) so the review can never crash on it
            if wait not in ("fastball", "offspeed"):
                wait = None
            if weak not in COURSE_SHORT:
                weak = None
            state.record_read(wait=wait, weak=weak)
            self._redirect()
            return

        self._redirect()


def _parse_args():
    p = argparse.ArgumentParser(description="Behind the Plate -- a catcher's game (Web version)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="do not open the game in your browser automatically")
    p.add_argument("--host", default="127.0.0.1",
                   help="Setting 0.0.0.0 makes it reachable from other devices on the same network (phones, etc.). "
                       "Use it only on a network you trust")
    p.add_argument("--lineup", help="opposing lineup file (.json / .csv)")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed. Makes scene generation and pitch judging reproducible")
    p.add_argument("--difficulty", choices=("beginner", "normal", "expert"),
                   default=os.environ.get("PITCHSIM_DIFFICULTY", "normal"))
    p.add_argument("--mlb-demo", action="store_true",
                   help="use a sample lineup of real players (via mlb_data_adapter)")
    p.add_argument("--mlb-live", action="store_true",
                   help="use with --mlb-demo. Try to fetch from the MLB Stats API "
                       "(falls back to sample data automatically on failure)")
    p.add_argument("--scenario", choices=_SCENARIOS,
                   help="set up the opening scene for testing: steal (steal throw) / block (blocking) / "
                       "dropped3 (dropped third strike) / bunt (bunt fielding) / signs (change signs)")
    return p.parse_args()


def main():
    args = _parse_args()
    batters = None
    if args.mlb_demo:
        from mlb_data_adapter import build_demo_lineup
        lineup, source = build_demo_lineup(prefer_live=args.mlb_live)
        batters = lineup.batters
        print(f"Loaded the MLB sample lineup ({'MLB Stats API' if source == 'live' else 'sample data'})")
    elif args.lineup:
        batters = load_lineup_file(args.lineup).batters
    global SESSION
    SESSION = GameSession(lineup_batters=batters, seed=args.seed, difficulty=args.difficulty,
                          scenario=args.scenario)
    if args.scenario:
        print(f"Starting scenario '{args.scenario}' (setting up the opening scene)")

    httpd, port = _bind_server(args.host, args.port)
    with httpd:
        url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{port}"
        print(f"Behind the Plate is running at {url} (press Ctrl+C to quit)")
        if args.host == "0.0.0.0":
            import socket
            try:
                lan_ip = socket.gethostbyname(socket.gethostname())
                print(f"From a phone on the same Wi-Fi, open http://{lan_ip}:{port}"
                      " (if it does not connect, use the IP shown by ipconfig / ifconfig)")
            except OSError:
                pass
        if not args.no_browser:
            threading.Timer(0.6, webbrowser.open, args=(url,)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def _bind_server(host, port, tries=10):
    """Bind to `port`, or the next free one if it is taken (so a second copy or a stale run never blocks the game)."""
    last_err = None
    for p in range(port, port + tries):
        try:
            return _Server((host, p), Handler), p
        except OSError as err:
            last_err = err
    raise SystemExit(f"Could not open a port from {port} to {port + tries - 1}: {last_err}")


if __name__ == "__main__":
    main()
