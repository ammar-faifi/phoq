"""Optical delay line (ODL): the model checked against its theory.

    uv run python examples/odl_validation.py      # every check: theory against phoq
    uv run phoq serve examples/odl_validation.py  # a DLI for 100 ps pulses: turn DLI-ODL's dial in 3D

The README section "Optical delay line" derives the expected values:

1. group delay    τ_g = τ₀ + τ, stopped at the end of travel and on the step grid
2. carrier phase  one fringe per optical period λ/c (5.17 fs at 1550 nm)
3. coherence      balanced MZI, CW laser of linewidth Δν: V(δ) = V₀·e^{−πΔν|δ|}
4. pulse overlap  DLI, Gaussian pulses of FWHM w: V(δ) = V₀·e^{−πΔν(T+δ)}·exp(−ln2·δ²/w²)
5. coupling loss  IL + 10·log10[1 + (d/2z_R)²], d the air gap away from mid-travel

V₀ = 2|a₁a₂|/(|a₁|² + |a₂|²) is the ceiling that unequal arm losses leave (the delay line loses 0.9–1.2 dB
more than the phase shifter, depending on the dial). Checks 3 and 4 measure V twice: in the steady state (`report`)
and from synthesized fields (one phase walk per record, energies integrated over it), by four-step
phase shifting with the phase shifter. Check 4 also runs the photon counter, whose bins average the
light unless `oversample` splits them finer than the pulses.
"""

import math
import sys
from pathlib import Path

import numpy as np

