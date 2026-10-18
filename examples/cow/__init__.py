"""Coherent one-way QKD (COW) on the simulated hardware, up to the sifted key, its QBER and V.

Stucki, Brunner, Gisin, Scarani & Zbinden, "Fast and simple one-way quantum key distribution",
Appl. Phys. Lett. 87, 194108 (2005). Physical layer (one :class:`~phoq.Setup`; see
``common.build``)::

    ALICE-AWG ch1 ──────┐
    ALICE-LD (CW) → ALICE-IM → ALICE-VOA → SPL ─→ BOB-TAP ─ t_B ─→ DB ───────────┐
                                                     └─ 1−t_B → [BOB-BS1 ⇒ short: PS + ODL ⇒ BOB-BS2] → DM1, DM2 → TAGGER
                                                                         long: VOA

Alice's intensity modulator carves her CW laser into pulses of μ photons, or blocks a slot: bit 0
is (μ, 0), bit 1 (0, μ), a decoy (μ, μ). Bob reads the bit from the time of arrival on DB; the
delay-line interferometer, one slot unbalanced, checks that neighbouring non-empty pulses are still
coherent. The tagger runs one bin per slot, its DB channel delayed to line up with DM1/DM2.

Parties, each in its own file and its own process, talking over an authenticated public channel:

* :class:`Alice` (:mod:`.alice`) draws the sequence, removes the decoys from Bob's detections,
  discloses a sample of her bits and computes the visibilities.
* :class:`Bob` (:mod:`.bob`) announces which bits clicked and his monitoring clicks, and answers
  with his bits at the sample.

``PARTIES`` and ``build()`` make this a protocol package: ``phoq run --protocol cow``. The
single-process reference ``run()`` (one god's-eye view of both records) and ``theory()`` are kept
for tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from phoq.core import Setup
from phoq.runner import Runner
from .alice import Alice
from .bob import Bob
from .common import (
    BIT0,
    BIT1,
    DB,
    DECOY,
    DM1,
    DM2,
    MON,
    COWParams,
    Tally,
    Topic,
    account,
    build,
    calibrate,
    carving,
    decode,
    describe,
    draw,
    interfering,
    monitor_counts,
    occupied,
    readouts,
    theory,
    visibilities,
    visibility,
)

PARTIES = [Alice, Bob]  # left to right on the lab table
params = COWParams()

__all__ = [
    "Alice",
    "Bob",
    "PARTIES",
    "COWParams",
    "COWResult",
    "Tally",
    "Topic",
    "BIT0",
    "BIT1",
    "DECOY",
    "DB",
    "DM1",
    "DM2",
    "MON",
    "account",
    "build",
    "calibrate",
    "carving",
    "decode",
    "describe",
    "draw",
    "interfering",
    "monitor_counts",
    "occupied",
    "readouts",
    "theory",
    "visibilities",
    "visibility",
    "run",
    "params",
]


@dataclass
class COWResult:
    params: COWParams
    tally: Tally
    expected: dict
    alice_key: np.ndarray
    bob_key: np.ndarray

    @property
    def qber(self) -> float:
        """Error rate of the whole sifted key (god's-eye: both keys are in one place)."""
        return float(np.mean(self.alice_key != self.bob_key)) if self.alice_key.size else float("nan")

    def summary(self) -> str:
        p, t, e = self.params, self.tally, self.expected
        return "\n".join([
            f"COW over {p.distance / 1e3:.0f} km, {p.source} source, μ = {p.mu}, f = {p.decoy}, t_B = {p.t_b}, "
            f"{t.bits:,} bits ({2 * t.bits:,} slots at {p.rate / 1e6:.0f} MHz)",
            f"detected {t.detected / t.bits:.3e} per bit (expected {e['detected / bit']:.3e}) · "
            f"sifted {t.sifted:,} ({t.sifted / t.bits:.3e} per bit, expected {e['sifted / bit']:.3e})",
            f"QBER {self.qber:.4f} on the whole sifted key (expected {e['QBER']:.4f})",
            f"V raw {t.v(net=False):.4f} (expected {e['V raw']:.4f}) · net {t.v():.4f} (decoy {t.v('decoy'):.4f}, "
            f"1-0 {t.v('1-0'):.4f}; expected {e['V net']:.4f})",
        ])


def run(p: COWParams | None = None, blocks: int = 4, slots: int = 400_000, seed: int | None = None,
        setup: Setup | None = None) -> COWResult:
    """Single-process reference: prepare, record and sift ``blocks`` blocks with both records in
    one place; the QBER is counted on the whole sifted key."""
    p = p or COWParams()
    setup = setup or build(p, slots)
    runner = Runner(setup, seed=seed)
    im, awg, tagger = setup["ALICE-IM"], setup["ALICE-AWG"], setup["TAGGER"]
    tally, alice_key, bob_key = Tally(), [], []
    for _ in range(blocks):
        kind = draw(runner.rng, tagger.slots // 2, p)
        awg.set_pattern("ch1", im.voltage_for(carving(occupied(kind))), 2 * p.rate)
        clicks = runner.acquire("TAGGER").clicks
        idx, bits, doubles = decode(clicks[DB], runner.rng)
        sent = kind[idx]
        decoy = sent == DECOY
        monitor = monitor_counts(kind, np.flatnonzero(clicks[DM1]), np.flatnonzero(clicks[DM2]))
        keep = np.flatnonzero(~decoy)
        tally += account(kind.size, idx.size, decoy, doubles, keep, sent[keep], bits[keep], monitor)
        alice_key.append(sent[keep].astype(np.int8))
        bob_key.append(bits[keep])
    return COWResult(p, tally, theory(setup, p), np.concatenate(alice_key), np.concatenate(bob_key))
