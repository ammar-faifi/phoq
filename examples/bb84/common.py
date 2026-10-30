"""What the BB84 parties share: public parameters, the hardware, the decoy-state math.

Phase-coding BB84 with weak coherent pulses (Bennett, PRL 68, 3121, 1992; Townsend,
Rarity & Tapster, Electron. Lett. 29, 634, 1993) and decoy states (Lo, Ma & Chen, PRL
94, 230504, 2005; Ma, Qi, Zhao & Lo, PRA 72, 012326, 2005). Alice's unbalanced
Mach–Zehnder interferometer splits every laser pulse into an early and a late time slot;
her phase modulator in the long arm writes φ ∈ {0, π} (Z basis) or {π/2, 3π/2} (X basis)
between them, and her intensity modulator picks μ, ν or (nearly) vacuum pulse by pulse.
Bob's matching interferometer, with θ = 0 (Z) or π/2 (X) in its long arm, overlaps the
late half of the early slot with the early half of the late slot: in the middle of the
three output slots DET-0 clicks for φ − θ = 0 and DET-1 for φ − θ = π, and the two
satellite slots carry no information. The time tagger runs three bins per pulse.

Everything here is public: the parameters are agreed beforehand, and the estimates are
the same deterministic functions both stations run on the same disclosed counts. Private
data (Alice's intensities, bases and bits; Bob's bases and clicks) only ever passes
through these functions inside its owner's own process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from phoq.core import Setup
from phoq.detectors import PowerMeter, SinglePhotonDetector
from phoq.engine import trace
from phoq.generators import ArbitraryWaveformGenerator
from phoq.instruments import PhotonCounter
from phoq.modulators import IntensityModulator, OpticalSwitch, PhaseModulator
from phoq.passive import Attenuator, Coupler, FiberSpool, PhaseShifter
from phoq.sources import Laser
from phoq.units import FIBER_DELAY, photon_energy

B_Z, B_X = 0, 1  # bases
I_MU, I_NU, I_VAC = 0, 1, 2  # intensity labels: signal, decoy, vacuum
DET_0, DET_1 = 0, 1
BINS = 3  # time-tagger bins per laser pulse: early | middle (interference) | late
MIDDLE = 1


class Topic:
    """Message topics on the classical channel."""

    # Bob → Alice: pulses with a middle-slot click, and his basis for every pulse
    DETECT = "detect"
    # Alice → Bob: which detections match in basis, their intensity labels, her sent
    # counts, her X bits, a Z sample
    SIFT = "sift"
    ERRORS = "errors"  # Bob → Alice: his bits at the X detections and at the Z sample


@dataclass
class BB84Params:
    distance: float = 20e3  # Alice–Bob fiber [m]
    mu: float = 0.5  # signal intensity (photons per pulse into the fiber)
    nu: float = 0.1  # decoy intensity
    p_mu: float = 0.7  # probability of sending μ …
    p_nu: float = 0.2  # … and ν; the rest is vacuum
    # probability of the Z basis, for Alice and Bob alike; Z bits make the key
    p_z: float = 0.5
    sample_fraction: float = 0.1  # share of the Z-μ bits disclosed to estimate E_μ^Z
    efficiency: float = 0.5  # detector efficiency
    dark_count_rate: float = 100.0  # [Hz]
    linewidth: float = 100e3  # each laser [Hz]
    rep_rate: float = 250e6
    wavelength: float = 1550e-9
    pulse_width: float = 100e-12
    # intensity-modulator extinction: how dark "vacuum" really is
    extinction_db: float = 30.0
    # residual phase error of Bob's interferometer [rad]: sin²(0.1) ≈ 1 % optical QBER
    phase_error: float = 0.2
    f_ec: float = 1.16  # error-correction inefficiency
    # where Eve's station cuts the fiber (fraction of the distance)
    eve_position: float = 0.5

    @property
    def slot(self) -> float:
        """Time-slot separation = interferometer imbalance = one tagger bin [s]."""
        return 1.0 / (BINS * self.rep_rate)

    @property
    def vacuum(self) -> float:
        """What 'vacuum' really is: the intensity modulator's leak at its null."""
        return self.mu * 10 ** (-self.extinction_db / 10)

    @property
    def intensities(self) -> tuple[float, float, float]:
        return (self.mu, self.nu, self.vacuum)

    @property
    def probabilities(self) -> tuple[float, float, float]:
        return (self.p_mu, self.p_nu, 1.0 - self.p_mu - self.p_nu)

    @property
    def mean_intensity(self) -> float:
        return sum(p * k for p, k in zip(self.probabilities, self.intensities))


