"""Delay-line interferometer (examples/dli.py): one slot of delay, DPSK decoding
(examples/dli_for_dpsk.py), and the COW monitoring visibility (examples/dli_for_cow.py)
following the coherence between neighboring pulses, e^{−πΔν·T}."""

import math
from pathlib import Path
from runpy import run_path

import pytest

from phoq.engine import trace

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
DPSK = run_path(str(EXAMPLES / "dli_for_dpsk.py"))
COW = run_path(str(EXAMPLES / "dli_for_cow.py"))
T = 1 / DPSK["RATE"]


def test_the_long_arm_is_one_slot_longer():
    setup = DPSK["build"]()
    d = sorted(trace(setup).delays(setup["PD-C"]))
    assert d[1] - d[0] == pytest.approx(T, abs=1e-13)


def test_dpsk_decodes_without_errors_on_a_narrow_laser():
    r = DPSK["decode"](DPSK["build"](linewidth=100e3), timebase=200e-9)
    assert r["slots"] > 150 and r["errors"] == 0
    assert r["contrast"] > 0.98


def test_dpsk_contrast_follows_the_pulse_to_pulse_coherence():
    r = DPSK["decode"](DPSK["build"](linewidth=10e6), timebase=200e-9)
    assert r["contrast"] == pytest.approx(math.exp(-math.pi * 10e6 * T), abs=0.08)


@pytest.mark.parametrize("linewidth", [100e3, 10e6])
def test_cow_visibility_is_the_coherence_between_neighboring_pulses(linewidth):
    m = COW["monitor"](COW["build"](linewidth=linewidth), batches=3)
    assert m["V"] == pytest.approx(math.exp(-math.pi * linewidth * T), abs=0.04)
    assert m["qber"] < 0.005  # the data line does not see coherence at all


def test_cow_visibility_vanishes_without_a_phase_relation():
    m = COW["monitor"](COW["build"](randomized=True), batches=3)
    assert abs(m["V"]) < 0.06 and m["qber"] < 0.005


def test_tagger_channel_delay_aligns_like_a_matched_cord():
    """DB behind 1 m with CH1 delayed 9.8 ns counts as DB behind 3 m
    (the DLI short arm's fiber)."""
    tagged = COW["monitor"](COW["build"](), batches=2)
    cord = COW["build"]()
    cord["CNT"].ch1_delay = 0.0
    cord.port("DB.inp").peer.component.length = 3.0
    matched = COW["monitor"](cord, batches=2)
    assert tagged["V"] == pytest.approx(matched["V"], abs=0.005)
    assert (
        tagged["qber"] == pytest.approx(matched["qber"], abs=0.0005)
        and tagged["qber"] < 0.005
    )
    unaligned = COW["build"]()
    unaligned["CNT"].ch1_delay = (
        0.0  # DM1/DM2 then land almost a slot late: wrong pairs counted
    )
    assert COW["monitor"](unaligned, batches=2)["V"] < 0.6
