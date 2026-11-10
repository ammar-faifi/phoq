"""Pulsed laser → attenuators → SPAD → spectrum analyzer from DC to 1.25 GHz: the comb.

uv run phoq run examples/spad_comb.py                  # print the lines
uv run phoq run examples/spad_comb.py --plot comb.png  # and plot both traces
uv run phoq serve examples/spad_comb.py                # the bench in 3D

The clicks repeat at the laser's clock, so their spectrum is a comb with a line at every
multiple of it: the repetition spectrum. The analyzer does not see clicks but phoq's
5 ns electrical pulse for each, so it shows that comb times the pulse's spectrum,
sin(πfw)/(πfw) with w = 5 ns. The lines at odd multiples of 100 MHz follow that envelope
down, and every multiple of 200 MHz, where it is zero, is gone.

The randomly placed clicks also leave a pedestal, which the envelope scales the same way,
so a line stands above its own stretch of pedestal by nearly the same margin at any
frequency. The SPAD's timing jitter takes exp(−(2πfσ)²) off the lines alone: for the
preset's 200 ps FWHM, 0.01 dB at 100 MHz and 1.5 dB at 1.1 GHz. The margin grows with
the click rate against the RBW. At the preset's shortest hold-off, 1 µs, and a 4 µs wait,
the SPAD counts 200 kHz and every line stands about 10 dB clear, 8.4 dB at 1.1 GHz.
At a 40 µs hold-off it counts 5 kHz, and at this RBW the comb drowns. A narrower RBW
digs one line out, as examples/spad_tones.py does.
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

RATE = 100e6  # laser repetition rate [Hz]
PULSE = 100e-12  # laser pulse, FWHM [s]
HOLD_OFF = 1e-6  # the SPAD's dead time: the preset allows 1 µs to 3 ms
WAIT = 4e-6  # a live SPAD's mean wait for its next click [s]: sets the light
STOP = 1.25e9  # the analyzer from DC to here [Hz]
RBW = 3e3  # [Hz]
SWEEPS = 20


def build(rate: float = RATE, hold_off: float = HOLD_OFF, wait: float = WAIT) -> Setup:
    """The bench, with the VOA set so that a live SPAD waits ``wait`` for a click."""
    laser = Laser(
        "LD",
        mode="pulsed",
        wavelength=1550e-9,
        power_dbm=-10.0,
        rep_rate=rate,
        pulse_width=PULSE,
    )
    att = Attenuator("ATT", attenuation_db=30.0)
    voa = Attenuator("VOA", attenuation_db=30.0)
    spd = SinglePhotonDetector("SPD", preset="mpd/pdm-ir", dead_time=hold_off)
    esa = SpectrumAnalyzer(
        "ESA",
        start_freq=0.0,
        stop_freq=STOP,
        rbw=RBW,
        points=2001,
        detector="peak",
        trace="average",
        sweep_count=SWEEPS,
    )
    bench = Setup.from_chain(
        laser, att, voa, spd, esa, name="The comb of a SPAD", seed=1
    )
    gain = sum(abs(q.amp) ** 2 for q in trace(bench).paths[spd] if q.source is laser)
    # photons per pulse at the SPAD for that wait: 1 / (η μ f) = wait
    mu = 1 / (spd.efficiency_at(laser.wavelength) * wait * rate)
    voa.attenuation_db = voa.attenuation_db + 10 * math.log10(
        laser.mean_photon_number * gain / mu
    )
    return bench


def measure(hold_off: float, wait: float, sweeps: int) -> dict:
    """Average ``sweeps`` sweeps; read each multiple of the clock, and the pedestal
    next to it (5 to 25 MHz away on either side)."""
    bench = build(hold_off=hold_off, wait=wait)
    runner = Runner(bench, seed=1)
    for _ in range(sweeps):
        res = runner.acquire("ESA")
    f, level = res.freq, res.level
    n = np.arange(1, int(STOP // RATE) + 1)
    lines = [level[max(k - 1, 0) : k + 2].max() for k in np.searchsorted(f, n * RATE)]
    beside = [(np.abs(f - m * RATE) > 5e6) & (np.abs(f - m * RATE) < 25e6) for m in n]
    return {
        "hold_off": hold_off,
        "rate": 1 / (hold_off + wait),
        "f": f,
        "level": level,
        "n": n,
        "lines": np.array(lines),
        "pedestals": np.array([np.median(level[b]) for b in beside]),
    }


def plot(readings: list[dict], path: str) -> None:
    import matplotlib.pyplot as plt

    plt.style.use("dark_background")
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, constrained_layout=True
    )
    for ax, r in zip(axes, readings):
        f = r["f"] / 1e6
        ax.plot(f, r["level"], color="#3987e5", lw=1.0)
        # the 5 ns pulse's envelope, through the first line
        env = 20 * np.log10(np.maximum(np.abs(np.sinc(r["f"] * 5e-9)), 1e-6))
        ref = r["lines"][0] - 20 * math.log10(abs(np.sinc(RATE * 5e-9)))
        ax.plot(f, ref + env, color="#d95926", lw=1.0)
        ax.set_ylim(np.median(r["pedestals"]) - 15, r["lines"][0] + 5)
        ax.set_title(
            f"Hold-off {fmt_si(r['hold_off'], 's')}: {fmt_si(r['rate'], 'Hz')} of"
            " clicks (orange: the 5 ns pulse's envelope)",
            loc="left",
        )
        ax.set_ylabel(f"dBm in {fmt_si(RBW, 'Hz')}")
        ax.grid(True, color="#2c2c2a", lw=0.8)
    axes[-1].set_xlabel("Frequency [MHz]")
    fig.savefig(path, dpi=170)


# what ``phoq serve`` puts on the table
setup = build()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sweeps", type=int, default=SWEEPS, help="sweeps to average")
    ap.add_argument("--plot", metavar="PNG", help="save both traces")
    args = ap.parse_args()
    readings = [
        measure(HOLD_OFF, WAIT, args.sweeps),
        measure(40e-6, 160e-6, args.sweeps),
    ]
    for r in readings:
        print(
            f"hold-off {fmt_si(r['hold_off'], 's')}, {fmt_si(r['rate'], 'Hz')} of"
            " clicks: each multiple of the clock"
        )
        for n, level, ped in zip(r["n"], r["lines"], r["pedestals"]):
            print(
                f"  {fmt_si(n * RATE, 'Hz'):>9}: {level:6.1f} dBm, pedestal beside it"
                f" {ped:6.1f} dBm: {level - ped:+5.1f} dB"
            )
    if args.plot:
        plot(readings, args.plot)
