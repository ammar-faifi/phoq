"""Coherent one-way QKD (examples/cow): the calibrated hardware, the reference run against the
expected rates, QBER and visibilities, the gain-switched source that has no coherence to show,
and the two parties sifting over the classical channel."""

import math
from pathlib import Path

import numpy as np
import pytest

from phoq.engine import trace
from phoq.parties import Experiment, load_protocol

cow = load_protocol(Path(__file__).resolve().parents[1] / "examples" / "cow")


def test_hardware_is_calibrated():
    p = cow.COWParams()
    setup = cow.build(p)
    assert cow.common.pulse_photons(setup, p) == pytest.approx(p.mu, rel=1e-9)
    short, long_ = cow.common.arms(setup)
    assert long_.delay - short.delay == pytest.approx(p.slot, abs=1e-15)  # the DLI: one slot
    assert abs(long_.amp) == pytest.approx(abs(short.amp), rel=1e-9)  # balanced by BOB-VOA
    a, b = (cow.common.static_amplitude(q) for q in (short, long_))
    assert abs(np.angle(a / b)) < 1e-9  # equal phases interfere constructively on DM1
    net, src = trace(setup), setup["ALICE-LD"]
    db, dm1 = (min(net.delays(setup[d], src)) for d in ("DB", "DM1"))
    assert db + setup["TAGGER"].ch1_delay == pytest.approx(dm1, abs=1e-15)  # slot j is pulse j
    assert all(c.length in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0) for c in setup if type(c).__name__ == "Fiber")


def test_sequence_classes_and_decoding():
    kind = np.array([cow.BIT0, cow.BIT1, cow.BIT0, cow.DECOY, cow.BIT1])
    occ = cow.occupied(kind)
    assert occ.astype(int).tolist() == [1, 0, 0, 1, 1, 0, 1, 1, 0, 1]
    cls = cow.interfering(kind)
    assert {int(j): int(c) for j, c in enumerate(cls) if c >= 0} == {2: 3, 4: 1, 7: 0}  # dark, 1-0, decoy
    db = np.zeros(10, dtype=bool)
    db[[0, 3, 6, 7]] = True  # bit 0 → 0, bit 1 → 1, the decoy clicks twice
    idx, bits, doubles = cow.decode(db, np.random.default_rng(0))
    assert idx.tolist() == [0, 1, 3] and bits[:2].tolist() == [0, 1] and doubles == 1


def test_reference_run_matches_theory():
    res = cow.run(blocks=12, seed=5)
    t, e = res.tally, res.expected
    assert t.detected / t.bits == pytest.approx(e["detected / bit"], rel=0.04)
    assert t.sifted / t.bits == pytest.approx(e["sifted / bit"], rel=0.04)
    assert res.alice_key.size == res.bob_key.size == t.sifted
    assert res.qber == pytest.approx(e["QBER"], abs=0.004)
    assert t.v(net=False) == pytest.approx(e["V raw"], abs=0.02)  # dark counts on DM2
    assert t.v() == pytest.approx(e["V net"], abs=0.025)
    assert e["V net"] == pytest.approx(math.exp(-math.pi * 1e6 / 434e6), rel=1e-3)  # balanced arms
    assert "QBER" in res.summary() and "V raw" in res.summary()


def test_gain_switched_source_shows_no_coherence():
    """Same data line, but every pulse starts with a fresh phase: the monitoring line sees V = 0."""
    res = cow.run(cow.COWParams(source="gain-switched"), blocks=6, seed=3)
    assert abs(res.tally.v(net=False)) < 0.1 and res.expected["V net"] == 0.0
    assert res.qber == pytest.approx(res.expected["QBER"], abs=0.006)


def test_parties_sift_the_same_key():
    setup = cow.build(slots=400_000)
    alice, bob = Experiment(setup, cow.PARTIES, seed=8).run(blocks=3, echo=False)
    assert [w.name for w in (alice, bob)] == ["ALICE", "BOB"] and bob.batches == 3
    for w in (alice, bob):
        assert not any("Traceback" in line for line in w.lines)
        assert w.result["bits sent"] == 600_000 and w.result["sifted bits"] > 2500
        assert w.result["QBER (sampled)"] < 0.05 and w.result["V raw"] > 0.85
    trim = lambda r: {k: v for k, v in r.items() if k not in ("sent [kB]", "received [kB]")}  # noqa: E731
    assert trim(alice.result) == pytest.approx(trim(bob.result), nan_ok=True)
    assert bob.net["sent"] == 6 and alice.net["sent"] == 3  # detect + errors per block; sift


# ---- the eavesdropper ---------------------------------------------------------------------------
EVE = Path(__file__).resolve().parents[1] / "examples" / "cow" / "eve.py"


def test_eve_station_is_calibrated():
    p, c = cow.COWParams(), cow.common
    setup = cow.build(p, eve=True)
    assert c.pulse_photons(setup, p) == pytest.approx(p.mu, rel=1e-9)  # μ at the fiber input, SPL-A
    assert c.eve_pulse_photons(setup, p) == pytest.approx(p.mu, rel=1e-9)  # her loopback, through the bar state
    net, awg = trace(setup), setup["EVE-AWG"]
    alice, eve = setup["ALICE-LD"], setup["EVE-TX-LD"]
    # symbol k of the switch pattern meets Alice's pulse k; Eve's pulse k reaches each of Bob's detectors with it
    assert awg.ch2_delay == pytest.approx(c.passing(net, setup["DB"], alice, setup["EVE-SW"]), abs=1e-15)
    for d in (setup[n] for n in ("DB", "DM1", "DM2")):
        mine = awg.ch1_delay - c.passing(net, d, eve, setup["EVE-TX-IM"]) + min(net.delays(d, eve))
        assert mine == pytest.approx(min(net.delays(d, alice)), abs=1e-15)
    short, long_ = c.arms(setup)  # Bob's interferometer is calibrated as without her
    assert long_.delay - short.delay == pytest.approx(p.slot, abs=1e-15)
    assert abs(long_.amp) == pytest.approx(abs(short.amp), rel=1e-9)
    sw, im = setup["EVE-SW"], setup["EVE-TX-IM"]  # idle: bar state, her transmitter dark
    assert not sw.crossed(sw.static_voltage()) and im.transmission(im.static_voltage()) == pytest.approx(im.t_min)
    assert all(c.length in (0.5, 1.0, 1.5, 2.0, 3.0, 5.0) for c in setup if type(c).__name__ == "Fiber")
    assert "EVE-SW" not in cow.build(p, eve=False)


