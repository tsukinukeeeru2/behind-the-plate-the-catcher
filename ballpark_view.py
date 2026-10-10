"""Draw the defensive alignment as a bird's-eye view of the field (with colors).

It only builds strings. It does not depend on any game logic.
"""

import ansi
from fielders import position_fit, weakest_fielder


def _cell(defense, position, weak):
    f = defense.fielder_at(position)
    fit = position_fit(f, position)
    name = f"{f.name}:{fit:02.0f}"
    if f is weak:
        name = ansi.red("▼" + name)          # ▼ + red
    elif fit >= 58:
        name = ansi.green(name)
    elif fit <= 46:
        name = ansi.yellow(name)
    return f"{ansi.dim(position)} {name}"


def render_field(defense, batter=None):
    weak = weakest_fielder(defense.fielders)
    c = lambda pos: _cell(defense, pos, weak)

    title = ansi.bold(f"DEFENSE  [{defense.describe_alignment()}]")
    lines = [
        ansi.dim("━" * 6) + " " + title + " " + ansi.dim("━" * 18),
        "",
        f"                    {c('CF')}",
        "",
        f"        {c('LF')}                 {c('RF')}",
        "",
        f"                {c('SS')}     {c('2B')}",
        "",
        f"        {c('3B')}                 {c('1B')}",
        "",
        ansi.dim("                   P      C (you)"),
    ]
    if batter is not None:
        lines.append(ansi.dim(f"              ▶ Batter  {batter.public_hand_name()}"
                              f" / {batter.type_label()}"))
    lines.append("")
    lines.append(ansi.dim("  ▼ = weakest fielder   number = fit at that position (0-99)"))
    lines.append(ansi.dim("━" * 58))
    return "\n".join(lines)
