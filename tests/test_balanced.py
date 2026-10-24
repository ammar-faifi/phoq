"""Balanced photodetector against the Thorlabs PDB440x/PDB450x operation manual (21709-d02, version
2.4): RF OUTPUT = (ℜP₁ − ℜP₂)·G (§3.3), the CW saturation powers, monitor conversion gain and monitor
saturation (§3, Technical Data), CMRR measured the way §3.6 measures it, and the path-length limit of
§3.7. Then the amplifier's NEP and the two diodes' shot noise, the report's warnings, the 50 Ω load,
and the bench of examples/balanced_detection.py."""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

from phoq import (
    BalancedPhotodetector,
    Coupler,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    Oscilloscope,
    Setup,
    SpectrumAnalyzer,
)
from phoq.engine import report, trace
from phoq.units import Q_E

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "balanced_detection.py"))


def bench(*, preset="thorlabs/pdb450c", wavelength=1550e-9, ratio=0.5, cords=(1.0, 1.0), minus=True,
          freq=None, cmrr=None, gain=None, power_dbm=5.0, analyzer=False) -> Setup:
    """Laser → IM (a sine at ``freq`` on the quadrature point, or none) → coupler → INPUT+ and,
    when ``minus``, INPUT−; RF OUTPUT to a scope (or a spectrum analyzer), MONITOR+ to the scope."""
    s = Setup("balanced")
    laser = s.add(Laser("LD", mode="cw", wavelength=wavelength, power_dbm=power_dbm))
    im, fg = s.add(IntensityModulator("IM"), FunctionGenerator("FG", trigger="free"))
    bs = s.add(Coupler("BS", ratio=ratio))
    bpd = s.add(BalancedPhotodetector("BPD", preset=preset))
    if gain is not None:
        bpd.gain = gain
    if cmrr is not None:
        bpd.cmrr_db = cmrr
    s.connect(laser.out, im.p1)
    s.connect(im.p2, bs.a1)
    s.connect(fg.ch1, im.rf)
    s.connect(bs.b1, bpd.inp_plus, length=cords[0])
    if minus:
        s.connect(bs.b2, bpd.inp_minus, length=cords[1])
    else:
        s.terminate(bs.b2)  # the blocked arm, capped
    s.terminate(bs.a2)  # capped: an open flat port would send LO light back in
    q = float(im.voltage_for(0.5))
    if freq:
        fg.set(ch1_shape="sine", ch1_freq=freq, ch1_vpp=2.0, ch1_offset=q)  # T swings ½(1 ± 0.7)
    else:
        fg.set(ch1_shape="dc", ch1_offset=q)
    if analyzer:
        esa = s.add(SpectrumAnalyzer("ESA", stop_freq=5e6, rbw=10e3, averages=4))
        s.connect(bpd.rf, esa.inp)
    else:
        window = 10 / freq if freq else 10e-6  # ten periods of the modulation
        osc = s.add(Oscilloscope("OSC", timebase=window / 10, bandwidth=200e6, trigger="free"))
        s.connect(bpd.rf, osc.ch1)
        s.connect(bpd.mon_plus, osc.ch2)
    return s


def scope(s: Setup, seed: int = 1):
    return s["OSC"].acquire(trace(s), np.random.default_rng(seed))


def tone(v: np.ndarray, t: np.ndarray, freq: float) -> float:
    """Amplitude of the component at ``freq``, as a network analyzer's receiver measures selectively."""
    a = np.column_stack([np.cos(2 * np.pi * freq * t), np.sin(2 * np.pi * freq * t), np.ones_like(t)])
    c = np.linalg.lstsq(a, v, rcond=None)[0]
    return float(np.hypot(c[0], c[1]))


@pytest.mark.parametrize("preset, wavelength", [
    ("thorlabs/pdb450c", 1550e-9), ("thorlabs/pdb450a", 820e-9), ("thorlabs/pdb440c", 1310e-9),
])
def test_rf_output_is_the_photocurrent_difference_times_the_gain(preset, wavelength):
    """§3.3: U_RF = (ℜ(λ)·P₁ − ℜ(λ)·P₂)·G; a 70:30 split leaves 40 % of the light unbalanced.
    The monitors read each diode alone, and the time-domain output averages to the same."""
    s = bench(preset=preset, wavelength=wavelength, ratio=0.7, power_dbm=-12.0)
    bpd, net = s["BPD"], trace(s)
    p_plus, p_minus = (sum(net.mean_powers(bpd, port).values()) for port in ("inp_plus", "inp_minus"))
    r_plus, r_minus = bpd.responsivities(wavelength)
    entry = report(s, net)["detectors"]["BPD"]
    assert p_plus / p_minus == pytest.approx(7 / 3, rel=1e-9)
    assert entry["rf"] == pytest.approx(bpd.gain * (r_plus * p_plus - r_minus * p_minus), rel=1e-12)
    assert entry["monitors"] == pytest.approx((bpd.monitor_gain * r_plus * p_plus, bpd.monitor_gain * r_minus * p_minus))
    res = scope(s)
    assert res["ch1"].data.mean() == pytest.approx(entry["rf"], rel=0.01)
    assert res["ch2"].data.mean() == pytest.approx(entry["monitors"][0], rel=0.01)


