"""What the COW parties share: public parameters, the hardware, the sifting and
visibility math.

Coherent one-way QKD (Stucki, Brunner, Gisin, Scarani & Zbinden, "Fast and simple
one-way quantum key distribution", Appl. Phys. Lett. 87, 194108 (2005),
quant-ph/0506097). Alice's CW laser is carved by an intensity modulator into equally
spaced pulses, each of mean photon number μ or empty. Logical bit k takes time slots 2k
and 2k+1:

    |0_k⟩ = |√μ⟩_{2k} |0⟩_{2k+1}
    |1_k⟩ = |0⟩_{2k} |√μ⟩_{2k+1}
    decoy = |√μ⟩_{2k} |√μ⟩_{2k+1}

bits with probability (1 − f)/2 each, decoys with probability f. The laser being CW, any
two non-empty pulses keep a fixed phase: inside a decoy, and across a bit boundary when
bit 1 is followed by bit 0. Bob's tap sends t_B of the light to the data detector DB,
whose time of arrival is the bit. The rest crosses a delay-line interferometer (DLI) one
slot unbalanced: where pulses j−1 and j are both present, only DM1 should fire in slot
j, and

    V = (p(DM1) − p(DM2)) / (p(DM1) + p(DM2))

measured there falls when an eavesdropper breaks the coherence (V_d inside decoys, V_1−0
across 1-0 bit boundaries). The paper notes that the source may also be a mode-locked
laser with a pulse picker; a gain-switched laser, whose every pulse starts from
spontaneous emission with a fresh phase, gives V = 0
(``COWParams(source="gain-switched")``).

Everything here is public: the parameters are agreed beforehand and both stations run
these functions on disclosed data. Alice's sequence and Bob's bits only pass through
them inside their owner's process. Eve's station (``eavesdropper``, ``EveParams``) and
her intercept-resend rule (``intercept``, ``resend_mu``) are here too, for ``build()``
and the reference run; nobody else knows of them.
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
from phoq.modulators import IntensityModulator, OpticalSwitch
from phoq.passive import Attenuator, Coupler, FiberSpool, OpticalDelayLine, PhaseShifter
from phoq.sources import Laser
from phoq.units import fmt_si, photon_energy

BIT0, BIT1, DECOY = 0, 1, 2  # what Alice sends in a pair of slots
NOTHING = 3  # … and what Eve may re-send instead: no light at all
DB, DM1, DM2 = (
    "ch1",
    "ch2",
    "ch3",
)  # tagger channels: data line, monitoring constructive / destructive
MON = (
    "decoy",
    "1-0",
    "other",
    "dark",
)  # pulses j−1, j both sent: inside a decoy, across a 1-0
# boundary, across any other boundary; or neither sent ("dark": a 0-1 boundary, the
# dark-count reference)
INTERFERING = (0, 1, 2)
# tagger sub-bins per slot: they resolve the half-slot pulses the modulator carves
SUB_BINS = 4


class Topic:
    """Message topics on the classical channel."""

    # Bob → Alice: bits with a data-line click (not which slot), monitoring clicks
    DETECT = "detect"
    # Alice → Bob: which detections are decoys, a sample of her bits, the monitoring
    # counts
    SIFT = "sift"
    ERRORS = "errors"  # Bob → Alice: his bits at the sample


@dataclass
class COWParams:
    # pulse clock C1: one slot τ = 2.30 ns; logical bits at half the rate
    rate: float = 434e6
    mu: float = 0.5  # photons per non-empty pulse into the fiber
    decoy: float = 0.1  # fraction f of decoy sequences
    # Bob's tap to the data line (the paper's experiment: a 50/50 coupler)
    t_b: float = 0.5
    # Alice–Bob fiber [m]: ≈ 5 dB with its splices, as in the paper
    distance: float = 25e3
    efficiency: float = 0.10  # detectors, as in the paper …
    dark_count_rate: float = 25e3  # … 2.5·10⁻⁵ dark counts per ns
    # the paper's detectors needed 10 µs against afterpulsing, not modeled
    dead_time: float = 0.0
    linewidth: float = 1e6  # Alice's laser [Hz] (a DFB; the paper does not say)
    extinction_db: float = 30.0  # intensity modulator: how dark an empty slot is
    # "cw" + carving modulator, or "gain-switched" (fresh phase every pulse)
    source: str = "cw"
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


@dataclass
class EveParams:
    """Eve's station: she buys the best parts and tells nobody."""

    # where she cuts the fiber, as a fraction of the distance from Alice
    position: float = 0.5
    # her time-of-arrival detector (a superconducting nanowire, say) …
    efficiency: float = 0.9
    dark_count_rate: float = 100.0  # … [Hz]; dead time not modeled, as for Bob's
    # her own CW laser [Hz]: Alice's wavelength, an unrelated phase
    linewidth: float = 1e6
    # her intensity modulator, better than Alice's: her re-sent pulses are
    extinction_db: float = 40.0
    # brighter, and so is their leak into the pairs she leaves empty — the only QBER she
    # adds


