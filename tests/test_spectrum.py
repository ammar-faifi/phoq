"""RF spectrum analyzer: levels against closed-form values (tones, noise floor, shot noise),
anti-aliasing, detector and trace modes, the lab API, and the delayed self-homodyne linewidth.
"""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

from phoq import (
    FunctionGenerator,
    Laser,
    Photodiode,
    Runner,
    Setup,
    SpectrumAnalyzer,
    SpectrumResult,
)
from phoq.engine import trace
from phoq.units import Q_E

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def sweep(esa, setup, seed=1):
    return esa.acquire(trace(setup), np.random.default_rng(seed))


def tone(freq=1.234e6, vpp=1.0, **esa):
    s = Setup("tone")
    fg = s.add(FunctionGenerator("FG", ch1_shape="sine", ch1_freq=freq, ch1_vpp=vpp))
    a = s.add(
        SpectrumAnalyzer("ESA", **{"stop_freq": 5e6, "rbw": 10e3, "averages": 4, **esa})
    )
    s.connect(fg.ch1, a.inp)
    return s, a


@pytest.mark.parametrize("rbw", [3e3, 10e3, 30e3])
def test_a_tone_reads_its_power_whatever_the_rbw(rbw):
    s, a = tone(rbw=rbw)
    r = sweep(a, s)
    expected = 10 * math.log10((0.5**2 / 2 / 50) / 1e-3)  # 1 Vpp into 50 Ω: +3.98 dBm
    f0, level = r.peak
    assert f0 == pytest.approx(1.234e6, abs=r.freq[1] - r.freq[0])
    assert level == pytest.approx(expected, abs=0.15)
    assert 10 * math.log10(r.power() / 1e-3) == pytest.approx(expected, abs=0.05)
    assert r.rbw == pytest.approx(rbw, rel=1e-3)


def test_the_empty_input_shows_the_noise_floor_in_the_rbw():
    s = Setup("floor")
    a = s.add(
        SpectrumAnalyzer(
            "ESA",
            stop_freq=5e6,
            rbw=10e3,
            averages=200,
            detector="average",
            noise_floor=-145.0,
        )
    )
    r = sweep(a, s)
    assert float(np.mean(r.level)) == pytest.approx(
        -145.0 + 10 * math.log10(r.rbw), abs=0.2
    )
    assert r.source is None
    peak = sweep(
        SpectrumAnalyzer("P", stop_freq=5e6, rbw=10e3, averages=20, detector="peak"), s
    )
    avg = sweep(
        SpectrumAnalyzer("A", stop_freq=5e6, rbw=10e3, averages=20, detector="average"),
        s,
    )
    assert np.mean(peak.level) > np.mean(
        avg.level
    )  # the peak detector rides on the noise


def test_shot_noise_of_a_photodiode():
    s = Setup("shot")
    ld = s.add(Laser("LD", mode="cw", power_dbm=0.0, linewidth=0.0))
    pd = s.add(Photodiode("PD", bandwidth=20e9, temperature=1.0))
    a = s.add(
        SpectrumAnalyzer(
            "ESA",
            stop_freq=50e6,
            rbw=100e3,
            averages=200,
            detector="average",
            noise_floor=-200,
        )
    )
    s.connect(ld.out, pd.inp)
    s.connect(pd.rf, a.inp)
    current = pd.mean_current(trace(s).mean_powers(pd))
    r = sweep(a, s)
    mid = (r.freq > 5e6) & (r.freq < 45e6)
    expected = 10 * math.log10(
        2 * Q_E * current * 50 * r.rbw / 1e-3
    )  # 2qI into 50 Ω, in the RBW
    assert float(np.mean(r.level[mid])) == pytest.approx(expected, abs=0.3)
    assert r.level[0] < float(np.mean(r.level[mid])) + 3  # AC-coupled: no DC line


def test_a_tone_above_the_span_does_not_fold_into_it():
    s, a = tone(
        freq=9e6, vpp=1.0, stop_freq=5e6
    )  # sampled at 12.8 MS/s it would alias to 3.8 MHz
    r = sweep(a, s)
    near = np.abs(r.freq - 3.8e6) < 50e3
    assert float(np.max(r.level[near])) < -60


def test_trace_average_max_hold_and_clearing():
    s = Setup("trace")
    a = s.add(
        SpectrumAnalyzer(
            "ESA",
            stop_freq=1e6,
            rbw=10e3,
            averages=2,
            trace="average",
            detector="average",
        )
    )
    net, rng = trace(s), np.random.default_rng(2)
    spread = [float(np.std(a.acquire(net, rng).level)) for _ in range(20)]
    assert (
        a._sweeps == 20 and spread[-1] < spread[0] / 2
    )  # the averaged trace smooths out
    a.rbw = 20e3  # any setting change restarts it
    assert a.acquire(net, rng).sweeps == 1
    a.trace = "max hold"
    first = a.acquire(net, rng).level.copy()
    assert np.all(a.acquire(net, rng).level >= first)


