"""The Play Viewer: replays a batted ball like a Japanese "yakyuban" board game.

For one fair ball it builds a small "script": where the ball flies, where each fielder runs, who throws
to which base, and how every runner moves, all on a timeline in seconds. The web page then plays that
script back on an SVG field (see render_play_viewer()).

This file only DRAWS what judge.py / fielding.py / baserunning.py already decided. It never changes the result
and it never touches the game's random numbers: the small wobbles (exactly where a fly ball lands, and so on)
come from its own random.Random seeded by the play itself, so the same play always looks the same.

Coordinates: an SVG viewBox of 400 x 400. Home plate is at the bottom centre. angle 0 = straight to
centre field, negative = toward left field, positive = toward right field (as seen from behind home plate).
"""

import json
import math
import random

HOME = (200.0, 372.0)
BASES = {0: HOME, 1: (267.0, 305.0), 2: (200.0, 238.0), 3: (133.0, 305.0), 4: HOME}
MOUND = (200.0, 318.0)
FENCE_R = 275.0

INFIELD = ("3B", "SS", "2B", "1B")
OUTFIELD = ("LF", "CF", "RF")
POSITIONS = INFIELD + OUTFIELD

# Where each fielder stands in the standard alignment: (angle in degrees, distance from home in px)
_STAND = {"3B": (-40, 108), "SS": (-17, 128), "2B": (17, 128), "1B": (42, 102),
          "LF": (-28, 195), "CF": (0, 205), "RF": (28, 195)}

_BALL_SPEED = {"soft": 170.0, "medium": 250.0, "hard": 330.0}   # px per second for a ground ball


def polar(angle, r):
    rad = math.radians(angle)
    return (HOME[0] + r * math.sin(rad), HOME[1] - r * math.cos(rad))


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _stand_polar(pos, alignment, bats):
    """(angle, radius) of a fielder after the defensive shift is applied."""
    a, r = _STAND[pos]
    infield = pos in INFIELD
    pull_sign = -1 if bats == "R" else 1      # a righty pulls toward left field (the left of the screen)
    if alignment == "pull":
        a += pull_sign * (7 if infield else 9)
    elif alignment == "oppo":
        a -= pull_sign * (7 if infield else 9)
    elif alignment == "infield_in" and infield:
        r *= 0.78
    elif alignment == "dp" and infield:
        r *= 0.92
        a *= 0.8
    elif alignment == "no_doubles" and not infield:
        r += 28
    elif alignment == "bunt" and pos in ("1B", "3B"):
        r *= 0.7
    return a, r


def _r2(x):
    return round(x, 2)


class _Track:
    """A thing that moves in straight segments: (t0, t1, x0, y0, x1, y1). It waits between segments."""

    def __init__(self, start):
        self.start = (_r2(start[0]), _r2(start[1]))
        self.pos = start
        self.segs = []

    def end_time(self):
        return self.segs[-1][1] if self.segs else 0.0

    def go(self, t0, t1, to):
        t0 = max(t0, self.end_time())
        t1 = max(t1, t0 + 0.05)
        self.segs.append([_r2(t0), _r2(t1), _r2(self.pos[0]), _r2(self.pos[1]), _r2(to[0]), _r2(to[1])])
        self.pos = to


class _Ball(_Track):
    """The ball: each segment also has an arc height (px) and a number of bounces/arcs."""

    def fly(self, t0, t1, to, height, arcs=1):
        n_before = len(self.segs)
        self.go(t0, t1, to)
        self.segs[n_before] += [round(height, 1), arcs]


