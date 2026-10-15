"""Electro-optic modulators.

A modulator has no drive of its own: it applies a voltage-dependent factor to the light passing
through it. The voltage is the sum of what is plugged into its two electrical inputs, ``rf``
(normally a :class:`~phoq.FunctionGenerator` channel) and ``bias`` (normally a
:class:`~phoq.DCPowerSupply` output). An unconnected input contributes 0 V.
"""

from __future__ import annotations

import math

import numpy as np

from .core import Component, Param, Transfer
from .units import db_to_t

DRIVE_PORTS = ("rf", "bias")


class _Modulator(Component):
    category = "modulator"
    inputs = (
        "p1",
        "rf",
    )  # chain() wires the rf drive; the bias is connected explicitly
    outputs = ("p2",)

    v_pi = Param(
        4.0, unit="V", label="Vπ", min=0.1, max=20.0, doc="Voltage for a π phase shift"
    )
    insertion_loss_db = Param(3.0, unit="dB", label="Insertion loss", min=0.0, max=20.0)

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")
        self.add_port("rf", "electrical", "in", "RF drive in")
        self.add_port("bias", "electrical", "in", "DC bias in")

    # ---- the electrical inputs -----------------------------------------------------------------
    def _source(self, port: str):
        """(component, channel) feeding ``port``, or None."""
        peer = self.ports[port].peer
        if peer is None or not callable(getattr(peer.component, "voltage", None)):
            return None
        return peer.component, peer.name

    def driver(self):
        """(generator, channel) on the rf input, or None."""
        return self._source("rf")

    def drivers(self) -> list:
        """Every (source, channel) connected to the rf and bias inputs."""
        return [
            src for src in (self._source(p) for p in DRIVE_PORTS) if src is not None
        ]

    def is_static(self) -> bool:
        return all(src.is_static(ch) for src, ch in self.drivers())

    def _port_static(self, port: str) -> float:
        src = self._source(port)
        return 0.0 if src is None else src[0].static_voltage(src[1])

    def bias_voltage(self) -> float:
        """DC level on the bias input (the mean of its waveform if it is not a DC source)."""
        src = self._source("bias")
        if src is None:
            return 0.0
        gen, ch = src
        return (
            gen.static_voltage(ch)
            if gen.is_static(ch)
            else float(np.mean(gen.waveform(ch)))
        )

    def static_voltage(self) -> float:
        """Total voltage on the electrodes when nothing is time-varying."""
        return self._port_static("rf") + self._port_static("bias")

    def drive_voltage(self, t_mod, t_emit=None) -> np.ndarray:
        """Total electrode voltage rf + bias at the given times."""
        total = np.zeros(np.shape(t_mod))
        for src, ch in self.drivers():
            total = total + src.voltage(ch, t_mod, t_emit)
        return total

    # ---- optics ----------------------------------------------------------------------------------
    def response(self, volts) -> np.ndarray:
        """Complex field factor for a total electrode voltage (overridden)."""
        raise NotImplementedError

    def factor(self, t_mod, t_emit) -> np.ndarray:
        return self.response(self.drive_voltage(t_mod, t_emit))

    def mean_factor(self) -> float:
        """Time-averaged power factor |response|² of the current drive (bias taken as DC)."""
        if self.is_static():
            v = np.array([self.static_voltage()])
        else:
            rf = self._source("rf")
            swing = (
                rf[0].waveform(rf[1])
                if rf is not None and not rf[0].is_static(rf[1])
                else np.array([self._port_static("rf")])
            )
            v = swing + self.bias_voltage()
        return float(np.mean(np.abs(self.response(v)) ** 2))

    def scatter(self, port, wavelength):
        other = self.ports["p2"] if port.name == "p1" else self.ports["p1"]
        return [Transfer(other, math.sqrt(db_to_t(self.insertion_loss_db)), 0.0, self)]

    def info(self):
        out = {}
        for port, label in (("rf", "RF drive"), ("bias", "Bias")):
            src = self._source(port)
            out[label] = (
                f"{src[0].name} {src[1].upper()} · {src[0].describe(src[1])}"
                if src
                else "none (0 V)"
            )
        return out


