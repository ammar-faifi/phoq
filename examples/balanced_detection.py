"""Balanced detection: a weak heterodyne beat on a noisy local oscillator.

    uv run python examples/balanced_detection.py                  # beat, LO-noise rejection, gain table
    uv run python examples/balanced_detection.py --plot bpd.png   # also save the three scope traces
    uv run phoq serve examples/balanced_detection.py              # the bench in 3D

    LO laser, 8 dBm → IM (5 % rms, < 30 kHz) ───────┐            ┌─ 1 m → INPUT+    RF OUTPUT → CH1
                                                    ├─ 50:50 ────┤                   MONITOR+  → CH2
    signal laser, −30 dBm, +400 kHz ────────────────┘            └─ 1 m → INPUT−    MONITOR−  → CH3

The coupler's outputs carry P± = ½(P_LO + P_S) ± √(P_LO·P_S)·cos(2πΔf·t + φ): the LO, with its
intensity noise, is the same on both inputs, and the beat has opposite signs. The detector subtracts
the two photocurrents before its transimpedance amplifier, U_RF = G·(ℜ₊P₊ − ℜ₋P₋) (Thorlabs
PDB440x/PDB450x manual, §3.3): the beat adds up, and the LO and its noise cancel down to the
common-mode rejection (30 dB leaves 3 % of one diode's), so the half-milliwatt LO on each diode no
longer saturates the amplifier. Each monitor reads one photodiode alone (10 V/mW, DC–1 MHz): the
noisy LO with a beat on top that is half the RF's size and in antiphase on the two monitors.

In the lab, CH2 and CH3 show the LO noise moving the two monitors together while CH1 shows a clean
beat. Untick LD-S's emission to look at what is left of the noise on CH1. Turn the gain switch up
one step to 10⁵ V/A and the beat's crests clip at the +4.6 V rail.
"""

import argparse
import math

import numpy as np

from phoq import (
    BalancedPhotodetector,
    Coupler,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    Oscilloscope,
    Setup,
)
from phoq.engine import report, trace
from phoq.units import fmt_si

BEAT = 400e3  # signal − LO optical frequency: inside the 45 MHz RF bandwidth and the 1 MHz monitors
RIN = 0.05  # the LO's relative intensity noise: 5 % rms, over the band below CORNER
CORNER = (
    30e3  # its one-pole corner: slow technical wander, not the laser's white rin_db_hz
)


def lo_noise(
    n: int, rate: float, rms: float = RIN, corner: float = CORNER, seed: int = 5
) -> np.ndarray:
    """Relative intensity fluctuations δ(t), n samples at ``rate``: white noise through a one-pole
    low-pass at ``corner``, built in the frequency domain so the pattern repeats without a jump.
    """
    rng = np.random.default_rng(seed)
    f = np.fft.rfftfreq(n, 1 / rate)
    x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) / (1 + 1j * f / corner), n)
    return rms * (x - x.mean()) / x.std()


def build(gain: float = 1e4, signal: bool = True, rin: float = RIN) -> Setup:
    setup = Setup("Balanced detection: heterodyne on a noisy LO", seed=11)
    lo = setup.add(Laser("LD-LO", mode="cw", power_dbm=8.0, linewidth=1e3))
    sig = setup.add(
        Laser(
            "LD-S",
            mode="cw",
            power_dbm=-30.0,
            linewidth=1e3,
            detuning=BEAT,
            enabled=signal,
        )
    )
    im = setup.add(IntensityModulator("IM-LO"))
    fg = setup.add(FunctionGenerator("FG-1", trigger="free"))
    bs = setup.add(Coupler("BS-1"))
    bpd = setup.add(
        BalancedPhotodetector("BPD-1", preset="thorlabs/pdb450c", gain=gain)
    )
    osc = setup.add(
        Oscilloscope(
            "OSC-1",
            timebase=10e-6,
            bandwidth=20e6,
            trigger="edge",
            trigger_source="ch1",
            ch1_scale=0.5,
            ch1_offset=-2.5,
            ch2_scale=1.0,
            ch2_offset=-4.0,
            ch3_scale=1.0,
            ch3_offset=-4.0,
        )
    )  # RF below, the monitors overlaid above
    setup.connect(lo.out, im.p1)
    setup.connect(im.p2, bs.a1)
    setup.connect(sig.out, bs.a2)
    setup.connect(
        bs.b1, bpd.inp_plus, length=1.0
    )  # equal cords: the beat and the noise arrive in step
    setup.connect(bs.b2, bpd.inp_minus, length=1.0)
    setup.connect(fg.ch1, im.rf)
    setup.connect(bpd.rf, osc.ch1)
    setup.connect(bpd.mon_plus, osc.ch2)
    setup.connect(bpd.mon_minus, osc.ch3)
    # the IM sits at quadrature, T = ½, and the pattern moves it by δ: the LO's intensity noise
    rate = 4e6
    fg.set_pattern(
        "ch1", im.voltage_for(0.5 * (1 + lo_noise(2**15, rate, rin))), rate=rate
    )
    osc.trigger_level = report(setup)["detectors"]["BPD-1"].get(
        "rf", 0.0
    )  # the beat's mid-level
    return setup


