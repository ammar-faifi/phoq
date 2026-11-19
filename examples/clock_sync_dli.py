"""Clock and frame synchronization at 1 GHz on a direct SPAD and a DLI's two SPADs.

    # recover Alice's clock and each line's delay
    uv run phoq run examples/clock_sync_dli.py
    # the lines' periodograms, patterns and cross-correlations
    uv run phoq run examples/clock_sync_dli.py --plot sync_dli.png
    # the bench in 3D
    uv run phoq serve examples/clock_sync_dli.py

examples/clock_sync.py recovers Alice's clock and the delay from one SPAD's time stamps.
This is its bench at 1 GHz (1 ns symbols, 250 ps pulses), over 25 km, with the receiver
of a COW link: a 90:10 tap sends 90 % of the light to the direct line's SPAD and the
rest through a delay-line interferometer (DLI) to the bright and the dark line's SPADs.
The SPADs are held off 10 µs, as free-running InGaAs SPADs are, and all three feed one
free-running tagger with 250 ps bins (clock_sync.py's ``LONG``), every channel delayed
by Bob's unknown epoch. Alice, her random string and the three steps of the analysis are
clock_sync.py's, with two changes:

1. Step 1 recovers the clock once, from all three lines. Each line's periodogram peaks
   at f_A, but each line has its own delay, and so its own phase of the clock line: the
   periodograms add as powers. Here that also rescues the clock (below).
2. Step 3 cross-correlates each line's folded clicks with its own pattern, the light it
   should see in each symbol, less its mean.

The DLI's long arm is one symbol, 0.20 m of fiber, longer. As in examples/cow_vacuum,
standard cords make it 0.25 m longer, and a delay line in the short arm takes back the
rest. A VOA in the long arm evens out the arms' losses, and the phase shifter is locked,
as one would on the bench, so that a pulse adds to the one before it on the bright SPAD.

The three lines see the string differently, and step 3 needs each line's own pattern.
The direct line clicks where s_k = 1. In the DLI, symbol k holds pulse k through the
short arm on top of pulse k − 1 through the long one, each arm carrying half a pulse's
light. Their fields add on the bright SPAD, (s_k + s_{k−1})²/4: a whole pulse where two
1s follow each other, a quarter where the string changes, nothing between two 0s. They
cancel on the dark SPAD, (s_k − s_{k−1})²/4, which lights only where the string changes.
Against the string itself the bright line peaks at its delay and again, as tall, one
symbol later: it holds s_k and s_{k−1} alike. The dark line does not peak at all, since
a random string's changes are uncorrelated with the string at every lag; they are as
random as the string, so the dark line's own pattern peaks once. A DLI that is not
locked moves the light of two 1s between its SPADs, and the bright and dark patterns no
longer hold.

The hold-off splits the lines in two. The direct SPAD, with 90 % of the light, waits
only 40 ns once live: saturated, it clicks almost every 10 µs, about a thousand times in
the 10 ms record. That rhythm strobes Alice's clock, as clock_sync.py's long hold-offs
do. On the direct line alone the clock line has copies every 1/τd, 100 ppm of the clock,
and one of them inside Bob's ±100 ppm search stands almost as tall as the line itself
(runner-up 0.95): which one step 1 picks is chance. The rhythm also pulls the direct
line's frame check onto a divisor of its 10,040 symbols. The DLI's lines get a tenth of
the light, less the DLI's losses, and wait 1 and 4 µs: their waits scatter the rhythm,
so their roughly 900 bright and 700 dark clicks show one clock line and confirm L at
about 16 σ. Added to the direct line's, their periodograms bring the copy down to 0.40
of the line. Each line's delay then comes to a few ps. The DLI's lines sit 11.24 ns
behind the direct one: the 1 m cord from the tap into the DLI, the short arm's 1.25 m
and the delay line.
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np

from phoq import (
    ArbitraryWaveformGenerator,
    Attenuator,
    Coupler,
    FiberSpool,
    IntensityModulator,
    Laser,
    OpticalDelayLine,
    PhaseShifter,
    PhotonCounter,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import trace
from phoq.units import fmt_si, photon_energy

sys.path.insert(0, str(Path(__file__).resolve().parent))
# clock_sync.py's agreed string and the steps that do not change with the lines
from clock_sync import (  # noqa: E402
    CARVE,
    EPOCH,
    L,
    MAX_SLOTS,
    MU,
    PPM,
    SEARCH_PPM,
    check_frame,
    periodogram,
    symbol_phase,
)

# the interferometer's two paths to a detector, and its phase lock
from dli import arms, lock  # noqa: E402

RATE = 1e9  # the agreed symbol rate: 1 ns symbols, as clock_sync.py's ``GHZ``
# Bob's tagger bin: clock_sync.py's ``LONG``, under half a symbol, for a long record
RESOLUTION = 250e-12
DEAD_TIME = 10e-6  # the SPADs' hold-off, a free-running InGaAs SPAD's
KM = 25.0  # the link
RECORD = 10e-3  # one acquisition [s]: about a thousand clicks per line
TAP = 0.9  # Bob's tap: 90 % to the direct line, the rest to the DLI, as in COW
LINES = ("direct", "bright", "dark")  # Bob's SPADs, on tagger channels 1, 2 and 3


# ---- the bench -----------------------------------------------------------------------
def dli(setup: Setup) -> tuple[Coupler, Coupler]:
    """The DLI for 1 ns symbols, of standard cords as in examples/cow_vacuum: BS1 →
    short arm (0.5 m, phase shifter, 0.25 m, delay line, 0.5 m) and long arm (1 m, VOA,
    0.5 m) → BS2. The cords make the long arm 0.25 m (1.22 ns) longer; ``tune_dli``
    sets the delay line to take back what is over one symbol."""
    bs1, bs2 = setup.add(Coupler("DLI-BS1"), Coupler("DLI-BS2"))
    ps, odl = setup.add(PhaseShifter("DLI-PS"), OpticalDelayLine("DLI-ODL"))
    voa = setup.add(Attenuator("DLI-VOA", attenuation_db=0.0))
    setup.connect(bs1.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, odl.p1, length=0.25)
    setup.connect(odl.p2, bs2.a1, length=0.5)
    setup.connect(bs1.b2, voa.p1, length=1.0)
    setup.connect(voa.p2, bs2.a2, length=0.5)
    return bs1, bs2


def tune_dli(setup: Setup) -> None:
    """As on the bench: the delay line so that the long arm is exactly one symbol
    longer, the VOA so that both arms bring the same light, then the phase shifter so
    that a pulse adds to the one before it on the bright SPAD."""
    odl, voa = setup["DLI-ODL"], setup["DLI-VOA"]
    short, long_ = arms(setup, "LD-1", "SPD-BRIGHT")
    want = odl.setting + (long_.delay - short.delay) - 1 / RATE
    if not 0.0 <= want <= odl.max_delay:
        raise ValueError(
            f"DLI-ODL would need {fmt_si(want, 's')}, outside its travel: change the"
            " DLI's cords"
        )
    odl.delay = want
    voa.attenuation_db = 0.0
    short, long_ = arms(setup, "LD-1", "SPD-BRIGHT")
    voa.attenuation_db = max(0.0, 20 * math.log10(abs(long_.amp) / abs(short.amp)))
    lock(setup, "LD-1", "SPD-BRIGHT", "DLI-PS")


def build(record: float = RECORD, seed: int = 7) -> Setup:
    """CW laser → IM (the string, carved, repeated) → VOA → 25 km → Bob's receiver →
    free-running tagger.

    The receiver taps ``TAP`` of the light to the direct line's SPAD and sends the rest
    through the DLI to the bright and the dark line's SPADs, on tagger channels 1, 2 and
    3. The VOA sets ``MU`` photons per "1" pulse at the direct SPAD; ``record`` is one
    acquisition."""
    setup = Setup("Clock and frame synchronization on three SPADs", seed=seed)
    f_a = RATE * (1 + PPM * 1e-6)  # Alice's clock is her AWG's
    laser = setup.add(Laser("LD-1", mode="cw", power_dbm=-10.0))
    im = setup.add(IntensityModulator("IM-1"))
    awg = setup.add(ArbitraryWaveformGenerator("AWG-1"))
    voa = setup.add(Attenuator("VOA-1"))
    spool = setup.add(FiberSpool("SPL", length=KM * 1e3))
    tap = setup.add(Coupler("TAP", ratio=TAP))
    spd = {
        line: setup.add(
            SinglePhotonDetector(f"SPD-{line.upper()}", dead_time=DEAD_TIME)
        )
        for line in LINES
    }
    bins = round(record / RESOLUTION)
    # the tagger runs on, and the SPADs' dead time with it
    setup.acquisitions = -(-bins // MAX_SLOTS)
    # one tagger, one clock: every channel reads Bob's epoch
    epochs = {f"ch{i}_delay": EPOCH for i in range(1, len(LINES) + 1)}
    tagger = setup.add(
        PhotonCounter(
            "TAGGER",
            bin_rate=1 / RESOLUTION,
            slots=-(-bins // setup.acquisitions),
            compensate_delay=False,
            **epochs,
        )
    )
    bs1, bs2 = dli(setup)
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    setup.connect(voa.p2, spool.p1)
    setup.connect(spool.p2, tap.a1)
    setup.connect(tap.b1, spd["direct"].inp)
    setup.connect(tap.b2, bs1.a1)
    setup.connect(bs2.b1, spd["bright"].inp)
    setup.connect(bs2.b2, spd["dark"].inp)
    setup.connect(awg.ch1, im.rf)
    for i, line in enumerate(LINES, 1):
        setup.connect(spd[line].out, getattr(tagger, f"ch{i}"))
    setup.terminate_spares()  # the tap's and the DLI's spare inputs
    # each line's SPAD and tagger channel
    setup.lines = {line: (spd[line].name, f"ch{i}") for i, line in enumerate(LINES, 1)}

    string = np.random.default_rng(seed).integers(0, 2, L).astype(np.int8)
    samples = np.zeros(L * CARVE)
    samples[::CARVE] = string  # sample k·CARVE is centered on Alice's tick k / f_A
    awg.set_pattern("ch1", im.voltage_for(samples), rate=CARVE * f_a)
    setup.string = string  # public: agreed beforehand …
    setup.nominal = RATE  # … with the nominal symbol rate
    setup.symbol_rate = f_a  # Alice's true clock (for scoring only)
    # μ photons in a "1" pulse at the direct SPAD: CW power through the open modulator
    # for one sample
    voa.attenuation_db = 0.0
    # the trace leaves the modulator's drive out
    on = trace(setup).port_power["SPD-DIRECT.inp"] * im.t_max
    voa.attenuation_db = 10 * math.log10(
        on / (CARVE * f_a) / photon_energy(laser.wavelength) / MU
    )
    tune_dli(setup)
    return setup


def truth(setup: Setup, line: str) -> dict:
    """What Bob should find on ``line``: Alice's clock and Δ = time of flight + his
    clock's offset (mod P). Behind the DLI the flight is the short arm's: there symbol k
    is pulse k through the short arm on top of pulse k − 1 through the long one."""
    f_a = setup.symbol_rate
    spd, ch = setup.lines[line]
    delay = min(trace(setup).delays(setup[spd], setup["LD-1"])) + getattr(
        setup["TAGGER"], f"{ch}_delay"
    )
    return {
        "f": f_a,
        "L": setup.string.size,
        "delay": delay,
        "delay mod P": delay % (setup.string.size / f_a),
    }


def record(setup: Setup, seed: int = 1) -> dict[str, np.ndarray]:
    """One record: each line's time stamps [s] on Bob's clock (one acquisition, or
    several back to back)."""
    runner = Runner(setup, seed=seed)
    batches = [runner.acquire("TAGGER") for _ in range(setup.acquisitions)]
    return {
        line: np.concatenate([b.timestamps(ch) for b in batches])
        for line, (_, ch) in setup.lines.items()
    }


# ---- Bob's analysis: time stamps → one clock, each line's delay ----------------------
def patterns(string: np.ndarray) -> dict[str, np.ndarray]:
    """The light each line should see in symbol k, from the agreed string, in units of
    a "1" pulse at the line's input. Behind the DLI, locked as in ``tune_dli``, symbol k
    holds pulse k through the short arm on top of pulse k − 1 through the long one, each
    arm carrying half a pulse's light: their fields add on the bright SPAD, (s_k +
    s_{k−1})²/4, and cancel on the dark one, (s_k − s_{k−1})²/4, which lights only
    where the string changes."""
    s = string.astype(float)
    before = np.roll(s, 1)  # s_{k−1}: the string repeats, so symbol 0 follows L − 1
    return {"direct": s, "bright": (s + before) ** 2 / 4, "dark": (s - before) ** 2 / 4}


def recover_clock(
    t: np.ndarray | list[np.ndarray],
    f_nom: float = RATE,
    search_ppm: float = SEARCH_PPM,
) -> dict:
    """Step 1, as clock_sync.py's ``recover_clock``, on one line's time stamps or on a
    list of several lines', whose powers add: the lines share Alice's clock and Bob's
    tagger, but each has its own delay and so its own phase of the clock line, and
    pooling the stamps themselves would let those phases partly cancel."""
    lines = [t] if isinstance(t, np.ndarray) else list(t)
    clicks = sum(x.size for x in lines)
    t0 = min(x[0] for x in lines)
    span = max(x[-1] for x in lines) - t0

    def power(freqs: np.ndarray) -> np.ndarray:
        """The lines' periodograms, each weighted by its share of the clicks."""
        return sum(periodogram(x, freqs) * x.size for x in lines) / clicks

    # 8 histogram bins per symbol: the clock line is well below Nyquist
    dt = 1 / (8 * f_nom)
    step = 1 / span  # one FFT bin
    if span / dt <= 2**25:
        bins = int(span / dt) + 1
        spec = 0.0
        for x in lines:
            idx = ((x - t0) / dt).astype(np.int64)
            hist = np.bincount(idx, minlength=bins).astype(float)
            spec = spec + np.abs(np.fft.rfft(hist - hist.mean())) ** 2
        freq = np.fft.rfftfreq(bins, dt)
        window = np.abs(freq - f_nom) <= f_nom * search_ppm * 1e-6
    else:
        half = f_nom * search_ppm * 1e-6
        freq = f_nom + np.arange(-half, half, step / 4)
        spec = power(freq)
        window = np.ones(freq.size, dtype=bool)
    coarse = float(freq[window][np.argmax(spec[window])])
    # the rest of the search window
    elsewhere = window & (np.abs(freq - coarse) > 2 * step)
    runner_up = (
        float(spec[elsewhere].max() / spec[window].max()) if elsewhere.any() else 0.0
    )
    fine = coarse + np.linspace(-2, 2, 801) * step
    z = power(fine)
    k = int(np.argmax(z))
    if 0 < k < fine.size - 1:  # parabolic peak between grid points
        y0, y1, y2 = z[k - 1], z[k], z[k + 1]
        f = float(fine[k] + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) * (fine[1] - fine[0]))
    else:
        f = float(fine[k])
    return {
        "f": f,
        "coarse": coarse,
        "fft bin": step,
        "span": span,
        "spectrum": (freq, spec),
        "zoom": (fine, z),
        "runner-up": runner_up,
    }


