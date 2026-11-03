"""COW with a vacuum decoy sequence (examples/cow_vacuum): the paper's arithmetic
against first principles and its figures, the calibrated hardware, counted gains against
the theory, what wrong settings do to the bound, and the two parties sifting over the
classical channel."""

import math
from pathlib import Path

import numpy as np
import pytest

from phoq.engine import trace
from phoq.parties import Experiment, load_protocol

cv = load_protocol(Path(__file__).resolve().parents[1] / "examples" / "cow_vacuum")
bound = cv.bound

# enough counts on every gain within a few blocks
BRIGHT = cv.COWParams(mu=0.5, decoy1=0.25, decoy2=0.25)
POINTS = [
    (0.02, 0.8, 0.5, 1e-8),
    (0.2, 0.25, 0.3, 1e-6),
    (0.5, 0.05, 0.7, 1e-5),
    (1.0, 0.5, 0.5, 1e-3),
]


# ---- the paper's arithmetic ----------------------------------------------------------
def no_click(a, b, mode, kappa, p_d):
    """⟨a| (1 − p_d) :e^{−κ n}: |b⟩ / ⟨a|b⟩ for coherent pairs and the mode
    mode·(a₁, a₂)."""
    return (1 - p_d) * np.exp(-kappa * np.conj(np.dot(mode, a)) * np.dot(mode, b))


def element(a, b, eta, t_b, p_d, port):
    """⟨a|M†M|b⟩ from first principles: D_T quiet in both windows, the other monitoring
    detector quiet, D_M``port`` clicking."""
    a, b = np.asarray(a, dtype=complex), np.asarray(b, dtype=complex)
    overlap = np.exp(-(abs(a) ** 2).sum() / 2 - (abs(b) ** 2).sum() / 2 + np.vdot(a, b))
    tau, rho = t_b * eta, (1 - t_b) * eta
    plus, minus = np.array([1, 1]) / math.sqrt(2), np.array([1, -1]) / math.sqrt(2)
    click, quiet = (plus, minus) if port == 0 else (minus, plus)
    data = no_click(a, b, [1, 0], tau, p_d) * no_click(a, b, [0, 1], tau, p_d)
    return (
        overlap
        * data
        * no_click(a, b, quiet, rho, p_d)
        * (1 - no_click(a, b, click, rho, p_d))
    ).real


def x_basis_gain(sign, mu, eta, t_b, p_d, port):
    """Q_0x (sign +1) or Q_1x (sign −1) at a monitoring detector, from the coherent
    pairs |0,α⟩ and |α,0⟩."""
    al = math.sqrt(mu)
    pairs = (((0, al), 1), ((al, 0), sign))
    norm = bound.normalization(mu)[0 if sign > 0 else 1]
    return (
        sum(
            sa * sb * element(a, b, eta, t_b, p_d, port)
            for a, sa in pairs
            for b, sb in pairs
        )
        / norm
    )


@pytest.mark.parametrize("mu, eta, t_b, p_d", POINTS)
def test_appendix_a_matches_first_principles(mu, eta, t_b, p_d):
    al = math.sqrt(mu)
    g = bound.paper_model(mu, eta, t_b, p_d)[0]
    for port in (0, 1):
        want = {
            "q0a": ((0, al), (0, al)),
            "qa0": ((al, 0), (al, 0)),
            "qaa": ((al, al), (al, al)),
            "q00": ((0, 0), (0, 0)),
        }
        for name, (a, b) in want.items():
            assert getattr(g, name)[port] == pytest.approx(
                element(a, b, eta, t_b, p_d, port), rel=1e-10
            )
    closed = bound.nonclassical_gains(mu, eta, t_b, p_d)
    np_, nm = bound.normalization(mu)
    for port in (0, 1):
        q0x = x_basis_gain(+1, mu, eta, t_b, p_d, port)
        q1x = x_basis_gain(-1, mu, eta, t_b, p_d, port)
        assert closed[port] == pytest.approx(q0x, rel=1e-10)
        # the relation behind Eq. (5), from Eq. (3)
        assert np_ * q0x + nm * q1x == pytest.approx(
            2 * (g.q0a[port] + g.qa0[port]), rel=1e-10
        )


