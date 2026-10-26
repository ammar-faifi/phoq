"""A quantum random number generator from vacuum fluctuations, digitized by the
oscilloscope.

    # noise check, correlations, min-entropy, extraction
    uv run python examples/vacuum_noise_qrng.py
    # PSDs, autocorrelation, histogram
    uv run python examples/vacuum_noise_qrng.py --plot qrng.png
    # the same with other noise
    uv run python examples/vacuum_noise_qrng.py --seed 2
    # the bench in 3D
    uv run phoq serve examples/vacuum_noise_qrng.py

    laser (LO, 4 dBm) → BS-1 ─┬─ 1 m → INPUT+  BPD-1 (PDB450C, 10⁴ V/A, 45 MHz)
    FC/APC cap: vacuum ───────┘─ 1 m → INPUT− RF OUTPUT → OSC-1 CH1, the ADC (8 bits)

The laser is the local oscillator of a balanced homodyne detector. Nothing enters the
coupler's second input: only the vacuum does. That input is capped with an angled
terminator: left open, its flat end would send back LO light reflected off the
detector's inputs, a delayed copy that turns the laser's phase noise into classical
noise and moves RF OUTPUT off the ADC's range (step 6). The difference current keeps the
vacuum's quadrature fluctuations, amplified by the LO, which is the shot noise
2q(I₊ + I₋) of examples/laser_noise.py. The laser's own noise reaches both diodes alike
and cancels. The scope samples RF OUTPUT with 8 bits across its screen, and every sample
is Gaussian (Gehring et al. 2021, Eq. 3):

    σ² = σ_Q² + σ_E²   (vacuum noise, plus classical excess noise: the amplifier,
                        any leaked laser noise)

Only σ_Q is unpredictable. The extractable randomness per sample is a min-entropy
conditioned on everything else, the side information:

- Classical (Park et al. 2025, Eqs. 7–8; Haw et al. 2015): an adversary who knows each
  sample's classical part e can at best guess the bin of a Gaussian of width σ_Q
  centered on e, so H = −log₂ max(c₁, c₂), with c₂ = erf(δ/(2√2 σ_Q)) for a bin of width
  δ.
- Quantum (Gehring et al. 2021, Eq. 67): an adversary holding a purification of the
  noise. The noise is modeled as a thermal state of n photons in a vacuum of variance
  g², so σ² = g²(1 + 2n) (Eq. 3). Then H ≥ −log₂[(n + δ)(1 + n + δ)/δ ·
  max{erf(Δx/2g′), ½erfc(R/g′)}], g′ = g·√[(4n(n + 1 + δ) + 2δ)/δ], maximized over δ.
  For n = 0 it is the classical value.
- Correlated samples (Gehring Eqs. 14, 22, 24): the detector's bandwidth correlates
  neighboring samples. What is new in each sample is its variance conditioned on the
  past, σ_X² = exp⟨ln f(λ)⟩, from the geometric mean of the PSD (Kolmogorov–Szegő);
  likewise σ_U² for the excess noise alone. Then g² = σ_X² − σ_U² and n = ½σ²/g² − ½ go
  into the bound.

A Toeplitz hash then compresses N samples to ℓ = N·H − log₂(1/2ε²) bits (the leftover
hash lemma, Gehring Eq. 1). phoq's "quantum" noise comes from numpy's pseudo-random
generator, so the bits are not random in fact: this models the QRNG's design trade-offs
(LO power, balance, bandwidth against sample rate, ADC resolution and range), not its
randomness.
"""

import argparse
import itertools
import math

import numpy as np
from scipy.optimize import brentq, minimize_scalar
from scipy.special import erf, erfc

from phoq import BalancedPhotodetector, Coupler, Laser, Oscilloscope, Setup
from phoq.engine import report, trace
from phoq.units import Q_E, fmt_si

