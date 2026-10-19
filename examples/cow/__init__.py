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

With an eavesdropper (``--party examples/cow/eve.py``) the spool is cut in two and Eve's station
spliced in between (``common.eavesdropper``)::

    SPL-A ─→ EVE-SW a1   b1 ─→ SPL-B ─→ BOB-TAP …
             EVE-SW a2   b2 ─→ EVE-D → EVE-TAGGER
    EVE-TX-LD (CW) → EVE-TX-IM → EVE-TX-VOA ─┘   (EVE-AWG: CH1 → IM, CH2 → switch)

Parties, each in its own file and its own process, talking over an authenticated public channel:

* :class:`Alice` (:mod:`.alice`) draws the sequence, removes the decoys from Bob's detections,
  discloses a sample of her bits and computes the visibilities.
* :class:`Bob` (:mod:`.bob`) announces which bits clicked and his monitoring clicks, and answers
  with his bits at the sample.
* Eve (``eve.py``, loaded with ``--party``) intercepts a fraction of the bits and re-sends them.

``PARTIES`` and ``build()`` make this a protocol package: ``phoq run --protocol cow``. The
single-process reference ``run()`` (one god's-eye view of every record, Eve's too) and
``theory()`` / ``eve_theory()`` are kept for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    NOTHING,
    COWParams,
    EveParams,
    Tally,
    Topic,
    account,
    build,
    calibrate,
    carving,
    decode,
    describe,
    draw,
    eve_theory,
    intercept,
    interfering,
    monitor_counts,
    occupied,
    readouts,
    resend_attenuation,
    resend_mu,
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
    "NOTHING",
    "EveParams",
    "account",
    "build",
    "calibrate",
    "carving",
    "decode",
    "describe",
    "draw",
    "eve_theory",
    "intercept",
    "interfering",
    "monitor_counts",
    "occupied",
    "readouts",
    "resend_attenuation",
    "resend_mu",
    "theory",
    "visibilities",
    "visibility",
    "eavesdrop",
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
    eve: dict = field(default_factory=dict)  # Eve's counts, when she was on the line

    @property
    def qber(self) -> float:
        """Error rate of the whole sifted key (god's-eye: both keys are in one place)."""
        return float(np.mean(self.alice_key != self.bob_key)) if self.alice_key.size else float("nan")

    def summary(self) -> str:
        p, t, e, v = self.params, self.tally, self.expected, self.eve
        lines = [
            f"COW over {p.distance / 1e3:.0f} km, {p.source} source, μ = {p.mu}, f = {p.decoy}, t_B = {p.t_b}, "
            f"{t.bits:,} bits ({2 * t.bits:,} slots at {p.rate / 1e6:.0f} MHz)",
            f"detected {t.detected / t.bits:.3e} per bit (expected {e['detected / bit']:.3e}) · "
            f"sifted {t.sifted:,} ({t.sifted / t.bits:.3e} per bit, expected {e['sifted / bit']:.3e})",
            f"QBER {self.qber:.4f} on the whole sifted key (expected {e['QBER']:.4f})",
        ]
        if not v:
            lines.append(f"V raw {t.v(net=False):.4f} (expected {e['V raw']:.4f}) · net {t.v():.4f} (decoy "
                         f"{t.v('decoy'):.4f}, 1-0 {t.v('1-0'):.4f}; expected {e['V net']:.4f})")
            return "\n".join(lines)
        return "\n".join(lines + [
            f"V raw {t.v(net=False):.4f} · net {t.v():.4f} · decoy {t.v('decoy'):.4f} (expected {e['V decoy']:.4f}), "
            f"1-0 {t.v('1-0'):.4f} (expected {e['V 1-0']:.4f}); {e['V net']:.4f} without Eve",
            f"Eve intercepted {v['attacked'] / v['bits']:.0%} of the bits · clicks per pulse {v['q']:.3f} (expected "
            f"{e['q (Eve)']:.3f}) · re-sent μ_E {v['mu_e']:.3f} (expected {e['mu_E']:.3f}) · paper: 1 − V = I·ξ "
            f"= {e['1 - V (paper)']:.4f}",
        ])