def test_intercept_resend_breaks_coherence_not_the_qber():
    """The paper's point: a time-of-arrival measurement leaves the data line alone, so with the
    flux matched Bob's rate and QBER stay where they were, while V_d and V_1−0 fall with the
    fraction of bits Eve intercepts — as ``eve_theory`` expects, within the counting statistics."""
    p = cow.COWParams()
    setup = cow.build(p, eve=True)
    runs = {f: cow.run(p, blocks=6, seed=21, setup=setup, eve=f) for f in (0.0, 0.5, 1.0)}
    for f, r in runs.items():
        t, e = r.tally, r.expected
        for which in ("decoy", "1-0"):
            n = t.monitor[cow.MON.index(which), :2].sum()  # DM clicks where the two pulses meet
            v = e[f"V {which}"]
            assert t.v(which) == pytest.approx(v, abs=4 * math.sqrt(max(1 - v * v, 0.05) / n)), (f, which)
        q = e["QBER"]
        assert r.qber == pytest.approx(q, abs=4 * math.sqrt(q * (1 - q) / t.sifted)), f
        assert t.detected / t.bits == pytest.approx(e["detected / bit"], rel=4 / math.sqrt(t.detected)), f
        if f:
            assert r.eve["q"] == pytest.approx(e["q (Eve)"], rel=0.02) and r.eve["mu_e"] == pytest.approx(e["mu_E"], rel=0.03)
    v_d, v_10 = ([runs[f].tally.v(w) for f in runs] for w in ("decoy", "1-0"))
    assert v_d[0] > 0.9 > v_d[1] > 0.3 > v_d[2] and v_10[0] > 0.9 > 0.5 > v_10[1] > v_10[2]
    assert runs[1.0].qber < 0.03 and abs(runs[1.0].qber - runs[0.0].qber) < 0.01
    assert runs[1.0].tally.detected == pytest.approx(runs[0.0].tally.detected, rel=0.1)
    assert "Eve intercepted 100%" in runs[1.0].summary()


def test_three_parties_with_an_eavesdropper(monkeypatch):
    from phoq import parties

    monkeypatch.setattr(parties, "PARTY_REGISTRY", list(parties.PARTY_REGISTRY))  # keep her out of other tests
    monkeypatch.setenv("PHOQ_EVE_FRACTION", "0.5")  # read in her own process
    (eve,) = parties.load_parties(EVE)
    assert eve.name == "EVE" and eve.stage == 1
    setup = cow.build(slots=400_000)  # an Eve party is loaded: build() splices her station in
    assert "EVE-SW" in setup and "EVE-TAGGER" in setup
    exp = Experiment(setup, [*cow.PARTIES, eve], seed=8)
    assert [lt["name"] for lt in exp.laptops()] == ["ALICE", "BOB", "EVE"]
    alice, bob, w_eve = exp.run(blocks=3, echo=False)
    assert not any("Traceback" in line for w in (alice, bob, w_eve) for line in w.lines)
    assert w_eve.batches == 3 and w_eve.result["intercepting"] == pytest.approx(0.5, abs=0.01)
    assert w_eve.result["bits re-sent"] > 10_000 and w_eve.result["re-sent μ_E"] > cow.COWParams().mu
    assert w_eve.result["bits intercepted"] == pytest.approx(300_000, rel=0.01)
    for w in (alice, bob):
        assert w.result["sifted bits"] > 1000 and w.result["QBER (sampled)"] < 0.08
        assert w.result["V 1-0 (net)"] < 0.6 and w.result["V raw"] < 0.75
    trim = lambda r: {k: v for k, v in r.items() if k not in ("sent [kB]", "received [kB]")}  # noqa: E731
    assert trim(alice.result) == pytest.approx(trim(bob.result), nan_ok=True)


def test_eve_stands_down_when_her_switch_drive_is_off(monkeypatch):
    """With EVE-AWG's CH2 output off her switch rests in bar: she intercepts nothing, and the
    running figures on Alice's laptop show a line as clean as with the station idle."""
    from phoq import parties

    monkeypatch.setattr(parties, "PARTY_REGISTRY", list(parties.PARTY_REGISTRY))
    (eve,) = parties.load_parties(EVE)
    setup = cow.build(slots=400_000)
    setup["EVE-AWG"].ch2_enabled = False
    alice, bob, w_eve = Experiment(setup, [*cow.PARTIES, eve], seed=3).run(blocks=2, echo=False)
    assert not any("Traceback" in line for w in (alice, bob, w_eve) for line in w.lines)
    assert any("switch drive off" in line for line in w_eve.lines)
    assert w_eve.result["intercepting"] == 0.0 and w_eve.result["bits re-sent"] == 0
    assert alice.result["V_d now"] > 0.8 and alice.result["V_1-0 now"] > 0.8 and alice.result["QBER now"] < 0.06
    assert alice.result["V_d now"] == alice.result["V decoy (net)"]  # two blocks: the window is everything
