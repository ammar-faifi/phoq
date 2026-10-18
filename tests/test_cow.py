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
