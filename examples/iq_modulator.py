"""IQ modulation: 16-QAM from a dual-parallel MZM, read back by a homodyne receiver.

    uv run python examples/iq_modulator.py                  # EVM, I/Q angle and carrier, four bias cases
    uv run python examples/iq_modulator.py --plot iq.png    # also save their constellations
    uv run phoq serve examples/iq_modulator.py              # the bench in 3D

                      FG-1 CH1, CH2: I and Q levels      PSU-1: DC1, DC2   PSU-2: DC3
                                 │                               │
    laser ─ TAP ─┬─ IQM (MXIQ-LN-30) ─ VOA (channel) ─ HYB-S ─┬──────────────── MIX-I ═ BPD-I → CH1
                 │                                             └──────────────── MIX-Q ═ BPD-Q → CH2
                 └─ 2 m ──────────────────────────────── HYB-L ─┬─ PS-I ───────── MIX-I
                                                                └─ PS-Q ───────── MIX-Q

The transmitter is the conventional IQ transmitter of Seimetz, "High-Order Modulation for Optical
Fiber Transmission" (2009), Fig. 2.18. DC1 = DC2 = −Vπ_DC hold both MZMs at their null, DC3 =
+Vπ_DC3/2 puts the arms in quadrature, and the AWG plays u = (2Vπ/π)·arcsin(i) on each RF input
(Eqs. 2.46–2.47), so every symbol leaves as the field ½(i + jq) (Eq. 2.16).

The receiver is a 90° hybrid made of four 50:50 couplers and a phase shifter in each LO branch, so
both branches lose the same. Each balanced detector reads one quadrature of E_S·E_LO*; the
shifters, set as a homodyne phase lock would set them, make those the I and Q axes, so CH1 + j·CH2
is the constellation. The LO is the same laser, and every signal and LO path is 4 m of cord, so the
laser's phase noise cancels. The DSP
finds the frame from the known symbols and fits CH1 + j·CH2 = a·i + b·q + c. Then |a| = |b| and an
angle of 90° between a and b mean a square constellation, and c is carrier left on the light.
Each bias error shows up in one of those numbers. In the lab, CH1 and CH2 show the four I and Q
levels. Move PSU-2's DC3 off 4.75 V and the I/Q phase in IQM-1's inspector follows.
"""

import argparse
import math

import numpy as np

from phoq import (
    Attenuator,
    BalancedPhotodetector,
    Coupler,
    DCPowerSupply,
    FunctionGenerator,
    IQModulator,
    Laser,
    Oscilloscope,
    PhaseShifter,
    Setup,
)
from phoq.engine import trace
from phoq.units import fmt_si

RATE = (
    25e6  # symbols/s: 40 ns symbols, far slower than the detectors' 150 MHz at 10³ V/A
)
LEVELS = np.array(
    [-1, -1 / 3, 1 / 3, 1]
)  # square 16-QAM: i, q ∈ {−1, −1/3, 1/3, 1} (Table 2.4)


