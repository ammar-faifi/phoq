"""Narrow linewidths, 1 kHz and 100 Hz, with a delayed self-homodyne and the RF spectrum analyzer.

    uv run python examples/linewidth_narrow.py                    # both lasers, a few delays each
    uv run python examples/linewidth_narrow.py --plot narrow.png  # also plot the 100 Hz fit
    uv run phoq serve examples/linewidth_narrow.py                # the 100 Hz bench in 3D

    laser → 50:50 ─┬─ fiber spool (long arm) ──────┬─ 50:50 → photodiode → spectrum analyzer
                   └─ phase shifter (short arm) ───┘

A delayed self-homodyne needs a delay of several coherence times τc = 1/(πΔν) to show the plain
Lorentzian (examples/linewidth_homodyne.py). For 1 kHz that is 150–400 km of fiber: one spool.
For 100 Hz it would be 1,500–3,900 km and 285–740 dB of loss, which no spool (500 km at most here)
and no bench without amplifiers can give. The way out is a delay *shorter* than τc and a fit of the
spectrum for any delay (README, "Laser linewidth by delayed self-homodyne"; Chen et al., Sensors 24,
3656 (2024), Eqs. (28)–(31)). That fit is the average over the interferometer's bias phase θ, so
spectra are taken at θ and θ + π/2 (the phase shifter) and averaged.

The short arm is not attenuated: the beat scales as √(P_short · P_long), and over hundreds of km of
fiber a balanced pair would sit below the analyzer's noise floor.
"""

import argparse
import math

import numpy as np

from phoq import Coupler, FiberSpool, Laser, PhaseShifter, Photodiode, Runner, Setup, SpectrumAnalyzer
from phoq.engine import trace
from phoq.units import fmt_si

LINEWIDTH = 100.0  # the laser `phoq serve` shows
SPOOL_KM = 500.0


def build(
    linewidth: float = LINEWIDTH,
    spool_km: float = SPOOL_KM,
    *,
    stop_freq: float | None = None,
    rbw: float | None = None,
    averages: int = 300,
) -> Setup:
    """The bench. The analyzer defaults to RBW = Δν/10 and a span of 20 Δν, widened to show at
    least five fringes (period 1/τd) when the delay is shorter than the coherence time."""
    setup = Setup("Narrow-linewidth self-homodyne", seed=7)
    laser = setup.add(Laser("LD-1", mode="cw", power_dbm=10.0, linewidth=linewidth))
    split, merge = setup.add(Coupler("BS-1"), Coupler("BS-2"))
    spool = setup.add(FiberSpool("SPL-1", length=spool_km * 1e3))
    shifter = setup.add(PhaseShifter("PS-1"))
    pd = setup.add(Photodiode("PD-1"))
    span = max(20 * linewidth, 5 / spool.delay)
    esa = setup.add(
        SpectrumAnalyzer(
            "ESA-1",
            stop_freq=stop_freq or span,
            rbw=rbw or linewidth / 10,
            averages=averages,
            detector="average",
            trace="average",
            ref_level=-40.0,
        )
    )
    setup.connect(laser.out, split.a1)
    setup.connect(split.b1, spool.p1)  # long arm
    setup.connect(spool.p2, merge.a1)
    setup.connect(split.b2, shifter.p1)  # short arm: sets the bias phase θ
    setup.connect(shifter.p2, merge.a2)
    setup.connect(merge.b1, pd.inp)
    setup.terminate_spares()  # every unused coupler port capped: open, a flat port reflects 3.6 %
    setup.connect(pd.rf, esa.inp)
    return setup


def arm_delay(setup: Setup) -> float:
    """τd: the delay difference between the two paths reaching the photodiode (spool and cords)."""
    a, b = trace(setup).delays(setup["PD-1"])
    return abs(a - b)