def _assign_runners(play, fielding):
    """Work out, for the batter and each runner, where he ends up and whether he is safe or out.

    judge.py only tells us who is on base before and after, how many runs scored, and how many outs were made.
    We match them up in the most natural way: the lead runner takes the furthest base, and when someone is out
    it is the runner furthest back.
    """
    before = play.get("runners_before") or [False, False, False]
    runs = play["runs"]
    outs_added = play["outs_added"]
    dests = [4] * runs + [b for b in (3, 2, 1) if play["new_runners"][b - 1]]
    batter_out = bool(fielding["batter_out"])
    out_n = max(0, outs_added - 1) if batter_out else outs_added

    ents = [(o, f"r{o}") for o in (3, 2, 1) if before[o - 1]]
    if not batter_out:
        ents.append((0, "b"))

    result = []
    for i, (origin, ident) in enumerate(ents):
        if i < len(dests):
            dest = max(dests[i], origin)
            fate = "scored" if dest == 4 else "safe"
        elif i >= len(ents) - out_n:
            dest, fate = min(origin + 1, 4), "out"
        else:
            dest, fate = origin, "hold"
        result.append({"id": ident, "origin": origin, "dest": dest, "fate": fate})
    if batter_out:
        result.append({"id": "b", "origin": 0, "dest": 1, "fate": "out"})
    return result


def _runner_label(ident):
    return "B" if ident == "b" else ident[1]


