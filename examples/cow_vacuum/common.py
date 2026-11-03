"""What the COW parties share: public parameters, the hardware, the per-pair events, the
tally and what to expect from the bench.

Coherent one-way QKD with a vacuum decoy sequence (Gao, Xie, Gu, Liu, Weng, Li, Yin &
Chen, "Simple security proof of coherent-one-way quantum key distribution",
arXiv:2107.09329v5, 2023). Alice's CW laser is carved by an intensity modulator into
equally spaced pulses, each of mean photon number μ or empty. Pair k takes time slots 2k
and 2k+1 (the paper's time points 2k−1 and 2k, counted from one) and holds one of four
states:

    bit 0        |0⟩|α⟩
    bit 1        |α⟩|0⟩
    decoy 1      |α⟩|α⟩
    decoy 2      |0⟩|0⟩     the paper's addition to the original protocol

Bob's tap sends t_B of the light to the data detector D_T, whose time of arrival is the
bit: T_0 is the slot where bit 0 has its pulse, the second of the pair, and T_1 the
first. (Sec. III of the paper calls T_0 the earlier moment. Its states in Sec. II and
its Eq. (4) need it this way; only the label differs.) The rest crosses a delay-line
interferometer (DLI) one slot unbalanced to D_M0, the constructive port, and D_M1.

The monitoring line is read in one window per pair, its second slot, where the pair's
first pulse arrives through the long arm and its second through the short one. The light
that a passive DLI puts in the other windows meets the neighboring pairs' pulses and is
not used, so the window holds half of the light the paper's gains assume
(``bound.paper_model``). The proof only asks for the measured gains, so these windows
are taken as a monitoring line with that extra loss; that is this package's reading, not
the paper's.

Everything here is public: the parameters are agreed beforehand and both stations run
these functions on disclosed data. Alice's sequence and Bob's bits only pass through
them inside their owner's process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np

from phoq.core import Setup
from phoq.detectors import SinglePhotonDetector
from phoq.engine import trace
from phoq.generators import ArbitraryWaveformGenerator
from phoq.instruments import PhotonCounter
from phoq.modulators import IntensityModulator
from phoq.passive import Attenuator, Coupler, FiberSpool, OpticalDelayLine, PhaseShifter
from phoq.sources import Laser
from phoq.units import fmt_si, photon_energy

from . import bound
from .bound import Gains, phase_error, terms

BIT0, BIT1, DECOY1, DECOY2 = 0, 1, 2, 3  # what Alice sends in a pair of slots
STATES = (BIT0, BIT1, DECOY1, DECOY2)  # in the order of bound.Gains
# tagger channels: data line, monitoring constructive / destructive
DT, DM0, DM1 = "ch1", "ch2", "ch3"
# a pair's event: the window Bob keeps, or none
NONE, T0, T1, M0, M1 = -1, 0, 1, 2, 3
SUB_BINS = 2  # tagger sub-bins per slot, one per half-slot symbol of the modulator
FIBER_INPUT = "SPL.p1"  # where μ is defined


class Topic:
    """Message topics on the classical channel."""

    # Bob → Alice: the pairs with a data-line event (not which slot), and the pairs with
    # a monitoring event and their detector
    DETECT = "detect"
    # Alice → Bob: which data-line events are decoys, a sample of her bits, the gains
    SIFT = "sift"
    ERRORS = "errors"  # Bob → Alice: his bits at the sample


@dataclass
class COWParams:
    rate: float = 434e6  # pulse clock: one slot τ = 2.30 ns, as in examples/cow
    # photons per non-empty pulse into the fiber: near the best μ on this bench
    mu: float = 1e-3
    decoy1: float = 0.1  # fraction of |α⟩|α⟩ pairs …
    decoy2: float = 0.1  # … and of |0⟩|0⟩ pairs
    # Bob's tap to the data line (a standard 50/50 coupler; the paper's model: 0.47)
    t_b: float = 0.5
    distance: float = 25e3  # Alice–Bob fiber [m]
    efficiency: float = 0.8  # η_d, as in the paper's simulation …
    p_d: float = 1e-8  # … and its dark-count probability per window
    dead_time: float = 0.0  # not modeled, as in the paper
    linewidth: float = 1e6  # Alice's laser [Hz] (a DFB; the paper does not say)
    extinction_db: float = 30.0  # intensity modulator: how dark an empty slot is
    sample_fraction: float = 0.1  # sifted bits disclosed to estimate E_b
    wavelength: float = 1550e-9

    @property
    def slot(self) -> float:
        """Time between pulses = the DLI's imbalance = one tagger bin [s]."""
        return 1.0 / self.rate

    @property
    def probabilities(self) -> tuple[float, float, float, float]:
        bit = (1 - self.decoy1 - self.decoy2) / 2
        return (bit, bit, self.decoy1, self.decoy2)

    @property
    def dark_count_rate(self) -> float:
        """The detectors' dark-count rate [Hz] that gives p_d per slot."""
        return -math.log1p(-self.p_d) * self.rate


