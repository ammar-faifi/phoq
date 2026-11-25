"""Data for the engine walkthrough: ``setup.run()`` on examples/modulated_link.py, a
CW laser through an intensity modulator to a photodiode that a scope watches. The walk
goes from Runner.run through trace() and _enter() to synthesize(), the modulator's
drive_voltage() and transmission(), and Photodiode.detect(), every intermediate value
kept. trace() runs as it is, with every _enter() call recorded; the rest is replayed
line by line with the scope's own time grid and random draws, checked against each
function and, at the end, against the CH1 and CH2 traces setup.run() itself
returns."""

import importlib
import inspect
import math
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

from phoq import engine as E
from phoq.loading import import_source
from phoq.runner import instruments_of
from phoq.units import K_B, Q_E, mating

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "modulated_link.py"
REFS = {
    "run": "phoq.runner:Runner.run",
    "trace": "phoq.engine:trace",
    "_enter": "phoq.engine:_enter",
    "synthesize": "phoq.engine:synthesize",
    "drive_voltage": "phoq.modulators:_Modulator.drive_voltage",
    "transmission": "phoq.modulators:IntensityModulator.transmission",
    "detect": "phoq.detectors:Photodiode.detect",
}
# The lines each step lights up: (function, the first line holds …, the last line
# holds …, lines past that one). Found in the source as it is now, so they follow
# edits; a missing anchor stops the build.
ANCHORS = {
    "run": ("run", "net = trace(self.setup)", "report=report(self.setup, net),", 0),
    "stack": ("trace", "net = Network()", "MIN_GAIN_TWICE_REFLECTED)", 2),
    "mating": ("trace", "t, r_back, _ = mating(", "amp * math.sqrt(t), delay", 1),
    "scatter": (
        "_enter",
        "for tr in comp.scatter(port, wl):",
        "(tr.port, amp * tr.amp",
        1,
    ),
    "path": ("_enter", "if isinstance(comp, _Detector):", "return", 0),
    "returned": ("_enter", "if isinstance(comp, Laser):", "net.returned[comp]", 1),
    "grid": ("synthesize", "out = {d: {} for d in detectors}", "gen.start(rng)", 0),
    "emit": ("synthesize", "delays = np.array(", "mid = emit + dt / 2", 0),
    "field": (
        "synthesize",
        "field_ = src.envelope(emit, dt)",
        "field_ = field_ * noise",
        0,
    ),
    "factor": ("synthesize", "f = p.amp * field_[k]", "f = f * mod.factor(", 0),
    "volts": ("drive_voltage", "total = np.zeros(", "return total", 0),
    "cosine": ("transmission", "v = np.asarray(volts", "return 0.5 * (1.0 +", 0),
    "acc": ("synthesize", "acc = out[d]", "acc[key] = acc[key] + f", 0),
    "current": ("detect", "current = np.full(n,", "current += self.responsivity", 0),
    "noise": ("detect", "density = (", "[alpha], [1.0, alpha - 1.0], out", 1),
    "acquire": ("run", "inst.acquire(net, self.rng)", "inst.acquire(net, self.rng)", 0),
}
EVERY = 10  # the sweep's curves keep every 10th bin
ZOOM = (2000, 2200)  # 20 ns of bins at full resolution, for the noise


def _source(ref: str) -> list[str]:
    module, qual = ref.split(":")
    obj = importlib.import_module(module)
    for part in qual.split("."):
        obj = getattr(obj, part)
    return inspect.getsource(obj).rstrip("\n").split("\n")


def lines() -> dict:
    """Each step's [first, last] line, 0-based in its function's source."""
    out = {}
    for step, (fn, first, last, extra) in ANCHORS.items():
        src = _source(REFS[fn])
        a = next((i for i, ln in enumerate(src) if first in ln), None)
        b = next((i for i in range(a or 0, len(src)) if last in src[i]), None)
        if a is None or b is None:
            raise LookupError(
                f"engine walkthrough: step {step!r} lost its lines in {fn}"
            )
        out[step] = [a, b + extra]
    return out


