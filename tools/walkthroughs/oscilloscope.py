"""Data for the oscilloscope walkthrough: Oscilloscope.acquire, with record, _front_end,
_digitize and measure, replayed line by line on a pulsed laser → photodiode bench with
an edge trigger and an 8-bit ADC, every intermediate array kept. The replay is checked
against acquire() itself."""

import math

import numpy as np
from scipy.signal import lfilter

from phoq import Laser, Oscilloscope, Photodiode, Setup
from phoq import instruments as I
from phoq.engine import driving_generators, synthesize, trace

SEED = 11
REFS = {
    "record": "phoq.instruments:Oscilloscope.record",
    "acquire": "phoq.instruments:Oscilloscope.acquire",
    "_front_end": "phoq.instruments:Oscilloscope._front_end",
    "_digitize": "phoq.instruments:Oscilloscope._digitize",
    "measure": "phoq.instruments:measure",
}


def bench(adc_bits=8, scale=None, level=None):
    s = Setup("Scope demo", seed=3)
    ld = s.add(
        Laser(
            "LD-1",
            mode="pulsed",
            rep_rate=50e6,
            pulse_width=1e-9,
            power_dbm=-10.0,
            rin_db_hz=-130.0,
        )
    )
    pd = s.add(Photodiode("PD-1"))
    osc = s.add(
        Oscilloscope(
            "OSC-1",
            timebase=10e-9,
            sample_rate=5e9,
            bandwidth=1e9,
            trigger="edge",
            trigger_source="ch1",
            adc_bits=adc_bits,
        )
    )
    s.connect(ld.out, pd.inp)
    s.connect(pd.rf, osc.ch1)
    if scale:
        osc.ch1_scale, osc.ch1_offset = scale, -3.0
    if level:
        osc.trigger_level = level
    return s


def _r(a, nd=5):
    return [float(f"{x:.{nd}g}") for x in np.asarray(a).ravel()]


def capture() -> dict:
    # the scale and level Autoset would pick, from an ideal-ADC sweep
    s0 = bench(adc_bits=0, level=0.5e-3)
    r0 = s0["OSC-1"].acquire(trace(s0), np.random.default_rng(SEED))
    peak = float(np.max(r0["ch1"].data))
    scale, level = I._ceil125(peak / 6), round(0.5 * peak, 6)

    s = bench(scale=scale, level=level)
    osc, pd, ld = s["OSC-1"], s["PD-1"], s["LD-1"]
    net = trace(s)
    rng = np.random.default_rng(SEED)

    # acquire()
    inputs = {ch: src for ch, src in osc._inputs().items() if src is not None}
    dets = osc._detectors(inputs)
    fs, n, dt = osc.record()
    k = max(1, min(math.ceil(6 * osc.bandwidth * dt), max(1, 200_000 // n)))
    dts = dt / k
    total = 2 * n  # edge trigger
    t0 = rng.uniform(0, 1e-3) + osc.h_position
    settle = [d.settle_time for d in dets] + [6 / (2 * math.pi * osc.bandwidth)]
    warm = min(4000, math.ceil(max(settle) / dts))
    t_sim = t0 + np.arange(-warm, total * k) * dts
    fields = synthesize(net, set(dets), t_sim, dts, rng)
    driving = driving_generators(net, dets)
    popt = sum(np.abs(E) ** 2 for E in fields[pd].values())
    raw = pd.detect(fields[pd], dts, rng, t_sim.size)
    fe = osc._front_end(raw, dts)
    sig = fe[warm::k][:total]
    t_s = t0 + np.arange(total) * dt
    pre = n // 10
    lv = osc.trigger_level
    hits = np.flatnonzero(
        (sig[pre + 1 : n + pre] >= lv) & (sig[pre : n + pre - 1] < lv)
    )
    start = int(hits[0]) + 1
    t_trig = t_s[start + pre]
    tt = t_s[start : start + n] - t_trig
    data_an = sig[start : start + n]
    data = osc._digitize(data_an, osc.ch1_scale, osc.ch1_offset)
    meas = I.measure(data, dt)

    # measure() internals
    mn, mx = float(data.min()), float(data.max())
    noise = float(np.median(np.abs(np.diff(data)))) / (0.6745 * math.sqrt(2))
    mid, h = (mx + mn) / 2, (mx - mn) * 0.2
    lo, hi = data < mid - h, data > mid + h
    rises, armed = [], False
    for i in np.flatnonzero(lo | hi):
        if lo[i]:
            armed = True
        elif armed:
            rises.append(int(i))
            armed = False

    # the front end's first pole alone
    alpha = -math.expm1(-dts * 2 * math.pi * 1.554 * osc.bandwidth)
    one_pole, _ = lfilter([alpha], [1.0, alpha - 1.0], raw, zi=[(1 - alpha) * raw[0]])

    s2 = bench(scale=scale, level=level)
    res = s2["OSC-1"].acquire(trace(s2), np.random.default_rng(SEED))
    assert res.triggered
    assert np.array_equal(res["ch1"].data, data), "replay differs from acquire()"
    assert np.allclose(res.t, tt, rtol=0, atol=1e-18)
    assert res["ch1"].meas == meas

    top = 2**osc.adc_bits - 1
    codes = np.rint(
        (data_an / osc.ch1_scale + osc.ch1_offset + I.DIV_Y / 2) / I.DIV_Y * top
    )
    return {
        "nums": {
            "timebase": osc.timebase,
            "window": 10 * osc.timebase,
            "sample_rate": osc.sample_rate,
            "max_points": osc.max_points,
            "fs": fs,
            "n": n,
            "dt": dt,
            "k": k,
            "dts": dts,
            "total": total,
            "bandwidth": osc.bandwidth,
            "settle": settle,
            "warm": warm,
            "t0": t0,
            "nsim": int(t_sim.size),
            "pre": pre,
            "level": lv,
            "hits": int(hits.size),
            "first_hit": int(hits[0]),
            "start": start,
            "t_trig": float(t_trig),
            "scale": osc.ch1_scale,
            "offset": osc.ch1_offset,
            "adc_bits": osc.adc_bits,
            "top": top,
            "lsb": I.DIV_Y * osc.ch1_scale / top,
            "alpha": alpha,
            "pd_bw": pd.bandwidth,
            "rep": ld.rep_rate,
            "fwhm": ld.pulse_width,
            "power_mw": ld.power * 1e3,
            "seed": SEED,
            "peak": peak,
            "noise": noise,
            "mid": mid,
            "h": h,
            "rises": rises,
            "meas": {key: (None if v is None else float(v)) for key, v in meas.items()},
            "codes_min": int(codes.min()),
            "codes_max": int(codes.max()),
            "n_driving": len(driving),
            "triggered": bool(res.triggered),
        },
        "popt_mW": _r(popt * 1e3),
        "raw_mA": _r(raw * 1e3),
        "one_pole_mA": _r(one_pole * 1e3),
        "fe_mA": _r(fe * 1e3),
        "sig_mA": _r(sig * 1e3),
        "data_an_mA": _r(data_an * 1e3),
        "data_mA": _r(data * 1e3, 6),
        "codes": [int(c) for c in codes],
    }
