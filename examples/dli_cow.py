"""A delay-line interferometer (DLI): DPSK demodulation, and the COW-QKD monitoring line.

    uv run python examples/dli_cow.py                # DPSK decoding, then COW visibility
    uv run python examples/dli_cow.py --plot dli.png # also save the scope view of the DPSK bench
    uv run phoq serve examples/dli_cow.py            # the DPSK bench in 3D

A DLI is an unbalanced Mach–Zehnder whose long arm is exactly one pulse period T longer. Each
output time slot then holds pulse k (short arm) on top of pulse k−1 (long arm), so the outputs
compare *neighbouring* pulses: with Δφ = φ_k − φ_{k−1} the constructive port gets
½[1 + V cos(Δφ + θ)] of the light and the destructive port the rest. V is the coherence between
neighbouring pulses, e^{−πΔν·T} for a laser of linewidth Δν, and θ the DLI's own phase, set with
the phase shifter in the short arm.

Both arms are standard patch cords: 0.5 m + 0.5 m around the phase shifter, 1 m + 2 m around an
optical delay line in the long arm. The cords give 2 m (9.795 ns); the delay line, set once by
`balance`, adds the last 205 ps so the arms differ by exactly T (see examples/odl_validation.py).

DPSK bench (what `phoq serve` shows): a phase modulator writes φ_k = φ_{k−1} + π·b_k on a 100 MHz
pulse train, so the bit b_k comes out of the destructive port. Scope CH1 is the constructive
port, CH2 the destructive port, CH3 the bits (the generator's second channel, delayed to line up).
In the lab, turn DLI-PS's phase to swap the outputs, raise LD-1's linewidth to 10 MHz to watch the
neighbouring pulses lose their phase relation, or tick "Random phase per pulse".

COW monitoring line (headless): coherent one-way QKD sends each bit as a pulse in one of two time
slots and checks, on a DLI at Bob, that neighbouring non-empty pulses (across bit boundaries and
inside decoys) are still coherent. Single-photon detectors DM1/DM2 on the DLI outputs give the
visibility V = (n₁ − n₂)/(n₁ + n₂); a drop reveals an attack that re-prepares pulses.
"""

import argparse
import math

import numpy as np