# ---- encoding ------------------------------------------------------------------------
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
    """Intensity-modulator transmissions at twice the pulse rate: each slot is
    (pulse or dark, dark), so a pulse fills the middle half of its slot and empty slots
    stay dark."""
    t = np.zeros(2 * occ.size)
    t[0::2] = occ
    return t


# ---- the hardware (physical layer) ---------------------------------------------------
def transmitter(setup: Setup, p: COWParams) -> Attenuator:
    """Laser → intensity modulator (AWG CH1, two symbols per slot) → VOA set to μ."""
    common = dict(wavelength=p.wavelength, power_dbm=-10.0, linewidth=p.linewidth)
    if p.source == "cw":
        laser = setup.add(Laser("ALICE-LD", mode="cw", **common))
    elif p.source == "gain-switched":
        laser = setup.add(
            Laser(
                "ALICE-LD",
                mode="pulsed",
                rep_rate=p.rate,
                pulse_width=p.pulse_width,
                phase_randomized=True,
                **common,
            )
        )
    else:
        raise ValueError(f"source must be 'cw' or 'gain-switched', not {p.source!r}")
    im = setup.add(IntensityModulator("ALICE-IM", extinction_db=p.extinction_db))
    awg = setup.add(
        FunctionGenerator(
            "ALICE-AWG",
            ch1_shape="dc",
            ch1_offset=float(im.voltage_for(1.0)),
            ch2_enabled=False,
        )
    )
    voa = setup.add(Attenuator("ALICE-VOA", attenuation_db=40.0))
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    setup.connect(awg.ch1, im.rf)
    return voa