def recover_delay(t: np.ndarray, f: float, pattern: np.ndarray) -> dict:
    """Step 3: fold the symbol indices modulo the agreed L and cross-correlate them with
    the line's pattern, the light it should see in each symbol (``patterns``; on the
    direct line, the string itself)."""
    size = pattern.size
    theta = symbol_phase(t, f)
    n = np.rint(t * f - theta).astype(np.int64)
    hist = np.bincount(n % size, minlength=size).astype(float)
    # less its mean, so that a flat histogram scores the same at every lag: for a random
    # string the peak then stands N/2 above a floor of ±√N/2
    ref = pattern - pattern.mean()
    # C[d] = Σ_m h[m] p[m − d]
    xcorr = np.fft.irfft(np.fft.rfft(hist) * np.conj(np.fft.rfft(ref)), size)
    d = int(np.argmax(xcorr))
    floor = np.delete(xcorr, d)
    lit = float(hist[(np.flatnonzero(pattern) + d) % size].sum() / hist.sum())
    return {
        "D": d,
        "theta": theta,
        "delay mod P": (d + theta) / f,
        "xcorr": xcorr,
        "folded": hist,
        "peak / rms": float((xcorr[d] - floor.mean()) / floor.std()),
        "on light": lit,
    }


def synchronize_lines(
    stamps: dict[str, np.ndarray], string: np.ndarray, f_nom: float = RATE
) -> dict:
    """Bob's receiver: step 1 once, on every line's clicks (one Alice, one tagger), then
    steps 2 and 3 on each line, its delay against its own pattern (``patterns``)."""
    clock = recover_clock(list(stamps.values()), f_nom)
    expect = patterns(string)
    lines = {
        line: {
            "frame": check_frame(t, clock["f"]),
            "delay": recover_delay(t, clock["f"], expect[line]),
            "clicks": t.size,
        }
        for line, t in stamps.items()
    }
    return {"clock": clock, "lines": lines}