# ---- encoding ------------------------------------------------------------------------
def phase_for(basis, bit) -> np.ndarray:
    """Alice's phase between the two time slots: Z = {0, π}, X = {π/2, 3π/2}."""
    return (
        np.asarray(basis, dtype=float) * (np.pi / 2)
        + np.asarray(bit, dtype=float) * np.pi
    )


def measurement_phase(basis) -> np.ndarray:
    """Bob's phase: θ = 0 measures Z, θ = π/2 measures X."""
    return np.asarray(basis, dtype=float) * (np.pi / 2)


def middle_slot(clicks: np.ndarray) -> np.ndarray:
    """The interference bin of every pulse from a time-tagger click record."""
    return clicks[MIDDLE::BINS]


# ---- the hardware (physical layer) ---------------------------------------------------
def unbalanced_mzi(setup: Setup, tag: str, p: BB84Params, shifter: bool):
    """Fiber Mach–Zehnder interferometer whose long arm is one time slot longer and
    holds a phase modulator (and, for a receiver, the phase shifter that stabilizes the
    interferometer).
    """
    bs_in, bs_out = setup.add(Coupler(f"{tag}-BS1"), Coupler(f"{tag}-BS2"))
    pm = setup.add(PhaseModulator(f"{tag}-PM"))
    setup.connect(bs_in.b1, bs_out.a1, length=3.0)  # short arm
    extra = p.slot / FIBER_DELAY  # long arm: +ΔT
    if shifter:
        ps = setup.add(PhaseShifter(f"{tag}-PS"))
        setup.connect(bs_in.b2, ps.p1, length=1.0)
        setup.connect(ps.p2, pm.p1, length=1.0)
    else:
        setup.connect(bs_in.b2, pm.p1, length=2.0)
    setup.connect(pm.p2, bs_out.a2, length=1.0 + extra)
    return bs_in, bs_out


def transmitter(
    setup: Setup, tag: str, p: BB84Params, shifter: bool = False
) -> Attenuator:
    """Pulsed laser → intensity modulator → unbalanced MZI
    (phase modulator in the long arm) → VOA. The AWG's CH1 drives the IM, CH2 the PM;
    the interferometer's second output feeds a monitor.
    """
    laser = setup.add(
        Laser(
            f"{tag}-LD",
            wavelength=p.wavelength,
            power_dbm=-10.0,
            mode="pulsed",
            rep_rate=p.rep_rate,
            pulse_width=p.pulse_width,
            linewidth=p.linewidth,
            # a weak-coherent-pulse source: fresh phase every pulse
            phase_randomized=True,
        )
    )
    im = setup.add(IntensityModulator(f"{tag}-IM", extinction_db=p.extinction_db))
    setup.connect(laser.out, im.p1)
    bs_in, bs_out = unbalanced_mzi(setup, tag, p, shifter)
    setup.connect(im.p2, bs_in.a1)
    voa = setup.add(Attenuator(f"{tag}-VOA", attenuation_db=50.0))
    setup.connect(bs_out.b1, voa.p1)
    setup.connect(bs_out.b2, setup.add(PowerMeter(f"{tag}-OPM")).inp)
    awg = setup.add(
        ArbitraryWaveformGenerator(
            f"{tag}-AWG", ch1_shape="dc", ch1_offset=0.0, ch2_shape="dc", ch2_offset=0.0
        )
    )
    setup.connect(awg.ch1, im.rf)
    setup.connect(awg.ch2, setup[f"{tag}-PM"].rf)
    return voa