def receiver(setup: Setup, p: COWParams, slots: int) -> Coupler:
    """Tap (t_B to DB) → DLI → DM1, DM2, and a time tagger with one bin per slot.

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
    db, dm1, dm2 = (
        setup.add(SinglePhotonDetector(n, **spd)) for n in ("DB", "DM1", "DM2")
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
    setup.connect(tap.b1, db.inp)
    setup.connect(tap.b2, bs1.a1)
    setup.connect(bs2.b1, dm1.inp)
    setup.connect(bs2.b2, dm2.inp)
    for d, ch in ((db, tagger.ch1), (dm1, tagger.ch2), (dm2, tagger.ch3)):
        setup.connect(d.out, ch)
    return tap


def eavesdropper(setup: Setup, p: COWParams, e: EveParams, slots: int) -> OpticalSwitch:
    """Eve's station on the cut fiber: a 2×2 switch (bar: Alice's light goes on to Bob
    and Eve's transmitter into her own detector; cross: Alice's light into Eve's
    detector, Eve's onto the line), a time-of-arrival detector on a tagger of her own,
    and a copy of Alice's transmitter with its own CW laser. One AWG, clocked from the
    light she receives, throws the switch (CH2) and carves her laser (CH1); its two
    channel delays, not fiber, put her pulse k where Alice's pulse k would have been
    (``calibrate_eve``). Every cord is a standard 1 m one.
    """
    sw = setup.add(OpticalSwitch("EVE-SW"))
    det = setup.add(
        SinglePhotonDetector(
            "EVE-D",
            efficiency=e.efficiency,
            dark_count_rate=e.dark_count_rate,
            dead_time=0.0,
        )
    )
    tagger = setup.add(
        PhotonCounter(
            "EVE-TAGGER",
            slots=slots,
            bin_rate=p.rate,
            oversample=SUB_BINS,
            sync_source="ALICE-LD",
        )
    )
    laser = setup.add(
        Laser(
            "EVE-TX-LD",
            mode="cw",
            wavelength=p.wavelength,
            power_dbm=-10.0,
            linewidth=e.linewidth,
        )
    )
    im = setup.add(IntensityModulator("EVE-TX-IM", extinction_db=e.extinction_db))
    voa = setup.add(Attenuator("EVE-TX-VOA", attenuation_db=40.0))
    awg = setup.add(
        FunctionGenerator(
            "EVE-AWG",
            trigger="laser",
            ch1_shape="dc",
            ch1_offset=float(im.voltage_for(0.0)),
            ch2_shape="dc",
            ch2_offset=0.0,
        )
    )
    setup.connect(sw.b2, det.inp)
    setup.connect(det.out, tagger.ch1)
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    setup.connect(voa.p2, sw.a2)
    setup.connect(awg.ch1, im.rf)
    setup.connect(awg.ch2, sw.ctrl)
    return sw


def build(
    p: COWParams | None = None,
    slots: int = 400_000,
    eve: bool | EveParams | None = None,
) -> Setup:
    """The COW hardware, calibrated: Alice's transmitter → fiber → Bob's receiver.
    ``slots`` time slots (``slots``/2 logical bits) make one block.

    With ``eve`` (True or an :class:`EveParams`) the fiber is cut at
    ``EveParams.position`` and Eve's station spliced in (``eavesdropper``), idle until
    she throws the switch. By default it is, when an Eve party has been loaded
    (``--party examples/cow/eve.py``): a party file cannot add hardware itself.
    """
    p = p or COWParams()
    if slots % 2:
        raise ValueError("slots must be even: two per logical bit")
    if eve is None:
        from phoq.parties import PARTY_REGISTRY

        eve = any(c.name == "EVE" for c in PARTY_REGISTRY)
    e = EveParams() if eve is True else eve or None
    setup = Setup("COW-QKD link")
    voa = transmitter(setup, p)
    tap = receiver(setup, p, slots)
    if e is None:
        spool = setup.add(FiberSpool("SPL", length=p.distance))
        setup.connect(voa.p2, spool.p1)
        setup.connect(spool.p2, tap.a1)
    else:
        sw = eavesdropper(setup, p, e, slots)
        spl_a = setup.add(FiberSpool("SPL-A", length=max(1.0, e.position * p.distance)))
        spl_b = setup.add(
            FiberSpool("SPL-B", length=max(1.0, (1 - e.position) * p.distance))
        )
        setup.connect(voa.p2, spl_a.p1)
        setup.connect(spl_a.p2, sw.a1)
        setup.connect(sw.b1, spl_b.p1)
        setup.connect(spl_b.p2, tap.a1)
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
    if "EVE-SW" in setup:
        calibrate_eve(setup, p)


def fiber_input(setup: Setup) -> str:
    """Where μ is defined: the input of the line (of its first span when Eve has cut
    it)."""
    return "SPL-A.p1" if "SPL-A" in setup else "SPL.p1"


def path_power(net, detector, source) -> float:
    """Power [W] of one laser reaching a detector, summed over its paths, modulator
    drives left out as in the trace (``port_power`` adds every laser, and both routes of
    a switch).
    """
    return source.power * sum(
        abs(q.amp) ** 2 for q in net.paths.get(detector, []) if q.source is source
    )


def pulse_energy(
    p: COWParams, laser: Laser, im: IntensityModulator, power: float
) -> float:
    """Photons in one non-empty pulse from traced ``power``: a CW laser carved for half
    a slot at the modulator's full transmission, or a whole gain-switched pulse."""
    power *= im.t_max  # the trace leaves the modulator's drive out
    energy = power * p.slot / 2 if laser.mode == "cw" else power / laser.rep_rate
    return energy / photon_energy(laser.wavelength)