@pytest.mark.parametrize("mu", [1e-3, 0.02, 0.2, 0.5, 1.0])
@pytest.mark.parametrize("eta", [0.01, 0.1, 0.8])
@pytest.mark.parametrize("t_b", [0.2, 0.5, 0.8])
@pytest.mark.parametrize("p_d", [1e-8, 1e-5])
def test_the_bound_brackets_the_true_gains(mu, eta, t_b, p_d):
    g = bound.paper_model(mu, eta, t_b, p_d)[0]
    upper, lower = bound.nonclassical_bounds(mu, g)
    m0, m1 = bound.nonclassical_gains(mu, eta, t_b, p_d)
    assert m1 <= upper and m0 >= lower
    assert bound.true_phase_error(mu, eta, t_b, p_d) <= bound.phase_error(mu, g)
    assert sum(bound.terms(mu, g).values()) == pytest.approx(bound.phase_error(mu, g))


def test_the_bound_stands_far_above_the_true_error():
    g = bound.paper_model(0.02, 0.8)[0]
    assert bound.true_phase_error(0.02, 0.8) == pytest.approx(0.0030, abs=1e-4)
    assert bound.phase_error(0.02, g) == pytest.approx(0.169, abs=1e-3)


def eta_at(km, eta_d=0.8):
    return eta_d * 10 ** (-0.02 * km)


def test_the_papers_phase_error_curve():
    """Fig. 3, active basis choice, e_a = 2 %, read off its axis."""
    for km, read in ((0, 0.17), (100, 0.24)):
        o = bound.optimum(eta_at(km), e_a=0.02, active=True)
        assert o["E_p"] == pytest.approx(read, abs=0.01)


def test_the_papers_key_rate_scales_as_eta_squared():
    """ "The key rate scales linearly with 0.005η²" at e_a = 0."""
    for km in (0, 25):
        o = bound.optimum(eta_at(km))
        assert o["R"] / eta_at(km) ** 2 == pytest.approx(0.005, abs=4e-4)
        assert o["t_b"] == pytest.approx(0.5, abs=0.05)


@pytest.mark.parametrize(
    "p_d, eta_d, e_a, active, read",
    [
        (1e-8, 0.8, 0.05, False, 68),  # Fig. 2
        (1e-8, 0.8, 0.02, False, 90),  # Fig. 3
        (1e-8, 0.8, 0.02, True, 102),  # Fig. 3
        (1e-7, 0.99, 0.01, False, 76),  # Fig. 5
        (1e-7, 0.99, 0.01, True, 88),  # Fig. 5
    ],
)
def test_the_papers_curves_end_where_it_shows(p_d, eta_d, e_a, active, read):
    """The distance where the rate falls off the figures' axis, 10⁻⁸."""
    a, b = 30.0, 130.0
    while b - a > 0.25:
        km = (a + b) / 2
        rate = bound.optimum(eta_at(km, eta_d), p_d, e_a, active)["R"]
        a, b = (km, b) if rate > 1e-8 else (a, km)
    assert a == pytest.approx(read, abs=2.5)


# ---- the hardware --------------------------------------------------------------------
def test_hardware_is_calibrated():
    p = cv.COWParams()
    setup = cv.build(p)
    assert cv.pulse_photons(setup, p) == pytest.approx(p.mu, rel=1e-9)
    assert 0.0 < setup["ALICE-VOA"].attenuation_db < 80.0
    assert p.dark_count_rate == pytest.approx(4.34, abs=0.01)
    arms = cv.common.arms
    short, long_ = arms(setup)
    # the DLI: one slot, balanced by BOB-VOA
    assert long_.delay - short.delay == pytest.approx(p.slot, abs=1e-15)
    assert abs(long_.amp) == pytest.approx(abs(short.amp), rel=1e-9)
    a, b = (cv.common.static_amplitude(q) for q in (short, long_))
    assert abs(np.angle(a / b)) < 1e-9  # a pair's pulses add on D_M0
    th = cv.theory(setup, p)
    assert th["ports"][0].cross > 0 > th["ports"][1].cross
    net, src = trace(setup), setup["ALICE-LD"]
    dt, dm0 = (min(net.delays(setup[d], src)) for d in ("DT", "DM0"))
    # slot j is pulse j
    assert dt + setup["TAGGER"].ch1_delay == pytest.approx(dm0, abs=1e-15)


def test_the_four_states():
    kind = np.array([cv.BIT0, cv.BIT1, cv.DECOY1, cv.DECOY2])
    occ = cv.occupied(kind)
    assert occ.tolist() == [0, 1, 1, 0, 1, 1, 0, 0]  # bit 0: |0⟩|α⟩, as the paper
    assert cv.carving(occ)[:4].tolist() == [0, 0, 1, 0]
    p = cv.COWParams(decoy1=0.1, decoy2=0.2)
    assert p.probabilities == pytest.approx((0.35, 0.35, 0.1, 0.2))
    drawn = np.bincount(cv.draw(np.random.default_rng(0), 100_000, p), minlength=4)
    assert drawn / 100_000 == pytest.approx(p.probabilities, abs=0.01)