def measure(setup: Setup, seed: int = 1, phases=(0.0, math.pi / 2)) -> dict:
    """Spectra at each bias phase, averaged; the short-delay fit, the plain Lorentzian fit and the
    −3 dB point, plus what a single bias phase would have given."""
    runner, tau = Runner(setup, seed=seed), arm_delay(setup)
    single = runner.acquire("ESA-1")
    avg = runner.average_spectrum("ESA-1", "PS-1", "phase", phases)
    fit = avg.fit_self_homodyne(tau)
    return {
        "result": avg,
        "tau_d": tau,
        "fit": fit["linewidth"],
        "fringe_depth": fit["fringe_depth"],
        "lorentzian": avg.fit_lorentzian(0.0)["hwhm"],
        "minus3db": avg.width(3.0)[1],
        "one_phase": single.fit_self_homodyne(tau)["linewidth"],
    }


def report(linewidth: float, spools_km, averages: int) -> None:
    tau_c = 1 / (math.pi * linewidth)
    print(f"\n{fmt_si(linewidth, 'Hz')} laser, coherence time τc = {fmt_si(tau_c, 's')}")
    print(f"  {'spool':>7} {'loss':>6} {'τd/τc':>6} {'depth':>6} {'RBW':>7} {'span':>8}  {'short-delay fit':>15} {'one θ':>9} {'Lorentzian':>10} {'−3 dB':>9}")
    for km in spools_km:
        setup = build(linewidth, km, averages=averages)
        m = measure(setup)
        r, loss = m["result"], setup["SPL-1"].loss_db(1550e-9)
        print(
            f"  {km:5g} km {loss:4.0f} dB {m['tau_d'] / tau_c:6.2f} {m['fringe_depth']:6.2f} {fmt_si(r.rbw, 'Hz'):>7} {fmt_si(r.stop, 'Hz'):>8}"
            f"  {fmt_si(m['fit'], 'Hz'):>15} {fmt_si(m['one_phase'], 'Hz'):>9} {fmt_si(m['lorentzian'], 'Hz'):>10} {fmt_si(m['minus3db'], 'Hz'):>9}"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the 100 Hz, 500 km spectrum with its fit")
    args = ap.parse_args()

    print("Short-delay fit = Eqs. (28)–(31) on spectra averaged at θ and θ + π/2; 'one θ' = the same fit on")
    print("a single bias phase; Lorentzian and −3 dB assume a delay much longer than τc.")
    report(1e3, (100, 200, 400), averages=500)
    report(100.0, (50, 100, 200, 500), averages=300)
    for km in (1500, 3900):
        loss = FiberSpool(length=500e3).loss_db(1550e-9) * km / 500
        print(f"  (the long-delay route at 100 Hz: {km} km = {km * 1e3 * FiberSpool(length=1).delay / (1 / (math.pi * 100)):.1f} τc, {loss:.0f} dB of fiber loss)")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        setup = build(100.0, 500)
        m = measure(setup)
        r, tau = m["result"], m["tau_d"]
        p = r.fit_self_homodyne(tau)
        f, psd = r.psd()
        keep = f > 3 * r.rbw
        u = f[keep]
        dnu = p["linewidth"]
        s2 = 1 - math.exp(-2 * math.pi * dnu * tau) * (np.cos(2 * math.pi * u * tau) + 2 * math.pi * dnu * tau * np.sinc(2 * u * tau))
        model = p["amplitude"] * dnu / (dnu**2 + u**2) * s2 + p["floor_psd"]
        lor = r.fit_lorentzian(0.0)
        fig, ax = plt.subplots(figsize=(7.5, 4))
        to_dbm = lambda w: 10 * np.log10(w * r.rbw / 1e-3)  # noqa: E731
        ax.plot(u, to_dbm(psd[keep]), lw=0.8, color="#d4a514", label="analyzer, θ and θ + π/2 averaged")
        ax.plot(u, to_dbm(model), "w--", lw=1, label=f"Eqs. (28)–(31) fit: Δν = {fmt_si(dnu, 'Hz')}")
        ax.plot(u, to_dbm(lor["peak_psd"] / (1 + (u / lor["hwhm"]) ** 2) + lor["floor_psd"]), ":", color="#60a5fa", lw=1.2,
                label=f"Lorentzian fit (assumes τd ≫ τc): {fmt_si(lor['hwhm'], 'Hz')}")
        ax.set(xlabel="frequency [Hz]", ylabel=f"level [dBm in {fmt_si(r.rbw, 'Hz')}]",
               title=f"100 Hz laser, 500 km delay (τd = {tau / (1 / (math.pi * 100)):.2f} τc)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
