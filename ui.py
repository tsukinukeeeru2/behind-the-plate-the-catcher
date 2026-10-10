"""Builds the CLI display (with colors).

Swap this out and you can turn the game into a GUI (the logic is untouched).
ansi.py handles the colors, and they turn off automatically when piped.

Internal measures such as decision_quality are NEVER shown during play (only after the game).
"""

import ansi
from constants import COURSE_SHORT
from pitch_data import pitch_name
from reaction import render_reaction_block
from strategy import PITCH_INTENTS, build_analysis, catcher_rating, grade_reads

_COURSE_TEXT = COURSE_SHORT


def _pips(filled, total, color):
    filled = max(0, min(total, filled))
    return ansi.paint("●" * filled, color) + ansi.dim("○" * (total - filled))


def _result_color(result):
    if result in ("Out", "Whiff", "Strikeout"):
        return "bgreen"
    if result in ("Single", "Double", "Triple", "Home run", "Walk", "Error"):
        return "bred"
    if result == "Ball":
        return "byellow"
    return "bcyan"          # Strike / Foul


_BEGINNER_HINTS = (
    "\n" + ansi.dim("--- Beginner hints (--difficulty beginner) ---") + "\n"
    " - A face is not always the truth (60% honest / 15% opposite / 25% blurred). Do not trust a single pitch.\n"
    " - The text in parentheses after each intent explains the goal. Ask whether it fits the count and the situation.\n"
    " - The pitch-calling review after three outs judges results and decision quality separately."
)


def render_intro(hints=False):
    text = (
        ansi.bold(ansi.cyan("══════════ BEHIND THE PLATE ══════════")) + "\n"
        " A different late-inning scene every time. One game = get three outs this half inning.\n"
        " As the catcher you pick the pitch type / target location / intent.\n"
        " No answers are shown. Read the reactions, history, batter, defense, and spray chart.\n"
        + ansi.dim("═══════════════════════════════════════════")
    )
    if hints:
        text += _BEGINNER_HINTS
    return text


# ---------------------------------------------------------------------------
# The situation card at the start
# ---------------------------------------------------------------------------
def render_situation_card(state):
    b = state.batter
    hand = "RHP" if state.pitcher.throws == "R" else "LHP"
    return "\n".join([
        ansi.dim("--------------------------------"),
        ansi.bold(ansi.cyan(state.inning_label())),
        f"Us {ansi.green(state.our_score)}  -  {ansi.bold(state.opp_score)} Them",
        "",
        f"{state.outs} OUT    Runners: {ansi.yellow(state.runners_text())}",
        f"Pitcher: Reliever ({hand})",
        "",
        ansi.bold(f"BATTER #{state.lineup.spot_number()}  {b.public_line()}"),
        ansi.yellow(b.type_label()),
        ansi.dim("--------------------------------"),
    ])


# ---------------------------------------------------------------------------
# The per-pitch dashboard = scoreboard + base diagram + batter card
# ---------------------------------------------------------------------------
def _diamond(state):
    occ = lambda i: (ansi.paint("◆", "bold", "byellow") if state.runners[i] else ansi.dim("◇"))
    return [
        f"        {occ(1)}",
        f"     {occ(2)}     {occ(0)}",
        f"        {ansi.dim('⌂')}",
    ]


def render_dashboard(state, last_outcome):
    b = state.batter
    hand = "RHP" if state.pitcher.throws == "R" else "LHP"
    tire = {0: "", 1: ansi.yellow("  ⚠ Tired"), 2: ansi.red("  ⚠⚠ Gassed")}[state.pitcher.fatigue_level()]

    head = ansi.bold(ansi.cyan(f"═══ {state.inning_label()} ═══"))
    score = f"Them {ansi.bold(state.opp_score)} - {ansi.green(state.our_score)} Us"
    line1 = f" {head}  {score}   {hand}   Pitches {state.pitcher.pitches_thrown}{tire}"
    line2 = (f"   OUT {_pips(state.outs, 3, 'bred')}    "
             f"B {_pips(state.balls, 4, 'byellow')}   S {_pips(state.strikes, 3, 'bcyan')}"
             f"   Runners {ansi.yellow(state.runners_text())}")
    if state.lead_runner_fast() and not state.runners[1]:
        line2 += ansi.red("  ⚡ Steal alert")

    dia = _diamond(state)
    card = [
        ansi.bold(f" ┌ BATTER #{state.lineup.spot_number()}  {b.type_label()}  "
                  f"{b.public_line()}"),
        ansi.dim(f" │ Scouting: {b.scouting_note()}"),
    ]
    read = state.current_read()
    if read and (read["wait"] or read["weak"]):
        bits = []
        if read["wait"]:
            bits.append("Fastball" if read["wait"] == "fastball" else "Off-speed")
        if read["weak"]:
            bits.append(f"weak={read['weak']}")
        card.append(ansi.magenta(f" │ Your read: {' / '.join(bits)}"))
    if last_outcome is not None and last_outcome.get("reaction"):
        r = last_outcome["reaction"]
        card.append(f" │   {ansi.bold(r['face'])}   {ansi.dim(r['text'])}")
    card.append(ansi.dim(f" └ Calls: {state.history.summary_text()}"))

    # Put the base diagram on the right side
    body = []
    for i, cl in enumerate(card):
        d = dia[i] if i < len(dia) else ""
        body.append(f"{cl}    {d}")
    body += [f"{' ' * 40}{d}" for d in dia[len(card):]]

    return "\n".join([line1, line2, ""] + body)


