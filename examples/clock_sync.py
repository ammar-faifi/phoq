"""Clock and frame synchronization from single-photon timestamps.

    uv run python examples/clock_sync.py                  # recover Alice's clock and the delay
    uv run python examples/clock_sync.py --plot sync.png  # the spectrum, periodogram and correlations
    uv run phoq serve examples/clock_sync.py              # the bench in 3D

Alice: CW laser → intensity modulator → VOA → Bob: single-photon detector → time tagger. Alice
and Bob agreed beforehand on a random string s of L bits, which Alice sends over and over. Her AWG
runs at ``CARVE`` samples per symbol and plays each symbol as (s_k, 0, 0, 0): a 1 lights a 2.5 ns
pulse centred on her clock tick k/f_A, a 0 stays dark. Bob's tagger is free-running: its bins are
100 ps of his own clock, not locked to Alice's, and its time zero is unrelated to hers (``EPOCH``,
set as the tagger's channel delay). Alice's clock runs ``PPM`` fast. From the time stamps t_i, the
string and the nominal rate, Bob recovers:

1. Alice's symbol clock f_A. The clicks of a "1" fall in its pulse, so the click train has a
   spectral line at f_A. The FFT of the click histogram finds it to one FFT bin, 1/T for a record T.
   The periodogram of the time stamps, Z(f) = |Σᵢ e^{2πi f tᵢ}|², maximised around that bin, refines
   it to the slope of the clicks' phase against time: σ_f ≈ √12 · f σ_t / (T √N) for N clicks spread
   by σ_t around the clock ticks, here the 2.5 ns pulse's 0.72 ns rms: tens of ppb.
2. A check of the agreed frame length, by epoch folding: fold the clicks' symbol indices at each
   trial length ℓ; at ℓ = L they pile onto the string's 1s and leave its 0s empty, and the folded
   histogram's χ² exceeds its ℓ − 1 degrees of freedom by ≈ N. A length sharing a factor g with L
   keeps ≈ N g/L of that excess (2L the whole of it, over twice the degrees of freedom), so the
   significance (χ² − ℓ + 1)/√(2(ℓ − 1)) peaks at L when the light really repeats every L symbols.
3. The delay Δ: time of flight plus the two clocks' offset, known modulo the frame P = L/f_A. The
   phase of Z(f_A) gives Δ inside one symbol (θ = frac(Δ f_A)). Each click then gets a symbol
   index n = rint(tᵢ f_A − θ). Folded modulo L and circularly cross-correlated (by FFT) with the
   string as ±1, these indices peak at the whole-symbol offset D, and Δ = (D + θ) / f_A.

``build(rate=, resolution=, record=, dead_time=, mu=)`` scales the bench; the defaults are 10 ns symbols,
and ``GHZ`` is 1 ns time bins (an AWG at 4 GS/s playing (1, 0, 0, 0): 250 ps pulses, 20 ps tagger
bins). There the detector, not the line, sets the statistics: a 1 µs dead time caps it at about one
click per microsecond whatever the symbol rate. Saturated, it clicks about once per dead time plus
a short wait, a rhythm of its own that sits next to a 1000-symbol frame and can outscore the frame
in step 2's blind check when there are only a couple of hundred clicks. Steps 1 and 3 do not care.

Long dead times, the 40–100 µs a free-running InGaAs SPAD is held off against afterpulsing (which
phoq does not model), leave N ≈ T/(τd + W) clicks in a record T, W being a live detector's mean wait.
Saturated (W ≪ τd) the clicks come almost every τd and strobe Alice's clock: the line has copies
every 1/τd (25 ppm of a 1 GHz clock at 40 µs, 10 ppm at 100 µs), several inside the search, and
which one step 1 picks is chance. ``recover_clock``'s "runner-up", the tallest other peak over the
chosen one, flags it near 1. Attenuating until W ≈ τd/10 (``mu=``) jitters the rhythm enough to wash
the copies out. ``LONG`` uses 250 ps tagger bins: bins must stay under half a symbol, or the tagger's
Nyquist frequency lands on the clock and mirrors the line about it.

The carving matters for step 1. A pulse filling a fraction d of the symbol spreads its clicks over
a phase 2πd of the clock, so the line keeps a depth sinc(πd) = sin(πd)/(πd): 0.90 for d = ¼, 0.64
for ½, and zero for NRZ (d = 1). A random NRZ train's spectrum has its null exactly at f_A, and step
1 has nothing to lock to (``carve=1``). The string is random so that step 3 has one answer: its
circular autocorrelation as ±1 is L at zero lag and only ≈ ±√L elsewhere, while a periodic 1010…
pattern would give Δ only modulo two symbols.
"""