def test_the_lab_streams_and_clears_spectra():
    from fastapi.testclient import TestClient
    from phoq.server import create_app

    s, a = tone()
    a.trace = "average"
    app = create_app(s, web_dir=None)
    with TestClient(app) as c:
        types = {t["type"] for t in c.get("/api/catalog").json()["types"]}
        assert "SpectrumAnalyzer" in types
        frames = [
            m["frame"] for m in app.state.session.tick(False) if m["type"] == "frame"
        ]
        spec = next(f for f in frames if f.get("kind") == "spectrum")
        assert (
            spec["instrument"] == "ESA"
            and spec["n"] == a.points
            and spec["meas"]["peak_dbm"] > 3
        )
        assert (
            c.post("/api/instruments/ESA/clear").json() == {"ok": True}
            and a._sweeps == 0
        )
        assert c.post("/api/instruments/FG/clear").status_code == 422


def test_delayed_self_homodyne_measures_the_linewidth():
    ex = run_path(str(EXAMPLES / "linewidth_homodyne.py"))
    r, fit, w3, w20 = ex["measure"](ex["build"](linewidth=100e3, averages=1000))
    assert fit == pytest.approx(100e3, rel=0.03)  # the fit uses the whole trace
    # single crossings on a noise-like trace scatter more: the flat top hurts the −3 dB point most
    assert w3 == pytest.approx(100e3, rel=0.12) and w20 == pytest.approx(
        100e3, rel=0.06
    )
    # the beat's absolute level: 4 R² P1 P2 / (π Δν) · R_in · RBW at f ≪ Δν
    setup = ex["build"](linewidth=100e3)
    pd = setup["PD-1"]
    net = trace(setup)
    p1, p2 = (
        abs(p.amp) ** 2 * setup["LD-1"].power
        for p in net.paths[pd]
        if not p.reflections
    )
    peak = 4 * pd.responsivity(1550e-9) ** 2 * p1 * p2 / (math.pi * 100e3) * 50 * r.rbw
    assert r.peak[1] == pytest.approx(10 * math.log10(peak / 1e-3), abs=0.5)
    # a delay shorter than the coherence time overestimates: the sinc² of the delay takes over
    short = ex["measure"](ex["build"](linewidth=100e3, spool_m=300.0, averages=300))[2]
    assert short > 2 * 100e3


# ---- checked against J.-Q. Chen et al., Sensors 24, 3656 (2024) --------------------------------
def test_sub_coherence_delay_matches_the_review_eq_28_to_31():
    """0.3 km at 100 kHz (τd ≈ 0.46 τc): averaged over the bias phase, phoq's homodyne spectrum is
    the Lorentzian times 1 − e^{−2πΔντd}[cos 2πfτd + (Δν/f) sin 2πfτd], in absolute level.
    """
    from phoq import Attenuator, Coupler, FiberSpool, PhaseShifter

    dnu, n = 100e3, 4
    psd = None
    for k in range(n):  # θ = kπ/4 cancels the cos 2θ and sin 2θ terms
        s = Setup("dsh", seed=7)
        ld = s.add(Laser("LD", mode="cw", power_dbm=0.0, linewidth=dnu))
        bs1, bs2 = s.add(Coupler("BS-1"), Coupler("BS-2"))
        spl = s.add(FiberSpool("SPL", length=300.0))
        voa = s.add(Attenuator("VOA", attenuation_db=0.0))
        ps = s.add(PhaseShifter("PS", phase=k * math.pi / n))
        pd = s.add(Photodiode("PD"))
        a = s.add(
            SpectrumAnalyzer(
                "ESA", stop_freq=2e6, rbw=5e3, averages=200, detector="average"
            )
        )
        s.connect(ld.out, bs1.a1)
        s.connect(bs1.b1, spl.p1)
        s.connect(spl.p2, bs2.a1)
        s.connect(bs1.b2, voa.p1)
        s.connect(voa.p2, ps.p1)
        s.connect(ps.p2, bs2.a2)
        s.connect(bs2.b1, pd.inp)
        s.connect(pd.rf, a.inp)
        f, S = sweep(a, s, seed=k).psd()
        psd = S if psd is None else psd + S
    psd /= n
    net = trace(s)
    p1, p2 = (abs(p.amp) ** 2 * ld.power for p in net.paths[pd] if not p.reflections)
    tau = abs(net.delays(pd)[1] - net.delays(pd)[0])
    x = 2 * math.pi * dnu * tau
    for fq in (
        20e3,
        100e3,
        340e3,
        680e3,
        1e6,
        1.5e6,
    ):  # flat top, fringe slope, near the 1/τd null
        k = int(np.argmin(abs(f - fq)))
        fk = f[k - 3 : k + 4]
        lorentz = (
            4
            * pd.responsivity(1550e-9) ** 2
            * p1
            * p2
            / math.pi
            * dnu
            / (dnu**2 + fk**2)
            * 50
        )
        s2 = 1 - math.exp(-x) * (
            np.cos(2 * math.pi * fk * tau) + dnu * np.sin(2 * math.pi * fk * tau) / fk
        )
        measured, theory = psd[k - 3 : k + 4].mean(), (lorentz * s2).mean()
        assert 10 * math.log10(measured / theory) == pytest.approx(0.0, abs=0.4), fq


