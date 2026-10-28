"""Laser intensity noise (Laser.rin_db_hz) and shot noise, against the definitions in
the RP Photonics encyclopedia ("Intensity noise", "Relative intensity noise", "Shot
noise", "Balanced photodetection"): a photodiode reads S_I = 2qI + 4kT/R + RIN·I²; a
balanced detector's difference current keeps 2q(I₊ + I₋) and only the CMRR residue of
the RIN; the noise is one realization per laser, shared by all its paths; and the bench
of examples/laser_noise.py measures q and the RIN."""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

from phoq import Coupler, Laser, Photodiode, Setup, SpectrumAnalyzer
from phoq.engine import report, synthesize, trace
from phoq.units import K_B, Q_E

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "laser_noise.py"))


@pytest.mark.parametrize("rin", [-200.0, -140.0])
def test_photodiode_reads_shot_thermal_and_intensity_noise(rin):
    s = Setup("direct")
    laser = s.add(Laser("LD", mode="cw", power_dbm=3.0, rin_db_hz=rin))
    pd = s.add(Photodiode("PD", bandwidth=1e9, load=50.0, saturation=0.05))
    esa = s.add(
        SpectrumAnalyzer(
            "ESA",
            stop_freq=20e6,
            rbw=30e3,
            averages=40,
            detector="average",
            noise_floor=-200.0,
        )
    )
    s.connect(laser.out, pd.inp)
    s.connect(pd.rf, esa.inp)
    current = report(s)["detectors"]["PD"]["current"]
    f, psd = esa.acquire(trace(s), np.random.default_rng(1)).psd()
    band = (f > 2e6) & (f < 18e6)
    s_i = (
        psd[band].mean() / esa.input_impedance
    )  # the photocurrent flows into the analyzer's 50 Ω
    want = (
        2 * Q_E * current
        + 4 * K_B * pd.temperature / pd.load
        + 10 ** (rin / 10) * current**2
    )
    assert 10 * math.log10(s_i / want) == pytest.approx(0.0, abs=0.1)


def test_without_intensity_noise_nothing_is_drawn():
    """The default draws no random numbers, so every seeded result from before stays the
    same."""
    laser = Laser("LD")
    assert (
        laser.intensity_factor(np.arange(10) * 1e-9, 1e-9, np.random.default_rng(0))
        is None
    )
    a, b = np.random.default_rng(5), np.random.default_rng(5)
    laser.intensity_factor(np.arange(10) * 1e-9, 1e-9, a)
    assert a.random() == b.random()


def test_intensity_noise_per_bin_and_shared_by_every_path():
    """δ averaged over a bin has variance S/(2Δt), and two paths that leave in the same
    bins carry the same δ: the ratio of the powers at a coupler's two outputs does not
    fluctuate.
    """
    s = Setup("shared")
    laser = s.add(
        Laser("LD", mode="cw", power_dbm=0.0, rin_db_hz=-120.0, linewidth=0.0)
    )
    bs = s.add(Coupler("BS"))
    pd1, pd2 = s.add(Photodiode("PD1"), Photodiode("PD2"))
    s.connect(laser.out, bs.a1)
    s.connect(bs.b1, pd1.inp, length=1.0)
    s.connect(bs.b2, pd2.inp, length=1.0)
    s.terminate(bs.a2)
    dt, n = 1e-9, 200_000
    fields = synthesize(
        trace(s), [pd1, pd2], np.arange(n) * dt, dt, np.random.default_rng(2)
    )
    p1, p2 = (sum(np.abs(e) ** 2 for e in fields[d].values()) for d in (pd1, pd2))
    rel = p1 / p1.mean() - 1
    assert rel.var() == pytest.approx(10 ** (-120 / 10) / (2 * dt), rel=0.02)
    assert np.std(p1 / p2) < 1e-9


def test_balanced_difference_keeps_the_shot_noise_and_cancels_the_rin():
    """Both inputs: noise above dark = 2q(I₊ + I₋) at every power, and q from the slope
    to 2 %. One input: RIN·I² above the crossover 2q/RIN, read back to 0.3 dB."""
    dark = EX["noise_density"](EX["build"](laser_on=False), sweeps=8)
    bpd = EX["build"]()["BPD-1"]
    assert dark == pytest.approx(
        (bpd.setting()[1] * bpd.peak_responsivity()) ** 2, rel=0.05
    )
    both = EX["sweep"](2, EX["BOTH_INPUTS"], dark)
    for r in both:
        assert 10 * math.log10(r["above_dark"] / (2 * Q_E * r["i"])) == pytest.approx(
            0.0, abs=0.5
        )
    assert EX["fit"](both)[0] / 2 == pytest.approx(Q_E, rel=0.02)
    one = EX["sweep"](1, EX["ONE_INPUT"], dark)
    a, b = EX["fit"](one)
    assert 10 * math.log10(b) == pytest.approx(EX["RIN"], abs=0.3)
    assert a / 2 == pytest.approx(Q_E, rel=0.08)
    top = max(both, key=lambda r: r["i"])
    single = 2 * Q_E * top["i"] + 10 ** (EX["RIN"] / 10) * top["i"] ** 2
    assert (
        10 * math.log10(single / top["above_dark"]) > 15
    )  # what one detector would see of this light


def test_laser_reports_its_rin_against_the_shot_noise_level():
    laser = Laser("LD", power_dbm=0.0, rin_db_hz=-150.0)
    shot = 10 * math.log10(
        2 * 6.62607015e-34 * 299792458 / laser.wavelength / 1e-3
    )  # 2hν/P
    assert laser.info()["Excess RIN"] == (
        f"-150 dBc/Hz; shot-noise RIN at this power: {shot:.1f} dBc/Hz"
    )


@pytest.mark.parametrize(
    "power_dbm, rp", [(0.0, 10 * math.log10(3.73e-16)), (20.0, -174.0)]
)
def test_shot_noise_rin_matches_rp_photonics(power_dbm, rp):
    """RP Photonics, "Relative intensity noise": a 1 mW beam at 1064 nm has a
    shot-noise RIN 2hν/P of 3.73·10⁻¹⁶ Hz⁻¹ (−154 dBc/Hz), and the 100 mW Nd:YAG of
    its Fig. 1 reaches −174 dBc/Hz."""
    laser = Laser("LD", wavelength=1064e-9, power_dbm=power_dbm, rin_db_hz=-150.0)
    shot = float(laser.info()["Excess RIN"].split("at this power: ")[1].split(" ")[0])
    assert shot == pytest.approx(rp, abs=0.5 if power_dbm else 0.05)


def test_an_open_connector_is_told_the_remedy_that_fits_it():
    """Unplug INPUT− (section 6 of the tutorial): the cord's loose end reflects onto the
    detector. That end is a plug, as a terminator is, so it goes back in, or into a
    mating sleeve with a terminator; a part's open port, the coupler's a2, takes the
    terminator itself."""
    s = EX["build"](10.0)
    s.disconnect("FIB-4.b")
    s.remove(s["BS-1"].ports["a2"].peer.component)
    strays = {
        w["text"].split()[2]: w["text"]
        for w in report(s)["warnings"]
        if "reflects light on to BPD-1" in w["text"]
    }
    assert strays["FIB-4.b"].endswith(
        "Plug the cord back in, or end it with a mating sleeve and a terminator of"
        " its own connector type."
    )
    assert strays["BS-1.a2"].endswith(
        "Cap the port with a terminator of its own connector type."
    )