def receiver(
    setup: Setup,
    tag: str,
    p: BB84Params,
    pulses: int,
    detectors: tuple[str, str],
    tagger: str,
) -> Coupler:
    """Unbalanced MZI (phase shifter + phase modulator in the long arm) → two SPDs →
    time tagger with three bins per pulse, synchronized to Alice's laser. The AWG's CH1
    drives the PM.
    """
    bs_in, bs_out = unbalanced_mzi(setup, tag, p, shifter=True)
    det = [
        setup.add(
            SinglePhotonDetector(
                n,
                efficiency=p.efficiency,
                dark_count_rate=p.dark_count_rate,
                dead_time=0.0,
            )
        )
        for n in detectors
    ]
    cnt = setup.add(
        PhotonCounter(
            tagger,
            slots=BINS * pulses,
            bin_rate=BINS * p.rep_rate,
            sync_source="ALICE-LD",
        )
    )
    setup.connect(bs_out.b1, det[0].inp)
    setup.connect(bs_out.b2, det[1].inp)
    setup.connect(det[0].out, cnt.ch1)
    setup.connect(det[1].out, cnt.ch2)
    awg = setup.add(
        ArbitraryWaveformGenerator(
            f"{tag}-AWG", ch1_shape="dc", ch1_offset=0.0, ch2_shape="dc", ch2_offset=0.0
        )
    )
    setup.connect(awg.ch1, setup[f"{tag}-PM"].rf)
    return bs_in


def build(
    p: BB84Params | None = None, pulses: int = 100_000, eve: bool | None = None
) -> Setup:
    """The BB84 hardware, calibrated: Alice's transmitter → fiber → Bob's receiver.

    With ``eve`` the fiber is cut at ``p.eve_position`` and Eve's station spliced in: a
    2×2 optical switch (bar: Alice's pulse goes on to Bob; cross: it goes into Eve's
    receiver, a copy of Bob's, while Eve's own transmitter's pulse takes its place),
    with her transmitter delayed to arrive in the same time slot. By default the station
    is added when an Eve party has been loaded (``--party examples/bb84/eve.py``) — a
    party file cannot add hardware itself.
    """
    p = p or BB84Params()
    if eve is None:
        from phoq.parties import PARTY_REGISTRY

        eve = any(c.name == "EVE" for c in PARTY_REGISTRY)
    setup = Setup("BB84 link")
    voa = transmitter(setup, "ALICE", p)
    bob = receiver(setup, "BOB", p, pulses, ("DET-0", "DET-1"), "TAGGER")
    if not eve:
        spool = setup.add(FiberSpool("SPL", length=p.distance, splice_loss_db=0.0))
        setup.connect(voa.p2, spool.p1)
        setup.connect(spool.p2, bob.a1)
    else:
        l_a = max(1.0, p.eve_position * p.distance)
        spl_a = setup.add(FiberSpool("SPL-A", length=l_a, splice_loss_db=0.0))
        spl_b = setup.add(
            FiberSpool("SPL-B", length=max(1.0, p.distance - l_a), splice_loss_db=0.0)
        )
        sw = setup.add(OpticalSwitch("EVE-SW"))
        setup.connect(voa.p2, spl_a.p1)
        setup.connect(spl_a.p2, sw.a1)
        setup.connect(sw.b1, spl_b.p1)
        setup.connect(spl_b.p2, bob.a1)
        rx = receiver(
            setup, "EVE-RX", p, pulses, ("EVE-DET-0", "EVE-DET-1"), "EVE-TAGGER"
        )
        setup.connect(sw.b2, rx.a1)
        # CH2 of her receiver's AWG throws the switch
        setup.connect(setup["EVE-RX-AWG"].ch2, sw.ctrl)
        tx_voa = transmitter(setup, "EVE-TX", p, shifter=True)
        delay = setup.add(FiberSpool("EVE-DELAY", length=1.0, splice_loss_db=0.0))
        setup.connect(tx_voa.p2, delay.p1)
        setup.connect(delay.p2, sw.a2)
    # the interferometers' spare inputs: open, they reflect light back in
    setup.terminate_spares()
    calibrate(setup, p, eve)
    return setup


