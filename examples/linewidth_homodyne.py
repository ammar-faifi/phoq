"""Measure a laser's linewidth with a delayed self-homodyne and the RF spectrum analyzer.

    uv run python examples/linewidth_homodyne.py                  # measure, then sweep the delay
    uv run python examples/linewidth_homodyne.py --plot dsh.png   # also save the spectrum and fit
    uv run phoq serve examples/linewidth_homodyne.py              # the bench in 3D, averaging live

    laser → 50:50 ─┬─ 10 km spool (τd = 49 µs) ───────────┬─ 50:50 → photodiode → spectrum analyzer
                   └─ attenuator (balances the two arms) ─┘

With τd ≫ τc = 1/(πΔν) the two arms reach the coupler mutually incoherent, and the photocurrent's
RF spectrum is a Lorentzian at 0 Hz whose half width at half maximum is the laser linewidth Δν
(README, "Laser linewidth by delayed self-homodyne").
"""

import argparse
import math

import numpy as np

from phoq import Attenuator, Coupler, FiberSpool, Laser, Photodiode, Setup, SpectrumAnalyzer
from phoq.engine import trace
from phoq.units import fmt_si

LINEWIDTH = 100e3  # what the measurement should find


def build(linewidth: float = LINEWIDTH, spool_m: float = 10e3, averages: int = 200) -> Setup:
    setup = Setup("Delayed self-homodyne", seed=7)
    laser = setup.add(Laser("LD-1", mode="cw", power_dbm=0.0, linewidth=linewidth))
    split, merge = setup.add(Coupler("BS-1"), Coupler("BS-2"))
    spool = setup.add(FiberSpool("SPL-1", length=spool_m))
    # the short arm gets the spool's loss, so both arms bring the same power: the largest beat
    voa_il = Attenuator.insertion_loss_db.default
    voa = setup.add(Attenuator("VOA-1", attenuation_db=max(0.0, spool.loss_db(laser.wavelength) - voa_il)))
    pd = setup.add(Photodiode("PD-1"))
    esa = setup.add(
        SpectrumAnalyzer(
            "ESA-1",
            stop_freq=2e6,  # ≥ 20 Δν: the wings and the floor
            rbw=5e3,  # ≤ Δν / 10: the RBW does not widen the line
            averages=averages,
            detector="average",  # the beat is noise, not a tone
            trace="average",  # in the lab, sweeps keep averaging into the trace
            ref_level=-30.0,
        )
    )
    setup.connect(laser.out, split.a1)
    setup.connect(split.b1, spool.p1)  # long arm
    setup.connect(spool.p2, merge.a1)
    setup.connect(split.b2, voa.p1)  # short arm
    setup.connect(voa.p2, merge.a2)
    setup.connect(merge.b1, pd.inp)
    setup.connect(pd.rf, esa.inp)
    return setup


def measure(setup: Setup, seed: int = 1):
    """One sweep; the linewidth from a Lorentzian fit, the −3 dB point and the −20 dB point."""
    esa = setup["ESA-1"]
    esa.trace = "write"
    r = esa.acquire(trace(setup), np.random.default_rng(seed))
    return r, r.fit_lorentzian(0.0)["hwhm"], r.width(3.0)[1], r.width(20.0)[1] / math.sqrt(99)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the spectrum with its Lorentzian fit")
    args = ap.parse_args()

    setup = build(averages=2000)
    laser, spool = setup["LD-1"], setup["SPL-1"]
    tau_c, tau_d = 1 / (math.pi * laser.linewidth), spool.delay
    print(
        f"laser linewidth {fmt_si(laser.linewidth, 'Hz')} (coherence time {fmt_si(tau_c, 's')}); "
        f"delay {fmt_si(tau_d, 's')} = {tau_d / tau_c:.0f} τc, coherence left "
        f"e^(−2πΔν·τd) = {math.exp(-2 * math.pi * laser.linewidth * tau_d):.1e}\n"
    )
    r, fit, w3, w20 = measure(setup)
    print(r.summary(), "\n")
    for name, value in (("Lorentzian fit", fit), ("−3 dB point", w3), ("−20 dB point / √99", w20)):
        print(f"  {name:20} Δν = {fmt_si(value, 'Hz'):>9}  ({100 * (value / laser.linewidth - 1):+.1f} %)")

    print("\nThe delay must beat the coherence time (1000 averages each):")
    print(f"  {'spool':>7} {'τd':>9} {'τd/τc':>6} {'coherence':>9} {'fit':>10} {'−3 dB':>10}")

    for km in (0.3, 0.6, 1, 2, 5, 10, 25, 50, 100, 150, 200, 250, 300):
        s = build(spool_m=km * 1e3, averages=1000)
        _, f_fit, f3, _ = measure(s)
        td = s["SPL-1"].delay
        coh = math.exp(-2 * math.pi * LINEWIDTH * td)
        print(f"  {km:5g} km {fmt_si(td, 's'):>9} {td / tau_c:6.1f} {coh:9.2g} {fmt_si(f_fit, 'Hz'):>10} {fmt_si(f3, 'Hz'):>10}")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        f, psd = r.psd()
        keep = f > 3 * r.rbw
        fig, ax = plt.subplots(figsize=(7.5, 4))
        ax.plot(f[keep] / 1e3, 10 * np.log10(psd[keep] * r.rbw / 1e-3), lw=0.8, color="#d4a514", label="analyzer")
        p = r.fit_lorentzian(0.0)
        model = p["peak_psd"] / (1 + (f[keep] / p["hwhm"]) ** 2) + p["floor_psd"]
        ax.plot(f[keep] / 1e3, 10 * np.log10(model * r.rbw / 1e-3), "w--", lw=1, label=f"Lorentzian fit, Δν = {fmt_si(p['hwhm'], 'Hz')}")
        ax.axvline(laser.linewidth / 1e3, color="#60a5fa", lw=0.8, label=f"laser setting {fmt_si(laser.linewidth, 'Hz')}")
        ax.set(xlabel="frequency [kHz]", ylabel=f"level [dBm in {fmt_si(r.rbw, 'Hz')}]", title="Delayed self-homodyne, 10 km")
        ax.grid(alpha=0.3, which="both")
        ax.legend()
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
