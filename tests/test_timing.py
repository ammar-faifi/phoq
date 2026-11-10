"""Rise time and jitter: the AWG's output edges, and the single-photon detector's click
times and output pulse."""

from pathlib import Path

import numpy as np
import pytest

from phoq import (
    ArbitraryWaveformGenerator,
    Attenuator,
    Laser,
    Oscilloscope,
    PhotonCounter,
    Setup,
    SinglePhotonDetector,
)
from phoq.parties import load_protocol
from phoq.runner import Runner
from phoq.timing import fold
from phoq.units import GAUSS_FWHM, photon_energy

cv = load_protocol(Path(__file__).resolve().parents[1] / "examples" / "cow_vacuum")


def crossing(t: np.ndarray, v: np.ndarray, level: float) -> float:
    """Time at which a rising ``v`` first reaches ``level``, between its samples."""
    i = int(np.argmax(v >= level))
    return float(t[i - 1] + (level - v[i - 1]) / (v[i] - v[i - 1]) * (t[i] - t[i - 1]))


# ---- the AWG's edges -----------------------------------------------------------------
def test_awg_edges_rise_in_the_rise_time_around_their_nominal_time():
    awg = ArbitraryWaveformGenerator()
    pattern = np.array([0.0, 1.0, 0.0, 0.0, 1.0, 1.0])
    awg.set_pattern("ch1", pattern, rate=2e9)
    t = np.linspace(-0.25e-9, 2.75e-9, 30_001)  # one pass of the pattern, 0.1 ps steps
    ideal = awg.voltage("ch1", t)
    assert set(np.unique(ideal)) == {0.0, 1.0}

    awg.rise_time = 100e-12
    v = awg.voltage("ch1", t)
    # the edge between symbols 0 and 1, nominally at 0.25 ns
    edge = (t > 0.0) & (t < 0.5e-9)
    t10, t50, t90 = (crossing(t[edge], v[edge], x) for x in (0.1, 0.5, 0.9))
    assert t90 - t10 == pytest.approx(100e-12, rel=1e-3)
    assert t50 == pytest.approx(0.25e-9, abs=0.1e-12)
    # falling edges alike: the one between symbols 1 and 2, at 0.75 ns
    fall = (t > 0.5e-9) & (t < 1.0e-9)
    f90, f10 = (crossing(t[fall], -v[fall], -x) for x in (0.9, 0.1))
    assert f10 - f90 == pytest.approx(100e-12, rel=1e-3)
    # symbols are still their own level at their centers, and no charge is lost
    assert awg.voltage("ch1", np.arange(6) / 2e9) == pytest.approx(pattern, abs=1e-9)
    assert v.mean() == pytest.approx(ideal.mean(), abs=1e-4)


def test_awg_edges_longer_than_a_symbol_low_pass_the_pattern():
    awg = ArbitraryWaveformGenerator(rise_time=1.5e-9)
    awg.set_pattern("ch1", [0.0, 1.0], rate=2e9)  # 1 GHz square, 0.5 ns symbols
    v = awg.voltage("ch1", np.linspace(0, 4e-9, 4001))
    # a Gaussian low-pass of σ = rise / 2.563 leaves exp(−2π²σ²f²) of the fundamental
    sigma = 1.5e-9 / 2.5631
    left = 4 / np.pi * 0.5 * np.exp(-2 * np.pi**2 * sigma**2 * 1e9**2)
    assert v.max() - v.min() == pytest.approx(2 * left, rel=0.02)
    assert v.mean() == pytest.approx(0.5, abs=1e-3)


def test_awg_square_wave_takes_the_rise_time_too():
    awg = ArbitraryWaveformGenerator(
        ch1_shape="square", ch1_freq=1e9, ch1_vpp=2.0, ch1_duty=0.3, rise_time=50e-12
    )
    t = np.linspace(-0.2e-9, 0.2e-9, 4001)
    v = awg.voltage("ch1", t)
    assert crossing(t, v, 0.8) - crossing(t, v, -0.8) == pytest.approx(50e-12, rel=1e-3)
    # high for 30 % of the cycle
    whole = awg.voltage("ch1", np.linspace(0, 1e-9, 10_001)[:-1])
    assert (whole > 0).mean() == pytest.approx(0.3, abs=1e-3)
    assert (whole.min(), whole.max()) == pytest.approx((-1.0, 1.0), abs=1e-6)


