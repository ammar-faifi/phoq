"""Optical detectors. Each absorbs the light reaching its ``inp`` port."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.signal import lfilter

from .core import Component, FiberConnectors, Param
from .units import (
    COMMON_WAVELENGTHS,
    DARK_CURRENT,
    K_B,
    Q_E,
    fmt_si,
    peak_responsivity,
    photon_energy,
    responsivity,
    w_to_dbm,
)


class _Detector(FiberConnectors, Component):
    category = "detector"
    inputs = ("inp",)
    signal_unit = ""


def _curve_info(curve, label: str) -> dict[str, str]:
    """Inspector read-out naming a preset's spectral curve and where it comes from."""
    if curve is None:
        return {}
    lo, hi = curve.span
    return {
        label: (
            f"{lo * 1e9:.0f}–{hi * 1e9:.0f} nm ·"
            f" {curve.provenance.kind.replace('-', ' ')}"
        )
    }


class _Responsive:
    """Mixin for detectors built on PIN photodiodes: the material's typical responsivity
    curve, or a preset's datasheet curve, scaled to a datasheet responsivity at a
    reference wavelength.
    """

    spectral_curves = {"responsivity": "A/W"}

    material = Param(
        "InGaAs", choices=("InGaAs", "Si", "Ge", "GaAs"), label="Detector material"
    )
    ref_responsivity = Param(
        0.0,
        unit="A/W",
        label="Responsivity at λ_ref",
        min=0.0,
        max=2.0,
        doc=(
            "Datasheet responsivity at the reference wavelength; scales the spectral"
            " curve (the preset's, else the material's typical one) to it. 0 = the"
            " curve as is"
        ),
    )
    ref_wavelength = Param(
        1550e-9,
        unit="m",
        label="Reference wavelength λ_ref",
        min=200e-9,
        max=2500e-9,
        presets=COMMON_WAVELENGTHS,
        display=("nm", 1e9),
        doc="Wavelength at which the responsivity is quoted",
    )

    def _shape(self, wavelength: float) -> float:
        """Responsivity curve before scaling: the preset's datasheet curve, else the
        material's."""
        c = self.curve("responsivity")
        return (
            c(wavelength) if c is not None else responsivity(self.material, wavelength)
        )

    def _scale(self) -> float:
        if self.ref_responsivity <= 0:
            return 1.0
        c = self.curve("responsivity")
        ref = self._shape(self.ref_wavelength) or (
            c.peak if c is not None else peak_responsivity(self.material)
        )  # λ_ref outside the curve: anchor at its peak
        return self.ref_responsivity / ref

    def responsivity(self, wavelength: float) -> float:
        return self._shape(wavelength) * self._scale()

    def peak_responsivity(self) -> float:
        """The scaled curve's maximum [A/W]: where datasheets quote conversion gain and
        NEP."""
        c = self.curve("responsivity")
        return (
            c.peak if c is not None else peak_responsivity(self.material)
        ) * self._scale()


class Photodiode(_Responsive, _Detector):
    """Fiber-coupled PIN photodiode; its ``rf`` output carries the photocurrent [A].

    I(t) = Σ R(λ)·P_λ(t) + I_dark plus white shot noise (density 2qI) and thermal noise
    (4kT/R_load), all low-pass filtered by ``bandwidth`` (one pole): the noise then has
    the density 2qI·|H(f)|² and the variance 2qI·B with B = (π/2)·f₃dB. A soft knee
    above ``saturation``.
    """

    prefix = "PD"
    label = "Photodiode"
    description = "InGaAs / Si / Ge / GaAs PIN, BNC output"
    outputs = ("rf",)
    signal_unit = "A"

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
        self.add_port("inp", "optical", "in", "in")
        self.add_port("rf", "electrical", "out", "BNC out")

    def ui_fields(self):
        return [
            "material",
            "ref_responsivity",
            "ref_wavelength",
            "bandwidth",
            "load",
            "saturation",
        ]

    def info(self):
        return _curve_info(self.curve("responsivity"), "Spectral response")

    @property
    def dark_current(self) -> float:
        return DARK_CURRENT[self.material]

    @property
    def settle_time(self) -> float:
        return 6.0 / (2 * math.pi * self.bandwidth)

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
        # Shot noise of the instantaneous photocurrent and thermal noise of the load:
        # white, with one-sided densities 2qI and 4kT/R, sampled up to the Nyquist
        # frequency 1/(2Δt) …
        density = (
            2 * Q_E * np.clip(current, 0, None) + 4 * K_B * self.temperature / self.load
        )
        out = current + np.sqrt(density / (2 * dt)) * rng.standard_normal(n)
        if (
            filtered
            # … then through the detector's pole with the signal: densities times
            # |H(f)|²,
        ):
            # a variance of (2qI + 4kT/R)·(π/2)·f₃dB, the noise bandwidth of one pole
            alpha = -math.expm1(-dt * 2 * math.pi * self.bandwidth)
            out, _ = lfilter(
                [alpha], [1.0, alpha - 1.0], out, zi=[(1 - alpha) * current[0]]
            )
        pos = out > 0
        out[pos] = out[pos] / (1 + (out[pos] / self.saturation) ** 4) ** 0.25
        return out


