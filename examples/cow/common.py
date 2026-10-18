"""What the COW parties share: public parameters, the hardware, the sifting and visibility maths.

Coherent one-way QKD (Stucki, Brunner, Gisin, Scarani & Zbinden, "Fast and simple one-way quantum
key distribution", Appl. Phys. Lett. 87, 194108 (2005), quant-ph/0506097). Alice's CW laser is
carved by an intensity modulator into equally spaced pulses, each of mean photon number μ or
empty. Logical bit k takes time slots 2k and 2k+1:

    |0_k⟩ = |√μ⟩_{2k} |0⟩_{2k+1}     |1_k⟩ = |0⟩_{2k} |√μ⟩_{2k+1}     decoy = |√μ⟩_{2k} |√μ⟩_{2k+1}

bits with probability (1 − f)/2 each, decoys with probability f. The laser being CW, any two
non-empty pulses keep a fixed phase: inside a decoy, and across a bit boundary when bit 1 is
followed by bit 0. Bob's tap sends t_B of the light to the data detector DB, whose time of arrival
is the bit. The rest crosses a delay-line interferometer (DLI) one slot unbalanced: where pulses
j−1 and j are both present, only DM1 should fire in slot j, and

    V = (p(DM1) − p(DM2)) / (p(DM1) + p(DM2))

measured there falls when an eavesdropper breaks the coherence (V_d inside decoys, V_1−0 across
1-0 bit boundaries). The paper notes that the source may also be a mode-locked laser with a
pulse picker; a gain-switched laser, whose every pulse starts from spontaneous emission with a
fresh phase, gives V = 0 (``COWParams(source="gain-switched")``).

Everything here is public: the parameters are agreed beforehand and both stations run these
functions on disclosed data. Alice's sequence and Bob's bits only pass through them inside their
owner's process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from phoq.core import Setup
from phoq.detectors import SinglePhotonDetector
from phoq.engine import trace
from phoq.generators import FunctionGenerator
from phoq.instruments import PhotonCounter
from phoq.modulators import IntensityModulator
from phoq.passive import Attenuator, BeamSplitter, FiberSpool, OpticalDelayLine, PhaseShifter
from phoq.sources import Laser
from phoq.units import fmt_si, photon_energy

BIT0, BIT1, DECOY = 0, 1, 2  # what Alice sends in a pair of slots
DB, DM1, DM2 = "ch1", "ch2", "ch3"  # tagger channels: data line, monitoring constructive / destructive
MON = ("decoy", "1-0", "other", "dark")  # pulses j−1, j both sent: inside a decoy, across a 1-0
# boundary, across any other boundary; or neither sent ("dark": a 0-1 boundary, the dark-count reference)
INTERFERING = (0, 1, 2)
SUB_BINS = 4  # tagger sub-bins per slot: they resolve the half-slot pulses the modulator carves


class Topic:
    """Message topics on the classical channel."""

    DETECT = "detect"  # Bob → Alice: bits with a data-line click (not which slot), monitoring clicks
    SIFT = "sift"  # Alice → Bob: which detections are decoys, a sample of her bits, the monitoring counts
    ERRORS = "errors"  # Bob → Alice: his bits at the sample


@dataclass
class COWParams:
    rate: float = 434e6  # pulse clock C1: one slot τ = 2.30 ns; logical bits at half the rate
    mu: float = 0.5  # photons per non-empty pulse into the fiber
    decoy: float = 0.1  # fraction f of decoy sequences
    t_b: float = 0.5  # Bob's tap to the data line (the paper's experiment: a 50/50 coupler)
    distance: float = 25e3  # Alice–Bob fiber [m]: ≈ 5 dB with its splices, as in the paper
    efficiency: float = 0.10  # detectors, as in the paper …
    dark_count_rate: float = 25e3  # … 2.5·10⁻⁵ dark counts per ns
    dead_time: float = 0.0  # the paper's detectors needed 10 µs against afterpulsing, not modelled
    linewidth: float = 1e6  # Alice's laser [Hz] (a DFB; the paper does not say)
    extinction_db: float = 30.0  # intensity modulator: how dark an empty slot is
    source: str = "cw"  # "cw" + carving modulator, or "gain-switched" (fresh phase every pulse)
    pulse_width: float = 300e-12  # gain-switched pulses only
    sample_fraction: float = 0.1  # sifted bits disclosed to estimate the QBER
    wavelength: float = 1550e-9

    @property
    def slot(self) -> float:
        """Time between pulses = the DLI's imbalance = one tagger bin [s]."""
        return 1.0 / self.rate

    @property
    def probabilities(self) -> tuple[float, float, float]:
        return ((1 - self.decoy) / 2, (1 - self.decoy) / 2, self.decoy)


