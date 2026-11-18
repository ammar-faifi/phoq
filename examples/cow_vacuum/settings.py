"""Which setting is wrong: E_p^u and its three parts, one wrong setting at a time.

    uv run phoq run examples/cow_vacuum/settings.py                      # the table
    uv run phoq run examples/cow_vacuum/settings.py --plot settings.png  # and a figure

Each row is the expected reading of ``theory()`` on the default bench with one setting
changed, the way ``tests/test_cow_vacuum.py`` changes it: a field of ``COWParams``, a
value set on all three detectors, or Bob's DLI turned off its fringe. The table is the
one in the package's README. The figure stacks E_p^u from the three parts of
``bound.terms``, then gives each part a panel of its own, all on one scale, with the
default dashed: μ moves ``beta`` far more than any fault does, and the hardware's faults
show in ``M0`` and ``M1``.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from phoq.parties import load_protocol

cv = load_protocol(Path(__file__).resolve().parent)
P = cv.COWParams

# (row, COWParams, DLI off its fringe [rad], values set on D_T, D_M0 and D_M1)
SETTINGS = [
    ("Default", P(), 0.0, {}),
    ("No click window", P(click_window=False), 0.0, {}),
    ("μ = 2·10⁻³", P(mu=2e-3), 0.0, {}),
    ("40 dB modulator", P(extinction_db=40.0), 0.0, {}),
    ("25 dB modulator", P(extinction_db=25.0), 0.0, {}),
    ("DLI 30° off its fringe", P(), math.radians(30), {}),
    ("10 MHz laser", P(linewidth=10e6), 0.0, {}),
    ("No phase shared between pulses", P(linewidth=10e9), 0.0, {}),
    (
        "Dark counts at 600 Hz, the datasheet's maximum",
        P(),
        0.0,
        dict(dark_count_rate=600.0),
    ),
    ("Hold-off of 100 µs, the datasheet's", P(dead_time=100e-6), 0.0, {}),
    ("No timing jitter", P(), 0.0, dict(timing_jitter=0.0)),
]
PARTS = (  # bound.terms' keys, their colors and panel titles
    ("beta", "#3987e5", r"beta: the $|\beta,\beta\rangle$ terms"),
    ("M0", "#d95926", r"M0: unexplained at $D_{M0}$"),
    ("M1", "#199e70", r"M1: clicks at $D_{M1}$"),
)


def expected(p, detune: float = 0.0, **detectors) -> dict:
    """``theory()`` on the bench built for ``p``, with ``detectors`` set on D_T, D_M0
    and D_M1 and Bob's DLI ``detune`` [rad] off its fringe."""
    setup = cv.build(p)
    for name in ("DT", "DM0", "DM1"):
        setup[name].set(**detectors)
    setup["BOB-PS"].phase = setup["BOB-PS"].phase + detune
    return cv.theory(setup, p)


def readings() -> list[dict]:
    """E_p^u, its three parts and V for every row of ``SETTINGS``."""
    rows = []
    for label, p, detune, detectors in SETTINGS:
        th = expected(p, detune, **detectors)
        rows.append(dict(setting=label, E=th["E_p^u"], V=th["V"], **th["terms"]))
    return rows


def table(rows: list[dict]) -> str:
    """The README's table, in Markdown."""
    lines = [
        "| Setting | $E_p^u$ | `beta` | `M0` | `M1` | V |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        # rounded first, so that a V of −0.0004 reads 0.000
        cells = (f"{round(r[k], 3) + 0.0:.3f}" for k in ("E", "beta", "M0", "M1", "V"))
        lines.append(f"| {r['setting']} | {' | '.join(cells)} |")
    return "\n".join(lines)


def plot(rows: list[dict], path: str) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    text, muted, grid = "#f0efec", "#c3c2b7", "#2c2c2a"
    plt.style.use("dark_background")
    fig, axes = plt.subplots(
        1,
        4,
        figsize=(12.5, 5),
        sharey=True,
        gridspec_kw=dict(width_ratios=[1.7, 1, 1, 1]),
        constrained_layout=True,
    )
    y = range(len(rows))
    bar = dict(height=0.62, zorder=3)

    # E_p^u, stacked from its parts; a black edge keeps them apart
    whole, left = axes[0], [0.0] * len(rows)
    for key, color, _ in PARTS:
        part = [r[key] for r in rows]
        whole.barh(y, part, left=left, color=color, edgecolor="black", lw=1.5, **bar)
        left = [a + b for a, b in zip(left, part)]
    whole.axvline(0.5, color=muted, lw=1, ls=(0, (4, 3)), zorder=2)
    whole.text(0.51, -0.75, "½: no key beyond", color=muted, fontsize=9, va="center")
    whole.set_xlim(0, 1.15)
    whole.set_title(r"$E_p^u$ = beta + M0 + M1", loc="left", color=text, fontsize=11)
    whole.set_xlabel(r"$E_p^u$", color=muted)

    # each part on one scale, so that they compare across panels
    for ax, (key, color, title) in zip(axes[1:], PARTS):
        ax.barh(y, [r[key] for r in rows], color=color, **bar)
        ax.axvline(rows[0][key], color=muted, lw=1, ls=(0, (4, 3)), zorder=4)
        ax.text(rows[0][key], -0.75, " default", color=muted, fontsize=9, va="center")
        ax.set_xlim(0, 0.66)
        ax.set_title(title, loc="left", color=text, fontsize=11)
        ax.set_xlabel(r"part of $E_p^u$", color=muted)

    for ax in axes:
        ax.set_ylim(len(rows) - 0.5, -1.2)
        ax.grid(True, axis="x", color=grid, lw=0.8, zorder=0)
        ax.tick_params(colors=muted, length=0)
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color(grid)
    # the rows' names without what follows a comma: "Dark counts at 600 Hz"
    axes[0].set_yticks(y, [r["setting"].split(",")[0] for r in rows], color=text)
    axes[0].get_yticklabels()[0].set_fontweight("bold")
    fig.legend(
        handles=[Patch(color=c, label=k) for k, c, _ in PARTS],
        loc="outside upper right",
        ncol=3,
        frameon=False,
        labelcolor=text,
    )
    fig.suptitle(
        "COW with vacuum decoys, 25 km: what one wrong setting does to the bound\n"
        "expected readings from theory(), μ = 4·10⁻⁴ unless the row says otherwise",
        x=0.01,
        ha="left",
        color=text,
        fontsize=12,
    )
    fig.savefig(path, dpi=140)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the figure")
    args = ap.parse_args()
    rows = readings()
    print(table(rows))
    if args.plot:
        plot(rows, args.plot)
        print(f"saved {args.plot}")


if __name__ == "__main__":
    main()