from phoq import (
    BeamSplitter,
    FiberSpool,
    FunctionGenerator,
    IntensityModulator,
    Attenuator,
    Laser,
    OpticalDelayLine,
    Oscilloscope,
    PhaseModulator,
    PhaseShifter,
    PhotonCounter,
    Photodiode,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import trace
from phoq.units import FIBER_DELAY, fmt_si, photon_energy

RATE = 100e6  # pulse rate: T = 10 ns between neighbouring pulses
N_BITS = 32  # DPSK pattern length (even parity, so the differential phase closes on itself)


# ---- the interferometer -------------------------------------------------------------------------
def dli(setup: Setup, tag: str):
    """BS → short arm (0.5 m, phase shifter, 0.5 m) and long arm (1 m, delay line, 2 m) → BS.
    Returns (in, out, phase shifter, delay line); `balance` then sets the delay line."""
    bs_in, bs_out = setup.add(BeamSplitter(f"{tag}-BS1"), BeamSplitter(f"{tag}-BS2"))
    ps, odl = setup.add(PhaseShifter(f"{tag}-PS"), OpticalDelayLine(f"{tag}-ODL"))
    setup.connect(bs_in.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, bs_out.a1, length=0.5)
    setup.connect(bs_in.b2, odl.p1, length=1.0)
    setup.connect(odl.p2, bs_out.a2, length=2.0)
    return bs_in, bs_out, ps, odl


def arms(setup: Setup, laser: str, detector: str) -> tuple:
    """The short- and long-arm paths of ``laser``'s light to ``detector`` (any reflected paths are
    far weaker and later)."""
    paths = sorted((p for p in trace(setup).paths[setup[detector]] if p.source is setup[laser]),
                   key=lambda p: p.delay)
    return paths[0], paths[1]


def balance(setup: Setup, laser: str, detector: str, odl: str, rate: float = RATE) -> float:
    """Set the delay line so the long arm is one slot 1/rate longer than the short one, as you
    would with the dial; fails when the cords leave more (or less) than the line can reach."""
    line = setup[odl]
    short, long_ = arms(setup, laser, detector)
    want = line.setting + 1 / rate - (long_.delay - short.delay)
    if not 0.0 <= want <= line.max_delay:
        raise ValueError(f"{odl} would need {fmt_si(want, 's')}, outside its 0–{fmt_si(line.max_delay, 's')} "
                         "travel: change the cords in the arms")
    line.delay = want
    return want


def lock(setup: Setup, laser: str, detector: str, shifter: str) -> float:
    """Set the phase shifter so neighbouring pulses with equal phases interfere constructively at
    ``detector`` (a real DLI is temperature-tuned to this point). Modulators count at 0 V."""
    ps = setup[shifter]
    ps.phase = 0.0

    def static(p):
        a = complex(p.amp)
        for mod, _ in p.mods:
            a *= complex(np.asarray(mod.response([mod.static_voltage()]))[0])
        return a

    short, long_ = arms(setup, laser, detector)
    ps.phase = float((np.angle(static(long_)) - np.angle(static(short))) % (2 * math.pi))
    return ps.phase


def slot_delay(setup: Setup, laser: str, detector: str) -> float:
    """When pulse k emitted at k·T reaches ``detector`` through the short arm, minus k·T."""
    return min(trace(setup).delays(setup[detector], setup[laser]))


# ---- 1. DPSK: what the lab shows ----------------------------------------------------------------
def dpsk_bits(n: int = N_BITS, seed: int = 11) -> np.ndarray:
    bits = np.random.default_rng(seed).integers(0, 2, n)
    if bits.sum() % 2:  # even parity: the pattern repeats without a phase step at the seam
        bits[-1] ^= 1
    return bits


def build(linewidth: float = 100e3, bits: np.ndarray | None = None) -> Setup:
    setup = Setup("Delay-line interferometer (DPSK)", seed=5)
    laser = setup.add(Laser("LD-1", mode="pulsed", rep_rate=RATE, pulse_width=1e-9, power_dbm=-3.0, linewidth=linewidth))
    pm = setup.add(PhaseModulator("PM-1"))
    fg = setup.add(FunctionGenerator("FG-1"))
    pd_c, pd_d = setup.add(Photodiode("PD-C", bandwidth=5e9), Photodiode("PD-D", bandwidth=5e9))
    osc = setup.add(Oscilloscope("OSC-1", timebase=20e-9, ch1_scale=500e-6, ch2_scale=500e-6, ch3_scale=0.5,
                                 ch1_offset=-1.0, ch2_offset=-3.5, ch3_offset=1.5))
    setup.connect(laser.out, pm.p1)
    bs_in, bs_out, _, _ = dli(setup, "DLI")
    setup.connect(pm.p2, bs_in.a1)
    setup.connect(bs_out.b1, pd_c.inp)
    setup.connect(bs_out.b2, pd_d.inp)
    setup.connect(fg.ch1, pm.rf)
    setup.connect(pd_c.rf, osc.ch1)
    setup.connect(pd_d.rf, osc.ch2)
    setup.connect(fg.ch2, osc.ch3)

    bits = dpsk_bits() if bits is None else np.asarray(bits)
    phase = math.pi * (np.cumsum(bits) % 2)  # differential encoding: φ_k = φ_{k−1} + π b_k
    fg.set_pattern("ch1", pm.voltage_for(phase), rate=RATE)  # aligned: symbol k meets pulse k
    fg.set_pattern("ch2", bits.astype(float), rate=RATE)  # the bits, for the eye
    balance(setup, "LD-1", "PD-C", "DLI-ODL")
    lock(setup, "LD-1", "PD-C", "DLI-PS")
    fg.ch2_delay = slot_delay(setup, "LD-1", "PD-C")  # CH3 shows b_k in the slot that decodes it
    return setup


def decode(setup: Setup, seed: int = 1, timebase: float | None = None) -> dict:
    """One scope sweep: the energy each port gets in each slot, the decoded bits, the errors.
    ``timebase`` widens the sweep (200 ns/div: about 200 slots) without changing the lab's view."""
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
        centre = k / RATE + d  # slot k after the trigger pulse, through the short arm
        sel = np.abs(t - centre) < 0.4 / RATE
        if not sel.any() or t[0] > centre - 0.4 / RATE or t[-1] < centre + 0.4 / RATE:
            continue
        out.append((k, float(c[sel].sum() * dt), float(dd[sel].sum() * dt), float(np.median(res["ch3"].data[sel]))))
    k, e_c, e_d, marker = (np.array(v) for v in zip(*out))
    decided = (e_d > e_c).astype(int)
    expected = (marker > 0.5).astype(int)
    contrast = (e_c - e_d) / (e_c + e_d) * np.where(expected == 1, -1.0, 1.0)  # +1: right port only
    return {"result": res, "slots": k.size, "errors": int(np.sum(decided != expected)),
            "decided": decided, "expected": expected, "contrast": float(np.mean(contrast))}


# ---- 2. COW-QKD monitoring line ------------------------------------------------------------------
def cow_sequence(n_bits: int, decoy: float = 0.1, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """Slot intensities (0/1) for n_bits logical bits: 0 → (μ, 0), 1 → (0, μ), decoy → (μ, μ)."""
    rng = np.random.default_rng(seed)
    kind = np.where(rng.random(n_bits) < decoy, 2, rng.integers(0, 2, n_bits))
    slots = np.zeros(2 * n_bits)
    slots[0::2] = (kind == 0) | (kind == 2)
    slots[1::2] = (kind == 1) | (kind == 2)
    return kind, slots


def build_cow(linewidth: float = 100e3, randomized: bool = False, mu: float = 0.5, km: float = 25.0, n_bits: int = 500) -> Setup:
    """Alice: pulsed laser → intensity modulator (COW pattern) → VOA (μ per pulse). 25 km of fiber.
    Bob: 90:10 tap → data detector DB; 10 % → DLI → DM1 (constructive), DM2 → time tagger."""
    setup = Setup("COW monitoring line", seed=9)
    laser = setup.add(Laser("LD-A", mode="pulsed", rep_rate=RATE, pulse_width=1e-9, power_dbm=-10.0,
                            linewidth=linewidth, phase_randomized=randomized))
    im = setup.add(IntensityModulator("IM-A"))
    fg = setup.add(FunctionGenerator("FG-A"))
    voa = setup.add(Attenuator("VOA-A"))
    spool = setup.add(FiberSpool("SPL", length=km * 1e3))
    tap = setup.add(BeamSplitter("TAP", ratio=0.9))
    spd = dict(efficiency=0.8, dark_count_rate=100.0, dead_time=30e-9)  # SNSPD-like
    db, dm1, dm2 = (setup.add(SinglePhotonDetector(n, **spd)) for n in ("DB", "DM1", "DM2"))
    cnt = setup.add(PhotonCounter("CNT", slots=200 * n_bits * 2))
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    setup.connect(voa.p2, spool.p1)
    setup.connect(spool.p2, tap.a1)
    setup.connect(tap.b1, db.inp)
    bs_in, bs_out, _, _ = dli(setup, "DLI")
    setup.connect(tap.b2, bs_in.a1)
    setup.connect(bs_out.b1, dm1.inp)
    setup.connect(bs_out.b2, dm2.inp)
    setup.connect(fg.ch1, im.rf)
    for d, ch in ((db, cnt.ch1), (dm1, cnt.ch2), (dm2, cnt.ch3)):
        setup.connect(d.out, ch)

    kind, slots = cow_sequence(n_bits)
    setup.cow = {"kind": kind, "slots": slots}  # Alice's record, for the analysis below
    fg.set_pattern("ch1", im.voltage_for(np.where(slots > 0, im.t_max, im.t_min)), rate=RATE)
    # μ photons per non-empty pulse leaving Alice
    net = trace(setup)
    p_out = net.port_power["SPL.p1"]
    mu_now = p_out / RATE / photon_energy(laser.wavelength) / max(slots.mean(), 1e-9)
    voa.attenuation_db = max(0.0, 10 * math.log10(mu_now / mu))
    balance(setup, "LD-A", "DM1", "DLI-ODL")
    lock(setup, "LD-A", "DM1", "DLI-PS")
    # The monitoring detectors sit behind 2 m more fiber than DB (the DLI's short arm): DB's light
    # arrives 9.8 ns earlier, so its tagger channel is delayed by as much, as one sets a tagger's input
    # delay from a histogram of arrival times. Bin k then holds pulse k on every channel.
    cnt.ch1_delay = slot_delay(setup, "LD-A", "DM1") - slot_delay(setup, "LD-A", "DB")
    print(slot_delay(setup, "LD-A", "DM1") , slot_delay(setup, "LD-A", "DB"))
    return setup


def monitor(setup: Setup, batches: int = 5, seed: int = 2) -> dict:
    """Visibility on pulse pairs that should interfere, and the data line's error rate."""
    slots, kind = setup.cow["slots"], setup.cow["kind"]
    n = slots.size
    pair = (slots > 0) & (np.roll(slots, 1) > 0)  # slot k: pulse k and pulse k−1 both present
    runner = Runner(setup, seed=seed)
    n1 = n2 = 0
    right = wrong = 0
    for _ in range(batches):
        res = runner.acquire("CNT")
        idx = (res.start + np.arange(res.slots)) % n
        dm1, dm2, db = res.clicks["ch2"], res.clicks["ch3"], res.clicks["ch1"]
        n1 += int(dm1[pair[idx]].sum())
        n2 += int(dm2[pair[idx]].sum())
        # data line: bit j = slot of the click inside its pair; decoys are not used for the key
        bit = idx // 2
        click_slot = idx % 2
        keyed = db & (kind[bit] < 2)
        right += int(np.sum(keyed & (click_slot == kind[bit])))
        wrong += int(np.sum(keyed & (click_slot != kind[bit])))
    v = (n1 - n2) / (n1 + n2) if n1 + n2 else float("nan")
    return {"V": v, "n1": n1, "n2": n2, "qber": wrong / max(right + wrong, 1), "sifted": right + wrong}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the DPSK scope view")
    args = ap.parse_args()

    T = 1 / RATE
    odl = build()["DLI-ODL"]
    print(f"DLI: cords 3 m (long arm) − 1 m (short arm) = {fmt_si(2 * FIBER_DELAY, 's')}; delay line "
          f"{fmt_si(odl.insertion_delay, 's')} + {fmt_si(odl.delay, 's')} on the dial → one slot T = {fmt_si(T, 's')}\n")
    print("1. DPSK demodulation (photodiodes, one 2 µs scope sweep)")
    for lw in (100e3, 10e6, 100e6):
        r = decode(build(linewidth=lw), timebase=200e-9)
        print(f"   linewidth {fmt_si(lw, 'Hz'):>8}: {r['slots']} slots decoded, {r['errors']} errors, "
              f"mean contrast {r['contrast']:+.3f}  (e^(−πΔνT) = {math.exp(-math.pi * lw * T):.3f})")

    print("\n2. COW monitoring line (μ = 0.5, 25 km, 10 % tap to the DLI, SNSPD-like detectors)")
    print(f"   {'source':38} {'V':>6} {'expected':>8} {'DM1':>6} {'DM2':>6}  data QBER")
    for title, kw in (
        ("coherent pulse train, 100 kHz", dict(linewidth=100e3)),
        ("coherent pulse train, 10 MHz", dict(linewidth=10e6)),
        ("coherent pulse train, 30 MHz", dict(linewidth=30e6)),
        ("no phase relation between pulses", dict(randomized=True)),
    ):
        m = monitor(build_cow(**kw))
        exp_v = 0.0 if kw.get("randomized") else math.exp(-math.pi * kw["linewidth"] * T)
        print(f"   {title:38} {m['V']:6.3f} {exp_v:8.3f} {m['n1']:6d} {m['n2']:6d}  {m['qber']:.4f} ({m['sifted']} bits)")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        res = decode(build())["result"]
        fig, axes = plt.subplots(3, 1, figsize=(8, 5.4), sharex=True)
        t = res.t * 1e9
        for ax, ch, label, scale in zip(axes, ("ch1", "ch2", "ch3"), ("constructive port [mA]", "destructive port [mA]", "bit b_k [V]"), (1e3, 1e3, 1.0)):
            ax.plot(t, res[ch].data * scale, color={"ch1": "#d4a514", "ch2": "#0e9fbf", "ch3": "#d6409f"}[ch], lw=1.2)
            ax.set_ylabel(label, fontsize=8)
            ax.grid(alpha=0.3)
        axes[0].set_title("DPSK through a delay-line interferometer: the bit leaves by the destructive port")
        axes[-1].set_xlabel("time from trigger [ns]")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