def clicks_of(pairs, dt=(), dm0=(), dm1=()):
    out = {ch: np.zeros(2 * pairs, dtype=bool) for ch in (cv.DT, cv.DM0, cv.DM1)}
    out[cv.DT][list(dt)] = True
    out[cv.DM0][list(dm0)] = True
    out[cv.DM1][list(dm1)] = True
    return out


def test_one_event_per_pair():
    rng = np.random.default_rng(1)
    # pair 0: the second slot on D_T; pair 1: the first; pairs 2, 3: the monitoring
    # detectors in the pair's second slot; pair 4: D_M0 in its first slot, which is not
    # used; pair 5: nothing
    clicks = clicks_of(6, dt=(1, 2), dm0=(5, 8), dm1=(7,))
    event, multi = cv.events(clicks, rng)
    assert event.tolist() == [cv.T0, cv.T1, cv.M0, cv.M1, cv.NONE, cv.NONE]
    assert multi == 0
    idx, bits, mon, det = cv.announcements(event)
    assert idx.tolist() == [0, 1] and bits.tolist() == [0, 1]
    assert mon.tolist() == [2, 3] and det.tolist() == [0, 1]
    table = cv.monitor_table(np.array([0, 1, 2, 3, 0, 0]), mon, det)
    assert table[cv.DECOY1].tolist() == [1, 0, 1]
    assert table[cv.DECOY2].tolist() == [0, 1, 1]
    assert table[cv.BIT0].tolist() == [0, 0, 3]


def test_several_clicks_give_one_at_random():
    n = 20_000
    second = 2 * np.arange(n) + 1
    event, multi = cv.events(
        clicks_of(n, dt=second, dm0=second), np.random.default_rng(2)
    )
    assert multi == n
    assert set(event.tolist()) == {cv.T0, cv.M0}
    assert np.mean(event == cv.T0) == pytest.approx(0.5, abs=0.02)
    # the theory's rule is the same one
    assert cv.one_event([1.0, 0.0, 1.0, 0.0]) == [0.5, 0.0, 0.5, 0.0]
    assert sum(cv.one_event([0.3, 0.2, 0.1, 0.4])) == pytest.approx(
        1 - 0.7 * 0.8 * 0.9 * 0.6
    )


# ---- counted against expected --------------------------------------------------------
def assert_gains_match(tally, expected, sigmas=4.0):
    for s, want in zip(cv.STATES, expected):
        sent = tally.monitor[s, 2]
        for d in (0, 1):
            n, mean = tally.monitor[s, d], sent * want[d]
            assert abs(n - mean) <= sigmas * math.sqrt(mean) + 1, (s, d, n, mean)


def test_reference_run_matches_theory():
    res = cv.run(BRIGHT, blocks=16, seed=3)
    t, e = res.tally, res.expected
    assert_gains_match(t, e["gains"])
    assert t.q == pytest.approx(e["Q"], rel=0.02)
    assert res.alice_key.size == res.bob_key.size == t.sifted
    assert res.e_b == pytest.approx(e["E_b"], abs=6e-4)
    assert t.multi > 0  # at this μ the rule is at work
    assert t.phase_error(0.5) == pytest.approx(
        e["E_p^u"], abs=4 * t.counting_error(0.5)
    )
    assert t.visibility == pytest.approx(e["V"], abs=0.005)
    assert "E_p^u" in res.summary() and "not in the bound" in res.summary()


def test_four_sub_bins_read_as_two():
    """The pulses sit off center so that two sub-bins sample each once: with four, and
    the pulses centered as in examples/cow, the gains are the same."""
    setup = cv.build(BRIGHT)
    setup["TAGGER"].oversample = 4
    setup["ALICE-AWG"].ch1_delay = 0.0
    res = cv.run(BRIGHT, blocks=8, seed=4, setup=setup)
    assert_gains_match(res.tally, res.expected["gains"])
    assert res.tally.q == pytest.approx(res.expected["Q"], rel=0.03)


def test_the_default_link_reads_near_its_expected_bound():
    res = cv.run(blocks=6, seed=6)
    t, e = res.tally, res.expected
    assert_gains_match(t, e["gains"])
    assert t.sifted == pytest.approx(6 * 200_000 * e["sifted / pair"], rel=0.5)
    assert abs(t.phase_error(cv.params.mu) - e["E_p^u"]) < 2 * t.counting_error(
        cv.params.mu
    )