# ---- encoding ----------------------------------------------------------------------------------------
def draw(rng: np.random.Generator, bits: int, p: COWParams) -> np.ndarray:
    """Alice's private sequence: BIT0, BIT1 or DECOY for each logical bit."""
    return rng.choice(3, size=bits, p=p.probabilities).astype(np.int8)


def occupied(kind: np.ndarray) -> np.ndarray:
    """Which time slots carry a pulse (two slots per logical bit)."""
    occ = np.zeros(2 * kind.size, dtype=bool)
    occ[0::2] = (kind == BIT0) | (kind == DECOY)
    occ[1::2] = (kind == BIT1) | (kind == DECOY)
    return occ


def carving(occ: np.ndarray) -> np.ndarray:
    """Intensity-modulator transmissions at twice the pulse rate: each slot is (pulse or dark,
    dark), so a pulse fills the middle half of its slot and empty slots stay dark."""
    t = np.zeros(2 * occ.size)
    t[0::2] = occ
    return t


# ---- the hardware (physical layer) ------------------------------------------------------------------
def transmitter(setup: Setup, p: COWParams) -> Attenuator:
    """Laser → intensity modulator (AWG CH1, two symbols per slot) → VOA set to μ."""
    common = dict(wavelength=p.wavelength, power_dbm=-10.0, linewidth=p.linewidth)
    if p.source == "cw":
        laser = setup.add(Laser("ALICE-LD", mode="cw", **common))
    elif p.source == "gain-switched":
        laser = setup.add(Laser("ALICE-LD", mode="pulsed", rep_rate=p.rate, pulse_width=p.pulse_width,
                                phase_randomized=True, **common))
    else:
        raise ValueError(f"source must be 'cw' or 'gain-switched', not {p.source!r}")
    im = setup.add(IntensityModulator("ALICE-IM", extinction_db=p.extinction_db))
    awg = setup.add(FunctionGenerator("ALICE-AWG", ch1_shape="dc", ch1_offset=float(im.voltage_for(1.0)),
                                      ch2_enabled=False))
    voa = setup.add(Attenuator("ALICE-VOA", attenuation_db=40.0))
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    setup.connect(awg.ch1, im.rf)
    return voa


def receiver(setup: Setup, p: COWParams, slots: int) -> BeamSplitter:
    """Tap (t_B to DB) → DLI → DM1, DM2, and a time tagger with one bin per slot.

    The DLI's long arm must be one slot (0.47 m of fiber at 434 MHz) longer. Standard cords give
    0.5 m: 1 m + VOA + 1 m against 0.5 m + phase shifter + 0.5 m + delay line + 0.5 m, so the delay
    line sits in the short arm and takes back the 145 ps the cords give too much; the VOA evens
    out the arms' losses.
    """
    tap = setup.add(BeamSplitter("BOB-TAP", ratio=p.t_b))
    bs1, bs2 = setup.add(BeamSplitter("BOB-BS1"), BeamSplitter("BOB-BS2"))
    ps, odl = setup.add(PhaseShifter("BOB-PS"), OpticalDelayLine("BOB-ODL"))
    voa = setup.add(Attenuator("BOB-VOA", attenuation_db=0.0))
    setup.connect(bs1.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, odl.p1, length=0.5)
    setup.connect(odl.p2, bs2.a1, length=0.5)
    setup.connect(bs1.b2, voa.p1, length=1.0)
    setup.connect(voa.p2, bs2.a2, length=1.0)
    spd = dict(efficiency=p.efficiency, dark_count_rate=p.dark_count_rate, dead_time=p.dead_time)
    db, dm1, dm2 = (setup.add(SinglePhotonDetector(n, **spd)) for n in ("DB", "DM1", "DM2"))
    tagger = setup.add(PhotonCounter("TAGGER", slots=slots, bin_rate=p.rate, oversample=SUB_BINS,
                                     sync_source="ALICE-LD"))
    setup.connect(tap.b1, db.inp)
    setup.connect(tap.b2, bs1.a1)
    setup.connect(bs2.b1, dm1.inp)
    setup.connect(bs2.b2, dm2.inp)
    for d, ch in ((db, tagger.ch1), (dm1, tagger.ch2), (dm2, tagger.ch3)):
        setup.connect(d.out, ch)
    return tap


