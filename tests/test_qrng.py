"""Vacuum-noise QRNG (examples/vacuum_noise_qrng.py) and the oscilloscope's ADC: the min-entropy bounds against
the papers' own numbers (Park et al. 2025, Eq. 8; Gehring et al. 2021, Table 1 and Eq. 1), the
bounds' limits, and the bench: shot noise linear in LO power, the quantum-to-classical noise ratio
from shot noise against the amplifier's NEP, correlations against sample rate, the vacuum's share of
each sample by ablating the shot-noise draw, the vacuum input left open or capped, and
extraction."""

import itertools
import math
from pathlib import Path
from runpy import run_path

import numpy as np
import pytest
from scipy.special import erf

from phoq import FunctionGenerator, Oscilloscope, Setup
from phoq.engine import report, trace
from phoq.units import Q_E

EX = run_path(str(Path(__file__).resolve().parents[1] / "examples" / "vacuum_noise_qrng.py"))


def test_scope_adc_quantizes_across_the_screen():
    """2^bits codes over the 8 divisions about the offset; beyond them, the end codes."""
    s = Setup("adc")
    fg = s.add(FunctionGenerator("FG", ch1_shape="sine", ch1_freq=1e6, ch1_vpp=1.0))
    osc = s.add(Oscilloscope("OSC", timebase=200e-9, ch1_scale=0.1, ch1_offset=0.0, adc_bits=4, trigger="free"))
    s.connect(fg.ch1, osc.ch1)
    r = osc.acquire(trace(s), np.random.default_rng(1))
    codes, v = r.codes("ch1"), r["ch1"].data
    assert set(np.unique(codes)) == set(range(16))
    assert np.allclose(np.unique(v), -0.4 + np.arange(16) * 0.8 / 15)  # ±4 div at 0.1 V/div, clipped
    osc.adc_bits = 0
    with pytest.raises(ValueError, match="no ADC resolution"):
        osc.acquire(trace(s), np.random.default_rng(1)).codes("ch1")


def test_park_2025_eq8_classical_side_information():
    """σ_Q = 4212 and σ_E = 2107 codes of a 16-bit ADC: 13.37 bits per sample."""
    assert EX["h_classical"](4212.0, 2107.0, 16) == pytest.approx(13.37, abs=0.005)


def test_gehring_2021_table1_quantum_side_information():
    """σ² = 3.96, σ_X² = 3.29, σ_U² = 2.49 (×10⁷ codes², 16 bits): the ideal-ADC min-entropy at the
    worst corner of the confidence intervals is the paper's 10.74 bits."""
    def h(s2, sx2, su2):
        return EX["h_quantum_optimal_range"](0.5 * s2 / (sx2 - su2) - 0.5, 16)

    assert h(3.96e7, 3.29e7, 2.49e7) == pytest.approx(11.07, abs=0.01)
    corners = itertools.product((3.87e7, 4.05e7), (3.22e7, 3.36e7), (2.43e7, 2.55e7))
    assert min(h(*c) for c in corners) == pytest.approx(10.74, abs=0.05)
    assert EX["secure_length"](352, 3.51, 1e-32) == pytest.approx(1024, abs=2)  # Eq. (1): 5632 bits → 1024


@pytest.mark.parametrize("g", [5.0, 20.0])
def test_quantum_bound_without_excess_noise_is_the_vacuum_centre_bin(g):
    """n = 0 (a pure vacuum): nothing to correlate with, so the bound is the classical one."""
    assert EX["h_quantum"](g, 0.0, 12) == pytest.approx(-math.log2(erf(1 / (2 * math.sqrt(2) * g))), rel=1e-9)
    assert EX["h_quantum"](g, 0.2, 12) < EX["h_quantum"](g, 0.0, 12)  # excess noise only costs