# V/A: 45 MHz, an amplifier noise of 2.2·10⁻²² A²/Hz, ~4 dB under the shot noise of 1.8
# mA
GAIN = 1e4
RIN = -140.0  # dBc/Hz: the LO's own intensity noise, which balancing has to remove
# The vacuum input: (BS-1's connectors, the cap on it or None for an open port)
VACUUM_PORTS = {
    "capped": ("FC/APC", "FC/APC"),
    "open": ("FC/UPC", None),
    "apc-on-upc": ("FC/UPC", "FC/APC"),
}
POINTS = 20_000  # samples per sweep
SWEEPS = 10
RANGE_SIGMA = 4.0  # the ADC's half range in standard deviations of the signal
EPS_HASH = 2.0**-64  # the extractor's distance from uniform


# ---- the bench -----------------------------------------------------------------------
def build(
    power_dbm: float = 4.0,
    sample_rate: float = 100e6,
    bits: int = 8,
    balanced: bool = True,
    laser_on: bool = True,
    scale: float = 0.6e-3,
    vacuum_port: str = "capped",
) -> Setup:
    """LO → coupler (second input: the vacuum) → balanced detector → scope as the ADC.
    ``balanced`` trims the coupler so the two photocurrents match; otherwise it is
    50:50. ``vacuum_port`` is one of VACUUM_PORTS: FC/APC capped with FC/APC, a flat
    FC/UPC port left open, or an FC/APC cap on it.
    """
    setup = Setup("Vacuum-noise QRNG", seed=2)
    lo = setup.add(
        Laser("LD-LO", mode="cw", power_dbm=power_dbm, rin_db_hz=RIN, enabled=laser_on)
    )
    bpd = setup.add(
        BalancedPhotodetector("BPD-1", preset="thorlabs/pdb450c", gain=GAIN)
    )
    short = 10 ** (-bpd.cmrr_db / 20)  # INPUT−'s responsivity is short by this much
    connector, cap = VACUUM_PORTS[vacuum_port]
    bs = setup.add(
        Coupler(
            "BS-1",
            ratio=(1 - short) / (2 - short) if balanced else 0.5,
            connector=connector,
        )
    )
    window = POINTS / sample_rate
    osc = setup.add(
        Oscilloscope(
            "OSC-1",
            timebase=window / 10,
            sample_rate=sample_rate,
            max_points=POINTS,
            bandwidth=200e6,
            adc_bits=bits,
            trigger="free",
            ch1_scale=scale,
            ch1_offset=0.0,
        )
    )
    setup.connect(lo.out, bs.a1)
    if cap is not None:  # bs.a2 is the vacuum input
        setup.terminate(bs.a2, cap)
    setup.connect(bs.b1, bpd.inp_plus, length=1.0)
    setup.connect(bs.b2, bpd.inp_minus, length=1.0)
    setup.connect(bpd.rf, osc.ch1)
    return setup


Seed = int | tuple[int, ...]


def _rng(seed: Seed, k: int) -> np.random.Generator:
    """Sweep k of a record: the stream (seed…, k) of numpy's SeedSequence, so two
    records with different seeds never share a draw."""
    return np.random.default_rng((*(seed if isinstance(seed, tuple) else (seed,)), k))


def streams(seed: Seed = 1):
    """Seeds for successive records, (seed, 0), (seed, 1), …: give every measurement its
    own, as every acquisition on a bench sees fresh noise."""
    base = seed if isinstance(seed, tuple) else (seed,)
    return ((*base, j) for j in itertools.count())


def record(setup: Setup, sweeps: int = SWEEPS, seed: Seed = 1) -> list[np.ndarray]:
    """ADC codes, one array per sweep (each sweep is an independent record)."""
    net, osc = trace(setup), setup["OSC-1"]
    return [
        osc.acquire(net, _rng(seed, k)).codes("ch1").astype(float)
        for k in range(sweeps)
    ]


