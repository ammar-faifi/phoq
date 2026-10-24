"""Laser intensity noise and shot noise, measured with a balanced detector and a spectrum analyzer.

    uv run python examples/laser_noise.py                    # dark, one input, both inputs, the fits
    uv run python examples/laser_noise.py --plot noise.png   # noise against photocurrent, log–log
    uv run phoq serve examples/laser_noise.py                # the bench in 3D

    laser (RIN −135 dB/Hz) → VOA → 50:50 ─┬─ 1 m → INPUT+    BPD-1 (PDB450C, 10⁵ V/A)
                                          └─ 1 m → INPUT−    RF OUTPUT → spectrum analyzer

The analyzer reads the noise PSD of RF OUTPUT. Divided by the gain and the detector's pole, that is
the noise S_I of the photocurrent the amplifier sees, in A²/Hz, which this script averages over
0.2–1 MHz. Three measurements, as on a bench:

1. Dark (laser off): the amplifier's own noise, (NEP·ℜ)² = 2.4·10⁻²⁴ A²/Hz at this gain.
2. One input (INPUT− unplugged): the balanced detector as a low-noise amplified photodetector.
   Above the dark noise, S_I = 2qI + RIN·I². The shot noise grows like I and the laser's intensity
   noise like I², so they cross at I = 2q/RIN, 10 µA for −135 dB/Hz. A fit of the two terms gives the
   electron charge and the RIN. (One input must stay under the CW saturation, 46 µA at this gain.)
3. Both inputs: the difference of two equal photocurrents. The laser's intensity noise reaches both
   diodes alike and cancels, down to the CMRR residue. The shot noises of the two halves are
   uncorrelated (RP Photonics, "Balanced photodetection") and add: S_I = 2q(I₊ + I₋). So the
   difference measures the shot-noise level of the light, at currents where one input sees mostly
   laser noise. That is the "shot-noise reference" of a balanced homodyne detector.

Phase noise plays no part: the detectors measure intensity. The laser's linewidth can be anything.
"""

import argparse
import math

import numpy as np

from phoq import (
    Attenuator,
    BalancedPhotodetector,
    Coupler,
    Laser,
    Setup,
    SpectrumAnalyzer,
)
from phoq.engine import trace
from phoq.units import Q_E, fmt_si, photon_energy

RIN = (
    -135.0
)  # dB/Hz: the laser's classical relative intensity noise (white), a noisy diode laser
GAIN = 1e5  # V/A: 4 MHz of bandwidth, and an amplifier noise 21 dB under the shot noise of 1.4 mA
BAND = (0.2e6, 1.0e6)  # where the noise is read: flat, and well inside the 4 MHz pole
ONE_INPUT = (
    13.0,
    15.0,
    17.0,
    19.0,
    21.0,
    23.0,
    25.0,
)  # VOA [dB]: INPUT+ from 28 µA down to 1.8 µA
BOTH_INPUTS = (
    0.0,
    4.0,
    8.0,
    12.0,
    16.0,
    20.0,
    24.0,
)  # VOA [dB]: 1.2 mA down to 5 µA in all


def build(
    attenuation_db: float = 10.0,
    inputs: int = 2,
    rin: float = RIN,
    laser_on: bool = True,
) -> Setup:
    """Laser → VOA → 50:50 coupler → balanced detector → spectrum analyzer, the coupler's spare
    input capped. ``inputs=1`` caps the second output and leaves INPUT− unplugged, as when one arm
    is blocked."""
    setup = Setup("Laser intensity noise and shot noise", seed=3)
    laser = setup.add(
        Laser("LD-1", mode="cw", power_dbm=3.0, rin_db_hz=rin, enabled=laser_on)
    )
    voa = setup.add(Attenuator("VOA-1", attenuation_db=attenuation_db))
    bs = setup.add(Coupler("BS-1"))
    bpd = setup.add(
        BalancedPhotodetector("BPD-1", preset="thorlabs/pdb450c", gain=GAIN)
    )
    esa = setup.add(
        SpectrumAnalyzer(
            "ESA-1",
            start_freq=0.0,
            stop_freq=2e6,
            rbw=10e3,
            averages=40,
            detector="average",
            ref_level=-80.0,
        )
    )  # 40 sweeps × 80 bins: ±1 % a point
    setup.connect(laser.out, voa.p1)
    setup.connect(voa.p2, bs.a1)
    setup.connect(bs.b1, bpd.inp_plus, length=1.0)
    if inputs == 2:
        setup.connect(bs.b2, bpd.inp_minus, length=1.0)
    setup.connect(bpd.rf, esa.inp)
    setup.terminate_spares()  # the spare input (and the blocked arm): open, they send light back in
    return setup


