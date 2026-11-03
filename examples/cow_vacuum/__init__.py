"""Coherent one-way QKD (COW) with a vacuum decoy sequence, on the simulated hardware,
up to the sifted key and the bound on its phase error rate.

Gao, Xie, Gu, Liu, Weng, Li, Yin & Chen, "Simple security proof of coherent-one-way
quantum key distribution", arXiv:2107.09329v5 (2023). The hardware is that of the
original protocol (``examples/cow``); Alice adds one state, |0⟩|0⟩, and the security
figure is no longer a visibility but the upper bound E_p^u of Eq. (7), computed from
what the monitoring line counts for each state she sent. Physical layer (one
:class:`~phoq.Setup`; see ``common.build``)::

    ALICE-AWG ch1 ──────┐
    ALICE-LD (CW) → ALICE-IM → ALICE-ATT → ALICE-VOA → SPL → BOB-TAP

    BOB-TAP ─ t_B ───→ DT ──────────────┐
       └─── 1−t_B → DLI → DM0, DM1 ─────┴→ TAGGER

    DLI = [BOB-BS1 ⇒ short: PS + ODL ⇒ BOB-BS2]
                     long: VOA

Alice's intensity modulator carves her CW laser into pulses of μ photons, or blocks a
slot: bit 0 is (0, μ), bit 1 (μ, 0), decoy 1 (μ, μ) and decoy 2 (0, 0). Bob reads the
bit from the time of arrival on DT; the delay-line interferometer, one slot unbalanced,
mixes the two pulses of a pair in its second slot. Each pair gives Bob at most one
event.

Parties, each in its own file and its own process, talking over an authenticated public
channel:

* :class:`Alice` (:mod:`.alice`) draws the sequence, removes the decoys from Bob's
  data-line events, discloses a sample of her bits and counts the gains.
* :class:`Bob` (:mod:`.bob`) announces his events and answers with his bits at the
  sample.

``PARTIES`` and ``build()`` make this a protocol package: ``phoq run --protocol
cow_vacuum``. The single-process reference ``run()`` (one god's-eye view of every
record) and ``theory()`` are kept for tests. ``bound`` holds the paper's arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from phoq.core import Setup
from phoq.runner import Runner
from . import bound
from .alice import Alice
from .bob import Bob
from .bound import Gains, phase_error, terms
from .common import (
    BIT0,
    BIT1,
    DECOY1,
    DECOY2,
    DM0,
    DM1,
    DT,
    M0,
    M1,
    NONE,
    STATES,
    T0,
    T1,
    COWParams,
    Port,
    Tally,
    Topic,
    account,
    announcements,
    best_mu,
    build,
    calibrate,
    carving,
    counting_error,
    describe,
    draw,
    events,
    gains_of,
    monitor_table,
    occupied,
    one_event,
    pair_gains,
    pulse_photons,
    readouts,
    theory,
    windows,
)

PARTIES = [Alice, Bob]  # left to right on the lab table
params = COWParams()

__all__ = [
    "Alice",
    "Bob",
    "PARTIES",
    "BIT0",
    "BIT1",
    "COWParams",
    "COWResult",
    "DECOY1",
    "DECOY2",
    "DM0",
    "DM1",
    "DT",
    "Gains",
    "M0",
    "M1",
    "NONE",
    "Port",
    "STATES",
    "T0",
    "T1",
    "Tally",
    "Topic",
    "account",
    "announcements",
    "best_mu",
    "bound",
    "build",
    "calibrate",
    "carving",
    "counting_error",
    "describe",
    "draw",
    "events",
    "gains_of",
    "monitor_table",
    "occupied",
    "one_event",
    "pair_gains",
    "params",
    "phase_error",
    "pulse_photons",
    "readouts",
    "run",
    "terms",
    "theory",
    "windows",
]


@dataclass
class COWResult:
    params: COWParams
    tally: Tally
    expected: dict
    alice_key: np.ndarray
    bob_key: np.ndarray

    @property
    def e_b(self) -> float:
        """Error rate of the whole sifted key (god's-eye: both keys are in one
        place)."""
        return (
            float(np.mean(self.alice_key != self.bob_key))
            if self.alice_key.size
            else float("nan")
        )

    def summary(self) -> str:
        p, t, e = self.params, self.tally, self.expected
        split, want = terms(p.mu, t.gains), e["terms"]
        m = t.monitor
        return "\n".join(
            [
                (
                    f"COW with vacuum decoys over {p.distance / 1e3:.0f} km, μ ="
                    f" {p.mu}, decoys {p.decoy1} + {p.decoy2}, t_B = {p.t_b},"
                    f" {t.pairs:,} pairs"
                    f" ({2 * t.pairs:,} slots at {p.rate / 1e6:.0f} MHz)"
                ),
                (
                    f"sifted {t.sifted:,}: Q {t.q:.3e} (expected {e['Q']:.3e}) · E_b"
                    f" {self.e_b:.4f} on the whole sifted key (expected {e['E_b']:.4f})"
                ),
                (
                    f"E_p^u {t.phase_error(p.mu):.3f} ± {t.counting_error(p.mu):.3f}"
                    f" (expected {e['E_p^u']:.3f}): |β,β⟩ {split['beta']:.3f}, M0"
                    f" {split['M0']:.3f}, M1 {split['M1']:.3f} (expected"
                    f" {want['beta']:.3f}, {want['M0']:.3f}, {want['M1']:.3f})"
                ),
                (
                    f"|α,α⟩ at D_M0 {m[DECOY1, 0]:,}, D_M1 {m[DECOY1, 1]:,} · |0,0⟩ at"
                    f" D_M0 {m[DECOY2, 0]:,}, D_M1 {m[DECOY2, 1]:,} · V"
                    f" {t.visibility:.4f} (expected {e['V']:.4f}, not in the bound)"
                ),
            ]
        )


def run(
    p: COWParams | None = None,
    blocks: int = 4,
    slots: int = 400_000,
    seed: int | None = None,
    setup: Setup | None = None,
) -> COWResult:
    """Single-process reference: prepare, record and sift ``blocks`` blocks with both
    records in one place; E_b is counted on the whole sifted key."""
    p = p or COWParams()
    setup = setup or build(p, slots)
    runner = Runner(setup, seed=seed)
    im, awg, tagger = setup["ALICE-IM"], setup["ALICE-AWG"], setup["TAGGER"]
    tally, alice_key, bob_key = Tally(), [], []
    for _ in range(blocks):
        kind = draw(runner.rng, tagger.slots // 2, p)
        awg.set_pattern("ch1", im.voltage_for(carving(occupied(kind))), 2 * p.rate)
        event, multi = events(runner.acquire("TAGGER").clicks, runner.rng)
        idx, bits, mon, det = announcements(event)
        sent = kind[idx]
        decoy = sent >= DECOY1
        keep = np.flatnonzero(~decoy)
        tally += account(
            kind.size,
            idx.size,
            decoy,
            multi,
            keep,
            sent[keep],
            bits[keep],
            monitor_table(kind, mon, det),
        )
        alice_key.append(sent[keep].astype(np.int8))
        bob_key.append(bits[keep])
    return COWResult(
        p,
        tally,
        theory(setup, p),
        np.concatenate(alice_key),
        np.concatenate(bob_key),
    )
