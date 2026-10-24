"""A delay-line interferometer as the monitoring line of coherent one-way QKD (COW).

    # visibility and data-line QBER at four sources
    uv run python examples/dli_for_cow.py
    # the bench in 3D
    uv run phoq serve examples/dli_for_cow.py

COW sends each bit as a pulse in one of two time slots, 0 → (μ, 0) and 1 → (0, μ), with
a fraction of decoys (μ, μ). Bob's tap sends most of the light to the data detector DB,
whose time of arrival is the bit; the rest goes through the DLI (examples/dli.py), which
checks that neighboring non-empty pulses, across bit boundaries and inside decoys, are
still coherent. Where pulses k−1 and k were both sent, only DM1 should fire; the
visibility V = (n₁ − n₂)/(n₁ + n₂) counted there is the coherence e^{−πΔν·T}, and a drop
reveals an attack that re-prepares pulses. The data line's error rate does not see
coherence at all. The full protocol, with Alice's and Bob's parties, is the examples/cow
package.

In the lab, the tagger counts DB (CH1), DM1 (CH2) and DM2 (CH3): DM1 runs well ahead of
DM2. Raise LD-A's linewidth to 30 MHz, or tick "Random phase per pulse", and DM2 catches
up while DB does not move.
"""

import math
import sys
from pathlib import Path

import numpy as np