@pytest.mark.parametrize("nu_t, nu_r", [(100e3, 100e3), (100e3, 1e3)])
def test_two_laser_heterodyne_width_is_the_sum_of_the_linewidths(nu_t, nu_r):
    """The review's Eq. (4) and Fig. 2: a beat of full width ν_t + ν_r at the frequency offset."""
    from phoq import Coupler

    s = Setup("het", seed=3)
    a = s.add(Laser("LD-T", mode="cw", power_dbm=-3.0, linewidth=nu_t))
    b = s.add(Laser("LD-R", mode="cw", power_dbm=-3.0, linewidth=nu_r, detuning=1.5e6))
    bs, pd = s.add(Coupler("BS"), Photodiode("PD"))
    esa = s.add(
        SpectrumAnalyzer(
            "ESA", stop_freq=3e6, rbw=5e3, averages=500, detector="average"
        )
    )
    s.connect(a.out, bs.a1)
    s.connect(b.out, bs.a2)
    s.connect(bs.b1, pd.inp)
    s.connect(pd.rf, esa.inp)
    fit = sweep(esa, s, seed=5).fit_lorentzian(center=None)
    assert fit["center"] == pytest.approx(1.5e6, abs=10e3)
    assert 2 * fit["hwhm"] == pytest.approx(nu_t + nu_r, rel=0.05)


# ---- narrow lines: the short-delay fit (examples/linewidth_narrow.py) ----------------------------
def narrow():
    return run_path(str(EXAMPLES / "linewidth_narrow.py"))


@pytest.mark.parametrize("spool_km", [100, 500])
def test_short_delay_fit_measures_a_100_hz_laser(spool_km):
    ex = narrow()
    m = ex["measure"](ex["build"](100.0, spool_km, averages=300))
    assert m["tau_d"] * math.pi * 100 < 1  # shorter than the coherence time
    assert m["fit"] == pytest.approx(100.0, rel=0.04)
    assert not m["lorentzian"] == pytest.approx(
        100.0, rel=0.2
    )  # the long-delay fit does not apply
    assert not m["one_phase"] == pytest.approx(
        100.0, rel=0.2
    )  # one bias phase is biased


def test_long_delay_fits_agree_for_1_khz():
    ex = narrow()
    m = ex["measure"](ex["build"](1e3, 400, averages=500))
    assert m["fringe_depth"] < 1e-4
    assert m["fit"] == pytest.approx(1e3, rel=0.04) and m[
        "lorentzian"
    ] == pytest.approx(m["fit"], rel=0.01)


def test_spectrum_average_and_its_guards():
    s, a = tone(rbw=10e3)
    net, rng = trace(s), np.random.default_rng(3)
    r1, r2 = a.acquire(net, rng), a.acquire(net, rng)
    avg = SpectrumResult.average([r1, r2])
    assert avg.averages == r1.averages + r2.averages and avg.sweeps == 2
    assert np.allclose(avg.bin_power, (r1.bin_power + r2.bin_power) / 2)
    assert avg.peak[1] == pytest.approx(r1.peak[1], abs=0.1)
    a.rbw = 20e3
    with pytest.raises(ValueError, match="different analyzer settings"):
        SpectrumResult.average([r1, a.acquire(net, rng)])
    with pytest.raises(ValueError):
        SpectrumResult.average([])
    with pytest.raises(ValueError, match="positive"):
        r1.fit_self_homodyne(0.0)


def test_average_spectrum_restores_the_stepped_parameter():
    ex = narrow()
    setup = ex["build"](100.0, 500, averages=20)
    ps = setup["PS-1"]
    ps.phase = 0.3
    r = Runner(setup, seed=1).average_spectrum("ESA-1", ps, "phase", [0.0, math.pi / 2])
    assert ps.phase == 0.3 and r.sweeps == 2 and r.averages == 40