def bench():
    """A fresh copy of the example's setup (the script runs again on every import)."""
    return import_source(EXAMPLE).setup


def _r(a, nd=5):
    return [float(f"{x:.{nd}g}") for x in np.asarray(a).ravel()]


def _sine(t, y, f):
    """Amplitude, phase [rad] and mean of y ≈ c + a·sin(2πft + φ)."""
    m = np.column_stack(
        [np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t), np.ones(t.size)]
    )
    (s, c, mean), *_ = np.linalg.lstsq(m, y, rcond=None)
    return math.hypot(s, c), math.atan2(c, s), mean


def capture() -> dict:
    setup = bench()
    # Runner.run acquires every instrument in turn on one rng: the replay's draws are
    # setup.run()'s only if the scope is the one instrument
    assert [i.name for i in instruments_of(setup)] == ["OSC-1"]
    ld, im, pd, osc, fg, psu = (
        setup[n] for n in ("LD-1", "IM-1", "PD-1", "OSC-1", "FG-1", "PSU-1")
    )

    # trace(), with every _enter() call recorded
    calls = []
    real_enter = E._enter

    def spy(net, stack, src, port, amp, delay, mods, hops, refl, site):
        calls.append(
            {
                "port": port.path,
                "w": src.power * abs(amp) ** 2,
                "delay": delay,
                "refl": refl,
                "mods": [[m.name, at] for m, at in mods],
                "stack": len(stack),
            }
        )
        real_enter(net, stack, src, port, amp, delay, mods, hops, refl, site)

    E._enter = spy
    try:
        net = E.trace(setup)
    finally:
        E._enter = real_enter
    (path,) = net.paths[pd]
    ((mod, at),) = path.mods
    assert mod is im and not path.reflections
    t_mate, r_back, _ = mating("FC/UPC", "FC/UPC")
    cord = setup["FIB-1"]
    (through,) = cord.scatter(cord.ports["a"], ld.wavelength)
    (in_im,) = im.scatter(im.ports["p1"], ld.wavelength)

    # OSC-1.acquire() up to synthesize(): its grid and its first random draw (the
    # oscilloscope walkthrough follows these lines)
    # setup.run() seeds its Runner with the setup's seed
    rng = np.random.default_rng(setup.seed)
    fs, n, dt = osc.record()
    k = max(1, min(math.ceil(6 * osc.bandwidth * dt), max(1, 200_000 // n)))
    dts = dt / k
    # sync, but CW light has no clock: a random start, untriggered
    assert osc.trigger == "sync" and ld.period is None
    t_trig = rng.uniform(0, 1e-3)
    t0 = t_trig + osc.h_position - osc.timebase
    settle = [pd.settle_time, 6 / (2 * math.pi * osc.bandwidth)]
    warm = min(4000, math.ceil(max(settle) / dts))
    t = t0 + np.arange(-warm, n * k) * dts

    # synthesize(net, {PD-1}, t, dts, rng)
    before = rng.bit_generator.state
    gens = E.driving_generators(net, [pd])
    for gen in gens:
        gen.start(rng)
    delays = np.array([path.delay])
    emit = t[None, :] - delays[:, None]
    mid = emit + dts / 2
    env = ld.envelope(emit, dts)
    phi = ld.phase(mid, rng)
    field_ = env * np.exp(1j * phi)
    assert ld.intensity_factor(emit, dts, rng) is None  # no excess RIN
    f0 = path.amp * field_[0]
    t_mod, t_emit = mid[0] + at, mid[0]
    # drive_voltage(): rf, then bias
    v_rf = fg.voltage("ch1", t_mod, t_emit)
    v_bias = psu.voltage("ch1", t_mod, t_emit)
    volts = np.zeros(np.shape(t_mod)) + v_rf
    volts = volts + v_bias * im.bias_scale()
    assert np.array_equal(volts, im.drive_voltage(t_mod, t_emit))
    # transmission()
    trans = 0.5 * (1.0 + im.contrast * np.cos(np.pi * volts / im.v_pi + im.phi0))
    assert np.array_equal(trans, im.transmission(volts))
    field = f0 * np.sqrt(trans)
    assert np.array_equal(field, f0 * mod.factor(t_mod, t_emit))
    check = np.random.default_rng()
    check.bit_generator.state = before
    ref = E.synthesize(net, {pd}, t, dts, check)
    assert np.array_equal(
        ref[pd][ld.wavelength], field
    ), "replay differs from synthesize()"
    fields = {ld.wavelength: field}

    # Photodiode.detect(fields, dts, rng, t.size)
    before = rng.bit_generator.state
    nsim = t.size
    current = np.full(nsim, pd.dark_current)
    current += pd.responsivity(ld.wavelength) * np.abs(field) ** 2
    density = 2 * Q_E * np.clip(current, 0, None) + 4 * K_B * pd.temperature / pd.load
    noisy = current + np.sqrt(density / (2 * dts)) * rng.standard_normal(nsim)
    alpha = -math.expm1(-dts * 2 * math.pi * pd.bandwidth)
    out, _ = lfilter([alpha], [1.0, alpha - 1.0], noisy, zi=[(1 - alpha) * current[0]])
    pos = out > 0
    out[pos] = out[pos] / (1 + (out[pos] / pd.saturation) ** 4) ** 0.25
    check.bit_generator.state = before
    assert np.array_equal(pd.detect(fields, dts, check, nsim), out)

    # the rest of OSC-1.acquire(): each input through the front end, every k-th bin;
    # CH2 reads FG-1's CH2 at the scope's own time
    ch1 = osc._front_end(out, dts)[warm::k][:n]
    ch2 = osc._front_end(fg.voltage("ch2", t), dts)[warm::k][:n]
    # a fresh copy's setup.run(): the first run the script's main block prints
    res = bench().run()["OSC-1"]
    assert not res.triggered
    assert np.array_equal(res["ch1"].data, ch1), "replay differs from setup.run()"
    assert np.array_equal(res["ch2"].data, ch2)

    # how CH1 follows the drive: the noiseless current through the same pole and front
    # end, and CH2, fitted to sines at the drive frequency
    f_rf = fg.ch1_freq
    t_s = t0 + np.arange(n) * dt
    clean, _ = lfilter(
        [alpha], [1.0, alpha - 1.0], current, zi=[(1 - alpha) * current[0]]
    )
    a1, p1, m1 = _sine(t_s, osc._front_end(clean, dts)[warm::k][:n], f_rf)
    a2, p2, _ = _sine(t_s, ch2, f_rf)
    # CH1 is inverted: its phase is CH2's plus π, less the lag
    lag = ((p2 - (p1 + math.pi)) / (2 * math.pi * f_rf)) % (1 / f_rf)
    # the drive is read at each bin's center, CH2 at its bins' starts; the pole's group
    # delay at 10 MHz is (1 − α)/α bins
    pole_delay = (1 - alpha) / alpha * dts
    assert abs(lag - (path.delay - dts / 2 + pole_delay)) < 1e-12

    returned = net.returned[ld]
    route = [c for c in calls if c["refl"] == 0]
    sl = slice(None, None, EVERY)
    z = slice(*ZOOM)
    rms_before = float(np.std((noisy - current)[warm:]))
    rms_after = float(np.std((out - current)[warm:]))
    return {
        "lines": lines(),
        "nums": {
            "seed": setup.seed,
            "power": ld.power,
            "power_dbm": ld.power_dbm,
            "wavelength": ld.wavelength,
            "linewidth": ld.linewidth,
            "t_mate": t_mate,
            "r_back": r_back,
            "cord_delay": through.delay,
            "cord_t": abs(through.amp) ** 2,
            "im_amp": in_im.amp,
            "il_db": im.insertion_loss_db,
            "path_delay": path.delay,
            "path_gain": abs(path.amp) ** 2,
            "path_amp": abs(path.amp),
            "mod_at": at,
            "n_paths": len(net.paths[pd]),
            "n_calls": len(calls),
            "max_hops": E.MAX_HOPS,
            "min_gain": E.MIN_GAIN,
            "min_twice": E.MIN_GAIN_TWICE_REFLECTED,
            "returned": [[where, w] for (own, where), w in returned.items()],
            "returned_total": sum(returned.values()),
            "port_power": dict(net.port_power),
            "gens": sorted(g.name for g in gens),
            "fs": fs,
            "n": n,
            "dt": dt,
            "k": k,
            "dts": dts,
            "warm": warm,
            "nsim": nsim,
            "t0": t0,
            "timebase": osc.timebase,
            "scope_bw": osc.bandwidth,
            "envelope": float(env[0, 0]),
            "phase_span": float(np.ptp(phi)),
            "phase_rms": math.sqrt(2 * math.pi * ld.linewidth * n * dt),
            "v_pi": im.v_pi,
            "contrast": im.contrast,
            "extinction_db": im.extinction_db,
            "phi0": im.phi0,
            "bias": float(v_bias[0]),
            "rf_vpp": fg.ch1_vpp,
            "rf_freq": f_rf,
            "v_min": float(volts[warm:].min()),
            "v_max": float(volts[warm:].max()),
            "t_min": float(trans[warm:].min()),
            "t_max": float(trans[warm:].max()),
            "p_min": float(np.min(np.abs(field[warm:]) ** 2)),
            "p_max": float(np.max(np.abs(field[warm:]) ** 2)),
            "p_mean": float(np.mean(np.abs(field[warm:]) ** 2)),
            "resp": pd.responsivity(ld.wavelength),
            "dark": pd.dark_current,
            "i_min": float(current[warm:].min()),
            "i_max": float(current[warm:].max()),
            "i_mean": float(current[warm:].mean()),
            "shot": float(2 * Q_E * current[warm:].mean()),
            "thermal": 4 * K_B * pd.temperature / pd.load,
            "load": pd.load,
            "temperature": pd.temperature,
            "saturation": pd.saturation,
            "pd_bw": pd.bandwidth,
            "alpha": alpha,
            "rms_before": rms_before,
            "rms_after": rms_after,
            "ch1_mean": float(np.mean(ch1)),
            "ch1_amp": a1,
            "ch1_fit_mean": m1,
            "ch2_amp": a2,
            "lag": lag,
            "pole_delay": pole_delay,
            "ch1_scale": osc.ch1_scale,
            "ch2_scale": osc.ch2_scale,
            "triggered": bool(res.triggered),
            "every": EVERY,
            "zoom": list(ZOOM),
        },
        "route": [
            {"port": c["port"], "w": c["w"], "delay": c["delay"], "mods": c["mods"]}
            for c in route
        ],
        "phase": _r(phi[0, sl] - phi[0, 0], 4),
        "volts": _r(volts[sl], 5),
        "trans": _r(trans[sl], 5),
        "p_uW": _r(np.abs(field[sl]) ** 2 * 1e6),
        "i_uA": _r(current[sl] * 1e6),
        "zoom_clean_uA": _r(current[z] * 1e6),
        "zoom_noisy_uA": _r(noisy[z] * 1e6),
        "zoom_out_uA": _r(out[z] * 1e6),
        "scope_t_ns": _r((res.t[::5]) * 1e9, 6),
        "ch1_uA": _r(ch1[::5] * 1e6),
        "ch2_V": _r(ch2[::5]),
    }