def test_awg_jitter_moves_every_edge_by_its_own_gaussian():
    awg = ArbitraryWaveformGenerator(jitter=5e-12)
    awg.set_pattern("ch1", np.tile([0.0, 1.0], 3000), rate=2e9)
    awg.set_pattern("ch2", np.tile([0.0, 1.0], 3000), rate=2e9)
    rng = np.random.default_rng(1)
    state = rng.bit_generator.state
    awg.start(rng)
    # the realization comes from a stream of its own: the run's draws are as they were
    assert rng.bit_generator.state == state

    t = np.arange(-0.25e-9, 3000e-9, 0.5e-12)

    def errors(ch):
        v = awg.voltage(ch, t)
        edges = t[1:][np.diff((v > 0.5).astype(int)) != 0]
        return edges - (np.rint(edges * 2e9 - 0.5) + 0.5) / 2e9, v

    e1, v1 = errors("ch1")
    e2, _ = errors("ch2")
    assert abs(e1.size - 6000) <= 1  # every edge is there, once
    e1, e2 = e1[:5999], e2[:5999]
    assert e1.std() == pytest.approx(5e-12, rel=0.05)
    assert abs(e1.mean()) < 0.3e-12
    # one edge's error says nothing about the next one's, or about the other output's
    assert abs(np.corrcoef(e1[:-1], e1[1:])[0, 1]) < 0.05
    assert abs(np.corrcoef(e1, e2)[0, 1]) < 0.05
    # asked again, an edge is where it was; a new acquisition moves them all
    assert np.array_equal(awg.voltage("ch1", t), v1)
    awg.start(rng)
    assert not np.array_equal(awg.voltage("ch1", t), v1)


def test_awg_without_rise_time_or_jitter_is_as_before():
    awg = ArbitraryWaveformGenerator()
    pattern = np.random.default_rng(0).normal(size=64)
    awg.set_pattern("ch1", pattern, rate=1e9)
    t = np.random.default_rng(1).uniform(0, 64e-9, 5000)
    k = np.rint(t * 1e9).astype(int) % 64
    assert np.array_equal(awg.voltage("ch1", t), awg.pattern_volts("ch1")[k])
    assert "rise_time" in awg.ui_fields() and "jitter" in awg.ui_fields()


# ---- the detector's click times ------------------------------------------------------
def tagger_bench(
    jitter: float = 0.0, width: float = 100e-12, sub: int = 1, loss_db: float = 58.0
) -> Setup:
    """Weak 100 MHz pulses on one SPD, a bin per pulse."""
    s = Setup("timing", seed=1)
    ld = s.add(
        Laser("LD", mode="pulsed", rep_rate=100e6, pulse_width=width, power_dbm=-20.0)
    )
    voa = s.add(Attenuator("VOA", attenuation_db=loss_db))
    spd = s.add(
        SinglePhotonDetector("SPD", efficiency=0.5, dead_time=0.0, timing_jitter=jitter)
    )
    cnt = s.add(PhotonCounter("CNT", slots=400_000, oversample=sub))
    s.connect(ld.out, voa.p1)
    s.connect(voa.p2, spd.inp)
    s.connect(spd.out, cnt.ch1)
    return s


def test_timing_jitter_spreads_the_stamps_and_leaves_the_clicks():
    plain = Runner(tagger_bench(), seed=4).acquire("CNT")
    jittered = Runner(tagger_bench(jitter=200e-12), seed=4).acquire("CNT")
    # the same clicks, bin for bin: the jitter draws from a stream of its own
    assert np.array_equal(plain.clicks["ch1"], jittered.clicks["ch1"])
    assert plain.counts["ch1"] > 10_000

    centers = np.flatnonzero(plain.clicks["ch1"]) / plain.rate
    assert not plain.fine and np.array_equal(plain.timestamps("ch1"), centers)
    off = jittered.fine["ch1"]
    assert np.allclose(jittered.timestamps("ch1") - centers, off, rtol=0, atol=1e-15)
    assert off.std() == pytest.approx(200e-12 / GAUSS_FWHM, rel=0.03)
    assert abs(off.mean()) < 3e-12
    # a Gaussian: 76 % within half the FWHM on either side
    assert (np.abs(off) < 100e-12).mean() == pytest.approx(0.761, abs=0.015)
    # … and events() gives the same times
    assert np.array_equal(jittered.events()["t"], np.sort(jittered.timestamps("ch1")))
    # folded on the laser's clock the phase is the click's own time error
    n, phase = fold(jittered.timestamps("ch1"), 0.0, 10e-9)
    assert np.array_equal(n, np.flatnonzero(jittered.clicks["ch1"]))
    assert phase == pytest.approx(off, abs=1e-15)


