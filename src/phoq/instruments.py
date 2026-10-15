"""Measurement instruments. Each has ``acquire(network, rng)`` returning a result object."""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter

from .core import Component, Param
from .detectors import Photodiode, SinglePhotonDetector
from .engine import Network, driving_generators, synthesize
from .generators import DCPowerSupply, FunctionGenerator
from .sources import Laser
from .units import fmt_si, seq125

TIMEBASES = seq125(1e-9, 10e-3)
SCALES = seq125(1e-9, 10.0)
CH_COLORS = ("#facc15", "#22d3ee", "#f472b6", "#4ade80")


def _ceil125(x: float, table=SCALES) -> float:
    return next((v for v in table if v >= x), table[-1])


def _sync_laser(net: Network, dets, name: str) -> Laser | None:
    lasers = [p.source for d in dets for p in net.paths.get(d, [])]
    if name:
        lasers = [
            p.source
            for paths in net.paths.values()
            for p in paths
            if p.source.name == name
        ] or lasers
    modulated = [L for L in lasers if L.period]
    if modulated:
        return modulated[0]
    if name and any(L.name == name for L in lasers):
        return next(L for L in lasers if L.name == name)
    return None


def measure(data: np.ndarray, dt: float) -> dict:
    """Mean, extremes, RMS and the frequency of a trace.

    A clean, well-sampled signal is timed by hysteretic mid-level crossings; a coarsely sampled one
    (where sample-to-sample steps look like noise) by its spectral peak, which is also what an
    aliased signal shows. Flat noise reports no frequency.
    """
    mn, mx = float(data.min()), float(data.max())
    n = data.size
    out = {
        "mean": float(data.mean()),
        "min": mn,
        "max": mx,
        "pk_pk": mx - mn,
        "rms": float(data.std()),
        "freq": None,
    }
    if n < 16 or mx <= mn:
        return out
    noise = float(np.median(np.abs(np.diff(data)))) / (0.6745 * math.sqrt(2))
    if mx - mn > 8 * noise:
        mid, h = (mx + mn) / 2, (mx - mn) * 0.2
        lo, hi = data < mid - h, data > mid + h
        rises, armed = [], False
        for i in np.flatnonzero(lo | hi):
            if lo[i]:
                armed = True
            elif armed:
                rises.append(i)
                armed = False
        if len(rises) >= 2:
            out["freq"] = (len(rises) - 1) / ((rises[-1] - rises[0]) * dt)
            return out
    spec = np.abs(np.fft.rfft(data - data.mean()))
    spec[0] = 0.0
    k = int(np.argmax(spec))
    if 0 < k < spec.size - 1 and spec[k] ** 2 > 0.2 * np.sum(
        spec**2
    ):  # one line dominates: periodic
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        denom = a - 2 * b + c
        out["freq"] = (k + (0.5 * (a - c) / denom if denom else 0.0)) / (n * dt)
    return out


# ---------------------------------------------------------------------------------------------
@dataclass
class Trace:
    channel: str
    source: str | None
    unit: str
    data: np.ndarray | None
    scale: float
    offset: float
    enabled: bool
    meas: dict = field(default_factory=dict)


