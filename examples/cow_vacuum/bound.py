"""The phase-error bound of COW with a vacuum decoy sequence, as arithmetic on gains.

R.-Q. Gao, Y.-M. Xie, J. Gu, W.-B. Liu, C.-X. Weng, B.-H. Li, H.-L. Yin & Z.-B. Chen,
"Simple security proof of coherent-one-way quantum key distribution", arXiv:2107.09329v5
(2023). Equation numbers below are theirs.

A gain Q_φ^{M_i} is the probability per pair that Alice sent φ and Bob announced a click
of the monitoring detector D_Mi. With |0_z⟩ = |0⟩|α⟩ and |1_z⟩ = |α⟩|0⟩ the virtual X
basis is |0_x⟩ = (|0_z⟩ + |1_z⟩)/√N⁺ and |1_x⟩ = (|0_z⟩ − |1_z⟩)/√N⁻, N^± = 2(1 ±
e^{−μ}), and the X-basis bit error rate of Eq. (5) needs the gains of |0_x⟩, which
nobody can send. (A2) writes |0_x⟩ with the two decoy sequences and the part of |α⟩|α⟩
with a photon in both pulses:

    |0_x⟩ = e^{μ/2} [e^{−μ}|0,0⟩ + |α,α⟩ − (1 − e^{−μ})|β,β⟩] / √N⁺.

Cauchy's inequality bounds the cross terms by the gains of |0,0⟩ and |α,α⟩, which Alice
and Bob count, and 0 ≤ Q_ββ ≤ 1 is all that is known of |β,β⟩. That gives (A5), and Eq.
(7) is the upper bound E_p^u on the phase error rate. No visibility enters.

Nearly all of E_p^u comes from the |β,β⟩ terms (``terms``). They grow with μ/η whatever
Bob measures, so μ has to fall with the transmittance η, and the key rate scales as η².

Everything here is plain arithmetic on public numbers. ``paper_model`` and ``optimum``
are the paper's own simulation, kept to check against its figures and to choose μ; the
secret-key rate of Eq. (6) appears only for that, since the protocol here stops at the
sifted key.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
from scipy.optimize import minimize_scalar

F = 1.1  # the error-correction efficiency of the paper's simulation


class Gains(NamedTuple):
    """Q_φ^{M_i}: for each state φ Alice sent, the probability per pair that Bob
    announces (D_M0, D_M1)."""

    q0a: tuple[float, float]  # |0⟩|α⟩, bit 0: the paper's Q_0α = Q_0z
    qa0: tuple[float, float]  # |α⟩|0⟩, bit 1: Q_α0 = Q_1z
    qaa: tuple[float, float]  # |α⟩|α⟩, decoy sequence 1
    q00: tuple[float, float]  # |0⟩|0⟩, decoy sequence 2


def normalization(mu: float) -> tuple[float, float]:
    """N⁺ and N⁻ of the virtual X basis."""
    return 2 * (1 + math.exp(-mu)), 2 * (1 - math.exp(-mu))


def nonclassical_bounds(mu: float, g: Gains) -> tuple[float, float]:
    """(A5): the upper bound on Q_0x^M1 and the lower bound on Q_0x^M0."""
    np_, nm = normalization(mu)
    grow, fall = math.exp(mu / 2), math.exp(-mu / 2)
    aa0, aa1 = math.sqrt(g.qaa[0]), math.sqrt(g.qaa[1])
    v0, v1 = math.sqrt(g.q00[0]), math.sqrt(g.q00[1])
    upper = (grow * aa1 + fall * v1) ** 2 / np_ + nm / np_ * (
        math.exp(mu) * nm / 4 + math.exp(mu) * aa1 + v1
    )
    lower = (grow * aa0 - fall * v0) ** 2 / np_ - nm / np_ * (math.exp(mu) * aa0 + v0)
    return upper, lower


def phase_error(mu: float, g: Gains) -> float:
    """Eq. (7): E_p^u, NaN with no monitoring click on a bit. It can exceed 1, where it
    bounds nothing."""
    den = 2 * (sum(g.q0a) + sum(g.qa0))
    if not den:
        return float("nan")
    np_ = normalization(mu)[0]
    upper, lower = nonclassical_bounds(mu, g)
    return (np_ * upper + 2 * (g.q0a[0] + g.qa0[0]) - np_ * lower) / den


def terms(mu: float, g: Gains) -> dict[str, float]:
    """E_p^u split by what sets each part of Eq. (7); the three add up to it.

    * ``"beta"``: the |β,β⟩ terms, bounded with Q_ββ ≤ 1. Too high a μ for the
      transmittance shows here.
    * ``"M0"``: what the |α,α⟩ and |0,0⟩ gains at D_M0 leave unexplained of a single
      pulse's. A modulator's leak (through Q_00^M0) and lost interference show here.
    * ``"M1"``: |α,α⟩ and |0,0⟩ clicks at D_M1, which should be dark: the laser's phase
      noise, the DLI's phase and dark counts show here.
    """
    den = 2 * (sum(g.q0a) + sum(g.qa0))
    if not den:
        return dict(beta=float("nan"), M0=float("nan"), M1=float("nan"))
    nm = normalization(mu)[1]
    grow, fall, e = math.exp(mu / 2), math.exp(-mu / 2), math.exp(mu)
    aa0, aa1 = math.sqrt(g.qaa[0]), math.sqrt(g.qaa[1])
    v0, v1 = math.sqrt(g.q00[0]), math.sqrt(g.q00[1])
    return dict(
        beta=nm * (e * nm / 4 + e * aa0 + v0) / den,
        M0=(2 * (g.q0a[0] + g.qa0[0]) - (grow * aa0 - fall * v0) ** 2) / den,
        M1=((grow * aa1 + fall * v1) ** 2 + nm * (e * aa1 + v1)) / den,
    )


def h(a: float) -> float:
    """Binary entropy. A bound above 1/2 says nothing and counts as 1/2."""
    a = min(a, 0.5)
    return 0.0 if a <= 0 else -a * math.log2(a) - (1 - a) * math.log2(1 - a)


def key_rate(q: float, e_p: float, e_b: float, f: float = F) -> float:
    """Eq. (6), per pair. Only for choosing μ and checking the paper's figures."""
    return q * (1 - h(e_p) - f * h(e_b))