def test_bench_shot_noise_qcnr_and_correlations():
    bench = EX["build"]()
    assert abs(report(bench)["detectors"]["BPD-1"]["rf"]) < 1e-6  # the coupler trims the currents equal
    scale = EX["fit_range"](bench)
    dark = EX["variance"](EX["record"](EX["build"](laser_on=False, scale=scale), sweeps=4))
    excess = []
    for p in (-2.0, 1.0, 4.0):
        s = EX["build"](power_dbm=p, scale=scale)
        cur = sum(s["BPD-1"].mean_currents(*(trace(s).mean_powers(s["BPD-1"], k) for k in ("inp_plus", "inp_minus"))))
        excess.append((EX["variance"](EX["record"](s, sweeps=4)) - dark) / cur)
    assert np.std(excess) / np.mean(excess) < 0.02  # σ² − σ_E² ∝ I: shot noise
    bpd = bench["BPD-1"]
    cur = sum(bpd.mean_currents(*(trace(bench).mean_powers(bpd, k) for k in ("inp_plus", "inp_minus"))))
    want = 10 * math.log10(2 * Q_E * cur / (bpd.setting()[1] * bpd.peak_responsivity()) ** 2)
    on = EX["record"](EX["build"](scale=scale), sweeps=4)
    off = EX["record"](EX["build"](scale=scale, laser_on=False), sweeps=4)
    assert EX["entropy"](on, off, 8)["qcnr_db"] == pytest.approx(want, abs=0.3)  # shot over NEP, one pole
    slow, fast = (EX["record"](EX["build"](sample_rate=fs, scale=scale), sweeps=4) for fs in (50e6, 200e6))
    a_slow, a_fast = EX["autocorrelation"](slow)[0], EX["autocorrelation"](fast)[0]
    assert abs(a_slow) < 0.03 and a_fast > 0.2
    ratio = EX["conditional_variance"](fast) / EX["variance"](fast)
    assert ratio == pytest.approx(1 - a_fast**2, abs=0.03)  # an AR(1)-like process: what the past predicts


def _volts(setup, sweeps, monkeypatch, shot=True):
    """The scope's unquantized samples over ``sweeps`` records (``record``'s streams). ``shot=False``
    zeroes the BPD's shot-noise draw but still makes it, so every other random number (trigger, RIN,
    TIA noise) is the same: what is left is each sample's classical part."""
    osc = setup["OSC-1"]
    bits, osc.adc_bits = osc.adc_bits, 0
    try:
        with monkeypatch.context() as m:
            if not shot:
                m.setattr("phoq.detectors.Q_E", 0.0)
            return np.concatenate([osc.acquire(trace(setup), np.random.default_rng((1, k)))["ch1"].data
                                   for k in range(sweeps)])
    finally:
        osc.adc_bits = bits


@pytest.mark.parametrize("balanced, rin", [(True, -140.0), (False, -120.0)])
def test_vacuum_share_by_ablation_against_the_lo_off_method(balanced, rin, monkeypatch):
    """v − e is the shot noise alone (the detector is linear before the ADC), and its share of the
    variance is 2q(I₊ + I₋) over the total, with the amplifier's (NEP·ℜ)² and the leaked RIN·(I₊ − I₋)².
    The lab's estimate, LO on minus LO off, agrees on the balanced bench. On a 50:50 coupler with a
    noisy LO it counts the leaked RIN as quantum: that noise exists only while the LO is on."""
    scale = EX["fit_range"](EX["build"]())
    s = EX["build"](balanced=balanced, scale=scale)
    s["LD-LO"].rin_db_hz = rin
    bpd = s["BPD-1"]
    ip, im = bpd.mean_currents(*(trace(s).mean_powers(bpd, k) for k in ("inp_plus", "inp_minus")))
    shot = 2 * Q_E * (ip + im)
    want = shot / (shot + (bpd.setting()[1] * bpd.peak_responsivity()) ** 2 + 10 ** (rin / 10) * (ip - im) ** 2)
    v, e = (_volts(s, 4, monkeypatch, shot=on) for on in (True, False))
    dark = _volts(EX["build"](balanced=balanced, scale=scale, laser_on=False), 4, monkeypatch)
    truth, lab = (v - e).var() / v.var(), (v.var() - dark.var()) / v.var()
    assert truth == pytest.approx(want, abs=0.02)
    assert abs(np.corrcoef(v - e, e)[0, 1]) < 0.02
    if balanced:
        assert lab == pytest.approx(truth, abs=0.01)
    else:
        assert truth < 0.4 and lab - truth > 0.4