def test_sub_bins_stamp_a_click_where_its_photon_arrived():
    """Dim 1 ns pulses in 10 ns bins of 20 sub-bins: the stamps follow the pulse's
    shape around the bin's center, as finely as the sub-bins show it. They are a
    histogram of the light in steps of w = 0.5 ns, whose variance is the pulse's plus
    w²/6; finer sub-bins come down to the pulse's own."""
    bench = tagger_bench(width=1e-9, sub=20, loss_db=68.0)
    batch = Runner(bench, seed=4).acquire("CNT")
    off = batch.fine["ch1"]
    assert off.size == batch.counts["ch1"] > 10_000
    assert np.all(np.abs(off) <= 5e-9)
    assert abs(off.mean()) < 15e-12
    shape = np.hypot(1e-9 / GAUSS_FWHM, 0.5e-9 / np.sqrt(6))
    assert off.std() == pytest.approx(shape, rel=0.03)
    # the jitter adds to it in quadrature, on the same clicks
    bench = tagger_bench(400e-12, width=1e-9, sub=20, loss_db=68.0)
    both = Runner(bench, seed=4).acquire("CNT")
    assert np.array_equal(both.clicks["ch1"], batch.clicks["ch1"])
    assert both.fine["ch1"].std() == pytest.approx(
        np.hypot(shape, 400e-12 / GAUSS_FWHM), rel=0.03
    )
    # bright pulses click at their first photon, which comes early
    bright = Runner(tagger_bench(width=1e-9, sub=20), seed=4).acquire("CNT")
    assert bright.fine["ch1"].mean() < -30e-12


def test_the_first_photon_of_a_bright_bin_comes_early():
    """Constant light of λ detected photons per bin: the click is at the first one, on
    average 1/λ − 1/(e^λ − 1) of a bin after its start."""
    d = SinglePhotonDetector(efficiency=1.0, dark_count_rate=0.0, dead_time=0.0)
    n, sub, dt, lam = 100_000, 16, 1e-9, 6.0
    amp = np.sqrt(lam / sub * photon_energy(1550e-9) / (dt / sub))
    fields = {1550e-9: np.full(n * sub, amp)}
    clicks = d.detect(fields, dt, np.random.default_rng(1), n, sub=sub)
    assert clicks.mean() == pytest.approx(1 - np.exp(-lam), abs=1e-3)
    off = d.click_offsets(fields, clicks, dt, np.random.default_rng(2), sub=sub)
    want = 1 / lam - 1 / np.expm1(lam) - 0.5
    assert off.mean() / dt == pytest.approx(want, abs=2e-3)
    assert off.min() >= -dt / 2 and off.max() < dt / 2
    # dim light: anywhere in the bin
    dim = {1550e-9: np.full(n * sub, amp * 0.05)}
    few = d.detect(dim, dt, np.random.default_rng(1), n, sub=sub)
    spread = d.click_offsets(dim, few, dt, np.random.default_rng(2), sub=sub)
    assert spread.std() / dt == pytest.approx(1 / np.sqrt(12), rel=0.05)
    # one sub-bin does not resolve the place: the center
    assert not d.click_offsets({1550e-9: np.full(n, amp)}, clicks, dt, None).any()


def test_dark_counts_fall_anywhere_in_a_bin_with_sub_bins():
    d = SinglePhotonDetector(efficiency=0.5, dark_count_rate=1e6, dead_time=0.0)
    n, sub, dt = 200_000, 8, 10e-9
    fields = {1550e-9: np.zeros(n * sub)}
    clicks = d.detect(fields, dt, np.random.default_rng(3), n, sub=sub)
    off = d.click_offsets(fields, clicks, dt, np.random.default_rng(4), sub=sub)
    assert clicks.sum() == pytest.approx(n * 1e6 * dt, rel=0.1)
    assert off.std() / dt == pytest.approx(1 / np.sqrt(12), rel=0.05)


# ---- the detector's output pulse -----------------------------------------------------
def test_spd_output_pulse_has_the_rise_time_on_both_edges():
    d = SinglePhotonDetector(rise_time=1e-9)
    clicks = np.zeros(600, dtype=bool)
    clicks[200] = True
    dt = 50e-12
    t = np.arange(clicks.size) * dt
    v = d.waveform(clicks, dt)  # a 5 ns pulse
    assert crossing(t, v, 0.9) - crossing(t, v, 0.1) == pytest.approx(1e-9, rel=0.01)
    late = t > 12e-9
    fall = crossing(t[late], -v[late], -0.1) - crossing(t[late], -v[late], -0.9)
    assert fall == pytest.approx(1e-9, rel=0.01)
    assert v.max() == pytest.approx(1.0, abs=1e-6)
    assert v.sum() * dt == pytest.approx(5e-9, rel=1e-6)  # the pulse's area stays
    # edges finer than the samples stay steps
    d.rise_time = 5e-12
    assert set(np.unique(d.waveform(clicks, dt))) == {0.0, 1.0}
    assert {"timing_jitter", "rise_time"} <= set(d.ui_fields())