@dataclass
class ScopeResult:
    instrument: str
    t: np.ndarray
    dt: float
    timebase: float
    trigger: str
    triggered: bool
    channels: dict[str, Trace]
    trigger_source: str = ""
    trigger_level: float = 0.0
    fs: float = 0.0  # effective sample rate [S/s]
    bandwidth: float = 0.0  # analogue front-end bandwidth [Hz]

    def __getitem__(self, ch: str) -> Trace:
        return self.channels[ch]

    def summary(self) -> str:
        lines = [
            f"{self.instrument}: {fmt_si(self.timebase, 's')}/div, {fmt_si(self.fs, 'S/s')}, BW {fmt_si(self.bandwidth, 'Hz')}, "
            f"trigger {self.trigger}{'' if self.triggered else ' (untriggered)'}"
        ]
        for tr in self.channels.values():
            if tr.data is None:
                continue
            m = tr.meas
            f = f", f {fmt_si(m['freq'], 'Hz')}" if m.get("freq") else ""
            lines.append(
                f"  {tr.channel} ← {tr.source}: mean {fmt_si(m['mean'], tr.unit)}, pk-pk {fmt_si(m['pk_pk'], tr.unit)}{f}"
            )
        return "\n".join(lines)

    def plot(self, ax=None):
        """Matplotlib plot of the enabled traces (one y-axis per unit)."""
        import matplotlib.pyplot as plt

        ax = ax or plt.subplots(figsize=(8, 3.6))[1]
        scale, unit = next(
            (
                (s, u)
                for s, u in ((1e-3, "ms"), (1e-6, "µs"), (1e-9, "ns"))
                if self.timebase >= s
            ),
            (1e-12, "ps"),
        )
        for i, tr in enumerate(self.channels.values()):
            if tr.data is not None and tr.enabled:
                ax.plot(
                    self.t / scale,
                    tr.data,
                    color=CH_COLORS[i],
                    lw=1.2,
                    label=f"{tr.channel}: {tr.source} [{tr.unit}]",
                )
        ax.set(
            xlabel=f"time from trigger [{unit}]", ylabel="signal", title=self.instrument
        )
        ax.legend(loc="upper right", fontsize=8)
        return ax

    def to_json(self) -> dict:
        def b64(a):
            return base64.b64encode(np.asarray(a, dtype="<f4").tobytes()).decode()

        return {
            "instrument": self.instrument,
            "t0": float(self.t[0]),
            "dt": self.dt,
            "n": int(self.t.size),
            "timebase": self.timebase,
            "trigger": self.trigger,
            "triggered": self.triggered,
            "trigger_source": self.trigger_source,
            "trigger_level": self.trigger_level,
            "fs": self.fs,
            "bandwidth": self.bandwidth,
            "points": int(self.t.size),
            "channels": [
                {
                    "channel": tr.channel,
                    "source": tr.source,
                    "unit": tr.unit,
                    "scale": tr.scale,
                    "offset": tr.offset,
                    "enabled": tr.enabled,
                    "meas": tr.meas,
                    "data": b64(_for_display(tr.data)) if tr.data is not None else None,
                }
                for tr in self.channels.values()
            ],
        }


def _for_display(data: np.ndarray, max_pts: int = 2000) -> np.ndarray:
    """Long records go to the screen as a min/max envelope, like a scope's display decimation."""
    n = data.size
    if n <= max_pts:
        return data
    cols = max_pts // 2
    m = n // cols
    blocks = data[: cols * m].reshape(cols, m)
    out = np.empty(2 * cols)
    out[0::2], out[1::2] = blocks.min(axis=1), blocks.max(axis=1)
    return out


