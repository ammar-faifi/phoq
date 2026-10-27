"""Data for the photon-counter walkthrough: PhotonCounter.acquire and the single-photon
detector's detect, photons, click_probability, dead_bins and blind_after, replayed line
by line on a weak pulsed laser → 50:50 → two SPDs bench, every intermediate array kept.
The replay is checked against acquire() itself. The run is cut into two chunks, and the
seed puts a click inside the dead time carried across the cut."""

import math

import numpy as np

from phoq import Attenuator, Coupler, Laser, PhotonCounter, Setup, SinglePhotonDetector
from phoq import instruments as I
from phoq.engine import synthesize, trace
from phoq.units import photon_energy

SEED = 18
REFS = {
    "acquire": "phoq.instruments:PhotonCounter.acquire",
    "detect": "phoq.detectors:SinglePhotonDetector.detect",
    "photons": "phoq.detectors:SinglePhotonDetector.photons",
    "click_probability": "phoq.detectors:SinglePhotonDetector.click_probability",
    "dead_bins": "phoq.detectors:SinglePhotonDetector.dead_bins",
    "blind_after": "phoq.detectors:SinglePhotonDetector.blind_after",
    "counts": "phoq.instruments:CounterResult.counts",
    "coincidences": "phoq.instruments:CounterResult.coincidences",
}


def bench():
    s = Setup("Tagger demo", seed=3)
    ld = s.add(
        Laser(
            "LD-1", mode="pulsed", rep_rate=100e6, pulse_width=100e-12, power_dbm=-20.0
        )
    )
    voa = s.add(Attenuator("VOA-1", attenuation_db=60.0))
    bs = s.add(Coupler("BS-1"))
    spd = dict(efficiency=0.25, dark_count_rate=1e3, dead_time=100e-9)
    d1 = s.add(SinglePhotonDetector("SPD-1", **spd))
    d2 = s.add(SinglePhotonDetector("SPD-2", **spd))
    cnt = s.add(PhotonCounter("CNT-1", slots=2000, chunk=1000))
    s.connect(ld.out, voa.p1)
    s.connect(voa.p2, bs.a1)
    s.connect(bs.b1, d1.inp)
    s.connect(bs.b2, d2.inp)
    s.connect(d1.out, cnt.ch1)
    s.connect(d2.out, cnt.ch2)
    s.terminate_spares()
    return s


def capture() -> dict:
    s = bench()
    cnt = s["CNT-1"]
    net = trace(s)
    rng = np.random.default_rng(SEED)

    # acquire()
    inputs = {}
    for i in range(1, 5):
        peer = cnt.ports[f"ch{i}"].peer
        if peer is not None and isinstance(peer.component, SinglePhotonDetector):
            inputs[f"ch{i}"] = peer.component
    first = cnt.position
    laser = I._sync_laser(net, list(inputs.values()), cnt.sync_source)
    rate = cnt.bin_rate or laser.rep_rate
    dt = 1.0 / rate
    delay = {d: getattr(cnt, f"{ch}_delay") for ch, d in inputs.items()}
    shift = min(t + delay[d] for d in inputs.values() for t in net.delays(d, laser))
    offsets = {d: -tau for d, tau in delay.items()}
    clicks = {ch: np.zeros(cnt.slots, dtype=bool) for ch in inputs}
    blind = {ch: 0 for ch in inputs}  # the first record carries no dead time in
    m = cnt.oversample
    step = max(1, cnt.chunk // m)
    p_all = {ch: np.zeros(cnt.slots) for ch in inputs}
    nbar_all = {ch: np.zeros(cnt.slots) for ch in inputs}
    u_all = {ch: np.zeros(cnt.slots) for ch in inputs}
    raw_all = {ch: np.zeros(cnt.slots, dtype=bool) for ch in inputs}
    blind_log = []
    for a in range(0, cnt.slots, step):
        b = min(cnt.slots, a + step)
        t = shift + (first + np.arange(a, b)) * dt - dt / 2
        fields = synthesize(net, set(inputs.values()), t, dt / m, rng, offsets)
        for ch, d in inputs.items():  # SinglePhotonDetector.detect, line by line
            nbar = d.photons(fields[d], dt / m, (b - a) * m, weighted=True)
            p = d.click_probability(nbar, dt)
            u = rng.random(b - a)
            raw = u < p
            dead = d.dead_bins(dt)
            keep, last = [], blind[ch] - dead
            for i in np.flatnonzero(raw):
                if i - last >= dead:
                    keep.append(i)
                    last = i
            c = np.zeros(b - a, dtype=bool)
            c[keep] = True
            clicks[ch][a:b] = c
            nbar_all[ch][a:b], p_all[ch][a:b] = nbar, p
            u_all[ch][a:b], raw_all[ch][a:b] = u, raw
            before = blind[ch]
            blind[ch] = d.blind_after(c, dt, blind[ch])
            blind_log.append({"ch": ch, "a": a, "b": b, "in": before, "out": blind[ch]})

    s2 = bench()
    res = s2["CNT-1"].acquire(trace(s2), np.random.default_rng(SEED))
    for ch in inputs:
        assert np.array_equal(
            res.clicks[ch], clicks[ch]
        ), "replay differs from acquire()"

    d1 = inputs["ch1"]
    D = d1.dead_bins(dt)
    p1, p2 = float(p_all["ch1"].mean()), float(p_all["ch2"].mean())
    q1, q2 = p1 / (1 + p1 * (D - 1)), p2 / (1 + p2 * (D - 1))  # kept per bin
    ld = s["LD-1"]
    return {
        "nums": {
            "rate": rate,
            "dt": dt,
            "slots": cnt.slots,
            "chunk": cnt.chunk,
            "step": step,
            "m": m,
            "first": first,
            "position_after": first + cnt.slots,
            "shift": shift,
            "p_dark": -math.expm1(-d1.dark_count_rate * dt),
            "dcr": d1.dark_count_rate,
            "eff": d1.efficiency,
            "dead_time": d1.dead_time,
            "dead_bins": D,
            "hnu": photon_energy(ld.wavelength),
            "laser_dbm": ld.power_dbm,
            "voa_db": s["VOA-1"].attenuation_db,
            "fwhm": ld.pulse_width,
            "nbar1": float(nbar_all["ch1"].mean()),
            "nbar2": float(nbar_all["ch2"].mean()),
            "p1": p1,
            "p2": p2,
            "q1": q1,
            "q2": q2,
            "raw": {ch: int(r.sum()) for ch, r in raw_all.items()},
            "counts": res.counts,
            "coinc": res.coincidences("ch1", "ch2"),
            "expect_coinc": q1 * q2 * cnt.slots,
            "blind": blind_log,
            "seed": SEED,
            "mu_laser": ld.power / (photon_energy(ld.wavelength) * ld.rep_rate),
        },
        "u1": [float(f"{x:.4g}") for x in u_all["ch1"]],
        "u2": [float(f"{x:.4g}") for x in u_all["ch2"]],
        "raw1": [int(i) for i in np.flatnonzero(raw_all["ch1"])],
        "raw2": [int(i) for i in np.flatnonzero(raw_all["ch2"])],
        "c1": [int(i) for i in np.flatnonzero(clicks["ch1"])],
        "c2": [int(i) for i in np.flatnonzero(clicks["ch2"])],
    }