def currents(setup: Setup) -> tuple[float, float]:
    """The two photocurrents (I₊, I₋) [A], from the mean powers at the inputs."""
    bpd, net = setup["BPD-1"], trace(setup)
    return bpd.mean_currents(
        *(net.mean_powers(bpd, port) for port in ("inp_plus", "inp_minus"))
    )


def noise_density(setup: Setup, seed: int = 1, sweeps: int = 1) -> float:
    """S_I [A²/Hz]: the analyzer's PSD over BAND, referred to the photocurrent the amplifier sees.
    RF OUTPUT drives the analyzer's 50 Ω from 50 Ω, so the analyzer gets half of G·i; the detector's
    pole |H(f)|² is divided out bin by bin. The analyzer averages power (an rms detector), so no
    correction for noise is needed, unlike a log-averaging one (RP Photonics, "Intensity noise").
    """
    bpd, esa = setup["BPD-1"], setup["ESA-1"]
    net = trace(setup)
    f, _ = esa.acquire(net, np.random.default_rng(seed)).psd()
    psd = np.mean(
        [
            esa.acquire(net, np.random.default_rng(seed + k)).psd()[1]
            for k in range(sweeps)
        ],
        axis=0,
    )
    band = (f > BAND[0]) & (f < BAND[1])
    h2 = 1 / (1 + (f[band] / bpd.bandwidth) ** 2)
    volts2 = (
        psd[band]
        * esa.input_impedance
        * (1 + bpd.rf_impedance / esa.input_impedance) ** 2
    )
    return float(np.mean(volts2 / h2)) / bpd.gain**2


def sweep(inputs: int, attenuations, dark: float) -> list[dict]:
    rows = []
    for k, att in enumerate(attenuations):
        s = build(att, inputs)
        ip, im = currents(s)
        total = noise_density(s, seed=10 + k)
        rows.append(
            {
                "att": att,
                "i": ip + im,
                "ip": ip,
                "im": im,
                "S": total,
                "excess": total - dark,
            }
        )
    return rows


def fit(rows: list[dict]) -> tuple[float, float]:
    """Excess noise = a·I + b·I², by least squares: (a, b), a = 2q, b = RIN. Each point's error is a
    fixed fraction of the total it was read from, dark included, so each is weighted by 1/total.
    """
    i = np.array([r["i"] for r in rows])
    w = 1 / np.array([r["S"] for r in rows])
    m = np.column_stack([i, i**2]) * w[:, None]
    a, b = np.linalg.lstsq(m, np.array([r["excess"] for r in rows]) * w, rcond=None)[0]
    return float(a), float(b)