class Oscilloscope(Component):
    """Four-channel digital oscilloscope. Connect detector outputs (``pd.rf``, ``spd.out``) to ch1…ch4."""

    category = "instrument"
    prefix = "OSC"
    label = "Oscilloscope"
    description = "4 channels, sync / edge / free-run trigger"
    inputs = ("ch1", "ch2", "ch3", "ch4")

    running = Param(True, label="Run")
    timebase = Param(
        20e-9,
        unit="s",
        label="Time / div",
        min=1e-12,
        max=10.0,
        presets=TIMEBASES,
        log=True,
    )
    h_position = Param(0.0, unit="s", label="Horizontal position", min=-10.0, max=10.0)
    trigger = Param("sync", choices=("sync", "edge", "free"))
    trigger_source = Param(
        "",
        label="Trigger source",
        doc="sync: laser name (empty = auto); edge: ch1…ch4 (empty = ch1)",
    )
    trigger_level = Param(0.5e-3, label="Edge trigger level", min=-1e3, max=1e3)
    bandwidth = Param(
        1e9,
        unit="Hz",
        label="Analogue bandwidth",
        min=1e6,
        max=50e9,
        log=True,
        presets=(20e6, 100e6, 200e6, 500e6, 1e9, 2e9, 4e9, 8e9),
        doc="Front-end low-pass (−3 dB)",
    )
    sample_rate = Param(
        5e9,
        unit="S/s",
        label="Sample rate (max)",
        min=1e3,
        max=50e9,
        log=True,
        presets=seq125(1e6, 20e9),
        doc="The record memory caps it at slow timebases: effective rate = min(this, max_points / window)",
    )
    max_points = Param(
        20_000,
        label="Record memory (points)",
        min=100,
        max=200_000,
        presets=(1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000, 200_000),
    )
    ch1_scale = Param(
        1e-3, label="CH1 scale / div", min=1e-12, max=1e3, presets=SCALES, log=True
    )
    ch2_scale = Param(
        1e-3, label="CH2 scale / div", min=1e-12, max=1e3, presets=SCALES, log=True
    )
    ch3_scale = Param(
        1e-3, label="CH3 scale / div", min=1e-12, max=1e3, presets=SCALES, log=True
    )
    ch4_scale = Param(
        1e-3, label="CH4 scale / div", min=1e-12, max=1e3, presets=SCALES, log=True
    )
    ch1_offset = Param(-3.0, unit="div", label="CH1 offset", min=-8.0, max=8.0)
    ch2_offset = Param(-3.0, unit="div", label="CH2 offset", min=-8.0, max=8.0)
    ch3_offset = Param(-3.0, unit="div", label="CH3 offset", min=-8.0, max=8.0)
    ch4_offset = Param(-3.0, unit="div", label="CH4 offset", min=-8.0, max=8.0)
    ch1_enabled = Param(True, label="CH1 on")
    ch2_enabled = Param(True, label="CH2 on")
    ch3_enabled = Param(True, label="CH3 on")
    ch4_enabled = Param(True, label="CH4 on")

    def __init__(self, name: str | None = None, **params):
        self.armed = False  # single shot: waiting for the first trigger
        self.held: ScopeResult | None = (
            None  # the sweep captured by the last single shot
        )
        super().__init__(name, **params)

    def _build(self):
        for i in range(1, 5):
            self.add_port(f"ch{i}", "electrical", "in", f"CH{i} input")

    def _param_changed(self, name: str) -> None:
        if name == "running":  # RUN or STOP from the panel cancels a single shot
            self.armed = False
            if self.running:
                self.held = None
        super()._param_changed(name)

    def ui_fields(self):
        f = [
            "running",
            "timebase",
            "h_position",
            "bandwidth",
            "sample_rate",
            "max_points",
            "trigger",
            "trigger_source",
        ]
        if self.trigger == "edge":
            f.append("trigger_level")
        for i in range(1, 5):
            if self.ports[f"ch{i}"].peer is not None:
                f += [f"ch{i}_scale", f"ch{i}_offset", f"ch{i}_enabled"]
        return f

    def _inputs(self):
        """Channel → (source component, its output port name) for every usable input."""
        out = {}
        for i in range(1, 5):
            peer = self.ports[f"ch{i}"].peer
            src = peer.component if peer is not None else None
            out[f"ch{i}"] = (
                (src, peer.name)
                if isinstance(
                    src,
                    (
                        Photodiode,
                        SinglePhotonDetector,
                        FunctionGenerator,
                        DCPowerSupply,
                    ),
                )
                else None
            )
        return out

    def _detectors(self, inputs):
        return [
            src
            for src, _ in inputs.values()
            if isinstance(src, (Photodiode, SinglePhotonDetector))
        ]

    def record(self) -> tuple[float, int, float]:
        """(effective sample rate, record length, sample interval) for the current settings."""
        window = 10 * self.timebase
        fs = min(self.sample_rate, self.max_points / window)
        n = max(100, int(round(fs * window)))
        return n / window, n, window / n

    def _front_end(self, sig: np.ndarray, dt: float) -> np.ndarray:
        """Analogue input bandwidth: two cascaded poles, −3 dB at ``bandwidth``."""
        if self.bandwidth >= 0.45 / dt:
            return sig
        alpha = -math.expm1(-dt * 2 * math.pi * 1.554 * self.bandwidth)
        for _ in range(2):
            sig, _ = lfilter(
                [alpha], [1.0, alpha - 1.0], sig, zi=[(1 - alpha) * sig[0]]
            )
        return sig

    def acquire(self, net: Network, rng: np.random.Generator) -> ScopeResult:
        """One sweep.

        The analogue signal is simulated finer than the sample interval (enough to resolve the
        front-end bandwidth), low-passed by the bandwidth, then point-sampled at the effective
        sample rate — so a rate below twice the signal bandwidth aliases, as on a real scope.
        """
        inputs = {ch: src for ch, src in self._inputs().items() if src is not None}
        dets = self._detectors(inputs)
        fs, n, dt = self.record()
        tb = self.timebase
        k = max(
            1, min(math.ceil(6 * self.bandwidth * dt), max(1, 200_000 // n))
        )  # analogue oversampling
        dts = dt / k
        edge = self.trigger == "edge"
        total = 2 * n if edge else n
        if self.trigger == "sync":
            laser = _sync_laser(net, dets, self.trigger_source)
            T = laser.period if laser else None
            t_trig = rng.integers(0, 1000) * T if T else rng.uniform(0, 1e-3)
            t0 = t_trig + self.h_position - tb
            triggered = bool(T)
        else:
            t_trig = t0 = rng.uniform(0, 1e-3) + self.h_position
            triggered = False
        settle = [d.settle_time for d in dets if isinstance(d, Photodiode)] + [
            6 / (2 * math.pi * self.bandwidth)
        ]
        warm = min(4000, math.ceil(max(settle) / dts))
        t_sim = t0 + np.arange(-warm, total * k) * dts
        fields = synthesize(net, set(dets), t_sim, dts, rng)
        driving = driving_generators(net, dets)
        signals = {}
        for ch, (d, port) in inputs.items():
            if isinstance(d, Photodiode):
                raw = d.detect(fields[d], dts, rng, t_sim.size)
            elif isinstance(d, SinglePhotonDetector):
                raw = d.waveform(d.detect(fields[d], dts, rng, t_sim.size), dts)
            else:  # a generator or supply output viewed directly
                if d not in driving:
                    d.start(rng)
                raw = d.voltage(port, t_sim)
            signals[ch] = self._front_end(raw, dts)[warm::k][:total]
        t_s = t0 + np.arange(total) * dt
        start = 0
        if edge:
            src = self.trigger_source or "ch1"
            sig, pre = signals.get(src), n // 10
            if sig is not None:
                lv = self.trigger_level
                hits = np.flatnonzero(
                    (sig[pre + 1 : n + pre] >= lv) & (sig[pre : n + pre - 1] < lv)
                )
                if hits.size:
                    start, triggered = int(hits[0]) + 1, True
                    t_trig = t_s[start + pre]
        tt = t_s[start : start + n] - t_trig  # time relative to the trigger
        channels = {}
        for i in range(1, 5):
            ch = f"ch{i}"
            d = inputs[ch][0] if ch in inputs else None
            data = signals[ch][start : start + n] if ch in signals else None
            channels[ch] = Trace(
                ch,
                d.name if d else None,
                d.signal_unit if d else "",
                data,
                getattr(self, f"ch{i}_scale"),
                getattr(self, f"ch{i}_offset"),
                getattr(self, f"ch{i}_enabled"),
                measure(data, dt) if data is not None else {},
            )
        return ScopeResult(
            self.name,
            tt,
            dt,
            tb,
            self.trigger,
            triggered,
            channels,
            (self.trigger_source or "ch1") if edge else self.trigger_source,
            self.trigger_level,
            fs,
            self.bandwidth,
        )

    # ---- single shot --------------------------------------------------------------------------------
    def arm(self) -> None:
        """Stop free running and wait for the next trigger; the first triggered sweep is then held."""
        self.running = False
        self.held = None
        self.armed = True
        self._param_changed("armed")

    def disarm(self) -> None:
        """Cancel a single shot that is still waiting (the scope stays stopped)."""
        self.armed = False
        self._param_changed("armed")

    def single(
        self, net: Network, rng: np.random.Generator, attempts: int = 100
    ) -> ScopeResult | None:
        """Arm, wait for the first trigger and hold that sweep (``running`` stays False).

        Each attempt looks at one window of signal; a sync or free-run trigger fires on the first,
        an edge trigger whenever the source crosses the level. Returns None if no trigger came.
        """
        if not self.armed:
            self.arm()
        res = None
        for _ in range(attempts):
            res = self.acquire(net, rng)
            if res.triggered or self.trigger == "free":
                self.held, self.armed = res, False
                self._param_changed("armed")
                return res
        return None

    @property
    def mode(self) -> str:
        if self.armed:
            return "wait (single shot)"
        if self.held is not None and not self.running:
            return "hold (single shot)"
        return "run" if self.running else "stop"

    def info(self):
        return {"Mode": self.mode}

    def autoset(self, net: Network, rng: np.random.Generator) -> None:
        """Pick timebase, trigger and channel scales from the incoming signals."""
        laser = _sync_laser(net, self._detectors(self._inputs()), "")
        self.set(
            trigger="sync",
            trigger_source="",
            h_position=0.0,
            timebase=(
                _ceil125(2 * laser.period / 10 * 0.999, TIMEBASES) if laser else 1e-6
            ),
        )
        res = self.acquire(net, rng)
        for i, tr in enumerate(res.channels.values(), start=1):
            if tr.data is not None:
                peak = max(float(np.max(np.abs(tr.data))), 1e-12)
                self.set(
                    **{
                        f"ch{i}_scale": _ceil125(peak / 6),
                        f"ch{i}_offset": -3.0,
                        f"ch{i}_enabled": True,
                    }
                )
        first = next((tr for tr in res.channels.values() if tr.data is not None), None)
        if first is not None:
            self.trigger_level = float(np.max(first.data)) * 0.5


# ---------------------------------------------------------------------------------------------
@dataclass
class CounterResult:
    """One batch of time-tagger data: a click record per channel, ``slots`` bins at ``rate``.

    ``start`` is the index of the first bin in the instrument's continuous time base, so
    :meth:`timestamps` and :meth:`events` give absolute times that run on from batch to batch.
    """

    instrument: str
    rate: float
    slots: int
    clicks: dict[str, np.ndarray]  # channel → boolean record, one entry per time bin
    sources: dict[str, str | None]
    start: int = 0  # first bin index (continuous across batches)
    seq: int = 0  # batch number, set by the session streaming it

    @property
    def duration(self) -> float:
        return self.slots / self.rate

    @property
    def t0(self) -> float:
        """Absolute time of the first bin [s]."""
        return self.start / self.rate

    def timestamps(self, channel: str) -> np.ndarray:
        """Absolute click times [s] on a channel (bin k is centred on t = k / rate)."""
        return (self.start + np.flatnonzero(self.clicks[channel])) / self.rate

    def events(self) -> np.ndarray:
        """Every click as a time-ordered structured array with fields ``t`` [s] and ``ch`` (1-based)."""
        parts = []
        for ch, rec in self.clicks.items():
            idx = np.flatnonzero(rec)
            ev = np.empty(idx.size, dtype=[("t", "f8"), ("ch", "i2")])
            ev["t"], ev["ch"] = (self.start + idx) / self.rate, int(ch[2:])
            parts.append(ev)
        out = (
            np.concatenate(parts)
            if parts
            else np.empty(0, dtype=[("t", "f8"), ("ch", "i2")])
        )
        return out[np.argsort(out["t"], kind="stable")]

    @property
    def counts(self) -> dict[str, int]:
        return {ch: int(c.sum()) for ch, c in self.clicks.items()}

    @property
    def rates(self) -> dict[str, float]:
        return {ch: n / self.duration for ch, n in self.counts.items()}

    def coincidences(self, a: str, b: str) -> int:
        return int(np.count_nonzero(self.clicks[a] & self.clicks[b]))

    def summary(self) -> str:
        lines = [
            f"{self.instrument}: {self.slots:,} bins at {fmt_si(self.rate, 'Hz')} ({fmt_si(self.duration, 's')})"
        ]
        for ch, n in self.counts.items():
            lines.append(
                f"  {ch} ← {self.sources[ch]}: {n:,} clicks, {fmt_si(n / self.duration, 'Hz')}, p = {n / self.slots:.3e}"
            )
        chs = list(self.clicks)
        for i in range(len(chs)):
            for j in range(i + 1, len(chs)):
                lines.append(
                    f"  coincidences {chs[i]}·{chs[j]}: {self.coincidences(chs[i], chs[j]):,}"
                )
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "instrument": self.instrument,
            "rate": self.rate,
            "slots": self.slots,
            "duration": self.duration,
            "counts": self.counts,
            "rates": self.rates,
            "sources": self.sources,
            "start": self.start,
            "t0": self.t0,
            "seq": self.seq,
        }


class PhotonCounter(Component):
    """Time tagger / counting module: one time bin per laser pulse, records every click."""

    category = "instrument"
    prefix = "CNT"
    label = "Photon counter"
    description = "Time tagger, records clicks per pulse"
    inputs = ("ch1", "ch2", "ch3", "ch4")

    running = Param(True, label="Run")
    slots = Param(100_000, label="Time bins per run", min=1, max=100_000_000)
    bin_rate = Param(
        0.0,
        unit="Hz",
        label="Bin rate",
        min=0.0,
        max=50e9,
        doc="0 = repetition rate of the laser feeding the detectors",
    )
    sync_source = Param("", label="Sync laser")
    compensate_delay = Param(
        True,
        label="Delay compensation",
        doc="Shift the bins by the time of flight so bin k holds pulse k of the sync laser",
    )
    chunk = Param(1_000_000, label="Bins per chunk", min=1000, max=100_000_000)

    def _build(self):
        for i in range(1, 5):
            self.add_port(f"ch{i}", "electrical", "in", f"CH{i} input")

    def ui_fields(self):
        return ["running", "slots", "bin_rate", "sync_source", "compensate_delay"]

    def __init__(self, name: str | None = None, **params):
        self.position = 0  # next bin index: acquisitions run on from one another like a tagger's clock
        super().__init__(name, **params)

    def acquire(
        self, net: Network, rng: np.random.Generator, start: int | None = None
    ) -> CounterResult:
        """Record ``slots`` bins. By default each call continues where the previous one ended."""
        inputs = {}
        for i in range(1, 5):
            peer = self.ports[f"ch{i}"].peer
            if peer is not None and isinstance(peer.component, SinglePhotonDetector):
                inputs[f"ch{i}"] = peer.component
        first = self.position if start is None else start
        self.position = first + self.slots
        laser = _sync_laser(net, list(inputs.values()), self.sync_source)
        rate = self.bin_rate or (laser.rep_rate if laser else 1e6)
        dt = 1.0 / rate
        shift = 0.0
        if self.compensate_delay and laser is not None:
            delays = [t for d in inputs.values() for t in net.delays(d, laser)]
            shift = min(delays) if delays else 0.0
        clicks = {ch: np.zeros(self.slots, dtype=bool) for ch in inputs}
        for a in range(0, self.slots, self.chunk):
            b = min(self.slots, a + self.chunk)
            t = (
                shift + (first + np.arange(a, b)) * dt - dt / 2
            )  # bins centred on the pulses
            fields = synthesize(net, set(inputs.values()), t, dt, rng)
            for ch, d in inputs.items():
                clicks[ch][a:b] = d.detect(fields[d], dt, rng, b - a)
        return CounterResult(
            self.name,
            rate,
            self.slots,
            clicks,
            {ch: d.name for ch, d in inputs.items()},
            start=first,
        )
