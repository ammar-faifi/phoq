"""Mode-pairing QKD (Zeng, Zhou, Wu & Ma, Nat. Commun. 13, 3903, 2022) on the simulated
hardware.

Physical layer (one :class:`~phoq.Setup`, viewable in the virtual lab; see
``common.build``)::

    ALICE-AWG ch1 ─┐  ch2 ─┐
    ALICE-LD → ALICE-IM → ALICE-PM → ALICE-VOA → ALICE-SPL ─┐
                                                           CHARLIE (2×2) → DET-L, DET-R
    BOB-LD   → BOB-IM   → BOB-PM   → BOB-VOA   → BOB-SPL ───┘              → TAGGER
    BOB-AWG   ch1 ─┘  ch2 ─┘

Parties, each in its own file and its own process, talking over an authenticated public
channel (TCP sockets, see :mod:`phoq.parties`):

* :class:`Alice` / :class:`Bob` (:mod:`.station`) program their own AWG with random
  intensities and phases, then pair, sift and estimate errors from Charlie's
  announcements, reconcile the sifted key (Cascade), verify it and distill the secret
  key (Toeplitz hashing sized by the MP-QKD key-rate formula, :mod:`.keyrate`).
* :class:`Charlie` (:mod:`.charlie`) reads the time tagger and announces each round's
  outcome.

``PARTIES`` and ``build()`` make this a protocol package: ``phoq serve --protocol
mpqkd``. The single-process reference ``run()`` (one god's-eye view of both records) is
kept for tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from phoq.core import Setup
from phoq.runner import Runner
from .alice import Alice
from .bob import Bob
from .charlie import Charlie
from .common import (
    B_MIXED,
    B_X,
    B_Z,
    B_ZERO,
    DET_L,
    DET_R,
    L0,
    LMU,
    LNU,
    STATIONS,
    MPQKDParams,
    Topic,
    assign_basis,
    build,
    calibrate_mu,
    draw_levels,
    pairing_rate_theory,
    sift_and_map,
    simple_pairing,
    transmissions,
)
from .keyrate import (
    Counts,
    Estimate,
    Theory,
    arm_transmittance,
    estimate,
    expected_counts,
    final_length,
    theory,
)
from .postprocess import (
    CascadeCorrector,
    CascadeReference,
    cascade_in_process,
    toeplitz_hash,
    verification_tag,
)

PARTIES = [Alice, Charlie, Bob]  # left to right on the lab table
params = MPQKDParams()

__all__ = [
    "Alice",
    "Bob",
    "Charlie",
    "PARTIES",
    "MPQKDParams",
    "MPQKDResult",
    "Topic",
    "STATIONS",
    "B_ZERO",
    "B_Z",
    "B_X",
    "B_MIXED",
    "L0",
    "LNU",
    "LMU",
    "DET_L",
    "DET_R",
    "build",
    "calibrate_mu",
    "prepare",
    "run",
    "simple_pairing",
    "pairing_rate_theory",
    "assign_basis",
    "draw_levels",
    "transmissions",
    "sift_and_map",
    "params",
    "Counts",
    "Estimate",
    "Theory",
    "arm_transmittance",
    "estimate",
    "expected_counts",
    "final_length",
    "theory",
    "CascadeReference",
    "CascadeCorrector",
    "cascade_in_process",
    "toeplitz_hash",
    "verification_tag",
]


def prepare(
    setup: Setup, p: MPQKDParams, rounds: int, rng: np.random.Generator
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """God's-eye state preparation: random intensity levels (vacuum / ν / μ) and phases
    loaded into both AWGs at once."""
    states = {}
    for tag in STATIONS:
        level = draw_levels(rng, rounds, p)
        k = rng.integers(0, p.D, size=rounds)
        laser, im, pm, awg = (setup[f"{tag}-{x}"] for x in ("LD", "IM", "PM", "AWG"))
        awg.set_pattern(
            "ch1", im.voltage_for(transmissions(p)[level]), rate=laser.rep_rate
        )
        awg.set_pattern("ch2", pm.voltage_for(2 * np.pi * k / p.D), rate=laser.rep_rate)
        states[tag] = (level, k)
    return states


@dataclass
class MPQKDResult:
    params: MPQKDParams
    rounds: int
    single_clicks: int
    double_clicks: int
    rows: list = field(default_factory=list)  # (l, r_p sim, r_p Eq.4, #Z, E_Z, #X, E_X)

    @property
    def p_click(self) -> float:
        return self.single_clicks / self.rounds

    def summary(self) -> str:
        lines = [
            (
                f"MP-QKD over {self.params.distance / 1e3:.0f} km, μ ="
                f" {self.params.mu}, {self.rounds:,} rounds"
            ),
            (
                f"single clicks {self.single_clicks:,} (p = {self.p_click:.3e}), double"
                f" clicks {self.double_clicks:,}"
            ),
            (
                f"{'l':>8} | {'r_p sim':>10} | {'r_p Eq.4':>10} | {'#Z':>7} |"
                f" {'E_Z':>7} | {'#X':>6} | {'E_X':>6}"
            ),
        ]
        for l, rs, rt, nz, ez, nx, ex in self.rows:
            lines.append(
                f"{l:>8} | {rs:>10.3e} | {rt:>10.3e} | {nz:>7} | {ez:>7.4f} | {nx:>6} |"
                f" {ex:>6.3f}"
            )
        return "\n".join(lines)


def run(
    p: MPQKDParams | None = None,
    rounds: int = 1_000_000,
    l_values=(1, 10, 100, 1000, 10_000),
    seed: int | None = None,
    setup: Setup | None = None,
) -> MPQKDResult:
    """Single-process reference: prepare, record, post-process with both records in one
    place."""
    p = p or MPQKDParams()
    setup = setup or build(p, rounds)
    setup["TAGGER"].slots = rounds
    runner = Runner(setup, seed=seed)
    states = prepare(setup, p, rounds, runner.rng)
    clicks = runner.acquire("TAGGER").clicks
    left, right = clicks["ch1"], clicks["ch2"]
    C = left ^ right
    detector = np.where(left, DET_L, DET_R)
    click_idx = np.flatnonzero(C)
    (lev_a, ph_a), (lev_b, ph_b) = states["ALICE"], states["BOB"]
    res = MPQKDResult(
        p, rounds, int(click_idx.size), int(np.count_nonzero(left & right))
    )
    for l in l_values:
        F, R = simple_pairing(click_idx, l)
        s = sift_and_map(F, R, lev_a, ph_a, lev_b, ph_b, detector, p.D)
        ez = float(np.mean(s["ka_z"] != s["kb_z"])) if s["n_z"] else float("nan")
        ex = float(np.mean(s["ka_x"] != s["kb_x"])) if s["n_x"] else float("nan")
        res.rows.append(
            (
                l,
                F.size / rounds,
                pairing_rate_theory(res.p_click, l),
                s["n_z"],
                ez,
                s["n_x"],
                ex,
            )
        )
    return res