# ---- encoding ------------------------------------------------------------------------
def draw(rng: np.random.Generator, pairs: int, p: COWParams) -> np.ndarray:
    """Alice's private sequence: one of the four states for each pair."""
    return rng.choice(len(STATES), size=pairs, p=p.probabilities).astype(np.int8)


def occupied(kind: np.ndarray) -> np.ndarray:
    """Which time slots carry a pulse (two slots per pair)."""
    occ = np.zeros(2 * kind.size, dtype=bool)
    occ[0::2] = (kind == BIT1) | (kind == DECOY1)
    occ[1::2] = (kind == BIT0) | (kind == DECOY1)
    return occ


def carving(occ: np.ndarray) -> np.ndarray:
    """Intensity-modulator transmissions at twice the pulse rate: each slot is
    (pulse or dark, dark), so a pulse fills half of its slot and empty slots stay dark.
    """
    t = np.zeros(2 * occ.size)
    t[0::2] = occ
    return t


# ---- the hardware (physical layer) ---------------------------------------------------
def transmitter(setup: Setup, p: COWParams) -> Attenuator:
    """CW laser → intensity modulator (AWG CH1, two symbols per slot) → attenuators.

    A fixed 30 dB stands ahead of the VOA set to μ: the VOA's 80 dB alone would stop at
    μ ≈ 2.6·10⁻³. The AWG's delay moves each pulse an eighth of a slot off center, so
    that no pulse edge falls on the center of one of the tagger's two sub-bins per slot,
    which is where the field is sampled.
    """
    laser = setup.add(
        Laser(
            "ALICE-LD",
            mode="cw",
            wavelength=p.wavelength,
            power_dbm=-10.0,
            linewidth=p.linewidth,
        )
    )
    im = setup.add(IntensityModulator("ALICE-IM", extinction_db=p.extinction_db))
    awg = setup.add(
        ArbitraryWaveformGenerator(
            "ALICE-AWG",
            ch1_shape="dc",
            ch1_offset=float(im.voltage_for(1.0)),
            ch1_delay=p.slot / 8,
            ch2_enabled=False,
        )
    )
    att = setup.add(Attenuator("ALICE-ATT", attenuation_db=30.0))
    voa = setup.add(Attenuator("ALICE-VOA", attenuation_db=40.0))
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, att.p1)
    setup.connect(att.p2, voa.p1)
    setup.connect(awg.ch1, im.rf)
    return voa


def receiver(setup: Setup, p: COWParams, slots: int) -> Coupler:
    """Tap (t_B to D_T) → DLI → D_M0, D_M1, and a time tagger with one bin per slot.

    The DLI's long arm must be one slot (0.47 m of fiber at 434 MHz) longer. Standard
    cords give 0.5 m: 1 m + VOA + 1 m against 0.5 m + phase shifter + 0.5 m + delay line
    + 0.5 m, so the delay line sits in the short arm and takes back the 145 ps the cords
    give too much; the VOA evens out the arms' losses.
    """
    tap = setup.add(Coupler("BOB-TAP", ratio=p.t_b))
    bs1, bs2 = setup.add(Coupler("BOB-BS1"), Coupler("BOB-BS2"))
    ps, odl = setup.add(PhaseShifter("BOB-PS"), OpticalDelayLine("BOB-ODL"))
    voa = setup.add(Attenuator("BOB-VOA", attenuation_db=0.0))
    setup.connect(bs1.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, odl.p1, length=0.5)
    setup.connect(odl.p2, bs2.a1, length=0.5)
    setup.connect(bs1.b2, voa.p1, length=1.0)
    setup.connect(voa.p2, bs2.a2, length=1.0)
    spd = dict(
        efficiency=p.efficiency,
        dark_count_rate=p.dark_count_rate,
        dead_time=p.dead_time,
    )
    dt, dm0, dm1 = (
        setup.add(SinglePhotonDetector(n, **spd)) for n in ("DT", "DM0", "DM1")
    )
    tagger = setup.add(
        PhotonCounter(
            "TAGGER",
            slots=slots,
            bin_rate=p.rate,
            oversample=SUB_BINS,
            sync_source="ALICE-LD",
        )
    )
    setup.connect(tap.b1, dt.inp)
    setup.connect(tap.b2, bs1.a1)
    setup.connect(bs2.b1, dm0.inp)
    setup.connect(bs2.b2, dm1.inp)
    for d, ch in ((dt, tagger.ch1), (dm0, tagger.ch2), (dm1, tagger.ch3)):
        setup.connect(d.out, ch)
    return tap