def test_scope_sees_the_click_pulses_at_their_jittered_times():
    def sweep(jitter):
        s = Setup("scope on an SPD", seed=1)
        ld = s.add(
            Laser(
                "LD", mode="pulsed", rep_rate=10e6, pulse_width=100e-12, power_dbm=-20
            )
        )
        voa = s.add(Attenuator("VOA", attenuation_db=45.0))
        spd = s.add(
            SinglePhotonDetector(
                "SPD", efficiency=0.9, dead_time=20e-9, timing_jitter=jitter
            )
        )
        osc = s.add(
            Oscilloscope(
                "OSC", timebase=2e-6, trigger="sync", bandwidth=20e9, adc_bits=0
            )
        )
        s.connect(ld.out, voa.p1)
        s.connect(voa.p2, spd.inp)
        s.connect(spd.out, osc.ch1)
        res = Runner(s, seed=6).acquire("OSC")
        v = res["ch1"].data
        return res.t[1:][(v[1:] > 0.5) & (v[:-1] <= 0.5)]

    sharp, late = sweep(0.0), sweep(3e-9)
    assert sharp.size == late.size > 50  # the same clicks
    moved = late - sharp
    assert np.all(np.abs(moved) < 12e-9)
    assert moved.std() == pytest.approx(3e-9 / GAUSS_FWHM, rel=0.25)


# ---- on a protocol's bench -----------------------------------------------------------
def bright_stamps(sub: int = 8, jitter: float = 0.0, rise: float = 0.0):
    """One block of the bright COW-vacuum bench: D_T's clicks' bins and their phases in
    their slots."""
    p = cv.COWParams(mu=0.5, dead_time=0.0)
    slots = 400_000
    s = cv.build(p, slots)
    s["TAGGER"].oversample = sub
    s["DT"].timing_jitter = jitter
    s["ALICE-AWG"].rise_time = rise
    runner = Runner(s, seed=3)
    kind = cv.common.draw(runner.rng, slots // 2, p)
    volts = s["ALICE-IM"].voltage_for(cv.common.carving(cv.common.occupied(kind)))
    s["ALICE-AWG"].set_pattern("ch1", volts, 2 * p.rate)
    batch = runner.acquire("TAGGER")
    _, phase = fold(batch.timestamps("ch1"), p.slot / 4, p.slot)
    return np.flatnonzero(batch.clicks["ch1"]), phase, p.slot


def test_jitter_takes_clicks_out_of_cow_vacuum_s_click_window():
    """A pulse fills its half slot, so its clicks are stamped anywhere in the click
    window. A Gaussian jitter of σ puts 2σ/(w√(2π)) of them outside a window of width w:
    13.6 % for the PDM-IR's 200 ps on 0.5 ns."""
    bins, phase, tau = bright_stamps()
    inside = (phase >= -tau / 2) & (phase < 0)
    assert bins.size > 2500
    assert inside.mean() == pytest.approx(0.998, abs=0.002)  # all but the leak's clicks
    assert phase[inside].std() == pytest.approx(tau / 2 / np.sqrt(12), rel=0.05)

    again, jittered, _ = bright_stamps(jitter=200e-12)
    assert np.array_equal(again, bins)
    sigma = 200e-12 / GAUSS_FWHM
    lost = 2 * sigma / (tau / 2 * np.sqrt(2 * np.pi))
    kept = ((jittered >= -tau / 2) & (jittered < 0)).mean()
    assert kept == pytest.approx(0.998 * (1 - lost), abs=0.02)
    assert lost == pytest.approx(0.136, abs=0.001)


def test_awg_rise_time_spills_a_carved_pulse_into_its_neighbors():
    """The intensity modulator turns the AWG's 100 ps edges into shorter optical ones,
    and 2.0 % of a 0.5 ns pulse's light leaves each end of its symbol, into the dark
    bins before and after it."""
    bins, _, _ = bright_stamps(sub=16)
    spilled, _, _ = bright_stamps(sub=16, rise=100e-12)
    dark = (bins % 2 == 1).mean()  # the modulator's leak, 30 dB down
    assert dark == pytest.approx(0.002, abs=0.002)
    assert (spilled % 2 == 1).mean() == pytest.approx(2 * 0.0196 + 0.002, abs=0.012)
