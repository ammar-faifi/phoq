"""Optical delay line (examples/odl_validation.py): group delay, carrier phase, coherence, pulse
overlap in a DLI, coupling loss, and the photon counter resolving pulses inside a bin."""

import math
from pathlib import Path
from runpy import run_path

import pytest

from phoq import OpticalDelayLine
from phoq.engine import report

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "odl_validation.py"))


def test_group_delay_is_the_insertion_delay_plus_the_dial():
    for dial, step, got, want, warned in EX["check_delay"]():
        assert got == pytest.approx(want, abs=1e-16)
        assert warned == (dial > 330e-12)  # set beyond the travel: stops there, and says so


def test_resolution_puts_the_dial_on_the_step_grid():
    odl = OpticalDelayLine(delay=123.4567e-12, resolution=2e-15)
    assert odl.setting == pytest.approx(123.456e-12, abs=1e-20)


@pytest.mark.parametrize("wavelength", [1550e-9, 1310e-9])
def test_one_fringe_per_optical_period(wavelength):
    got, want = EX["check_fringe"](wavelength, span=12e-15, step=0.05e-15)
    assert got == pytest.approx(want, rel=1e-4)


def test_mzi_visibility_falls_as_the_coherence_time():
    for delta, want, rep, syn in EX["check_coherence"]():
        assert rep == pytest.approx(want, abs=0.005)
        assert syn == pytest.approx(want, abs=0.02)


def test_dli_visibility_is_the_overlap_of_neighbouring_pulses():
    rows = EX["check_overlap"](dials=(0.0, 105e-12, 205e-12))
    for dial, delta, want, rep, syn in rows:
        assert rep == pytest.approx(want, abs=0.005)
        assert syn == pytest.approx(want, abs=0.02)
    assert max(rows, key=lambda r: r[3])[0] == 105e-12  # the peak sits where `balance` set it


def test_standard_cords_alone_lose_short_pulses():
    delta, want, rep = EX["check_cords_alone"](EX["WIDTH"])
    assert delta == pytest.approx(-205e-12, abs=2e-12)
    assert rep == pytest.approx(want, abs=0.005) and rep < 0.1


def test_balance_refuses_a_delay_beyond_the_travel():
    setup = EX["build"]()
    setup["DLI-ODL"].insertion_delay = 300e-12  # the cords already leave only 205 ps to add
    with pytest.raises(ValueError, match="travel"):
        EX["balance"](setup, "LD-1", "PD-C", "DLI-ODL")


@pytest.mark.parametrize("oversample", [1, 40])
def test_counter_sub_bins_resolve_pulses_that_miss_each_other(oversample):
    """1 ns pulses 1 ns apart in 10 ns bins: whole bins count them as overlapping (V ≈ V₀);
    250 ps sub-bins see half the overlap, exp(−ln 2) (+2 % for the sub-bin size)."""
    setup = EX["counting"](width=1e-9, oversample=oversample)
    setup["CNT"].set(bin_rate=0.0, slots=20_000)
    odl = setup["DLI-ODL"]
    odl.max_delay = 2e-9
    EX["balance"](setup, "LD-1", "DM1", "DLI-ODL")
    odl.delay += 1e-9
    v0 = EX["ceiling"](setup, "LD-1", "DM1")
    v = EX["counted"](setup, batches=3)
    if oversample == 1:
        assert v == pytest.approx(v0 * math.exp(-math.pi * 100e3 * 11e-9), abs=0.05)
    else:
        assert v == pytest.approx(v0 * EX["overlap"](1e-9, 1e-9) * 1.02, abs=0.06)


def test_coupling_loss_follows_the_gaussian_beam_overlap():
    for wl, dial, want, got in EX["check_loss"]():
        assert got == pytest.approx(want, abs=1e-6)


def test_dli_arms_are_standard_cords():
    dli = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "dli_cow.py"))
    for setup in (dli["build"](), dli["build_cow"](), EX["build"]()):
        lengths = {c.length for c in setup if type(c).__name__ == "Fiber"}
        assert lengths <= {0.5, 1.0, 2.0, 3.0}, lengths
        assert all("ODL" not in w["text"] for w in report(setup)["warnings"])  # within its travel