class PhaseModulator(_Modulator):
    """LiNbO₃ phase modulator: φ = π·(V_rf + V_bias)/Vπ."""

    prefix = "PM"
    label = "Phase modulator"
    description = "LiNbO₃, φ = π·V/Vπ, rf + bias inputs"

    def response(self, volts):
        return np.exp(1j * np.pi * np.asarray(volts, dtype=float) / self.v_pi)

    def voltage_for(self, phase):
        """rf voltage giving a phase shift of ``phase`` radians on top of the bias."""
        return np.asarray(phase, dtype=float) * self.v_pi / np.pi - self.bias_voltage()

    def info(self):
        out = super().info()
        if self.is_static():
            out["Static phase"] = f"{np.pi * self.static_voltage() / self.v_pi:.3f} rad"
        return out


class IntensityModulator(_Modulator):
    """Mach–Zehnder intensity modulator (push–pull, Fraunhofer IIS 2024 Eqs. 4–10).

    The two arms carry powers P₁ + P₂ = P_in and recombine with a phase difference
    φ = π(V_rf + V_bias)/Vπ + φ₀, so the output is P = ½P_in + √(P₁P₂)·cos φ.  Writing the
    arm imbalance as k = 2√(P₁P₂)/P_in gives the transmission

        T(V) = ½[1 + k·cos(π·V/Vπ + φ₀)],   k = (ER − 1)/(ER + 1),

    with T_max = ER/(ER+1) and T_min = 1/(ER+1): a finite extinction ratio ER comes from the
    arms not splitting exactly 50:50, not from a clamp. 0 V is maximum transmission, Vπ/2
    quadrature (the linear point) and Vπ the null; φ₀ is the intrinsic path imbalance that
    drifts in real devices (a bias controller would null it — not modelled).
    """

    prefix = "IM"
    label = "Intensity modulator"
    description = "Mach–Zehnder, T = ½[1 + k·cos(πV/Vπ + φ₀)], rf + bias inputs"

    extinction_db = Param(30.0, unit="dB", label="Extinction ratio", min=0.1, max=80.0)
    phi0 = Param(
        0.0,
        unit="rad",
        label="Intrinsic phase imbalance φ₀",
        min=-math.pi,
        max=math.pi,
        doc="Static arm path difference; shifts the whole curve along V (bias drift, not modelled, would move it)",
    )

    @property
    def contrast(self) -> float:
        """Arm-imbalance factor k = 2√(P₁P₂)/P_in, from the extinction ratio."""
        er = 10 ** (self.extinction_db / 10)
        return (er - 1) / (er + 1)

    @property
    def t_max(self) -> float:
        return 0.5 * (1 + self.contrast)

    @property
    def t_min(self) -> float:
        return 0.5 * (1 - self.contrast)

    floor = t_min

    def transmission(self, volts) -> np.ndarray:
        """Power transmission for a total electrode voltage (relative to the insertion loss)."""
        v = np.asarray(volts, dtype=float)
        return 0.5 * (1.0 + self.contrast * np.cos(np.pi * v / self.v_pi + self.phi0))

    def response(self, volts):
        return np.sqrt(self.transmission(volts))

    def voltage_for(self, transmission):
        """rf voltage giving power transmission ``transmission`` (clipped to [T_min, T_max]) on top of the bias."""
        t = np.clip(np.asarray(transmission, dtype=float), self.t_min, self.t_max)
        return (
            self.v_pi / np.pi * (np.arccos((2.0 * t - 1.0) / self.contrast) - self.phi0)
            - self.bias_voltage()
        )

    def info(self):
        out = super().info()
        label = "Static transmission" if self.is_static() else "Mean transmission"
        t = (
            float(self.transmission(self.static_voltage()))
            if self.is_static()
            else self.mean_factor()
        )
        out[label] = f"{t:.3g} ({10 * math.log10(max(t, 1e-12)):.1f} dB)"
        return out