def test_the_default_operating_point():
    p = cv.COWParams()
    th = cv.theory(cv.build(p), p)
    assert th["E_p^u"] == pytest.approx(0.278, abs=0.001)
    assert th["E_b"] == pytest.approx(0.0021, abs=1e-4)
    assert th["Q"] == pytest.approx(9.4e-5, rel=0.01)
    assert th["terms"]["beta"] == pytest.approx(0.181, abs=0.001)
    assert th["terms"]["M0"] == pytest.approx(0.081, abs=0.001)
    assert th["terms"]["M1"] == pytest.approx(0.016, abs=0.001)
    assert th["coherence"] == pytest.approx(math.exp(-math.pi * 1e6 / 434e6))


def cost_rows(th):
    """The steps from the paper's model to this bench, as in the README."""
    tau, (m0, m1) = th["tau"], th["ports"]
    lossless = (tau / 2, tau / 2, tau / 2)
    side = (tau / 4, tau / 4, tau / 4)
    ports = lambda a: (cv.Port(*a), cv.Port(a[0], a[1], -a[2]))  # noqa: E731
    bench = (m0, m1)
    v = th["coherence"]
    return [
        (ports(lossless), 0.0, 1.0),
        (ports(side), 0.0, 1.0),
        (bench, 0.0, 1.0),
        (bench, 0.0, v),
        (bench, 1e-4, v),
        (bench, 1e-3, v),
    ]


def test_what_the_real_bench_costs():
    p = cv.COWParams()
    th = cv.theory(cv.build(p), p)
    expected = [
        (8.44e-3, 0.236, 7.91e-4),
        (4.15e-3, 0.239, 3.89e-4),
        (1.67e-3, 0.250, 1.56e-4),
        (1.52e-3, 0.253, 1.43e-4),
        (1.38e-3, 0.259, 1.29e-4),
        (9.35e-4, 0.272, 8.80e-5),
    ]
    for (ports, leak, v), (mu_want, e_want, q_want) in zip(cost_rows(th), expected):
        model = lambda m: cv.pair_gains(
            m, th["tau"], ports, leak, v, p.p_d
        )  # noqa: E731
        mu = bound.best_mu(model)[0]
        g, q, _ = model(mu)
        assert mu == pytest.approx(mu_want, rel=0.02)
        assert bound.phase_error(mu, g) == pytest.approx(e_want, abs=0.002)
        assert q == pytest.approx(q_want, rel=0.02)
    # the default μ sits at the bench's own optimum, which falls with the transmittance
    assert cv.best_mu(cv.build(p), p) == pytest.approx(p.mu, rel=0.1)
    far = cv.COWParams(distance=50e3)
    assert cv.best_mu(cv.build(far), far) < p.mu / 2


def test_the_lossless_model_is_the_papers():
    """pair_gains with all of the light in the window is Appendix A, but for the rule
    for several clicks, which matters little at small μ."""
    tau, p_d = 0.09, 1e-8
    ports = (cv.Port(tau / 2, tau / 2, tau / 2), cv.Port(tau / 2, tau / 2, -tau / 2))
    for mu in (1e-3, 1e-2):
        ours, q, e_b = cv.pair_gains(mu, tau, ports, p_d=p_d)
        theirs, q_p, e_p = bound.paper_model(mu, 2 * tau, 0.5, p_d)
        assert q == pytest.approx(q_p, rel=0.01)
        assert e_b == pytest.approx(e_p, rel=0.01)
        assert bound.phase_error(mu, ours) == pytest.approx(
            bound.phase_error(mu, theirs), rel=0.01
        )


# ---- what wrong settings do to the bound ---------------------------------------------
def expected(p=None, detune=0.0):
    p = p or cv.COWParams()
    setup = cv.build(p)
    setup["BOB-PS"].phase = setup["BOB-PS"].phase + detune
    return cv.theory(setup, p)


def test_settings_show_in_the_terms_of_the_bound():
    base = expected()
    # the DLI off its fringe: |α,α⟩ clicks reach D_M1
    off = expected(detune=math.radians(30))
    assert off["E_p^u"] > base["E_p^u"] + 0.05
    assert off["terms"]["M1"] > 5 * base["terms"]["M1"]
    # a broad laser: no phase shared between the pulses
    broad = expected(cv.COWParams(linewidth=10e6))
    assert broad["coherence"] < base["coherence"]
    assert broad["terms"]["M1"] > base["terms"]["M1"]
    none = expected(cv.COWParams(linewidth=10e9))
    assert none["coherence"] < 1e-20
    assert none["E_p^u"] > 2 * base["E_p^u"]
    # the modulator's leak: a tenth of its power takes far less than nine tenths of the
    # term away, since the leak adds to the pulses in amplitude
    clean = expected(cv.COWParams(extinction_db=40.0))
    assert base["terms"]["M0"] / 10 < clean["terms"]["M0"] < base["terms"]["M0"] / 2
    assert clean["E_p^u"] == pytest.approx(0.226, abs=0.002)
    # dark counts
    dark = expected(cv.COWParams(p_d=100.0 / 434e6))
    assert dark["terms"]["M1"] > 2 * base["terms"]["M1"]
    # too high a μ for the transmittance
    bright = expected(cv.COWParams(mu=5e-3))
    assert bright["terms"]["beta"] > 2 * base["terms"]["beta"]
    assert bright["E_p^u"] > 0.5  # bounds nothing useful
    far_too_bright = expected(cv.COWParams(mu=0.5))
    assert far_too_bright["E_p^u"] > 1