def build(p: COWParams | None = None, slots: int = 400_000) -> Setup:
    """The hardware, calibrated: Alice's transmitter → fiber → Bob's receiver. ``slots``
    time slots (``slots``/2 pairs) make one block."""
    p = p or COWParams()
    if slots % 2:
        raise ValueError("slots must be even: two per pair")
    setup = Setup("COW-QKD link, vacuum decoys")
    voa = transmitter(setup, p)
    tap = receiver(setup, p, slots)
    spool = setup.add(FiberSpool("SPL", length=p.distance))
    setup.connect(voa.p2, spool.p1)
    setup.connect(spool.p2, tap.a1)
    setup.terminate_spares()  # the tap's and the DLI's spare inputs
    calibrate(setup, p)
    return setup


# ---- calibration: what a bench needs before a run ------------------------------------
def calibrate(setup: Setup, p: COWParams) -> None:
    calibrate_mu(setup, p)
    balance_delay(setup, p)
    balance_arms(setup)
    lock(setup)
    align_tagger(setup)


def path_power(net, detector, source) -> float:
    """Power [W] of one laser reaching a detector, summed over its paths, modulator
    drives left out as in the trace."""
    return source.power * sum(
        abs(q.amp) ** 2 for q in net.paths.get(detector, []) if q.source is source
    )


def pulse_photons(setup: Setup, p: COWParams) -> float:
    """Mean photon number of one of Alice's non-empty pulses at the fiber input: the CW
    laser carved for half a slot at the modulator's full transmission (the trace leaves
    the modulator's drive out)."""
    power = trace(setup).port_power[FIBER_INPUT] * setup["ALICE-IM"].t_max
    return power * p.slot / 2 / photon_energy(setup["ALICE-LD"].wavelength)


def calibrate_mu(setup: Setup, p: COWParams) -> float:
    """Alice's VOA, set so that a non-empty pulse holds μ photons at the fiber input."""
    voa = setup["ALICE-VOA"]
    voa.attenuation_db = min(
        80.0,
        max(0.0, voa.attenuation_db + 10 * math.log10(pulse_photons(setup, p) / p.mu)),
    )
    return voa.attenuation_db


def arms(setup: Setup, detector: str = "DM0") -> tuple:
    """The short- and long-arm paths of Alice's light to a monitoring detector."""
    src = setup["ALICE-LD"]
    paths = sorted(
        (
            q
            for q in trace(setup).paths[setup[detector]]
            if q.source is src and not q.reflections
        ),
        key=lambda q: q.delay,
    )
    return paths[0], paths[1]


def balance_delay(setup: Setup, p: COWParams) -> float:
    """The delay line (in the short arm) set so the long arm is exactly one slot
    longer."""
    odl = setup["BOB-ODL"]
    short, long_ = arms(setup)
    want = odl.setting + (long_.delay - short.delay) - p.slot
    if not 0.0 <= want <= odl.max_delay:
        raise ValueError(
            f"BOB-ODL would need {fmt_si(want, 's')}, outside its travel: change the"
            " DLI's cords"
        )
    odl.delay = want
    return want


def balance_arms(setup: Setup) -> float:
    """The long arm's VOA set to the short arm's loss: equal amplitudes, full
    interference."""
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
    """The phase shifter set so that a pair's two pulses interfere constructively on
    D_M0."""
    ps = setup["BOB-PS"]
    ps.phase = 0.0
    short, long_ = arms(setup)
    ps.phase = float(
        (np.angle(static_amplitude(long_)) - np.angle(static_amplitude(short)))
        % (2 * math.pi)
    )
    return ps.phase