def fit_range(setup: Setup, k: float = RANGE_SIGMA, seed: Seed = 0) -> float:
    """Set CH1's scale so that the screen's ±4 divisions span ±k standard deviations, as
    one sets a QRNG's ADC range (Haw et al. 2015). Returns the scale [V/div]."""
    osc = setup["OSC-1"]
    bits = osc.adc_bits
    osc.adc_bits = 0
    try:
        v = osc.acquire(trace(setup), _rng(seed, 0))["ch1"].data
    finally:
        osc.adc_bits = bits
    osc.ch1_scale = k * float(v.std()) / 4
    return osc.ch1_scale


def ideal(setup: Setup, sweeps: int = SWEEPS, seed: Seed = 1) -> list[np.ndarray]:
    """The scope's samples before its ADC [V], one array per sweep
    (the streams of ``record``)."""
    net, osc = trace(setup), setup["OSC-1"]
    bits, osc.adc_bits = osc.adc_bits, 0
    try:
        return [osc.acquire(net, _rng(seed, k))["ch1"].data for k in range(sweeps)]
    finally:
        osc.adc_bits = bits


def vacuum_port_check(scale: float, sweeps: int = 4, seed: Seed = 1) -> list[dict]:
    """Each VACUUM_PORTS variant against the capped one: the report's warnings, RF
    OUTPUT's DC level on the screen, the share of samples at an end code, and the added
    noise (with the DC removed) as a fraction of the shot noise."""
    seeds = streams(seed)
    dark = variance(ideal(build(scale=scale, laser_on=False), sweeps, next(seeds)))
    shot = None
    rows = []
    for port in VACUUM_PORTS:
        s = build(scale=scale, vacuum_port=port)
        v = ideal(s, sweeps, next(seeds))
        codes = np.concatenate(record(s, sweeps, next(seeds)))
        if (
            shot
            is None
            # the capped port comes first: its noise above the dark noise is the shot
            # noise
        ):
            shot = variance(v) - dark
        rows.append(
            {
                "port": port,
                "warnings": [w["text"] for w in report(s)["warnings"]],
                "dc": float(np.mean(np.concatenate(v))),
                "screen": 4 * scale,
                "ends": float(
                    np.mean((codes == 0) | (codes == 2 ** s["OSC-1"].adc_bits - 1))
                ),
                "excess": (variance(v) - dark - shot) / shot,
            }
        )
    return rows


# ---- statistics of the samples -------------------------------------------------------
def variance(runs: list[np.ndarray]) -> float:
    return float(np.mean([r.var() for r in runs]))


def conditional_variance(runs: list[np.ndarray], segment: int = 2000) -> float:
    """σ² conditioned on the past: exp of the mean log PSD (Kolmogorov–Szegő; Gehring
    Eq. 14). The PSD is the periodogram averaged over segments, normalized so that its
    mean is the variance.
    """
    segs = [
        r[i : i + segment] - r[i : i + segment].mean()
        for r in runs
        for i in range(0, r.size - segment + 1, segment)
    ]
    psd = np.mean([np.abs(np.fft.rfft(s)) ** 2 / segment for s in segs], axis=0)[
        1:-1
    ]  # no DC, no Nyquist
    k = len(segs)
    return float(
        np.exp(np.mean(np.log(psd)) + 1 / (2 * k))
    )  # E ln(mean of K) = ln σ² − 1/2K: undo the bias


def autocorrelation(runs: list[np.ndarray], lags: int = 20) -> np.ndarray:
    """Normalized autocorrelation at lags 1 … ``lags``, averaged over the sweeps."""
    out = []
    for r in runs:
        x = r - r.mean()
        out.append([np.dot(x[:-k], x[k:]) / np.dot(x, x) for k in range(1, lags + 1)])
    return np.mean(out, axis=0)