def build_play_script(batted_ball, fielding, play, bats=None, alignment=None, seed_key=""):
    """Build the replay script (a JSON-friendly dict) for one fair ball."""
    bats = bats or fielding.get("bats") or "R"
    alignment = alignment or fielding.get("alignment") or "standard"
    res = fielding["result"]
    pos = fielding["position"]
    bt, dist, hard = batted_ball.ball_type, batted_ball.distance, batted_ball.hardness
    rnd = random.Random(f"{seed_key}|{batted_ball.direction}|{bt}|{dist}|{batted_ball.hardness_score}|{pos}|{res}")

    stand_pol = {p: _stand_polar(p, alignment, bats) for p in POSITIONS}
    stand_xy = {p: polar(*stand_pol[p]) for p in POSITIONS}
    sa, sr = stand_pol[pos]
    fielders = {p: _Track(stand_xy[p]) for p in POSITIONS}
    ball = _Ball(HOME)
    events = []
    ents = _assign_runners(play, fielding)
    runs_scored = any(e["fate"] == "scored" for e in ents) and res != "Home run"
    throw_to = HOME if runs_scored else MOUND

    def add_event(t, text, kind="info"):
        events.append([_r2(t), text, kind])

    runner_end = {}          # ident -> (t0, t1) overrides for the run
    last_t = 1.0

    def throw(t0, frm, to, who, arc=18):
        """The fielder `who` throws from `frm` to `to`. Returns the arrival time."""
        d = _dist(frm, to)
        arrive = t0 + 0.12 + d / 440.0
        ball.fly(t0, arrive, to, arc if d > 40 else 6)
        return arrive

    if res == "Home run":
        a = max(-42.0, min(42.0, sa + rnd.uniform(-8, 8)))
        end = polar(a, 322)
        ball.fly(0.2, 2.7, end, 240.0)
        fielders[pos].go(0.4, 2.4, polar(a, 262))
        add_event(2.55, "HOME RUN!", "bad")
        for e in ents:
            runner_end[e["id"]] = (0.5, 0.5 + 0.7 * (e["dest"] - e["origin"]))
        last_t = 2.7

    elif fielding["air_out"]:
        # ---- caught in the air ----
        if dist == "infield":
            r = sr + rnd.uniform(2, 10) if pos in INFIELD else rnd.uniform(118, 140)
        elif dist == "shallow":
            r = rnd.uniform(135, 165)
        else:
            r = rnd.uniform(212, 244)
        a = max(-43.0, min(43.0, sa + rnd.uniform(-5, 5)))
        land = polar(a, r)
        if bt == "fly":
            tf = 1.2 + r / 200.0
            if hard == "hard":
                tf *= 0.9
            height = 110 + 0.3 * r
        else:
            tf = 0.3 + r / 380.0
            height = 28
        tc = 0.2 + tf
        ball.fly(0.2, tc, land, height)
        fielders[pos].go(0.35, tc - 0.05, land)
        add_event(tc, "CAUGHT!", "good")
        for e in ents:
            if e["id"] == "b":
                runner_end[e["id"]] = (0.28, tc)          # he stops running when the ball is caught
            elif e["dest"] > e["origin"]:
                runner_end[e["id"]] = (tc, tc + 0.62 * (e["dest"] - e["origin"]))   # tag up
        if runs_scored:
            arrive = throw(tc + 0.25, land, HOME, pos, arc=30)
            last_t = arrive
        else:
            last_t = tc
        if pos in OUTFIELD:                               # a neighbour backs up the play
            mate = "CF" if pos != "CF" else ("LF" if a > 0 else "RF")
            fielders[mate].go(0.4, tc, (( stand_xy[mate][0] + land[0]) / 2, (stand_xy[mate][1] + land[1]) / 2))

    elif bt == "ground" and fielding["batter_out"]:
        # ---- ground ball, the batter is thrown out ----
        a = sa + rnd.uniform(-3, 3)
        spot = polar(a, sr + rnd.uniform(4, 12))
        tb = 0.2 + _dist(HOME, spot) / _BALL_SPEED[hard]
        ball.fly(0.2, tb, spot, 7, arcs=max(2, int(_dist(HOME, spot) / 40)))
        fielders[pos].go(0.3, tb - 0.05, spot)
        tf = tb + 0.15
        add_event(tf, "FIELDED", "info")
        double_play = play["outs_added"] >= 2
        cover = None
        if double_play:
            cover = {"3B": "SS", "SS": "2B", "2B": "SS", "1B": "2B"}.get(pos, "SS")
            second = (BASES[2][0], BASES[2][1] + 6)
            fielders[cover].go(0.3, tf + 0.1, second)
            t1 = throw(tf + 0.15, spot, second, pos)
            add_event(t1, "OUT!", "good")
            first = (BASES[1][0] - 6, BASES[1][1] + 4)
            t2 = throw(t1 + 0.15, second, first, cover)
            add_event(t2, "DOUBLE PLAY!", "good")
            arrive_first = t2
            for e in ents:
                if e["fate"] == "out" and e["id"] != "b":
                    runner_end[e["id"]] = (0.3, t1 + 0.08)
        else:
            if pos == "1B":
                fielders[pos].go(tf, tf + 0.45, (BASES[1][0] - 6, BASES[1][1] + 4))
                arrive_first = tf + 0.45
                ball.fly(tf, arrive_first, BASES[1], 4)
            else:
                first = (BASES[1][0] - 6, BASES[1][1] + 4)
                arrive_first = throw(tf + 0.1, spot, first, pos)
            add_event(arrive_first, "OUT!", "good")
        if pos != "1B":
            fielders["1B"].go(0.3, arrive_first - 0.15, (BASES[1][0] - 6, BASES[1][1] + 4))
        batter_end = max(arrive_first + 0.12, 0.28 + 1.3)
        runner_end["b"] = (0.28, batter_end)
        for e in ents:
            if e["id"] != "b" and e["fate"] in ("safe", "scored"):
                runner_end[e["id"]] = (0.3, 0.3 + 0.62 * (e["dest"] - e["origin"]))
        last_t = max(arrive_first, batter_end)

    elif bt == "ground":
        # ---- ground ball, a hit or an error ----
        batter_dest = next((e["dest"] for e in ents if e["id"] == "b"), 1)
        if dist == "infield" and res in ("Single", "Error"):
            # The infielder gets to it, but not in time to get the (fast) batter
            a = sa + rnd.uniform(-3, 3)
            spot = polar(a, sr + rnd.uniform(4, 12))
            tb = 0.2 + _dist(HOME, spot) / _BALL_SPEED[hard]
            ball.fly(0.2, tb, spot, 7, arcs=max(2, int(_dist(HOME, spot) / 40)))
            fielders[pos].go(0.3, tb - 0.05, spot)
            first = (BASES[1][0] - 6, BASES[1][1] + 4)
            batter_end = 0.28 + 1.3
            tf = tb + 0.15
            if res == "Error":
                loose = polar(a + rnd.choice([-1, 1]) * 5, sr + 34)
                ball.fly(tb, tb + 0.35, loose, 6, arcs=2)
                add_event(tb + 0.1, "ERROR!", "bad")
                fielders[pos].go(tb + 0.1, tb + 0.55, loose)
                tf = tb + 0.55
            arrive = max(tf + 0.12 + _dist(ball.pos, first) / 440.0, batter_end + 0.2)
            ball.fly(arrive - 0.12 - _dist(ball.pos, first) / 440.0, arrive, first, 18)
            add_event(arrive, res.upper() if res != "Single" else "BEATS THE THROW!", "bad")
            runner_end["b"] = (0.28, batter_end)
            last_t = arrive
        else:
            # Through the hole (or down the line) into the outfield
            side = 1 if (sa >= 0) else -1
            if res in ("Double", "Triple"):
                q = polar(side * 41, 262 if res == "Triple" else 245)
            else:
                a_pass = max(-43.0, min(43.0, sa - side * rnd.uniform(9, 13)))
                if abs(a_pass) < 5:
                    a_pass = side * -6.0
                q = polar(a_pass, rnd.uniform(160, 176))
            tq = 0.2 + _dist(HOME, q) / _BALL_SPEED[hard]
            ball.fly(0.2, tq, q, 6, arcs=max(3, int(_dist(HOME, q) / 40)))
            dive = polar(math.degrees(math.atan2(q[0] - HOME[0], HOME[1] - q[1])), sr)
            fielders[pos].go(0.3, 0.2 + sr / _BALL_SPEED[hard], dive)
            qa = math.degrees(math.atan2(q[0] - HOME[0], HOME[1] - q[1]))
            of = "LF" if qa < -14 else ("RF" if qa > 14 else "CF")
            fielders[of].go(0.4, max(tq + 0.1, 0.4 + _dist(stand_xy[of], q) / 130.0), q)
            tp = fielders[of].end_time()
            arrive = throw(tp + 0.15, q, throw_to, of, arc=40)
            add_event(tq, "THROUGH THE HOLE!" if res == "Single" else res.upper() + "!", "bad")
            last_t = arrive

    else:
        # ---- fly ball / line drive that is not caught: a hit, or a dropped ball ----
        side = -1 if sa > 6 else (1 if sa < -6 else rnd.choice([-1, 1]))
        if res == "Error":
            r = sr + rnd.uniform(-4, 4) if dist != "deep" else rnd.uniform(205, 225)
            a = sa
        else:
            if res == "Triple":
                r = rnd.uniform(255, 268)
            elif res == "Double":
                r = rnd.uniform(235, 255) if dist == "deep" else rnd.uniform(190, 215)
            elif dist == "infield":
                r = sr + rnd.uniform(35, 55) if pos in INFIELD else rnd.uniform(105, 125)
            elif dist == "shallow":
                r = rnd.uniform(140, 165)
            else:
                r = rnd.uniform(190, 215)
            a = max(-43.0, min(43.0, sa + side * rnd.uniform(9, 14)))
        land = polar(a, r)
        if bt == "fly":
            tf = 1.2 + r / 200.0
            if hard == "hard":
                tf *= 0.9
            height = 110 + 0.3 * r
        else:
            tf = 0.3 + r / 380.0
            height = 28
        t_land = 0.2 + tf
        ball.fly(0.2, t_land, land, height)
        if res == "Error":
            fielders[pos].go(0.35, t_land - 0.05, land)
            loose = polar(a + rnd.choice([-1, 1]) * 5, min(r + 24, FENCE_R - 8))
            ball.fly(t_land, t_land + 0.4, loose, 8, arcs=2)
            add_event(t_land, "DROPPED!", "bad")
            fielders[pos].go(t_land + 0.1, t_land + 0.55, loose)
            pickup, t_pick = loose, t_land + 0.55
        else:
            wall = res in ("Double", "Triple")
            roll_to = polar(a + side * 2, min(r + (38 if wall else 18), FENCE_R - 5))
            roll_t = 0.9 if wall else 0.45
            ball.fly(t_land, t_land + roll_t, roll_to, 7, arcs=2)
            add_event(t_land, "IN THE GAP!" if wall else "FALLS IN!", "bad")
            t_pick = max(t_land + roll_t, 0.35 + _dist(stand_xy[pos], roll_to) / 125.0)
            fielders[pos].go(0.35, t_pick, roll_to)
            pickup = roll_to
        arrive = throw(t_pick + 0.15, pickup, throw_to, pos, arc=40)
        last_t = arrive
        mate = "CF" if pos != "CF" else ("LF" if a > 0 else "RF")
        if pos in OUTFIELD:
            fielders[mate].go(0.4, t_pick, ((stand_xy[mate][0] + pickup[0]) / 2, (stand_xy[mate][1] + pickup[1]) / 2))

    # ---- runners: any run not overridden above uses the default pace ----
    runners = []
    t_end_all = last_t
    for e in ents:
        t0, t1 = runner_end.get(e["id"], (0.3, 0.3 + 0.62 * max(0, e["dest"] - e["origin"])))
        tr = _Track(BASES[e["origin"]])
        n = e["dest"] - e["origin"]
        if e["fate"] == "out" and e["id"] == "b" and fielding["air_out"]:
            tr.go(t0, t1, ((HOME[0] * 0.45 + BASES[1][0] * 0.55), (HOME[1] * 0.45 + BASES[1][1] * 0.55)))
        elif n > 0 and t1 > t0:
            step = (t1 - t0) / n
            for k in range(n):
                tr.go(t0 + k * step, t0 + (k + 1) * step, BASES[e["origin"] + k + 1])
        runners.append({"id": e["id"], "label": _runner_label(e["id"]), "start": list(tr.start),
                        "segs": tr.segs, "fate": e["fate"]})
        t_end_all = max(t_end_all, tr.end_time())

    label = play["label"].upper()
    if play["runs"]:
        label += f" · {play['runs']} RUN{'S' if play['runs'] != 1 else ''}"
    duration = _r2(max(t_end_all, last_t) + 1.0)
    if res != "Home run":
        add_event(duration - 0.7, label, "good" if fielding["batter_out"] else "bad")
    elif play["runs"]:
        events.append([_r2(duration - 0.5), f"{play['runs']} RUN{'S' if play['runs'] != 1 else ''} SCORE", "bad"])
    events.sort(key=lambda ev: ev[0])

    return {
        "duration": duration,
        "ball": {"start": list(ball.start), "segs": ball.segs},
        "fielders": {p: {"start": list(fielders[p].start), "segs": fielders[p].segs} for p in POSITIONS},
        "runners": runners,
        "events": events,
    }