def qam16(n: int = 1024, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.choice(LEVELS, n) + 1j * rng.choice(LEVELS, n)


def build(
    *,
    arcsin: bool = True,
    dc1_error: float = 0.0,
    dc3_error_deg: float = 0.0,
    channel_db: float = 20.0,
) -> Setup:
    """The bench at its operating point; ``dc1_error`` [V] moves DC1 off the null, ``dc3_error_deg``
    turns the I/Q phase away from 90°, and ``arcsin=False`` drives the RF inputs linearly instead.
    """
    setup = Setup("IQ modulator: 16-QAM and a homodyne receiver", seed=5)
    laser = setup.add(Laser("LD-1", mode="cw", power_dbm=6.0))
    tap, hyb_s, hyb_l, mix_i, mix_q = (
        setup.add(Coupler(n)) for n in ("TAP", "HYB-S", "HYB-L", "MIX-I", "MIX-Q")
    )
    iqm = setup.add(IQModulator("IQM-1", preset="exail/mxiq-ln-30"))
    voa = setup.add(Attenuator("VOA-1", attenuation_db=channel_db))
    ps_i, ps_q = setup.add(PhaseShifter("PS-I"), PhaseShifter("PS-Q"))
    fg = setup.add(FunctionGenerator("FG-1", trigger="free"))
    psu1, psu2 = setup.add(DCPowerSupply("PSU-1"), DCPowerSupply("PSU-2"))
    bpd_i, bpd_q = (
        setup.add(BalancedPhotodetector(n, preset="thorlabs/pdb450c", gain=1e3))
        for n in ("BPD-I", "BPD-Q")
    )
    osc = setup.add(
        Oscilloscope(
            "OSC-1",
            timebase=200e-9,
            sample_rate=1e9,
            bandwidth=200e6,
            trigger="free",
            ch1_scale=0.01,
            ch1_offset=1.5,
            ch2_scale=0.01,
            ch2_offset=-2.5,
        )
    )  # I above Q
    setup.connect(laser.out, tap.a1)
    setup.connect(tap.b1, iqm.p1)
    setup.connect(iqm.p2, voa.p1)
    setup.connect(voa.p2, hyb_s.a1)
    setup.connect(
        tap.b2, hyb_l.a1, length=2.0
    )  # 2 m here, 1 m everywhere else: 4 m on every path
    setup.connect(hyb_s.b1, mix_i.a1)
    setup.connect(hyb_s.b2, mix_q.a1)
    for ps, b, mix in ((ps_i, hyb_l.b1, mix_i), (ps_q, hyb_l.b2, mix_q)):
        setup.connect(b, ps.p1)
        setup.connect(ps.p2, mix.a2)
    for mix, bpd, ch in ((mix_i, bpd_i, osc.ch1), (mix_q, bpd_q, osc.ch2)):
        setup.connect(mix.b1, bpd.inp_plus)
        setup.connect(mix.b2, bpd.inp_minus)
        setup.connect(bpd.rf, ch)
    for out, port in (
        (psu1.ch1, iqm.bias_i),
        (psu1.ch2, iqm.bias_q),
        (psu2.ch1, iqm.bias_p),
    ):
        setup.connect(out, port)
    setup.connect(fg.ch1, iqm.rf_i)
    setup.connect(fg.ch2, iqm.rf_q)
    setup.terminate_spares()  # the tap's and both hybrids' spare inputs

    bias = iqm.operating_bias()
    psu1.set(ch1_voltage=bias["bias_i"] + dc1_error, ch2_voltage=bias["bias_q"])
    psu2.ch1_voltage = bias["bias_p"] + dc3_error_deg / 180 * iqm.v_pi_p
    sent = qam16()
    if arcsin:
        vi, vq = iqm.voltage_for(sent)
    else:  # the same ±Vπ swing without the arcsin: the cosine of the MZMs pulls the inner levels out
        vi, vq = iqm.v_pi * sent.real, iqm.v_pi * sent.imag
    fg.set_pattern("ch1", vi, rate=RATE)
    fg.set_pattern("ch2", vq, rate=RATE)
    setup.sent = sent  # what the receiver's DSP knows: the transmitted symbols
    lock_lo(setup)
    return setup


def lock_lo(setup: Setup) -> tuple[float, float]:
    """Set the LO's phase shifters as a homodyne receiver's phase lock would: each detector reads
    Re(x·z) of the symbol field x, with z its signal path times the conjugate LO path (at INPUT+).
    PS-I turns z_I real, so CH1 reads i; PS-Q turns z_Q to −j, so CH2 reads q, 90° from it.
    """
    iqm = setup["IQM-1"]
    setup["PS-I"].phase = setup["PS-Q"].phase = 0.0
    net = trace(setup)

    def z(bpd):  # the modulator's own factor left out: it is the symbol
        paths = [p for p in net.paths[setup[bpd]] if p.port == "inp_plus"]
        sig = max(
            (p for p in paths if any(m is iqm for m, _ in p.mods)),
            key=lambda p: abs(p.amp),
        )
        lo = max((p for p in paths if not p.mods), key=lambda p: abs(p.amp))
        return sig.amp * np.conj(lo.amp)

    setup["PS-I"].phase = float(
        np.angle(z("BPD-I")) % (2 * math.pi)
    )  # the shifter adds e^{jψ} to the LO
    setup["PS-Q"].phase = float((np.angle(z("BPD-Q")) + math.pi / 2) % (2 * math.pi))
    return setup["PS-I"].phase, setup["PS-Q"].phase


def receive(
    setup: Setup, sweeps: int = 8, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Received symbols CH1 + j·CH2 and the sent symbols they belong to, over several sweeps: in
    each, sample mid-symbol and find the frame by correlating with the known sequence.
    """
    osc, sent = setup["OSC-1"], setup.sent
    net = trace(setup)
    ys, ss = [], []
    view, osc.timebase = osc.timebase, 2e-6  # 500 symbols a sweep; the lab shows 50
    try:
        records = [
            osc.acquire(net, np.random.default_rng(seed + k)) for k in range(sweeps)
        ]
    finally:
        osc.timebase = view
    for res in records:
        r = res["ch1"].data + 1j * res["ch2"].data
        sps = round(1 / (RATE * res.dt))
        phase = max(
            range(sps), key=lambda o: np.mean(np.abs(r[o::sps] - r.mean()) ** 2)
        )  # mid-symbol
        y = r[phase::sps]
        fold = np.zeros(sent.size, dtype=complex)
        np.add.at(fold, np.arange(y.size) % sent.size, y - y.mean())
        corr = [abs(np.vdot(np.roll(sent, -d), fold)) for d in range(sent.size)]
        d = int(np.argmax(corr))
        ys.append(y)
        ss.append(sent[(np.arange(y.size) + d) % sent.size])
    return np.concatenate(ys), np.concatenate(ss)


def analyse(y: np.ndarray, s: np.ndarray, noise: float) -> dict:
    """Fit y = a·i + b·q + c. EVM: the rms distance from that fit (the noise) and from the best
    square constellation h·s + c (everything the bias errors add), both over the rms symbol.
    """
    m = np.column_stack([s.real, s.imag, np.ones(s.size)])
    a, b, c = np.linalg.lstsq(m, y, rcond=None)[0]
    h = np.linalg.lstsq(np.column_stack([s, np.ones(s.size)]), y, rcond=None)[0]
    scale = abs(h[0]) * math.sqrt(np.mean(np.abs(s) ** 2))
    iq = np.linalg.solve(
        np.array([[a.real, b.real], [a.imag, b.imag]]),
        np.vstack([(y - c).real, (y - c).imag]),
    )
    i_est = iq[0]
    return {
        "evm": math.sqrt(np.mean(np.abs(y - h[0] * s - h[1]) ** 2)) / scale,
        "evm_fit": math.sqrt(np.mean(np.abs(y - m @ [a, b, c]) ** 2)) / scale,
        "evm_noise": noise / scale,
        "angle": math.degrees(np.angle(b / a)),
        "gain": abs(b / a),
        "carrier_db": 20 * math.log10(max(abs(c), 1e-12) / scale),
        "outer_inner": abs(
            i_est[np.isclose(s.real, 1)].mean()
            / i_est[np.isclose(s.real, 1 / 3)].mean()
        ),
        "equalized": (y - h[1]) / h[0],
    }


def receiver_floor(
    setup: Setup, sweeps: int = 16, seed: int = 50
) -> tuple[complex, float]:
    """The receiver alone, the channel VOA at 80 dB: its offset (LO and CMRR residue) and noise."""
    voa = setup["VOA-1"]
    was, voa.attenuation_db = voa.attenuation_db, 80.0
    try:
        net = trace(setup)
        records = [
            setup["OSC-1"].acquire(net, np.random.default_rng(seed + k))
            for k in range(sweeps)
        ]
        r = np.concatenate([res["ch1"].data + 1j * res["ch2"].data for res in records])
    finally:
        voa.attenuation_db = was
    return complex(r.mean()), float(np.sqrt(np.mean(np.abs(r - r.mean()) ** 2)))


def measure(setup: Setup) -> dict:
    """Through the receiver: its own offset removed, its noise measured on the side."""
    offset, noise = receiver_floor(setup)
    y, s = receive(setup)
    return analyse(y - offset, s, noise)


def model(setup: Setup) -> dict:
    """The same fit on the transfer function itself, IQM.field() of every sent symbol: what a
    perfect receiver would read."""
    iqm, fg = setup["IQM-1"], setup["FG-1"]
    dc = (
        setup["PSU-1"].ch1_voltage,
        setup["PSU-1"].ch2_voltage,
        setup["PSU-2"].ch1_voltage,
    )
    return analyse(
        iqm.field(fg.pattern_volts("ch1"), fg.pattern_volts("ch2"), *dc),
        setup.sent,
        0.0,
    )


CASES = (
    ("operating point", {}),
    ("DC3 10° off quadrature", {"dc3_error_deg": 10.0}),
    ("DC1 0.5 V off the null", {"dc1_error": 0.5}),
    ("linear drive, no arcsin", {"arcsin": False}),
)

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the four constellations")
    args = ap.parse_args()

    setup = build()
    iqm = setup["IQM-1"]
    print(
        f"{iqm.name}: {iqm.preset}, Vπ RF {iqm.v_pi:g} V, DC1/DC2 {iqm.v_pi_dc:g} V, DC3 {iqm.v_pi_p:g} V; "
        f"16-QAM at {fmt_si(RATE, 'Bd')}, {setup['VOA-1'].attenuation_db:g} dB channel"
    )
    for key in (
        "DC1 (I)",
        "DC2 (Q)",
        "DC3 (I/Q phase)",
        "Bias point",
        "Carrier at this bias",
    ):
        print(f"  {key:22} {iqm.info()[key]}")

    print(
        "\nThrough the receiver, and (in brackets) the model: the same fit on IQM.field() of each symbol"
    )
    print(
        f"  {'case':25} {'EVM':>16} {'I/Q angle':>16} {'Q/I gain':>14} {'carrier':>18} {'outer/inner':>12}"
    )
    results = {}
    for title, kw in CASES:
        b = build(**kw)
        r, m = measure(b), model(b)
        results[title] = r
        evm = math.hypot(
            m["evm"], r["evm_noise"]
        )  # the model's error and the receiver's noise add
        print(
            f"  {title:25} {100 * r['evm']:5.1f} % ({100 * evm:4.1f} %)  {r['angle']:6.1f}° ({m['angle']:5.1f}°)"
            f"  {r['gain']:5.3f} ({m['gain']:5.3f})  {r['carrier_db']:5.1f} dB ({m['carrier_db']:5.1f} dB)"
            f"  {r['outer_inner']:4.2f} ({m['outer_inner']:4.2f})"
        )
    print(
        f"\n  receiver noise alone: {100 * results['operating point']['evm_noise']:.1f} % EVM. Ideal: 90°, gain 1, "
        "no carrier, outer/inner 3 (the arcsin makes the levels equidistant)"
    )

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")
        fig, axes = plt.subplots(1, len(CASES), figsize=(13, 3.6))
        for ax, (title, r), color in zip(
            axes, results.items(), ("#facc15", "#22d3ee", "#f472b6", "#4ade80")
        ):
            z = r["equalized"]
            ax.plot(z.real, z.imag, ".", ms=1.5, color=color, alpha=0.6)
            ax.plot(*np.meshgrid(LEVELS, LEVELS), "+", color="white", ms=7, mew=0.8)
            ax.set(xlim=(-1.6, 1.6), ylim=(-1.6, 1.6), aspect="equal")
            ax.set_title(f"{title}\nEVM {100 * r['evm']:.1f} %", fontsize=9)
            ax.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