def test_eve_with_classical_side_information_guesses_the_centre_code(monkeypatch):
    """Eve knows each sample's classical part e and bets on the code that contains it, the centre of
    a Gaussian of σ_Q codes. Her hit rate is the guessing probability erf(δ/2√2σ_Q): the min-entropy,
    measured."""
    s = EX["build"]()
    EX["fit_range"](s)
    osc = s["OSC-1"]
    top = 2**osc.adc_bits - 1
    v, e = (_volts(s, 10, monkeypatch, shot=on) for on in (True, False))
    code_v, code_e = (np.clip(np.rint((x / osc.ch1_scale + 4) / 8 * top), 0, top) for x in (v, e))
    sigma_q = (v - e).std() / osc.ch1_scale / 8 * top
    h = -math.log2(np.mean(code_v == code_e))
    assert h == pytest.approx(-math.log2(erf(1 / (2 * math.sqrt(2) * sigma_q))), abs=0.08)


def test_an_open_or_wrongly_capped_vacuum_port_is_classical_noise(monkeypatch):
    """Left open (a flat FC/UPC end), or capped with an angled cap on a flat port, the vacuum input
    sends LO light reflected off the detector's inputs back in. It beats with the LO: RF OUTPUT
    leaves the ADC's range, and the added noise is classical (the ablation leaves the shot-noise
    part as it was). The report says why; the matching FC/APC cap removes all of it."""
    scale = EX["fit_range"](EX["build"]())
    rows = {r["port"]: r for r in EX["vacuum_port_check"](scale)}
    capped, opened, wrong = rows["capped"], rows["open"], rows["apc-on-upc"]
    assert abs(capped["dc"]) < 0.01 * capped["screen"] and not capped["warnings"]
    assert capped["ends"] < 1e-3  # the Gaussian's own tails beyond ±4σ: 6·10⁻⁵
    for r in (opened, wrong):
        assert abs(r["dc"]) > r["screen"] and r["ends"] > 0.99 and r["excess"] > 0.2
    assert any(w.startswith("Open connector BS-1.a2 reflects light on to BPD-1") for w in opened["warnings"])
    assert any("air gap" in w for w in wrong["warnings"])
    v_c, e_c = (_volts(EX["build"](scale=scale), 4, monkeypatch, shot=on) for on in (True, False))
    v_o, e_o = (_volts(EX["build"](scale=scale, vacuum_port="open"), 4, monkeypatch, shot=on) for on in (True, False))
    assert (v_o - e_o).var() == pytest.approx((v_c - e_c).var(), rel=0.03)
    assert e_o.var() - e_c.var() == pytest.approx(v_o.var() - v_c.var(), rel=0.1)


def test_unbalanced_detector_leaves_the_adc_range():
    s = EX["build"](balanced=False)
    codes = np.concatenate(EX["record"](s, sweeps=1))
    assert report(s)["detectors"]["BPD-1"]["rf"] > 0.2 and np.mean((codes == 0) | (codes == 255)) > 0.99


def test_toeplitz_extraction_length_and_balance():
    bench = EX["build"]()
    scale = EX["fit_range"](bench)
    on = EX["record"](EX["build"](scale=scale), sweeps=6, seed=101)
    out = EX["extract"](on, 8, 5.5)
    blocks = sum(r.size for r in on) // 512
    assert out.size == blocks * EX["secure_length"](512, 5.5)
    assert abs(out.mean() - 0.5) < 0.003
    tests = EX["bit_tests"](out)
    assert min(tests["frequency p"], tests["runs p"], tests["byte chi2 p"]) > 0.01