from phoq import (
    Coupler,
    Laser,
    OpticalDelayLine,
    Oscilloscope,
    PhaseShifter,
    PhotonCounter,
    Photodiode,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import report, synthesize, trace
from phoq.units import C0, N_AIR, fmt_si

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dli import (
    RATE,
    arms,
    balance,
    dli,
    lock,
)  # noqa: E402  (the shared interferometer)

WIDTH = (
    100e-12  # pulse FWHM for the DLI checks: its delay must match to a fraction of this
)
T = 1 / RATE


# ---- benches ------------------------------------------------------------------------------------
def mzi(linewidth: float = 0.0, wavelength: float = 1550e-9) -> Setup:
    """CW laser → coupler → (0.5 m, PS-1, 0.5 m) and (0.5 m, ODL-1, 0.5 m) → coupler → PD-1, PD-2.
    The cords match, so the arms differ by the delay line alone: δ = τ₀ + τ."""
    setup = Setup("Balanced MZI with a delay line", seed=1)
    laser = setup.add(
        Laser(
            "LD-1", mode="cw", power_dbm=0.0, linewidth=linewidth, wavelength=wavelength
        )
    )
    bs1, bs2 = setup.add(Coupler("BS-1"), Coupler("BS-2"))
    ps, odl = setup.add(PhaseShifter("PS-1"), OpticalDelayLine("ODL-1"))
    pd1, pd2 = setup.add(Photodiode("PD-1"), Photodiode("PD-2"))
    setup.connect(laser.out, bs1.a1)
    setup.connect(bs1.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, bs2.a1, length=0.5)
    setup.connect(bs1.b2, odl.p1, length=0.5)
    setup.connect(odl.p2, bs2.a2, length=0.5)
    setup.connect(bs2.b1, pd1.inp)
    setup.connect(bs2.b2, pd2.inp)
    setup.terminate_spares()
    return setup


def build(width: float = WIDTH, linewidth: float = 100e3) -> Setup:
    """Pulsed laser → DLI (examples/dli.py) → 20 GHz photodiodes → scope at 1 ns/div. With the
    delay line balanced, one slot's pulse leaves PD-C alone; turn DLI-ODL's dial and the two arms'
    pulses walk apart, both ports showing two half pulses that no longer interfere."""
    setup = Setup("Delay-line interferometer, 100 ps pulses", seed=3)
    # −20 dBm average is ≈ 1 mW peak in 100 ps pulses: the photodiodes stay linear.
    laser = setup.add(
        Laser(
            "LD-1",
            mode="pulsed",
            rep_rate=RATE,
            pulse_width=width,
            power_dbm=-20.0,
            linewidth=linewidth,
        )
    )
    pd_c, pd_d = setup.add(
        Photodiode("PD-C", bandwidth=20e9), Photodiode("PD-D", bandwidth=20e9)
    )
    osc = setup.add(
        Oscilloscope(
            "OSC-1",
            timebase=1e-9,
            bandwidth=20e9,
            sample_rate=50e9,
            ch1_scale=100e-6,
            ch2_scale=100e-6,
            ch1_offset=-1.0,
            ch2_offset=-3.5,
        )
    )
    bs_in, bs_out, _, _ = dli(setup, "DLI")
    setup.connect(laser.out, bs_in.a1)
    setup.connect(bs_out.b1, pd_c.inp)
    setup.connect(bs_out.b2, pd_d.inp)
    setup.connect(pd_c.rf, osc.ch1)
    setup.connect(pd_d.rf, osc.ch2)
    setup.terminate_spares()
    balance(setup, "LD-1", "PD-C", "DLI-ODL")
    lock(setup, "LD-1", "PD-C", "DLI-PS")
    osc.h_position = (
        min(trace(setup).delays(pd_c, laser)) % T - 4 * osc.timebase
    )  # pulse mid-screen
    return setup


def counting(width: float = WIDTH, oversample: int = 1) -> Setup:
    """The DLI with single-photon detectors and a time tagger in 1 ns bins (10 per pulse)."""
    setup = Setup("DLI, photon counting", seed=4)
    laser = setup.add(
        Laser(
            "LD-1",
            mode="pulsed",
            rep_rate=RATE,
            pulse_width=width,
            power_dbm=-88.0,
            linewidth=100e3,
        )
    )
    spd = dict(efficiency=0.8, dark_count_rate=100.0, dead_time=0.0)
    dm1, dm2 = setup.add(
        SinglePhotonDetector("DM1", **spd), SinglePhotonDetector("DM2", **spd)
    )
    cnt = setup.add(
        PhotonCounter("CNT", slots=200_000, bin_rate=10 * RATE, oversample=oversample)
    )
    bs_in, bs_out, _, _ = dli(setup, "DLI")
    setup.connect(laser.out, bs_in.a1)
    setup.connect(bs_out.b1, dm1.inp)
    setup.connect(bs_out.b2, dm2.inp)
    setup.connect(dm1.out, cnt.ch1)
    setup.connect(dm2.out, cnt.ch2)
    setup.terminate_spares()
    balance(setup, "LD-1", "DM1", "DLI-ODL")
    return setup


# ---- measurements -------------------------------------------------------------------------------
def visibility(setup: Setup, shifter: str, measure) -> float:
    """Four-step phase shifting: P(θ) = A + B·cos(θ + θ₀) at θ = 0, π/2, π, 3π/2 gives V = B/A."""
    ps = setup[shifter]
    keep, p = ps.phase, []
    for k in range(4):
        ps.phase = keep + k * math.pi / 2
        p.append(measure())
    ps.phase = keep
    return math.hypot(p[0] - p[2], p[1] - p[3]) / 2 / (sum(p) / 4)


def steady(setup: Setup, detector: str):
    return lambda: report(setup)["detectors"][detector]["power"]


def synthesized(setup: Setup, detector: str, dt: float, n: int, seed: int = 7):
    """Energy reaching ``detector`` over one synthesized record of n bins of dt (a new phase walk
    per call)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * dt

    def energy():
        d = setup[detector]
        fields = synthesize(trace(setup), [d], t, dt, rng)[d]
        return sum(float(np.sum(np.abs(E) ** 2)) for E in fields.values()) * dt

    return energy


def ceiling(setup: Setup, laser: str, detector: str) -> float:
    """V₀ = 2|a₁a₂|/(|a₁|² + |a₂|²) of the two arms' amplitudes at ``detector``."""
    a, b = (abs(p.amp) for p in arms(setup, laser, detector))
    return 2 * a * b / (a * a + b * b)


def counted(setup: Setup, batches: int = 2, seed: int = 5) -> float:
    """V = (n₁ − n₂)/(n₁ + n₂) from the tagger, the DLI locked so equal phases go to DM1."""
    lock(setup, "LD-1", "DM1", "DLI-PS")
    runner, n1, n2 = Runner(setup, seed=seed), 0, 0
    for _ in range(batches):
        res = runner.acquire("CNT")
        n1, n2 = n1 + int(res.clicks["ch1"].sum()), n2 + int(res.clicks["ch2"].sum())
    return (n1 - n2) / (n1 + n2)


def overlap(delta: float, width: float, linewidth: float = 100e3) -> float:
    """Theory without V₀: coherence of neighbouring pulses times their Gaussian field overlap."""
    return math.exp(-math.pi * linewidth * (T + delta)) * math.exp(
        -math.log(2) * delta**2 / width**2
    )


# ---- the checks ---------------------------------------------------------------------------------
def check_delay() -> list[tuple]:
    """Trace delay through the line against τ₀ + τ (the cords' share subtracted)."""
    setup = mzi()
    odl, pd = setup["ODL-1"], setup["PD-1"]
    rows = []
    for dial, step in (
        (0.0, 0.0),
        (105e-12, 0.0),
        (330e-12, 0.0),
        (400e-12, 0.0),
        (123.4567e-12, 1e-15),
    ):
        odl.set(delay=dial, resolution=step)
        d = sorted(trace(setup).delays(pd))
        warned = any(odl.name in w["text"] for w in report(setup)["warnings"])
        expect = odl.insertion_delay + min(
            round(dial / step) * step if step else dial, odl.max_delay
        )
        rows.append((dial, step, d[1] - d[0], expect, warned))
    odl.set(delay=0.0, resolution=0.0)
    return rows


def check_fringe(
    wavelength: float = 1550e-9, span: float = 30e-15, step: float = 0.02e-15
) -> tuple[float, float]:
    """Fringe period of P(τ) at PD-1 from zero crossings over ``span`` of dial, against λ/c."""
    setup = mzi(wavelength=wavelength)
    odl = setup["ODL-1"]
    taus = 50e-12 + np.arange(0, span, step)
    p = []
    for tau in taus:
        odl.delay = float(tau)
        p.append(report(setup)["detectors"]["PD-1"]["power"])
    p = np.array(p) - np.mean(p)
    k = np.flatnonzero(
        (p[:-1] < 0) & (p[1:] >= 0)
    )  # rising crossings: immune to a biased mean
    cross = taus[k] - p[k] * (taus[k + 1] - taus[k]) / (p[k + 1] - p[k])
    return (cross[-1] - cross[0]) / (len(cross) - 1), wavelength / C0


def check_coherence(linewidth: float = 1e9) -> list[tuple]:
    """Visibility against δ = τ₀ + τ in the balanced MZI, for a 1 GHz-wide laser."""
    setup = mzi(linewidth=linewidth)
    odl, rows = setup["ODL-1"], []
    v0 = ceiling(setup, "LD-1", "PD-1")
    for dial in (0.0, 100e-12, 200e-12, 330e-12):
        odl.delay = dial
        delta = odl.total_delay
        rows.append(
            (
                delta,
                v0 * math.exp(-math.pi * linewidth * delta),
                visibility(setup, "PS-1", steady(setup, "PD-1")),
                visibility(setup, "PS-1", synthesized(setup, "PD-1", 20e-12, 250_000)),
            )
        )
    return rows


def check_overlap(
    width: float = WIDTH, dials=(0.0, 55e-12, 105e-12, 155e-12, 205e-12, 330e-12)
) -> list[tuple]:
    """DLI visibility against δ = arm difference − T, scanning the dial across the travel."""
    setup = build(width=width)
    odl = setup["DLI-ODL"]
    best = odl.delay
    rows = []
    for dial in dials:
        odl.delay = dial
        delta = dial - best
        v0 = ceiling(setup, "LD-1", "PD-C")
        rows.append(
            (
                dial,
                delta,
                v0 * overlap(delta, width),
                visibility(setup, "DLI-PS", steady(setup, "PD-C")),
                visibility(
                    setup, "DLI-PS", synthesized(setup, "PD-C", 4e-12, 1_000_000)
                ),
            )
        )
    return rows


def check_cords_alone(width: float) -> tuple[float, float, float]:
    """The arms' 3 m − 1 m of cords without the delay line's 205 ps (τ₀ and the dial at 0)."""
    setup = build(width=width)
    odl = setup["DLI-ODL"]
    short, long_ = arms(setup, "LD-1", "PD-C")
    delta = (long_.delay - short.delay - odl.total_delay) - T
    odl.set(insertion_delay=0.0, delay=0.0)
    v0 = ceiling(setup, "LD-1", "PD-C")
    return (
        delta,
        v0 * overlap(delta, width),
        visibility(setup, "DLI-PS", steady(setup, "PD-C")),
    )


def check_counter(delta: float = 100e-12) -> list[tuple]:
    """The tagger at δ = +100 ps: whole 1 ns bins against 25 ps sub-bins."""
    rows = []
    for m in (1, 40):
        setup = counting(oversample=m)
        odl = setup["DLI-ODL"]
        odl.delay += delta
        v0 = ceiling(setup, "LD-1", "DM1")
        rows.append((m, v0 * overlap(delta, WIDTH), counted(setup)))
    return rows


def check_loss() -> list[tuple]:
    """Loss of the line against the dial, relative to mid-travel, at 1550 and 1310 nm."""
    rows = []
    for wl in (1550e-9, 1310e-9):
        setup = mzi(wavelength=wl)
        odl = setup["ODL-1"]
        z_r = math.pi * odl.beam_waist**2 / wl

        def through(dial):  # power the line passes on to its output cord
            odl.delay = dial
            net = trace(setup)
            out = setup["ODL-1"].ports["p2"].peer.path
            return net.port_power[out] / net.port_power["ODL-1.p1"]

        mid = through(odl.max_delay / 2)
        for dial in (0.0, odl.max_delay / 4, odl.max_delay / 2, odl.max_delay):
            d = C0 * (dial - odl.max_delay / 2) / N_AIR
            rows.append(
                (
                    wl,
                    dial,
                    10 * math.log10(1 + (d / (2 * z_r)) ** 2),
                    10 * math.log10(mid / through(dial)),
                )
            )
    return rows


if __name__ == "__main__":
    print("1. Group delay: trace delay through the line against τ₀ + τ")
    for dial, step, got, want, warned in check_delay():
        note = (
            f"step {fmt_si(step, 's')}"
            if step
            else ("end stop, warned" if warned else "")
        )
        print(
            f"   dial {fmt_si(dial, 's'):>9}: {fmt_si(got, 's', 7):>13}  expected {fmt_si(want, 's', 7):>13}  {note}"
        )

    print("\n2. Carrier phase: fringe period while scanning the dial")
    for wl in (1550e-9, 1310e-9):
        got, want = check_fringe(wl)
        print(
            f"   {wl * 1e9:.0f} nm: {fmt_si(got, 's', 6)}  expected λ/c = {fmt_si(want, 's', 6)}"
        )

    print("\n3. Coherence: balanced MZI, CW laser of 1 GHz linewidth (τ_c = 318 ps)")
    print(f"   {'δ':>8} {'theory':>8} {'report':>8} {'fields':>8}")
    for delta, want, rep, syn in check_coherence():
        print(f"   {fmt_si(delta, 's'):>8} {want:8.3f} {rep:8.3f} {syn:8.3f}")

    print(
        f"\n4. Pulse overlap: DLI, {fmt_si(WIDTH, 's')} pulses, 100 kHz laser (dial balanced at 105 ps)"
    )
    print(f"   {'dial':>8} {'δ':>8} {'theory':>8} {'report':>8} {'fields':>8}")
    for dial, delta, want, rep, syn in check_overlap():
        print(
            f"   {fmt_si(dial, 's'):>8} {fmt_si(delta, 's'):>8} {want:8.3f} {rep:8.3f} {syn:8.3f}"
        )
    for w in (1e-9, WIDTH):
        delta, want, rep = check_cords_alone(w)
        print(
            f"   standard cords alone (δ = {fmt_si(delta, 's')}), {fmt_si(w, 's')} pulses: theory {want:.3f}, report {rep:.3f}"
        )
    for m, want, got in check_counter():
        print(
            f"   time tagger at δ = +100 ps, oversample {m:>2}: counted V = {got:.3f}  (theory {want:.3f})"
        )

    print("\n5. Coupling loss against the dial, relative to mid-travel (w₀ = 250 µm)")
    for wl, dial, want, got in check_loss():
        print(
            f"   {wl * 1e9:.0f} nm, dial {fmt_si(dial, 's'):>9}: {got:6.3f} dB  expected {want:6.3f} dB"
        )