def pulse_photons(setup: Setup, p: COWParams, port: str | None = None) -> float:
    """Mean photon number of one of Alice's non-empty pulses entering ``port``
    (the fiber input)."""
    power = trace(setup).port_power[port or fiber_input(setup)]
    return pulse_energy(p, setup["ALICE-LD"], setup["ALICE-IM"], power)


def calibrate_mu(setup: Setup, p: COWParams) -> float:
    """Alice's VOA, set so that a non-empty pulse holds μ photons at the fiber input."""
    voa = setup["ALICE-VOA"]
    voa.attenuation_db = min(
        80.0,
        max(0.0, voa.attenuation_db + 10 * math.log10(pulse_photons(setup, p) / p.mu)),
    )
    return voa.attenuation_db


def arms(setup: Setup, detector: str = "DM1") -> tuple:
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
    visibility."""
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
    """The phase shifter set so that neighboring pulses of equal phase interfere
    constructively on DM1 (the paper's interferometer is temperature-tuned to this
    point)."""
    ps = setup["BOB-PS"]
    ps.phase = 0.0
    short, long_ = arms(setup)
    ps.phase = float(
        (np.angle(static_amplitude(long_)) - np.angle(static_amplitude(short)))
        % (2 * math.pi)
    )
    return ps.phase


def align_tagger(setup: Setup) -> float:
    """DB sits behind less fiber than the DLI: delay its channel so slot j means pulse j
    everywhere."""
    net, src = trace(setup), setup["ALICE-LD"]
    tagger = setup["TAGGER"]
    tagger.ch1_delay = min(net.delays(setup["DM1"], src)) - min(
        net.delays(setup["DB"], src)
    )
    return tagger.ch1_delay


def passing(net, detector, source, part) -> float:
    """When light that ``source`` emits at t = 0 passes ``part``
    (a modulator or a switch) on its earliest path to ``detector``."""
    path = min(
        (q for q in net.paths[detector] if q.source is source), key=lambda q: q.delay
    )
    return next(at for mod, at in path.mods if getattr(mod, "switch", mod) is part)


def eve_pulse_photons(setup: Setup, p: COWParams) -> float:
    """Photons that one of Eve's non-empty pulses brings to her own detector, through
    the switch's bar state (her loopback)."""
    laser = setup["EVE-TX-LD"]
    return pulse_energy(
        p, laser, setup["EVE-TX-IM"], path_power(trace(setup), setup["EVE-D"], laser)
    )


def calibrate_eve(setup: Setup, p: COWParams) -> None:
    """Eve's station before she goes live.

    * Her VOA, set through the switch's bar state so that a pulse at full modulator
      transmission brings μ photons to her own detector: the μ she reads off her clicks
      and the intensity she re-sends then refer to one point, and the switch's loss, the
      same on both routes, cancels.
    * Her AWG's CH2 delay: the moment Alice's pulse 0 reaches the switch, so symbol k of
      the switch pattern takes Alice's pulse k (the clock she recovers from Alice's
      light, delayed).
    * Its CH1 delay: her pulse k leaves her modulator so that it reaches Bob when
      Alice's pulse k would have — through the switch at the same moment, hence in the
      same slot of every one of Bob's detectors.
    """
    voa, awg = setup["EVE-TX-VOA"], setup["EVE-AWG"]
    voa.attenuation_db = min(
        80.0,
        max(
            0.0,
            voa.attenuation_db + 10 * math.log10(eve_pulse_photons(setup, p) / p.mu),
        ),
    )
    net, alice, eve = trace(setup), setup["ALICE-LD"], setup["EVE-TX-LD"]
    db = setup["DB"]
    awg.ch2_delay = passing(net, setup["EVE-D"], alice, setup["EVE-SW"])
    awg.ch1_delay = (
        min(net.delays(db, alice))
        - min(net.delays(db, eve))
        + passing(net, db, eve, setup["EVE-TX-IM"])
    )