# ---- min-entropy ---------------------------------------------------------------------
def h_classical(sigma_q: float, sigma_e: float, bits: int, delta: float = 1.0) -> float:
    """Against classical side information (Park et al. 2025, Eq. 7): the adversary knows
    the classical part e of each sample (|e| ≤ e_max = 5σ_E) and guesses its bin: the
    center bin of the quantum Gaussian (c₂) or, pushed to the edge of the range, an end
    bin (c₁). R: half the full scale.
    """
    r = 2**bits / 2 * delta
    c1 = 0.5 * (erf((5 * sigma_e - r + 1.5 * delta) / (math.sqrt(2) * sigma_q)) + 1)
    c2 = erf(delta / (2 * math.sqrt(2) * sigma_q))
    return -math.log2(max(c1, c2))


def h_quantum(g: float, n: float, bits: int, dx: float = 1.0) -> float:
    """Against quantum side information, i.i.d. (Gehring et al. 2021, Eq. 67, maximized
    over δ): a vacuum of variance g² with n thermal photons of excess noise, an ADC of
    2^bits bins of width Δx, the two end bins collecting everything beyond ±R, R =
    (2^bits − 2)Δx/2 (Eqs. 49–51).
    """
    r = (2**bits - 2) * dx / 2

    def bound(log_d: float) -> float:
        d = math.exp(log_d)
        gp = g * math.sqrt((4 * n * (n + 1 + d) + 2 * d) / d)
        return math.log2(
            (n + d) * (1 + n + d) / d * max(erf(dx / (2 * gp)), 0.5 * erfc(r / gp))
        )

    if n <= 0:  # pure vacuum: the δ → 0 limit, the classical bound with σ_Q = g
        gp = g * math.sqrt(2)
        return -math.log2(max(erf(dx / (2 * gp)), 0.5 * erfc(r / gp)))
    best = minimize_scalar(bound, bounds=(-30.0, 30.0), method="bounded")
    return -float(best.fun)


def h_quantum_optimal_range(n: float, bits: int) -> float:
    """Gehring Eqs. (9)–(11): the same bound with the ADC range chosen best, which
    leaves only n and the number of bins: −log₂[(√n + √(n+1))² erf(Δx/2g′*)],
    erf(Δx/2g′*) = ½erfc(R/g′*).
    """
    d = 2**bits
    u = brentq(
        lambda u: erf(u / (d - 2)) - 0.5 * erfc(u), 1e-6, 40.0
    )  # u = R/g′*, Δx/2g′* = u/(d − 2)
    return -math.log2((math.sqrt(n) + math.sqrt(n + 1)) ** 2) - math.log2(
        erf(u / (d - 2))
    )


def entropy(on: list[np.ndarray], dark: list[np.ndarray], bits: int) -> dict:
    """Everything from two records, the LO on and the LO off (the excess noise alone, as
    Park et al. measure it), in ADC codes."""
    s2, s2_dark = variance(on), variance(dark)
    sx2, su2 = conditional_variance(on), conditional_variance(dark)
    g2 = sx2 - su2  # the conditional vacuum variance (Eq. 22)
    n = (
        0.5 * s2 / g2 - 0.5
    )  # the excess noise as thermal photons, with the past's share (Eq. 24)
    sigma_q = math.sqrt(s2 - s2_dark)
    return {
        "sigma2": s2,
        "sigma_e2": s2_dark,
        "sigma_x2": sx2,
        "sigma_u2": su2,
        "g2": g2,
        "n": n,
        "qcnr_db": 10 * math.log10((s2 - s2_dark) / s2_dark),
        "cond_qcnr_db": 10 * math.log10(g2 / su2),
        "correlation": sx2 / s2,
        "h_classical": h_classical(sigma_q, math.sqrt(s2_dark), bits),
        "h_quantum": h_quantum(math.sqrt(g2), n, bits),
        "h_optimal": h_quantum_optimal_range(n, bits),
    }


# ---- extraction ----------------------------------------------------------------------
def secure_length(samples: int, h: float, eps: float = EPS_HASH) -> int:
    """The leftover hash lemma against quantum side information (Gehring Eq. 1)."""
    return max(0, math.floor(samples * h - math.log2(1 / (2 * eps**2))))