def align_tagger(setup: Setup) -> float:
    """D_T sits behind less fiber than the DLI: delay its channel so slot j means pulse
    j everywhere."""
    net, src = trace(setup), setup["ALICE-LD"]
    tagger = setup["TAGGER"]
    tagger.ch1_delay = min(net.delays(setup["DM0"], src)) - min(
        net.delays(setup["DT"], src)
    )
    return tagger.ch1_delay


# ---- the data line and the monitoring line -------------------------------------------
def events(clicks: dict, rng: np.random.Generator) -> tuple[np.ndarray, int]:
    """One event per pair from its four windows: D_T in T_0 or T_1, D_M0 or D_M1 in the
    pair's second slot. If several clicked, Bob takes one of them at random, the paper's
    squashing rule; the monitoring detectors' other windows are not used. Returns
    (NONE, T0, T1, M0 or M1 per pair; pairs where several clicked).
    """
    w = np.stack(
        (clicks[DT][1::2], clicks[DT][0::2], clicks[DM0][1::2], clicks[DM1][1::2])
    )
    n = w.sum(axis=0)
    out = np.full(n.size, NONE, dtype=np.int8)
    one, many = np.flatnonzero(n == 1), np.flatnonzero(n > 1)
    out[one] = np.argmax(w[:, one], axis=0)
    out[many] = np.argmax(w[:, many] * rng.random((w.shape[0], many.size)), axis=0)
    return out, int(many.size)


def announcements(event: np.ndarray) -> tuple:
    """Bob's reading of the events: (pairs with a data-line event, his bits there, pairs
    with a monitoring event, their detector, 0 for D_M0 and 1 for D_M1)."""
    idx = np.flatnonzero((event == T0) | (event == T1))
    mon = np.flatnonzero(event >= M0)
    return (
        idx,
        (event[idx] == T1).astype(np.int8),
        mon,
        (event[mon] - M0).astype(np.int8),
    )


def monitor_table(kind: np.ndarray, pairs: np.ndarray, detector: np.ndarray):
    """Per state sent: [D_M0 events, D_M1 events, pairs sent]."""
    out = np.zeros((len(STATES), 3), dtype=np.int64)
    sent = kind[np.asarray(pairs, dtype=np.int64)]
    for d in (0, 1):
        out[:, d] = np.bincount(sent[np.asarray(detector) == d], minlength=len(STATES))
    out[:, 2] = np.bincount(kind, minlength=len(STATES))
    return out


def gains_of(monitor: np.ndarray) -> Gains:
    """The gains counted in a monitor table."""
    m = np.asarray(monitor, dtype=float)
    return Gains(*(tuple(m[s, :2] / max(m[s, 2], 1.0)) for s in STATES))


def counting_error(monitor: np.ndarray, mu: float) -> float:
    """±1σ of E_p^u from the counts alone: each of the eight counts moved by its Poisson
    σ, and by one count when it is zero, where the reading would otherwise look exact.
    The paper's bound is asymptotic; this is no finite-key term, only a measure of how
    far a run is from that limit.
    """
    base = phase_error(mu, gains_of(monitor))
    total = 0.0
    for s, d in np.ndindex(len(STATES), 2):
        m = np.array(monitor, dtype=float)
        m[s, d] += max(1.0, math.sqrt(m[s, d]))
        total += (phase_error(mu, gains_of(m)) - base) ** 2
    return math.sqrt(total)