def calibrate(setup: Setup, p: BB84Params, eve: bool) -> None:
    """What a real bench needs before a run: μ at the fiber input, the interferometers'
    phases, and (with Eve) her resend intensity, her delay line and her two
    interferometers."""
    calibrate_mu(setup, "ALICE", p.mu, "SPL-A.p1" if eve else "SPL.p1")
    calibrate_interferometer(setup, "ALICE-LD", "DET-0", "BOB-PS", p.slot)
    if eve:
        awg, sw = setup["EVE-RX-AWG"], setup["EVE-SW"]
        awg.ch2_offset = 2 * sw.threshold  # cross while Eve's optics are calibrated …
        calibrate_interferometer(setup, "ALICE-LD", "EVE-DET-0", "EVE-RX-PS", p.slot)
        match_delay(setup, "EVE-TX-LD", "ALICE-LD", "DET-0", "EVE-DELAY")
        # after the delay line: it has loss
        calibrate_mu(setup, "EVE-TX", p.mu, "EVE-SW.a2")
        calibrate_interferometer(setup, "EVE-TX-LD", "DET-0", "EVE-TX-PS", p.slot)
        # … then bar: Alice's pulses pass until Eve throws the switch
        awg.ch2_offset = 0.0
    setup["BOB-PS"].phase += p.phase_error  # what the stabilization leaves behind


def calibrate_mu(setup: Setup, tag: str, mu: float, port: str) -> float:
    """Set a transmitter's VOA so that ``mu`` photons per pulse enter ``port``
    (a power-meter check)."""
    laser, voa = setup[f"{tag}-LD"], setup[f"{tag}-VOA"]
    for _ in range(2):
        p_in = trace(setup).port_power[port]
        mu_now = p_in / laser.rep_rate / photon_energy(laser.wavelength)
        voa.attenuation_db = min(
            80.0, max(0.0, voa.attenuation_db + 10 * math.log10(mu_now / mu))
        )
    return voa.attenuation_db


def _static_amplitude(path) -> complex:
    """A path's field amplitude with every modulator on it at its static (0 V)
    response."""
    amp = complex(path.amp)
    for mod, _ in path.mods:
        amp *= complex(np.asarray(mod.response([mod.static_voltage()]))[0])
    return amp


def middle_paths(net, source: Laser, detector, slot: float) -> list:
    """The two strongest paths from ``source`` to ``detector`` that arrive one slot
    after the earliest one: the pair that interferes in the middle time slot."""
    paths = [q for q in net.paths.get(detector, []) if q.source is source]
    d0 = min(q.delay for q in paths)
    mid = [q for q in paths if abs(q.delay - d0 - slot) < 1e-13]
    return sorted(mid, key=lambda q: -abs(_static_amplitude(q)))[:2]


def calibrate_interferometer(
    setup: Setup, source: str, detector: str, shifter: str, slot: float
) -> float:
    """Set the phase shifter so that the two middle-slot paths from ``source`` interfere
    constructively at ``detector`` — the interferometer stabilization of a real
    receiver.
    """
    src, det, ps = setup[source], setup[detector], setup[shifter]
    key = lambda q: tuple(id(m) for m, _ in q.mods)  # noqa: E731
    ps.phase = 0.0
    a0 = {
        key(q): _static_amplitude(q) for q in middle_paths(trace(setup), src, det, slot)
    }
    ps.phase = 1.0  # the path through the shifter is the one that turns by 1 rad
    a1 = {
        key(q): _static_amplitude(q) for q in middle_paths(trace(setup), src, det, slot)
    }
    through, other = sorted(a0, key=lambda k: -abs(np.angle(a1[k] / a0[k])))
    ps.phase = float((np.angle(a0[other]) - np.angle(a0[through])) % (2 * np.pi))
    return ps.phase


