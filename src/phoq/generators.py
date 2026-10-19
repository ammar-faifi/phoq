"""Electrical signal sources. The function generator is the only thing that drives modulators."""

from __future__ import annotations

import numpy as np

from .core import Component, Param
from .units import fmt_si, seq125

CHANNELS = ("ch1", "ch2")
SHAPES = ("dc", "sine", "square", "triangle", "ramp", "pattern")
TRIGGERS = ("aligned", "laser", "free")


class FunctionGenerator(Component):
    """Two-channel arbitrary function generator (AWG).

    Each output has a shape, a frequency (the symbol rate for a loaded pattern), a peak-to-peak
    amplitude, an offset, and a delay. Connect an output to a modulator's ``rf`` port::

        fg = setup.add(FunctionGenerator())
        setup.connect(fg.ch1, pm.rf)
        fg.set(ch1_shape="sine", ch1_freq=1e9, ch1_vpp=2.0)
        fg.set_pattern("ch2", im.voltage_for(np.where(bits, 1.0, 0.0)), rate=laser.rep_rate)

    Time base (``trigger``): *aligned* locks the generator to the laser's pulse clock with its delay
    already calibrated, so pattern symbol k meets pulse k inside whatever modulator the channel
    drives; *laser* is locked to the laser clock as it leaves the laser, and you set ``chN_delay``
    yourself; *free* runs free with a random start each acquisition. Symbol k of a pattern is
    centred on t = k / rate.
    """

    category = "instrument"
    prefix = "FG"
    label = "Function generator"
    description = "2-channel AWG: dc / sine / square / triangle / ramp / pattern"
    outputs = CHANNELS
    signal_unit = "V"

    trigger = Param(
        "aligned",
        choices=TRIGGERS,
        label="Time base",
        doc="aligned: symbol k meets pulse k at the driven modulator; laser: locked to the laser clock, set delays by hand; free: free-running",
    )
    ch1_enabled = Param(True, label="CH1 output")
    ch1_shape = Param("sine", choices=SHAPES, label="CH1 shape")
    ch1_freq = Param(
        10e6,
        unit="Hz",
        label="CH1 frequency / symbol rate",
        min=1.0,
        max=50e9,
        presets=seq125(1e3, 10e9),
        log=True,
    )
    ch1_vpp = Param(2.0, unit="V", label="CH1 amplitude (pk-pk)", min=0.0, max=20.0)
    ch1_offset = Param(0.0, unit="V", label="CH1 offset", min=-10.0, max=10.0)
    ch1_duty = Param(0.5, label="CH1 duty cycle", min=0.01, max=0.99)
    ch1_phase = Param(0.0, unit="deg", label="CH1 phase", min=-360.0, max=360.0)
    ch1_delay = Param(
        0.0, unit="s", label="CH1 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )
    ch2_enabled = Param(True, label="CH2 output")
    ch2_shape = Param("sine", choices=SHAPES, label="CH2 shape")
    ch2_freq = Param(
        10e6,
        unit="Hz",
        label="CH2 frequency / symbol rate",
        min=1.0,
        max=50e9,
        presets=seq125(1e3, 10e9),
        log=True,
    )
    ch2_vpp = Param(2.0, unit="V", label="CH2 amplitude (pk-pk)", min=0.0, max=20.0)
    ch2_offset = Param(0.0, unit="V", label="CH2 offset", min=-10.0, max=10.0)
    ch2_duty = Param(0.5, label="CH2 duty cycle", min=0.01, max=0.99)
    ch2_phase = Param(0.0, unit="deg", label="CH2 phase", min=-360.0, max=360.0)
    ch2_delay = Param(
        0.0, unit="s", label="CH2 delay", min=-1e-3, max=1e-3, display=("ns", 1e9)
    )

    def __init__(self, name: str | None = None, **params):
        self.patterns: dict[str, np.ndarray | None] = {ch: None for ch in CHANNELS}
        self._epoch = 0.0
        super().__init__(name, **params)

    def _build(self):
        for ch in CHANNELS:
            self.add_port(ch, "electrical", "out", f"{ch.upper()} out")

    def ui_fields(self):
        fields = ["trigger"]
        for ch in CHANNELS:
            shape = getattr(self, f"{ch}_shape")
            fields += [f"{ch}_enabled", f"{ch}_shape"]
            if shape != "dc":
                fields += [f"{ch}_freq", f"{ch}_vpp"]
            fields.append(f"{ch}_offset")
            if shape == "square":
                fields.append(f"{ch}_duty")
            if shape in ("sine", "triangle", "ramp"):
                fields.append(f"{ch}_phase")
            if self.trigger != "aligned" or shape == "pattern":
                fields.append(f"{ch}_delay")
        return fields

    # ---- programming ---------------------------------------------------------------------------
    def _check(self, channel: str) -> str:
        if channel not in CHANNELS:
            raise ValueError(
                f"{self.name}: channel must be one of {CHANNELS}, not {channel!r}"
            )
        return channel

    def set_pattern(
        self, channel: str, volts, rate: float | None = None
    ) -> FunctionGenerator:
        """Load an arbitrary waveform, one voltage per symbol, and switch the channel to it.

        The amplitude and offset are set from the pattern's extremes; the stored samples are
        normalised, so changing ``chN_vpp`` later rescales the pattern like on a real AWG.
        """
        ch = self._check(channel)
        v = np.asarray(volts, dtype=float).ravel()
        if v.size == 0:
            raise ValueError(f"{self.name}: pattern must have at least one symbol")
        lo, hi = float(v.min()), float(v.max())
        vpp, offset = hi - lo, (hi + lo) / 2
        self.patterns[ch] = (v - offset) / (vpp / 2) if vpp > 0 else np.zeros(v.size)
        params = {f"{ch}_shape": "pattern", f"{ch}_vpp": vpp, f"{ch}_offset": offset}
        if rate is not None:
            params[f"{ch}_freq"] = rate
        self.set(**params)
        self._param_changed(f"{ch}_pattern")
        return self

    def pattern_volts(self, channel: str) -> np.ndarray | None:
        """The loaded pattern in volts (after the current amplitude and offset)."""
        ch = self._check(channel)
        pat = self.patterns[ch]
        if pat is None:
            return None
        return getattr(self, f"{ch}_offset") + 0.5 * getattr(self, f"{ch}_vpp") * pat

    # ---- signal ----------------------------------------------------------------------------------
    def start(self, rng: np.random.Generator) -> None:
        """Begin an acquisition: a free-running generator starts at a random point."""
        self._epoch = float(rng.uniform(0, 1e-3)) if self.trigger == "free" else 0.0

    def is_static(self, channel: str) -> bool:
        ch = self._check(channel)
        shape = getattr(self, f"{ch}_shape")
        return (
            (not getattr(self, f"{ch}_enabled"))
            or shape == "dc"
            or (shape == "pattern" and self.patterns[ch] is None)
        )

    def static_voltage(self, channel: str) -> float:
        ch = self._check(channel)
        return getattr(self, f"{ch}_offset") if getattr(self, f"{ch}_enabled") else 0.0

    def voltage(self, channel: str, t_mod, t_emit=None) -> np.ndarray:
        """Output voltage [V] at the driven device.

        ``t_mod`` is the time at that device; ``t_emit`` the time the light passing it left the
        laser (used by the *aligned* time base). Pass only ``t_mod`` to look at the raw output.
        """
        ch = self._check(channel)
        base = t_emit if (self.trigger == "aligned" and t_emit is not None) else t_mod
        tau = np.asarray(base, dtype=float) - getattr(self, f"{ch}_delay") - self._epoch
        return self._shape(ch, tau)

    def waveform(self, channel: str, n: int = 512) -> np.ndarray:
        """One period of the output (or every symbol of a pattern), for averaging and display."""
        ch = self._check(channel)
        f = getattr(self, f"{ch}_freq")
        if getattr(self, f"{ch}_shape") == "pattern" and self.patterns[ch] is not None:
            tau = np.arange(self.patterns[ch].size) / f
        else:
            tau = np.arange(n) / (n * f)
        return self._shape(ch, tau)

    def _shape(self, ch: str, tau: np.ndarray) -> np.ndarray:
        g = lambda key: getattr(self, f"{ch}_{key}")  # noqa: E731
        tau = np.asarray(tau, dtype=float)
        if not g("enabled"):
            return np.zeros(tau.shape)
        shape, offset, amp, f = g("shape"), g("offset"), 0.5 * g("vpp"), g("freq")
        if shape == "dc":
            return np.full(tau.shape, offset)
        if shape == "pattern":
            pat = self.patterns[ch]
            if pat is None:
                return np.full(tau.shape, offset)
            return offset + amp * pat[np.rint(tau * f).astype(np.int64) % pat.size]
        cycles = tau * f + g("phase") / 360.0
        frac = cycles - np.floor(cycles)
        if shape == "sine":
            return offset + amp * np.sin(2 * np.pi * cycles)
        if shape == "square":
            return offset + amp * np.where(frac < g("duty"), 1.0, -1.0)
        if shape == "triangle":
            return offset + amp * (1.0 - 4.0 * np.abs(frac - 0.5))
        return offset + amp * (2.0 * frac - 1.0)  # ramp

    # ---- read-outs -----------------------------------------------------------------------------
    def describe(self, channel: str) -> str:
        ch = self._check(channel)
        if not getattr(self, f"{ch}_enabled"):
            return "off"
        shape = getattr(self, f"{ch}_shape")
        if shape == "dc":
            return f"dc {getattr(self, f'{ch}_offset'):+.2f} V"
        freq = fmt_si(getattr(self, f"{ch}_freq"), "Hz")
        if shape != "pattern":
            return f"{shape} {freq} · {getattr(self, f'{ch}_vpp'):.2f} Vpp"
        if self.patterns[ch] is None:
            return f"pattern {freq} · {getattr(self, f'{ch}_vpp'):.2f} Vpp · NO PATTERN LOADED"
        # the levels the pattern really takes: a pattern with no swing sits at its offset, which
        # "0.00 Vpp" alone would hide (a switch held in one state, a modulator held dark). The
        # lab's front-panel screen reads them from this text (web/js/parts.js, patternLevels).
        v = self.pattern_volts(ch)
        lo, hi = float(v.min()), float(v.max())
        levels = f"constant {lo:+.2f} V" if lo == hi else f"{lo:+.2f} … {hi:+.2f} V"
        return f"pattern {freq} · {levels} · {v.size:,} symbol{'' if v.size == 1 else 's'}"

    def info(self):
        out = {}
        for ch in CHANNELS:
            peer = self.ports[ch].peer
            out[ch.upper()] = self.describe(ch) + (f" → {peer.path}" if peer else "")
        return out


class DCPowerSupply(Component):
    """Two-output bench DC supply, e.g. for a modulator's ``bias`` input::

    psu = setup.add(DCPowerSupply(ch1_voltage=im.v_pi / 2))   # quadrature bias
    setup.connect(psu.ch1, im.bias)
    """

    category = "instrument"
    prefix = "PSU"
    label = "DC power supply"
    description = "2 outputs, ±30 V"
    outputs = CHANNELS
    signal_unit = "V"

    ch1_enabled = Param(True, label="CH1 output")
    ch1_voltage = Param(0.0, unit="V", label="CH1 voltage", min=-30.0, max=30.0)
    ch2_enabled = Param(True, label="CH2 output")
    ch2_voltage = Param(0.0, unit="V", label="CH2 voltage", min=-30.0, max=30.0)

    def _build(self):
        for ch in CHANNELS:
            self.add_port(ch, "electrical", "out", f"{ch.upper()} out")

    def _check(self, channel: str) -> str:
        if channel not in CHANNELS:
            raise ValueError(
                f"{self.name}: channel must be one of {CHANNELS}, not {channel!r}"
            )
        return channel

    def start(self, rng: np.random.Generator) -> None:
        """A DC supply has nothing to synchronise."""

    def is_static(self, channel: str) -> bool:
        self._check(channel)
        return True

    def static_voltage(self, channel: str) -> float:
        ch = self._check(channel)
        return getattr(self, f"{ch}_voltage") if getattr(self, f"{ch}_enabled") else 0.0

    def voltage(self, channel: str, t_mod, t_emit=None) -> np.ndarray:
        return np.full(np.shape(t_mod), self.static_voltage(channel))

    def waveform(self, channel: str, n: int = 1) -> np.ndarray:
        return np.array([self.static_voltage(channel)])

    def describe(self, channel: str) -> str:
        ch = self._check(channel)
        return (
            f"dc {getattr(self, f'{ch}_voltage'):+.3f} V"
            if getattr(self, f"{ch}_enabled")
            else "off"
        )

    def info(self):
        out = {}
        for ch in CHANNELS:
            peer = self.ports[ch].peer
            out[ch.upper()] = self.describe(ch) + (f" → {peer.path}" if peer else "")
        return out