def build(p: COWParams | None = None, slots: int = 400_000) -> Setup:
    """The COW hardware, calibrated: Alice's transmitter → fiber → Bob's receiver. ``slots`` time
    slots (``slots``/2 logical bits) make one block."""
    p = p or COWParams()
    if slots % 2:
        raise ValueError("slots must be even: two per logical bit")
    setup = Setup("COW-QKD link")
    voa = transmitter(setup, p)
    tap = receiver(setup, p, slots)
    spool = setup.add(FiberSpool("SPL", length=p.distance))
    setup.connect(voa.p2, spool.p1)
    setup.connect(spool.p2, tap.a1)
    calibrate(setup, p)
    return setup


# ---- calibration: what a bench needs before a run -----------------------------------------------------
def calibrate(setup: Setup, p: COWParams) -> None:
    calibrate_mu(setup, p)
    balance_delay(setup, p)
    balance_arms(setup)
    lock(setup)
    align_tagger(setup)


def pulse_photons(setup: Setup, p: COWParams, port: str = "SPL.p1") -> float:
    """Mean photon number of one non-empty pulse entering ``port``: a CW laser carved for half a
    slot at the modulator's full transmission, or a whole gain-switched pulse."""
    im, laser = setup["ALICE-IM"], setup["ALICE-LD"]
    power = trace(setup).port_power[port] * im.t_max  # the trace leaves the modulator's drive out
    energy = power * p.slot / 2 if p.source == "cw" else power / laser.rep_rate
    return energy / photon_energy(laser.wavelength)


def calibrate_mu(setup: Setup, p: COWParams) -> float:
    """Alice's VOA, set so that a non-empty pulse holds μ photons at the fiber input."""
    voa = setup["ALICE-VOA"]
    voa.attenuation_db = min(80.0, max(0.0, voa.attenuation_db + 10 * math.log10(pulse_photons(setup, p) / p.mu)))
    return voa.attenuation_db


def arms(setup: Setup, detector: str = "DM1") -> tuple:
    """The short- and long-arm paths of Alice's light to a monitoring detector."""
    src = setup["ALICE-LD"]
    paths = sorted((q for q in trace(setup).paths[setup[detector]] if q.source is src), key=lambda q: q.delay)
    return paths[0], paths[1]


def balance_delay(setup: Setup, p: COWParams) -> float:
    """The delay line (in the short arm) set so the long arm is exactly one slot longer."""
    odl = setup["BOB-ODL"]
    short, long_ = arms(setup)
    want = odl.setting + (long_.delay - short.delay) - p.slot
    if not 0.0 <= want <= odl.max_delay:
        raise ValueError(f"BOB-ODL would need {fmt_si(want, 's')}, outside its travel: change the DLI's cords")
    odl.delay = want
    return want


def balance_arms(setup: Setup) -> float:
    """The long arm's VOA set to the short arm's loss: equal amplitudes, full visibility."""
    voa = setup["BOB-VOA"]
    voa.attenuation_db = 0.0
    short, long_ = arms(setup)
    voa.attenuation_db = max(0.0, 20 * math.log10(abs(long_.amp) / abs(short.amp)))
    return voa.attenuation_db


def static_amplitude(path) -> complex:
    """A path's field amplitude with every modulator on it at its static response."""
    amp = complex(path.amp)
    for mod, _ in path.mods:
        amp *= complex(np.asarray(mod.response([mod.static_voltage()]))[0])
    return amp


def lock(setup: Setup) -> float:
    """The phase shifter set so that neighbouring pulses of equal phase interfere constructively
    on DM1 (the paper's interferometer is temperature-tuned to this point)."""
    ps = setup["BOB-PS"]
    ps.phase = 0.0
    short, long_ = arms(setup)
    ps.phase = float((np.angle(static_amplitude(long_)) - np.angle(static_amplitude(short))) % (2 * math.pi))
    return ps.phase


def align_tagger(setup: Setup) -> float:
    """DB sits behind less fiber than the DLI: delay its channel so slot j means pulse j everywhere."""
    net, src = trace(setup), setup["ALICE-LD"]
    tagger = setup["TAGGER"]
    tagger.ch1_delay = min(net.delays(setup["DM1"], src)) - min(net.delays(setup["DB"], src))
    return tagger.ch1_delay


# ---- the data line and the monitoring line --------------------------------------------------------------
def decode(db: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, int]:
    """Bob's bits from the data detector: a click in the first slot of a pair is 0, in the second
    1; a click in both gets a random bit. Returns (bit indices with a click, bits, doubles)."""
    c0, c1 = db[0::2], db[1::2]
    idx = np.flatnonzero(c0 | c1)
    both = c0[idx] & c1[idx]
    bits = (c1[idx] & ~c0[idx]).astype(np.int8)
    bits[both] = rng.integers(0, 2, size=int(both.sum()), dtype=np.int8)
    return idx, bits, int(both.sum())


