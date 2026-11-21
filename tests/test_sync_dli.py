"""Clock and frame synchronization at 1 GHz on three SPADs held off 10 µs
(examples/clock_sync_dli.py): a direct line and a DLI's bright and dark lines, each
seeing Alice's string through its own pattern. Bob recovers one clock from all of them
and each line's delay, against the traced truth."""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest

from phoq.units import FIBER_DELAY

EX = run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "clock_sync_dli.py")
)


@pytest.fixture(scope="module")
def run():
    """The example's one setting, one 10 ms record, shared by the tests below."""
    return EX["report"]()


def test_bob_recovers_clock_and_every_lines_delay(run):
    """One clock from all the lines' clicks; each line's delay against its own pattern,
    to about the per-click spread over √N, ≈ 0.1 ns/√N for 250 ps pulses in 250 ps
    bins."""
    lines = run["lines"]
    assert lines["direct"]["clicks"] > 900
    assert lines["bright"]["clicks"] > 800 and lines["dark"]["clicks"] > 650
    # ≈ 0.7 ppb rms from 2600 clicks over 10 ms, against tens of ppm
    assert abs(run["f error ppm"]) < 0.005
    for line, x in lines.items():
        spread = 0.1e-9 / math.sqrt(x["clicks"])
        # 10 ps for the clock error: 5 ps per ppb at the record's middle, 5 ms in
        assert abs(x["delay error"]) < 4 * spread + 10e-12, line
        assert x["assigned"] > 0.999 and x["delay"]["on light"] > 0.98, line
    traced = {line: x["truth"]["delay"] for line, x in lines.items()}
    # the bright and the dark SPAD share the DLI's short arm: the 1 m cord into it, its
    # 1.25 m of cords and the delay line, against the direct SPAD's cord from the tap
    odl = run["setup"]["DLI-ODL"]
    behind = 2.25 * FIBER_DELAY + odl.insertion_delay + odl.delay
    assert traced["bright"] - traced["direct"] == pytest.approx(behind, abs=1e-12)
    assert traced["dark"] == pytest.approx(traced["bright"], abs=1e-15)
    true = lines["direct"]["truth"]
    assert true["delay"] > true["L"] / true["f"]  # 25 km: Δ is known mod P only


def test_the_dli_lines_rescue_the_saturated_direct_line(run):
    """The direct SPAD waits 40 ns against its 10 µs hold-off: its clicks come almost
    every 10 µs and strobe the clock, a copy of the line 100 ppm away standing nearly as
    tall. The DLI's lines, below saturation, wash the copy out of the shared clock."""
    direct = run["lines"]["direct"]
    hold_off = run["setup"]["SPD-DIRECT"].dead_time
    assert np.median(np.diff(direct["t"])) < 1.02 * hold_off
    assert EX["recover_clock"](direct["t"])["runner-up"] > 0.8
    assert run["clock"]["runner-up"] < 0.6


def test_the_lines_see_their_patterns(run):
    """Clicks per symbol by its bits (s_{k−1}, s_k), from the traced delays: the direct
    line clicks where s_k = 1, the bright line four times as often where two 1s meet as
    where the string changes, the dark line where the string changes and not where two
    1s meet (the DLI is locked and its arms balanced)."""
    s = run["setup"].string.astype(int)
    kind = 2 * np.roll(s, 1) + s  # 00, 01, 10, 11
    size = np.bincount(kind, minlength=4)
    per = {}
    for line, x in run["lines"].items():
        true = x["truth"]
        k = np.rint((x["t"] - true["delay"]) * true["f"]).astype(np.int64) % s.size
        per[line] = np.bincount(kind[k], minlength=4) / size
    direct, bright, dark = per["direct"], per["bright"], per["dark"]
    assert direct[0] + direct[2] < 0.01 * (direct[1] + direct[3])
    assert bright[0] < 0.05 * bright[1] and bright[3] > 3 * bright[1]
    assert dark[0] < 0.05 * dark[1] and dark[3] < 0.05 * dark[1]
    assert bright[2] > 0.8 * bright[1] and dark[2] > 0.8 * dark[1]


def test_each_line_needs_its_own_pattern(run):
    """Against the string itself, the bright line peaks at its delay and one symbol
    later alike, and the dark line not at all: a random string's changes are
    uncorrelated with the string at every lag. The dark line's own pattern peaks."""
    f, string = run["clock"]["f"], run["setup"].string
    bright, dark = run["lines"]["bright"], run["lines"]["dark"]
    xc = EX["recover_delay"](bright["t"], f, string)["xcorr"]
    two = (np.argsort(xc)[-2:] - bright["delay"]["D"]) % string.size
    assert sorted(two) == [0, 1]
    # the tallest of 1000 noise lags is ≈ 3 σ; the dark line's ~700 clicks peak higher
    assert EX["recover_delay"](dark["t"], f, string)["peak / rms"] < 5
    assert dark["delay"]["peak / rms"] > 10


def test_lines_on_synthetic_time_stamps():
    """Hand-made clicks drawn on each line's pattern, the DLI's two lines 11.2 ns behind
    the direct one: one clock from all of them, and each delay from its own pattern."""
    rng = np.random.default_rng(5)
    string = rng.integers(0, 2, 640).astype(np.int8)
    f = 80e6 * (1 + 41e-6)
    behind = 1.2345e-6 + 11.2e-9
    delays = {"direct": 1.2345e-6, "bright": behind, "dark": behind}
    expect = EX["patterns"](string)
    stamps = {}
    for line, delay in delays.items():
        light = np.tile(expect[line], 400)
        k = np.sort(rng.choice(light.size, 1500, replace=False, p=light / light.sum()))
        stamps[line] = k / f + delay + rng.uniform(-1 / (8 * f), 1 / (8 * f), k.size)
    s = EX["synchronize_lines"](stamps, string, f_nom=80e6)
    assert s["clock"]["f"] == pytest.approx(f, rel=1e-7)
    p = string.size / f
    for line, delay in delays.items():
        got = s["lines"][line]["delay"]["delay mod P"]
        assert abs((got - delay % p + p / 2) % p - p / 2) < 200e-12, line