# ---------------------------------------------------------------------------
# The page piece: static field + player
# ---------------------------------------------------------------------------
_FIELD_SVG = """
<svg id="pv-svg" viewBox="0 0 400 400" role="img" aria-label="Replay of the batted ball">
  <defs>
    <radialGradient id="pv-grass" cx="50%" cy="95%" r="80%">
      <stop offset="0%" stop-color="#3f9a58"/><stop offset="100%" stop-color="#2c7a43"/>
    </radialGradient>
    <radialGradient id="pv-dirt" cx="50%" cy="60%" r="70%">
      <stop offset="0%" stop-color="#d49a55"/><stop offset="100%" stop-color="#a2703a"/>
    </radialGradient>
  </defs>
  <rect width="400" height="400" fill="#16301f"/>
  <path d="M200 372 L5.5 177.5 A275 275 0 0 1 394.5 177.5 Z" fill="url(#pv-grass)"/>
  <path d="M30 203 A245 245 0 0 1 370 203" fill="none" stroke="#b58a52" stroke-width="16" opacity=".55"/>
  <path d="M200 372 L5.5 177.5 A275 275 0 0 1 394.5 177.5 Z" fill="none" stroke="#f5eeda" stroke-width="2.5"/>
  <circle cx="200" cy="318" r="112" fill="url(#pv-dirt)"/>
  <polygon points="200,372 267,305 200,238 133,305" fill="#3a9150" opacity=".95"/>
  <polygon points="200,372 267,305 200,238 133,305" fill="none" stroke="#f5eeda" stroke-width="2" opacity=".8"/>
  <path d="M200 372 L5.5 177.5 M200 372 L394.5 177.5" stroke="#f5eeda" stroke-width="2" opacity=".85"/>
  <g fill="none" stroke="#f5eeda" stroke-width="1" opacity=".22" stroke-dasharray="4 5">
    <path d="M52.7 224 A200 200 0 0 1 347.3 224"/><path d="M95 266 A140 140 0 0 1 305 266"/>
  </g>
  <circle cx="200" cy="318" r="9" fill="#b47a3e" stroke="#f5eeda" stroke-width="1"/>
  <g fill="#f8f2e2">
    <rect x="261.5" y="299.5" width="11" height="11" transform="rotate(45 267 305)"/>
    <rect x="194.5" y="232.5" width="11" height="11" transform="rotate(45 200 238)"/>
    <rect x="127.5" y="299.5" width="11" height="11" transform="rotate(45 133 305)"/>
    <path d="M200 380 l-8 -8 v-7 h16 v7 z"/>
  </g>
  <g id="pv-fielders"></g>
  <g id="pv-runners"></g>
  <ellipse id="pv-shadow" rx="4.5" ry="2.2" fill="#000" opacity=".3"/>
  <circle id="pv-ball" r="5" fill="#fff" stroke="#c33" stroke-width="1"/>
  <g id="pv-banner" opacity="0">
    <rect x="70" y="30" width="260" height="38" rx="8" fill="rgba(8,14,24,.82)" stroke="currentColor" stroke-width="2"/>
    <text x="200" y="56" text-anchor="middle" font-size="19" font-weight="800" fill="currentColor"
          style="letter-spacing:.06em"></text>
  </g>
</svg>
"""

