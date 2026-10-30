"""What the MP-QKD parties share: public parameters, the hardware, the post-processing
math.

Everything here is public knowledge — the protocol's parameters (μ, ν, their
probabilities, D, the pairing interval l, the disclosed-sample fraction, the
post-processing sizes) are agreed before the run ([CLASSICAL #2] in
``examples/mpqkd/reference.py``), and the pairing / sifting functions are the
deterministic algorithms both stations run on the same public announcements. Private
data (each station's intensities and phases) never passes through this module's
functions except inside that station's own process.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from phoq.core import Setup
from phoq.detectors import SinglePhotonDetector
from phoq.engine import trace
from phoq.generators import FunctionGenerator
from phoq.instruments import PhotonCounter
from phoq.modulators import IntensityModulator, PhaseModulator
from phoq.passive import Attenuator, Coupler, FiberSpool
from phoq.sources import Laser
from phoq.units import photon_energy

B_ZERO, B_Z, B_X, B_MIXED = (
    0,
    1,
    2,
    3,
)  # pair basis: vacuum twice / one vacuum / equal bright / unequal
L0, LNU, LMU = 0, 1, 2  # intensity levels of a pulse: vacuum, decoy ν, signal μ
DET_L, DET_R = 0, 1
STATIONS = ("ALICE", "BOB")


class Topic:
    """Message topics on the classical channel (see ``examples/mpqkd/reference.py``
    [CLASSICAL #1–#5])."""

    CLICKS = "clicks"  # #1 Charlie → all: single-click rounds and which detector
    # #3 Alice ↔ Bob: basis label per pair (Z / X / '0'), never the intensities
    BASIS = "basis"
    THETA = "theta"  # #4 Alice ↔ Bob: alignment angle of each X pair
    XBITS = "xbits"  # #5 Alice ↔ Bob: X-basis bits, disclosed for error estimation
    # #5 leader → partner: positions and bits of a Z sample to disclose
    SAMPLE = "sample"
    SAMPLE_REPLY = "sample-reply"  # #5 partner → leader: its bits at those positions
    # #6 leader → partner: the chunk to reconcile, Cascade seed, first parities
    EC_START = "ec-start"
    EC_QUERY = "ec-query"  # #6 partner → leader: parities wanted (pass, lo, hi)
    # #6 leader → partner: those parities (each one a leaked bit)
    EC_PARITY = "ec-parity"
    # #6 partner → leader: Cascade finished, verification tag of its key
    EC_DONE = "ec-done"
    VERIFIED = "verified"  # #6 leader → partner: tag matched (or not: chunk dropped)
    # #6 leader → partner: Toeplitz seed and output length for the verified chunks
    PA = "pa"


@dataclass
class MPQKDParams:
    distance: float = 100e3  # Alice–Bob [m], Charlie in the middle
    mu: float = 0.4  # signal intensity (photons per pulse into the link)
    nu: float = 0.1  # weak decoy intensity
    p_mu: float = 0.25  # probability of sending μ
    p_nu: float = 0.15  # probability of sending ν (the rest, 0.6, is vacuum)
    D: int = 16  # number of discrete phases
    l: int = 1000  # maximal pairing interval (rounds)
    sample_fraction: float = 0.1  # share of key Z pairs disclosed to estimate E_Z
    chunk_bits: int = 1000  # sifted key bits reconciled and amplified together
    # the last passes cost 1–2 parities each and catch hidden error pairs
    cascade_passes: int = 6
    verify_bits: int = 32  # universal-hash tag that confirms the corrected chunks agree
    estimator: str = "model"  # how e_11^X is sized: "model" (see keyrate.py) or "decoy"
    eps: float = 1e-10  # security parameter of the (labeled) finite-key estimate
    efficiency: float = 0.5  # detector efficiency
    dark_count_rate: float = 6.25  # [Hz]; ≈ 1e-8 per 1.6 ns bin
    linewidth: float = 3e3  # each laser [Hz]
    rep_rate: float = 625e6
    wavelength: float = 1550e-9
    pulse_width: float = 100e-12
    extinction_db: float = 40.0  # intensity-modulator extinction (vacuum leakage)


# ---- the hardware (physical layer) ---------------------------------------------------
def build(p: MPQKDParams | None = None, rounds: int = 200_000) -> Setup:
    """The MP-QKD hardware with μ already calibrated. Each station's AWG drives its
    modulators."""
    p = p or MPQKDParams()
    setup = Setup("MP-QKD link")
    for tag in STATIONS:
        setup.chain(
            Laser(
                f"{tag}-LD",
                wavelength=p.wavelength,
                power_dbm=-10.0,
                mode="pulsed",
                rep_rate=p.rep_rate,
                pulse_width=p.pulse_width,
                linewidth=p.linewidth,
            ),
            IntensityModulator(f"{tag}-IM", extinction_db=p.extinction_db),
            PhaseModulator(f"{tag}-PM"),
            Attenuator(f"{tag}-VOA", attenuation_db=50.0),
            FiberSpool(f"{tag}-SPL", length=p.distance / 2, splice_loss_db=0.0),
        )
        awg = setup.add(FunctionGenerator(f"{tag}-AWG"))
        setup.connect(awg.ch1, setup[f"{tag}-IM"].rf)
        setup.connect(awg.ch2, setup[f"{tag}-PM"].rf)
    bs = setup.add(Coupler("CHARLIE", excess_loss_db=0.0))
    det = [
        setup.add(
            SinglePhotonDetector(
                n,
                efficiency=p.efficiency,
                dark_count_rate=p.dark_count_rate,
                dead_time=0.0,
            )
        )
        for n in ("DET-L", "DET-R")
    ]
    tagger = setup.add(PhotonCounter("TAGGER", slots=rounds, sync_source="ALICE-LD"))
    setup.connect(setup["ALICE-SPL"].p2, bs.a1)
    setup.connect(setup["BOB-SPL"].p2, bs.a2)
    setup.connect(bs.b1, det[0].inp)
    setup.connect(bs.b2, det[1].inp)
    setup.connect(det[0].out, tagger.ch1)
    setup.connect(det[1].out, tagger.ch2)
    calibrate_mu(setup, p.mu)
    return setup


def calibrate_mu(setup: Setup, mu: float) -> dict[str, float]:
    """Set each station's VOA so ``mu`` photons per pulse enter its fiber link
    (like a power-meter check)."""
    out = {}
    for tag in STATIONS:
        laser, voa = setup[f"{tag}-LD"], setup[f"{tag}-VOA"]
        for _ in range(2):
            p_in = trace(setup).port_power[f"{tag}-SPL.p1"]
            mu_now = p_in / laser.rep_rate / photon_energy(laser.wavelength)
            voa.attenuation_db = min(
                80.0, max(0.0, voa.attenuation_db + 10 * math.log10(mu_now / mu))
            )
        out[tag] = voa.attenuation_db
    return out


# ---- post-processing (ported from examples/mpqkd/reference.py) -----------------------
def simple_pairing(click_idx: np.ndarray, l: int) -> tuple[np.ndarray, np.ndarray]:
    """Box 2: pair the front click with the next one if it is within l rounds."""
    fronts, rears, front = [], [], None
    for i in click_idx:
        if front is None:
            front = i
        elif i - front <= l:
            fronts.append(front)
            rears.append(i)
            front = None
        else:
            front = i
    return np.array(fronts, dtype=np.int64), np.array(rears, dtype=np.int64)


def pairing_rate_theory(p: float, l):
    """Eq. (4)."""
    return 1.0 / (1.0 / (p * (1 - (1 - p) ** l)) + 1.0 / p)


def draw_levels(rng: np.random.Generator, n: int, p: MPQKDParams) -> np.ndarray:
    """Random intensity level per pulse: vacuum / ν / μ with 1 − p_μ − p_ν, p_ν, p_μ."""
    u = rng.random(n)
    return np.where(u < p.p_mu, LMU, np.where(u < p.p_mu + p.p_nu, LNU, L0)).astype(
        np.int8
    )


def transmissions(p: MPQKDParams) -> np.ndarray:
    """Intensity-modulator transmission asked for each level: μ is the calibrated
    maximum."""
    return np.array([0.0, p.nu / p.mu, 1.0])


def assign_basis(lev_i: np.ndarray, lev_j: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Basis and announced intensity level of a pair from its two pulse levels (Table of
    the paper's Methods III.2): one vacuum → Z with the bright level; two equal bright →
    X with that level; two vacua → '0'; two unequal bright → discarded. Each station
    computes this from its own draws; the level says *how bright*, never *which* pulse
    was bright (that is the Z key bit).
    """
    lev_i, lev_j = np.asarray(lev_i, dtype=np.int8), np.asarray(lev_j, dtype=np.int8)
    nz_i, nz_j = lev_i > 0, lev_j > 0
    basis = np.full(lev_i.size, B_ZERO, dtype=np.int8)
    basis[nz_i ^ nz_j] = B_Z
    basis[nz_i & nz_j & (lev_i == lev_j)] = B_X
    basis[nz_i & nz_j & (lev_i != lev_j)] = B_MIXED
    level = np.where(basis == B_MIXED, 0, np.maximum(lev_i, lev_j)).astype(np.int8)
    return basis, level


def sift_and_map(F, R, lev_a, ph_a, lev_b, ph_b, detector, D) -> dict:
    """God's-eye sifting used by the single-process reference run
    (both records in one place): the key from the (μ, μ) Z pairs, E_X from the (μ, μ) X
    pairs."""
    (basis_a, level_a), (basis_b, level_b) = assign_basis(
        lev_a[F], lev_a[R]
    ), assign_basis(lev_b[F], lev_b[R])
    z = (basis_a == B_Z) & (basis_b == B_Z) & (level_a == LMU) & (level_b == LMU)
    ka_z = (lev_a[F][z] > 0).astype(int)
    kb_z = (lev_b[F][z] == 0).astype(int)
    x = (basis_a == B_X) & (basis_b == B_X) & (level_a == LMU) & (level_b == LMU)
    half = D // 2
    da, db = (ph_a[R][x] - ph_a[F][x]) % D, (ph_b[R][x] - ph_b[F][x]) % D
    ka_x, theta_a = da // half, da % half
    kb_x, theta_b = db // half, db % half
    kb_x = kb_x ^ (detector[F][x] != detector[R][x]).astype(int)
    keep = theta_a == theta_b
    return dict(
        ka_z=ka_z,
        kb_z=kb_z,
        ka_x=ka_x[keep],
        kb_x=kb_x[keep],
        n_pairs=F.size,
        n_z=int(z.sum()),
        n_x=int(keep.sum()),
    )
