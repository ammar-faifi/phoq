"""Pulsed laser → attenuators → SPAD → spectrum analyzer: the clock's tone in the clicks.

uv run phoq run examples/spad_tones.py                   # print both readings
uv run phoq run examples/spad_tones.py --plot tones.png  # and plot both traces
uv run phoq serve examples/spad_tones.py                 # the bench in 3D

A SPAD clicks only where the pulses are, so its click train has a line at the pulse
clock. How the line looks depends on W, how long a live detector waits for its next
click, against its hold-off τd. With W ≈ 4 τd there is one line. With W ≪ τd the
detector clicks almost every τd, and that rhythm puts a copy of the line every
1/(τd + W) on each side of it, nearly as tall as the line itself.

The analyzer sees the detector's electrical output, which in phoq is a 5 ns rectangle
per click. Its spectrum has nulls at multiples of 200 MHz, so ``rate=1e9`` shows nothing:
that limit is the pulse's, not the clicks'. Time stamps have no such null.
"""

import argparse
import math

import numpy as np

from phoq import (
    Attenuator,
    Laser,
    Runner,
    Setup,
    SinglePhotonDetector,
    SpectrumAnalyzer,
)
from phoq.engine import trace
from phoq.units import fmt_si

RATE = 100e6  # pulse clock [Hz]
PULSE = 100e-12  # laser pulse, FWHM [s]
HOLD_OFF = 40e-6  # the SPAD's dead time [s]
QUIET = 2.5e-4  # photons per pulse at the SPAD: W ≈ 4 τd
BRIGHT = 0.1  # … and saturated: W ≈ τd / 100
SPAN = 130e3  # the analyzer's view on each side of the clock [Hz]
RBW = 1e3  # [Hz]
SWEEPS = 100  # averaged: enough for a converged trace


def build(
    rate: float = RATE,
    mu: float = QUIET,
    hold_off: float = HOLD_OFF,
    pulse: float = PULSE,
) -> Setup:
    """The bench, with the VOA set so that a pulse holds ``mu`` photons at the SPAD."""
    laser = Laser(
        "LD",
        mode="pulsed",
        wavelength=1550e-9,
        power_dbm=-10.0,
        rep_rate=rate,
        pulse_width=pulse,
    )
    att = Attenuator("ATT", attenuation_db=40.0)
    voa = Attenuator("VOA", attenuation_db=40.0)
    spd = SinglePhotonDetector("SPD", preset="mpd/pdm-ir", dead_time=hold_off)
    esa = SpectrumAnalyzer(
        "ESA",
        start_freq=rate - SPAN,
        stop_freq=rate + SPAN,
        rbw=RBW,
        points=1601,
        detector="rms",
        trace="average",
        sweep_count=SWEEPS,
    )
    bench = Setup.from_chain(
        laser, att, voa, spd, esa, name="Clicks on a spectrum analyzer", seed=1
    )
    gain = sum(abs(q.amp) ** 2 for q in trace(bench).paths[spd] if q.source is laser)
    voa.attenuation_db = voa.attenuation_db + 10 * math.log10(
        laser.mean_photon_number * gain / mu
    )
    return bench


def wait(bench: Setup) -> float:
    """W: a live detector's mean wait for its next click, from the VOA's setting [s]."""
    laser, spd = bench["LD"], bench["SPD"]
    gain = sum(abs(q.amp) ** 2 for q in trace(bench).paths[spd] if q.source is laser)
    mu = laser.mean_photon_number * gain
    return 1 / (spd.efficiency_at(laser.wavelength) * mu * laser.rep_rate)


def measure(mu: float, sweeps: int) -> dict:
    """Average ``sweeps`` sweeps and read the line, the pedestal and the tallest other
    peak, more than 5 kHz from the line."""
    bench = build(mu=mu)
    runner = Runner(bench, seed=1)
    for _ in range(sweeps):
        res = runner.acquire("ESA")
    offset = res.freq - bench["LD"].rep_rate
    other = np.abs(offset) > 5e3
    k = np.flatnonzero(other)[np.argmax(res.level[other])]
    return {
        "wait": wait(bench),
        "offset": offset,
        "level": res.level,
        "line": res.level[np.abs(offset) < 2 * res.rbw].max(),
        "pedestal": np.median(res.level),
        "runner-up": res.level[k],
        "runner-up at": offset[k],
    }


def plot(readings: list[dict], path: str) -> None:
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    fig, axes = plt.subplots(
        1, 2, figsize=(11, 4.2), sharey=True, constrained_layout=True
    )
    for ax, r in zip(axes, readings):
        ax.plot(r["offset"] / 1e3, r["level"], color="#3987e5", lw=1.4)
        ax.set_title(f"Wait between clicks {fmt_si(r['wait'], 's')}", loc="left")
        ax.set_xlabel(f"Offset from the {fmt_si(RATE, 'Hz')} clock [kHz]")
        ax.grid(True, color="#2c2c2a", lw=0.8)
    axes[0].set_ylabel(f"Power in {fmt_si(RBW, 'Hz')} RBW [dBm]")
    fig.savefig(path, dpi=170)


# what ``phoq serve`` puts on the table
setup = build()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sweeps", type=int, default=SWEEPS, help="sweeps to average")
    ap.add_argument("--plot", metavar="PNG", help="save both traces")
    args = ap.parse_args()
    readings = [measure(mu, args.sweeps) for mu in (QUIET, BRIGHT)]
    for r in readings:
        print(
            f"wait {fmt_si(r['wait'], 's')}, hold-off {fmt_si(HOLD_OFF, 's')}: line"
            f" {r['line']:.1f} dBm, pedestal {r['pedestal']:.1f} dBm, tallest other"
            f" peak {r['runner-up']:.1f} dBm at {r['runner-up at'] / 1e3:+.1f} kHz"
        )
    if args.plot:
        plot(readings, args.plot)