def match_delay(
    setup: Setup, source: str, reference: str, detector: str, spool: str
) -> float:
    """Lengthen ``spool`` so that light from ``source`` reaches ``detector`` exactly
    when light from ``reference`` does: pulse k of the one lands in the time bins of
    pulse k of the other.
    """
    net, det = trace(setup), setup[detector]
    d_ref = min(net.delays(det, setup[reference]))
    d_src = min(net.delays(det, setup[source]))
    length = setup[spool].length + (d_ref - d_src) / FIBER_DELAY
    if length < 1.0:
        raise ValueError(
            f"{source} already arrives later than {reference}; move the station"
        )
    setup[spool].length = length
    return length


# ---- post-processing: what both stations count and estimate --------------------------
@dataclass
class Tally:
    """Public counts both stations accumulate — everything the decoy-state estimate
    needs."""

    pulses: int = 0
    sent: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 2), dtype=np.int64)
        # [intensity, basis]: pulses with that intensity and basis for which Bob chose
        # the same basis
    )
    detected: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 2), dtype=np.int64)
    )  # … of which Bob had a middle-slot click
    # errors among the (all disclosed) X-basis detections, per intensity
    x_errors: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=np.int64))
    z_sampled: int = 0  # Z-μ bits disclosed …
    z_errors: int = 0  # … and wrong
    doubles: int = 0
    key_bits: int = 0

    def __iadd__(self, other: Tally) -> Tally:
        self.pulses += other.pulses
        self.sent += other.sent
        self.detected += other.detected
        self.x_errors += other.x_errors
        self.z_sampled += other.z_sampled
        self.z_errors += other.z_errors
        self.doubles += other.doubles
        self.key_bits += other.key_bits
        return self


def h2(x: float) -> float:
    """Binary entropy (1 beyond x = ½)."""
    if not (x > 0.0):
        return 0.0
    if x >= 0.5:
        return 1.0
    return float(-x * math.log2(x) - (1 - x) * math.log2(1 - x))


def estimate(t: Tally, p: BB84Params) -> dict:
    """QBER, decoy-state bounds and the asymptotic key rate from the public counts.

    Z-basis gains give the single-photon yield Y₁ (Ma et al. 2005, Eqs. 34–37, with the
    vacuum decoy's true leak ω) and hence Q₁ = Y₁ μ e^{−μ}; the X-basis error gains give
    the single-photon phase-error bound e₁; GLLP then gives, in secret bits per laser
    pulse,

        R = (N_{μ,Z} / N) · [ Q₁ (1 − H₂(e₁)) − Q_μ f H₂(E_μ) ].
    """
    mu, nu, om = p.intensities
    with np.errstate(divide="ignore", invalid="ignore"):
        Q = np.where(t.sent > 0, t.detected / np.maximum(t.sent, 1), np.nan)  # gains
        eq_x = t.x_errors / np.maximum(t.sent[:, B_X], 1)  # error gains E·Q in X
        E_x = np.where(
            t.detected[:, B_X] > 0,
            t.x_errors / np.maximum(t.detected[:, B_X], 1),
            np.nan,
        )
    e_z = t.z_errors / t.z_sampled if t.z_sampled else float("nan")

    def bounds(q):  # (Y₀ lower, Y₁ lower) from the gains (μ, ν, ω) of one basis
        qm, qn, qo = (float(x) for x in q)
        if not all(math.isfinite(x) for x in (qm, qn, qo)):
            return float("nan"), float("nan")
        y0 = max((nu * qo * math.exp(om) - om * qn * math.exp(nu)) / (nu - om), 0.0)
        y1 = (
            mu
            / (mu * nu - mu * om - nu**2 + om**2)
            * (
                qn * math.exp(nu)
                - qo * math.exp(om)
                - (nu**2 - om**2) / mu**2 * (qm * math.exp(mu) - y0)
            )
        )
        return y0, max(y1, 0.0)

    y0_z, y1_z = bounds(Q[:, B_Z])
    y0_x, y1_x = bounds(Q[:, B_X])
    if math.isfinite(y1_x) and y1_x > 0:
        num = eq_x[I_NU] * math.exp(nu) - eq_x[I_VAC] * math.exp(om)
        e1 = min(max(num / ((nu - om) * y1_x), 0.0), 0.5)
    else:
        e1 = float("nan") if not math.isfinite(y1_x) else 0.5
    q1 = y1_z * mu * math.exp(-mu)
    q_mu = float(Q[I_MU, B_Z])
    if t.pulses and all(math.isfinite(x) for x in (q1, e1, q_mu, e_z)):
        raw = (
            t.sent[I_MU, B_Z] / t.pulses * (q1 * (1 - h2(e1)) - q_mu * p.f_ec * h2(e_z))
        )
    else:
        raw = float("nan")
    return dict(
        gains=Q,
        qber_x=E_x,
        qber_z=e_z,
        y0=y0_z,
        y1=y1_z,
        y1_x=y1_x,
        e1=e1,
        q1=q1,
        q_mu=q_mu,
        rate=max(raw, 0.0) if math.isfinite(raw) else raw,
        raw_rate=raw,
    )