import argparse
import math

import numpy as np

from phoq import (
    Attenuator,
    FiberSpool,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    PhotonCounter,
    Runner,
    Setup,
    SinglePhotonDetector,
)
from phoq.engine import trace
from phoq.units import fmt_si, photon_energy

F_NOM = 100e6  # the symbol rate both sides agreed on (``rate=``)
PPM = 23.4  # how fast Alice's clock really runs [ppm] (Bob does not know)
L = 1000  # string length: one frame P = L / f = 10 µs
CARVE = 4  # AWG samples per symbol: a "1" lights the first, a pulse of 1/4 symbol
EPOCH = 3.21e-6  # Bob's clock reads this much later than Alice's (Bob does not know)
RESOLUTION = 100e-12  # Bob's tagger bin (``resolution=``; phoq's tagger goes down to 20 ps)
RECORD = 2e-3  # one acquisition [s] (``record=``)
DEAD_TIME = 1e-6  # free-running InGaAs hold-off (``dead_time=``)
MU = 0.2  # photons per "1" pulse at the detector
SEARCH_PPM = 100.0  # how far from nominal Bob looks for Alice's clock
# (dead time, μ, record) at 1 ns symbols: saturated, then attenuated until the detector waits ≈ τd/10
DEAD_CASES = ((40e-6, MU, 20e-3), (40e-6, 0.002, 2e-3), (40e-6, 0.002, 20e-3), (100e-6, MU, 50e-3), (100e-6, 0.0008, 50e-3))
GHZ = dict(rate=1e9, resolution=20e-12)  # 1 ns time bins: an AWG at 4 GS/s carves 250 ps pulses
LONG = dict(rate=1e9, resolution=250e-12)  # the same with 250 ps bins: still under half a symbol (see build)
MAX_SLOTS = 100_000_000  # the tagger's bins per acquisition; a longer record is several, back to back