# ---- the data line and the monitoring line -------------------------------------------
def decode(
    db: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, int]:
    """Bob's bits from the data detector: a click in the first slot of a pair is 0, in
    the second 1; a click in both gets a random bit. Returns (bit indices with a click,
    bits, doubles).
    """
    c0, c1 = db[0::2], db[1::2]
    idx = np.flatnonzero(c0 | c1)
    both = c0[idx] & c1[idx]
    bits = (c1[idx] & ~c0[idx]).astype(np.int8)
    bits[both] = rng.integers(0, 2, size=int(both.sum()), dtype=np.int8)
    return idx, bits, int(both.sum())


def interfering(kind: np.ndarray) -> np.ndarray:
    """Per time slot j: the class (index into MON) of pulses j−1 and j at the DLI's
    output, −1 when only one of them was sent. Slot 0 is skipped: its predecessor
    belongs to another block.
    """
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
    """(raw, net) visibility over the given classes. Net subtracts each detector's
    dark-count probability per slot, measured where neither neighboring pulse was sent
    (the paper's net V).
    """
    m = np.asarray(monitor, dtype=float)
    n1, n2, slots = m[list(rows)].sum(axis=0)
    dark = m[MON.index("dark")]
    d1, d2 = (dark[:2] / dark[2]) if dark[2] else (0.0, 0.0)
    return visibility(n1, n2), visibility(n1 - d1 * slots, n2 - d2 * slots)


# ---- the eavesdropper: intercept-resend in the time basis ----------------------------
INCONCLUSIVE = (
    "empty",
    "decoy",
)  # what Eve re-sends for a pair of slots where her detector stayed dark


def intercept(
    c0: np.ndarray, c1: np.ndarray, attack: np.ndarray, inconclusive: str = "empty"
) -> np.ndarray:
    """What Eve re-prepares per logical bit from her clicks in its two slots: the bit
    she saw, a decoy when both slots clicked (she knows what the sequence was, as the
    paper assumes), and, when neither did, nothing (``"empty"``) or a decoy
    (``"decoy"``). NOTHING where she let the bit pass."""
    if inconclusive not in INCONCLUSIVE:
        raise ValueError(
            f"inconclusive must be one of {INCONCLUSIVE}, not {inconclusive!r}"
        )
    out = np.full(attack.size, NOTHING, dtype=np.int8)
    out[attack & c0 & ~c1] = BIT0
    out[attack & c1 & ~c0] = BIT1
    out[attack & c0 & c1] = DECOY
    if inconclusive == "decoy":
        out[attack & ~c0 & ~c1] = DECOY
    return out


def resend_mu(
    clicks: int, attacked: int, resent: int, efficiency: float, p: COWParams
) -> float:
    """Photons per re-sent pulse (at her detector, the point her transmitter is
    calibrated to) that give Bob the photon flux the attacked pairs carried. Her clicks
    per occupied slot q give the μ reaching her detector, 1 − e^{−η μ} = q; that flux,
    (1 + f)·μ per pair on average, is spread over the pulses she re-sends. With empty
    inconclusive pairs that is μ/q: brighter, rarer pulses.
    """
    # occupied slots among the attacked pairs, on average
    pulses = attacked * (1 + p.decoy)
    if not clicks or not resent:
        return p.mu
    q = min(clicks / pulses, 0.999)
    return -math.log1p(-q) / efficiency * pulses / resent


def resend_attenuation(att0: float, mu_e: float, p: COWParams) -> float:
    """Eve's VOA for ``mu_e`` photons per pulse, from ``att0``, its setting for μ."""
    return float(np.clip(att0 - 10 * math.log10(mu_e / p.mu), 0.0, 80.0))


