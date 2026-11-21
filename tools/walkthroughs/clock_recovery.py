"""Data for the clock-recovery walkthrough: examples/clock_sync.py's Bob finding Alice's
symbol clock in his time stamps, through synchronize, recover_clock and the periodogram
it calls, then symbol_phase, replayed line by line on the example's default bench, every
intermediate array kept. The default record takes the FFT branch. A second record, 5 ms
at 1 GHz on a detector saturated by a 40 µs dead time, is too long for the histogram:
it takes the scan branch, and its runner-up shows what recover_clock reports it for.
Both replays are checked against recover_clock(), periodogram() and symbol_phase()
themselves, and what the page draws from the bench's truth is checked against the time
stamps.
"""

import math
from pathlib import Path

import numpy as np

from phoq.loading import import_source

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = "examples/clock_sync.py"
REFS = {
    "synchronize": f"{EXAMPLE}:synchronize",
    "recover_clock": f"{EXAMPLE}:recover_clock",
    "periodogram": f"{EXAMPLE}:periodogram",
    "symbol_phase": f"{EXAMPLE}:symbol_phase",
}
# the second record: with clock_sync's LONG (1 GHz, 250 ps tagger bins), a saturated
# 40 µs dead time and 5 ms, just over the 2**25 histogram bins
SATURATED = dict(record=5e-3, dead_time=40e-6, mu=0.2)
GROUPS = 1000  # points kept of the whole spectrum
FLOOR_DB = -60.0  # where the spectra's dB scales stop


def db(x: np.ndarray, ref: float) -> list[float]:
    """10·log10(x/ref), clipped at FLOOR_DB: the mean-free DC bin is 0, and log10(0)
    would put -Infinity in the page's JSON."""
    rel = np.maximum(np.asarray(x, dtype=float) / ref, 10 ** (FLOOR_DB / 10))
    return [round(float(v), 2) for v in 10 * np.log10(rel)]


def periodogram(t: np.ndarray, freqs: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """periodogram(), line by line, and the size of each block of frequencies."""
    out = np.empty(freqs.size)
    blocks = []
    for a in range(0, freqs.size, 256):
        f = freqs[a : a + 256, None]
        out[a : a + 256] = (
            np.abs(np.exp(2j * np.pi * f * t[None, :]).sum(axis=1)) ** 2 / t.size
        )
        blocks.append(int(f.shape[0]))
    return out, blocks


def recover(t: np.ndarray, f_nom: float, search_ppm: float) -> dict:
    """recover_clock(), line by line, every intermediate kept."""
    span = t[-1] - t[0]
    dt = 1 / (8 * f_nom)
    step = 1 / span
    r = dict(span=span, dt=dt, step=step, fft=bool(span / dt <= 2**25))
    if r["fft"]:
        hist = np.bincount(((t - t[0]) / dt).astype(np.int64)).astype(float)
        spec = np.abs(np.fft.rfft(hist - hist.mean())) ** 2
        freq = np.fft.rfftfreq(hist.size, dt)
        window = np.abs(freq - f_nom) <= f_nom * search_ppm * 1e-6
        r["hist"] = hist
    else:
        half = f_nom * search_ppm * 1e-6
        freq = f_nom + np.arange(-half, half, step / 4)
        spec, r["scan blocks"] = periodogram(t, freq)
        window = np.ones(freq.size, dtype=bool)
    coarse = float(freq[window][np.argmax(spec[window])])
    elsewhere = window & (np.abs(freq - coarse) > 2 * step)
    runner_up = (
        float(spec[elsewhere].max() / spec[window].max()) if elsewhere.any() else 0.0
    )
    fine = coarse + np.linspace(-2, 2, 801) * step
    z, blocks = periodogram(t, fine)
    k = int(np.argmax(z))
    assert 0 < k < fine.size - 1, "the fine grid's peak sits on its edge"
    y0, y1, y2 = z[k - 1], z[k], z[k + 1]
    f = float(fine[k] + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) * (fine[1] - fine[0]))
    r.update(
        freq=freq,
        spec=spec,
        window=window,
        coarse=coarse,
        elsewhere=elsewhere,
        runner_up=runner_up,
        fine=fine,
        z=z,
        blocks=blocks,
        k=k,
        y=(float(y0), float(y1), float(y2)),
        f=f,
        depth=math.sqrt(float(z[k]) / t.size),
    )
    return r