def input_powers(setup: Setup, laser: str) -> tuple[float, float]:
    """(P₊, P₋): one laser's mean power at each input, the other laser blocked."""
    others = [c for c in setup.of_type(Laser) if c.name != laser]
    was = [c.enabled for c in others]
    for c in others:
        c.enabled = False
    try:
        net = trace(setup)
        return tuple(
            sum(net.mean_powers(setup["BPD-1"], port).values())
            for port in ("inp_plus", "inp_minus")
        )
    finally:
        for c, on in zip(others, was):
            c.enabled = on


def expected_beat(setup: Setup, output: str = "rf") -> float:
    """Beat amplitude at an output. Each diode sees 2ℜ±√(P_LO±·P_S±), in antiphase on the two, so
    RF OUTPUT, which subtracts them, adds them: G·|H(Δf)|·2(ℜ₊√(P_LO₊·P_S₊) + ℜ₋√(P_LO₋·P_S₋)).
    A monitor shows its own diode's: 10 V/mW·|H_mon(Δf)|·2ℜ±√(P_LO±·P_S±)."""
    bpd = setup["BPD-1"]
    lo, sig = input_powers(setup, "LD-LO"), input_powers(setup, "LD-S")
    beats = [
        2 * r * math.sqrt(a * b)
        for r, a, b in zip(bpd.responsivities(setup["LD-LO"].wavelength), lo, sig)
    ]
    if output == "rf":  # the amplifier's pole; the scope's 20 MHz is far away
        return bpd.gain * sum(beats) / math.hypot(1, BEAT / bpd.bandwidth)
    return (
        bpd.monitor_gain
        * beats[output == "mon_minus"]
        / math.hypot(1, BEAT / bpd.monitor_bandwidth)
    )


def sweep(setup: Setup, seed: int = 1):
    """One scope sweep: (RF, MONITOR+, MONITOR−) in volts."""
    res = setup["OSC-1"].acquire(trace(setup), np.random.default_rng(seed))
    return res, tuple(res[ch].data for ch in ("ch1", "ch2", "ch3"))


def beat_amplitudes(setup: Setup, sweeps: int = 8) -> tuple[float, float]:
    """√2 × rms over several sweeps, which is the beat's amplitude whatever the two lasers' phases
    do, with the LO power's wander inside one sweep averaged out: (on RF OUTPUT, on half the
    difference of the monitors, where the LO and its noise drop out and the beat stays only if it
    is opposite on the two)."""
    runs = [sweep(setup, seed)[1] for seed in range(sweeps)]
    rf = np.mean([r.var() for r, _, _ in runs])
    mon = np.mean([((p - m) / 2).var() for _, p, m in runs])
    return math.sqrt(2 * rf), math.sqrt(2 * mon)