def interfering(kind: np.ndarray) -> np.ndarray:
    """Per time slot j: the class (index into MON) of pulses j−1 and j at the DLI's output, −1 when
    only one of them was sent. Slot 0 is skipped: its predecessor belongs to another block."""
    occ = occupied(kind)
    cls = np.full(occ.size, -1, dtype=np.int8)
    both, neither = np.zeros(occ.size, dtype=bool), np.zeros(occ.size, dtype=bool)
    both[1:] = occ[1:] & occ[:-1]
    neither[1:] = ~occ[1:] & ~occ[:-1]
    j = np.arange(occ.size)
    k = j // 2
    inside = both & (j % 2 == 1)  # slots 2k, 2k+1: only a decoy has both
    across = both & (j % 2 == 0)  # slots 2k−1, 2k
    one_zero = across & (kind[np.maximum(k - 1, 0)] == BIT1) & (kind[k] == BIT0)
    cls[inside] = 0
    cls[one_zero] = 1
    cls[across & ~one_zero] = 2
    cls[neither] = 3
    return cls


def monitor_counts(kind: np.ndarray, dm1: np.ndarray, dm2: np.ndarray) -> np.ndarray:
    """Per class of MON: [DM1 clicks, DM2 clicks, slots]."""
    cls = interfering(kind)
    out = np.zeros((len(MON), 3), dtype=np.int64)
    for d, clicks in enumerate((dm1, dm2)):
        c = cls[np.asarray(clicks, dtype=np.int64)]
        out[:, d] = np.bincount(c[c >= 0], minlength=len(MON))
    out[:, 2] = np.bincount(cls[cls >= 0], minlength=len(MON))
    return out


def visibility(n1: float, n2: float) -> float:
    return (n1 - n2) / (n1 + n2) if n1 + n2 else float("nan")


def visibilities(monitor: np.ndarray, rows=INTERFERING) -> tuple[float, float]:
    """(raw, net) visibility over the given classes. Net subtracts each detector's dark-count
    probability per slot, measured where neither neighbouring pulse was sent (the paper's net V)."""
    m = np.asarray(monitor, dtype=float)
    n1, n2, slots = m[list(rows)].sum(axis=0)
    dark = m[MON.index("dark")]
    d1, d2 = (dark[:2] / dark[2]) if dark[2] else (0.0, 0.0)
    return visibility(n1, n2), visibility(n1 - d1 * slots, n2 - d2 * slots)


# ---- what both stations count -------------------------------------------------------------------------
@dataclass
class Tally:
    """Public counts both stations accumulate."""

    bits: int = 0  # logical bits sent
    detected: int = 0  # bits with a data-line click
    decoys: int = 0  # … removed as decoys
    doubles: int = 0  # … clicked in both slots
    sifted: int = 0  # detected bits that are not decoys
    sampled: int = 0  # sifted bits disclosed for the QBER …
    errors: int = 0  # … and wrong
    key_bits: int = 0  # sifted bits not disclosed
    monitor: np.ndarray = field(default_factory=lambda: np.zeros((len(MON), 3), dtype=np.int64))

    def __iadd__(self, other: Tally) -> Tally:
        for name in ("bits", "detected", "decoys", "doubles", "sifted", "sampled", "errors", "key_bits"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.monitor = self.monitor + other.monitor
        return self

    @property
    def qber(self) -> float:
        return self.errors / self.sampled if self.sampled else float("nan")

    def v(self, which: str | None = None, net: bool = True) -> float:
        """Visibility over all interfering pairs, or one class of MON; net of dark counts or raw."""
        rows = INTERFERING if which is None else (MON.index(which),)
        return visibilities(self.monitor, rows)[1 if net else 0]


def account(bits: int, n_detected: int, decoy: np.ndarray, doubles: int, sample: np.ndarray,
            mine: np.ndarray, theirs: np.ndarray, monitor: np.ndarray) -> Tally:
    """One block's public counts, once both stations hold each other's sample bits."""
    sifted = int(n_detected - decoy.sum())
    return Tally(bits=bits, detected=int(n_detected), decoys=int(decoy.sum()), doubles=doubles,
                 sifted=sifted, sampled=int(sample.size),
                 errors=int(np.count_nonzero(np.asarray(mine) != np.asarray(theirs))),
                 key_bits=sifted - int(sample.size), monitor=np.asarray(monitor, dtype=np.int64))


def fmt(x, digits: int = 3) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}g}"