def best_mu(model, f: float = F, low: float = 1e-7, high: float = 1.0):
    """The μ that maximizes Eq. (6) for ``model(mu) → (Gains, Q, E_b)``, and that rate.
    A coarse scan first: the rate is positive on a narrow hump near the end of a link.
    """

    def rate(log_mu: float) -> float:
        mu = 10.0**log_mu
        g, q, e_b = model(mu)
        return key_rate(q, phase_error(mu, g), e_b, f)

    grid = np.linspace(math.log10(low), math.log10(high), 61)
    i = int(np.argmax([rate(x) for x in grid]))
    a, b = grid[max(i - 1, 0)], grid[min(i + 1, grid.size - 1)]
    r = minimize_scalar(
        lambda x: -rate(x), bounds=(a, b), method="bounded", options=dict(xatol=1e-4)
    )
    return float(10.0**r.x), float(-r.fun)


# ---- the paper's simulation ----------------------------------------------------------
def paper_model(
    mu: float,
    eta: float,
    t_b: float = 0.5,
    p_d: float = 1e-8,
    e_a: float = 0.0,
    active: bool = False,
) -> tuple[Gains, float, float]:
    """Appendix A's gains on an honest channel, with Q and E_b: (Gains, Q, E_b).

    η is the channel and the detector together, p_d the dark-count probability per
    window. All of a pair's light reaches the interference, 2μ(1 − t_B)η at D_M0 for
    |α,α⟩, which a passive one-slot interferometer cannot do (``common.pair_gains``).

    The appendix leaves two things out, supplied here:

    * The data-line gains: the same model, a click in one window with the other three
      quiet.
    * The misalignment e_a: a fraction e_a of the bits are wrong and of the |α,α⟩ light
      goes to D_M1. This reading gives the end points of its Figs. 2, 3 and 5; e_a on
      the data line alone does not.

    ``active``: a switch instead of the tap, so each line gets all of η and the other
    line's factors drop out.
    """
    tau = eta if active else t_b * eta  # the data line
    rho = eta if active else (1 - t_b) * eta  # the monitoring line
    keep = 1 - p_d
    quiet_1 = 1.0 if active else keep**2 * math.exp(-tau * mu)  # D_T quiet: one pulse
    quiet_2 = 1.0 if active else keep**2 * math.exp(-2 * tau * mu)  # … two pulses
    quiet_0 = 1.0 if active else keep**2  # … none
    c1 = keep * math.exp(-rho * mu / 2)
    single = quiet_1 * c1 * (1 - c1)
    plus, minus = 2 * mu * rho * (1 - e_a), 2 * mu * rho * e_a
    aa0 = quiet_2 * keep * math.exp(-minus) * (1 - keep * math.exp(-plus))
    aa1 = quiet_2 * keep * math.exp(-plus) * (1 - keep * math.exp(-minus))
    v = quiet_0 * p_d * keep
    gains = Gains((single, single), (single, single), (aa0, aa1), (v, v))
    idle = 1.0 if active else c1**2  # D_M0 and D_M1 quiet
    right = (1 - keep * math.exp(-tau * mu)) * keep * idle
    wrong = p_d * keep * math.exp(-tau * mu) * idle
    q = right + wrong
    return gains, q, (wrong + e_a * (right - wrong)) / q