# ---- what both stations count --------------------------------------------------------
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
    monitor: np.ndarray = field(
        default_factory=lambda: np.zeros((len(MON), 3), dtype=np.int64)
    )

    def __iadd__(self, other: Tally) -> Tally:
        for name in (
            "bits",
            "detected",
            "decoys",
            "doubles",
            "sifted",
            "sampled",
            "errors",
            "key_bits",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.monitor = self.monitor + other.monitor
        return self

    @property
    def qber(self) -> float:
        return self.errors / self.sampled if self.sampled else float("nan")

    def v(self, which: str | None = None, net: bool = True) -> float:
        """Visibility over all interfering pairs, or one class of MON; net of dark
        counts or raw."""
        rows = INTERFERING if which is None else (MON.index(which),)
        return visibilities(self.monitor, rows)[1 if net else 0]


def account(
    bits: int,
    n_detected: int,
    decoy: np.ndarray,
    doubles: int,
    sample: np.ndarray,
    mine: np.ndarray,
    theirs: np.ndarray,
    monitor: np.ndarray,
) -> Tally:
    """One block's public counts, once both stations hold each other's sample bits."""
    sifted = int(n_detected - decoy.sum())
    return Tally(
        bits=bits,
        detected=int(n_detected),
        decoys=int(decoy.sum()),
        doubles=doubles,
        sifted=sifted,
        sampled=int(sample.size),
        errors=int(np.count_nonzero(np.asarray(mine) != np.asarray(theirs))),
        key_bits=sifted - int(sample.size),
        monitor=np.asarray(monitor, dtype=np.int64),
    )


def fmt(x, digits: int = 3) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}g}"


def describe(t: Tally) -> str:
    """One line with the figures of merit, for the consoles."""
    return (
        f"sifted {t.sifted:,} of {t.bits:,} bits · QBER {fmt(t.qber)}"
        f" ({t.errors}/{t.sampled}) · V raw {fmt(t.v(net=False))}, net {fmt(t.v())}"
        f" (decoy {fmt(t.v('decoy'))}, 1-0 {fmt(t.v('1-0'))})"
    )


# blocks in the running ("now") figures: a change on the line shows within them
WINDOW = 10


def window(recent) -> Tally:
    """The sum of the last blocks' tallies."""
    total = Tally()
    for t in recent:
        total += t
    return total


def readouts(
    t: Tally, p: COWParams, net_stats: dict | None = None, recent: Tally | None = None
) -> dict:
    """Live read-outs for a station's laptop (and the result dict of a headless run).
    ``recent``, the last ``WINDOW`` blocks, goes first: the laptop's screen shows the
    first three entries, and running figures show an eavesdropper come and go where the
    totals barely move."""
    out = {}
    if recent is not None:
        out["QBER now"], out["V_d now"], out["V_1-0 now"] = (
            recent.qber,
            recent.v("decoy"),
            recent.v("1-0"),
        )
    out |= {
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
        "DM dark / slot": (
            t.monitor[MON.index("dark"), :2].sum()
            / max(2 * t.monitor[MON.index("dark"), 2], 1)
        ),
        "sifted / bit": t.sifted / t.bits if t.bits else float("nan"),
        "sifted rate [bit/s]": (
            t.sifted / t.bits * p.rate / 2 if t.bits else float("nan")
        ),
    }
    if net_stats:
        out["sent [kB]"] = net_stats["bytes_out"] / 1e3
        out["received [kB]"] = net_stats["bytes_in"] / 1e3
    return out


