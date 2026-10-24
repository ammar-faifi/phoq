"""IQ modulator against M. Seimetz, "High-Order Modulation for Optical Fiber Transmission" (Springer
2009), ch. 2: the field transfer function at the operating point (Eqs. 2.15–2.18), the arcsin drive
of the conventional IQ transmitter (Eqs. 2.46–2.47), and the MZM it is built from (Eqs. 2.13–2.14);
then the finite extinction, the engine's steady state against synthesized fields, the Exail
MXIQ-LN presets and absolute maximum ratings, and the bench of examples/iq_modulator.py.
"""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

from phoq import (
    DCPowerSupply,
    FunctionGenerator,
    IntensityModulator,
    IQModulator,
    Laser,
    PowerMeter,
    Setup,
)
from phoq.engine import report, synthesize, trace

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "iq_modulator.py"))


def ideal(**kw) -> IQModulator:
    """MXIQ-LN-30 electrodes with MZMs of 80 dB extinction: within 10⁻⁴ of a perfect modulator."""
    return IQModulator(
        v_pi=5.0, v_pi_bias=6.0, v_pi_phase=9.5, extinction_db=80.0, **kw
    )


def test_operating_point_gives_seimetz_eq_2_16_to_2_18():
    """MZMs at the null, arms in quadrature: E_out/E_in = ½cos(Δφ_I/2) + j½cos(Δφ_Q/2), with
    Δφ = u·π/Vπ and u the RF drive on top of the −Vπ bias (Eq. 2.15)."""
    iqm = ideal()
    b = iqm.operating_bias()
    rf_i, rf_q = np.meshgrid(np.linspace(-5, 5, 21), np.linspace(-5, 5, 21))
    got = iqm.field(rf_i, rf_q, b["bias_i"], b["bias_q"], b["bias_p"])
    u_i, u_q = rf_i - iqm.v_pi, rf_q - iqm.v_pi
    want = 0.5 * np.cos(u_i * np.pi / iqm.v_pi / 2) + 0.5j * np.cos(
        u_q * np.pi / iqm.v_pi / 2
    )
    assert np.abs(got - want).max() < 2e-4
    amplitude = 0.5 * np.sqrt(
        np.cos(u_i * np.pi / (2 * iqm.v_pi)) ** 2
        + np.cos(u_q * np.pi / (2 * iqm.v_pi)) ** 2
    )
    assert np.abs(np.abs(got) - amplitude).max() < 2e-4  # Eq. (2.17)
    big = amplitude > 0.05
    phase = np.arctan2(
        np.cos(u_q * np.pi / (2 * iqm.v_pi)), np.cos(u_i * np.pi / (2 * iqm.v_pi))
    )
    assert (
        np.abs(np.angle(got[big] * np.exp(-1j * phase[big]))).max() < 5e-3
    )  # Eq. (2.18)


@pytest.mark.parametrize(
    "levels",
    [[-1, -1 / 3, 1 / 3, 1], [-1, -5 / 7, -3 / 7, -1 / 7, 1 / 7, 3 / 7, 5 / 7, 1]],
)
def test_arcsin_drive_places_square_qam_exactly(levels):
    """Eqs. (2.46)–(2.47), u = (2Vπ/π)·arcsin(i): 16-QAM and 64-QAM land on ½(i + jq)."""
    iqm = ideal()
    b = iqm.operating_bias()
    sym = np.add.outer(np.array(levels), 1j * np.array(levels)).ravel()
    vi, vq = iqm.voltage_for(sym)
    assert (
        np.abs(iqm.field(vi, vq, b["bias_i"], b["bias_q"], b["bias_p"]) - sym / 2).max()
        < 2e-4
    )
    assert np.abs(vi).max() == pytest.approx(
        iqm.v_pi
    )  # 2Vπ peak to peak, as Seimetz drives it


