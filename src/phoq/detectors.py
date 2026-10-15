"""Optical detectors. Each absorbs the light reaching its ``inp`` port."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

from .core import Component, Param
from .units import (
    COMMON_WAVELENGTHS,
    DARK_CURRENT,
    K_B,
    Q_E,
    fmt_si,
    photon_energy,
    responsivity,
    w_to_dbm,
)


class _Detector(Component):
    category = "detector"
    inputs = ("inp",)
    signal_unit = ""


class Photodiode(_Detector):
    """Fiber-coupled PIN photodiode; its ``rf`` output carries the photocurrent [A].

    I(t) = Σ R(λ)·P_λ(t) + I_dark, low-pass filtered by ``bandwidth`` (one pole), plus shot noise
    √(2qIB) and thermal noise √(4kTB/R_load), with a soft knee above ``saturation``.
    """

    prefix = "PD"
    label = "Photodiode"
    description = "InGaAs / Si / Ge PIN, BNC output"
    outputs = ("rf",)
    signal_unit = "A"

    material = Param(
        "InGaAs", choices=("InGaAs", "Si", "Ge"), label="Detector material"
    )
    bandwidth = Param(
        1e9,
        unit="Hz",
        label="Bandwidth (−3 dB)",
        min=1e3,
        max=100e9,
        presets=(10e6, 150e6, 1e9, 5e9, 20e9),
        log=True,
    )
    load = Param(50.0, unit="Ω", label="Load resistance", min=1.0, max=1e7)
    saturation = Param(5e-3, unit="A", label="Saturation current", min=1e-6, max=1.0)
    temperature = Param(300.0, unit="K", min=1.0, max=400.0)

    def _build(self):
        self.add_port("inp", "optical", "in", "FC/PC in")
        self.add_port("rf", "electrical", "out", "BNC out")

    def ui_fields(self):
        return ["material", "bandwidth", "load", "saturation"]

    @property
    def dark_current(self) -> float:
        return DARK_CURRENT[self.material]

    @property
    def settle_time(self) -> float:
        return 6.0 / (2 * math.pi * self.bandwidth)

    def responsivity(self, wavelength: float) -> float:
        return responsivity(self.material, wavelength)

    def mean_current(self, powers: dict[float, float]) -> float:
        return self.dark_current + sum(
            self.responsivity(wl) * p for wl, p in powers.items()
        )

    def detect(
        self,
        fields: dict[float, np.ndarray],
        dt: float,
        rng: np.random.Generator,
        n: int,
        filtered: bool = True,
    ) -> np.ndarray:
        current = np.full(n, self.dark_current)
        for wl, E in fields.items():
            current += self.responsivity(wl) * np.abs(E) ** 2
        if filtered:
            alpha = -math.expm1(-dt * 2 * math.pi * self.bandwidth)
            current, _ = lfilter(
                [alpha], [1.0, alpha - 1.0], current, zi=[(1 - alpha) * current[0]]
            )
        B = min(self.bandwidth * math.pi / 2, 0.5 / dt)
        sigma = np.sqrt(
            2 * Q_E * np.clip(current, 0, None) * B
            + 4 * K_B * self.temperature * B / self.load
        )
        out = current + sigma * rng.standard_normal(n)
        pos = out > 0
        out[pos] = out[pos] / (1 + (out[pos] / self.saturation) ** 4) ** 0.25
        return out


class SinglePhotonDetector(_Detector):
    """Threshold single-photon detector (SPAD / SNSPD). Its ``out`` port emits 1 V click pulses.

    Click probability per time bin: 1 − (1 − p_dark)·exp(−η·n̄), where n̄ is the mean photon number
    in the bin; after a click the detector is blind for ``dead_time``.
    """

    prefix = "SPD"
    label = "Single-photon detector"
    description = "Threshold click detector (SPAD / SNSPD)"
    outputs = ("out",)
    signal_unit = "V"

    efficiency = Param(0.25, label="Detection efficiency", min=0.0, max=1.0)
    dark_count_rate = Param(
        100.0,
        unit="Hz",
        label="Dark-count rate",
        min=0.0,
        max=1e7,
        presets=(1.0, 10.0, 100.0, 1e3, 1e4),
    )
    dead_time = Param(
        10e-6,
        unit="s",
        label="Dead time",
        min=0.0,
        max=1e-2,
        presets=(0.0, 20e-9, 100e-9, 1e-6, 10e-6),
    )

    def _build(self):
        self.add_port("inp", "optical", "in", "FC/PC in")
        self.add_port("out", "electrical", "out", "SMA click out")

    def photons(self, fields: dict[float, np.ndarray], dt: float, n: int) -> np.ndarray:
        nbar = np.zeros(n)
        for wl, E in fields.items():
            nbar += np.abs(E) ** 2 * dt / photon_energy(wl)
        return nbar

    def click_probability(self, nbar: np.ndarray, dt: float) -> np.ndarray:
        p_dark = -math.expm1(-self.dark_count_rate * dt)
        return 1 - (1 - p_dark) * np.exp(-self.efficiency * nbar)

    def detect(self, fields, dt, rng, n: int) -> np.ndarray:
        """Boolean click record, one entry per time bin."""
        clicks = rng.random(n) < self.click_probability(self.photons(fields, dt, n), dt)
        dead = int(math.ceil(self.dead_time / dt)) if self.dead_time > 0 else 0
        if dead > 1 and clicks.any():
            keep, last = [], -dead
            for i in np.flatnonzero(clicks):
                if i - last >= dead:
                    keep.append(i)
                    last = i
            clicks = np.zeros(n, dtype=bool)
            clicks[keep] = True
        return clicks

    def waveform(
        self, clicks: np.ndarray, dt: float, width: float = 5e-9
    ) -> np.ndarray:
        """Electrical output: 1 V pulses of ``width`` for every click."""
        k = max(1, int(round(width / dt)))
        v = clicks.astype(float)
        return np.minimum(1.0, np.convolve(v, np.ones(k))[: v.size]) if k > 1 else v


class PowerMeter(_Detector):
    """Optical power meter with an InGaAs head, calibrated at ``cal_wavelength``."""

    prefix = "OPM"
    label = "Power meter"
    description = "InGaAs head, reads average power"
    signal_unit = "W"

    cal_wavelength = Param(
        1550e-9,
        unit="m",
        label="Calibration wavelength",
        min=800e-9,
        max=1700e-9,
        presets=COMMON_WAVELENGTHS,
        display=("nm", 1e9),
    )

    def _build(self):
        self.add_port("inp", "optical", "in", "FC/PC in")

    def reading(self, powers: dict[float, float]) -> float:
        """What the display shows: power weighted by the head's responsivity at each λ."""
        r_cal = responsivity("InGaAs", self.cal_wavelength) or 1.0
        return sum(p * responsivity("InGaAs", wl) / r_cal for wl, p in powers.items())

    def acquire(self, net, rng) -> PowerReading:
        return PowerReading(self.name, self.reading(net.mean_powers(self)))


@dataclass
class PowerReading:
    instrument: str
    power: float

    def summary(self) -> str:
        if self.power <= 0:
            return f"{self.instrument}: no light"
        return f"{self.instrument}: {fmt_si(self.power, 'W')} ({w_to_dbm(self.power):.2f} dBm)"

    def to_json(self) -> dict:
        return {"instrument": self.instrument, "power": self.power}
