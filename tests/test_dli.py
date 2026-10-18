"""Delay-line interferometer (examples/dli_cow.py): one slot of delay, DPSK decoding, and the COW
monitoring visibility following the coherence between neighbouring pulses, e^{−πΔν·T}."""

import math
from pathlib import Path
from runpy import run_path

import pytest

from phoq.engine import trace

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "dli_cow.py"))
T = 1 / EX["RATE"]


def test_the_long_arm_is_one_slot_longer():
    setup = EX["build"]()
    d = sorted(trace(setup).delays(setup["PD-C"]))
    assert d[1] - d[0] == pytest.approx(T, abs=1e-13)


def test_dpsk_decodes_without_errors_on_a_narrow_laser():
    r = EX["decode"](EX["build"](linewidth=100e3), timebase=200e-9)
    assert r["slots"] > 150 and r["errors"] == 0
    assert r["contrast"] > 0.98


def test_dpsk_contrast_follows_the_pulse_to_pulse_coherence():
    r = EX["decode"](EX["build"](linewidth=10e6), timebase=200e-9)
    assert r["contrast"] == pytest.approx(math.exp(-math.pi * 10e6 * T), abs=0.08)


@pytest.mark.parametrize("linewidth", [100e3, 10e6])
def test_cow_visibility_is_the_coherence_between_neighbouring_pulses(linewidth):
    m = EX["monitor"](EX["build_cow"](linewidth=linewidth), batches=3)
    assert m["V"] == pytest.approx(math.exp(-math.pi * linewidth * T), abs=0.04)
    assert m["qber"] < 0.005  # the data line does not see coherence at all


def test_cow_visibility_vanishes_without_a_phase_relation():
    m = EX["monitor"](EX["build_cow"](randomized=True), batches=3)
    assert abs(m["V"]) < 0.06 and m["qber"] < 0.005


def test_tagger_channel_delay_aligns_like_a_matched_cord():
    """DB behind 1 m with CH1 delayed 9.8 ns counts as DB behind 3 m (the DLI short arm's fiber)."""
    tagged = EX["monitor"](EX["build_cow"](), batches=2)
    cord = EX["build_cow"]()
    cord["CNT"].ch1_delay = 0.0
    cord.port("DB.inp").peer.component.length = 3.0
    matched = EX["monitor"](cord, batches=2)
    assert tagged["V"] == pytest.approx(matched["V"], abs=0.005)
    assert tagged["qber"] == pytest.approx(matched["qber"], abs=0.0005) and tagged["qber"] < 0.005
    unaligned = EX["build_cow"]()
    unaligned["CNT"].ch1_delay = 0.0  # DM1/DM2 then land almost a slot late: wrong pairs counted
    assert EX["monitor"](unaligned, batches=2)["V"] < 0.6
