"""Data for the spectrum-analyzer walkthrough: SpectrumAnalyzer.record, _signal, acquire
and _detect replayed line by line on the laser-noise bench (one input, 16 dB) with the
laser switched to a 500 kHz sine, every intermediate array kept. The replay is checked
against acquire() itself."""

import math
from pathlib import Path
from runpy import run_path

import numpy as np
from scipy.signal import resample_poly

from phoq import instruments as I
from phoq.engine import synthesize, trace

ROOT = Path(__file__).resolve().parents[2]
SEED = 7
REFS = {
    "record": "phoq.instruments:SpectrumAnalyzer.record",
    "acquire": "phoq.instruments:SpectrumAnalyzer.acquire",
    "_signal": "phoq.instruments:SpectrumAnalyzer._signal",
    "_detect": "phoq.instruments:SpectrumAnalyzer._detect",
}
build = run_path(str(ROOT / "examples" / "laser_noise.py"))["build"]


def bench():
    s = build(16.0, inputs=1)
    ld, esa = s["LD-1"], s["ESA-1"]
    ld.mode, ld.rep_rate, ld.mod_depth = "sine", 500e3, 0.002
    esa.averages, esa.points, esa.detector, esa.trace = 10, 201, "average", "average"
    return s


def _r(a, nd=5):
    return [float(f"{x:.{nd}g}") for x in np.asarray(a).ravel()]


def _dbm(a):
    return [round(10 * math.log10(max(x, 1e-30) / 1e-3), 2) for x in a]


def capture() -> dict:
    s = bench()
    esa, bpd, ld = s["ESA-1"], s["BPD-1"], s["LD-1"]
    net = trace(s)
    rng = np.random.default_rng(SEED)

    # record()
    fs, n, k, rbw = esa.record()
    hop, R = n // 2, esa.input_impedance
    total = n + (k - 1) * hop

    # _signal()
    d, port = esa._input()
    pad = 64
    dts = 1.0 / (fs * I.DECIMATE)
    settle = d.settle_time
    warm = min(50_000, math.ceil(settle / dts))
    t0 = rng.uniform(0, 1e-3)
    t = t0 + np.arange(-warm, (total + 2 * pad) * I.DECIMATE) * dts
    fields = synthesize(net, {d}, t, dts, rng)
    popt = sum(np.abs(E) ** 2 for (p, _), E in fields[d].items() if p == "inp_plus")
    r = esa.input_impedance
    xfine = (
        d.detect(fields[d], dts, rng, t.size)[port] * r / (r + d.output_impedance(port))
    )
    xdec = resample_poly(xfine[warm:], 1, I.DECIMATE)
    v = xdec[pad : pad + total]

    # acquire()
    dc = float(v.mean())
    v_ac = v - v.mean()
    atten = esa.input_attenuation()
    danl = 10 ** ((esa.noise_floor + atten - 30) / 10) * R
    v2 = v_ac + math.sqrt(danl * fs / 2) * rng.standard_normal(total)
    w = np.hanning(n + 1)[:-1]
    segs = v2[np.arange(k)[:, None] * hop + np.arange(n)[None, :]] * w
    nfft = n * I.ZERO_PAD
    spec = np.abs(np.fft.rfft(segs, nfft, axis=1)) ** 2 * 2 / (w.sum() ** 2 * R)
    power = spec.mean(axis=0)
    p0_before = float(power[0])
    power[0] /= 2
    f = np.arange(power.size) * fs / nfft
    stop = esa.stop_freq
    start = min(esa.start_freq, stop * (1 - 1e-6))
    fd = np.linspace(start, stop, esa.points)
    modes = {}
    for mode in ("sample", "peak", "average"):
        esa.detector = mode
        modes[mode] = esa._detect(f, power, fd)
    esa.detector = "average"

    s2 = bench()
    res = s2["ESA-1"].acquire(trace(s2), np.random.default_rng(SEED))
    mine = 10 * np.log10(np.maximum(modes["average"], 1e-30) / 1e-3)
    assert np.allclose(mine, res.level, atol=1e-9), "replay differs from acquire()"

    # trace average (running) and the single sweeps behind it
    s3 = bench()
    e3, n3 = s3["ESA-1"], trace(s3)
    sweeps = [
        e3.acquire(n3, np.random.default_rng(SEED + j)).level.tolist() for j in range(8)
    ]
    singles = []
    for j in range(8):
        s4 = bench()
        s4["ESA-1"].trace = "write"
        singles.append(
            s4["ESA-1"]
            .acquire(trace(s4), np.random.default_rng(SEED + j))
            .level.tolist()
        )
    last = np.array(sweeps[-1])

    nz = int(round(20e-6 / dts))  # 20 µs of the fine grid, from the first output sample
    return {
        "nums": {
            "fs": fs,
            "n": n,
            "k": k,
            "rbw": rbw,
            "hop": hop,
            "R": R,
            "total": total,
            "stop": stop,
            "start": start,
            "points": int(esa.points),
            "nfft": nfft,
            "pad": pad,
            "dts": dts,
            "decimate": I.DECIMATE,
            "settle": settle,
            "warm": warm,
            "t0": t0,
            "nfine": int(t.size),
            "dc": dc,
            "danl_dbm_hz": esa.noise_floor,
            "atten_db": atten,
            "danl_v2": danl,
            "noise_std": math.sqrt(danl * fs / 2),
            "zero_pad": I.ZERO_PAD,
            "max_samples": I.MAX_SAMPLES,
            "averages": int(esa.averages),
            "p0_before_dbm": 10 * math.log10(p0_before / 1e-3),
            "p0_after_dbm": 10 * math.log10(power[0] / 1e-3),
            "gain": bpd.gain,
            "rout": d.output_impedance(port),
            "bpd_bw": bpd.bandwidth,
            "mon_bw": bpd.monitor_bandwidth,
            "tone": ld.rep_rate,
            "depth": ld.mod_depth,
            "power_mw": ld.power * 1e3,
            "seed": SEED,
            "wsum": float(w.sum()),
            "i_plus": float(np.mean(xfine) * 2 / bpd.gain),
            "p_in_uW": float(np.mean(popt) * 1e6),
            "nbins": int(power.size),
            "sig_std": float(np.std(v_ac)),
            "fine_rate": fs * I.DECIMATE,
            "median_dbm": float(np.median(last)),
            "peak_dbm": float(last.max()),
            "peak_hz": float(fd[int(np.argmax(last))]),
        },
        "popt_uW": _r(popt[warm + 4 * pad : warm + 4 * pad + nz] * 1e6),
        "xfine": _r(xfine[warm + 4 * pad : warm + 4 * pad + nz]),
        "xfine_warm": _r(xfine[: warm + 40]),
        "xdec": _r(xdec[pad : pad + nz // I.DECIMATE]),
        "xdec_head": _r(xdec[: pad + 40]),
        "v": _r(v),
        "spec0": _dbm(spec[0]),
        "power": _dbm(power),
        "modes": {m: _dbm(a) for m, a in modes.items()},
        "sweeps": [[round(x, 2) for x in sw] for sw in sweeps],
        "singles": [[round(x, 2) for x in sw] for sw in singles],
    }
