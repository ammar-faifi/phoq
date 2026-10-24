"""Clock and frame synchronization from time stamps (examples/clock_sync.py): Bob
recovers Alice's clock and the delay from a free-running tagger, against the traced
truth. Alice's CW laser is carved by her intensity modulator; how much of a symbol the
pulse fills sets the clock line."""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "clock_sync.py"))


@pytest.mark.parametrize(
    "bench", [dict(km=0.0), dict(km=25.0, ppm=-57.0, epoch=7.77e-6)]
)
def test_bob_recovers_clock_and_delay(bench):
    r = EX["report"](**bench)
    s, true = r["sync"], r["truth"]
    assert s["clicks"] > 1000
    assert (
        abs(r["f error ppm"]) < 0.15
    )  # ≈ 30 ppb rms from the 2.5 ns pulse, against tens of ppm
    assert (
        s["frame"]["L"] == true["L"]
        and s["frame"]["significance"] > 4 * s["frame"]["noise"]
    )
    assert abs(r["delay error"]) < 200e-12
    assert r["assigned"] > 0.999 and s["delay"]["on ones"] > 0.98
    assert r["depth"] == pytest.approx(math.sin(math.pi / 4) / (math.pi / 4), abs=0.03)
    if bench["km"]:
        assert (
            true["delay"] > true["L"] / true["f"]
        )  # Δ itself is beyond one frame: known mod P only


def test_nrz_carving_leaves_no_clock_line():
    """Half-symbol pulses keep a line of depth 2/π; NRZ has its null exactly at the
    symbol rate."""
    half = EX["report"](carve=2)
    assert half["depth"] == pytest.approx(2 / math.pi, abs=0.04)
    assert abs(half["f error ppm"]) < 0.3 and half["assigned"] > 0.999
    nrz = EX["report"](carve=1)
    assert nrz["depth"] < 0.1 and nrz["assigned"] < 0.5  # step 1 has nothing to lock to


def test_analysis_on_synthetic_time_stamps():
    """The same chain on hand-made clicks: spread over a quarter-symbol pulse, plus 1 %
    dark counts."""
    rng = np.random.default_rng(4)
    string = rng.integers(0, 2, 640).astype(np.int8)
    f, delay = 80e6 * (1 + 41e-6), 1.2345e-6
    k = np.sort(rng.choice(np.flatnonzero(np.tile(string, 400)), 2000, replace=False))
    t = k / f + delay + rng.uniform(-1 / (8 * f), 1 / (8 * f), k.size)
    t = np.sort(np.concatenate([t, rng.uniform(0, t[-1], 20)]))
    s = EX["synchronize"](t, string, f_nom=80e6)
    assert s["clock"]["f"] == pytest.approx(f, rel=1e-7)
    assert s["frame"]["L"] == string.size
    p = string.size / f
    err = (s["delay"]["delay mod P"] - delay % p + p / 2) % p - p / 2
    assert abs(err) < 200e-12
    assert math.isclose(s["delay"]["on ones"], 0.995, abs_tol=0.01)


def test_one_nanosecond_time_bins():
    """1 GHz symbols, a 4 GS/s AWG carving 250 ps pulses, 20 ps tagger bins, a 50 ns
    detector."""
    r = EX["report"](record=2e-4, dead_time=50e-9, **EX["GHZ"])
    s, true = r["sync"], r["truth"]
    assert s["clicks"] > 1500 and true["f"] == pytest.approx(
        1e9 * (1 + EX["PPM"] * 1e-6)
    )
    assert abs(r["f error ppm"]) < 0.15
    assert (
        s["frame"]["L"] == true["L"]
        and s["frame"]["significance"] > 4 * s["frame"]["noise"]
    )
    assert abs(r["delay error"]) < 30e-12 and r["assigned"] > 0.999
    assert r["depth"] == pytest.approx(math.sin(math.pi / 4) / (math.pi / 4), abs=0.03)


