"""Tests for phoq: parameters, wiring, physics, persistence, the API and the MP-QKD
layer."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from phoq import (
    Attenuator,
    Coupler,
    DCPowerSupply,
    FiberSpool,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    OpticalSwitch,
    Oscilloscope,
    PhaseModulator,
    PhaseShifter,
    PhotonCounter,
    Photodiode,
    PowerMeter,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import synthesize, trace
from phoq.parties import load_parties, load_protocol
from phoq.units import CONNECTOR_LOSS_DB, FIBER_DELAY, db_to_t, photon_energy

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# ---- parameters & wiring -------------------------------------------------------------
def test_params_are_validated_attributes():
    laser = Laser(power_dbm="-3", mode="cw")
    assert laser.power_dbm == -3.0 and laser.mode == "cw"
    with pytest.raises(ValueError):
        laser.power_dbm = 99
    with pytest.raises(ValueError):
        laser.mode = "chirped"
    with pytest.raises(TypeError):
        Laser(colour="red")


def test_chain_inserts_cords_and_wires_electrical_ports():
    s = Setup.from_chain(Laser(), Photodiode(), Oscilloscope())
    pairs = {frozenset((a.path, b.path)) for a, b in s.connections()}
    assert pairs == {
        frozenset(p)
        for p in (
            ("LD-1.out", "FIB-1.a"),
            ("FIB-1.b", "PD-1.inp"),
            ("PD-1.rf", "OSC-1.ch1"),
        )
    }


def test_connect_rejects_bad_connections():
    s = Setup()
    laser, pd = s.add(Laser(), Photodiode())
    s.connect(laser.out, pd.inp)
    with pytest.raises(ConnectionError):
        s.connect(laser.out, pd.inp)  # both already used
    with pytest.raises(ConnectionError):
        s.connect(pd.rf, s.add(Photodiode()).inp)  # electrical ↔ optical


def test_change_notifications():
    s = Setup()
    laser = s.add(Laser())
    events = []
    s.on_change(lambda ev, comp, param: events.append((ev, comp.name, param)))
    laser.power_dbm = 1.0
    laser.power_dbm = 1.0  # unchanged: no event
    assert events == [("param", "LD-1", "power_dbm")]


# ---- physics -------------------------------------------------------------------------
def test_power_budget_through_attenuator():
    s = Setup.from_chain(
        Laser(mode="cw", power_dbm=0.0), Attenuator(attenuation_db=10), PowerMeter()
    )
    reading = s.run()["OPM-1"].power
    # VOA + 4 mated pairs + 2 m fiber
    expected_db = 10 + 0.6 + 4 * CONNECTOR_LOSS_DB + 2 * 0.19e-3
    assert 10 * math.log10(1e-3 / reading) == pytest.approx(expected_db, abs=0.01)


def test_spool_delay_shows_on_scope():
    r = Setup.load(EXAMPLES / "delay_line.py").run()["OSC-1"]
    # first rising edge
    rise = lambda ch: r.t[np.flatnonzero(r[ch].data > 0.5 * r[ch].data.max())[0]]
    assert rise("ch2") - rise("ch1") == pytest.approx(1e3 * FIBER_DELAY, rel=0.01)


def _mzi(linewidth=0.0, long_arm=None):
    s = Setup(seed=0)
    laser = s.add(Laser(mode="cw", power_dbm=0, linewidth=linewidth))
    b1, b2 = s.add(Coupler("BS-1"), Coupler("BS-2"))
    ps = s.add(PhaseShifter(insertion_loss_db=0))
    m1, m2 = s.add(PowerMeter("M1"), PowerMeter("M2"))
    s.connect(laser.out, b1.a1)
    s.terminate(b1.a2)
    s.connect(b1.b1, ps.p1)
    s.connect(ps.p2, b2.a1)
    if long_arm:
        spool = s.add(FiberSpool(length=long_arm, splice_loss_db=0))
        s.connect(b1.b2, spool.p1)
        s.connect(spool.p2, b2.a2)
    else:
        s.connect(b1.b2, b2.a2)
    s.connect(b2.b1, m1.inp)
    s.connect(b2.b2, m2.inp)
    return s, ps


def test_mzi_fringes_conserve_energy():
    s, ps = _mzi()
    readings = []
    for phi in np.linspace(0, 2 * np.pi, 9):
        ps.phase = phi
        r = s.run()
        readings.append((r["M1"].power, r["M2"].power))
    p1, p2 = np.array(readings).T
    assert np.ptp(p1 + p2) / np.mean(p1 + p2) < 1e-9
    assert (p1.max() - p1.min()) / (p1.max() + p1.min()) > 0.97  # visibility


def test_long_imbalance_destroys_interference():
    s, ps = _mzi(linewidth=100e3, long_arm=10e3)
    p = []
    for phi in (0.0, np.pi / 2, np.pi):
        ps.phase = phi
        p.append(s.run()["M1"].power)
    assert np.ptp(p) / np.mean(p) < 1e-6


def test_scope_synthesis_matches_steady_state():
    s, ps = _mzi(linewidth=1e3)
    for name, meter in (("M1", s["M1"]), ("M2", s["M2"])):
        pd = s.add(Photodiode(f"PD-{name}"))
        port = meter.inp.peer
        s.disconnect(port)
        s.connect(port, pd.inp)
        s.remove(meter)
    osc = s.add(Oscilloscope(timebase=100e-9))
    s.connect(s["PD-M1"].rf, osc.ch1)
    s.connect(s["PD-M2"].rf, osc.ch2)
    ps.phase = 1.0
    r = s.run()
    for ch, pd in (("ch1", "PD-M1"), ("ch2", "PD-M2")):
        assert r["OSC-1"][ch].meas["mean"] == pytest.approx(
            r.report["detectors"][pd]["current"], rel=0.02
        )


def test_single_photon_detector_click_probability():
    s = Setup.from_chain(
        Laser(
            power_dbm=-78, rep_rate=100e6, pulse_width=100e-12, phase_randomized=True
        ),
        SinglePhotonDetector(efficiency=0.2, dark_count_rate=0, dead_time=0),
        PhotonCounter(slots=400_000),
        seed=5,
    )
    mu = s["LD-1"].mean_photon_number * db_to_t(2 * CONNECTOR_LOSS_DB)
    p = s.run()["CNT-1"].counts["ch1"] / 400_000
    assert p == pytest.approx(1 - math.exp(-0.2 * mu), rel=0.02)


def test_modulator_pattern_aligned_with_counter_bins():
    """Every other pulse is blocked; after a 1 km spool the counter still sees only even
    bins."""
    s = Setup(seed=2)
    laser = s.add(Laser(power_dbm=-60, rep_rate=50e6, pulse_width=200e-12))
    im = s.add(IntensityModulator(extinction_db=80, insertion_loss_db=0))
    spool = s.add(FiberSpool(length=1e3))
    spd = s.add(SinglePhotonDetector(efficiency=1.0, dark_count_rate=0, dead_time=0))
    cnt = s.add(PhotonCounter(slots=2000))
    s.chain(laser, im, spool, spd, cnt)
    fg = s.add(FunctionGenerator())
    s.connect(fg.ch1, im.rf)
    fg.set_pattern(
        "ch1",
        im.voltage_for(np.where(np.arange(2000) % 2 == 0, 1.0, 0.0)),
        rate=laser.rep_rate,
    )
    clicks = s.run()["CNT-1"].clicks["ch1"]
    assert clicks[0::2].all() and not clicks[1::2].any()


# ---- function generator & modulators -------------------------------------------------
def test_generator_shapes_and_pattern():
    fg = FunctionGenerator(
        ch1_shape="sine",
        ch1_freq=1e6,
        ch1_vpp=3.0,
        ch1_offset=0.5,
        ch2_shape="square",
        ch2_duty=0.25,
        ch2_vpp=2.0,
    )
    t = (np.arange(2000) + 0.5) * 0.5e-9  # one period, samples off the duty-cycle edges
    sine = fg.voltage("ch1", t)
    assert sine.mean() == pytest.approx(0.5, abs=1e-6) and np.ptp(
        sine
    ) == pytest.approx(3.0, rel=1e-3)
    square = fg.voltage("ch2", t)
    assert square.mean() == pytest.approx(2.0 / 2 * (2 * 0.25 - 1), abs=1e-3)
    volts = np.array([0.0, 1.5, 4.0, 0.25])
    fg.set_pattern("ch1", volts, rate=10e6)
    assert fg.ch1_shape == "pattern" and fg.ch1_vpp == 4.0 and fg.ch1_offset == 2.0
    # symbol k centered on k/f
    assert fg.voltage("ch1", np.arange(8) / 10e6) == pytest.approx(np.tile(volts, 2))
    fg.ch1_enabled = False
    assert not fg.voltage("ch1", t).any() and fg.is_static("ch1")
    with pytest.raises(ValueError):
        fg.voltage("ch3", t)


def test_generator_describes_pattern_levels():
    """A pattern reads as the levels it takes, so one with no swing shows where it sits
    rather than '0.00 Vpp' (an eavesdropper's switch held in cross: 2 V on its control
    input)."""
    s = Setup()
    fg = s.add(
        FunctionGenerator(
            ch1_shape="sine", ch1_freq=1e6, ch1_vpp=3.0, ch2_shape="pattern"
        )
    )
    sw = s.add(OpticalSwitch())
    s.connect(fg.ch2, sw.ctrl)
    assert fg.describe("ch1") == "sine 1 MHz · 3.00 Vpp"
    assert fg.describe("ch2") == "pattern 10 MHz · 2.00 Vpp · NO PATTERN LOADED"
    fg.set_pattern("ch1", [0.0, 1.5, 4.0, 0.25], rate=10e6)
    assert fg.describe("ch1") == "pattern 10 MHz · +0.00 … +4.00 V · 4 symbols"
    # the levels follow the amplitude and offset, as on the instrument
    fg.ch1_offset = -1.0
    assert fg.describe("ch1") == "pattern 10 MHz · -3.00 … +1.00 V · 4 symbols"
    fg.set_pattern("ch2", sw.voltage_for(np.ones(6, dtype=bool)), rate=434e6)
    assert (
        fg.ch2_vpp == 0.0
        and fg.describe("ch2") == "pattern 434 MHz · constant +2.00 V · 6 symbols"
    )
    assert (
        sw.info()["Control"].endswith("constant +2.00 V · 6 symbols")
        and sw.info()["Cross fraction"] == "1"
    )
    fg.ch2_vpp = 1.0  # a flat pattern stays flat at any amplitude
    assert "constant +2.00 V" in fg.describe("ch2")
    fg.ch2_enabled = False
    assert fg.describe("ch2") == "off" and fg.describe("ch1").startswith("pattern")


def test_intensity_modulator_transfer_curve():
    im = IntensityModulator(extinction_db=20)  # ER = 100 → arm imbalance k = 99/101
    assert im.transmission(0.0) == pytest.approx(
        100 / 101
    ) and im.t_max == pytest.approx(100 / 101)
    assert im.transmission(im.v_pi / 2) == pytest.approx(0.5)
    assert im.transmission(im.v_pi) == pytest.approx(1 / 101) and im.t_min == im.floor
    # ER by definition
    assert im.transmission(0.0) / im.transmission(im.v_pi) == pytest.approx(100)
    v = np.linspace(-im.v_pi, 2 * im.v_pi, 601)  # smooth, no kink at the null
    assert np.abs(np.diff(im.transmission(v), 2)).max() < 1e-3
    for target in (0.99, 0.7, 0.3, 0.05):
        assert im.transmission(im.voltage_for(target)) == pytest.approx(target)
    im.phi0 = 0.4  # path imbalance shifts the curve
    assert im.transmission(-0.4 * im.v_pi / np.pi) == pytest.approx(im.t_max)
    assert im.transmission(im.voltage_for(0.3)) == pytest.approx(0.3)


def test_dc_supply_biases_modulators():
    s = Setup.from_chain(
        Laser(mode="cw", power_dbm=0.0, linewidth=0.0),
        IntensityModulator("IM", insertion_loss_db=0.0),
        PowerMeter("M"),
    )
    full = s.run()["M"].power
    im = s["IM"]
    psu = s.add(DCPowerSupply(ch1_voltage=im.v_pi / 2, ch2_voltage=1.0))
    s.connect(psu.ch1, im.bias)
    assert (
        im.is_static()
        and im.static_voltage() == pytest.approx(im.v_pi / 2)
        and im.bias_voltage() == pytest.approx(im.v_pi / 2)
    )
    # quadrature: T = ½ exactly
    assert s.run()["M"].power == pytest.approx(0.5 / im.t_max * full, rel=1e-9)
    # rf needed to cancel the bias
    assert im.voltage_for(1.0) == pytest.approx(-im.v_pi / 2)
    psu.ch1_enabled = False
    assert s.run()["M"].power == pytest.approx(full, rel=1e-9)  # output off → 0 V
    psu.ch1_enabled = True
    fg = s.add(
        FunctionGenerator(
            ch1_shape="square", ch1_freq=1e6, ch1_vpp=im.v_pi, ch1_duty=0.5
        )
    )
    s.connect(fg.ch1, im.rf)  # swing ±Vπ/2 about quadrature
    assert im.mean_factor() == pytest.approx(0.5, rel=1e-6)  # (T_max + T_min)/2
    pm = s.add(PhaseModulator("PM"))
    s.connect(psu.ch2, pm.bias)
    assert pm.static_voltage() == pytest.approx(1.0) and pm.info()["Bias"].startswith(
        "PSU-1 CH2 · dc +1.000 V"
    )
    osc = s.add(Oscilloscope(timebase=1e-6))
    with pytest.raises(ConnectionError):
        s.connect(psu.ch2, osc.ch1)  # ch2 already feeds PM.bias
    s.disconnect(pm.bias)
    s.connect(psu.ch2, osc.ch1)
    tr = s.run()["OSC-1"]["ch1"]
    assert (
        tr.unit == "V"
        and tr.meas["mean"] == pytest.approx(1.0)
        and tr.meas["pk_pk"] == pytest.approx(0.0)
    )


def test_modulator_has_no_drive_of_its_own():
    assert (
        not hasattr(IntensityModulator(), "set_pattern")
        and "drive" not in IntensityModulator.param_specs()
    )
    s = Setup.from_chain(
        Laser(mode="cw", power_dbm=0.0, linewidth=0.0),
        IntensityModulator(insertion_loss_db=0.0),
        PowerMeter(),
    )
    undriven = s.run()["OPM-1"].power
    expected_db = 4 * CONNECTOR_LOSS_DB + 2 * 0.19e-3
    # 0 V → full transmission
    assert 10 * math.log10(1e-3 / undriven) == pytest.approx(expected_db, abs=0.01)
    fg = s.add(FunctionGenerator(ch1_shape="dc", ch1_offset=s["IM-1"].v_pi))
    s.connect(fg.ch1, s["IM-1"].rf)
    # Vπ → extinction floor
    assert s.run()["OPM-1"].power == pytest.approx(undriven * db_to_t(30), rel=1e-6)


def test_optical_switch_routes_statically_and_pulse_by_pulse():
    s = Setup.from_chain(
        Laser(mode="cw", power_dbm=0.0, linewidth=0.0),
        OpticalSwitch("SW"),
        PowerMeter("M1"),
    )
    sw = s["SW"]
    s.connect(sw.b2, s.add(PowerMeter("M2")).inp)
    r = s.run()
    bar, leak = r["M1"].power, r["M2"].power
    assert sw.info()["State"] == "bar" and sw.is_static()
    assert 10 * math.log10(bar / leak) == pytest.approx(40.0, abs=0.01)  # crosstalk
    # insertion loss + 4 mated pairs + 2 m
    expected_db = 1.0 + 4 * CONNECTOR_LOSS_DB + 2 * 0.19e-3
    assert 10 * math.log10(1e-3 / (bar + leak)) == pytest.approx(expected_db, abs=0.01)
    psu = s.add(DCPowerSupply(ch1_voltage=2.0))
    s.connect(psu.ch1, sw.ctrl)
    r = s.run()
    assert sw.info()["State"] == "cross"
    assert r["M2"].power == pytest.approx(bar, rel=1e-9) and r[
        "M1"
    ].power == pytest.approx(leak, rel=1e-9)
    # a pattern on ctrl throws it pulse by pulse: every third pulse goes to the second
    # detector
    s = Setup(seed=1)
    laser = s.add(Laser(power_dbm=-60, rep_rate=50e6, pulse_width=200e-12))
    sw = s.add(OpticalSwitch("SW", crosstalk_db=80))
    d0, d1 = s.add(
        SinglePhotonDetector("D0", efficiency=1.0, dark_count_rate=0, dead_time=0),
        SinglePhotonDetector("D1", efficiency=1.0, dark_count_rate=0, dead_time=0),
    )
    cnt = s.add(PhotonCounter("CNT", slots=3000))
    s.connect(laser.out, sw.a1)
    s.connect(sw.b1, d0.inp)
    s.connect(sw.b2, d1.inp)
    s.connect(d0.out, cnt.ch1)
    s.connect(d1.out, cnt.ch2)
    fg = s.add(FunctionGenerator())
    s.connect(fg.ch1, sw.ctrl)
    cross = np.arange(3000) % 3 == 0
    fg.set_pattern("ch1", sw.voltage_for(cross), rate=laser.rep_rate)
    assert not sw.is_static() and sw.info()["Cross fraction"] == "0.333"
    c = s.run()["CNT"].clicks
    assert c["ch2"][cross].all() and not c["ch1"][cross].any()
    assert c["ch1"][~cross].all() and not c["ch2"][~cross].any()


def test_phase_modulator_driven_by_generator_shifts_fringe():
    s = Setup(seed=0)
    laser = s.add(Laser(mode="cw", power_dbm=0, linewidth=0.0))
    b1, b2 = s.add(Coupler("BS-1"), Coupler("BS-2"))
    pm = s.add(PhaseModulator(insertion_loss_db=0))
    m1 = s.add(PowerMeter("M1"))
    fg = s.add(FunctionGenerator(ch1_shape="dc", ch1_offset=0.0))
    s.connect(laser.out, b1.a1)
    s.connect(b1.b1, pm.p1)
    s.connect(pm.p2, b2.a1)
    s.connect(b1.b2, b2.a2)
    s.connect(b2.b1, m1.inp)
    s.terminate(b1.a2)
    s.terminate(b2.b2)
    s.connect(fg.ch1, pm.rf)
    phases = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    powers = []
    for phase in phases:
        fg.ch1_offset = float(pm.voltage_for(phase))
        powers.append(s.run()["M1"].power)
    p = np.array(powers)
    assert (p.max() - p.min()) / (p.max() + p.min()) > 0.97  # a full-visibility fringe
    # P(φ) + P(φ+π) is constant
    assert p[:8] + p[8:] == pytest.approx(2 * p.mean(), rel=1e-6)


def test_scope_can_view_generator_output_and_chain_wires_rf():
    s = Setup.from_chain(
        FunctionGenerator(ch2_shape="sine", ch2_freq=1e6, ch2_vpp=2.0), PhaseModulator()
    )
    assert s["PM-1"].driver() == (s["FG-1"], "ch1")
    osc = s.add(Oscilloscope(timebase=1e-6))
    s.connect(s["FG-1"].ch2, osc.ch1)
    tr = s.run()["OSC-1"]["ch1"]
    assert (
        tr.unit == "V"
        and tr.meas["pk_pk"] == pytest.approx(2.0, rel=0.02)
        and tr.meas["freq"] == pytest.approx(1e6, rel=0.02)
    )


def test_single_shot_waits_for_edge_trigger_and_holds():
    s = Setup.from_chain(
        Laser(power_dbm=-78, rep_rate=1e6, pulse_width=100e-12, phase_randomized=True),
        SinglePhotonDetector(efficiency=0.2, dark_count_rate=0, dead_time=0),
        Oscilloscope(
            timebase=2e-6, trigger="edge", trigger_source="ch1", trigger_level=0.5
        ),
        seed=9,
    )
    osc = s["OSC-1"]
    assert osc.mode == "run"
    runner = Runner(s, seed=9)
    res = runner.single(osc, attempts=500)
    assert (
        res is not None
        and res.triggered
        and res.trigger_source == "ch1"
        and res.trigger_level == 0.5
    )
    assert res["ch1"].data.max() >= 1.0  # a click sits in the window
    assert (
        osc.held is res
        and not osc.armed
        and not osc.running
        and osc.mode == "hold (single shot)"
    )
    osc.running = True  # RUN clears the held sweep
    assert osc.held is None and osc.mode == "run"
    osc.arm()
    assert osc.armed and not osc.running and osc.mode == "wait (single shot)"
    osc.disarm()  # SINGLE again cancels the wait
    assert not osc.armed and osc.mode == "stop"
    osc.arm()
    osc.running = True  # RUN while waiting cancels it too
    assert not osc.armed and osc.mode == "run"


def test_scope_bandwidth_sample_rate_and_memory():
    fg = FunctionGenerator(
        ch1_shape="square",
        ch1_freq=10e6,
        ch1_vpp=2.0,
        ch2_shape="sine",
        ch2_freq=10e6,
        ch2_vpp=2.0,
    )
    s = Setup(seed=1)
    osc = s.add(Oscilloscope(timebase=100e-9, trigger="free"))
    s.add(fg)
    s.connect(fg.ch1, osc.ch1)
    s.connect(fg.ch2, osc.ch2)
    # Record length and rate follow the timebase: 1 µs window at 5 GS/s = 5000 points,
    # 200 ps apart.
    fs, n, dt = osc.record()
    assert (fs, n) == (5e9, 5000) and dt == pytest.approx(200e-12)
    # Bandwidth: a 20 MHz front end rounds a 10 MHz square wave off; 4 GHz leaves it
    # alone.
    osc.bandwidth = 4e9
    square_full = s.run()["OSC-1"]["ch1"].data
    osc.bandwidth = 20e6
    square_slow = s.run()["OSC-1"]["ch1"].data
    assert np.ptp(square_full) == pytest.approx(2.0, rel=1e-3)
    # edges rounded off
    assert np.abs(np.diff(square_slow)).max() < 0.1 * np.abs(np.diff(square_full)).max()
    assert square_slow.std() < 0.9 * square_full.std()  # harmonics removed
    osc.bandwidth = 1e9
    # Record memory caps the rate at slow timebases: 10 ms window / 2000 points = 200
    # kS/s.
    osc.set(timebase=1e-3, max_points=2000)
    fs, n, dt = osc.record()
    assert (fs, n) == (200e3, 2000)
    # Undersampling aliases: a 10 MHz sine sampled at 11 MS/s shows up at 1 MHz.
    osc.set(timebase=1e-6, max_points=200_000, sample_rate=11e6)
    tr = s.run()["OSC-1"]["ch2"]
    assert tr.meas["freq"] == pytest.approx(1e6, rel=0.05) and tr.meas[
        "pk_pk"
    ] == pytest.approx(2.0, rel=0.1)
    osc.sample_rate = 5e9
    assert s.run()["OSC-1"]["ch2"].meas["freq"] == pytest.approx(10e6, rel=0.02)


# ---- time tagger batches & analysis tasks --------------------------------------------
def _click_setup(slots=20_000):
    return Setup.from_chain(
        Laser(
            power_dbm=-80, rep_rate=100e6, pulse_width=100e-12, phase_randomized=True
        ),
        SinglePhotonDetector(efficiency=0.2, dark_count_rate=0, dead_time=0),
        PhotonCounter(slots=slots),
        seed=21,
    )


def test_synthesize_offset_moves_one_detectors_bins():
    """A channel delay's grid offset gives the fields of a shifted time grid, same phase
    walk."""
    s = Setup.from_chain(
        Laser(rep_rate=100e6, pulse_width=1e-9, linewidth=10e6), Photodiode(), seed=1
    )
    net, d = trace(s), s["PD-1"]
    t, dt = np.arange(4000) * 10e-12, 10e-12
    a = synthesize(net, [d], t, dt, np.random.default_rng(3), {d: 2.5e-9})[d]
    b = synthesize(net, [d], t + 2.5e-9, dt, np.random.default_rng(3))[d]
    assert a.keys() == b.keys() and all(np.allclose(a[wl], b[wl]) for wl in a)


def test_counter_batches_are_continuous_in_time():
    s = _click_setup()
    runner = Runner(s, seed=1)
    a, b = runner.acquire("CNT-1"), runner.acquire("CNT-1")
    assert (a.start, b.start) == (0, 20_000) and b.t0 == pytest.approx(20_000 / 100e6)
    ta, tb = a.timestamps("ch1"), b.timestamps("ch1")
    assert ta.size > 100 and ta.max() < tb.min() and np.all(np.diff(tb) > 0)
    assert ta[0] == pytest.approx(np.flatnonzero(a.clicks["ch1"])[0] / a.rate)
    ev = b.events()
    assert (
        ev.dtype.names == ("t", "ch")
        and np.all(np.diff(ev["t"]) >= 0)
        and set(ev["ch"]) == {1}
    )
    assert (
        runner.acquire("CNT-1", net=None).start == 40_000
        and s["CNT-1"].acquire(trace(s), runner.rng, start=5).start == 5
    )
    assert a.to_json()["t0"] == 0.0 and "seq" in a.to_json()


def test_analysis_tasks_run_in_worker_processes(tmp_path):
    from phoq.analysis import Session, load_tasks

    script = tmp_path / "my_analysis.py"
    script.write_text(
        "from phoq.analysis import task\nseen = []\n@task('CNT-1')\ndef rates(batch):\n"
        "    seen.append(batch.seq)\n    print('batch', batch.seq, 'clicks',"
        " batch.counts['ch1'], 'seen', len(seen))\n    return {'rate':"
        " batch.rates['ch1'], 'seq': batch.seq}\n@task\ndef broken(batch):\n    raise"
        " RuntimeError('boom %d' % batch.seq)\n"
    )
    tasks = load_tasks(script)
    assert (
        [t.name for t in tasks] == ["rates", "broken"]
        and tasks[0].sources == ("CNT-1",)
        and tasks[1].accepts("anything")
    )
    workers = Session(_click_setup(5_000), tasks, seed=3).run(batches=3, echo=False)
    rates, broken = workers
    lines = list(rates.lines)
    assert any("batch 0 clicks" in l for l in lines) and any(
        "batch 2 clicks" in l and "seen 3" in l for l in lines
    )  # state persists
    assert (
        rates.batches == 3
        and rates.result["seq"] == 2
        and rates.result["rate"] > 0
        and rates.status == "stopped"
    )
    assert broken.batches == 3 and any(
        "RuntimeError: boom 1" in l for l in broken.lines
    )  # errors are shown, worker survives
    assert all(w.proc is None for w in workers)


def test_api_lists_and_controls_tasks(tmp_path):
    from fastapi.testclient import TestClient
    from phoq.analysis import load_tasks
    from phoq.server import create_app

    script = tmp_path / "t.py"
    script.write_text(
        "from phoq.analysis import task\n@task\ndef echo(batch):\n    print('got',"
        " batch.seq)\n"
    )
    with TestClient(
        create_app(_click_setup(2_000), web_dir=None, tasks=load_tasks(script))
    ) as client:
        listed = client.get("/api/tasks").json()["tasks"]
        assert [t["task"] for t in listed] == ["echo"] and listed[0]["file"] == str(
            script
        )
        assert client.post("/api/tasks/echo/stop").json()["status"] == "stopped"
        assert client.post("/api/tasks/nope/stop").status_code == 404
        assert (
            client.post(
                "/api/tasks", json={"path": str(tmp_path / "missing.py")}
            ).status_code
            == 404
        )
        with client.websocket_connect("/ws") as ws:
            consoles = [
                m["console"]
                for m in (json.loads(ws.receive_text()) for _ in range(6))
                if m["type"] == "console"
            ]
        assert (
            consoles
            and consoles[0]["task"] == "echo"
            and consoles[0]["full"]
            and consoles[0]["status"] == "stopped"
        )


# ---- parties: classical channel, hardware link, the MP-QKD dialogue ------------------
def test_classical_channel_between_party_processes(tmp_path):
    from phoq.parties import Experiment, load_parties

    script = tmp_path / "pingpong.py"
    script.write_text(
        "from phoq.parties import Party\nclass Ping(Party):\n    name = 'PING';"
        " instruments = ('CNT-1',)\n    def measure(self, batch):\n        n ="
        " self.net.broadcast('hello', block=batch.seq,"
        " clicks=int(batch.counts['ch1']))\n        print('sent', n, 'bytes for block',"
        " batch.seq)\n    def receive(self, msg):\n        print('reply from',"
        " msg.sender, msg.topic, msg.block, msg.echo)\n       "
        " self.report(replies=msg.block + 1)\nclass Pong(Party):\n    name = 'PONG'\n  "
        "  def setup(self):\n        try:\n            self.hw.get('CNT-1', 'slots')\n "
        "       except PermissionError as e:\n            print('hardware link"
        " refused:', e)\n    def receive(self, msg):\n        self.net.send(msg.sender,"
        " 'echo', block=msg.block, echo=msg.clicks * 2)\n"
    )
    ping, pong = load_parties(script)
    assert ping.name == "PING" and pong.source == str(script)
    exp = Experiment(_click_setup(5_000), [ping, pong], seed=4)
    workers = exp.run(blocks=3)
    w_ping, w_pong = workers
    assert (
        w_ping.port != w_pong.port
        and w_ping.net["sent"] == 3
        and w_pong.net["recv"] == 3
    )
    assert any(
        "reply from PONG echo 2" in l for l in w_ping.lines
    ) and w_ping.result == {"replies": 3}
    assert any(
        "hardware link refused" in l and "not part of this station" in l
        for l in w_pong.lines
    )
    assert exp.laptops() == [
        {"name": "PING", "kind": "party", "port": w_ping.port, "host": "127.0.0.1"},
        {"name": "PONG", "kind": "party", "port": w_pong.port, "host": "127.0.0.1"},
    ]


def test_mpqkd_parties_agree_on_a_key():
    from phoq.parties import Experiment

    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    # short link: a chunk fills fast
    setup = mpqkd.build(mpqkd.MPQKDParams(distance=10e3), rounds=200_000)
    workers = Experiment(setup, mpqkd.PARTIES, seed=8).run(blocks=6, echo=False)
    alice, charlie, bob = workers
    assert [w.name for w in workers] == [
        "ALICE",
        "CHARLIE",
        "BOB",
    ] and charlie.batches == 6
    for w in (alice, bob):
        assert w.result["blocks"] == 6 and w.result["sifted bits"] > 1000
        assert w.result["E_Z (sample)"] < 0.01 and 0.1 < w.result["E_X (μμ)"] < 0.4
        assert not any("Traceback" in l for l in w.lines)
    a, b = alice.result, bob.result
    # [#6] a chunk was reconciled, verified and amplified, identically on both sides
    assert a["corrected bits"] == b["corrected bits"] >= 1000
    assert (
        a["final key bits"] == b["final key bits"] > 0 and a["key rate [bit/pulse]"] > 0
    )
    assert a["final key sha256"] == b["final key sha256"]
    keys = {
        w.name: [l.split("key ")[-1] for l in w.lines if "final bits · key " in l]
        for w in (alice, bob)
    }
    # the consoles print the same key
    assert keys["ALICE"] and keys["ALICE"] == keys["BOB"]
    # leak accounting: every disclosed parity and tag bit is counted, the same on both
    # sides
    assert a["leak EC [bits]"] == b["leak EC [bits]"] > 0
    # chunking depends on message timing; a failed tag discards a chunk
    chunks = sum("bits reconciled" in l for l in bob.lines)
    assert (
        chunks >= 1
        and a["leak verify [bits]"] == b["leak verify [bits]"] == 32 * chunks
    )
    import re

    sized, leaked = (
        0,
        0,
    )  # every PA step: floor(n × secret fraction − leaked bits) = final bits
    for l in alice.lines:
        m = re.search(
            r"PA: ([\d,]+) corrected bits × secret fraction ([\d.]+) .* − (\d+) leaked"
            r" → (\d+) final",
            l,
        )
        if m:
            n, frac, leak, final = (
                int(m[1].replace(",", "")),
                float(m[2]),
                int(m[3]),
                int(m[4]),
            )
            assert abs(final - (n * frac - leak)) <= 1 + 0.0005 * n
            sized, leaked = sized + final, leaked + leak
    assert (
        sized == a["final key bits"]
        and leaked <= a["leak EC [bits]"] + a["leak verify [bits]"]
    )
    assert any("tag matches" in l for l in bob.lines)
    # clicks + 4 per block + ec/pa
    assert charlie.net["sent"] == 2 * 6 and alice.net["recv"] >= 6 + 4 * 6 + 3


def test_cascade_reconciles_and_both_sides_count_the_same_leak():
    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    from phoq.loading import import_source  # noqa: F401

    h = lambda x: -x * math.log2(x) - (1 - x) * math.log2(1 - x)
    rng = np.random.default_rng(5)
    for n, q in ((2000, 0.0), (2000, 0.02), (300, 0.08), (1, 0.0)):
        a = rng.integers(0, 2, n, dtype=np.uint8)
        b = a.copy()
        flips = rng.random(n) < q
        b[flips] ^= 1
        corrected, ref, cor = mpqkd.cascade_in_process(a, b, seed=3, qber=max(q, 1e-3))
        assert np.array_equal(corrected, a) and len(cor.corrected) == int(flips.sum())
        # Alice counts what she sent, Bob what he received
        assert ref.leak == cor.leak > 0
        if flips.any():
            # Cascade's usual efficiency
            assert 1.0 <= ref.leak / (n * h(flips.mean())) <= 2.5
            assert mpqkd.verification_tag(b, 9, 32) != mpqkd.verification_tag(a, 9, 32)
        assert mpqkd.verification_tag(corrected, 9, 32) == mpqkd.verification_tag(
            a, 9, 32
        )


def test_toeplitz_hash_is_the_matrix_product_and_linear():
    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    n, m, seed = 23, 7, 11
    r = np.random.default_rng(seed).integers(0, 2, n + m - 1, dtype=np.uint8)
    T = np.array([[r[i - j + n - 1] for j in range(n)] for i in range(m)])
    rng = np.random.default_rng(2)
    x, y = rng.integers(0, 2, n, dtype=np.uint8), rng.integers(0, 2, n, dtype=np.uint8)
    assert np.array_equal(mpqkd.toeplitz_hash(x, seed, m), (T @ x) % 2)
    assert np.array_equal(
        mpqkd.toeplitz_hash(x ^ y, seed, m),
        mpqkd.toeplitz_hash(x, seed, m) ^ mpqkd.toeplitz_hash(y, seed, m),
    )
    big = rng.integers(0, 2, 20_000, dtype=np.uint8)
    assert mpqkd.toeplitz_hash(big, seed, 5_000).size == 5_000  # FFT path stays exact
    assert np.array_equal(
        mpqkd.toeplitz_hash(big[:300], seed, 40),
        (
            np.array(
                [
                    [
                        np.random.default_rng(seed).integers(0, 2, 339, dtype=np.uint8)[
                            i - j + 299
                        ]
                        for j in range(300)
                    ]
                    for i in range(40)
                ]
            )
            @ big[:300]
        )
        % 2,
    )


def test_mpqkd_keyrate_estimate_reproduces_the_theory():
    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    p = mpqkd.MPQKDParams()
    setup = mpqkd.build(p)
    eta = mpqkd.arm_transmittance(setup, "ALICE", p)
    th = mpqkd.theory(p, eta)
    assert (
        0 < th.rate_bounds < th.rate and 0 < th.q11_lower < th.q11 < 1 and th.e11 < 0.01
    )
    assert 0.2 < th.e_x < 0.3  # weak coherent X pairs: 1/4 plus the phase walk
    # the stations' estimator on expectation values
    est = mpqkd.estimate(mpqkd.expected_counts(p, eta), p)
    assert est.q11_lower == pytest.approx(th.q11_lower, rel=1e-4)
    assert est.e11_model == pytest.approx(
        th.e11, rel=0.01
    ) and est.eta_fit == pytest.approx(eta, rel=1e-4)
    assert est.e_x_model == pytest.approx(th.e_x, rel=1e-3)
    # the X-type yield bound does not close at 2ν, 2μ (see keyrate.py)
    assert math.isnan(est.e11_decoy)

    assert (
        mpqkd.final_length(1000, est, 30, 32)
        == math.floor(1000 * est.secret_fraction) - 62
    )


def test_relay_stage_re_encodes_within_the_block(tmp_path):
    """A stage-1 party's measure() runs before the stage-0 counters are recorded, so
    what it programs from its clicks reaches the end station in the same block."""
    from phoq.parties import Experiment

    script = tmp_path / "relay.py"
    script.write_text(
        "from phoq.parties import Party\nclass Relay(Party):\n    name = 'RELAY'; stage"
        " = 1; instruments = ('CNT-R', 'VOA-1')\n    def measure(self, batch):\n       "
        " self.hw.set('VOA-1', attenuation_db=80.0 if batch.seq % 2 else 0.0)\n       "
        " print('block', batch.seq, 'clicks', batch.counts['ch1'])\nclass End(Party):\n"
        "    name = 'END'; instruments = ('CNT-E',)\n    def measure(self, batch):\n   "
        "     self.report(**{str(batch.seq): batch.counts['ch1']})\n        self.counts"
        " = getattr(self, 'counts', {}) | {batch.seq: batch.counts['ch1']}\n       "
        " self.report(**{str(k): v for k, v in self.counts.items()})\n"
    )
    s = Setup(seed=1)
    laser = s.add(
        Laser(power_dbm=-75, rep_rate=100e6, pulse_width=100e-12, phase_randomized=True)
    )
    bs, voa = s.add(Coupler(), Attenuator("VOA-1", attenuation_db=0.0))
    dr, de = s.add(
        SinglePhotonDetector("DR", efficiency=0.5, dark_count_rate=0, dead_time=0),
        SinglePhotonDetector("DE", efficiency=0.5, dark_count_rate=0, dead_time=0),
    )
    cr, ce = s.add(
        PhotonCounter("CNT-R", slots=20_000), PhotonCounter("CNT-E", slots=20_000)
    )
    s.connect(laser.out, bs.a1)
    s.connect(bs.b1, dr.inp)
    s.connect(bs.b2, voa.p1)
    s.connect(voa.p2, de.inp)
    s.connect(dr.out, cr.ch1)
    s.connect(de.out, ce.ch1)
    relay, end = load_parties(script)
    assert relay.stage == 1 and end.stage == 0
    w_relay, w_end = Experiment(s, [relay, end], seed=2).run(blocks=4, echo=False)
    assert w_relay.batches == 4 and w_end.batches == 4
    counts = {int(k): v for k, v in w_end.result.items()}
    assert counts[0] > 1000 and counts[2] > 1000 and counts[1] == 0 and counts[3] == 0


# ---- BB84 with decoy states: reference run, the two parties, and the eavesdropper ----
def test_bb84_hardware_is_calibrated():
    bb84 = load_protocol(EXAMPLES / "bb84")
    p = bb84.BB84Params(mu=0.4, phase_error=0.0)
    setup = bb84.build(p, pulses=10_000, eve=True)
    net = trace(setup)
    for tag, port in (("ALICE", "SPL-A.p1"), ("EVE-TX", "EVE-SW.a2")):
        laser = setup[f"{tag}-LD"]
        mu = net.port_power[port] / laser.rep_rate / photon_energy(laser.wavelength)
        assert mu == pytest.approx(0.4, rel=1e-6)
    # Eve's re-sent pulses land in the same time bins as Alice's: equal flight time to
    # DET-0
    d_alice = min(net.delays(setup["DET-0"], setup["ALICE-LD"]))
    d_eve = min(net.delays(setup["DET-0"], setup["EVE-TX-LD"]))
    assert d_eve == pytest.approx(d_alice, abs=1e-15)
    # every interferometer pair interferes constructively on DET-0 at zero phase
    sw, awg = setup["EVE-SW"], setup["EVE-RX-AWG"]
    for source, det in (
        ("ALICE-LD", "DET-0"),
        ("ALICE-LD", "EVE-DET-0"),
        ("EVE-TX-LD", "DET-0"),
    ):
        awg.ch2_offset = (
            0.0 if det == "DET-0" and source == "ALICE-LD" else 2 * sw.threshold
        )
        a, b = (
            bb84.common._static_amplitude(q)
            for q in bb84.common.middle_paths(
                trace(setup), setup[source], setup[det], p.slot
            )
        )
        assert abs(np.angle(a / b)) < 1e-9 and abs(a) == pytest.approx(abs(b), rel=0.1)
    awg.ch2_offset = 0.0
    assert "EVE-SW" not in bb84.build(p, pulses=10_000, eve=False)


def test_bb84_reference_run_has_positive_key_rate():
    bb84 = load_protocol(EXAMPLES / "bb84")
    res = bb84.run(pulses=300_000, seed=3)
    t, e = res.tally, res.estimate
    assert (
        t.pulses == 300_000
        and t.key_bits > 500
        and res.alice_key.size == res.bob_key.size
    )
    # the 0.2 rad phase error: ≈ 1 %
    assert res.qber < 0.04 and e["qber_x"][bb84.I_MU] < 0.04
    assert e["y0"] < 1e-4 and 0.01 < e["y1"] < 0.1 and e["e1"] < 0.08  # decoy bounds
    assert e["q1"] < e["q_mu"] and e["rate"] > 5e-4
    g = e["gains"]
    assert g[bb84.I_MU, 0] > g[bb84.I_NU, 0] > g[bb84.I_VAC, 0] == 0
    # with a perfectly stabilized interferometer only the lasers' phase diffusion over
    # one time slot and the arms' unequal loss remain: a few 1e-3
    perfect = bb84.run(bb84.BB84Params(phase_error=0.0), pulses=100_000, seed=3)
    assert perfect.qber < 0.01 and perfect.estimate["rate"] > 0
    assert "R " in res.summary() and "bit/pulse" in res.summary()


def test_bb84_parties_agree_on_a_key():
    from phoq.parties import Experiment

    bb84 = load_protocol(EXAMPLES / "bb84")
    setup = bb84.build(pulses=100_000, eve=False)
    alice, bob = Experiment(setup, bb84.PARTIES, seed=8).run(blocks=3, echo=False)
    assert [w.name for w in (alice, bob)] == ["ALICE", "BOB"] and bob.batches == 3
    for w in (alice, bob):
        assert not any("Traceback" in l for l in w.lines)
        assert w.result["pulses"] == 300_000 and w.result["key bits"] > 600
        assert w.result["QBER Z (μ, sampled)"] < 0.08 and w.result["QBER X (μ)"] < 0.05
        assert w.result["e1 ≤"] < 0.15 and w.result["key rate [bit/pulse]"] > 0
    assert alice.result == {
        k: v for k, v in bob.result.items() if k not in ("sent [kB]", "received [kB]")
    } | {
        "sent [kB]": alice.result["sent [kB]"],
        "received [kB]": alice.result["received [kB]"],
    }
    # detect + errors per block; sift
    assert bob.net["sent"] == 6 and alice.net["sent"] == 3


def test_bb84_intercept_resend_kills_the_key(monkeypatch):
    from phoq.parties import Experiment

    bb84 = load_protocol(EXAMPLES / "bb84")
    (eve,) = load_parties(EXAMPLES / "bb84" / "eve.py")
    assert eve.name == "EVE" and eve.stage == 1 and eve.fraction == 1.0
    # an Eve party is loaded: build() adds her station
    setup = bb84.build(pulses=100_000)
    assert "EVE-SW" in setup and "EVE-TAGGER" in setup
    alice, bob, w_eve = Experiment(setup, [*bb84.PARTIES, eve], seed=8).run(
        blocks=3, echo=False
    )
    assert w_eve.batches == 3 and w_eve.result["intercepted"] == 1.0
    assert not any("Traceback" in l for w in (alice, bob, w_eve) for l in w.lines)
    for w in (alice, bob):
        assert w.result["pulses"] == 300_000 and w.result["sifted"] > 300
        # intercept-resend: 25 % in the wrong basis
        assert 0.18 < w.result["QBER X (μ)"] < 0.38
        assert w.result["e1 ≤"] > 0.2 and w.result["key rate [bit/pulse]"] == 0.0
    assert alice.result["key bits"] == bob.result["key bits"]


# ---- persistence ---------------------------------------------------------------------
def test_save_load_and_python_export_roundtrip(tmp_path):
    s = Setup.load(EXAMPLES / "delay_line.py")
    s["SPL-1"].length = 2e3
    s.save(tmp_path / "setup.json")
    loaded = Setup.load(tmp_path / "setup.json")
    assert loaded.to_dict() == s.to_dict()

    namespace = {}
    exec(s.to_python(), namespace)
    assert namespace["setup"].to_dict() == s.to_dict()


def test_beamsplitter_is_the_old_name_of_coupler():
    """Scripts and setups saved before the rename still work: the old name hands over a
    Coupler."""
    with pytest.warns(DeprecationWarning, match="now Coupler"):
        from phoq import BeamSplitter
    assert BeamSplitter is Coupler and Coupler().prefix == "CPL"
    s = Setup("old")
    s.add(Laser("LD-1"), Coupler("BS-1"))
    s.connect("LD-1.out", "BS-1.a1")
    # an old save
    data = json.loads(json.dumps(s.to_dict()).replace('"Coupler"', '"BeamSplitter"'))
    loaded = Setup.from_dict(data)
    # saved anew as Coupler
    assert type(loaded["BS-1"]) is Coupler and loaded.to_dict() == s.to_dict()
    from phoq.core import Component

    assert (
        Component.lookup("BeamSplitter") is Coupler
        and "BeamSplitter" not in Component.registry
    )


# ---- API -----------------------------------------------------------------------------
def test_api_edits_the_python_objects():
    from fastapi.testclient import TestClient
    from phoq.server import create_app

    s = Setup.from_chain(Laser(), Photodiode(), Oscilloscope(), name="api test")
    with TestClient(create_app(s, web_dir=None)) as client:
        types = [t["type"] for t in client.get("/api/catalog").json()["types"]]
        assert (
            "Laser" in types
            and types.count("Coupler") == 1
            and "BeamSplitter" not in types
        )
        assert (
            client.post(
                "/api/components", json={"type": "BeamSplitter", "name": "BS-9"}
            ).status_code
            == 200
        )
        # an old client's type name still adds a coupler
        assert type(s["BS-9"]) is Coupler
        s.remove("BS-9")
        assert (
            client.patch(
                "/api/components/LD-1", json={"params": {"power_dbm": -1}}
            ).status_code
            == 200
        )
        assert s["LD-1"].power_dbm == -1
        assert (
            client.patch(
                "/api/components/LD-1", json={"params": {"power_dbm": 50}}
            ).status_code
            == 422
        )
        assert client.post("/api/components", json={"type": "Attenuator"}).json() == {
            "name": "VOA-1"
        }
        assert (
            client.post(
                "/api/connections", json={"a": "LD-1.out", "b": "VOA-1.p1"}
            ).status_code
            == 409
        )
        client.post("/api/disconnect", json={"port": "FIB-1.b"})
        assert (
            client.post(
                "/api/connections", json={"a": "FIB-1.b", "b": "VOA-1.p1"}
            ).status_code
            == 200
        )
        with client.websocket_connect("/ws") as ws:
            kinds = {json.loads(ws.receive_text())["type"] for _ in range(3)}
        assert {"state", "report", "frame"} <= kinds
        # no light left for a sync
        client.patch("/api/components/OSC-1", json={"params": {"trigger": "free"}})
        assert client.post("/api/instruments/OSC-1/single").json() == {"armed": True}
        assert s["OSC-1"].armed and not s["OSC-1"].running
        # pressed again: canceled
        assert client.post("/api/instruments/OSC-1/single").json() == {"armed": False}
        client.post("/api/instruments/OSC-1/single")
        # the capture is streamed once, then held
        with client.websocket_connect("/ws") as ws:
            frames = [
                m["frame"]
                for m in (json.loads(ws.receive_text()) for _ in range(12))
                if m["type"] == "frame"
            ]
        assert (
            len(frames) == 1
            and frames[0]["trigger"] == "free"
            and s["OSC-1"].held is not None
            and not s["OSC-1"].armed
        )


# ---- protocol ------------------------------------------------------------------------
def test_mpqkd_pairing_rate_matches_theory():
    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    res = mpqkd.run(rounds=300_000, l_values=(1000,), seed=3)
    l, rp_sim, rp_th, nz, ez, nx, ex = res.rows[0]
    assert rp_sim == pytest.approx(rp_th, rel=0.05)
    assert ez < 0.02


def test_mpqkd_calibrates_mean_photon_number():
    mpqkd = load_protocol(EXAMPLES / "mpqkd")
    setup = mpqkd.build(mpqkd.MPQKDParams(mu=0.3))
    net = trace(setup)
    for tag in mpqkd.STATIONS:
        laser = setup[f"{tag}-LD"]
        mu = (
            net.port_power[f"{tag}-SPL.p1"]
            / laser.rep_rate
            / photon_energy(laser.wavelength)
        )
        assert mu == pytest.approx(0.3, rel=1e-6)