@pytest.mark.parametrize("er", [20.0, 30.0])
def test_each_arm_is_the_intensity_modulator(er):
    """With the arms in phase and driven alike, the output is one MZM: power
    ½[1 + k·cos(πV/Vπ)] (Eq. 2.14 with the finite extinction of IntensityModulator)."""
    iqm, im = IQModulator(v_pi=5.0, extinction_db=er), IntensityModulator(
        v_pi=5.0, extinction_db=er
    )
    v = np.linspace(-10, 10, 41)
    assert np.abs(np.abs(iqm.field(v, v)) ** 2 - im.transmission(v)).max() < 1e-12


@pytest.mark.parametrize("er", [20.0, 30.0, 40.0])
def test_carrier_left_at_the_operating_point_is_set_by_the_extinction(er):
    iqm = IQModulator(v_pi=5.0, v_pi_bias=6.0, v_pi_phase=9.5, extinction_db=er)
    b = iqm.operating_bias()
    carrier = abs(iqm.field(0.0, 0.0, b["bias_i"], b["bias_q"], b["bias_p"])) ** 2
    assert carrier == pytest.approx(
        (1 - iqm.contrast) / 4, rel=1e-9
    )  # 1/(2(ER+1)): 33 dB below full at 30 dB


def outer_fringe_bench():
    s = Setup("fringe")
    laser, iqm, pm = s.add(
        Laser("LD", mode="cw", power_dbm=0.0),
        IQModulator("IQM", preset="exail/mxiq-ln-30"),
        PowerMeter("PM"),
    )
    psu = s.add(DCPowerSupply("PSU"))
    s.connect(laser.out, iqm.p1)
    s.connect(iqm.p2, pm.inp)
    s.connect(psu.ch1, iqm.bias_p)
    return s


def test_dc3_alone_sweeps_the_outer_interferometer():
    """Both MZMs at full transmission (0 V) carry the same real field, so the outer arms interfere
    fully: P ∝ cos²(φ_P/2), φ_P = π·V_DC3/Vπ_DC3. Static, so the report takes the modulator's exact
    complex factor."""
    s = outer_fringe_bench()
    iqm, psu = s["IQM"], s["PSU"]
    full = report(s)["detectors"]["PM"]["power"]
    for v in (2.0, 4.75, 9.5, 14.0):
        psu.ch1_voltage = v
        want = full * math.cos(math.pi * v / iqm.v_pi_p / 2) ** 2
        assert report(s)["detectors"]["PM"]["power"] == pytest.approx(
            want, rel=1e-9, abs=1e-15
        )
    psu.ch1_voltage = 4.75
    assert "I/Q phase +90.0°" in iqm.info()["Bias point"]


def test_steady_state_matches_synthesized_16qam():
    """Driven by 16-QAM patterns, the report's mean power (mean_factor over the symbols) equals the
    time average of the synthesized field, ¼·E[i² + q²] = 5/18 of the input at the operating point.
    """
    s = outer_fringe_bench()
    iqm = s["IQM"]
    psu2, fg = s.add(DCPowerSupply("PSU-2")), s.add(FunctionGenerator("FG"))
    b = iqm.operating_bias()
    s["PSU"].ch1_voltage = b["bias_p"]
    s.connect(psu2.ch1, iqm.bias_i)
    s.connect(psu2.ch2, iqm.bias_q)
    psu2.set(ch1_voltage=b["bias_i"], ch2_voltage=b["bias_q"])
    sym = EX["qam16"](512)
    vi, vq = iqm.voltage_for(sym)
    s.connect(fg.ch1, iqm.rf_i)
    s.connect(fg.ch2, iqm.rf_q)
    fg.set_pattern("ch1", vi, rate=25e6)
    fg.set_pattern("ch2", vq, rate=25e6)
    net = trace(s)
    mean = report(s, net)["detectors"]["PM"]["power"]
    t = (np.arange(512 * 4) + 0.5) / 25e6 / 4
    field = synthesize(net, [s["PM"]], t, 1e-12, np.random.default_rng(1))[s["PM"]]
    synth = float(np.mean(sum(np.abs(f) ** 2 for f in field.values())))
    assert mean == pytest.approx(synth, rel=0.01)
    assert iqm.mean_factor() == pytest.approx(np.mean(np.abs(sym) ** 2) / 4, rel=0.01)