# ---------------------------------------------------------------------------
# The spray chart (#B batted-ball tendencies build up and become visible during the game)
# ---------------------------------------------------------------------------
_SPRAY_W, _SPRAY_H = 25, 6


def _spray_cell(entry):
    # Left / right (the real direction, taking the batter's handedness into account)
    d, bats = entry["direction"], entry["bats"]
    if d == "center":
        side = "center"
    elif (d == "pull" and bats == "R") or (d == "oppo" and bats == "L"):
        side = "left"
    else:
        side = "right"
    col = {"left": 5, "center": 12, "right": 19}[side]
    # Front / back (the distance)
    row = {"deep": 0, "shallow": 2, "infield": 4}[entry["distance"]]
    glyph, color = {
        "Out": ("·", "grey"), "Single": ("o", "white"), "Error": ("e", "byellow"),
        "Double": ("O", "byellow"), "Triple": ("O", "byellow"), "Home run": ("H", "bred"),
    }.get(entry["result"], ("?", "white"))
    return row, col, glyph, color


def render_spray(state):
    title = ansi.bold("Spray chart (this game)") + ansi.dim(
        "   · out  o single  e error  O extra-base  H home run")
    if not state.spray:
        return title + "\n  " + ansi.dim("(no balls in play yet)")

    grid = [[" "] * _SPRAY_W for _ in range(_SPRAY_H)]
    for entry in state.spray:
        row, col, glyph, color = _spray_cell(entry)
        for dc in range(2):                       # scatter it slightly sideways
            r, c = row + (dc and entry["ball_type"] == "fly"), col + dc
            if 0 <= r < _SPRAY_H and 0 <= c < _SPRAY_W and grid[r][c] == " ":
                grid[r][c] = ansi.paint(glyph, color)
                break
    grid[_SPRAY_H - 1][_SPRAY_W // 2] = ansi.dim("⌂")
    fence = ansi.dim("  " + "-" * _SPRAY_W)
    return "\n".join([title] + ["  " + "".join(r) for r in grid] + [fence])


# ---------------------------------------------------------------------------
# The fielders' ability table
# ---------------------------------------------------------------------------
def render_fielder_table(defense):
    from fielders import ATTRS, POSITIONS, position_fit, weakest_fielder
    weak = weakest_fielder(defense.fielders)
    header = "  Pos Name  " + " ".join(f"{a[:5]:>5}" for a in ATTRS) + "   Ovr   Fit"
    rows = [ansi.dim(header), ansi.dim("  " + "─" * (len(header) - 2))]
    for pos in POSITIONS:
        f = defense.fielder_at(pos)
        stats = " ".join(f"{getattr(f, a):5.0f}" for a in ATTRS)
        line = f"  {pos:<3} {f.name:<4} {stats}   {f.overall():4.0f}  {position_fit(f, pos):4.0f}"
        rows.append(ansi.red(line) if f is weak else line)
    rows.append("")
    rows.append(ansi.bold(f"  Expected Defensive Efficiency (reference): "
                          f"{defense.expected_defensive_efficiency():.0f} / 100"))
    rows.append(ansi.dim("  * A rough guide, not the best answer."))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# The result of one pitch
# ---------------------------------------------------------------------------
def render_play_result(outcome, ascii_only=None):
    pn = pitch_name(outcome["pitch_type"])
    tgt = f"{pn} / {_COURSE_TEXT[outcome['target_course']]}"
    if outcome["missed"]:
        act = f"{pn} / {ansi.red(_COURSE_TEXT[outcome['actual_course']])}  {ansi.red('<- missed spot')}"
    else:
        act = f"{pn} / {_COURSE_TEXT[outcome['actual_course']]}"
    rc = _result_color(outcome["result"])
    lines = [
        ansi.dim(f"Call: {tgt}    Intent: {PITCH_INTENTS[outcome['intent']]}"),
        f"Actual: {act}    Pitch quality: {int(outcome['quality'] * 100)}",
        f">>> Result: {ansi.bold(ansi.paint(outcome['result'], rc))}",
    ]
    bb, fd = outcome.get("batted_ball"), outcome.get("fielding")
    if bb is not None and fd is not None:
        lines.append(ansi.dim(f">>> Batted ball: {bb.describe()} -> {fd['fielder']} at {fd['position']}"
                              f" (out probability {fd['out_probability'] * 100:.0f}%)"))
    play = outcome.get("play")
    if play is not None:
        bits = []
        if play["label"] != outcome["result"]:
            bits.append(play["label"])
        if play["detail"]:
            bits.append(play["detail"])
        if play["runs"]:
            bits.append(ansi.red(f"{play['runs']} run{'s' if play['runs'] != 1 else ''} allowed"))
        if play["outs_added"] >= 2:
            bits.append("2 outs")
        if bits:
            lines.append(f">>> Baserunning: {' / '.join(bits)}")
    r = outcome.get("reaction")
    if r is not None:
        lines.append("")
        lines.append(render_reaction_block(r, ascii_only))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# After the game (only after three outs)
# ---------------------------------------------------------------------------
def render_inning_result(state):
    run_txt, status = state.result_summary()
    runc = "bgreen" if state.runs_this_inning == 0 else "bred"
    return "\n".join([
        ansi.dim("═" * 44),
        ansi.bold(f" Inning over: {ansi.paint(run_txt, runc)} / {ansi.yellow(status)}"),
        f" Us {state.our_score} - {state.opp_score} Them"
        + ansi.dim(f" (started {state.start_our} - {state.start_opp})"),
        ansi.dim("═" * 44),
    ])


def _fmt_moments(moments, limit=3, color=None):
    if not moments:
        return ansi.dim("  none")
    out = []
    for item in moments[:limit]:
        num, label = item[0], item[1]
        tail = f" -> {item[2]}" if len(item) > 2 else ""
        line = f"  Pitch {num}: {label}{tail}"
        out.append(ansi.paint(line, color) if color else line)
    if len(moments) > limit:
        out.append(ansi.dim(f"  ...and {len(moments) - limit} more"))
    return "\n".join(out)


def render_analysis(state):
    a = build_analysis(state.pitch_log)
    reads = grade_reads(state.reads, state.lineup.batters)
    lines = [
        ansi.bold("--- Pitch-calling review (results and decisions judged separately) ---"),
        f"Total pitches: {a['total_pitches']}",
        "",
        ansi.green("◎ Good calls (fooled him / threw him off / fit the defense):"),
        _fmt_moments(a["good_calls"], color="green"),
        "",
        ansi.yellow("△ Risky calls (loose / overshown / wrong for the count):"),
        _fmt_moments(a["risky_calls"], color="byellow"),
        "",
        f"Fooled his guess: {len(a['fooled_moments'])}   "
        f"He read it: {len(a['read_moments'])}",
        f"Defense fit: {len(a['defense_moments'])}   "
        f"Misses that hurt: {len(a['misfire_moments'])}",
        "",
        ansi.red("◇ Extra-base hits allowed:"),
        _fmt_moments(a["extra_base"], color="bred"),
        "",
        ansi.cyan("★ Bad result, good decision (unlucky):"),
        _fmt_moments(a["unlucky"], color="bcyan"),
        ansi.yellow("★ Bad decision, saved by the result (lucky):"),
        _fmt_moments(a["lucky"], color="byellow"),
        "",
    ]
    if reads["rows"]:
        lines.append(ansi.magenta(f"Your reads: {reads['correct']} / {reads['graded']} right"))
        lines += reads["rows"]
        lines.append("")
    framed = getattr(state, "framed_strikes", 0)
    if framed:
        lines.append(ansi.cyan(f"Strikes stolen by framing: {framed}"))
    lines.append(ansi.bold(f"Decision quality (average): {a['decision_quality_label']}"))
    rating = catcher_rating(state, analysis=a)
    lines.append("")
    lines.append(ansi.bold(ansi.cyan(
        f"=== CATCHER RATING: {rating['grade']}  ({rating['score']} / 100)  {rating['title']} ===")))
    if rating["badges"]:
        lines.append("Badges: " + ", ".join(name for name, _ in rating["badges"]))
    lines.append(ansi.dim("* This is a post-game review. No answers are shown during play."))
    return "\n".join(lines)