from phoq import (
    Attenuator,
    Coupler,
    FiberSpool,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    PhotonCounter,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import trace
from phoq.units import FIBER_DELAY, fmt_si, photon_energy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dli import (
    RATE,
    balance,
    dli,
    lock,
    slot_delay,
)  # noqa: E402  (the shared interferometer)


def cow_sequence(
    n_bits: int, decoy: float = 0.1, seed: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Slot intensities (0/1) for n_bits logical bits: 0 → (μ, 0), 1 → (0, μ), decoy →
    (μ, μ)."""
    rng = np.random.default_rng(seed)
    kind = np.where(rng.random(n_bits) < decoy, 2, rng.integers(0, 2, n_bits))
    slots = np.zeros(2 * n_bits)
    slots[0::2] = (kind == 0) | (kind == 2)
    slots[1::2] = (kind == 1) | (kind == 2)
    return kind, slots


def build(
    linewidth: float = 100e3,
    randomized: bool = False,
    mu: float = 0.5,
    km: float = 25.0,
    n_bits: int = 500,
) -> Setup:
    """Alice: pulsed laser → intensity modulator (COW pattern) → VOA (μ per pulse). 25
    km of fiber. Bob: 90:10 tap → data detector DB; 10 % → DLI → DM1 (constructive), DM2
    → time tagger.
    """
    setup = Setup("Delay-line interferometer: COW monitoring line", seed=9)
    laser = setup.add(
        Laser(
            "LD-A",
            mode="pulsed",
            rep_rate=RATE,
            pulse_width=1e-9,
            power_dbm=-10.0,
            linewidth=linewidth,
            phase_randomized=randomized,
        )
    )
    im = setup.add(IntensityModulator("IM-A"))
    fg = setup.add(FunctionGenerator("FG-A"))
    voa = setup.add(Attenuator("VOA-A"))
    spool = setup.add(FiberSpool("SPL", length=km * 1e3))
    tap = setup.add(Coupler("TAP", ratio=0.9))
    spd = dict(efficiency=0.8, dark_count_rate=100.0, dead_time=30e-9)  # SNSPD-like
    db, dm1, dm2 = (
        setup.add(SinglePhotonDetector(n, **spd)) for n in ("DB", "DM1", "DM2")
    )
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
    setup.terminate_spares()  # the tap's and the DLI's spare inputs

    kind, slots = cow_sequence(n_bits)
    setup.cow = {"kind": kind, "slots": slots}  # Alice's record, for the analysis below
    fg.set_pattern(
        "ch1", im.voltage_for(np.where(slots > 0, im.t_max, im.t_min)), rate=RATE
    )
    # μ photons per non-empty pulse leaving Alice
    net = trace(setup)
    p_out = net.port_power["SPL.p1"]
    mu_now = p_out / RATE / photon_energy(laser.wavelength) / max(slots.mean(), 1e-9)
    voa.attenuation_db = max(0.0, 10 * math.log10(mu_now / mu))
    balance(setup, "LD-A", "DM1", "DLI-ODL")
    lock(setup, "LD-A", "DM1", "DLI-PS")
    # The monitoring detectors sit behind 2 m more fiber than DB (the DLI's short arm):
    # DB's light arrives 9.8 ns earlier, so its tagger channel is delayed by as much, as
    # one sets a tagger's input delay from a histogram of arrival times. Bin k then
    # holds pulse k on every channel.
    cnt.ch1_delay = slot_delay(setup, "LD-A", "DM1") - slot_delay(setup, "LD-A", "DB")
    return setup


def monitor(setup: Setup, batches: int = 5, seed: int = 2) -> dict:
    """Visibility on pulse pairs that should interfere, and the data line's error
    rate."""
    slots, kind = setup.cow["slots"], setup.cow["kind"]
    n = slots.size
    pair = (slots > 0) & (
        np.roll(slots, 1) > 0
    )  # slot k: pulse k and pulse k−1 both present
    runner = Runner(setup, seed=seed)
    n1 = n2 = 0
    right = wrong = 0
    for _ in range(batches):
        res = runner.acquire("CNT")
        idx = (res.start + np.arange(res.slots)) % n
        dm1, dm2, db = res.clicks["ch2"], res.clicks["ch3"], res.clicks["ch1"]
        n1 += int(dm1[pair[idx]].sum())
        n2 += int(dm2[pair[idx]].sum())
        # data line: bit j = slot of the click inside its pair; decoys are not used for
        # the key
        bit = idx // 2
        click_slot = idx % 2
        keyed = db & (kind[bit] < 2)
        right += int(np.sum(keyed & (click_slot == kind[bit])))
        wrong += int(np.sum(keyed & (click_slot != kind[bit])))
    v = (n1 - n2) / (n1 + n2) if n1 + n2 else float("nan")
    return {
        "V": v,
        "n1": n1,
        "n2": n2,
        "qber": wrong / max(right + wrong, 1),
        "sifted": right + wrong,
    }


if __name__ == "__main__":
    T = 1 / RATE
    odl = build()["DLI-ODL"]
    print(
        f"DLI: cords 3 m (long arm) − 1 m (short arm) = {fmt_si(2 * FIBER_DELAY, 's')};"
        f" delay line {fmt_si(odl.insertion_delay, 's')} + {fmt_si(odl.delay, 's')} on"
        f" the dial → one slot T = {fmt_si(T, 's')}\n"
    )
    print(
        "COW monitoring line (μ = 0.5, 25 km, 10 % tap to the DLI, SNSPD-like"
        " detectors)"
    )
    print(f"   {'source':38} {'V':>6} {'expected':>8} {'DM1':>6} {'DM2':>6}  data QBER")
    for title, kw in (
        ("coherent pulse train, 100 kHz", dict(linewidth=100e3)),
        ("coherent pulse train, 10 MHz", dict(linewidth=10e6)),
        ("coherent pulse train, 30 MHz", dict(linewidth=30e6)),
        ("no phase relation between pulses", dict(randomized=True)),
    ):
        m = monitor(build(**kw))
        exp_v = (
            0.0 if kw.get("randomized") else math.exp(-math.pi * kw["linewidth"] * T)
        )
        print(
            f"   {title:38} {m['V']:6.3f} {exp_v:8.3f} {m['n1']:6d} {m['n2']:6d} "
            f" {m['qber']:.4f} ({m['sifted']} bits)"
        )