def lo_noise_through(setup: Setup, sweeps: int = 8) -> dict:
    """With the signal blocked: the LO noise on MONITOR+ and on RF (rms), how closely MONITOR−
    follows MONITOR+, and the common-mode rejection [dB], the part of the RF output that follows
    MONITOR+, both referred to photocurrent.
    A regression rather than a ratio of rms values, so neither the amplifier's own noise nor the
    monitor's 1 MHz pole biases it."""
    bpd, sig = setup["BPD-1"], setup["LD-S"]
    was, sig.enabled = sig.enabled, False
    try:
        runs = [sweep(setup, 100 + seed)[1] for seed in range(sweeps)]
    finally:
        sig.enabled = was
    rf, mon, minus = (
        np.concatenate([r[k] - r[k].mean() for r in runs]) for k in range(3)
    )
    leak = np.dot(rf, mon) / np.dot(mon, mon) * bpd.monitor_gain / bpd.gain
    return {
        "mon": mon.std(),
        "rf": rf.std(),
        "rejection": -20 * math.log10(abs(leak)),
        "together": float(np.corrcoef(mon, minus)[0, 1]),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the three scope traces")
    args = ap.parse_args()

    setup = build()
    bpd = setup["BPD-1"]
    entry = report(setup)["detectors"]["BPD-1"]
    print(
        f"{bpd.name}: {bpd.preset}, gain {fmt_si(bpd.gain, 'V/A')}, RF bandwidth {fmt_si(bpd.bandwidth, 'Hz')}, "
        f"CMRR {bpd.cmrr_db:g} dB\n"
    )
    for key, value in entry["readouts"].items():
        print(f"  {key:28} {value}")

    res, (rf, mon_p, mon_m) = sweep(setup)
    rf_beat, mon_beat = beat_amplitudes(setup)
    want_rf, want_mon = (
        expected_beat(setup),
        (expected_beat(setup, "mon_plus") + expected_beat(setup, "mon_minus")) / 2,
    )
    noise = lo_noise_through(setup)
    mon_dc = entry["monitors"][0]
    single, swing = (
        bpd.gain * mon_dc / bpd.monitor_gain,
        bpd.setting()[2],
    )  # INPUT+'s photocurrent alone
    print(f"\nScope, {fmt_si(10 * res.timebase, 's')} sweeps:")
    print(
        f"  RF beat             {rf_beat:.3f} V amplitude, expected {want_rf:.3f} V ({100 * (rf_beat / want_rf - 1):+.1f} %)"
    )
    print(
        f"  MONITOR± beat       {mon_beat:.3f} V on (MON+ − MON−)/2, expected {want_mon:.3f} V "
        f"({100 * (mon_beat / want_mon - 1):+.1f} %): opposite on the two monitors"
    )
    print(
        f"  signal blocked      LO noise {1e3 * noise['mon']:.0f} mV rms on MONITOR+ ({100 * noise['mon'] / mon_dc:.1f} % "
        f"of {mon_dc:.2f} V), the same on MONITOR− (correlation {noise['together']:+.2f}), {1e3 * noise['rf']:.1f} mV rms on RF"
    )
    print(
        f"  LO noise rejection  {noise['rejection']:.1f} dB (CMRR {bpd.cmrr_db:g} dB)"
    )
    print(
        f"  INPUT− dark         RF would sit at {single:.2f} V, "
        + (
            f"beyond the ±{swing:g} V swing"
            if single > swing
            else f"inside the ±{swing:g} V swing"
        )
    )

    print("\nThe gain switch (RF level here = common-mode residue ± beat):")
    print(f"  {'gain':>9} {'bandwidth':>10} {'NEP':>12} {'CW sat.':>9}  RF level")
    for gain, (bw, nep, swing) in bpd.settings().items():
        s = build(gain=gain)
        d, dc, beat = (
            s["BPD-1"],
            report(s)["detectors"]["BPD-1"]["rf"],
            expected_beat(s),
        )
        state = (
            "saturates"
            if abs(dc) + beat > swing
            else "this bench" if gain == bpd.gain else ""
        )
        print(
            f"  {fmt_si(gain, 'V/A'):>9} {fmt_si(bw, 'Hz'):>10} {fmt_si(nep, 'W/√Hz'):>12} "
            f"{fmt_si(d.cw_saturation_power(), 'W'):>9}  {dc:+.3g} V ± {beat:.3g} V  {state}"
        )

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        fig, axes = plt.subplots(3, 1, figsize=(7.5, 6), sharex=True)
        for ax, v, title, color in zip(
            axes,
            (rf, mon_p, mon_m),
            ("RF", "MON+", "MON−"),
            ("#facc15", "#22d3ee", "#f472b6"),
        ):
            ax.plot(res.t * 1e6, v, lw=0.7, color=color)
            ax.set(ylabel=f"{title} [V]")
            ax.grid(alpha=0.3)
        axes[-1].set(xlabel="time from trigger [µs]")
        axes[0].set_title(
            f"{bpd.preset} at {fmt_si(bpd.gain, 'V/A')}: {fmt_si(BEAT, 'Hz')} beat on a LO with "
            f"{100 * RIN:g} % rms intensity noise below {fmt_si(CORNER, 'Hz')}",
            fontsize=10,
        )
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