# ---- the bench ----------------------------------------------------------------------------------
def build(km: float = 0.0, ppm: float = PPM, epoch: float = EPOCH, record: float = RECORD, seed: int = 7,
          carve: int = CARVE, rate: float = F_NOM, resolution: float = RESOLUTION,
          dead_time: float = DEAD_TIME, mu: float = MU) -> Setup:
    """CW laser → IM (the string, carved, repeated) → VOA → [fiber] → SPD → free-running tagger.

    ``rate`` is the agreed symbol rate (the AWG then runs at ``carve`` × ``rate``), ``resolution``
    the tagger's bin, ``record`` one acquisition, ``dead_time`` the detector's hold-off and ``mu``
    the photons per "1" pulse at the detector (the VOA is set for it)."""
    setup = Setup("Clock and frame synchronization", seed=seed)
    f_a = rate * (1 + ppm * 1e-6)  # Alice's clock is her AWG's
    laser = setup.add(Laser("LD-1", mode="cw", power_dbm=-10.0))
    im = setup.add(IntensityModulator("IM-1"))
    awg = setup.add(FunctionGenerator("AWG-1"))
    voa = setup.add(Attenuator("VOA-1"))
    spd = setup.add(SinglePhotonDetector("SPD-1", dead_time=dead_time))
    bins = round(record / resolution)
    setup.acquisitions = -(-bins // MAX_SLOTS)  # the tagger runs on, and the detector's dead time with it
    tagger = setup.add(PhotonCounter("TAGGER", bin_rate=1 / resolution, slots=-(-bins // setup.acquisitions),
                                     compensate_delay=False, ch1_delay=epoch))
    setup.connect(laser.out, im.p1)
    setup.connect(im.p2, voa.p1)
    if km > 0:
        spool = setup.add(FiberSpool("SPL", length=km * 1e3))
        setup.connect(voa.p2, spool.p1)
        setup.connect(spool.p2, spd.inp)
    else:
        setup.connect(voa.p2, spd.inp)
    setup.connect(awg.ch1, im.rf)
    setup.connect(spd.out, tagger.ch1)

    string = np.random.default_rng(seed).integers(0, 2, L).astype(np.int8)
    samples = np.zeros(L * carve)
    samples[::carve] = string  # sample k·carve is centred on Alice's tick k / f_A
    awg.set_pattern("ch1", im.voltage_for(samples), rate=carve * f_a)
    setup.string = string  # public: agreed beforehand …
    setup.nominal = rate  # … with the nominal symbol rate
    setup.symbol_rate = f_a  # Alice's true clock (for scoring only)
    setup.mu = mu
    # μ photons in a "1" pulse at the detector: CW power through the open modulator for one sample
    voa.attenuation_db = 0.0
    on = trace(setup).port_power["SPD-1.inp"] * im.t_max  # the trace leaves the modulator's drive out
    voa.attenuation_db = 10 * math.log10(on / (carve * f_a) / photon_energy(laser.wavelength) / mu)
    return setup


def truth(setup: Setup) -> dict:
    """What Bob should find: Alice's clock and Δ = time of flight + his clock's offset (mod P)."""
    f_a = setup.symbol_rate
    delay = min(trace(setup).delays(setup["SPD-1"], setup["LD-1"])) + setup["TAGGER"].ch1_delay
    return {"f": f_a, "L": setup.string.size, "delay": delay, "delay mod P": delay % (setup.string.size / f_a)}


def record(setup: Setup, seed: int = 1) -> np.ndarray:
    """One record: Bob's time stamps [s] on his own clock (one acquisition, or several back to back)."""
    runner = Runner(setup, seed=seed)
    return np.concatenate([runner.acquire("TAGGER").timestamps("ch1") for _ in range(setup.acquisitions)])


# ---- Bob's analysis: time stamps → clock, frame, delay ----------------------------------------------
def periodogram(t: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Z(f) = |Σ e^{2πi f t}|² / N: the spectrum of the click train straight from the time stamps."""
    out = np.empty(freqs.size)
    for a in range(0, freqs.size, 256):  # keep the (frequencies × clicks) block small
        f = freqs[a:a + 256, None]
        out[a:a + 256] = np.abs(np.exp(2j * np.pi * f * t[None, :]).sum(axis=1)) ** 2 / t.size
    return out


def recover_clock(t: np.ndarray, f_nom: float = F_NOM, search_ppm: float = SEARCH_PPM) -> dict:
    """Step 1: the FFT of the click histogram, then the periodogram of the time stamps around its peak.
    A long record with few clicks (a long dead time) skips the histogram, which would not fit in
    memory, and scans the periodogram over the whole search window at a quarter FFT bin."""
    span = t[-1] - t[0]
    dt = 1 / (8 * f_nom)  # 8 histogram bins per symbol: the clock line is well below Nyquist
    step = 1 / span  # one FFT bin
    if span / dt <= 2**25:
        hist = np.bincount(((t - t[0]) / dt).astype(np.int64)).astype(float)
        spec = np.abs(np.fft.rfft(hist - hist.mean())) ** 2
        freq = np.fft.rfftfreq(hist.size, dt)
        window = np.abs(freq - f_nom) <= f_nom * search_ppm * 1e-6
    else:
        half = f_nom * search_ppm * 1e-6
        freq = f_nom + np.arange(-half, half, step / 4)
        spec = periodogram(t, freq)
        window = np.ones(freq.size, dtype=bool)
    coarse = float(freq[window][np.argmax(spec[window])])
    elsewhere = window & (np.abs(freq - coarse) > 2 * step)  # the rest of the search window
    runner_up = float(spec[elsewhere].max() / spec[window].max()) if elsewhere.any() else 0.0
    fine = coarse + np.linspace(-2, 2, 801) * step
    z = periodogram(t, fine)
    k = int(np.argmax(z))
    if 0 < k < fine.size - 1:  # parabolic peak between grid points
        y0, y1, y2 = z[k - 1], z[k], z[k + 1]
        f = float(fine[k] + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) * (fine[1] - fine[0]))
    else:
        f = float(fine[k])
    return {"f": f, "coarse": coarse, "fft bin": step, "span": span, "spectrum": (freq, spec), "zoom": (fine, z),
            "depth": math.sqrt(float(z[k]) / t.size), "runner-up": runner_up}


def symbol_phase(t: np.ndarray, f: float) -> float:
    """θ = frac(Δ f): where in the symbol the clicks fall, from the phase of the clock line."""
    return float(np.angle(np.exp(2j * np.pi * f * t).sum()) / (2 * np.pi)) % 1.0


def check_frame(t: np.ndarray, f: float, min_len: int = 200, max_len: int = 5000) -> dict:
    """Step 2, epoch folding: the trial frame length whose folded click histogram departs most
    from flat, to compare with the agreed L. Lengths below ``min_len`` (inside the detector's
    dead time) are not searched."""
    n = np.rint(t * f - symbol_phase(t, f)).astype(np.int64)
    n -= n.min()
    lengths = np.arange(min_len, max_len + 1)
    sig = np.empty(lengths.size)
    for i, ell in enumerate(lengths):
        h = np.bincount(n % ell, minlength=ell)
        lam = n.size / ell
        chi2 = float(((h - lam) ** 2).sum() / lam)
        sig[i] = (chi2 - (ell - 1)) / math.sqrt(2 * (ell - 1))
    k = int(np.argmax(sig))
    # a length sharing a factor g with L keeps a share g/L of the excess (L/2, 2L, …); coprime ones none
    other = np.gcd(lengths, lengths[k]) == 1
    return {"L": int(lengths[k]), "scan": (lengths, sig), "significance": float(sig[k]),
            "noise": float(sig[other].max())}


def recover_delay(t: np.ndarray, f: float, string: np.ndarray) -> dict:
    """Step 3: fold the symbol indices modulo the agreed L and cross-correlate them with the string."""
    size = string.size
    theta = symbol_phase(t, f)
    n = np.rint(t * f - theta).astype(np.int64)
    hist = np.bincount(n % size, minlength=size).astype(float)
    ref = 2.0 * string - 1.0  # ±1: the correlation peak stands L above a ±√L floor
    xcorr = np.fft.irfft(np.fft.rfft(hist) * np.conj(np.fft.rfft(ref)), size)  # C[d] = Σ_m h[m] s[m − d]
    d = int(np.argmax(xcorr))
    floor = np.delete(xcorr, d)
    on_ones = float(hist[(np.flatnonzero(string) + d) % size].sum() / hist.sum())
    return {"D": d, "theta": theta, "delay mod P": (d + theta) / f, "xcorr": xcorr, "folded": hist,
            "peak / rms": float((xcorr[d] - floor.mean()) / floor.std()), "on ones": on_ones}


def synchronize(t: np.ndarray, string: np.ndarray, f_nom: float = F_NOM) -> dict:
    clock = recover_clock(t, f_nom)
    frame = check_frame(t, clock["f"])
    delay = recover_delay(t, clock["f"], string)
    return {"clock": clock, "frame": frame, "delay": delay, "clicks": t.size}


def waiting_time(setup: Setup) -> float:
    """Mean wait W of a live detector for its next click: 1/(f·⟨s⟩·(1 − e^{−ημ}) + dark rate)."""
    spd = setup["SPD-1"]
    rate = setup.symbol_rate * setup.string.mean() * -math.expm1(-spd.efficiency * setup.mu) + spd.dark_count_rate
    return 1 / rate


def assigned(t: np.ndarray, sync: dict, true: dict) -> float:
    """Share of clicks given the symbol index they truly came from."""
    f, delay = sync["clock"]["f"], sync["delay"]["delay mod P"]
    mine = np.rint((t - delay) * f)
    theirs = np.rint((t - true["delay mod P"]) * true["f"])
    return float(np.mean(mine == theirs))


def report(km: float = 0.0, seed: int = 1, **bench) -> dict:
    """Build the bench (``build`` keywords), record, synchronize, and score against the truth."""
    setup = build(km=km, **bench)
    t = record(setup, seed)
    s, true = synchronize(t, setup.string, setup.nominal), truth(setup)
    c, d = s["clock"], s["delay"]
    p = true["L"] / true["f"]
    err = (d["delay mod P"] - true["delay mod P"] + p / 2) % p - p / 2
    return {"setup": setup, "t": t, "sync": s, "truth": true, "f error ppm": (c["f"] / true["f"] - 1) * 1e6,
            "delay error": err, "assigned": assigned(t, s, true),
            "depth": math.sqrt(periodogram(t, np.array([true["f"]]))[0] / t.size),  # the line at Alice's clock
            "spacing": float(np.median(np.diff(t)) * setup.nominal)}  # symbols between clicks (median)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plot", metavar="PNG", help="save the spectrum, periodogram and correlations")
    args = ap.parse_args()
    print(f"Alice: CW laser, {F_NOM / 1e6:.0f} MHz nominal, clock {PPM:+.1f} ppm, a {L}-bit random string "
          f"(frame {fmt_si(L / F_NOM, 's')}) carved into {fmt_si(1 / (CARVE * F_NOM), 's')} pulses · "
          f"Bob: free-running tagger, {fmt_si(RESOLUTION, 's')} bins, {fmt_si(RECORD, 's')} records\n")
    runs = {}
    for km in (0.0, 25.0):
        r = runs[km] = report(km)
        s, true = r["sync"], r["truth"]
        c, fr, d = s["clock"], s["frame"], s["delay"]
        link = f"{km:.0f} km of fiber" if km else "patch cords only"
        print(f"{link}: {s['clicks']:,} clicks")
        print(f"  1. clock  FFT peak {c['coarse'] / 1e6:.6f} MHz (bin {fmt_si(c['fft bin'], 'Hz')}) → periodogram "
              f"{c['f'] / 1e6:.7f} MHz; Alice's {true['f'] / 1e6:.7f} MHz: {r['f error ppm'] * 1e3:+.0f} ppb "
              f"(Bob reads her clock as {(c['f'] / F_NOM - 1) * 1e6:+.3f} ppm)")
        print(f"  2. frame  epoch folding peaks at L = {fr['L']} symbols ({fr['significance']:.0f} σ; best length "
              f"coprime to it: {fr['noise']:.1f} σ): the agreed {true['L']}")
        print(f"  3. delay  cross-correlation peak at D = {d['D']} symbols ({d['peak / rms']:.0f} σ above the floor), "
              f"θ = {d['theta']:.4f} → Δ mod P = {fmt_si(d['delay mod P'], 's', 7)}; true "
              f"{fmt_si(true['delay mod P'], 's', 7)} (flight + epoch = {fmt_si(true['delay'], 's', 6)}): "
              f"error {r['delay error'] * 1e12:+.0f} ps")
        print(f"     {d['on ones'] * 100:.2f} % of clicks fall on the string's 1s; "
              f"{r['assigned'] * 100:.2f} % get their true symbol index\n")

    print(f"1 ns time bins: {fmt_si(GHZ['rate'], 'Hz')} symbols, AWG at {fmt_si(CARVE * GHZ['rate'], 'S/s')} "
          f"(1, 0, 0, 0): {fmt_si(1 / (CARVE * GHZ['rate']), 's')} pulses, frame {fmt_si(L / GHZ['rate'], 's')}, "
          f"{fmt_si(GHZ['resolution'], 's')} tagger bins")
    print(f"   {'link':>8} {'record':>7} {'dead time':>9} {'clicks':>6} {'spacing':>8} {'clock error':>11} "
          f"{'frame check (σ; coprime best)':>30} {'delay error':>11} {'assigned':>8}")
    for km, rec, dead in ((0.0, 2e-4, 1e-6), (0.0, 2e-3, 1e-6), (0.0, 2e-4, 50e-9), (25.0, 2e-4, 50e-9)):
        r = report(km, record=rec, dead_time=dead, **GHZ)
        s, fr = r["sync"], r["sync"]["frame"]
        check = f"{fr['L']} {'ok' if fr['L'] == L else 'wrong'} ({fr['significance']:.0f} σ; {fr['noise']:.1f} σ)"
        print(f"   {f'{km:.0f} km':>8} {fmt_si(rec, 's'):>7} {fmt_si(dead, 's'):>9} {s['clicks']:6d} "
              f"{r['spacing']:8.0f} {r['f error ppm'] * 1e3:+7.0f} ppb {check:>30} "
              f"{r['delay error'] * 1e12:+8.1f} ps {r['assigned'] * 100:7.1f} %")
    print("   (spacing: median symbols between clicks. With 1 µs of dead time the detector is saturated and\n"
          "    clicks about once per 1000 + 25 symbols, a rhythm of its own next to the 1000-symbol frame.)\n")

    print("Long dead times: a free-running InGaAs SPAD held off 40 or 100 µs against afterpulsing (phoq has no\n"
          f"afterpulsing: here the dead time only costs clicks). 1 ns symbols, {fmt_si(LONG['resolution'], 's')} tagger bins")
    print(f"   {'dead time':>9} {'μ':>6} {'record':>6} {'clicks':>6} {'T/(τd+W)':>8} {'W/τd':>6} {'runner-up':>9} "
          f"{'clock error':>11} {'frame check (σ; coprime best)':>30} {'delay error':>11} {'assigned':>8}")
    for dead, mu, rec in DEAD_CASES:
        r = report(record=rec, dead_time=dead, mu=mu, **LONG)
        s, fr = r["sync"], r["sync"]["frame"]
        w = waiting_time(r["setup"])
        check = f"{fr['L']} {'ok' if fr['L'] == L else 'wrong'} ({fr['significance']:.0f} σ; {fr['noise']:.1f} σ)"
        print(f"   {fmt_si(dead, 's'):>9} {mu:6g} {fmt_si(rec, 's'):>6} {s['clicks']:6d} {rec / (dead + w):8.0f} "
              f"{w / dead:6.3f} {s['clock']['runner-up']:9.2f} {r['f error ppm'] * 1e3:+8.0f} ppb {check:>30} "
              f"{r['delay error'] * 1e12:+8.1f} ps {r['assigned'] * 100:7.1f} %")
    print("   (W: a live detector's mean wait for its next click. Saturated, W ≪ τd: the clicks come almost every τd\n"
          "    and strobe Alice's clock, so the line has copies every 1/τd, 25 ppm at 40 µs and 10 ppm at 100 µs,\n"
          "    and ±100 ppm of search holds several (runner-up ≈ 1): which one wins is chance, and the rhythm also\n"
          "    fools the frame check. With W ≈ τd/10 the waits scatter the copies.)\n")

    print("Carving: the clock line's depth at Alice's clock, and what steps 1 and 3 make of it")
    print(f"   {'pulse':>16} {'depth':>6} {'sinc(πd)':>8} {'clock error':>12} {'delay error':>12} {'assigned':>9}")
    for carve in (4, 2, 1):
        r = report(carve=carve)
        dty = 1 / carve
        name = "NRZ (d = 1)" if carve == 1 else f"{fmt_si(dty / F_NOM, 's')} (d = 1/{carve})"
        print(f"   {name:>16} {r['depth']:6.3f} {math.sin(math.pi * dty) / (math.pi * dty):8.3f} "
              f"{r['f error ppm'] * 1e3:+9.0f} ppb {r['delay error'] * 1e12:+9.0f} ps {r['assigned'] * 100:8.1f} %")

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.style.use("dark_background")

        r = runs[25.0]
        c, fr, d = r["sync"]["clock"], r["sync"]["frame"], r["sync"]["delay"]
        fig, ax = plt.subplots(2, 2, figsize=(10, 6.4))
        freq, spec = c["spectrum"]
        sel = np.abs(freq - F_NOM) < 2e6
        ax[0, 0].semilogy((freq[sel] - F_NOM) / 1e3, spec[sel], lw=0.8, color="#60a5fa")
        ax[0, 0].set(title="1. FFT of the click histogram", xlabel="f − 100 MHz [kHz]", ylabel="power")
        fine, z = c["zoom"]
        ax[0, 1].plot((fine - F_NOM), z, color="#60a5fa")
        ax[0, 1].axvline(r["truth"]["f"] - F_NOM, color="#d6409f", ls="--", lw=1, label="Alice's clock")
        ax[0, 1].set(title="1. periodogram of the time stamps", xlabel="f − 100 MHz [Hz]", ylabel="Z(f)")
        ax[0, 1].legend(fontsize=8)
        lengths, sig = fr["scan"]
        ax[1, 0].plot(lengths, sig, lw=0.7, color="#4ade80")
        ax[1, 0].set(title=f"2. epoch folding: the light repeats every {fr['L']} symbols",
                     xlabel="trial frame length [symbols]", ylabel="significance [σ]")
        ax[1, 1].plot(d["xcorr"], lw=0.7, color="#d4a514")
        ax[1, 1].set(title=f"3. cross-correlation with the string: D = {d['D']}", xlabel="lag d [symbols]")
        for a in ax.flat:
            a.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"saved {args.plot}")
