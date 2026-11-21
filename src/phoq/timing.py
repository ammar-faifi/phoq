"""A station's clock: its time tagger's stamps, folded onto the slots of a protocol.

A time tagger stamps every click on its own clock and knows nothing of where Alice's
pulses fall. The station's software holds her clock, slot n centered on t₀ + n·τ, and
folds each stamp onto it: t = t₀ + n·τ + φ. The slot n says which of Alice's pulses a
click belongs to, and the phase φ where in the slot it fell, which is what a click
window cuts on. That is how a bench reads its tagger, and how a protocol here reads its
own, rather than through the counter's record of bins (``CounterResult.clicks``), which
only a simulator has.

τ is the agreed clock period, or one recovered from the clicks. t₀, where the tagger
stamps the center of the session's first pulse, is found once per session.
"""

from __future__ import annotations

import numpy as np

from .units import fmt_si


def fold(t, t0: float, period: float) -> tuple[np.ndarray, np.ndarray]:
    """The slot n and the phase φ, in [−period/2, period/2), of each time stamp:
    t = t0 + n·period + φ, slot n centered on t0 + n·period."""
    x = (np.asarray(t, dtype=float) - t0) / period
    n = np.floor(x + 0.5)
    return n.astype(np.int64), (x - n) * period


def clicked_slots(
    t, t0: float, period: float, slots: int, window: tuple[float, float] | None = None
) -> np.ndarray:
    """Which of ``slots`` slots, slot n centered on t0 + n·period, hold a time stamp.
    With ``window`` = (lo, hi), only the stamps whose phase lies in lo ≤ φ < hi count:
    the station's click window. Stamps outside slots 0 … slots − 1 belong to another
    block."""
    n, phase = fold(t, t0, period)
    if window is not None:
        n = n[(phase >= window[0]) & (phase < window[1])]
    out = np.zeros(slots, dtype=bool)
    out[n[(n >= 0) & (n < slots)]] = True
    return out


def plot_sync(
    t,
    before: tuple[float, float],
    after: tuple[float, float],
    window: tuple[float, float] | None = None,
    axes=None,
):
    """Each time stamp's phase in its slot against time, before and after the station
    found Alice's clock, side by side: folded as by :func:`fold` on ``before`` = (t0,
    period), what the station starts with (its tagger's time zero and the agreed
    period, say), and on ``after``, the clock it recovered. On a period off hers by a
    fraction ε the phase walks through the slot ε·T/period times in a record T; on her
    clock the stamps lie flat, in a band as wide as her pulses. ``window`` = (lo, hi),
    a click window as in :func:`clicked_slots`, is shaded on the second panel, with the
    share of the stamps inside it. Draws on two ``axes``, or on a new figure, in the
    dark_background style; returns the two axes."""
    import matplotlib.pyplot as plt

    t = np.asarray(t, dtype=float)
    clocks = (before, after)
    phases = [fold(t, t0, period)[1] for t0, period in clocks]
    shared = axes is None  # a new figure shares its y axis: one label
    with plt.style.context("dark_background"):
        if shared:
            axes = plt.subplots(1, 2, figsize=(10, 3.6), sharex=True, sharey=True)[1]
        ts, tu = _unit(float(np.abs(t).max()) if t.size else 1.0)
        for ax, (t0, period), phase, name in zip(
            axes, clocks, phases, ("before", "after")
        ):
            ps, pu = _unit(period / 2)
            ax.plot(
                t / ts,
                phase / ps,
                ".",
                ms=2.5,
                alpha=0.8,
                color="#60a5fa",
                rasterized=True,
            )
            ax.set(
                title=f"{name} sync: {fmt_si(1 / period, 'Hz', 9)},"
                f" t₀ = {fmt_si(t0, 's', 4)}",
                xlabel=f"time stamp [{tu}]",
                ylabel=f"phase φ in the slot [{pu}]",
                ylim=(-period / 2 / ps, period / 2 / ps),
            )
        if shared:
            axes[1].set_ylabel("")
        if window is not None:
            lo, hi = window
            ps = _unit(after[1] / 2)[0]
            inside = (phases[1] >= lo) & (phases[1] < hi)
            share = float(inside.mean()) if t.size else 0.0
            axes[1].axhspan(
                lo / ps,
                hi / ps,
                color="#d4a514",
                alpha=0.2,
                lw=0,
                label=f"click window: {share * 100:.1f} % of the stamps",
            )
            axes[1].legend(loc="upper right", fontsize=8)
    return axes


def _unit(x: float) -> tuple[float, str]:
    """The largest of s, ms, µs and ns not above ``x``, as (scale, name); ps below."""
    units = ((1.0, "s"), (1e-3, "ms"), (1e-6, "µs"), (1e-9, "ns"))
    return next(((s, u) for s, u in units if x >= s), (1e-12, "ps"))
