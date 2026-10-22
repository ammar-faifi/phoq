"""Light sources."""

from __future__ import annotations

import math

import numpy as np
from scipy.special import erf

from .core import Component, FiberConnectors, Param
from .units import COMMON_WAVELENGTHS, dbm_to_w, fmt_si, photon_energy, seq125

MODES = ("cw", "pulsed", "sine", "ook")
RIN_OFF = -200.0  # dB/Hz: a laser without classical intensity noise (no random draws at all)
_FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0))  # Gaussian FWHM / sigma


class Laser(FiberConnectors, Component):
    """Fiber-coupled DFB laser module with an internal isolator.

    The emitted field is ``sqrt(P(t)·(1 + δ(t))) · exp(i φ(t))`` where the envelope follows ``mode``,
    the phase performs a random walk set by ``linewidth`` (plus ``detuning`` and, for weak coherent
    pulse sources, an optional uniformly random phase per pulse), and δ is the laser's classical
    relative intensity noise, white at ``rin_db_hz``. Shot noise is not part of δ: phoq draws it at
    detection, so a detector measures RIN = rin + 2hν/(ηP).
    """

    category = "source"
    prefix = "LD"
    label = "Laser module"
    description = "DFB laser, fiber output, CW or modulated"
    outputs = ("out",)

    enabled = Param(True, label="Emission")
    wavelength = Param(
        1550e-9,
        unit="m",
        min=400e-9,
        max=1700e-9,
        presets=COMMON_WAVELENGTHS,
        display=("nm", 1e9),
    )
    power_dbm = Param(-7.0, unit="dBm", label="Average power", min=-90.0, max=20.0)
    mode = Param(
        "pulsed",
        choices=MODES,
        label="Modulation",
        doc="cw, pulsed (gain-switched Gaussian), sine or ook (on-off keying)",
    )
    rep_rate = Param(
        10e6,
        unit="Hz",
        label="Repetition / modulation rate",
        min=1.0,
        max=10e9,
        presets=seq125(1e3, 5e9),
        log=True,
    )
    pulse_width = Param(
        5e-9,
        unit="s",
        label="Pulse width (FWHM)",
        min=10e-12,
        max=1e-3,
        presets=seq125(50e-12, 100e-9),
        log=True,
    )
    mod_depth = Param(0.8, label="Modulation depth", min=0.0, max=1.0)
    duty = Param(0.5, label="Duty cycle", min=0.01, max=0.99)
    linewidth = Param(
        100e3,
        unit="Hz",
        label="Linewidth",
        min=0.0,
        max=10e9,
        presets=(0.0, 1e3, 10e3, 100e3, 1e6, 10e6),
        doc="Lorentzian linewidth (phase diffusion)",
    )
    detuning = Param(0.0, unit="Hz", label="Frequency offset", min=-100e9, max=100e9)
    rin_db_hz = Param(
        RIN_OFF,
        unit="dB/Hz",
        label="Intensity noise (RIN)",
        min=RIN_OFF,
        max=-80.0,
        presets=(RIN_OFF, -160.0, -150.0, -140.0, -130.0, -120.0),
        doc="One-sided PSD of the classical relative intensity noise δP/P̄, white; the shot noise "
        "comes on top at detection. −200 dB/Hz = none",
    )
    phase_randomized = Param(
        False,
        label="Random phase per pulse",
        doc="Weak-coherent-pulse sources: a fresh uniform phase each pulse",
    )

    def _build(self):
        self.add_port("out", "optical", "out", "out")

    def ui_fields(self) -> list[str]:
        f = ["enabled", "wavelength", "power_dbm", "mode"]
        if self.mode != "cw":
            f.append("rep_rate")
        f += {
            "pulsed": ["pulse_width", "phase_randomized"],
            "sine": ["mod_depth"],
            "ook": ["duty"],
        }.get(self.mode, [])
        return f + ["linewidth", "detuning", "rin_db_hz"]

    # ---- derived quantities -------------------------------------------------------------------
    @property
    def power(self) -> float:
        """Average output power [W]."""
        return dbm_to_w(self.power_dbm) if self.enabled else 0.0

    @property
    def period(self) -> float | None:
        return None if self.mode == "cw" else 1.0 / self.rep_rate

    @property
    def sigma(self) -> float:
        return min(self.pulse_width, 0.45 / self.rep_rate) / _FWHM

    @property
    def peak_power(self) -> float:
        P = self.power
        return {
            "sine": P * (1 + self.mod_depth),
            "ook": P / self.duty,
            "pulsed": P / self.rep_rate / (self.sigma * np.sqrt(2 * np.pi)),
        }.get(self.mode, P)

    @property
    def pulse_energy(self) -> float | None:
        return self.power / self.rep_rate if self.mode == "pulsed" else None

    @property
    def mean_photon_number(self) -> float | None:
        """Mean photons per pulse at the laser output (pulsed mode)."""
        e = self.pulse_energy
        return None if e is None else e / photon_energy(self.wavelength)

    def info(self) -> dict[str, str]:
        out = {"Peak power": fmt_si(self.peak_power, "W")}
        if self.mode == "pulsed" and self.power > 0:
            out["Pulse energy"] = fmt_si(self.pulse_energy, "J")
            out["Photons / pulse"] = f"{self.mean_photon_number:.3g}"
        if self.linewidth > 0:
            out["Coherence time"] = fmt_si(1 / (np.pi * self.linewidth), "s")
        if self.rin_db_hz > RIN_OFF and self.power > 0:
            shot = 10 * math.log10(2 * photon_energy(self.wavelength) / self.power)
            out["RIN"] = f"{self.rin_db_hz:g} dB/Hz; shot noise at this power: {shot:.1f} dB/Hz"
        return out

    # ---- field synthesis -----------------------------------------------------------------------
    def envelope(self, t: np.ndarray, dt: float) -> np.ndarray:
        """sqrt of the power averaged over [t, t+dt) — exact for any sampling interval."""
        P = self.power
        if P <= 0:
            return np.zeros_like(t, dtype=float)
        f, mode = self.rep_rate, self.mode
        if mode == "cw":
            avg = np.full_like(t, P, dtype=float)
        elif mode == "sine":
            w = 2 * np.pi * f
            if w * dt < 1e-6:
                avg = P * (1 + self.mod_depth * np.sin(w * t))
            else:
                avg = P * (
                    1
                    + self.mod_depth * (np.cos(w * t) - np.cos(w * (t + dt))) / (w * dt)
                )
        elif mode == "ook":
            d = self.duty

            def on(x):
                c = x * f
                k = np.floor(c)
                return (k * d + np.minimum(c - k, d)) / f

            avg = (P / d) * (on(t + dt) - on(t)) / dt
        else:
            avg = (P / f) * (self._pulse_cum(t + dt) - self._pulse_cum(t)) / dt
        return np.sqrt(np.clip(avg, 0.0, None))

    def _pulse_cum(self, x: np.ndarray) -> np.ndarray:
        """Number of Gaussian pulses (centred on k/f) emitted before time x, up to a constant."""
        f, s = self.rep_rate, self.sigma
        k0 = np.floor(x * f)
        m = int(np.ceil(6 * s * f)) + 1
        acc = k0 - m
        for j in range(-m + 1, m + 1):
            acc = acc + 0.5 * (1 + erf((x - (k0 + j) / f) / (s * math.sqrt(2))))
        return acc

    def intensity_factor(self, t: np.ndarray, dt: float, rng: np.random.Generator) -> np.ndarray | None:
        """√(1 + δ) at emission times ``t`` (bin starts, any shape), or None without intensity noise.

        δ is white with one-sided PSD S = 10^(rin_db_hz/10): its average over a bin of ``dt`` has
        variance S/(2·dt). The bins sit on one absolute grid of ``dt``, so every path of this laser
        that leaves in the same bin carries the same δ, as the phase walk is shared."""
        if self.rin_db_hz <= RIN_OFF:
            return None
        k = np.floor(np.asarray(t, dtype=float) / dt).astype(np.int64)
        if k.size == 0:
            return np.ones(k.shape)
        k0 = int(k.min())
        delta = rng.standard_normal(int(k.max()) - k0 + 1) * math.sqrt(10 ** (self.rin_db_hz / 10) / (2 * dt))
        return np.sqrt(np.clip(1.0 + delta[k - k0], 0.0, None))

    def phase(self, t: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """One joint phase realisation evaluated at arbitrary (possibly repeated) times."""
        flat = np.asarray(t, dtype=float).ravel()
        phi = np.full(flat.shape, rng.uniform(0, 2 * np.pi))
        if self.linewidth > 0 and flat.size:
            order = np.argsort(flat, kind="stable")
            ts = flat[order]
            steps = np.sqrt(
                2 * np.pi * self.linewidth * np.diff(ts, prepend=ts[0])
            ) * rng.standard_normal(ts.size)
            walk = np.empty_like(ts)
            walk[order] = np.cumsum(steps)
            phi += walk
        if self.detuning:
            phi += 2 * np.pi * self.detuning * flat
        if self.phase_randomized and self.mode == "pulsed":
            slot = np.rint(flat * self.rep_rate).astype(np.int64)
            uniq, inv = np.unique(slot, return_inverse=True)
            phi += rng.uniform(0, 2 * np.pi, uniq.size)[inv]
        return phi.reshape(np.shape(t))