def test_saturated_detector_ticks_next_to_the_frame():
    """A 1 µs dead time at 1 GHz: about one click per dead time plus a short wait. With
    so few clicks the blind frame check cannot confirm L, but the clock and the delay
    (which use the agreed L) hold.
    """
    r = EX["report"](record=2e-4, **EX["GHZ"])
    s = r["sync"]
    assert s["clicks"] < 260 and 1000 <= r["spacing"] < 1060  # symbols between clicks
    lengths, sig = s["frame"]["scan"]
    assert sig[lengths == 1000][0] < 10  # ≈ N / √(2L): too weak to stand out
    assert (
        abs(r["f error ppm"]) < 0.3
        and abs(r["delay error"]) < 50e-12
        and r["assigned"] > 0.99
    )


def test_dead_time_outlasts_the_tagger_chunks_and_records():
    """A 40 µs dead time against 10 µs chunks: the detector stays dead across each cut,
    and across the start of the next acquisition, so a bright input clicks exactly once
    per dead time.
    """
    from phoq import Laser, PhotonCounter, Runner, Setup, SinglePhotonDetector

    s = Setup("dead time")
    laser = s.add(Laser("LD", mode="cw", power_dbm=-30.0))  # ~80 photons per 10 ns bin
    spd = s.add(
        SinglePhotonDetector(
            "SPD", efficiency=1.0, dark_count_rate=0.0, dead_time=40e-6
        )
    )
    cnt = s.add(PhotonCounter("CNT", bin_rate=1e8, slots=400_000, chunk=1000))
    s.connect(laser.out, spd.inp)
    s.connect(spd.out, cnt.ch1)
    runner = Runner(s, seed=1)
    t = np.concatenate([runner.acquire("CNT").timestamps("ch1") for _ in range(2)])
    assert t.size == 200  # 8 ms / 40 µs
    assert np.diff(t).min() == pytest.approx(40e-6, abs=1e-12)


def test_long_dead_time_below_saturation_recovers_clock_and_delay():
    """40 µs, attenuated until a live detector waits about τd/10: N ≈ T/(τd + W) clicks,
    the copies of the clock line every 1/τd washed out, and 45 clicks enough for the
    clock and the delay.
    """
    r = EX["report"](record=2e-3, dead_time=40e-6, mu=0.002, **EX["LONG"])
    s = r["sync"]
    w = EX["waiting_time"](r["setup"])
    assert w / 40e-6 == pytest.approx(0.1, abs=0.01)
    assert s["clicks"] == pytest.approx(2e-3 / (40e-6 + w), rel=0.15)
    assert np.diff(r["t"]).min() >= 40e-6 - 1e-12
    assert s["clock"]["runner-up"] < 0.6
    assert (
        abs(r["f error ppm"]) < 0.2
        and abs(r["delay error"]) < 100e-12
        and r["assigned"] > 0.99
    )


def test_saturated_long_dead_time_strobes_the_clock():
    """Saturated (μ = 0.2), the clicks come every τd plus ~40 ns: copies of the clock
    line every 1/τd stand as tall as the line itself, which the runner-up ratio flags
    without the truth.
    """
    r = EX["report"](record=2e-3, dead_time=40e-6, **EX["LONG"])
    assert r["sync"]["clicks"] == pytest.approx(50, abs=1)
    assert r["sync"]["clock"]["runner-up"] > 0.9


def test_long_dead_time_frame_check_needs_hundreds_of_clicks():
    """20 ms at 40 µs below saturation: ~450 clicks, enough for N/√(2L) to clear the
    noise."""
    r = EX["report"](record=20e-3, dead_time=40e-6, mu=0.002, **EX["LONG"])
    fr = r["sync"]["frame"]
    assert fr["L"] == EX["L"] and fr["significance"] > 2 * fr["noise"]
    assert r["sync"]["clock"]["runner-up"] < 0.3