def _one_pole(x: np.ndarray, bandwidth: float, dt: float, start: float) -> np.ndarray:
    """First-order low-pass, exact for samples of width ``dt``, settled at ``start``."""
    alpha = -math.expm1(-dt * 2 * math.pi * bandwidth)
    y, _ = lfilter([alpha], [1.0, alpha - 1.0], x, zi=[(1 - alpha) * start])
    return y


def _soft_clip(v: np.ndarray, swing: float) -> np.ndarray:
    """An amplifier output running into its rails at ±``swing``: linear to 0.03 % of its
    gain up to 60 % of the swing, 3 % compressed at 80 %, flat beyond (a 16th-power
    knee; the photodiode's own 4th-power one would already take 7 % off the gain at half
    the swing)."""
    return v / (1 + np.minimum(np.abs(v) / swing, 1e6) ** 16) ** (1 / 16)


# Generic switch positions (assumed): gain [V/A] : −3 dB bandwidth [Hz] : NEP [W/√Hz] :
# RF swing [V]
GENERIC_GAINS = (
    "1e3:100e6:50e-12:4, 1e4:30e6:10e-12:4, 1e5:3e6:2e-12:4, 1e6:300e3:1e-12:4,"
    " 1e7:30e3:1e-12:4"
)


class BalancedPhotodetector(_Responsive, _Detector):
    """Balanced amplified photodetector: two matched PIN photodiodes and a
    transimpedance amplifier.

    The diodes behind ``inp_plus`` (INPUT+) and ``inp_minus`` (INPUT−) are wired so that
    their photocurrents subtract, and the TIA turns the difference into the ``rf``
    voltage, U = G·(i₊ − i₋) = G·ℜ(λ)·(P₊ − P₋) into a high-impedance load (half that
    into 50 Ω, the output impedance): light common to both inputs, and with it the
    laser's intensity noise, cancels. The two diodes' responsivities differ by
    10^(−CMRR/20), which sets how much survives. ``mon_plus`` and ``mon_minus`` give
    each diode's current alone, slower (MONITOR±).

    ``gain`` picks a switch position from ``gain_settings``, each with its own
    bandwidth, noise (NEP at peak responsivity) and output swing; a fixed-gain detector
    has a single position. Noise: each diode's shot noise (density 2qi) and the
    amplifier's input noise (NEP·ℜ_peak)², white, then through the TIA's pole with the
    signal.
    """

    prefix = "BPD"
    label = "Balanced photodetector"
    description = (
        "Two matched photodiodes, TIA RF output = G·(i₊ − i₋), two monitor outputs"
    )
    inputs = ("inp_plus", "inp_minus")
    outputs = ("rf",)
    signal_unit = "V"

    gain = Param(
        1e4,
        unit="V/A",
        label="Transimpedance gain",
        min=1.0,
        max=1e9,
        presets=(1e3, 1e4, 1e5, 1e6, 1e7),
        log=True,
        doc=(
            "RF OUTPUT volts per ampere of i₊ − i₋ into a high-impedance load (half"
            " into 50 Ω): one of the gain settings"
        ),
    )
    gain_settings = Param(
        GENERIC_GAINS,
        label="Gain settings",
        doc=(
            "The gain switch's positions, comma separated, each gain [V/A] : −3 dB"
            " bandwidth [Hz] : NEP [W/√Hz, at peak responsivity] : RF swing [V,"
            " high-impedance load]"
        ),
    )
    cmrr_db = Param(
        30.0,
        unit="dB",
        label="CMRR",
        min=0.0,
        max=120.0,
        doc=(
            "Common-mode rejection: the INPUT− diode's responsivity is 1 −"
            " 10^(−CMRR/20) of INPUT+'s"
        ),
    )
    rf_impedance = Param(50.0, unit="Ω", label="RF output impedance", min=0.0, max=1e4)
    monitor_gain = Param(
        10e3,
        unit="V/A",
        label="Monitor gain",
        min=0.0,
        max=1e9,
        doc=(
            "Each MONITOR output's volts per ampere of its own diode's current"
            " (high-impedance load)"
        ),
    )
    monitor_bandwidth = Param(
        1e6, unit="Hz", label="Monitor bandwidth (−3 dB)", min=1.0, max=10e9, log=True
    )
    monitor_swing = Param(10.0, unit="V", label="Monitor swing", min=0.0, max=100.0)
    monitor_impedance = Param(
        220.0, unit="Ω", label="Monitor output impedance", min=0.0, max=1e4
    )
    monitor_noise = Param(
        180e-6, unit="V", label="Monitor noise (RMS)", min=0.0, max=1.0
    )

    def _build(self):
        self.add_port("inp_plus", "optical", "in", "INPUT+")
        self.add_port("inp_minus", "optical", "in", "INPUT−")
        self.add_port("rf", "electrical", "out", "RF OUTPUT")
        self.add_port("mon_plus", "electrical", "out", "MONITOR+")
        self.add_port("mon_minus", "electrical", "out", "MONITOR−")

    def ui_fields(self):
        return ["material", "ref_responsivity", "ref_wavelength", "gain", "cmrr_db"]

    # ---- the gain switch -------------------------------------------------------------
    def settings(self) -> dict[float, tuple[float, float, float]]:
        """Every switch position: gain → (bandwidth, NEP, RF swing)."""
        out = {}
        for item in self.gain_settings.split(","):
            if item.strip():
                try:
                    g, bw, nep, swing = (float(x) for x in item.split(":"))
                except ValueError:
                    raise ValueError(
                        f"{self.name}: gain setting {item.strip()!r} is not"
                        " gain:bandwidth:NEP:swing"
                    ) from None
                out[g] = (bw, nep, swing)
        return out

    def setting(self) -> tuple[float, float, float]:
        """(bandwidth, NEP, RF swing) at the selected gain."""
        table = self.settings()
        for g, v in table.items():
            if math.isclose(g, self.gain, rel_tol=1e-6):
                return v
        offered = ", ".join(fmt_si(g, "V/A") for g in table)
        raise ValueError(
            f"{self.name} has no {fmt_si(self.gain, 'V/A')} gain setting (it offers"
            f" {offered})"
        )

    @property
    def bandwidth(self) -> float:
        return self.setting()[0]

    @property
    def settle_time(self) -> float:
        return 6.0 / (2 * math.pi * min(self.bandwidth, self.monitor_bandwidth))

    def output_impedance(self, port: str) -> float:
        return self.rf_impedance if port == "rf" else self.monitor_impedance

    # ---- physics ---------------------------------------------------------------------
    def responsivities(self, wavelength: float) -> tuple[float, float]:
        """(INPUT+, INPUT−) diode responsivities: the − diode short by 10^(−CMRR/20)."""
        r = self.responsivity(wavelength)
        return r, r * (1 - 10 ** (-self.cmrr_db / 20))

    def mean_currents(
        self, plus: dict[float, float], minus: dict[float, float]
    ) -> tuple[float, float]:
        """Mean photocurrents from the optical powers per wavelength at each input."""
        return (
            sum(self.responsivities(wl)[0] * p for wl, p in plus.items()),
            sum(self.responsivities(wl)[1] * p for wl, p in minus.items()),
        )

    def cw_saturation_power(self, wavelength: float | None = None) -> float:
        """Optical power on one input (or difference of the two) that drives RF OUTPUT
        to its swing."""
        _, _, swing = self.setting()
        r = (
            self.peak_responsivity()
            if wavelength is None
            else self.responsivity(wavelength)
        )
        return swing / (self.gain * r) if r > 0 else math.inf

    def detect(
        self, fields: dict, dt: float, rng: np.random.Generator, n: int
    ) -> dict[str, np.ndarray]:
        """Open-circuit voltages of RF OUTPUT and the two MONITOR outputs; ``fields`` is
        keyed by (input port, wavelength). The three share one noise realization, as on
        the real unit.
        """
        i = {"inp_plus": np.zeros(n), "inp_minus": np.zeros(n)}
        for (port, wl), E in fields.items():
            rp, rm = self.responsivities(wl)
            i[port] = i[port] + (rp if port == "inp_plus" else rm) * np.abs(E) ** 2
        noisy = {
            port: cur
            + np.sqrt(2 * Q_E * np.clip(cur, 0, None) / (2 * dt))
            * rng.standard_normal(n)
            for port, cur in i.items()
        }  # each diode's shot noise, white (density 2qi) up to 1/(2Δt)
        bw, nep, swing = self.setting()
        amp = (
            nep * self.peak_responsivity() / math.sqrt(2 * dt) * rng.standard_normal(n)
        )  # TIA input noise
        diff = i["inp_plus"][0] - i["inp_minus"][0]
        rf = self.gain * _one_pole(
            noisy["inp_plus"] - noisy["inp_minus"] + amp, bw, dt, diff
        )
        out = {"rf": _soft_clip(rf, swing)}
        b_mon = (
            self.monitor_bandwidth * math.pi / 2
        )  # noise bandwidth of the monitor's pole
        for port, mon in (("inp_plus", "mon_plus"), ("inp_minus", "mon_minus")):
            extra = (
                self.monitor_noise / self.monitor_gain if self.monitor_gain else 0.0
            ) / math.sqrt(b_mon)
            x = noisy[port] + extra / math.sqrt(2 * dt) * rng.standard_normal(n)
            v = self.monitor_gain * _one_pole(x, self.monitor_bandwidth, dt, i[port][0])
            out[mon] = (
                _soft_clip(v, self.monitor_swing) if self.monitor_swing > 0 else v
            )
        return out

    def info(self):
        out = _curve_info(self.curve("responsivity"), "Spectral response")
        try:
            bw, nep, swing = self.setting()
        except ValueError as e:
            return out | {"Gain": str(e)}
        r = self.peak_responsivity()
        out |= {
            "RF bandwidth": fmt_si(bw, "Hz"),
            "Conversion gain": (
                f"{fmt_si(self.gain * r, 'V/W')} at peak ℜ (½ into 50 Ω)"
            ),
            "CW saturation": f"{fmt_si(self.cw_saturation_power(), 'W')} at peak ℜ",
            "NEP": fmt_si(nep, "W/√Hz"),
            "Common-mode residue": (
                f"{10 ** (-self.cmrr_db / 20):.2%} ({self.cmrr_db:g} dB)"
            ),
        }
        return out