def account(st: dict, mine: dict, theirs: dict) -> Tally:
    """One block's public counts, once both stations hold each other's disclosed bits.

    ``st`` holds the sifting state (``sent``, ``label``, ``basis``, ``keep``, ``x``,
    ``zmu``, ``sample``, ``n``, ``doubles``); ``mine``/``theirs`` the two stations'
    ``xbits`` and ``sample_bits``. The disclosed Z-μ sample leaves the key.
    """
    t = Tally(pulses=int(st["n"]), doubles=int(st["doubles"]))
    t.sent = np.asarray(st["sent"], dtype=np.int64).copy()
    keep, label, basis = st["keep"], st["label"], st["basis"]
    for k in range(3):
        for b in range(2):
            t.detected[k, b] = np.count_nonzero(keep & (label == k) & (basis == b))
    wrong = mine["xbits"] != theirs["xbits"]
    label_x = label[st["x"]]
    for k in range(3):
        t.x_errors[k] = np.count_nonzero(wrong & (label_x == k))
    t.z_sampled = int(st["sample"].size)
    t.z_errors = int(np.count_nonzero(mine["sample_bits"] != theirs["sample_bits"]))
    t.key_bits = int(np.count_nonzero(st["zmu"])) - t.z_sampled
    return t


def key_from(st: dict, bits: np.ndarray) -> np.ndarray:
    """A station's raw key bits of one block: its bits at the kept Z-μ detections,
    sample removed."""
    keep = st["zmu"].copy()
    keep[st["sample"]] = False
    return bits[keep].astype(np.int8)


def fmt(x, digits: int = 3) -> str:
    return "—" if x is None or not math.isfinite(x) else f"{x:.{digits}g}"


def describe(est: dict) -> str:
    """One line with the figures of merit, for the consoles."""
    return (
        f"QBER Z {fmt(est['qber_z'])} X {fmt(est['qber_x'][I_MU])} · Y₁ ≥"
        f" {fmt(est['y1'])} e₁ ≤ {fmt(est['e1'])} Q₁ {fmt(est['q1'])} · R"
        f" {fmt(est['rate'])} bit/pulse"
    )


def readouts(t: Tally, est: dict, net_stats: dict | None = None) -> dict:
    """Live read-outs for a station's laptop (and the result dict of a headless run)."""
    out = {
        "key bits": t.key_bits,
        "pulses": t.pulses,
        "sifted": int(t.detected.sum()),
        "QBER Z (μ, sampled)": est["qber_z"],
        "QBER X (μ)": float(est["qber_x"][I_MU]),
        "Q_μ (Z)": est["q_mu"],
        "Y0 ≥": est["y0"],
        "Y1 ≥": est["y1"],
        "e1 ≤": est["e1"],
        "Q1": est["q1"],
        "key rate [bit/pulse]": est["rate"],
    }
    if net_stats:
        out["sent [kB]"] = net_stats["bytes_out"] / 1e3
        out["received [kB]"] = net_stats["bytes_in"] / 1e3
    return out