def test_a_detuned_dli_counts_as_expected():
    p = BRIGHT
    setup = cv.build(p)
    setup["BOB-PS"].phase = setup["BOB-PS"].phase + math.radians(40)
    res = cv.run(p, blocks=6, seed=9, setup=setup)
    assert res.expected["V"] == pytest.approx(math.cos(math.radians(40)) * 0.9928, 2e-3)
    assert_gains_match(res.tally, res.expected["gains"])


def test_no_shared_phase_counts_as_expected():
    """A very broad laser: the pair's pulses no longer interfere, as with a
    gain-switched source, and the data line does not change."""
    p = cv.COWParams(mu=0.5, decoy1=0.25, decoy2=0.25, linewidth=10e9)
    res = cv.run(p, blocks=6, seed=10)
    assert res.expected["V"] == pytest.approx(0.0, abs=1e-9)
    assert abs(res.tally.visibility) < 0.05
    assert_gains_match(res.tally, res.expected["gains"])
    assert res.e_b == pytest.approx(res.expected["E_b"], abs=1e-3)


# ---- the counting error --------------------------------------------------------------
def test_counting_error_follows_the_spread():
    p = cv.COWParams()
    th = cv.theory(cv.build(p), p)
    rng = np.random.default_rng(3)
    share = dict(zip(cv.STATES, p.probabilities))
    for blocks, spread in ((100, 0.076), (1000, 0.031), (5000, 0.012)):
        sent = {s: int(200_000 * blocks * share[s]) for s in cv.STATES}
        readings, errors = [], []
        for _ in range(300):
            m = np.zeros((4, 3), dtype=np.int64)
            for s, g in zip(cv.STATES, th["gains"]):
                m[s] = [
                    rng.poisson(sent[s] * g[0]),
                    rng.poisson(sent[s] * g[1]),
                    sent[s],
                ]
            readings.append(cv.phase_error(p.mu, cv.gains_of(m)))
            errors.append(cv.counting_error(m, p.mu))
        # the spread the plan quoted, and an error bar that is not smaller than it
        assert np.std(readings) == pytest.approx(spread, rel=0.25)
        assert np.median(errors) == pytest.approx(np.std(readings), rel=1.0)
        assert np.median(errors) > 0.8 * np.std(readings)
    # no counts at all on the rare gains: still an error bar, not a clean reading
    m = np.array(
        [[100, 90, 80_000], [100, 90, 80_000], [400, 0, 20_000], [0, 0, 20_000]]
    )
    assert cv.counting_error(m, 1e-3) > 0.01


def test_no_monitoring_clicks_read_nan():
    t = cv.Tally()
    assert math.isnan(t.phase_error(1e-3)) and math.isnan(t.counting_error(1e-3))
    assert math.isnan(t.e_b) and math.isnan(t.q) and math.isnan(t.visibility)
    assert math.isnan(cv.readouts(t, cv.COWParams())["E_p^u"])


# ---- the two parties -----------------------------------------------------------------
def test_parties_sift_the_same_key():
    setup = cv.build(slots=400_000)
    alice, bob = Experiment(setup, cv.PARTIES, seed=8).run(blocks=4, echo=False)
    assert [w.name for w in (alice, bob)] == ["ALICE", "BOB"] and bob.batches == 4
    for w in (alice, bob):
        assert not any("Traceback" in line for line in w.lines)
        assert w.result["pairs sent"] == 800_000
        assert 20 < w.result["sifted bits"] < 120
        assert w.result["E_p^u ±"] > 0
    trim = lambda r: {
        k: v for k, v in r.items() if k not in ("sent [kB]", "received [kB]")
    }  # noqa: E731
    assert trim(alice.result) == pytest.approx(trim(bob.result), nan_ok=True)
    # detect + errors per block; sift
    assert bob.net["sent"] == 8 and alice.net["sent"] == 4