# ---- what to expect ------------------------------------------------------------------
def theory(setup: Setup, p: COWParams) -> dict:
    """Expected figures from the calibrated hardware.

    μ_B, the photons per pulse reaching DB, follows from the traced losses. A pulse slot
    clicks with P_A = 1 − (1 − p_d)·e^{−η μ_B}, its empty partner with P_B = 1 −
    (1 − p_d)·e^{−η ε μ_B} (ε: the modulator's leak, twice as long in a dark slot as in
    a carved pulse), so per bit

        detected = 1 − (1 − P_A)(1 − P_B),
        QBER = [P_B (1 − P_A) + ½ P_A P_B] / detected,

    the paper's R ≈ μ t t_B η and Q ≈ p_d / (R + 2 p_d) for small numbers; a decoy
    clicks with 1 − (1 − P_A)². The net visibility is the coherence of neighboring
    pulses, e^{−πΔντ}, times the arms' balance 2|a₁a₂|/(|a₁|² + |a₂|²), zero for a
    gain-switched source. Where two pulses meet, DM1 sees m₊ = m₁ + m₂ + 2√(m₁m₂)·V_net
    photons and DM2 m₋ (the minus sign); dark counts make the raw visibility
    (P₊ − P₋)/(P₊ + P₋) with P± = 1 − (1 − p_d)·e^{−η m±}. With Eve's station on the
    line (idle) only Alice's light is counted, through the switch's bar state.
    """
    net = trace(setup)
    im, alice = setup["ALICE-IM"], setup["ALICE-LD"]
    # photons per pulse per watt of traced power
    scale = p.mu / net.port_power[fiber_input(setup)]
    mu_b = scale * path_power(net, setup["DB"], alice)
    p_d = -math.expm1(-p.dark_count_rate * p.slot)
    leak = im.t_min / im.t_max * (2.0 if p.source == "cw" else 1.0)
    pa = 1 - (1 - p_d) * math.exp(-p.efficiency * mu_b)
    pb = 1 - (1 - p_d) * math.exp(-p.efficiency * leak * mu_b)
    det = 1 - (1 - pa) * (1 - pb)
    # amplitudes without the drive
    m1, m2 = (scale * alice.power * abs(q.amp) ** 2 for q in arms(setup))
    balance = 2 * math.sqrt(m1 * m2) / (m1 + m2)
    v = balance * (
        math.exp(-math.pi * p.linewidth * p.slot) if p.source == "cw" else 0.0
    )
    plus, minus = (
        1 - (1 - p_d) * math.exp(-p.efficiency * (m1 + m2) * (1 + sign * v))
        for sign in (1, -1)
    )
    return {
        "mu_B": mu_b,
        "detected / bit": (1 - p.decoy) * det + p.decoy * (1 - (1 - pa) ** 2),
        "sifted / bit": (1 - p.decoy) * det,
        "QBER": (pb * (1 - pa) + 0.5 * pa * pb) / det,
        "V net": v,
        "V raw": visibility(plus, minus),
        "R (paper)": p.efficiency * mu_b,
        "DLI balance": balance,
    }