def assigned(t: np.ndarray, f: float, delay: float, true: dict) -> float:
    """Share of clicks given the symbol index they truly came from, by the clock ``f``
    and the delay (mod P) Bob recovered."""
    mine = np.rint((t - delay) * f)
    theirs = np.rint((t - true["delay mod P"]) * true["f"])
    return float(np.mean(mine == theirs))


def report(seed: int = 1, **bench) -> dict:
    """Build the bench (``build`` keywords), record, synchronize every line on one
    clock, and score each line against the truth."""
    setup = build(**bench)
    stamps = record(setup, seed)
    s = synchronize_lines(stamps, setup.string, setup.nominal)
    f = s["clock"]["f"]
    lines = {}
    for line, t in stamps.items():
        true, d = truth(setup, line), s["lines"][line]["delay"]
        p = true["L"] / true["f"]
        lines[line] = {
            **s["lines"][line],
            "t": t,
            "truth": true,
            "delay error": (d["delay mod P"] - true["delay mod P"] + p / 2) % p - p / 2,
            "assigned": assigned(t, f, d["delay mod P"], true),
        }
    return {
        "setup": setup,
        "clock": s["clock"],
        "f error ppm": (f / setup.symbol_rate - 1) * 1e6,
        "lines": lines,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--plot",
        metavar="PNG",
        help="save the lines' periodograms, patterns and cross-correlations",
    )
    args = ap.parse_args()
    print(
        f"Alice: CW laser, {fmt_si(RATE, 'Hz')} nominal, clock {PPM:+.1f} ppm, a"
        f" {L}-bit random string (frame {fmt_si(L / RATE, 's')}) carved into"
        f" {fmt_si(1 / (CARVE * RATE), 's')} pulses; {KM:.0f} km of fiber\nBob:"
        f" {TAP * 100:.0f} % of the light to the direct SPAD, the rest through the DLI"
        f" (long arm {fmt_si(1 / RATE, 's')} longer) to the bright and the dark SPAD,"
        f" {fmt_si(DEAD_TIME, 's')} dead time; a free-running tagger,"
        f" {fmt_si(RESOLUTION, 's')} bins, {fmt_si(RECORD, 's')} record\n"
    )
    shape = {
        "direct": "s_k",
        "bright": "(s_k + s_{k−1})²/4",
        "dark": "(s_k − s_{k−1})²/4",
    }
    r = report()
    c, lines = r["clock"], r["lines"]
    counts = ", ".join(f"{x['clicks']:,} {line}" for line, x in lines.items())
    print(f"{counts} clicks")
    print(
        f"  1. clock  all lines' clicks: coarse peak {c['coarse'] / 1e6:.5f} MHz (FFT"
        f" bin {fmt_si(c['fft bin'], 'Hz')}) → periodogram {c['f'] / 1e6:.6f} MHz;"
        f" Alice's {r['setup'].symbol_rate / 1e6:.6f} MHz:"
        f" {r['f error ppm'] * 1e3:+.0f} ppb; runner-up {c['runner-up']:.2f}"
    )
    # the saturated direct line on its own: its rhythm strobes the clock
    alone = recover_clock(lines["direct"]["t"])
    print(
        f"     the direct line's clicks alone: runner-up {alone['runner-up']:.2f},"
        f" clock {(alone['f'] / r['setup'].symbol_rate - 1) * 1e9:+.0f} ppb off"
    )
    print(
        "  2. frame  epoch folding, line by line   3. delay  each line's folded"
        " clicks against the light it should see"
    )
    print(
        f"     {'line':6} {'pattern':18} {'frame check (σ; coprime best)':>29}"
        f" {'D':>4} {'peak':>5} {'Δ mod P':>12} {'error':>7} {'on light':>8}"
        f" {'assigned':>8}"
    )
    for line, x in lines.items():
        fr, d = x["frame"], x["delay"]
        check = (
            f"{fr['L']} {'ok' if fr['L'] == L else 'wrong'}"
            f" ({fr['significance']:.0f} σ; {fr['noise']:.1f} σ)"
        )
        print(
            f"     {line:6} {shape[line]:18} {check:>29} {d['D']:4d}"
            f" {d['peak / rms']:3.0f} σ {fmt_si(d['delay mod P'], 's', 7):>12}"
            f" {x['delay error'] * 1e12:+4.0f} ps {d['on light'] * 100:7.2f} %"
            f" {x['assigned'] * 100:7.2f} %"
        )
    p = L / c["f"]
    got = {line: x["delay"]["delay mod P"] for line, x in lines.items()}
    traced = {line: x["truth"]["delay mod P"] for line, x in lines.items()}

    def gap(delays: dict, a: str, b: str) -> float:
        """Line a's delay minus line b's, within half a frame."""
        return (delays[a] - delays[b] + p / 2) % p - p / 2

    print(
        "     the DLI's lines behind the direct one:"
        f" {fmt_si(gap(got, 'bright', 'direct'), 's', 5)} (traced"
        f" {fmt_si(gap(traced, 'bright', 'direct'), 's', 5)}); dark − bright:"
        f" {gap(got, 'dark', 'bright') * 1e12:+.0f} ps (traced"
        f" {gap(traced, 'dark', 'bright') * 1e12:+.0f} ps)"
    )
    tallest = []
    for line in ("bright", "dark"):
        xc = recover_delay(lines[line]["t"], c["f"], r["setup"].string)["xcorr"]
        top = np.argsort(xc)[::-1][:2]
        rest = np.delete(xc, top)
        tallest.append(
            f"{line} D = {top[0]} ({(xc[top[0]] - rest.mean()) / rest.std():.1f} σ)"
            f" or {top[1]} ({(xc[top[1]] - rest.mean()) / rest.std():.1f} σ)"
        )
    print(f"     against the string itself: {'; '.join(tallest)}")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")
        color = {"direct": "#3987e5", "bright": "#d95926", "dark": "#199e70"}
        muted = "#898781"  # what the lines are compared with

        string = r["setup"].string
        fig, ax = plt.subplots(2, 3, figsize=(13.5, 7.2), constrained_layout=True)
        # 1. each line's periodogram across Bob's search, scaled to its own peak: the
        # saturated direct line's rhythm puts a copy of the clock line 1/τd away
        for line, x in lines.items():
            freq, spec = recover_clock(x["t"])["spectrum"]
            near = np.abs(freq - RATE) <= RATE * SEARCH_PPM * 1e-6
            ax[0, 0].plot(
                (freq[near] - RATE) / 1e3,
                spec[near] / spec[near].max(),
                lw=0.8,
                color=color[line],
                label=line,
            )
        ax[0, 0].axvline(
            (r["setup"].symbol_rate - RATE) / 1e3,
            color=muted,
            ls="--",
            lw=1,
            label="Alice's clock",
        )
        ax[0, 0].set(
            title="1. the clock line across Bob's search",
            xlabel=f"f − {fmt_si(RATE, 'Hz')} [kHz]",
            ylabel="periodogram / its peak",
        )
        ax[0, 0].legend(fontsize=8)
        # 2. epoch folding, line by line: the DLI's lines over the direct line's rhythm
        for line, x in lines.items():
            lengths, sig = x["frame"]["scan"]
            ax[0, 1].plot(lengths, sig, lw=0.6, color=color[line], label=line)
        ax[0, 1].set(
            title="2. epoch folding, line by line",
            xlabel="trial frame length [symbols]",
            ylabel="significance [σ]",
        )
        ax[0, 1].legend(fontsize=8)
        # the patterns as Bob reads them once synchronized: clicks per symbol, by the
        # bits the symbol and the one before it carry, with their Poisson errors
        kind = 2 * np.roll(string, 1) + string  # (s_{k−1}, s_k): 00, 01, 10, 11
        size = np.bincount(kind, minlength=4)
        for i, (line, x) in enumerate(lines.items()):
            d = x["delay"]
            per_symbol = d["folded"][(np.arange(L) + d["D"]) % L]
            n = np.bincount(kind, weights=per_symbol, minlength=4)
            ratio = n / size / (n[1] / size[1])
            ax[0, 2].bar(
                np.arange(4) + (i - 1) * 0.27,
                ratio,
                width=0.25,
                yerr=ratio * np.sqrt(1 / np.maximum(n, 1) + 1 / n[1]),
                error_kw=dict(ecolor="#c3c2b7", lw=1, capsize=2),
                color=color[line],
                label=line,
            )
        ax[0, 2].set(
            title="the patterns Bob reads once synchronized",
            xticks=range(4),
            xticklabels=["00", "01", "10", "11"],
            xlabel=r"the symbol's bits $s_{k-1}\,s_k$",
            ylabel="clicks per symbol, relative to 01",
        )
        ax[0, 2].legend(fontsize=8)
        # 3. each line's cross-correlation near its peak: against its own pattern, and
        # against the string, which is the direct line's own
        expect = patterns(string)
        for a, (line, x) in zip(ax[1], lines.items()):
            d = x["delay"]
            lags = np.arange(d["D"] - 20, d["D"] + 21)
            guides = [(expect[line], color[line], "its pattern")]
            if line != "direct":
                guides.append((string, muted, "the string"))
            for pattern, col, label in guides:
                xc = recover_delay(x["t"], c["f"], pattern)["xcorr"]
                sigma = (xc - xc.mean()) / xc.std()
                a.plot(
                    lags,
                    sigma[lags % L],
                    marker="o",
                    ms=3,
                    lw=1,
                    color=col,
                    label=label,
                )
            a.set(
                title=f"3. {line}: D = {d['D']}",
                xlabel="lag d [symbols]",
                ylabel="cross-correlation [σ]",
            )
            a.legend(fontsize=8)
        for a in ax.flat:
            a.grid(True, color="#2c2c2a", lw=0.8)
        fig.savefig(args.plot, dpi=140)
        print(f"saved {args.plot}")