def test_single_detector_mode_signs():
    """§3.2: in single detector mode RF OUTPUT is positive from INPUT+ and negative from INPUT−."""
    s = bench(power_dbm=-10.0, minus=False)
    assert report(s)["detectors"]["BPD"]["rf"] > 0
    assert report(s)["detectors"]["BPD"]["status"] == "Single input"
    s.disconnect(s["BPD"].inp_plus)
    s.disconnect(s["BS"].b2)  # the cap off, INPUT− plugged in
    s.connect(s["BS"].b2, s["BPD"].inp_minus)
    assert report(s)["detectors"]["BPD"]["rf"] < 0


@pytest.mark.parametrize("preset, wavelength, manual", [
    ("thorlabs/pdb440c", 1550e-9, 70e-6),  # Technical Data: "70 µW @ 1550 nm"
    ("thorlabs/pdb440a", 820e-9, 130e-6),  # "130 µW @ 820 nm"
    ("thorlabs/pdb450c", 1550e-9, 4.5e-3),  # "4.5 mW @ 1550 nm", at the lowest gain
    ("thorlabs/pdb450a", 820e-9, 9e-3),  # "9 mW @ 820 nm"
])
def test_cw_saturation_monitor_gain_and_monitor_saturation_match_the_manual(preset, wavelength, manual):
    bpd = BalancedPhotodetector(preset=preset)
    bpd.gain = min(bpd.settings())
    assert bpd.cw_saturation_power(wavelength) == pytest.approx(manual, rel=0.04)  # the manual rounds
    # "conversion gain 10 V/mW" at peak responsivity; "Saturation of the MONITOR outputs will occur
    # at optical input power greater than 1 mW"
    assert bpd.monitor_gain * bpd.peak_responsivity() == pytest.approx(10e3, rel=1e-3)
    assert bpd.monitor_swing / (bpd.monitor_gain * bpd.peak_responsivity()) == pytest.approx(1e-3, rel=1e-3)


@pytest.mark.parametrize("cmrr", [25.0, 30.0, 40.0])
def test_equal_inputs_leave_the_common_mode_residue(cmrr):
    s = bench(cmrr=cmrr, power_dbm=-6.0)
    bpd = s["BPD"]
    entry = report(s)["detectors"]["BPD"]
    one = bpd.gain * bpd.responsivity(1550e-9) * sum(trace(s).mean_powers(bpd, "inp_plus").values())
    assert entry["rf"] / one == pytest.approx(10 ** (-cmrr / 20), rel=1e-9)
    assert entry["status"] == "Balanced"


def rejection(freq: float, cords=(1.0, 1.0), cmrr=None, seed=1) -> float:
    """§3.6: the RF OUTPUT's response at ``freq`` from one input, over its response with both
    inputs lit by the same modulated light (a common-mode signal) [dB]."""
    single, both = (bench(freq=freq, cords=cords, cmrr=cmrr, minus=m, gain=1e3) for m in (False, True))
    a = [tone((r := scope(x, seed))["ch1"].data, r.t, freq) for x in (single, both)]
    return 20 * math.log10(a[0] / a[1])


@pytest.mark.parametrize("freq", [1e6, 30e6])
def test_cmrr_measured_as_section_3_6_measures_it(freq):
    """Matched powers and paths: the RF OUTPUT's own frequency response cancels in the ratio."""
    assert rejection(freq) == pytest.approx(30.0, abs=0.1)


@pytest.mark.parametrize("freq", [1e6, 3e6, 10e6])
def test_path_length_difference_limits_the_rejection_as_section_3_7_shows(freq):
    """A path difference Δτ turns the cancellation into |1 − e^(−i2πfΔτ)|: a rejection of
    −20·log10(2·sin(πfΔτ)), the curves of the §3.7 figure (in air; in fiber ΔL divided by 1.5).
    1 m and 2 m cords, with an ideal detector so the paths alone limit it."""
    s = bench(cords=(1.0, 2.0))
    paths = trace(s).paths[s["BPD"]]
    plus, minus = (min(p.delay for p in paths if p.port == port) for port in ("inp_plus", "inp_minus"))
    dtau = minus - plus
    assert dtau == pytest.approx(1.468 / 299792458, rel=0.01)  # 1 m of fiber, 1.47 m of air
    want = -20 * math.log10(2 * math.sin(math.pi * freq * dtau))
    assert rejection(freq, cords=(1.0, 2.0), cmrr=120.0) == pytest.approx(want, abs=0.1)
    if freq == 1e6:  # the figure's 30 dB line passes 1 MHz at about 1.5 m of air
        assert want == pytest.approx(30.0, abs=0.5)


def noise_bandwidth(bw: float, dt: float) -> float:
    """Equivalent noise bandwidth of the detector's sampled one-pole filter, → (π/2)·bw as dt → 0."""
    alpha = -math.expm1(-dt * 2 * math.pi * bw)
    return alpha / (2 - alpha) / (2 * dt)