def nonclassical_gains(
    mu: float, eta: float, t_b: float = 0.5, p_d: float = 1e-8
) -> tuple[float, float]:
    """Appendix A: Q_0x^M0 and Q_0x^M1 of the nonclassical state on an honest channel,
    the gains (A5) has to bracket."""
    np_ = normalization(mu)[0]
    keep = 1 - p_d
    c1 = keep * math.exp(-(1 - t_b) * mu * eta / 2)
    c2 = 2 * math.cosh((1 - t_b) * mu * (1 - eta) / 2)
    c3 = math.exp(-t_b * mu * eta) - math.exp(-t_b * mu)
    c4 = 2 * math.cosh((1 - t_b) * mu / 2)
    fall = math.exp(-(1 + t_b) * mu / 2)
    m0 = (
        2
        / np_
        * keep**3
        * (1 - c1)
        * (fall * c2 + math.exp(-(1 - t_b) * mu * eta / 2) * c3)
    )
    m1 = 2 / np_ * keep**2 * c1 * (fall * (c4 - keep * c2) + c3 * (1 - c1))
    return m0, m1


def true_phase_error(
    mu: float, eta: float, t_b: float = 0.5, p_d: float = 1e-8
) -> float:
    """Eq. (5) from the true gains: what the bound stands above on an honest channel."""
    g = paper_model(mu, eta, t_b, p_d)[0]
    np_ = normalization(mu)[0]
    m0, m1 = nonclassical_gains(mu, eta, t_b, p_d)
    den = 2 * (sum(g.q0a) + sum(g.qa0))
    return (np_ * m1 + 2 * (g.q0a[0] + g.qa0[0]) - np_ * m0) / den


def optimum(
    eta: float,
    p_d: float = 1e-8,
    e_a: float = 0.0,
    active: bool = False,
    f: float = F,
) -> dict:
    """The paper's optimization at one transmittance: μ and t_B that maximize Eq.
    (6)."""
    best = None
    for t_b in (0.5,) if active else np.arange(0.05, 0.96, 0.025):
        mu, rate = best_mu(lambda m: paper_model(m, eta, t_b, p_d, e_a, active), f)
        if best is None or rate > best["R"]:
            best = dict(mu=mu, t_b=float(t_b), R=rate)
    g, q, e_b = paper_model(best["mu"], eta, best["t_b"], p_d, e_a, active)
    return best | dict(Q=q, E_b=e_b, E_p=phase_error(best["mu"], g))
