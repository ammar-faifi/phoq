"""The MP-QKD secret-key rate: decoy bounds, the honest-channel model and the theory
curve.

Everything here is public arithmetic on public numbers, so both stations run it on the
same announcements and arrive at the same privacy-amplification size ([CLASSICAL #6] in
``examples/mpqkd/reference.py``).

The key rate of mode-pairing QKD (Zeng, Zhou, Wu & Ma, Nat. Commun. 13, 3903 (2022), Eq.
(7), Methods Eq. (B23) and Appendix C–D) is

    R = r_p · r_s · { q_11 [1 − h(e_11^X)] − f h(E^Z_μμ) }            [bits per pulse]

* ``r_p``  pairs per pulse (Eq. 4), ``r_s`` the share of pairs that are signal Z pairs,
* ``q_11`` the fraction of those Z pairs in which Alice's pair and Bob's pair each
  carried exactly one photon (the "single-photon pair" component), ``e_11^X`` its
  phase-error rate, ``E^Z_μμ`` the bit-error rate of the Z key and ``f h(E^Z)`` the
  error-correction leak per key bit.

Decoy bounds (Appendix C of the paper). A pair is a *total-intensity* object: Alice's
pair (μ_i, μ_j) has total μ_i + μ_j and its photon number is Poissonian in that total
(Lemma 4). The parties count, per block, how many pairs of every total-intensity class
(A, B) Charlie reported and divide by the class's prior probability; the normalization
constant is arbitrary (the paper's N_p, Eq. (C18)–(C19)) because the standard MDI
two-decoy bounds are homogeneous in the gains:

    Y_11 ≥ [μ³ G(ν,ν) − ν³ G(μ,μ)] / [μ² ν² (μ − ν)]          vacuum + weak decoy,
    G(A,B) = F(A,B) − F(A,0) − F(0,B) + F(0,0)                 assuming only Y_nm ≥ 0
    F(A,B) = e^{A+B} Q_AB

    (e Y)_11 ≤ [H(ν,ν) − H(ν,0) − H(0,ν) + H(0,0)] / ν²,   H(A,B) = e^{A+B} E_AB Q_AB

applied with totals {0, ν, μ} to the Z-type pairs (one vacuum pulse) and {0, 2ν, 2μ} to
the X-type pairs (two equal pulses). A pair in which one party sent vacuum twice gives
an X error of exactly 1/2, so the vacuum error gains need no disclosed bits.

Statistics. The phase-error bound is a *difference* of X-basis error gains of the
weakest decoy class, which has to be known to ≈1 % — that takes ~10⁹–10¹² pulses, which
is what the experiments use and why the paper's curves are asymptotic. Moreover the
X-type pairs carry *twice* the pulse intensity (totals 2ν, 2μ) while their
single-photon-pair yield is only η²/2 (the two photons bunch half of the time), so at
this showcase's 2ν = 0.2 and 2μ = 0.8 the two-intensity lower bound on the X-type Y_11
does not close even asymptotically; a rigorous e_11^X needs a weaker pair of X
intensities on top of the statistics. A 20-block showcase run has a few million pulses,
so by default the parties size privacy amplification with ``estimator="model"``: q_11
from the decoy bound on the run's own Z-type counts (that one resolves at this scale:
~60 (ν,ν) Z pairs per 20 blocks) and e_11^X from the honest-channel model (laser
linewidths and detuning, dark counts, the observed pairing intervals; Eq. (D15) of the
paper — the same choice the paper's simulation appendix makes), checked against the
measured X-basis error rate. The rigorous decoy bound is always computed and reported,
as "unresolved" (NaN) while it cannot be formed; ``estimator="decoy"`` sizes with it
instead and then yields no key here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .common import B_X, B_Z, B_ZERO, L0, LNU, LMU, MPQKDParams, pairing_rate_theory

E0 = 0.5  # error rate of vacuum events
LEVELS = (L0, LNU, LMU)
MIN_DECOY_X_PAIRS = (
    100  # below this the decoy phase-error bound is reported as unresolved
)


# ---- small math ---------------------------------------------------------------------
def h(x: float) -> float:
    """Binary entropy."""
    x = float(np.clip(x, 0.0, 1.0))
    if x <= 0.0 or x >= 1.0:
        return 0.0
    return -x * math.log2(x) - (1 - x) * math.log2(1 - x)


def poisson(n: int, mean: float) -> float:
    return math.exp(-mean) * mean**n / math.factorial(n)


def intensities(p: MPQKDParams) -> np.ndarray:
    """Photons per pulse for the levels vacuum / ν / μ, with the modulator's extinction
    leakage."""
    leak = p.mu / 10 ** (p.extinction_db / 10)
    return np.array([leak, p.nu, p.mu])


def level_probabilities(p: MPQKDParams) -> np.ndarray:
    return np.array([1.0 - p.p_mu - p.p_nu, p.p_nu, p.p_mu])


# ---- the counts both stations keep (public) ------------------------------------------
@dataclass
class Counts:
    """Pair counts per total-intensity class, accumulated over blocks from public
    announcements.

    ``z[A, B]``: pairs where Alice's pair is Z-type with bright level A
    (A = 0: she sent vacuum twice) and Bob's is Z-type with level B. ``x[A, B]``: X-type
    pairs (two equal pulses of level A / B; level 0 is again vacuum twice), ``x_sifted``
    those with matching alignment angles and ``x_errors`` the disclosed-bit errors among
    the sifted (X, X) classes. The key comes from ``z[LMU, LMU]``; ``z_sample`` /
    ``z_sample_errors`` are the disclosed Z sample.
    """

    rounds: int = 0
    clicks: int = 0  # single-click rounds Charlie announced
    pairs: int = 0
    z: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=np.int64))
    x: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=np.int64))
    x_sifted: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 3), dtype=np.int64)
    )
    x_errors: np.ndarray = field(
        default_factory=lambda: np.zeros((3, 3), dtype=np.int64)
    )
    z_sample: int = 0
    z_sample_errors: int = 0
    interval_sum: float = (
        0.0  # Σ (rear − front) over key pairs, for the phase-drift model
    )
    linewidth: tuple[float, float] = (0.0, 0.0)  # announced laser linewidths [Hz]
    detuning: float = 0.0  # frequency offset between the lasers [Hz]

    def add_block(
        self,
        n_rounds: int,
        n_clicks: int,
        basis_a: np.ndarray,
        level_a: np.ndarray,
        basis_b: np.ndarray,
        level_b: np.ndarray,
        theta_match: np.ndarray,
        x_err_mask: np.ndarray | None,
        intervals: np.ndarray,
    ) -> None:
        """Tally one block's pairs. ``x_err_mask`` marks errors among the (X, X) pairs
        that were sifted and disclosed (same order as the sifted (X, X) pairs)."""
        self.rounds += int(n_rounds)
        self.clicks += int(n_clicks)
        self.pairs += int(basis_a.size)
        ztype_a = (basis_a == B_Z) | (basis_a == B_ZERO)
        ztype_b = (basis_b == B_Z) | (basis_b == B_ZERO)
        xtype_a = (basis_a == B_X) | (basis_a == B_ZERO)
        xtype_b = (basis_b == B_X) | (basis_b == B_ZERO)
        la, lb = level_a.astype(np.int64), level_b.astype(np.int64)
        both_z = ztype_a & ztype_b
        np.add.at(self.z, (la[both_z], lb[both_z]), 1)
        both_x = xtype_a & xtype_b
        np.add.at(self.x, (la[both_x], lb[both_x]), 1)
        sx = both_x & theta_match
        np.add.at(self.x_sifted, (la[sx], lb[sx]), 1)
        if x_err_mask is not None:
            xx = (basis_a == B_X) & (basis_b == B_X) & theta_match
            np.add.at(self.x_errors, (la[xx][x_err_mask], lb[xx][x_err_mask]), 1)
        key = (basis_a == B_Z) & (basis_b == B_Z) & (la == LMU) & (lb == LMU)
        self.interval_sum += float(intervals[key].sum())

    @property
    def key_pairs(self) -> int:
        return int(self.z[LMU, LMU])

    @property
    def mean_interval(self) -> float:
        return self.interval_sum / self.key_pairs if self.key_pairs else 0.0


# ---- decoy bounds --------------------------------------------------------------------
def decoy_y11_lower(gain: np.ndarray, total: np.ndarray) -> float:
    """Vacuum + weak decoy lower bound on the single-photon-pair yield from the 3×3 gain
    table.

    ``gain[A, B]`` are the pair gains per prior probability for totals ``total =
    (0, ν', μ')`` (ν' = ν, μ' = μ for Z-type pairs; 2ν, 2μ for X-type). Only Y_nm ≥ 0 is
    used, so no photon-number cut-off and no absolute normalization are needed. Clipped
    at 0 (statistical noise).
    """
    t0, nu, mu = (float(v) for v in total)
    F = np.exp(total[:, None] + total[None, :]) * gain
    G = lambda i, j: F[i, j] - F[i, 0] - F[0, j] + F[0, 0]
    y11 = (mu**3 * G(1, 1) - nu**3 * G(2, 2)) / (mu**2 * nu**2 * (mu - nu))
    return max(0.0, float(y11))


def decoy_ey11_upper(error_gain: np.ndarray, total: np.ndarray) -> float:
    """Upper bound on e_11·Y_11 from the error-gain table
    (same normalization as the gains)."""
    nu = float(total[1])
    H = np.exp(total[:, None] + total[None, :]) * error_gain
    ey = (H[1, 1] - H[1, 0] - H[0, 1] + H[0, 0]) / nu**2
    return max(0.0, float(ey))


def z_priors(p: MPQKDParams) -> np.ndarray:
    """Prior probability that one party's pair is Z-type with bright level A
    (A = 0: two vacua)."""
    s = level_probabilities(p)
    return np.array([s[L0] ** 2, 2 * s[L0] * s[LNU], 2 * s[L0] * s[LMU]])


def x_priors(p: MPQKDParams) -> np.ndarray:
    """Prior probability that one party's pair is X-type with level A
    (two equal pulses)."""
    s = level_probabilities(p)
    return s**2


def gains(counts: np.ndarray, priors: np.ndarray) -> np.ndarray:
    """Pair gains per prior probability (common normalization constant dropped)."""
    return counts / (priors[:, None] * priors[None, :])


# ---- the honest-channel model --------------------------------------------------------
def single_click(a: float, b: float, p_dark: float, n_phase: int = 64) -> float:
    """Probability that exactly one of Charlie's detectors clicks in a bin where Alice's
    pulse carries ``a`` and Bob's ``b`` *detected* photons on average, averaged over
    their relative phase.
    """
    psi = (np.arange(n_phase) + 0.5) * 2 * np.pi / n_phase
    cross = 2 * math.sqrt(a * b) * np.cos(psi)
    n_l, n_r = (a + b + cross) / 2, (a + b - cross) / 2
    p_l, p_r = 1 - (1 - p_dark) * np.exp(-n_l), 1 - (1 - p_dark) * np.exp(-n_r)
    return float(np.mean(p_l * (1 - p_r) + p_r * (1 - p_l)))


def single_click_photons(n: int, eta: float, p_dark: float) -> float:
    """Exactly one click when ``n`` photons from one party reach the 50:50 splitter."""
    p = 1 - (1 - p_dark) * (1 - eta / 2) ** n
    return 2 * p * (1 - p)


def misalignment(
    linewidth: tuple[float, float],
    detuning: float,
    rep_rate: float,
    intervals,
    weights=None,
) -> float:
    """Phase-error rate of the single-photon pair from the lasers' phase walk between
    the two paired rounds: e_d = ⟨sin²(ε/2)⟩ with ε ~ N(2πΔf τ, 2π(Δν_a + Δν_b) τ) over
    the intervals τ.
    """
    tau = np.asarray(intervals, dtype=float) / rep_rate
    var = 2 * np.pi * (linewidth[0] + linewidth[1]) * tau
    e = 0.5 * (1 - np.exp(-var / 2) * np.cos(2 * np.pi * detuning * tau))
    return float(np.average(e, weights=weights))


def e11_model(
    e_d: float, eta_a: float, eta_b: float, p_dark: float
) -> tuple[float, float]:
    """Single-photon-pair X yield and phase-error rate, Eq. (D15) of the paper
    (Ma–Fung–Razavi)."""
    y11 = (1 - p_dark) ** 2 * (
        eta_a * eta_b / 2
        + (2 * eta_a + 2 * eta_b - 3 * eta_a * eta_b) * p_dark
        + 4 * (1 - eta_a) * (1 - eta_b) * p_dark**2
    )
    ey = E0 * y11 - (E0 - e_d) * (1 - p_dark**2) * eta_a * eta_b / 2
    return y11, (ey / y11 if y11 > 0 else E0)


def interval_pmf(p_click: float, l: int) -> tuple[np.ndarray, np.ndarray]:
    """Distribution of the gap between a front click and its paired rear click
    (geometric, cut at l)."""
    k = np.arange(1, l + 1)
    w = p_click * (1 - p_click) ** (k - 1)
    return k, w / w.sum()


def fit_transmittance(p_click: float, p: MPQKDParams) -> float:
    """Arm transmittance (fiber + connectors + detector) that reproduces Charlie's
    single-click probability, assuming a symmetric link — the parties' only use of a
    channel model.
    """
    s, I = level_probabilities(p), intensities(p)
    p_dark = -math.expm1(-p.dark_count_rate / p.rep_rate)

    def model(eta):
        return sum(
            s[i] * s[j] * single_click(eta * I[i], eta * I[j], p_dark)
            for i in LEVELS
            for j in LEVELS
        )

    lo, hi = 1e-7, 1.0
    if model(hi) <= p_click:
        return hi
    for _ in range(60):
        mid = math.sqrt(lo * hi)
        if model(mid) < p_click:
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def x_error_model(
    a: float, b: float, e_d: float, p_dark: float, n_phase: int = 48
) -> float:
    """X-basis error rate of sifted (a,a)&(b,b) pairs (detected mean photon numbers)
    when the relative phase walks by ε between the two rounds, with ⟨sin²(ε/2)⟩ = e_d.
    Coherent-state interference on the 50:50 splitter; the alignment-angle match fixes
    the announced relative phase to 0 or π, so the error is the fraction of click
    patterns that contradict it.
    """
    psi = (np.arange(n_phase) + 0.5) * 2 * np.pi / n_phase
    eps = np.arccos(np.clip(1 - 2 * e_d, -1, 1))  # a representative phase walk

    def lr(phase):
        cross = 2 * math.sqrt(a * b) * np.cos(phase)
        n_l, n_r = (a + b + cross) / 2, (a + b - cross) / 2
        p_l, p_r = 1 - (1 - p_dark) * np.exp(-n_l), 1 - (1 - p_dark) * np.exp(-n_r)
        return p_l * (1 - p_r), p_r * (1 - p_l)

    fl, fr = lr(psi)
    fl2, fr2 = lr(psi + eps)
    wrong = np.mean(fl * fr2 + fr * fl2)
    total = np.mean((fl + fr) * (fl2 + fr2))
    return float(wrong / total) if total > 0 else E0


# ---- the estimate the parties agree on -----------------------------------------------
@dataclass
class Estimate:
    """What the accumulated counts say, as both stations compute it."""

    estimator: str
    q11_lower: (
        float  # decoy lower bound on the single-photon-pair fraction of the key pairs
    )
    e11_model: float  # model phase-error rate of the single-photon pair
    e11_decoy: (
        float  # rigorous decoy upper bound; NaN while unresolved (needs ~1e9+ pulses)
    )
    e11_used: float
    e_d: float  # misalignment from the phase walk
    eta_fit: float
    e_x_measured: float  # E_X of the sifted signal X pairs
    e_x_model: float
    n_x_decoy: int  # sifted (ν,ν)&(ν,ν) X pairs — why the decoy bound is unresolved
    p_click: float
    pairs_per_round: float
    key_share: float  # key pairs / pairs (r_s)

    @property
    def secret_fraction(self) -> float:
        """Secret bits per corrected key bit before the error-correction leak."""
        return self.q11_lower * (1 - h(self.e11_used))


def q11_lower_from_z(z: np.ndarray, p: MPQKDParams) -> float:
    """Decoy lower bound on the single-photon-pair fraction of the (μ, μ) Z pairs from
    the Z-type table."""
    gz = gains(np.asarray(z, dtype=float), z_priors(p))
    if gz[LMU, LMU] <= 0:
        return 0.0
    y11 = decoy_y11_lower(gz, np.array([0.0, p.nu, p.mu]))
    return min(1.0, poisson(1, p.mu) ** 2 * y11 / gz[LMU, LMU])


def estimate(c: Counts, p: MPQKDParams) -> Estimate:
    """The asymptotic estimate from the public counts (identical at both stations)."""
    I = intensities(p)
    p_dark = -math.expm1(-p.dark_count_rate / p.rep_rate)
    totals_z = np.array([0.0, p.nu, p.mu])
    q11 = q11_lower_from_z(c.z, p)  # from the Z-type decoy classes
    # the rigorous phase-error bound from the X-type decoy classes
    gx = gains(c.x.astype(float), x_priors(p))
    totals_x = 2 * totals_z
    y11_x = decoy_y11_lower(gx, totals_x)
    sift = c.x_sifted.sum() / c.x.sum() if c.x.sum() else 2.0 / p.D
    eg = np.zeros((3, 3))
    gs = gains(c.x_sifted.astype(float), x_priors(p))
    for i in LEVELS:
        for j in LEVELS:
            if i == L0 or j == L0:
                eg[i, j] = (
                    E0 * gs[i, j]
                )  # a vacuum pair's bit is independent of everything
            else:
                eg[i, j] = gains(c.x_errors.astype(float), x_priors(p))[i, j]
    ey = decoy_ey11_upper(eg, totals_x)
    n_decoy = int(c.x_sifted[LNU, LNU])
    if y11_x > 0 and sift > 0 and n_decoy >= MIN_DECOY_X_PAIRS:
        e11_decoy = min(E0, ey / (sift * y11_x))
    # too few (ν,ν) X pairs for a difference of error gains: unresolved, not "zero"
    else:
        e11_decoy = float("nan")
    # the honest-channel model
    p_click = c.clicks / c.rounds if c.rounds else 0.0
    eta = fit_transmittance(p_click, p) if p_click > 0 else 0.0
    tau = c.mean_interval or 1.0 / max(p_click, 1e-9)
    e_d = misalignment(c.linewidth, c.detuning, p.rep_rate, [tau])
    _, e11_m = e11_model(e_d, eta, eta, p_dark)
    n_xx = int(c.x_sifted[LMU, LMU])
    e_x = c.x_errors[LMU, LMU] / n_xx if n_xx else float("nan")
    e_x_m = x_error_model(eta * I[LMU], eta * I[LMU], e_d, p_dark) if eta > 0 else E0
    if n_xx and math.isfinite(
        e_x
    ):  # more X errors than the model: inflate the phase walk
        sigma = math.sqrt(max(e_x * (1 - e_x), 0.25 / n_xx) / n_xx)
        if e_x - 2 * sigma > e_x_m:
            lo, hi = e_d, 0.5
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                if (
                    x_error_model(eta * I[LMU], eta * I[LMU], mid, p_dark)
                    < e_x - 2 * sigma
                ):
                    lo = mid
                else:
                    hi = mid
            e_d = 0.5 * (lo + hi)
            _, e11_m = e11_model(e_d, eta, eta, p_dark)
    if p.estimator == "decoy":
        used = e11_decoy if math.isfinite(e11_decoy) else E0  # unresolved: no key
    else:
        used = e11_m
    return Estimate(
        estimator=p.estimator,
        q11_lower=q11,
        e11_model=e11_m,
        e11_decoy=e11_decoy,
        e11_used=used,
        e_d=e_d,
        eta_fit=eta,
        e_x_measured=e_x,
        e_x_model=e_x_m,
        n_x_decoy=int(c.x_sifted[LNU, LNU]),
        p_click=p_click,
        pairs_per_round=c.pairs / c.rounds if c.rounds else 0.0,
        key_share=c.key_pairs / c.pairs if c.pairs else 0.0,
    )


def final_length(n: int, est: Estimate, leak_ec: int, leak_verify: int) -> int:
    """Asymptotic privacy-amplification output for ``n`` corrected key bits."""
    return max(0, int(math.floor(n * est.secret_fraction - leak_ec - leak_verify)))


def finite_key_length(
    n: int, c: Counts, p: MPQKDParams, est: Estimate, leak_ec: int, leak_verify: int
) -> int:
    """A *simplified* finite-key estimate — clearly not the paper's full analysis:
    Chernoff-style fluctuations (ε = ``p.eps``) on the decoy counts behind q_11, a
    Hoeffding term on the phase-error rate from the signal X sample, and the usual
    ε-terms for verification and privacy amplification."""
    ln = math.log(1 / p.eps)
    dev = lambda m: math.sqrt(2 * m * ln) + ln
    z = c.z.astype(float)
    worst = z.copy()
    worst[LNU, LNU] = max(
        0.0, z[LNU, LNU] - dev(z[LNU, LNU])
    )  # the term that pulls Y_11 down
    worst[LMU, LMU] = z[LMU, LMU] + dev(z[LMU, LMU])
    worst[L0, :] += dev(z[L0, :].sum()) / 3
    worst[:, L0] += dev(z[:, L0].sum()) / 3
    q11 = q11_lower_from_z(worst, p)
    n_x = max(1, int(c.x_sifted[LMU, LMU]))
    e11 = min(E0, est.e11_used + math.sqrt(ln / (2 * n_x)))
    bits = (
        n * q11 * (1 - h(e11))
        - leak_ec
        - leak_verify
        - 2 * math.log2(1 / p.eps)
        - math.log2(2 / p.eps)
    )
    return max(0, int(math.floor(bits)))


# ---- theory --------------------------------------------------------------------------
@dataclass
class Theory:
    distance: float
    eta_arm: float
    p_click: float
    pairs_per_round: float
    key_share: float
    q11: float
    e11: float
    e_z: float
    e_x: float
    e_d: float
    rate: float  # bits per pulse with the ideal (1,1) component, Eq. (D1)
    rate_bounds: float  # the same with q_11 from the decoy bound the parties use
    q11_lower: float


def _class_tables(p: MPQKDParams, eta_arm: float):
    """Per-round click probabilities and the expected Z- and X-type pair tables
    (per pair)."""
    s, I = level_probabilities(p), intensities(p)
    p_dark = -math.expm1(-p.dark_count_rate / p.rep_rate)
    click = np.array(
        [
            [single_click(eta_arm * I[i], eta_arm * I[j], p_dark) for j in LEVELS]
            for i in LEVELS
        ]
    )
    p_click = float((s[:, None] * s[None, :] * click).sum())
    w = (
        s[:, None] * s[None, :] * click / p_click
    )  # class distribution of a clicked round
    z_tab, x_tab = np.zeros((3, 3)), np.zeros((3, 3))
    for ai in LEVELS:
        for aj in LEVELS:
            for bi in LEVELS:
                for bj in LEVELS:
                    prob = w[ai, bi] * w[aj, bj]
                    za = (ai == L0) != (aj == L0) or (ai == L0 and aj == L0)
                    zb = (bi == L0) != (bj == L0) or (bi == L0 and bj == L0)
                    if za and zb:
                        z_tab[max(ai, aj), max(bi, bj)] += prob
                    if ai == aj and bi == bj:
                        x_tab[ai, bi] += prob
    return p_dark, click, p_click, w, z_tab, x_tab


def theory(
    p: MPQKDParams, eta_arm: float, distance: float | None = None, f_ec: float = 1.16
) -> Theory:
    """Eq. (D1)–(D15) of the paper on this showcase's parameters: ``eta_arm`` is the
    transmittance from a station's spool input to a click (fiber, connectors, detector
    efficiency).

    ``rate`` uses the ideal single-photon-pair quantities (the paper's curves);
    ``rate_bounds`` replaces q_11 by the vacuum + weak decoy bound evaluated on the
    model's own gains, i.e. what the parties' estimator converges to with infinite
    statistics."""
    s, I = level_probabilities(p), intensities(p)
    p_dark, click, p_click, w, z_tab, x_tab = _class_tables(p, eta_arm)
    r_p = pairing_rate_theory(p_click, p.l)
    key_share = 2 * w[L0, LMU] * w[LMU, L0]  # complementary (0,μ)&(μ,0) pairs …
    wrong = (
        2 * w[L0, L0] * w[LMU, LMU]
    )  # … and both bright in the same pulse: a Z error
    e_z = wrong / (key_share + wrong)
    key_share += wrong
    y1 = single_click_photons(1, eta_arm, p_dark)
    q11 = 2 * (s[L0] * s[LMU] * poisson(1, p.mu) * y1 / p_click) ** 2 / key_share
    k, pmf = interval_pmf(p_click, p.l)
    e_d = misalignment((p.linewidth, p.linewidth), 0.0, p.rep_rate, k, pmf)
    _, e11 = e11_model(e_d, eta_arm, eta_arm, p_dark)
    e_x = x_error_model(eta_arm * I[LMU], eta_arm * I[LMU], e_d, p_dark)
    rate = r_p * key_share * (q11 * (1 - h(e11)) - f_ec * h(e_z))
    q11_lower = q11_lower_from_z(z_tab, p)
    rate_bounds = r_p * key_share * (q11_lower * (1 - h(e11)) - f_ec * h(e_z))
    return Theory(
        distance=distance if distance is not None else p.distance,
        eta_arm=eta_arm,
        p_click=p_click,
        pairs_per_round=r_p,
        key_share=key_share,
        q11=q11,
        e11=e11,
        e_z=e_z,
        e_x=e_x,
        e_d=e_d,
        rate=max(0.0, rate),
        rate_bounds=max(0.0, rate_bounds),
        q11_lower=q11_lower,
    )


def expected_counts(p: MPQKDParams, eta_arm: float, pairs: int = 10**9) -> Counts:
    """The :class:`Counts` an infinitely patient run would accumulate (expectation
    values for ``pairs`` pairs): the stations' estimator applied to them must reproduce
    :func:`theory`.
    """
    p_dark, click, p_click, w, z_tab, x_tab = _class_tables(p, eta_arm)
    I = intensities(p)
    r_p = pairing_rate_theory(p_click, p.l)
    c = Counts()
    c.pairs = pairs
    c.rounds = int(round(pairs / r_p))
    c.clicks = int(round(c.rounds * p_click))
    c.z = np.rint(z_tab * pairs).astype(np.int64)
    c.x = np.rint(x_tab * pairs).astype(np.int64)
    c.x_sifted = np.rint(x_tab * pairs * 2 / p.D).astype(np.int64)
    k, pmf = interval_pmf(p_click, p.l)
    e_d = misalignment((p.linewidth, p.linewidth), 0.0, p.rep_rate, k, pmf)
    for i in (LNU, LMU):
        for j in (LNU, LMU):
            e = x_error_model(eta_arm * I[i], eta_arm * I[j], e_d, p_dark)
            c.x_errors[i, j] = int(round(e * c.x_sifted[i, j]))
    c.interval_sum = float(np.sum(pmf * k)) * c.key_pairs
    c.linewidth = (p.linewidth, p.linewidth)
    return c


def arm_transmittance(setup, tag: str, p: MPQKDParams | None = None) -> float:
    """Transmittance from ``tag``'s spool input to a click, read off the lab's own
    components (fiber loss at the laser's wavelength, splices, connectors, coupler
    excess loss, detector).
    """
    from phoq.units import CONNECTOR_LOSS_DB, db_to_t

    spool, det, bs = setup[f"{tag}-SPL"], setup["DET-L"], setup["CHARLIE"]
    wl = setup[f"{tag}-LD"].wavelength
    loss_db = spool.loss_db(wl) + 4 * CONNECTOR_LOSS_DB + bs.excess_loss_db
    return db_to_t(loss_db) * det.efficiency