def db(x: float) -> str:
    return f"{10 * math.log10(x):7.1f}" if x > 0 else "   —   "


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save noise against photocurrent")
    args = ap.parse_args()

    setup = build(0.0)
    laser, bpd = setup["LD-1"], setup["BPD-1"]
    bw, nep, _ = bpd.setting()
    shot_rin = 2 * photon_energy(laser.wavelength) / laser.power
    print(
        f"{laser.name}: {laser.power_dbm:g} dBm, RIN {laser.rin_db_hz:g} dB/Hz; its shot-noise RIN 2hν/P is "
        f"{10 * math.log10(shot_rin):.1f} dB/Hz, so the laser is {laser.rin_db_hz - 10 * math.log10(shot_rin):.0f} dB "
        f"above the shot-noise limit"
    )
    print(
        f"{bpd.name}: {bpd.preset} at {fmt_si(GAIN, 'V/A')}, {fmt_si(bw, 'Hz')}, NEP {fmt_si(nep, 'W/√Hz')}; noise read over "
        f"{fmt_si(BAND[0], 'Hz')}–{fmt_si(BAND[1], 'Hz')}; S_I in dB re 1 A²/Hz\n"
    )

    dark = noise_density(
        build(laser_on=False), sweeps=8
    )  # subtracted from every point: read it well
    print(
        f"1. Dark: S_I = {db(dark).strip()} dB; (NEP·ℜ)² = {db((nep * bpd.peak_responsivity()) ** 2).strip()} dB\n"
    )

    print("2. One input (INPUT− unplugged): laser noise against shot noise")
    print(
        f"   {'VOA':>5} {'I':>9} {'S_I − dark':>10} {'2qI':>7} {'RIN·I²':>7} {'RIN read':>9}  (RIN read = excess/I² − 2q/I)"
    )
    one = sweep(1, ONE_INPUT, dark)
    for r in one:
        read = r["excess"] / r["i"] ** 2 - 2 * Q_E / r["i"]
        print(
            f"   {r['att']:4.0f}  {fmt_si(r['i'], 'A'):>9} {db(r['excess'])}   {db(2 * Q_E * r['i'])} "
            f"{db(10 ** (RIN / 10) * r['i'] ** 2)} {db(read):>9}"
        )
    a1, b1 = fit(one)
    print(
        f"   fit a·I + b·I²: a = {a1:.3e} A → q = a/2 = {a1 / 2:.4g} C ({100 * (a1 / 2 / Q_E - 1):+.1f} %), "
        f"b = RIN = {10 * math.log10(b1):.2f} dB/Hz (set {RIN:g}); they cross at 2q/RIN = {fmt_si(2 * Q_E / 10 ** (RIN / 10), 'A')}\n"
    )

    print("3. Both inputs: the difference current, at the shot-noise level")
    print(
        f"   {'VOA':>5} {'I₊ + I₋':>9} {'S_I − dark':>10} {'2q(I₊+I₋)':>9} {'difference':>10} {'one input would read':>20}"
    )
    both = sweep(2, BOTH_INPUTS, dark)
    for r in both:
        would = (
            2 * Q_E * r["i"] + 10 ** (RIN / 10) * r["i"] ** 2
        )  # all this light on one diode
        print(
            f"   {r['att']:4.0f}  {fmt_si(r['i'], 'A'):>9} {db(r['excess'])}   {db(2 * Q_E * r['i'])}   "
            f"{10 * math.log10(r['excess'] / (2 * Q_E * r['i'])):+6.2f} dB {db(would):>14} ({10 * math.log10(would / r['excess']):+.1f} dB)"
        )
    a2, b2 = fit(both)
    top = max(both, key=lambda r: r["i"])
    residue = (
        10 ** (RIN / 10) * (top["ip"] - top["im"]) ** 2
    )  # the CMRR leaves ℜ₊P₊ − ℜ₋P₋ of the RIN
    print(
        f"   fit a·I + b·I²: q = a/2 = {a2 / 2:.4g} C ({100 * (a2 / 2 / Q_E - 1):+.1f} %); "
        f"the I² term is {db(b2 * top['i'] ** 2).strip()} dB at {fmt_si(top['i'], 'A')} (the CMRR residue "
        f"RIN·(I₊ − I₋)² is {db(residue).strip()} dB), against {db(2 * Q_E * top['i']).strip()} dB of shot noise"
    )
    print(
        f"   dark-noise clearance at {fmt_si(top['i'], 'A')}: {10 * math.log10(top['excess'] / dark):.1f} dB"
    )

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")
        fig, ax = plt.subplots(figsize=(7.5, 4.6))
        i = np.logspace(-6.0, -2.8, 100)
        ax.loglog(i, 2 * Q_E * i, "w--", lw=1, label="shot noise 2qI")
        ax.loglog(
            i,
            10 ** (RIN / 10) * i**2,
            ":",
            color="#f472b6",
            lw=1.2,
            label=f"laser RIN·I², {RIN:g} dB/Hz",
        )
        ax.loglog(
            i,
            2 * Q_E * i + 10 ** (RIN / 10) * i**2,
            color="#facc15",
            lw=0.8,
            alpha=0.6,
            label="one input: 2qI + RIN·I²",
        )
        ax.axhline(dark, color="#9ca3af", lw=0.8, label="dark (amplifier) noise")
        for rows, color, label in (
            (one, "#facc15", "one input"),
            (both, "#22d3ee", "both inputs (difference)"),
        ):
            ax.loglog(
                [r["i"] for r in rows],
                [r["excess"] for r in rows],
                "o",
                color=color,
                label=label,
            )
        ax.set(
            xlabel="photocurrent [A] (one input: I₊; both: I₊ + I₋)",
            ylabel="S_I above dark [A²/Hz]",
            title=f"{bpd.preset} at {fmt_si(GAIN, 'V/A')}: shot noise and laser intensity noise",
        )
        ax.grid(alpha=0.3, which="major")
        ax.grid(alpha=0.1, which="minor")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