def toeplitz(bits_in: np.ndarray, m: int, seed: Seed = 7) -> np.ndarray:
    """m output bits: a random m × n Toeplitz matrix (m + n − 1 seed bits) times the
    input, mod 2."""
    n = bits_in.size
    t = np.random.default_rng(seed).integers(0, 2, m + n - 1)  # the extractor's seed
    return (
        np.convolve(t, bits_in[::-1].astype(np.int64))[n - 1 : n - 1 + m] % 2
    ).astype(np.uint8)


def extract(
    runs: list[np.ndarray], bits: int, h: float, block: int = 512, seed: Seed = 7
) -> np.ndarray:
    """Hash the codes in blocks of ``block`` samples, each to its secure length, block i
    with the Toeplitz matrix of seed (seed…, i)."""
    base = seed if isinstance(seed, tuple) else (seed,)
    codes = np.concatenate(runs).astype(np.int64)
    m = secure_length(block, h)
    out = []
    for i in range(0, codes.size - block + 1, block):
        raw = ((codes[i : i + block, None] >> np.arange(bits)[::-1]) & 1).ravel()
        out.append(toeplitz(raw, m, seed=(*base, i)))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.uint8)


def bit_tests(b: np.ndarray) -> dict:
    """A few quick checks of the output (NIST SP 800-22 frequency and runs tests, and
    byte χ²)."""
    from scipy.stats import chi2

    n = b.size
    ones = float(b.mean())
    freq_p = float(erfc(abs(2 * int(b.sum()) - n) / math.sqrt(2 * n)))
    runs = 1 + int(np.count_nonzero(b[1:] != b[:-1]))
    runs_p = float(
        erfc(
            abs(runs - 2 * n * ones * (1 - ones))
            / (2 * math.sqrt(2 * n) * ones * (1 - ones))
        )
    )
    by = np.packbits(b[: n // 8 * 8])
    counts = np.bincount(by, minlength=256)
    stat = float(((counts - by.size / 256) ** 2 / (by.size / 256)).sum())
    return {
        "bits": n,
        "ones": ones,
        "frequency p": freq_p,
        "runs p": runs_p,
        "byte chi2 p": float(chi2.sf(stat, 255)),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--plot",
        metavar="PNG",
        help="save the PSDs, the autocorrelation and the histogram",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=1,
        help="base seed: every record draws its own streams from it",
    )
    args = ap.parse_args()
    seeds = streams(args.seed)

    bench = build()
    entry = report(bench)["detectors"]["BPD-1"]
    print(
        f"LO 4 dBm, RIN {RIN:g} dBc/Hz → BS-1 at {bench['BS-1'].ratio:.4f} (balanced) →"
        f" {bench['BPD-1'].preset} at {fmt_si(GAIN, 'V/A')} → OSC-1, 8-bit ADC over"
        " ±4 div"
    )
    print(
        f"  {entry['readouts']['INPUT+']} and {entry['readouts']['INPUT−']}; RF OUTPUT"
        f" {entry['readouts']['RF OUTPUT']}\n"
    )

    # 1. the quantum signal: noise against LO power (Park §3.1)
    scale = fit_range(bench, seed=next(seeds))
    dark = record(build(laser_on=False, scale=scale), seed=next(seeds))
    print(
        f"1. Shot noise against LO power (ADC at {fmt_si(scale, 'V')}/div; dark noise"
        f" σ_E² = {variance(dark):.1f} codes²)"
    )
    print(
        f"   {'LO':>6} {'I₊ + I₋':>9} {'σ²':>7} {'σ² − σ_E²':>9} {'per mA':>8}"
        f" {'QCNR':>7}"
    )
    rows = []
    for p in (-2.0, 0.0, 2.0, 4.0):
        s = build(power_dbm=p, scale=scale)
        cur = sum(
            s["BPD-1"].mean_currents(
                *(
                    trace(s).mean_powers(s["BPD-1"], k)
                    for k in ("inp_plus", "inp_minus")
                )
            )
        )
        v = variance(record(s, seed=next(seeds)))
        q = v - variance(dark)
        rows.append((cur, q))
        print(
            f"   {p:4.0f} dBm {fmt_si(cur, 'A'):>9} {v:7.1f} {q:9.1f}"
            f" {q / cur / 1e3:8.1f} {10 * math.log10(q / variance(dark)):+6.1f} dB"
        )
    cur, q = map(np.array, zip(*rows))
    slope = float(np.dot(cur, q) / np.dot(cur, cur))
    r2 = 1 - float(np.sum((q - slope * cur) ** 2) / np.sum((q - q.mean()) ** 2))
    print(
        f"   σ² − σ_E² ∝ I: through the origin, R² = {r2:.4f} (Park et al.: 0.9957)\n"
    )

    # 2. sample rate against correlations (Park §3.3; Gehring's non-i.i.d. bound)
    print(
        "2. Sample rate against the detector's 45 MHz: correlations and min-entropy"
        " (quantum side info, 8 bits)"
    )
    print(
        f"   {'rate':>9} {'acf(1)':>7} {'⟨|acf|⟩ 1–20':>12} {'σ_X²/σ²':>8}"
        f" {'H per sample':>12} {'rate × H':>10}"
    )
    results = {}
    for fs in (20e6, 50e6, 100e6, 200e6):
        on = record(build(sample_rate=fs, scale=scale), seed=next(seeds))
        off = record(
            build(sample_rate=fs, scale=scale, laser_on=False), seed=next(seeds)
        )
        e = results[fs] = entropy(on, off, 8)
        a = autocorrelation(on)
        print(
            f"   {fmt_si(fs, 'S/s'):>9} {a[0]:+7.3f} {np.mean(np.abs(a)):12.4f}"
            f" {e['correlation']:8.3f} {e['h_quantum']:9.2f} bit"
            f" {fmt_si(fs * e['h_quantum'], 'b/s'):>10}"
        )
    print(
        "   (Sampling faster than the bandwidth still adds bits per second, but each"
        " sample carries fewer.)\n"
    )

    # 3. min-entropy at 100 MS/s against the ADC's range and resolution
    print(
        "3. Min-entropy per sample at 100 MS/s, against the ADC's range (±R) and"
        " resolution"
    )
    print(
        f"   {'ADC':>6} {'±R':>5} {'QCNR':>7} {'n':>6} {'classical side info':>19}"
        f" {'quantum side info':>17} {'quantum, best range':>19}"
    )
    best = None
    for bits, k in (
        (8, 3.0),
        (8, 4.0),
        (8, 6.0),
        (8, 8.0),
        (12, 4.0),
        (12, 8.0),
        (16, 4.0),
        (16, 8.0),
    ):
        sc = scale * k / RANGE_SIGMA
        on = record(build(bits=bits, scale=sc), seed=next(seeds))
        off = record(build(bits=bits, scale=sc, laser_on=False), seed=next(seeds))
        e = entropy(on, off, bits)
        if (bits, k) == (8, RANGE_SIGMA):
            best = (on, e)
        print(
            f"   {bits:3d} bit {k:3.0f}σ {e['qcnr_db']:+6.1f} dB {e['n']:6.3f}"
            f" {e['h_classical']:16.2f} bit {e['h_quantum']:14.2f} bit"
            f" {e['h_optimal']:16.2f} bit"
        )
    print(
        "   (Classical: the adversary may push a sample up to 5σ_E toward an end of the"
        " range, so a narrow range\n    costs; quantum: Gaussian excess noise held by"
        " an adversary, best near ±4σ for 8 bits.)\n"
    )

    # 4. what happens unbalanced: a 50:50 coupler (Park §3.2)
    s = build(balanced=False, scale=scale)
    entry = report(s)["detectors"]["BPD-1"]
    codes = np.concatenate(record(s, seed=next(seeds)))
    print(
        f"4. Unbalanced (BS-1 at 0.5000): RF OUTPUT {entry['readouts']['RF OUTPUT']};"
        f" the screen spans ±{fmt_si(4 * scale, 'V')}, so"
        f" {np.mean((codes == 0) | (codes == 255)) * 100:.0f} % of the samples read an"
        " end code\n"
    )

    # 5. extraction
    on, e = best
    h = e["h_quantum"]
    m = secure_length(512, h)
    out = extract(on, 8, h, seed=next(seeds))
    t = bit_tests(out)
    print(
        "5. Toeplitz extraction at 100 MS/s, 8 bits: blocks of 512 samples (4096 bits)"
        f" → {m} bits (ℓ = 512·{h:.2f} − log₂(1/2ε²), ε = 2⁻⁶⁴);"
        f" {fmt_si(100e6 * m / 512, 'b/s')}"
    )
    print(
        f"   {t['bits']:,} bits out: ones {t['ones']:.4f}, frequency p ="
        f" {t['frequency p']:.2f}, runs p = {t['runs p']:.2f}, byte χ² p ="
        f" {t['byte chi2 p']:.2f}"
    )
    print(
        "   (NIST SP 800-22 passes a test at p ≥ 0.01. phoq's noise is pseudo-random:"
        " these tests check the\n    pipeline, not quantum randomness.)\n"
    )

    # 6. the vacuum input: capped, left open, or capped with the wrong polish
    print(
        "6. The vacuum input (BS-1.a2): LO light reflected off the detector's inputs"
        " comes back in through an open"
    )
    print(
        "   flat end, ~10 ns late. It beats with the LO, so RF OUTPUT moves, and the"
        " delay turns the laser's phase noise"
    )
    print("   into intensity noise")
    print(
        f"   {'vacuum input':>14} {'RF OUTPUT DC':>13} {'screen':>8}"
        f" {'at an end code':>14} {'added noise':>12}"
    )
    for row in vacuum_port_check(scale, seed=next(seeds)):
        print(
            f"   {row['port']:>14} {fmt_si(row['dc'], 'V'):>13}"
            f" ±{fmt_si(row['screen'], 'V'):>7} {row['ends']:13.0%}"
            f" {row['excess']:+11.0%}  of the shot noise"
        )
        for w in row["warnings"]:
            print(f"      {w[:110]}…")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")
        fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
        for runs, color, label in (
            (on, "#22d3ee", "LO on"),
            (
                record(build(scale=scale, laser_on=False), seed=next(seeds)),
                "#9ca3af",
                "dark",
            ),
        ):
            segs = [
                r[i : i + 2000] - r[i : i + 2000].mean()
                for r in runs
                for i in range(0, r.size - 1999, 2000)
            ]
            psd = np.mean([np.abs(np.fft.rfft(x)) ** 2 / 2000 for x in segs], axis=0)
            ax[0].semilogy(
                np.fft.rfftfreq(2000, 1e-8)[1:] / 1e6,
                psd[1:],
                lw=0.8,
                color=color,
                label=label,
            )
        ax[0].set(
            xlabel="frequency [MHz]",
            ylabel="PSD [codes²/bin]",
            title="spectrum of the ADC samples",
        )
        ax[0].legend(fontsize=8)
        for fs, color in zip(results, ("#facc15", "#4ade80", "#22d3ee", "#f472b6")):
            a = autocorrelation(
                record(build(sample_rate=fs, scale=scale), sweeps=3, seed=next(seeds))
            )
            ax[1].plot(
                range(1, 21),
                a,
                "o-",
                ms=3,
                lw=0.8,
                color=color,
                label=fmt_si(fs, "S/s"),
            )
        ax[1].set(
            xlabel="lag [samples]",
            ylabel="autocorrelation",
            title="correlation against sample rate",
        )
        ax[1].legend(fontsize=8)
        codes = np.concatenate(on)
        ax[2].hist(codes, bins=np.arange(257) - 0.5, color="#22d3ee")
        ax[2].set(xlabel="ADC code", ylabel="counts", title="8-bit samples: Gaussian")
        for a_ in ax:
            a_.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"\nsaved {args.plot}")
