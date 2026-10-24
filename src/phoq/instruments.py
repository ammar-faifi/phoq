"""Measurement instruments. Each has ``acquire(network, rng)`` returning a result
object."""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter, resample_poly

from .core import Component, Param
from .detectors import BalancedPhotodetector, Photodiode, SinglePhotonDetector
from .engine import Network, driving_generators, synthesize
from .generators import DCPowerSupply, FunctionGenerator
from .sources import Laser
from .units import fmt_si, seq125

TIMEBASES = seq125(1e-9, 10e-3)
SCALES = seq125(1e-9, 10.0)
DIV_Y = 8  # vertical divisions on the scope's screen, which its ADC spans
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

    A clean, well-sampled signal is timed by hysteretic mid-level crossings; a coarsely
    sampled one (where sample-to-sample steps look like noise) by its spectral peak,
    which is also what an aliased signal shows. Flat noise reports no frequency.
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


# --------------------------------------------------------------------------------------
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
    bandwidth: float = 0.0  # analog front-end bandwidth [Hz]
    adc_bits: int = 0  # vertical resolution; 0 = ideal

    def __getitem__(self, ch: str) -> Trace:
        return self.channels[ch]

    def codes(self, ch: str) -> np.ndarray:
        """A channel's ADC codes, 0 … 2^adc_bits − 1 from the bottom of the screen to
        the top."""
        if not self.adc_bits:
            raise ValueError(f"{self.instrument}: no ADC resolution set (adc_bits = 0)")
        tr = self.channels[ch]
        return np.rint(
            (tr.data / tr.scale + tr.offset + DIV_Y / 2)
            / DIV_Y
            * (2**self.adc_bits - 1)
        ).astype(np.int64)

    def summary(self) -> str:
        lines = [
            f"{self.instrument}: {fmt_si(self.timebase, 's')}/div,"
            f" {fmt_si(self.fs, 'S/s')}, BW {fmt_si(self.bandwidth, 'Hz')}, trigger"
            f" {self.trigger}{'' if self.triggered else ' (untriggered)'}"
        ]
        for tr in self.channels.values():
            if tr.data is None:
                continue
            m = tr.meas
            f = f", f {fmt_si(m['freq'], 'Hz')}" if m.get("freq") else ""
            lines.append(
                f"  {tr.channel} ← {tr.source}: mean {fmt_si(m['mean'], tr.unit)},"
                f" pk-pk {fmt_si(m['pk_pk'], tr.unit)}{f}"
            )
        return "\n".join(lines)

    def plot(self, ax=None):
        """Matplotlib plot of the enabled traces (one y-axis per unit), in the
        dark_background style."""
        import matplotlib.pyplot as plt

        with plt.style.context("dark_background"):
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
                xlabel=f"time from trigger [{unit}]",
                ylabel="signal",
                title=self.instrument,
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
    """Long records go to the screen as a min/max envelope, like a scope's display
    decimation."""
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
    """Four-channel digital oscilloscope. Connect detector outputs
    (``pd.rf``, ``spd.out``) to ch1…ch4."""

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
        label="Analog bandwidth",
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
        doc=(
            "The record memory caps it at slow timebases: effective rate = min(this,"
            " max_points / window)"
        ),
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
    adc_bits = Param(
        0,
        label="ADC resolution (bits)",
        min=0,
        max=16,
        presets=(0, 8, 10, 12, 14, 16),
        doc=(
            "Vertical resolution across the 8-division screen: a sample off the screen"
            " reads the end code. 0 = ideal, no quantization"
        ),
    )

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
            "adc_bits",
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
            if peer is None:
                continue
            src = peer.component
            out[f"ch{i}"] = (
                (src, peer.name)
                if isinstance(
                    src,
                    (
                        Photodiode,
                        BalancedPhotodetector,
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
            if isinstance(
                src, (Photodiode, BalancedPhotodetector, SinglePhotonDetector)
            )
        ]

    def record(self) -> tuple[float, int, float]:
        """(effective sample rate, record length, sample interval) for the current
        settings."""
        window = 10 * self.timebase
        fs = min(self.sample_rate, self.max_points / window)
        n = max(100, int(round(fs * window)))
        return n / window, n, window / n

    def _digitize(self, v: np.ndarray, scale: float, offset: float) -> np.ndarray:
        """The ADC: 2^bits levels across the screen (±4 div about the offset); off the
        screen, the end codes. Returns the voltage each code stands for."""
        top = 2**self.adc_bits - 1
        code = np.clip(np.rint((v / scale + offset + DIV_Y / 2) / DIV_Y * top), 0, top)
        return (code / top * DIV_Y - DIV_Y / 2 - offset) * scale

    def _front_end(self, sig: np.ndarray, dt: float) -> np.ndarray:
        """Analog input bandwidth: two cascaded poles, −3 dB at ``bandwidth``."""
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

        The analog signal is simulated finer than the sample interval (enough to
        resolve the front-end bandwidth), low-passed by the bandwidth, then
        point-sampled at the effective sample rate — so a rate below twice the signal
        bandwidth aliases, as on a real scope.
        """
        inputs = {ch: src for ch, src in self._inputs().items() if src is not None}
        dets = self._detectors(inputs)
        fs, n, dt = self.record()
        tb = self.timebase
        k = max(
            1, min(math.ceil(6 * self.bandwidth * dt), max(1, 200_000 // n))
        )  # analog oversampling
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
        settle = [
            d.settle_time
            for d in dets
            if isinstance(d, (Photodiode, BalancedPhotodetector))
        ] + [6 / (2 * math.pi * self.bandwidth)]
        warm = min(4000, math.ceil(max(settle) / dts))
        t_sim = t0 + np.arange(-warm, total * k) * dts
        fields = synthesize(net, set(dets), t_sim, dts, rng)
        driving = driving_generators(net, dets)
        signals, balanced = {}, {}
        for ch, (d, port) in inputs.items():
            if isinstance(
                d, BalancedPhotodetector
            ):  # its outputs share one noise realization
                if d not in balanced:
                    balanced[d] = d.detect(fields[d], dts, rng, t_sim.size)
                raw = balanced[d][port]  # the scope's input is high-impedance
            elif isinstance(d, Photodiode):
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
            if data is not None and self.adc_bits:
                data = self._digitize(
                    data, getattr(self, f"ch{i}_scale"), getattr(self, f"ch{i}_offset")
                )
            channels[ch] = Trace(
                ch,
                (
                    (
                        f"{d.name}.{inputs[ch][1]}"
                        if isinstance(d, BalancedPhotodetector)
                        else d.name
                    )
                    if d
                    else None
                ),
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
            self.adc_bits,
        )

    # ---- single shot -----------------------------------------------------------------
    def arm(self) -> None:
        """Stop free running and wait for the next trigger; the first triggered sweep is
        then held."""
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
        """Arm, wait for the first trigger and hold that sweep
        (``running`` stays False).

        Each attempt looks at one window of signal; a sync or free-run trigger fires on
        the first, an edge trigger whenever the source crosses the level. Returns None
        if no trigger came.
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


# --------------------------------------------------------------------------------------
@dataclass
class CounterResult:
    """One batch of time-tagger data: a click record per channel, ``slots`` bins at
    ``rate``.

    ``start`` is the index of the first bin in the instrument's continuous time base, so
    :meth:`timestamps` and :meth:`events` give absolute times that run on from batch to
    batch.
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
        """Absolute click times [s] on a channel (bin k is centered on t = k / rate)."""
        return (self.start + np.flatnonzero(self.clicks[channel])) / self.rate

    def events(self) -> np.ndarray:
        """Every click as a time-ordered structured array with fields ``t`` [s] and
        ``ch`` (1-based)."""
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
            f"{self.instrument}: {self.slots:,} bins at {fmt_si(self.rate, 'Hz')}"
            f" ({fmt_si(self.duration, 's')})"
        ]
        for ch, n in self.counts.items():
            lines.append(
                f"  {ch} ← {self.sources[ch]}: {n:,} clicks,"
                f" {fmt_si(n / self.duration, 'Hz')}, p = {n / self.slots:.3e}"
            )
        chs = list(self.clicks)
        for i in range(len(chs)):
            for j in range(i + 1, len(chs)):
                lines.append(
                    f"  coincidences {chs[i]}·{chs[j]}:"
                    f" {self.coincidences(chs[i], chs[j]):,}"
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
    """Time tagger / counting module: one time bin per laser pulse, records every click.

    ``chN_delay`` is the input delay of channel N, added to its time stamps as in a
    tagger's software: a detector whose light arrives Δ before the others' lines up with
    them at +Δ.
    """

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
        doc=(
            "Shift the bins by the time of flight so bin k holds pulse k of the sync"
            " laser"
        ),
    )
    oversample = Param(
        1,
        label="Sub-bins per bin",
        min=1,
        max=256,
        doc=(
            "Synthesize the light on this many sub-bins per time bin and sum their"
            " photons. With 1, two pulses inside one bin interfere as if they"
            " overlapped; make a sub-bin a quarter of the pulse width or less to"
            " resolve pulses that miss each other (an unbalanced DLI)."
        ),
    )
    chunk = Param(1_000_000, label="Bins per chunk", min=1000, max=100_000_000)
    ch1_delay = Param(
        0.0, unit="s", label="CH1 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )
    ch2_delay = Param(
        0.0, unit="s", label="CH2 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )
    ch3_delay = Param(
        0.0, unit="s", label="CH3 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )
    ch4_delay = Param(
        0.0, unit="s", label="CH4 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )

    def _build(self):
        for i in range(1, 5):
            self.add_port(f"ch{i}", "electrical", "in", f"CH{i} input")

    def ui_fields(self):
        wired = [
            f"{ch}_delay"
            for ch in ("ch1", "ch2", "ch3", "ch4")
            if self.ports[ch].peer is not None
        ]
        return [
            "running",
            "slots",
            "bin_rate",
            "sync_source",
            "compensate_delay",
            "oversample",
            *wired,
        ]

    def __init__(self, name: str | None = None, **params):
        # next bin index: acquisitions run on from one another like a tagger's clock
        self.position = 0
        self._blind = (
            0,
            {},
        )  # (bin where the last record ended, dead bins each channel carries on)
        super().__init__(name, **params)

    def acquire(
        self, net: Network, rng: np.random.Generator, start: int | None = None
    ) -> CounterResult:
        """Record ``slots`` bins. By default each call continues where the previous one
        ended."""
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
        delay = {d: getattr(self, f"{ch}_delay") for ch, d in inputs.items()}
        shift = 0.0
        if (
            self.compensate_delay and laser is not None
        ):  # the earliest time stamp of pulse 0
            stamps = [
                t + delay[d] for d in inputs.values() for t in net.delays(d, laser)
            ]
            shift = min(stamps) if stamps else 0.0
        offsets = {
            d: -tau for d, tau in delay.items()
        }  # bin k of a channel: stamps, less its delay
        clicks = {ch: np.zeros(self.slots, dtype=bool) for ch in inputs}
        # a click near the end of a chunk (or of the previous record, when this one
        # continues it) keeps its detector dead into the next: the dead time does not
        # restart at the cut
        end, carried = self._blind
        blind = {ch: carried.get(ch, 0) if first == end else 0 for ch in inputs}
        m = self.oversample
        step = max(1, self.chunk // m)
        for a in range(0, self.slots, step):
            b = min(self.slots, a + step)
            t = (
                shift + (first + np.arange(a, b)) * dt - dt / 2
            )  # bins centered on the pulses
            if m > 1:
                t = (t[:, None] + np.arange(m) * (dt / m)).ravel()
            fields = synthesize(net, set(inputs.values()), t, dt / m, rng, offsets)
            for ch, d in inputs.items():
                c = clicks[ch][a:b] = d.detect(
                    fields[d], dt, rng, b - a, sub=m, blind=blind[ch]
                )
                blind[ch] = d.blind_after(c, dt, blind[ch])
        self._blind = (self.position, blind)
        return CounterResult(
            self.name,
            rate,
            self.slots,
            clicks,
            {ch: d.name for ch, d in inputs.items()},
            start=first,
        )


# --------------------------------------------------------------------------------------
FREQS = seq125(1e3, 20e9)
RBWS = seq125(1.0, 10e6)
DECIMATE = (
    4  # the signal is computed at 4 f_s, anti-alias filtered and decimated to f_s
)
ZERO_PAD = 4  # FFT zero padding: a tone's level is right wherever it falls between bins
MAX_SAMPLES = 4_000_000  # per sweep, after decimation


@dataclass
class SpectrumResult:
    """One sweep of an RF spectrum analyzer: the displayed trace and the full-resolution
    bins."""

    instrument: str
    source: str | None
    freq: np.ndarray  # display frequencies [Hz]
    level: np.ndarray  # displayed level [dBm in the RBW]
    rbw: float  # resolution (noise) bandwidth actually used [Hz]
    start: float
    stop: float
    ref_level: float
    db_per_div: float
    detector: str
    averages: int  # records averaged in this sweep
    sweeps: int  # sweeps in the displayed trace (trace average / max hold)
    bin_freq: np.ndarray  # FFT bins inside the span [Hz]
    bin_power: np.ndarray  # power in the RBW at each bin [W], this sweep
    bin_spacing: float

    def psd(self) -> tuple[np.ndarray, np.ndarray]:
        """(frequency [Hz], one-sided power spectral density at the input [W/Hz])."""
        return self.bin_freq, self.bin_power / self.rbw

    @property
    def peak(self) -> tuple[float, float]:
        """(frequency [Hz], level [dBm]) of the highest display point."""
        k = int(np.argmax(self.level))
        return float(self.freq[k]), float(self.level[k])

    @property
    def median_level(self) -> float:
        """Median of the displayed trace [dBm in the RBW]: the noise floor when signals
        cover little of the span."""
        return float(np.median(self.level))

    def width(self, drop_db: float = 3.0) -> tuple[float, float]:
        """(lower, upper) frequencies where the trace has fallen ``drop_db`` below its
        peak, walking out from the peak. A peak at the start of the span
        (a self-homodyne beat at 0 Hz) gives the start frequency as the lower edge, so
        the upper edge alone is the one-sided width.
        """
        lv, f = self.level, self.freq
        k = int(np.argmax(lv))
        thr = lv[k] - drop_db

        def cross(i, j):  # interpolate between display points i (above) and j (below)
            a, b = lv[i], lv[j]
            return (
                float(f[i] + (f[j] - f[i]) * (a - thr) / (a - b))
                if a != b
                else float(f[j])
            )

        hi = k
        while hi < lv.size - 1 and lv[hi + 1] >= thr:
            hi += 1
        lo = k
        while lo > 0 and lv[lo - 1] >= thr:
            lo -= 1
        f_hi = cross(hi, hi + 1) if hi < lv.size - 1 else float(f[-1])
        f_lo = cross(lo, lo - 1) if lo > 0 else float(f[0])
        return f_lo, f_hi

    def power(self) -> float:
        """Power integrated over the span [W]."""
        return float(np.sum(self.bin_power) * self.bin_spacing / self.rbw)

    def fit_lorentzian(self, center: float | None = 0.0) -> dict:
        """Least-squares fit of S₀ / (1 + ((f − f₀)/γ)²) + S_floor to the PSD, in dB.

        ``center=0`` fits a self-homodyne beat (Lorentzian folded at DC: γ is the laser
        linewidth); ``center=None`` also fits f₀, for a heterodyne beat (γ is then half
        the full −3 dB width, i.e. again the laser linewidth). Bins within 3 RBW of DC
        are skipped: the input is AC-coupled. Returns {"hwhm", "center", "peak_psd",
        "floor_psd"} [Hz, W/Hz].
        """
        import warnings

        from scipy.optimize import OptimizeWarning, curve_fit

        f, psd = self.psd()
        keep = (f > 3 * self.rbw) & (psd > 0)
        f, lin = f[keep], psd[keep]
        y = 10 * np.log10(lin)
        k = int(np.argmax(lin))
        floor0 = float(np.median(lin[-max(1, lin.size // 10) :]))
        below = np.flatnonzero(
            lin[k:] < (lin[k] + floor0) / 2
        )  # first fall to half height
        f0_guess = f[k] if center is None else center
        gamma0 = max(
            float(f[k + below[0]] - f0_guess) if below.size else f[-1] / 2, self.rbw
        )

        # Fitted in log10 of S₀, γ and S_floor: well conditioned (they span ~17 decades)
        # and positive. Bounded to the trace (levels within 3 decades of it, γ from a
        # tenth of a bin to ten spans), so a trace that is no Lorentzian cannot drive
        # them past 10³⁰⁸.
        def model(fq, a, b, c, f0):
            return 10 * np.log10(10**a / (1 + ((fq - f0) / 10**b) ** 2) + 10**c)

        low, high = math.log10(lin.min()) - 3, math.log10(lin.max()) + 3
        lower = [low, math.log10(np.diff(f).min() / 10), low]
        upper = [high, math.log10(10 * f[-1]), high]
        p0 = [
            math.log10(lin[k]),
            math.log10(gamma0),
            math.log10(max(floor0, lin[k] * 1e-12)),
        ]
        p0 = list(np.clip(p0, lower, upper))
        with (
            warnings.catch_warnings()
        ):  # a non-Lorentzian trace still gets its best fit
            warnings.simplefilter("ignore", OptimizeWarning)
            if center is None:
                span = f[-1] - f[0]
                p, _ = curve_fit(
                    model,
                    f,
                    y,
                    p0=p0 + [f0_guess],
                    max_nfev=20000,
                    bounds=(lower + [f[0] - span], upper + [f[-1] + span]),
                )
            else:
                p, _ = curve_fit(
                    lambda fq, a, b, c: model(fq, a, b, c, center),
                    f,
                    y,
                    p0=p0,
                    max_nfev=20000,
                    bounds=(lower, upper),
                )
                p = (*p, center)
        return {
            "hwhm": 10 ** float(p[1]),
            "center": float(p[3]),
            "peak_psd": 10 ** float(p[0]),
            "floor_psd": 10 ** float(p[2]),
        }

    def fit_self_homodyne(self, tau_d: float, center: float = 0.0) -> dict:
        """Laser linewidth from a delayed self-homodyne (or, with ``center=f_AOM``,
        self-heterodyne) beat for any delay ``tau_d`` [s], including delays shorter than
        the coherence time.

        Fits, in dB, A·Δν/(Δν² + u²)·S₂(u) + S_floor with u = f − f₀ and S₂ = 1 −
        e^{−2πΔντd}[cos 2πuτd + (Δν/u) sin 2πuτd] (Chen et al., Sensors 24, 3656 (2024),
        Eqs. (28)–(31)). The model is the average over the interferometer's bias phase
        θ: a homodyne trace taken at one fixed θ is biased, so average spectra taken at
        θ and θ + π/2 first (:meth:`average`, :meth:`phoq.Runner.average_spectrum`).
        When τd ≪ 1/πΔν the linewidth only sets the fringe depth e^{−2πΔντd}, so the
        span should cover several fringes (period 1/τd). The fringes make the cost
        multimodal: the fit starts from a ladder of linewidths and keeps the best. Bins
        within 3 RBW of f₀ are skipped. Returns {"linewidth", "fringe_depth",
        "amplitude", "floor_psd", "rms_db", "tau_d", "center"}.
        """
        import warnings

        from scipy.optimize import OptimizeWarning, curve_fit

        if tau_d <= 0:
            raise ValueError(
                "tau_d must be the (positive) delay difference between the arms"
            )
        f, psd = self.psd()
        keep = (np.abs(f - center) > 3 * self.rbw) & (psd > 0)
        f, lin = f[keep], psd[keep]
        y = 10 * np.log10(lin)
        u = f - center

        def shape(dnu):  # A = 1
            s2 = 1 - np.exp(-2 * np.pi * dnu * tau_d) * (
                np.cos(2 * np.pi * u * tau_d)
                + 2 * np.pi * dnu * tau_d * np.sinc(2 * u * tau_d)
            )
            return dnu / (dnu**2 + u**2) * np.clip(s2, 0.0, None)

        def model(
            _, a, b, c
        ):  # log10 of A, Δν and the floor: positive and well conditioned
            return 10 * np.log10(10**a * shape(10**b) + 10**c)

        floor0 = float(
            np.median(lin[np.argsort(np.abs(u))[-max(1, lin.size // 10) :]])
        )  # the far end
        span = float(np.max(np.abs(u)))
        best = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            warnings.simplefilter("ignore", RuntimeWarning)
            for dnu0 in np.logspace(
                math.log10(self.rbw / 10), math.log10(3 * span), 13
            ):
                g = shape(dnu0)
                p0 = [
                    math.log10(
                        max(lin.max() - floor0, lin.max() * 1e-3) / max(g.max(), 1e-300)
                    ),
                    math.log10(dnu0),
                    math.log10(max(floor0, 1e-300)),
                ]
                try:
                    p, _ = curve_fit(model, f, y, p0=p0, maxfev=4000)
                except RuntimeError, ValueError:
                    continue
                cost = float(np.mean((model(f, *p) - y) ** 2))
                if np.isfinite(cost) and (best is None or cost < best[0]):
                    best = (cost, p)
        if best is None:
            raise RuntimeError("the self-homodyne fit did not converge")
        cost, (a, b, c) = best
        dnu = 10 ** float(b)
        return {
            "linewidth": dnu,
            "fringe_depth": math.exp(-2 * math.pi * dnu * tau_d),
            "amplitude": 10 ** float(a),
            "floor_psd": 10 ** float(c),
            "rms_db": math.sqrt(cost),
            "tau_d": tau_d,
            "center": center,
        }

    @classmethod
    def average(cls, results) -> SpectrumResult:
        """Power-average sweeps taken with the same analyzer settings into one result
        (e.g. at several bias phases of an interferometer, for
        :meth:`fit_self_homodyne`)."""
        results = list(results)
        if not results:
            raise ValueError("nothing to average")
        first = results[0]
        for r in results[1:]:
            if (
                r.bin_freq.shape != first.bin_freq.shape
                or r.freq.shape != first.freq.shape
                or r.rbw != first.rbw
            ):
                raise ValueError(
                    "spectra taken with different analyzer settings cannot be averaged"
                )
        k = len(results)
        level = 10 * np.log10(sum(10 ** (r.level / 10) for r in results) / k)
        return cls(
            first.instrument,
            first.source,
            first.freq,
            level,
            first.rbw,
            first.start,
            first.stop,
            first.ref_level,
            first.db_per_div,
            first.detector,
            sum(r.averages for r in results),
            k,
            first.bin_freq,
            sum(r.bin_power for r in results) / k,
            first.bin_spacing,
        )

    def summary(self) -> str:
        f0, l0 = self.peak
        lo, hi = self.width(3.0)
        p = self.power()
        return (
            f"{self.instrument}: {fmt_si(self.start, 'Hz')} …"
            f" {fmt_si(self.stop, 'Hz')}, RBW {fmt_si(self.rbw, 'Hz')}, {self.averages}"
            f" avg, {self.detector} detector\n  {self.source or 'no input'}: peak"
            f" {l0:.1f} dBm at {fmt_si(f0, 'Hz')}, −3 dB {fmt_si(lo, 'Hz')} …"
            f" {fmt_si(hi, 'Hz')}, median {self.median_level:.1f} dBm,"
            f" {10 * math.log10(max(p, 1e-30) / 1e-3):.1f} dBm in the span"
        )

    def plot(self, ax=None):
        """Matplotlib plot of the trace (dBm against frequency), on the analyzer's own
        scale, in the dark_background style."""
        import matplotlib.pyplot as plt

        with plt.style.context("dark_background"):
            ax = ax or plt.subplots(figsize=(8, 3.6))[1]
            scale, unit = next(
                (
                    (s, u)
                    for s, u in ((1e9, "GHz"), (1e6, "MHz"), (1e3, "kHz"))
                    if self.stop >= s
                ),
                (1.0, "Hz"),
            )
            ax.plot(self.freq / scale, self.level, color=CH_COLORS[0], lw=1.2)
            ax.set(
                xlabel=f"frequency [{unit}]",
                ylabel=f"level [dBm in {fmt_si(self.rbw, 'Hz')}]",
                title=self.instrument,
                xlim=(self.start / scale, self.stop / scale),
                ylim=(self.ref_level - 10 * self.db_per_div, self.ref_level),
            )
            ax.grid(alpha=0.3)
        return ax

    def to_json(self) -> dict:
        f0, l0 = self.peak
        lo, hi = self.width(3.0)
        meas = {
            "peak_freq": f0,
            "peak_dbm": l0,
            "median_dbm": self.median_level,
            "w3_lo": lo,
            "w3_hi": hi,
            "span_dbm": 10 * math.log10(max(self.power(), 1e-30) / 1e-3),
        }
        if lo <= self.start + 1e-3 * (self.stop - self.start) and hi < self.stop:
            # a line folded at DC (self-homodyne): its Lorentzian half width is the
            # linewidth
            try:
                hwhm = self.fit_lorentzian(0.0)["hwhm"]
                if (
                    self.rbw <= hwhm <= self.stop
                ):  # narrower is unresolved, wider is no line in this span
                    meas["fit_hwhm"] = hwhm
            except RuntimeError, ValueError:
                pass
        return {
            "kind": "spectrum",
            "instrument": self.instrument,
            "source": self.source,
            "start": self.start,
            "stop": self.stop,
            "rbw": self.rbw,
            "ref_level": self.ref_level,
            "db_per_div": self.db_per_div,
            "detector": self.detector,
            "averages": self.averages,
            "sweeps": self.sweeps,
            "n": int(self.level.size),
            "data": (
                base64.b64encode(np.asarray(self.level, dtype="<f4").tobytes()).decode()
            ),
            "meas": meas,
        }


class SpectrumAnalyzer(Component):
    """RF (electrical) spectrum analyzer: the power in the resolution bandwidth against
    frequency, in dBm into an AC-coupled input of ``input_impedance``.

    FFT method (README, "RF spectrum analyzer"): anti-alias filter and decimate to f_s =
    2.56 f_stop, Hann records of N = 1.5 f_s / RBW samples, ``averages`` of them
    power-averaged with 50 % overlap, then ``points`` display points through the
    ``detector``.
    """

    category = "instrument"
    prefix = "ESA"
    label = "RF spectrum analyzer"
    description = "Electrical spectrum: RBW, averaging, dBm into 50 Ω"
    inputs = ("inp",)

    running = Param(True, label="Run")
    start_freq = Param(
        0.0,
        unit="Hz",
        label="Start frequency",
        min=0.0,
        max=50e9,
        display=("MHz", 1e-6),
    )
    stop_freq = Param(
        50e6,
        unit="Hz",
        label="Stop frequency",
        min=1e3,
        max=50e9,
        presets=FREQS,
        log=True,
    )
    rbw = Param(
        100e3,
        unit="Hz",
        label="Resolution bandwidth",
        min=1.0,
        max=10e6,
        presets=RBWS,
        log=True,
        doc="Noise bandwidth of the resolution filter: one record lasts 1.5 / RBW",
    )
    averages = Param(
        10,
        label="Averages per sweep",
        min=1,
        max=2000,
        presets=(1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000),
        doc="Records power-averaged in every sweep (Welch, 50 % overlap)",
    )
    detector = Param(
        "peak",
        choices=("peak", "average", "sample"),
        doc=(
            "How the bins in each display point's bucket become one value: peak for"
            " tones, average for noise"
        ),
    )
    trace = Param(
        "write",
        choices=("write", "average", "max hold"),
        doc=(
            "write: each sweep replaces the trace; average: running mean of the sweeps;"
            " max hold: the largest seen"
        ),
    )
    ref_level = Param(0.0, unit="dBm", label="Reference level", min=-200.0, max=40.0)
    db_per_div = Param(
        10.0,
        unit="dB",
        label="Scale / div",
        min=0.1,
        max=20.0,
        presets=(1.0, 2.0, 5.0, 10.0),
    )
    points = Param(
        1001,
        label="Display points",
        min=101,
        max=10001,
        presets=(101, 201, 501, 1001, 2001, 5001),
    )
    input_impedance = Param(50.0, unit="Ω", label="Input impedance", min=1.0, max=1e6)
    noise_floor = Param(
        -150.0,
        unit="dBm/Hz",
        label="Noise floor (DANL)",
        min=-200.0,
        max=-60.0,
        doc=(
            "Displayed average noise level, added at the input as white noise; a"
            " generic default (analyzers span about −140 … −165 dBm/Hz)"
        ),
    )

    def __init__(self, name: str | None = None, **params):
        self._trace: np.ndarray | None = (
            None  # displayed trace in W, for trace average / max hold
        )
        self._sweeps = 0
        super().__init__(name, **params)

    def _build(self):
        self.add_port("inp", "electrical", "in", "RF input")

    def _param_changed(self, name: str) -> None:
        if name != "running":
            self.clear_trace()
        super()._param_changed(name)

    def clear_trace(self) -> None:
        """Restart trace averaging and max hold."""
        self._trace, self._sweeps = None, 0

    def ui_fields(self):
        return [
            "running", "start_freq", "stop_freq", "rbw", "averages", "detector",
            "trace", "ref_level", "db_per_div", "points", "input_impedance",
            "noise_floor",
        ]  # fmt: skip

    def _input(self):
        peer = self.ports["inp"].peer
        src = peer.component if peer is not None else None
        ok = (
            Photodiode,
            BalancedPhotodetector,
            SinglePhotonDetector,
            FunctionGenerator,
            DCPowerSupply,
        )
        return (src, peer.name) if isinstance(src, ok) else None

    def record(self) -> tuple[float, int, int, float]:
        """(sample rate, samples per record, records per sweep, effective RBW) for the
        settings.

        The sweep is capped at MAX_SAMPLES: fewer averages first, then a coarser RBW."""
        fs = 2.56 * self.stop_freq
        n = max(16, int(round(1.5 * fs / self.rbw)))
        n = min(n, MAX_SAMPLES)
        hop = n // 2
        k = max(1, min(self.averages, (MAX_SAMPLES - n) // hop + 1))
        return fs, n, k, 1.5 * fs / n

    def info(self):
        fs, n, k, rbw = self.record()
        out = {
            "Sweep": f"{fmt_si(fs, 'S/s')}, {k} × {fmt_si(n / fs, 's')} records",
            "RBW used": fmt_si(rbw, "Hz"),
        }
        if self.trace != "write":
            out["Trace"] = f"{self.trace} of {self._sweeps} sweeps"
        return out

    def _signal(
        self, net: Network, rng: np.random.Generator, fs: float, total: int
    ) -> np.ndarray:
        """``total`` samples at ``fs`` of the input voltage across the analyzer's
        input."""
        src = self._input()
        if src is None:
            return np.zeros(total)
        d, port = src
        pad = 64  # FIR transient at both ends of the decimated record
        dts = 1.0 / (fs * DECIMATE)
        settle = (
            d.settle_time if isinstance(d, (Photodiode, BalancedPhotodetector)) else 0.0
        )
        warm = min(50_000, math.ceil(settle / dts))
        t = rng.uniform(0, 1e-3) + np.arange(-warm, (total + 2 * pad) * DECIMATE) * dts
        if isinstance(d, (Photodiode, BalancedPhotodetector, SinglePhotonDetector)):
            fields = synthesize(net, {d}, t, dts, rng)
            if isinstance(
                d, BalancedPhotodetector
            ):  # a voltage source behind its output impedance
                r = self.input_impedance
                x = (
                    d.detect(fields[d], dts, rng, t.size)[port]
                    * r
                    / (r + d.output_impedance(port))
                )
            elif isinstance(d, Photodiode):
                x = (
                    d.detect(fields[d], dts, rng, t.size) * self.input_impedance
                )  # current → volts
            else:
                x = d.waveform(d.detect(fields[d], dts, rng, t.size), dts)
        else:  # a generator or supply output straight into the analyzer
            d.start(rng)
            x = np.asarray(d.voltage(port, t), dtype=float)
        x = resample_poly(x[warm:], 1, DECIMATE)  # anti-alias low-pass + decimation
        return x[pad : pad + total]

    def acquire(self, net: Network, rng: np.random.Generator) -> SpectrumResult:
        """One sweep (README, "RF spectrum analyzer")."""
        fs, n, k, rbw = self.record()
        hop, R = n // 2, self.input_impedance
        total = n + (k - 1) * hop
        v = self._signal(net, rng, fs, total)
        v = v - v.mean()  # AC-coupled input: the DC photocurrent is blocked
        danl = (
            10 ** ((self.noise_floor - 30) / 10) * R
        )  # one-sided voltage PSD of the instrument [V²/Hz]
        v = v + math.sqrt(danl * fs / 2) * rng.standard_normal(total)

        w = np.hanning(n + 1)[
            :-1
        ]  # periodic Hann, equivalent noise bandwidth 1.5 f_s / N
        segs = v[np.arange(k)[:, None] * hop + np.arange(n)[None, :]] * w
        nfft = n * ZERO_PAD
        power = (
            (np.abs(np.fft.rfft(segs, nfft, axis=1)) ** 2).mean(axis=0)
            * 2
            / (w.sum() ** 2 * R)
        )
        power[0] /= 2
        f = np.arange(power.size) * fs / nfft

        stop = self.stop_freq
        start = min(self.start_freq, stop * (1 - 1e-6))
        fd = np.linspace(start, stop, self.points)
        shown = self._detect(f, power, fd)
        if (
            self.trace == "average"
            and self._trace is not None
            and self._trace.size == shown.size
        ):
            self._trace = (self._trace * self._sweeps + shown) / (self._sweeps + 1)
            self._sweeps += 1
        elif (
            self.trace == "max hold"
            and self._trace is not None
            and self._trace.size == shown.size
        ):
            self._trace = np.maximum(self._trace, shown)
            self._sweeps += 1
        else:
            self._trace, self._sweeps = shown, 1
        inside = (f >= start) & (f <= stop)
        peer = self.ports["inp"].peer
        return SpectrumResult(
            self.name,
            peer.component.name if peer is not None else None,
            fd,
            10 * np.log10(np.maximum(self._trace, 1e-30) / 1e-3),
            rbw,
            start,
            stop,
            self.ref_level,
            self.db_per_div,
            self.detector,
            k,
            self._sweeps,
            f[inside],
            power[inside],
            fs / nfft,
        )

    def _detect(self, f: np.ndarray, power: np.ndarray, fd: np.ndarray) -> np.ndarray:
        """Power at each display frequency from the FFT bins in its bucket
        (detector mode)."""
        out = np.interp(
            fd, f, power
        )  # sample detector, and buckets narrower than a bin
        if self.detector == "sample" or fd.size < 2:
            return out
        half = (fd[1] - fd[0]) / 2
        edges = np.concatenate(
            ([fd[0] - half], (fd[:-1] + fd[1:]) / 2, [fd[-1] + half])
        )
        idx = np.searchsorted(f, edges)
        counts = np.diff(idx)
        full = counts > 0
        if full.any():
            starts = idx[:-1][full]
            part = power[: idx[-1]]
            if self.detector == "peak":
                out[full] = np.maximum.reduceat(part, starts)
            else:
                out[full] = np.add.reduceat(part, starts) / counts[full]
        return out
