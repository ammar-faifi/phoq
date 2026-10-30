"""Photodiode noise against the textbook receiver model: D. J. Blumenthal, ECE228B
Lecture 2 "Photodetection and Photodetectors" (shot noise 2q(I_p + I_d)Δf filling the
detector bandwidth, the shot-noise-limited SNR, heterodyne detection, responsivity and
band gaps), and I. S. Kolev, T. V. Karadzhov and E. N. Petkova, "Noises in
Photodetectors", ICEST 2005 (thermal noise 4kTΔf/R).
"""

import math

import numpy as np
import pytest
from scipy.signal import welch

from phoq import Coupler, Laser, Photodiode, Setup
from phoq.engine import synthesize, trace
from phoq.units import C0, H, K_B, Q_E, RESPONSIVITY

WL = 1550e-9
HV = H * C0 / WL
N, DT = 2**21, 10e-12  # 21 µs at 100 GS/s: the detector's 1 GHz is oversampled 50 times


def noise_bandwidth(pd: Photodiode, dt: float = DT) -> float:
    """B_N of the photodiode's discrete pole: ≈ (π/2)·f₃dB."""
    a = -math.expm1(-dt * 2 * math.pi * pd.bandwidth)
    return a / (2 - a) / (2 * dt)


def quiet(**kw) -> Photodiode:
    # thermal noise negligible
    return Photodiode(bandwidth=1e9, temperature=1.0, load=1e7, **kw)


def test_shot_noise_fills_the_bandwidth_with_density_2qI():
    """Lecture 2, slides 6–7: σ² = 2q(I_p + I_d)Δf, a flat 2qI over the detector
    bandwidth B. phoq used to spread the right variance flat up to the sampling Nyquist
    frequency, 15 dB too thin in band at 50× oversampling; the noise now goes through
    the detector's pole with the signal.
    """
    pd = quiet()
    i = pd.detect({WL: np.full(N, math.sqrt(1e-3))}, DT, np.random.default_rng(1), N)
    I = i.mean()
    assert i.var() == pytest.approx(2 * Q_E * I * noise_bandwidth(pd), rel=0.02)
    assert noise_bandwidth(pd) == pytest.approx(math.pi / 2 * pd.bandwidth, rel=0.01)
    f, S = welch(i - I, fs=1 / DT, nperseg=2**13)
    band = (f > 1e7) & (f < 1e8)
    assert np.mean(S[band]) == pytest.approx(2 * Q_E * I, rel=0.05)


def test_shot_noise_limited_snr_is_eta_p_over_2_h_nu_b():
    """Lecture 2, slide 10: SNR_dd = ηP / (2hνB), η = R·hν/q."""
    pd, P = quiet(), 1e-6
    i = pd.detect({WL: np.full(N, math.sqrt(P))}, DT, np.random.default_rng(2), N)
    eta = pd.responsivity(WL) * HV / Q_E
    assert i.mean() ** 2 / i.var() == pytest.approx(
        eta * P / (2 * HV * noise_bandwidth(pd)), rel=0.02
    )


def test_dark_and_thermal_noise_floor():
    """Kolev et al., Eqs. (20)–(21) and (29): 2qI_dΔf + 4kTΔf/R in the dark
    (50 Ω at 300 K)."""
    pd = Photodiode(bandwidth=1e9)
    i = pd.detect({WL: np.zeros(N)}, DT, np.random.default_rng(3), N)
    want = (
        2 * Q_E * pd.dark_current + 4 * K_B * pd.temperature / pd.load
    ) * noise_bandwidth(pd)
    assert i.var() == pytest.approx(want, rel=0.02)


def test_heterodyne_beat_and_its_shot_noise_limited_snr():
    """Lecture 2, slides 11–12: i = R[P_lo + P_s + 2√(P_lo P_s) cos(ω_IF t + φ)], and
    with a strong local oscillator SNR_het = ηP_s/(hνB), P_s the signal power reaching
    the detector.
    """
    s = Setup("heterodyne")
    lo = s.add(Laser("LO", mode="cw", power_dbm=0.0, linewidth=0.0))
    sig = s.add(Laser("SIG", mode="cw", power_dbm=-60.0, linewidth=0.0, detuning=100e6))
    cp = s.add(Coupler("CPL", ratio=0.99, excess_loss_db=0.0))
    pd = s.add(Photodiode("PD", bandwidth=1e9, temperature=1.0, load=1e7))
    s.connect(lo.out, cp.a1)
    s.connect(sig.out, cp.a2)
    s.connect(cp.b1, pd.inp)
    s.terminate(cp.b2)
    net, rng = trace(s), np.random.default_rng(4)
    p_lo, p_s = (
        sum(abs(p.amp) ** 2 for p in net.paths[pd] if p.source is x) * x.power
        for x in (lo, sig)
    )
    t = np.arange(N) * DT
    tone = np.vstack(
        [np.ones(N), np.cos(2 * math.pi * 100e6 * t), np.sin(2 * math.pi * 100e6 * t)]
    ).T
    fields = synthesize(net, [pd], t, DT, rng)[pd]
    r = pd.responsivity(WL)
    c, *_ = np.linalg.lstsq(tone, r * np.abs(sum(fields.values())) ** 2, rcond=None)
    assert c[0] == pytest.approx(r * (p_lo + p_s), rel=1e-6)
    assert math.hypot(c[1], c[2]) == pytest.approx(
        2 * r * math.sqrt(p_lo * p_s), rel=1e-4
    )
    snr = []
    for _ in range(4):
        i = pd.detect(fields, DT, rng, N)
        c, *_ = np.linalg.lstsq(tone, i, rcond=None)
        snr.append((c[1] ** 2 + c[2] ** 2) / 2 / (i - tone @ c).var())
    a = -math.expm1(-DT * 2 * math.pi * pd.bandwidth)
    # the pole at the IF
    h2 = abs(a / (1 - (1 - a) * np.exp(-2j * math.pi * 100e6 * DT))) ** 2
    eta = r * HV / Q_E
    assert np.mean(snr) == pytest.approx(
        eta * p_s / (HV * noise_bandwidth(pd)) * h2, rel=0.05
    )


@pytest.mark.parametrize(
    "material, gap_nm", [("Si", 1110), ("Ge", 1850), ("GaAs", 870), ("InGaAs", 1700)]
)
def test_responsivity_is_below_one_electron_per_photon_and_ends_at_the_band_gap(
    material, gap_nm
):
    """Lecture 2, slides 17–18: R = ηq/hν with 0 ≤ η ≤ 1, and no response beyond λ =
    1240 nm/E_g (Si 1110, Ge 1850, GaAs 870 nm; InGaAs detectors are specified to 1700
    nm). The tables' last non-zero point may sit a little beyond the nominal gap: the
    datasheet curves have an absorption tail (GaAs: 0.10 A/W at 890 nm on the DX25C
    plot)."""
    lams, rs = RESPONSIVITY[material]
    assert all(r * H * C0 / (lam * 1e-9) / Q_E <= 1.0 for lam, r in zip(lams, rs))
    lit = [lam for lam, r in zip(lams, rs) if r > 0]
    assert max(lit) <= gap_nm + 30