def describe(t: Tally) -> str:
    """One line with the figures of merit, for the consoles."""
    return (f"sifted {t.sifted:,} of {t.bits:,} bits · QBER {fmt(t.qber)} ({t.errors}/{t.sampled}) · "
            f"V raw {fmt(t.v(net=False))}, net {fmt(t.v())} (decoy {fmt(t.v('decoy'))}, 1-0 {fmt(t.v('1-0'))})")


def readouts(t: Tally, p: COWParams, net_stats: dict | None = None) -> dict:
    """Live read-outs for a station's laptop (and the result dict of a headless run)."""
    out = {
        "bits sent": t.bits,
        "detections": t.detected,
        "decoys removed": t.decoys,
        "sifted bits": t.sifted,
        "key bits": t.key_bits,
        "QBER (sampled)": t.qber,
        "V raw": t.v(net=False),
        "V net": t.v(),
        "V decoy (net)": t.v("decoy"),
        "V 1-0 (net)": t.v("1-0"),
        "DM dark / slot": t.monitor[MON.index("dark"), :2].sum() / max(2 * t.monitor[MON.index("dark"), 2], 1),
        "sifted / bit": t.sifted / t.bits if t.bits else float("nan"),
        "sifted rate [bit/s]": t.sifted / t.bits * p.rate / 2 if t.bits else float("nan"),
    }
    if net_stats:
        out["sent [kB]"] = net_stats["bytes_out"] / 1e3
        out["received [kB]"] = net_stats["bytes_in"] / 1e3
    return out


# ---- what to expect -------------------------------------------------------------------------------------
def theory(setup: Setup, p: COWParams) -> dict:
    """Expected figures from the calibrated hardware.

    μ_B, the photons per pulse reaching DB, follows from the traced losses. A pulse slot clicks
    with P_A = 1 − (1 − p_d)·e^{−η μ_B}, its empty partner with P_B = 1 − (1 − p_d)·e^{−η ε μ_B}
    (ε: the modulator's leak, twice as long in a dark slot as in a carved pulse), so per bit

        detected = 1 − (1 − P_A)(1 − P_B),   QBER = [P_B (1 − P_A) + ½ P_A P_B] / detected,

    the paper's R ≈ μ t t_B η and Q ≈ p_d / (R + 2 p_d) for small numbers; a decoy clicks with
    1 − (1 − P_A)². The net visibility is the coherence of neighbouring pulses, e^{−πΔντ}, times the
    arms' balance 2|a₁a₂|/(|a₁|² + |a₂|²), zero for a gain-switched source. Where two pulses meet,
    DM1 sees m₊ = m₁ + m₂ + 2√(m₁m₂)·V_net photons and DM2 m₋ (the minus sign); dark counts make the
    raw visibility (P₊ − P₋)/(P₊ + P₋) with P± = 1 − (1 − p_d)·e^{−η m±}.
    """
    net = trace(setup)
    im = setup["ALICE-IM"]
    scale = p.mu / net.port_power["SPL.p1"]  # photons per pulse per watt of traced power
    mu_b = scale * net.port_power["DB.inp"]
    p_d = -math.expm1(-p.dark_count_rate * p.slot)
    leak = im.t_min / im.t_max * (2.0 if p.source == "cw" else 1.0)
    pa = 1 - (1 - p_d) * math.exp(-p.efficiency * mu_b)
    pb = 1 - (1 - p_d) * math.exp(-p.efficiency * leak * mu_b)
    det = 1 - (1 - pa) * (1 - pb)
    power = setup["ALICE-LD"].power  # the traced amplitudes leave the modulator's drive out
    m1, m2 = (scale * power * abs(q.amp) ** 2 for q in arms(setup))
    v = 2 * math.sqrt(m1 * m2) / (m1 + m2) * (math.exp(-math.pi * p.linewidth * p.slot) if p.source == "cw" else 0.0)
    plus, minus = (1 - (1 - p_d) * math.exp(-p.efficiency * (m1 + m2) * (1 + sign * v)) for sign in (1, -1))
    return {
        "mu_B": mu_b,
        "detected / bit": (1 - p.decoy) * det + p.decoy * (1 - (1 - pa) ** 2),
        "sifted / bit": (1 - p.decoy) * det,
        "QBER": (pb * (1 - pa) + 0.5 * pa * pb) / det,
        "V net": v,
        "V raw": visibility(plus, minus),
        "R (paper)": p.efficiency * mu_b,
    }