def test_one_voltage_does_not_describe_an_iq_modulator():
    iqm = IQModulator("IQ")
    with pytest.raises(TypeError, match="five electrodes"):
        iqm.static_voltage()
    assert iqm.static_factor() == pytest.approx(iqm.field(0, 0))


@pytest.mark.parametrize(
    "preset, v_pi", [("exail/mxiq-ln-10", 3.8), ("exail/mxiq-ln-30", 5.0)]
)
def test_exail_presets_hold_the_datasheet_values(preset, v_pi):
    iqm = IQModulator(preset=preset)
    assert (iqm.v_pi, iqm.v_pi_dc, iqm.v_pi_p, iqm.insertion_loss_db) == (
        v_pi,
        6.0,
        9.5,
        5.0,
    )
    assert iqm.operating_bias() == {"bias_i": -6.0, "bias_q": -6.0, "bias_p": 4.75}
    assert iqm.connector == "FC/APC"
    assert (
        iqm.limits["bias_voltage_v"] == [-20.0, 20.0]
        and iqm.limits["rf_input_dbm"] == 28.0
    )


def test_ratings_are_checked_on_every_electrode():
    s = EX["build"]()
    assert report(s)["warnings"] == []
    s["PSU-2"].ch1_voltage = 25.0
    s["FG-1"].ch2_vpp = 20.0
    texts = [w["text"] for w in report(s)["warnings"]]
    assert any(
        t.startswith("IQM-1 bias_p is 25 V, outside the absolute maximum -20 … 20 V")
        for t in texts
    )
    assert any("IQM-1 RF drive on rf_q peaks at 10 V" in t for t in texts)
    assert not any("rf_i" in t or "bias_i" in t for t in texts)


@pytest.mark.parametrize("title, kw", EX["CASES"])
def test_the_receiver_reads_what_the_transfer_function_predicts(title, kw):
    """Through the 90° hybrid, the balanced detectors, the scope and the DSP: the same fit as on
    IQM.field() of each symbol, to within the receiver's noise."""
    setup = EX["build"](**kw)
    r, m = EX["measure"](setup), EX["model"](setup)
    assert r["angle"] == pytest.approx(m["angle"], abs=0.5)
    assert r["gain"] == pytest.approx(m["gain"], abs=0.02)
    assert r["carrier_db"] == pytest.approx(m["carrier_db"], abs=1.5)
    assert r["outer_inner"] == pytest.approx(m["outer_inner"], abs=0.06)
    assert r["evm"] == pytest.approx(math.hypot(m["evm"], r["evm_noise"]), rel=0.08)


def test_the_cases_show_their_signatures():
    """Each bias error moves its own number: DC3 the I/Q angle, DC1 the carrier, a linear drive
    the ratio of the outer to the inner levels (sin 90°/sin 30° = 2 instead of 3)."""
    m = {title: EX["model"](EX["build"](**kw)) for title, kw in EX["CASES"]}
    assert m["operating point"]["angle"] == pytest.approx(90.0, abs=0.05)
    assert m["DC3 10° off quadrature"]["angle"] == pytest.approx(100.0, abs=0.05)
    assert (
        m["DC1 0.5 V off the null"]["carrier_db"]
        > m["operating point"]["carrier_db"] + 10
    )
    assert m["linear drive, no arcsin"]["outer_inner"] == pytest.approx(2.0, abs=0.01)
    assert m["operating point"]["outer_inner"] == pytest.approx(3.0, abs=0.01)