def check(cs, t: np.ndarray, f_nom: float, r: dict) -> None:
    """The replay against recover_clock() and periodogram() themselves."""
    ref = cs.recover_clock(t, f_nom)
    assert ref["f"] == r["f"] and ref["coarse"] == r["coarse"], "replay differs: f"
    assert ref["runner-up"] == r["runner_up"], "replay differs: runner-up"
    assert ref["depth"] == r["depth"], "replay differs: depth"
    assert np.array_equal(ref["spectrum"][0], r["freq"])
    assert np.array_equal(ref["spectrum"][1], r["spec"]), "replay differs: spectrum"
    assert np.array_equal(ref["zoom"][1], r["z"]), "replay differs: zoom"
    assert np.array_equal(cs.periodogram(t, r["fine"]), r["z"]), "replay differs: Z"


def capture() -> dict:
    cs = import_source(ROOT / EXAMPLE)
    setup = cs.build()
    t = cs.record(setup)
    f_nom, search_ppm = setup.nominal, cs.SEARCH_PPM
    true = cs.truth(setup)
    f_a, delay, size = true["f"], true["delay"], setup.string.size

    # synchronize → recover_clock (→ periodogram)
    r = recover(t, f_nom, search_ppm)
    assert r["fft"], "the default record should take the FFT branch"
    check(cs, t, f_nom, r)
    f, step = r["f"], r["step"]

    # symbol_phase
    theta = float(np.angle(np.exp(2j * np.pi * f * t).sum()) / (2 * np.pi)) % 1.0
    assert theta == cs.symbol_phase(t, f), "replay differs from symbol_phase()"

    # the truth the page draws, against the stamps: the clicks sit under Alice's
    # pulses, one in CARVE of each symbol, centered on her ticks (assigned()'s model) …
    x = (t - delay) * f_a
    tick = np.rint(x).astype(np.int64)
    edge = 1 / (2 * cs.CARVE) + setup["TAGGER"].bin_rate ** -1 * f_a / 2
    under = (setup.string[tick % size] == 1) & (np.abs(x - tick) <= edge)
    assert under.mean() > 0.99, "the clicks should fall under Alice's pulses"
    # … and θ is where her ticks fall in Bob's symbols
    theta_true = (delay * f) % 1.0
    assert abs((theta - theta_true + 0.5) % 1.0 - 0.5) < 0.01, "θ misses Alice's ticks"

    # what θ and f do in step 3: the delay with this clock, the coarse one and one half
    # a bin off
    p = size / f_a
    delays = {}
    for name, fx in (("fine", f), ("coarse", r["coarse"]), ("half", f_a + step / 2)):
        d = cs.recover_delay(t, fx, setup.string)
        err = (d["delay mod P"] - true["delay mod P"] + p / 2) % p - p / 2
        sync = {"clock": {"f": fx}, "delay": d}
        delays[name] = dict(
            df=fx - f_a,
            err_ps=round(err * 1e12, 1),
            assigned=round(cs.assigned(t, sync, true) * 100, 2),
            theta=round(d["theta"], 4),
        )

    # one click, near the record's middle, among the histogram's bins and Alice's pulses
    i0 = int(np.flatnonzero(under)[np.searchsorted(np.flatnonzero(under), t.size // 2)])
    tc, k0 = t[i0], int(tick[i0])
    j0 = int((tc - t[0]) / r["dt"])
    assert r["hist"][j0] >= 1
    zoom = dict(
        bin=j0,
        edges=[
            round((t[0] + (j0 + j) * r["dt"] - tc) * 1e9, 4) for j in range(-12, 14)
        ],
        ticks=[
            dict(x=round((delay + (k0 + j) / f_a - tc) * 1e9, 4), bit=int(b))
            for j in range(-2, 3)
            for b in [setup.string[(k0 + j) % size]]
        ],
    )

    # the whole spectrum, the largest of each group, and the search window's bins
    spec, freq = r["spec"], r["freq"]
    peak = float(spec[r["window"]].max())
    g = -(-spec.size // GROUPS)
    whole = np.pad(spec, (0, g * GROUPS - spec.size)).reshape(GROUPS, g).max(axis=1)
    near = np.abs(freq - f_nom) <= 1.2 * f_nom * search_ppm * 1e-6
    bars = np.flatnonzero(near)
    win = r["window"][bars]
    runner = int(np.argmax(np.where(r["elsewhere"][bars], spec[bars], -1.0)))

    # the second record: the scan branch, and a runner-up near 1
    sat = cs.build(**SATURATED, **cs.LONG)
    t2 = cs.record(sat)
    r2 = recover(t2, sat.nominal, search_ppm)
    assert not r2["fft"], "the saturated record should take the scan branch"
    check(cs, t2, sat.nominal, r2)
    # what the runner-up step says: nothing competes on the first record, and on the
    # saturated one the line's copies tie
    assert r["runner_up"] < 0.05 and r2["runner_up"] > 0.9, "the runner-ups changed"
    f2a = sat.symbol_rate
    spacing = float(np.median(np.diff(t2)))
    ppm2 = lambda v: (v / sat.nominal - 1) * 1e6  # noqa: E731

    sigma_t = 1 / (cs.CARVE * f_nom) / math.sqrt(12)  # a flat pulse's rms
    spd = setup["SPD-1"]
    return {
        "nums": {
            "N": int(t.size),
            "f_nom": f_nom,
            "search_ppm": search_ppm,
            "f_a": f_a,
            "ppm": cs.PPM,
            "L": int(size),
            "carve": cs.CARVE,
            "resolution": cs.RESOLUTION,
            "record": cs.RECORD,
            "dead_time": spd.dead_time,
            "mu": cs.MU,
            "eff": spd.efficiency,
            "dcr": spd.dark_count_rate,
            "extinction": setup["IM-1"].extinction_db,
            "epoch": cs.EPOCH,
            "t0": float(t[0]),
            "t_mean": float(t.mean()),
            "span": r["span"],
            "dt": r["dt"],
            "step": step,
            "nbins": int(r["hist"].size),
            "limit": 2**25,
            "fft_size": int(spec.size),
            "fft_spacing": float(freq[1] - freq[0]),
            "window_bins": int(r["window"].sum()),
            "coarse": r["coarse"],
            "runner_up": r["runner_up"],
            "fine_n": int(r["fine"].size),
            "fine_spacing": float(r["fine"][1] - r["fine"][0]),
            "blocks": r["blocks"],
            "k": r["k"],
            "grid_max": float(r["fine"][r["k"]]),
            "f": f,
            "z_max": float(r["z"][r["k"]]),
            "z_nom": float(periodogram(t, np.array([f_nom]))[0][0]),
            "depth": r["depth"],
            "sigma_t": sigma_t,
            "sigma_f": math.sqrt(12) * f_a * sigma_t / (r["span"] * math.sqrt(t.size)),
            "theta": theta,
            "theta_true": theta_true,
            "under": round(float(under.mean()) * 100, 2),
            "delays": delays,
        },
        "t": [round(float(v) * 1e9, 3) for v in t],  # [ns] on Bob's clock
        "zoom": zoom,
        "whole": {
            "f_max": float(freq[-1]),
            "group_hz": float(g * (freq[1] - freq[0])),
            "db": db(whole, peak),
        },
        "bars": {
            "off": [round(float(v - f_nom), 3) for v in freq[bars]],
            "db": db(spec[bars], peak),
            "win": [bool(v) for v in win],
            "coarse": int(np.flatnonzero(freq[bars] == r["coarse"])[0]),
            "runner": runner,
        },
        "fine": [round(float(v) / float(r["z"][r["k"]]), 5) for v in r["z"]],
        "para": {
            "x": [float(r["fine"][r["k"] + j] - f_a) for j in (-1, 0, 1)],
            "y": list(r["y"]),
            "vertex": f - f_a,
        },
        "sat": {
            "N": int(t2.size),
            "rate": sat.nominal,
            "resolution": cs.LONG["resolution"],
            "record": SATURATED["record"],
            "dead_time": SATURATED["dead_time"],
            "mu": SATURATED["mu"],
            "span": r2["span"],
            "nbins": r2["span"] / r2["dt"],
            "step": r2["step"],
            "scan_n": int(r2["freq"].size),
            "scan_blocks": len(r2["scan blocks"]),
            "x0": ppm2(r2["freq"][0]),
            "dx": ppm2(r2["freq"][1]) - ppm2(r2["freq"][0]),
            "z": [round(float(v), 4) for v in r2["spec"] / r2["spec"].max()],
            "coarse_ppm": ppm2(r2["coarse"]),
            "f_a_ppm": ppm2(f2a),
            "err_ppm": (r2["f"] / f2a - 1) * 1e6,
            "runner_up": r2["runner_up"],
            "runner_ppm": ppm2(
                r2["freq"][np.argmax(np.where(r2["elsewhere"], r2["spec"], -1.0))]
            ),
            "spacing": spacing,
            "copy_ppm": 1 / spacing / sat.nominal * 1e6,
        },
    }