# ---- what both stations count --------------------------------------------------------
@dataclass
class Tally:
    """Public counts both stations accumulate."""

    pairs: int = 0  # pairs sent
    detected: int = 0  # pairs with a data-line event
    decoys: int = 0  # … removed as decoys
    multi: int = 0  # pairs where several windows clicked
    sifted: int = 0  # data-line events on a bit
    sampled: int = 0  # sifted bits disclosed for E_b …
    errors: int = 0  # … and wrong
    key_bits: int = 0  # sifted bits not disclosed
    monitor: np.ndarray = field(
        default_factory=lambda: np.zeros((len(STATES), 3), dtype=np.int64)
    )

    def __iadd__(self, other: Tally) -> Tally:
        for name in (
            "pairs",
            "detected",
            "decoys",
            "multi",
            "sifted",
            "sampled",
            "errors",
            "key_bits",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.monitor = self.monitor + other.monitor
        return self

    @property
    def e_b(self) -> float:
        return self.errors / self.sampled if self.sampled else float("nan")

    @property
    def q(self) -> float:
        """The gain Q: sifted bits per bit pair sent."""
        bits = self.monitor[BIT0, 2] + self.monitor[BIT1, 2]
        return self.sifted / bits if bits else float("nan")

    @property
    def gains(self) -> Gains:
        return gains_of(self.monitor)

    @property
    def visibility(self) -> float:
        """Of the |α,α⟩ pairs at D_M0 and D_M1: for debugging the DLI, not in the
        bound."""
        n0, n1 = self.monitor[DECOY1, :2]
        return (n0 - n1) / (n0 + n1) if n0 + n1 else float("nan")

    def phase_error(self, mu: float) -> float:
        return phase_error(mu, self.gains)

    def counting_error(self, mu: float) -> float:
        return counting_error(self.monitor, mu)


def account(
    pairs: int,
    n_detected: int,
    decoy: np.ndarray,
    multi: int,
    sample: np.ndarray,
    mine: np.ndarray,
    theirs: np.ndarray,
    monitor: np.ndarray,
) -> Tally:
    """One block's public counts, once both stations hold each other's sample bits."""
    sifted = int(n_detected - decoy.sum())
    return Tally(
        pairs=pairs,
        detected=int(n_detected),
        decoys=int(decoy.sum()),
        multi=multi,
        sifted=sifted,
        sampled=int(sample.size),
        errors=int(np.count_nonzero(np.asarray(mine) != np.asarray(theirs))),
        key_bits=sifted - int(sample.size),
        monitor=np.asarray(monitor, dtype=np.int64),
    )


def fmt(x, digits: int = 3) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}g}"


def describe(t: Tally, p: COWParams) -> str:
    """One line with the figures of merit, for the consoles."""
    return (
        f"sifted {t.sifted:,} of {t.pairs:,} pairs · E_b {fmt(t.e_b)}"
        f" ({t.errors}/{t.sampled}) · E_p^u {fmt(t.phase_error(p.mu))}"
        f" ± {fmt(t.counting_error(p.mu), 2)}"
    )


def readouts(t: Tally, p: COWParams, net_stats: dict | None = None) -> dict:
    """Live read-outs for a station's laptop (and the result dict of a headless run).
    The laptop's screen shows the first three entries. They are running totals: a block
    holds too few monitoring clicks for a reading of its own to mean anything."""
    split = terms(p.mu, t.gains)
    m = t.monitor
    out = {
        "E_p^u": t.phase_error(p.mu),
        "E_p^u ±": t.counting_error(p.mu),
        "E_b (sampled)": t.e_b,
        "sifted bits": t.sifted,
        "key bits": t.key_bits,
        "pairs sent": t.pairs,
        "data-line events": t.detected,
        "decoys removed": t.decoys,
        "Q": t.q,
        "sifted rate [bit/s]": (
            t.sifted / t.pairs * p.rate / 2 if t.pairs else float("nan")
        ),
        "E_p^u from |β,β⟩": split["beta"],
        "E_p^u from M0": split["M0"],
        "E_p^u from M1": split["M1"],
        "|α,α⟩ at D_M0": int(m[DECOY1, 0]),
        "|α,α⟩ at D_M1": int(m[DECOY1, 1]),
        "|0,0⟩ at D_M0": int(m[DECOY2, 0]),
        "|0,0⟩ at D_M1": int(m[DECOY2, 1]),
        "V (not in the bound)": t.visibility,
    }
    if net_stats:
        out["sent [kB]"] = net_stats["bytes_out"] / 1e3
        out["received [kB]"] = net_stats["bytes_in"] / 1e3
    return out


# ---- what to expect ------------------------------------------------------------------
class Port(NamedTuple):
    """A monitoring detector behind the DLI: detected photons per photon at the fiber
    input, in the pair's window."""

    short: float  # of the pair's second pulse, through the short arm
    long: float  # of its first pulse, through the long arm
    cross: float  # Re(long* · short): +√(short·long) on a constructive port


def windows(
    first: bool,
    second: bool,
    mu: float,
    tau: float,
    ports: tuple[Port, Port],
    leak: float,
    coherence: float,
    p_d: float,
) -> list[float]:
    """Click probabilities in a pair's four windows (T_0, T_1, M0, M1), which of its
    pulses being sent. A slot is a half that is carved (μ photons, or the modulator's
    leak ε·μ) and a half that is always dark (ε·μ). The carved halves of the two pulses
    meet at the DLI's output, and so do the dark halves."""
    a, b, dark = (mu if first else leak * mu), (mu if second else leak * mu), leak * mu
    photons = [tau * (b + dark), tau * (a + dark)]
    for port in ports:
        cross = 2 * coherence * port.cross
        photons.append(
            port.long * a
            + port.short * b
            + cross * math.sqrt(a * b)
            + (port.long + port.short + cross) * dark
        )
    return [1 - (1 - p_d) * math.exp(-n) for n in photons]