def eavesdrop(setup: Setup, runner: Runner, p: COWParams, attack: np.ndarray, att0: float,
              inconclusive: str = "empty") -> dict:
    """One block of Eve's, as ``eve.py`` runs it: switch the attacked bits into her detector,
    record her tagger, re-prepare what she saw and program her transmitter before Bob records."""
    sw, im, awg = setup["EVE-SW"], setup["EVE-TX-IM"], setup["EVE-AWG"]
    awg.set_pattern("ch2", sw.voltage_for(np.repeat(attack, 2)), p.rate)
    awg.set_pattern("ch1", [float(im.voltage_for(0.0))], 2 * p.rate)  # dark while she listens
    d = runner.acquire("EVE-TAGGER").clicks["ch1"]
    c0, c1 = d[0::2], d[1::2]
    occ = occupied(intercept(c0, c1, attack, inconclusive))
    clicks = int(np.count_nonzero(c0 & attack) + np.count_nonzero(c1 & attack))
    mu_e = resend_mu(clicks, int(attack.sum()), int(occ.sum()), setup["EVE-D"].efficiency, p)
    setup["EVE-TX-VOA"].attenuation_db = resend_attenuation(att0, mu_e, p)
    awg.set_pattern("ch1", im.voltage_for(carving(occ)), 2 * p.rate)
    return dict(bits=attack.size, attacked=int(attack.sum()), clicks=clicks, mu_e=mu_e)


def run(p: COWParams | None = None, blocks: int = 4, slots: int = 400_000, seed: int | None = None,
        setup: Setup | None = None, eve: float | None = None, inconclusive: str = "empty") -> COWResult:
    """Single-process reference: prepare, record and sift ``blocks`` blocks with both records in
    one place; the QBER is counted on the whole sifted key.

    ``eve`` splices Eve's station in and has her intercept that fraction of the bits (0: the
    station on the line, idle), with ``inconclusive`` as in ``eve.py``.
    """
    p = p or COWParams()
    setup = setup or build(p, slots, eve=eve is not None)
    runner = Runner(setup, seed=seed)
    im, awg, tagger = setup["ALICE-IM"], setup["ALICE-AWG"], setup["TAGGER"]
    tally, alice_key, bob_key = Tally(), [], []
    att0 = setup["EVE-TX-VOA"].attenuation_db if eve is not None else None
    seen = dict(bits=0, attacked=0, clicks=0)
    for _ in range(blocks):
        kind = draw(runner.rng, tagger.slots // 2, p)
        awg.set_pattern("ch1", im.voltage_for(carving(occupied(kind))), 2 * p.rate)
        if eve is not None:
            block = eavesdrop(setup, runner, p, runner.rng.random(kind.size) < eve, att0, inconclusive)
            seen = {k: seen[k] + block[k] for k in seen} | dict(mu_e=block["mu_e"])
        clicks = runner.acquire("TAGGER").clicks
        idx, bits, doubles = decode(clicks[DB], runner.rng)
        sent = kind[idx]
        decoy = sent == DECOY
        monitor = monitor_counts(kind, np.flatnonzero(clicks[DM1]), np.flatnonzero(clicks[DM2]))
        keep = np.flatnonzero(~decoy)
        tally += account(kind.size, idx.size, decoy, doubles, keep, sent[keep], bits[keep], monitor)
        alice_key.append(sent[keep].astype(np.int8))
        bob_key.append(bits[keep])
    expected = theory(setup, p)
    if eve is not None:
        setup["EVE-TX-VOA"].attenuation_db = att0
        expected |= eve_theory(setup, p, eve)
        seen["q"] = seen["clicks"] / (seen["attacked"] * (1 + p.decoy)) if seen["attacked"] else float("nan")
    return COWResult(p, tally, expected, np.concatenate(alice_key), np.concatenate(bob_key),
                     seen if eve is not None else {})
