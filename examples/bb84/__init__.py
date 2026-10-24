"""Decoy-state BB84 with phase coding on the simulated hardware — no relay, two parties.

Physical layer (one :class:`~phoq.Setup`, viewable in the virtual lab; see
``common.build``)::

    ALICE-AWG ch1 ─┐                                ch2 ─┐
    ALICE-LD → ALICE-IM → [ALICE-BS1 ⇒ short / long + ALICE-PM ⇒ ALICE-BS2]
                                                                → ALICE-VOA → SPL ─┐
    BOB-AWG ch1 ──────────────────────────┐                                        │
    [BOB-BS1 ⇒ short / long + BOB-PS + BOB-PM ⇒ BOB-BS2] → DET-0, DET-1 → TAGGER ◄─┘

With an eavesdropper (``--party examples/bb84/eve.py``) the spool is cut in two and
EVE-SW, a 2×2 optical switch, sits in between with Eve's receiver on its second output
and her transmitter on its second input.

Parties, each in its own file and its own process, talking over an authenticated public
channel:

* :class:`Alice` (:mod:`.alice`) draws intensity, basis and bit per pulse and leads the
  sifting.
* :class:`Bob` (:mod:`.bob`) draws his basis per pulse, announces his detections and
  answers.
* :class:`Eve` (``eve.py``, loaded with ``--party``) intercepts a fraction of the
  pulses.

``PARTIES`` and ``build()`` make this a protocol package: ``phoq run --protocol bb84``.
The single-process reference ``run()`` (one god's-eye view of both records) is kept for
tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from phoq.core import Setup
from phoq.runner import Runner
from .alice import Alice
from .bob import Bob
from .common import (
    B_X,
    B_Z,
    BINS,
    DET_0,
    DET_1,
    I_MU,
    I_NU,
    I_VAC,
    MIDDLE,
    BB84Params,
    Tally,
    Topic,
    account,
    build,
    calibrate_interferometer,
    calibrate_mu,
    describe,
    estimate,
    h2,
    key_from,
    measurement_phase,
    middle_slot,
    phase_for,
    readouts,
)

PARTIES = [Alice, Bob]  # left to right on the lab table
params = BB84Params()

__all__ = [
    "Alice",
    "Bob",
    "PARTIES",
    "BB84Params",
    "BB84Result",
    "Tally",
    "Topic",
    "B_Z",
    "B_X",
    "I_MU",
    "I_NU",
    "I_VAC",
    "DET_0",
    "DET_1",
    "BINS",
    "MIDDLE",
    "build",
    "calibrate_mu",
    "calibrate_interferometer",
    "estimate",
    "describe",
    "readouts",
    "h2",
    "phase_for",
    "measurement_phase",
    "middle_slot",
    "prepare",
    "run",
    "params",
]


def prepare(setup: Setup, p: BB84Params, pulses: int, rng: np.random.Generator) -> dict:
    """God's-eye state preparation: Alice's intensities, bases and bits and Bob's bases,
    loaded into both AWGs at once."""
    label = rng.choice(3, size=pulses, p=p.probabilities).astype(np.int8)
    basis = (rng.random(pulses) >= p.p_z).astype(np.int8)
    bit = rng.integers(0, 2, size=pulses, dtype=np.int8)
    bob_basis = (rng.random(pulses) >= p.p_z).astype(np.int8)
    im, pm, awg = setup["ALICE-IM"], setup["ALICE-PM"], setup["ALICE-AWG"]
    rate = setup["ALICE-LD"].rep_rate
    awg.set_pattern(
        "ch1", im.voltage_for(np.array([1.0, p.nu / p.mu, 0.0])[label]), rate
    )
    awg.set_pattern("ch2", pm.voltage_for(phase_for(basis, bit)), rate)
    setup["BOB-AWG"].set_pattern(
        "ch1", setup["BOB-PM"].voltage_for(measurement_phase(bob_basis)), rate
    )
    return dict(label=label, basis=basis, bit=bit, bob_basis=bob_basis)


@dataclass
class BB84Result:
    params: BB84Params
    tally: Tally
    estimate: dict
    alice_key: np.ndarray
    bob_key: np.ndarray

    @property
    def qber(self) -> float:
        """Error rate of the raw key itself (god's-eye: both keys are in one place)."""
        return (
            float(np.mean(self.alice_key != self.bob_key))
            if self.alice_key.size
            else float("nan")
        )

    def summary(self) -> str:
        p, t, e = self.params, self.tally, self.estimate
        q = e["gains"]
        lines = [
            (
                f"BB84 over {p.distance / 1e3:.0f} km, μ = {p.mu}, ν = {p.nu},"
                f" {t.pulses:,} pulses"
            ),
            (
                f"sifted {int(t.detected.sum()):,} detections, raw key {t.key_bits:,}"
                f" bits (true error rate {self.qber:.4f})"
            ),
            (
                f"gains Z: μ {q[I_MU, B_Z]:.3e} ν {q[I_NU, B_Z]:.3e} vac"
                f" {q[I_VAC, B_Z]:.3e} · X: μ {q[I_MU, B_X]:.3e} ν {q[I_NU, B_X]:.3e}"
                f" vac {q[I_VAC, B_X]:.3e}"
            ),
            describe(e),
        ]
        return "\n".join(lines)


def run(
    p: BB84Params | None = None,
    pulses: int = 200_000,
    seed: int | None = None,
    setup: Setup | None = None,
) -> BB84Result:
    """Single-process reference: prepare, record, sift with both records in one
    place."""
    p = p or BB84Params()
    setup = setup or build(p, pulses, eve=False)
    setup["TAGGER"].slots = BINS * pulses
    runner = Runner(setup, seed=seed)
    states = prepare(setup, p, pulses, runner.rng)
    clicks = runner.acquire("TAGGER").clicks
    c0, c1 = middle_slot(clicks["ch1"]), middle_slot(clicks["ch2"])
    idx = np.flatnonzero(c0 | c1)
    both = c0[idx] & c1[idx]
    bob_bits = np.where(c1[idx] & ~c0[idx], 1, 0).astype(np.int8)
    bob_bits[both] = runner.rng.integers(0, 2, size=int(both.sum()), dtype=np.int8)
    label, basis, bit, bob_basis = (
        states[k] for k in ("label", "basis", "bit", "bob_basis")
    )
    same = basis == bob_basis
    sent = np.zeros((3, 2), dtype=np.int64)
    for k in range(3):
        for b in range(2):
            sent[k, b] = np.count_nonzero((label == k) & (basis == b) & same)
    keep = same[idx]
    label_d, basis_d, alice_bits = label[idx], basis[idx], bit[idx]
    x = keep & (basis_d == B_X)
    zmu = keep & (basis_d == B_Z) & (label_d == I_MU)
    pos = np.flatnonzero(zmu)
    sample = pos[runner.rng.random(pos.size) < p.sample_fraction]
    sift = dict(
        sent=sent,
        keep=keep,
        label=label_d,
        basis=basis_d,
        x=x,
        zmu=zmu,
        sample=sample,
        n=pulses,
        doubles=int(both.sum()),
    )
    tally = account(
        sift,
        dict(xbits=alice_bits[x], sample_bits=alice_bits[sample]),
        dict(xbits=bob_bits[x], sample_bits=bob_bits[sample]),
    )
    return BB84Result(
        p,
        tally,
        estimate(tally, p),
        key_from(sift, alice_bits),
        key_from(sift, bob_bits),
    )