def eve_theory(setup: Setup, p: COWParams, fraction: float) -> dict:
    """What intercept-resend on a fraction p_IR of the bits should leave, for Eve as in
    ``eve.py`` (the bit she saw, a decoy when both slots clicked, nothing when neither
    did, the flux matched by ``resend_mu``).

    Her detector clicks on one of Alice's pulses with probability q. Matched in flux, an
    attacked pair brings Bob as much light as one left alone, but of an attacked decoy
    only the share she recognized and re-sent as a decoy, q, is coherent — with her own
    laser, V_E over one slot through Bob's interferometer. A 1-0 boundary stays coherent
    where both bits were left alone, or both re-sent by her:

        V_d = (1 − p_IR)·V + p_IR·c·V_E,
        V_1−0 = (1 − p_IR)²·V + 2p_IR(1 − p_IR)·√x·V + p_IR²·c·V_E,

    c = q + 2(1 − q)·√ε_E. Two small coherent terms come from leaks: a pulse she
    re-sends alone has her modulator's leak, amplitude √ε_E of it, in the slot next to
    it, from the same laser; and Alice's pulse that the switch takes still reaches Bob
    through the bar state with amplitude √x (the crosstalk), coherent with a neighbor
    left alone.

    Stucki et al. estimate the loss of visibility per decoy event instead of per photon,
    1 − V = I·ξ, with I = p_IR (every detection Bob gets in an attacked pair is a bit
    Eve re-sent) and ξ = 2e^{−μt}/(1 + e^{−μt}) the chance that she sees one pulse of a
    decoy and not the other, here 2(1 − q)/(2 − q). Weighted by photons the drop is
    p_IR·(V − c·V_E) ≈ p_IR·(1 − q); the two agree for small q.

    The data line: Eve's flux-matched pulses, μ_E = μ/q at her detector, reach DB as
    μ_B/q, her errors come from clicks q₁ on Alice's empty slots, and the pairs she
    leaves empty carry her modulator's leak 2ε_E·μ_B/q per slot: Bob's detections and
    QBER follow as in ``theory``, mixed with the bits she let pass. The ``detected /
    bit``, ``sifted / bit`` and ``QBER`` returned are those under attack.
    """
    th, net = theory(setup, p), trace(setup)
    det, alice, p_ir, f_d = setup["EVE-D"], setup["ALICE-LD"], fraction, p.decoy
    # Alice's pulse at Eve's detector
    mu_d = p.mu / net.port_power[fiber_input(setup)] * path_power(net, det, alice)
    leak_a, leak_e = (
        2 * im.t_min / im.t_max for im in (setup["ALICE-IM"], setup["EVE-TX-IM"])
    )  # a dark slot
    p_de = -math.expm1(-det.dark_count_rate * p.slot)
    q = 1 - (1 - p_de) * math.exp(-det.efficiency * mu_d)  # she clicks on a pulse …
    # … and on an empty slot
    q1 = 1 - (1 - p_de) * math.exp(-det.efficiency * leak_a * mu_d)
    resent = (1 - f_d) * (q + q1) + f_d * 2 * q  # pulses she re-sends per attacked pair
    q_hat = resent / (1 + f_d)  # her clicks per occupied slot: what resend_mu inverts
    mu_e = -math.log1p(-q_hat) / det.efficiency / q_hat
    p_d = -math.expm1(-p.dark_count_rate * p.slot)
    # one of her pulses at DB: the loopback reference holds for her light too
    s = th["mu_B"] / mu_d * mu_e
    pa, pb = (1 - (1 - p_d) * math.exp(-p.efficiency * n) for n in (s, leak_e * s))
    det_bit, err_bit = 1 - (1 - pa) * (1 - pb), pb * (1 - pa) + 0.5 * pa * pb
    det_dec, det_empty = 1 - (1 - pa) ** 2, 1 - (1 - pb) ** 2
    right, wrong, both, none = (
        q * (1 - q1),
        (1 - q) * q1,
        q * q1,
        (1 - q) * (1 - q1),
    )  # her outcomes on a bit
    sift_att = (right + wrong) * det_bit + both * det_dec + none * det_empty
    err_att = (
        right * err_bit
        + wrong * (det_bit - err_bit)
        + 0.5 * (both * det_dec + none * det_empty)
    )
    # an attacked decoy
    decoy_att = 2 * q * (1 - q) * det_bit + q * q * det_dec + (1 - q) ** 2 * det_empty
    sift0, decoy0 = (
        th["sifted / bit"] / (1 - f_d),
        (th["detected / bit"] - th["sifted / bit"]) / f_d,
    )
    sift = (1 - p_ir) * sift0 + p_ir * sift_att
    v, v_e = th["V net"], th["DLI balance"] * math.exp(
        -math.pi * setup["EVE-TX-LD"].linewidth * p.slot
    )
    im_e, sw = setup["EVE-TX-IM"], setup["EVE-SW"]
    # coherent share of a pair she attacked
    c = q + 2 * (1 - q) * math.sqrt(im_e.t_min / im_e.t_max)
    # amplitude of Alice's diverted pulse through the bar state
    x = 10 ** (-sw.crosstalk_db / 20)
    xi = 2 * (1 - q) / (2 - q)
    return {
        "mu at Eve": mu_d,
        "q (Eve)": q,
        "mu_E": mu_e,
        "V_E": v_e,
        "V decoy": (1 - p_ir) * v + p_ir * c * v_e,
        "V 1-0": (
            (1 - p_ir) ** 2 * v + 2 * p_ir * (1 - p_ir) * x * v + p_ir**2 * c * v_e
        ),
        "xi": xi,
        "1 - V (paper)": p_ir * xi,
        "detected / bit": (
            (1 - f_d) * sift + f_d * ((1 - p_ir) * decoy0 + p_ir * decoy_att)
        ),
        "sifted / bit": (1 - f_d) * sift,
        "QBER": ((1 - p_ir) * sift0 * th["QBER"] + p_ir * err_att) / sift,
    }