class SinglePhotonDetector(_Detector):
    """Threshold single-photon detector (SPAD / SNSPD). Its ``out`` port emits 1 V click
    pulses.

    Click probability per time bin: 1 − (1 − p_dark)·exp(−Σ η(λ)·n̄_λ), where n̄_λ is
    the mean photon number in the bin at each wavelength; after a click the detector is
    blind for ``dead_time``. η(λ) = ``efficiency`` everywhere for a generic detector; a
    preset's datasheet PDE curve makes it η(λ) = efficiency · PDE(λ)/PDE(λ_ref), zero
    outside the curve.
    """

    prefix = "SPD"
    label = "Single-photon detector"
    description = "Threshold click detector (SPAD / SNSPD)"
    outputs = ("out",)
    signal_unit = "V"
    spectral_curves = {"efficiency": ""}

    efficiency = Param(0.25, label="Detection efficiency", min=0.0, max=1.0)
    ref_wavelength = Param(
        1550e-9,
        unit="m",
        label="Reference wavelength λ_ref",
        min=200e-9,
        max=2500e-9,
        presets=COMMON_WAVELENGTHS,
        display=("nm", 1e9),
        doc=(
            "Wavelength at which the efficiency is quoted; with a preset's spectral"
            " curve the efficiency at other wavelengths follows the curve"
        ),
    )
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
        self.add_port("inp", "optical", "in", "in")
        self.add_port("out", "electrical", "out", "SMA click out")

    def ui_fields(self):
        fields = ["efficiency", "dark_count_rate", "dead_time"]
        if self.curve("efficiency") is not None:
            fields.insert(1, "ref_wavelength")
        return fields

    def info(self):
        return _curve_info(self.curve("efficiency"), "Spectral PDE")

    def spectral_factor(self, wavelength: float) -> float:
        """η(λ)/efficiency: 1 for a generic detector, PDE(λ)/PDE(λ_ref) with a datasheet
        curve."""
        c = self.curve("efficiency")
        if c is None:
            return 1.0
        ref = (
            c(self.ref_wavelength) or c.peak
        )  # λ_ref outside the curve: anchor at its peak
        return c(wavelength) / ref

    def efficiency_at(self, wavelength: float) -> float:
        """Detection efficiency at a wavelength."""
        return min(1.0, self.efficiency * self.spectral_factor(wavelength))

    def photons(
        self, fields: dict[float, np.ndarray], dt: float, n: int, weighted: bool = False
    ) -> np.ndarray:
        """Mean photon number per bin; ``weighted`` scales each wavelength by its
        spectral factor, so that efficiency × the result is the detected mean."""
        nbar = np.zeros(n)
        for wl, E in fields.items():
            k = self.spectral_factor(wl) if weighted else 1.0
            if k:
                nbar += k * np.abs(E) ** 2 * dt / photon_energy(wl)
        return nbar

    def click_probability(self, nbar: np.ndarray, dt: float) -> np.ndarray:
        p_dark = -math.expm1(-self.dark_count_rate * dt)
        return 1 - (1 - p_dark) * np.exp(-self.efficiency * nbar)

    def detect(
        self, fields, dt, rng, n: int, sub: int = 1, blind: int = 0
    ) -> np.ndarray:
        """Boolean click record, one entry per time bin; ``fields`` may hold ``sub``
        sub-bins per bin, whose photons add up. ``blind`` bins at the start are still
        inside the dead time of a click before this record (:meth:`blind_after` gives it
        for the next one)."""
        nbar = self.photons(fields, dt / sub, n * sub, weighted=True)
        if sub > 1:
            nbar = nbar.reshape(n, sub).sum(axis=1)
        clicks = rng.random(n) < self.click_probability(nbar, dt)
        dead = self.dead_bins(dt)
        if dead > 1 and clicks.any():
            keep, last = [], blind - dead
            for i in np.flatnonzero(clicks):
                if i - last >= dead:
                    keep.append(i)
                    last = i
            clicks = np.zeros(n, dtype=bool)
            clicks[keep] = True
        return clicks

    def dead_bins(self, dt: float) -> int:
        """The dead time in bins of ``dt`` (a click blinds this bin and the next
        dead_bins − 1)."""
        return (
            int(math.ceil(self.dead_time / dt - 1e-9)) if self.dead_time > 0 else 0
        )  # 40 µs / 10 ns is 4000

    def blind_after(self, clicks: np.ndarray, dt: float, blind: int = 0) -> int:
        """Bins of the next record still inside the dead time, after ``clicks`` (which
        started ``blind`` bins blind): what a record cut in pieces hands on, so that a
        dead time longer than one piece is still a dead time."""
        hits = np.flatnonzero(clicks)
        if hits.size:
            return max(0, int(hits[-1]) + self.dead_bins(dt) - clicks.size)
        return max(0, blind - clicks.size)

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
        self.add_port("inp", "optical", "in", "in")

    def reading(self, powers: dict[float, float]) -> float:
        """What the display shows: power weighted by the head's responsivity at each
        λ."""
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
        return (
            f"{self.instrument}: {fmt_si(self.power, 'W')} ({w_to_dbm(self.power):.2f}"
            " dBm)"
        )

    def to_json(self) -> dict:
        return {"instrument": self.instrument, "power": self.power}
