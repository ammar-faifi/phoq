"""A delay-line interferometer as a DPSK demodulator, the receiver of DPS-QKD.

    # decode a 2 µs sweep at three linewidths
    uv run phoq run examples/dli_for_dpsk.py
    # also save the scope view
    uv run phoq run examples/dli_for_dpsk.py --plot dpsk.png
    # the bench in 3D
    uv run phoq serve examples/dli_for_dpsk.py

A phase modulator writes φ_k = φ_{k−1} + π·b_k on a 100 MHz pulse train
(differential encoding). The DLI (examples/dli.py) overlaps pulse k with pulse k−1, so
equal neighbors (b_k = 0) leave by the constructive port and a π step (b_k = 1) by the
destructive one: the bit comes out without any phase reference at the receiver. How
cleanly depends on the coherence between neighboring pulses, e^{−πΔν·T}: the decoded
contrast follows the laser's linewidth.

Scope CH1 is the constructive port (PD-C), CH2 the destructive port (PD-D), CH3 the bits
(the generator's second channel, delayed into the slot that decodes them). In the lab,
turn DLI-PS's phase to swap the outputs, raise LD-1's linewidth to 10 MHz to watch the
neighboring pulses lose their phase relation, or tick "Random phase per pulse".
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np

from phoq import (
    ArbitraryWaveformGenerator,
    Laser,
    Oscilloscope,
    PhaseModulator,
    Photodiode,
    Runner,
    Setup,
)
from phoq.units import FIBER_DELAY, fmt_si

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dli import (
    RATE,
    balance,
    dli,
    lock,
    slot_delay,
)  # noqa: E402  (the shared interferometer)

N_BITS = 32  # pattern length (even parity, so the differential phase closes on itself)


def dpsk_bits(n: int = N_BITS, seed: int = 11) -> np.ndarray:
    bits = np.random.default_rng(seed).integers(0, 2, n)
    # even parity: the pattern repeats without a phase step at the seam
    if bits.sum() % 2:
        bits[-1] ^= 1
    return bits


def build(linewidth: float = 100e3, bits: np.ndarray | None = None) -> Setup:
    """Pulsed laser → phase modulator (FG-1 CH1) → DLI → PD-C, PD-D → scope; FG-1 CH2 →
    scope CH3."""
    setup = Setup("Delay-line interferometer: DPSK demodulator", seed=5)
    laser = setup.add(
        Laser(
            "LD-1",
            mode="pulsed",
            rep_rate=RATE,
            pulse_width=1e-9,
            power_dbm=-3.0,
            linewidth=linewidth,
        )
    )
    pm = setup.add(PhaseModulator("PM-1"))
    fg = setup.add(ArbitraryWaveformGenerator("FG-1"))
    pd_c, pd_d = setup.add(
        Photodiode("PD-C", bandwidth=5e9), Photodiode("PD-D", bandwidth=5e9)
    )
    osc = setup.add(
        Oscilloscope(
            "OSC-1",
            timebase=20e-9,
            ch1_scale=500e-6,
            ch2_scale=500e-6,
            ch3_scale=0.5,
            ch1_offset=-1.0,
            ch2_offset=-3.5,
            ch3_offset=1.5,
        )
    )
    setup.connect(laser.out, pm.p1)
    bs_in, bs_out, _, _ = dli(setup, "DLI")
    setup.connect(pm.p2, bs_in.a1)
    setup.connect(bs_out.b1, pd_c.inp)
    setup.connect(bs_out.b2, pd_d.inp)
    setup.connect(fg.ch1, pm.rf)
    setup.connect(pd_c.rf, osc.ch1)
    setup.connect(pd_d.rf, osc.ch2)
    setup.connect(fg.ch2, osc.ch3)
    setup.terminate_spares()  # the DLI's spare input

    bits = dpsk_bits() if bits is None else np.asarray(bits)
    # differential encoding: φ_k = φ_{k−1} + π b_k
    phase = math.pi * (np.cumsum(bits) % 2)
    # aligned: symbol k meets pulse k
    fg.set_pattern("ch1", pm.voltage_for(phase), rate=RATE)
    fg.set_pattern("ch2", bits.astype(float), rate=RATE)  # the bits, for the eye
    balance(setup, "LD-1", "PD-C", "DLI-ODL")
    lock(setup, "LD-1", "PD-C", "DLI-PS")
    # CH3 shows b_k in the slot that decodes it
    fg.ch2_delay = slot_delay(setup, "LD-1", "PD-C")
    return setup


def decode(setup: Setup, seed: int = 1, timebase: float | None = None) -> dict:
    """One scope sweep: the energy each port gets in each slot, the decoded bits, the
    errors. ``timebase`` widens the sweep (200 ns/div: about 200 slots) without changing
    the lab's view.
    """
    osc = setup["OSC-1"]
    keep = osc.timebase
    if timebase:
        osc.timebase = timebase
    try:
        res = Runner(setup, seed=seed).acquire("OSC-1")
    finally:
        osc.timebase = keep
    t, dt = res.t, res.dt
    d = slot_delay(setup, "LD-1", "PD-C")
    # The bits come from CH3 (the generator's copy, delayed into the decoding slot).
    c, dd = res["ch1"].data, res["ch2"].data
    out = []
    for k in range(-2, int((t[-1] - t[0]) * RATE) + 2):
        center = k / RATE + d  # slot k after the trigger pulse, through the short arm
        sel = np.abs(t - center) < 0.4 / RATE
        if not sel.any() or t[0] > center - 0.4 / RATE or t[-1] < center + 0.4 / RATE:
            continue
        out.append(
            (
                k,
                float(c[sel].sum() * dt),
                float(dd[sel].sum() * dt),
                float(np.median(res["ch3"].data[sel])),
            )
        )
    k, e_c, e_d, marker = (np.array(v) for v in zip(*out))
    decided = (e_d > e_c).astype(int)
    expected = (marker > 0.5).astype(int)
    # +1: right port only
    contrast = (e_c - e_d) / (e_c + e_d) * np.where(expected == 1, -1.0, 1.0)
    return {
        "result": res,
        "slots": k.size,
        "errors": int(np.sum(decided != expected)),
        "decided": decided,
        "expected": expected,
        "contrast": float(np.mean(contrast)),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the scope view")
    args = ap.parse_args()

    T = 1 / RATE
    odl = build()["DLI-ODL"]
    print(
        f"DLI: cords 3 m (long arm) − 1 m (short arm) = {fmt_si(2 * FIBER_DELAY, 's')};"
        f" delay line {fmt_si(odl.insertion_delay, 's')} + {fmt_si(odl.delay, 's')} on"
        f" the dial → one slot T = {fmt_si(T, 's')}\n"
    )
    print("DPSK demodulation (photodiodes, one 2 µs scope sweep)")
    for lw in (100e3, 10e6, 100e6):
        r = decode(build(linewidth=lw), timebase=200e-9)
        print(
            f"   linewidth {fmt_si(lw, 'Hz'):>8}: {r['slots']} slots decoded,"
            f" {r['errors']} errors, mean contrast {r['contrast']:+.3f}  (e^(−πΔνT) ="
            f" {math.exp(-math.pi * lw * T):.3f})"
        )

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        res = decode(build())["result"]
        fig, axes = plt.subplots(3, 1, figsize=(8, 5.4), sharex=True)
        t = res.t * 1e9
        for ax, ch, label, scale in zip(
            axes,
            ("ch1", "ch2", "ch3"),
            ("constructive port [mA]", "destructive port [mA]", "bit b_k [V]"),
            (1e3, 1e3, 1.0),
        ):
            ax.plot(
                t,
                res[ch].data * scale,
                color={"ch1": "#d4a514", "ch2": "#0e9fbf", "ch3": "#d6409f"}[ch],
                lw=1.2,
            )
            ax.set_ylabel(label, fontsize=8)
            ax.grid(alpha=0.3)
        axes[0].set_title(
            "DPSK through a delay-line interferometer: the bit leaves by the"
            " destructive port"
        )
        axes[-1].set_xlabel("time from trigger [ns]")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