@pytest.mark.parametrize("watts", [0.0, 50e-6, 400e-6])
def test_rf_noise_is_the_nep_plus_both_diodes_shot_noise(watts):
    """Dark: the amplifier's NEP. Lit: each diode adds its own shot noise 2qi; they are independent,
    so they add in power, while the light's common mean cancels (the residue is the CMRR's)."""
    bpd = BalancedPhotodetector(preset="thorlabs/pdb450c", gain=1e4)
    bw, nep, _ = bpd.setting()
    dt, n = 1 / (40 * bw), 400_000
    field = np.full(n, math.sqrt(watts), dtype=complex)
    fields = {("inp_plus", 1550e-9): field, ("inp_minus", 1550e-9): field} if watts else {}
    out = bpd.detect(fields, dt, np.random.default_rng(3), n)
    i_plus, i_minus = bpd.responsivities(1550e-9)
    density = 2 * Q_E * (i_plus + i_minus) * watts + (nep * bpd.peak_responsivity()) ** 2
    rf = out["rf"][n // 20:]
    assert rf.std() == pytest.approx(bpd.gain * math.sqrt(density * noise_bandwidth(bw, dt)), rel=0.01)
    assert rf.mean() == pytest.approx(bpd.gain * (i_plus - i_minus) * watts, abs=3 * rf.std() / math.sqrt(n / 40))
    if not watts:  # the monitors' own noise, "< 180 µV RMS", over 2 ms of their 1 MHz bandwidth
        mon = bpd.detect({}, 5e-9, np.random.default_rng(4), n)["mon_plus"][n // 20:]
        assert mon.std() == pytest.approx(180e-6, rel=0.02)


def test_report_warns_of_saturation_damage_and_a_missing_gain():
    s = bench(power_dbm=0.0, minus=False, gain=1e4)  # ~0.25 mW on one input: 2.5 V of 4.6 V
    assert not [w for w in report(s)["warnings"] if "BPD" in w["text"]]
    s["BPD"].gain = 1e5
    w = [w["text"] for w in report(s)["warnings"]]
    assert any("RF OUTPUT would reach" in t and "46 µW" in t for t in w)
    s["BPD"].gain = 3e4
    assert any("has no" in t["text"] for t in report(s)["warnings"])
    assert report(s)["detectors"]["BPD"]["status"] == "No such gain"
    s["BPD"].gain = 1e3
    s["LD"].power_dbm = 15.0  # 2 mW on INPUT+: the monitor (1 mW), not yet the diode (20 mW)
    w = [w["text"] for w in report(s)["warnings"]]
    assert any("MONITOR output would reach" in t for t in w) and not any("absolute maximum" in t for t in w)
    s.disconnect(s["BS"].b2)  # the cap off, INPUT− plugged in
    s.connect(s["BS"].b2, s["BPD"].inp_minus)
    s["FG"].ch1_offset = float(s["IM"].voltage_for(1.0))  # the IM fully open
    s["BS"].ratio, s["LD"].power_dbm = 0.95, 20.0  # 30 mW on INPUT+, 1.6 mW on INPUT−
    w = [w for w in report(s)["warnings"] if "absolute maximum" in w["text"]]
    assert len(w) == 1 and w[0]["level"] == "bad" and "20 mW" in w[0]["text"]


def test_spectrum_analyzer_sees_half_the_voltage_on_its_50_ohms():
    """The manual's swings and conversion gains are for a high-impedance load, half into 50 Ω."""
    freq = 1e6
    s = bench(freq=freq, minus=False, power_dbm=0.0)
    amplitude = tone((r := scope(s))["ch1"].data, r.t, freq)
    esa = bench(freq=freq, minus=False, power_dbm=0.0, analyzer=True)["ESA"]
    level = esa.acquire(trace(esa.setup), np.random.default_rng(1)).peak[1]
    assert level == pytest.approx(10 * math.log10((amplitude / 2) ** 2 / 2 / 50 / 1e-3), abs=0.15)


def test_the_balanced_detection_bench():
    s = EX["build"]()
    assert report(s)["warnings"] == []
    assert EX["beat_amplitudes"](s, sweeps=4)[0] == pytest.approx(EX["expected_beat"](s), rel=0.01)
    mon = (EX["expected_beat"](s, "mon_plus") + EX["expected_beat"](s, "mon_minus")) / 2
    assert EX["beat_amplitudes"](s, sweeps=4)[1] == pytest.approx(mon, rel=0.01)
    noise = EX["lo_noise_through"](s, sweeps=4)
    assert noise["rejection"] == pytest.approx(30.0, abs=0.3)
    assert noise["together"] > 0.99
    loud = EX["build"](gain=1e5)  # one step up the beat swings past the rails
    rf = EX["sweep"](loud)[1][0]
    swing = loud["BPD-1"].setting()[2]
    assert report(loud)["detectors"]["BPD-1"]["rf"] + EX["expected_beat"](loud) > swing > rf.max()