def one_event(clicks: list[float]) -> list[float]:
    """The probability that a pair's event is each window, under the rule of ``events``:
    of the windows that clicked, one at random."""
    n = len(clicks)
    out = [0.0] * n
    for mask in range(1, 2**n):
        fired = [i for i in range(n) if mask >> i & 1]
        w = math.prod(c if mask >> i & 1 else 1 - c for i, c in enumerate(clicks))
        for i in fired:
            out[i] += w / len(fired)
    return out


def pair_gains(
    mu: float,
    tau: float,
    ports: tuple[Port, Port],
    leak: float = 0.0,
    coherence: float = 1.0,
    p_d: float = 1e-8,
) -> tuple[Gains, float, float]:
    """(Gains, Q, E_b) of a bench: ``tau`` detected photons on the data line per photon
    at the fiber input, ``ports`` the two monitoring detectors, ``leak`` the modulator's
    t_min/t_max and ``coherence`` the fringe contrast of the laser over one slot."""
    args = (mu, tau, ports, leak, coherence, p_d)
    e0, e1, e_a, e_v = (
        one_event(windows(first, second, *args))
        for first, second in (
            (False, True),
            (True, False),
            (True, True),
            (False, False),
        )
    )
    right, wrong = (e0[T0] + e1[T1]) / 2, (e0[T1] + e1[T0]) / 2
    gains = Gains(
        (e0[M0], e0[M1]), (e1[M0], e1[M1]), (e_a[M0], e_a[M1]), (e_v[M0], e_v[M1])
    )
    return gains, right + wrong, wrong / (right + wrong)


def monitoring_port(setup: Setup, detector: str, per_watt: float, eta: float) -> Port:
    """A monitoring detector as traced: the arms' losses and the DLI's phase."""
    alice = setup["ALICE-LD"]
    short, long_ = arms(setup, detector)
    s, l = (eta * per_watt * alice.power * abs(q.amp) ** 2 for q in (short, long_))
    phase = np.angle(static_amplitude(long_)) - np.angle(static_amplitude(short))
    return Port(s, l, math.sqrt(s * l) * math.cos(phase))


def theory(setup: Setup, p: COWParams) -> dict:
    """Expected figures from the calibrated hardware.

    The traced losses give the detected photons per photon at the fiber input, on the
    data line and through each arm of the DLI, whose phase comes from the trace too. The
    laser's phase walk over one slot keeps a fringe contrast e^{−πΔντ}. ``windows`` and
    ``one_event`` turn these into the gains, and Eq. (7) into E_p^u.
    """
    net, alice, im = trace(setup), setup["ALICE-LD"], setup["ALICE-IM"]
    # photons at the fiber input per watt of traced power
    per_watt = 1 / net.port_power[FIBER_INPUT]
    tau = p.efficiency * per_watt * path_power(net, setup["DT"], alice)
    ports = tuple(
        monitoring_port(setup, d, per_watt, p.efficiency) for d in ("DM0", "DM1")
    )
    leak = im.t_min / im.t_max
    coherence = math.exp(-math.pi * alice.linewidth * p.slot)
    g, q, e_b = pair_gains(p.mu, tau, ports, leak, coherence, p.p_d)
    n0, n1 = g.qaa
    return {
        "tau": tau,
        "ports": ports,
        "leak": leak,
        "coherence": coherence,
        "gains": g,
        "Q": q,
        "E_b": e_b,
        "E_p^u": phase_error(p.mu, g),
        "terms": terms(p.mu, g),
        "sifted / pair": (1 - p.decoy1 - p.decoy2) * q,
        "V": (n0 - n1) / (n0 + n1),
    }


def best_mu(setup: Setup, p: COWParams) -> float:
    """The μ that maximizes Eq. (6) on this calibrated bench. It falls with the
    transmittance, so another distance or modulator needs its own."""
    th = theory(setup, p)
    return bound.best_mu(
        lambda mu: pair_gains(
            mu, th["tau"], th["ports"], th["leak"], th["coherence"], p.p_d
        )
    )[0]