_CSS = """
<style>
.pv-card { max-width:560px; margin-left:auto; margin-right:auto; }
.pv-card svg { width:100%; height:auto; display:block; border-radius:12px; background:#16301f; }
.pv-bar { display:flex; gap:.6em; align-items:center; flex-wrap:wrap; margin-top:.6em; }
.pv-bar button { font-size:.85em; }
.pv-caption { color:var(--muted); font-size:.88em; flex:1; min-width:10em; }
#pv-banner { color:#e9eef7; }
#pv-banner.good { color:#2fd08a; }
#pv-banner.bad { color:#ff6a6a; }
.pv-fielder circle { fill:#2f6fe0; stroke:#fff; stroke-width:1.6; }
.pv-fielder text, .pv-runner text { font-size:8.5px; font-weight:800; fill:#fff; text-anchor:middle; pointer-events:none; }
.pv-runner circle { fill:#ff5a4a; stroke:#fff; stroke-width:1.4; }
.pv-runner.out { opacity:.35; }
</style>
"""

_JS = """
<script>
(function () {
  var D = JSON.parse(document.getElementById('pv-data').textContent);
  var NS = 'http://www.w3.org/2000/svg';
  var autoplay = document.getElementById('pv-data').getAttribute('data-autoplay') === '1';
  var fieldersG = document.getElementById('pv-fielders'), runnersG = document.getElementById('pv-runners');
  var ballEl = document.getElementById('pv-ball'), shadowEl = document.getElementById('pv-shadow');
  var banner = document.getElementById('pv-banner'), bannerText = banner.querySelector('text');
  var slow = document.getElementById('pv-slow');
  var items = [];
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function mk(g, cls, label, r, d) {
    var grp = el('g', {'class': cls}, g);
    el('circle', {r: r}, grp);
    var t = el('text', {y: 3}, grp);
    t.textContent = label;
    return {g: grp, d: d};
  }
  Object.keys(D.fielders).forEach(function (p) { items.push(mk(fieldersG, 'pv-fielder', p, 11, D.fielders[p])); });
  D.runners.forEach(function (r) { var it = mk(runnersG, 'pv-runner', r.label, 8, r); it.r = r; items.push(it); });
  // The pitcher and catcher stand still
  [['P', 200, 318], ['C', 200, 386]].forEach(function (s) {
    var it = mk(fieldersG, 'pv-fielder', s[0], 9, {start: [s[1], s[2]], segs: []});
    items.push(it);
  });

  function ease(u) { return u * u * (3 - 2 * u); }
  function posAt(d, t, smooth) {
    var p = d.start;
    for (var i = 0; i < d.segs.length; i++) {
      var s = d.segs[i];
      if (t < s[0]) return p;
      if (t >= s[1]) { p = [s[4], s[5]]; continue; }
      var u = (t - s[0]) / (s[1] - s[0]); if (smooth) u = ease(u);
      return [s[2] + (s[4] - s[2]) * u, s[3] + (s[5] - s[3]) * u];
    }
    return p;
  }
  function ballAt(t) {
    var d = D.ball, p = d.start, z = 0;
    for (var i = 0; i < d.segs.length; i++) {
      var s = d.segs[i];
      if (t < s[0]) break;
      if (t >= s[1]) { p = [s[4], s[5]]; z = 0; continue; }
      var u = (t - s[0]) / (s[1] - s[0]);
      p = [s[2] + (s[4] - s[2]) * u, s[3] + (s[5] - s[3]) * u];
      var h = s[6] || 0, n = s[7] || 1;
      z = n <= 1 ? h * 4 * u * (1 - u) : h * Math.abs(Math.sin(n * Math.PI * u)) * (1 - 0.6 * u);
      break;
    }
    return {x: p[0], y: p[1], z: z};
  }
  function render(t) {
    items.forEach(function (it) {
      var p = posAt(it.d, t, true);
      it.g.setAttribute('transform', 'translate(' + p[0].toFixed(1) + ',' + p[1].toFixed(1) + ')');
      if (it.r) {
        var out = it.r.fate === 'out' && t >= D.duration - 0.8;
        it.g.setAttribute('class', 'pv-runner' + (out ? ' out' : ''));
      }
    });
    var b = ballAt(t);
    ballEl.setAttribute('cx', b.x.toFixed(1)); ballEl.setAttribute('cy', (b.y - b.z * 0.85).toFixed(1));
    ballEl.setAttribute('r', (5 + b.z / 45).toFixed(1));
    shadowEl.setAttribute('cx', b.x.toFixed(1)); shadowEl.setAttribute('cy', (b.y + 2).toFixed(1));
    var ev = null;
    for (var i = 0; i < D.events.length; i++) if (D.events[i][0] <= t) ev = D.events[i];
    if (ev) {
      bannerText.textContent = ev[1]; banner.setAttribute('class', ev[2]); banner.setAttribute('opacity', '1');
    } else { banner.setAttribute('opacity', '0'); }
  }
  var raf = null, t0 = 0;
  function frame(now) {
    var speed = slow && slow.checked ? 0.45 : 1;
    var t = (now - t0) / 1000 * speed;
    if (t >= D.duration) { render(D.duration); raf = null; return; }
    render(t); raf = requestAnimationFrame(frame);
  }
  function play() {
    if (raf) cancelAnimationFrame(raf);
    render(0); t0 = performance.now(); raf = requestAnimationFrame(frame);
  }
  window.pvSeek = function (t) { if (raf) { cancelAnimationFrame(raf); raf = null; } render(t); };   // handy for screenshots
  document.getElementById('pv-replay').addEventListener('click', play);
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (autoplay && !reduce) { setTimeout(play, 350); } else { render(D.duration); }
})();
</script>
"""


def render_play_viewer(script, caption, autoplay=True):
    """The HTML for the viewer card. `script` comes from build_play_script()."""
    import html
    data = json.dumps(script, separators=(",", ":")).replace("</", "<\\/")
    return f"""
{_CSS}
<div class="card pv-card">
  <h3>Play Viewer</h3>
  {_FIELD_SVG}
  <div class="pv-bar">
    <button type="button" id="pv-replay">&#9654; Replay</button>
    <label class="dim" style="font-size:.85em;"><input type="checkbox" id="pv-slow"> Slow-mo</label>
    <span class="pv-caption">{html.escape(caption)}</span>
  </div>
  <script type="application/json" id="pv-data" data-autoplay="{1 if autoplay else 0}">{data}</script>
</div>
{_JS}"""
