"""Electro-optic modulators.

A modulator has no drive of its own: it applies a voltage-dependent factor to the light passing
through it. The voltage is the sum of what is plugged into its two electrical inputs, ``rf``
(normally a :class:`~phoq.FunctionGenerator` channel) and ``bias`` (normally a
:class:`~phoq.DCPowerSupply` output). An unconnected input contributes 0 V. The
:class:`IQModulator` has two RF inputs and three bias inputs, one per electrode. The
:class:`OpticalSwitch` follows the same rule: its route is set by the voltage on ``ctrl``.
"""

from __future__ import annotations

import math
from typing import ClassVar

import numpy as np

from .core import Component, FiberConnectors, Param, Transfer
from .units import db_to_t

DRIVE_PORTS = ("rf", "bias")


class _Modulator(FiberConnectors, Component):
    category = "modulator"
    inputs = (
        "p1",
        "rf",
    )  # chain() wires the rf drive; the bias is connected explicitly
    outputs = ("p2",)

    rf_ports: ClassVar[tuple[str, ...]] = (
        "rf",
    )  # the drive inputs, checked against rf_input_dbm
    bias_ports: ClassVar[tuple[str, ...]] = (
        "bias",
    )  # the DC inputs, checked against bias_voltage_v

    v_pi = Param(
        4.0, unit="V", label="Vπ", min=0.1, max=20.0, doc="Voltage for a π phase shift"
    )
    v_pi_bias = Param(
        0.0,
        unit="V",
        label="Vπ (bias electrode)",
        min=0.0,
        max=20.0,
        doc="Vπ of the DC electrodes when they differ from the RF ones (datasheets quote both); 0 = same as Vπ",
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
            src
            for src in (self._source(p) for p in self.rf_ports + self.bias_ports)
            if src is not None
        ]

    def is_static(self) -> bool:
        return all(src.is_static(ch) for src, ch in self.drivers())

    def _port_static(self, port: str) -> float:
        src = self._source(port)
        return 0.0 if src is None else src[0].static_voltage(src[1])

    def bias_scale(self) -> float:
        """Factor referring a bias-electrode voltage to the RF electrode: Vπ_RF / Vπ_DC."""
        return self.v_pi / self.v_pi_bias if self.v_pi_bias > 0 else 1.0

    def bias_voltage(self) -> float:
        """DC level on the bias input (the mean of its waveform if it is not a DC source),
        referred to the RF electrode."""
        src = self._source("bias")
        if src is None:
            return 0.0
        gen, ch = src
        v = (
            gen.static_voltage(ch)
            if gen.is_static(ch)
            else float(np.mean(gen.waveform(ch)))
        )
        return v * self.bias_scale()

    def static_voltage(self) -> float:
        """Total RF-referred electrode voltage when nothing is time-varying."""
        return self._port_static("rf") + self._port_static("bias") * self.bias_scale()

    def drive_voltage(self, t_mod, t_emit=None) -> np.ndarray:
        """Total RF-referred electrode voltage rf + bias at the given times."""
        total = np.zeros(np.shape(t_mod))
        for port in DRIVE_PORTS:
            src = self._source(port)
            if src is None:
                continue
            v = src[0].voltage(src[1], t_mod, t_emit)
            total = total + (v * self.bias_scale() if port == "bias" else v)
        return total

    # ---- optics ----------------------------------------------------------------------------------
    def response(self, volts) -> np.ndarray:
        """Complex field factor for a total electrode voltage (overridden)."""
        raise NotImplementedError

    def factor(self, t_mod, t_emit) -> np.ndarray:
        return self.response(self.drive_voltage(t_mod, t_emit))

    def static_factor(self) -> complex:
        """Complex field factor when nothing driving it is time-varying."""
        return complex(np.asarray(self.response([self.static_voltage()]))[0])

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
        if self.v_pi_bias > 0:
            out["Bias electrode"] = (
                f"Vπ {self.v_pi_bias:g} V: quadrature at {self.v_pi_bias / 2:g} V, null at {self.v_pi_bias:g} V"
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


class IQModulator(_Modulator):
    """Optical IQ modulator: a dual-parallel Mach–Zehnder (Seimetz 2009, §2.1.2, Fig. 2.3).

    Light splits into an in-phase (I) and a quadrature (Q) arm, each a push–pull MZM, and recombines
    after a relative phase set on a third DC electrode. Each MZM gives the signed field behind the
    intensity modulator's T = ½[1 + k·cos φ], k from the extinction ratio:

        e(φ) = [√(1+k)·cos(φ/2) + j·√(1−k)·sin(φ/2)] / √2,
        φ_I = π·(V_rf,I + V_DC1·Vπ/Vπ_bias)/Vπ + φ0_I,   φ_Q likewise from rf_q and DC2,

    and the outer interferometer adds the two arms (Eq. 2.11):

        E_out/E_in = ½·[e(φ_I) + e^{jφ_P}·e(φ_Q)],   φ_P = π·V_DC3/Vπ_phase + φ0_P.

    At the operating point, both MZMs at their null (DC1 = DC2 = −Vπ_bias) and the arms in
    quadrature (DC3 = Vπ_phase/2), this is Eq. (2.16): E_out/E_in = ½[cos(Δφ_I/2) + j·cos(Δφ_Q/2)].
    The drive u = (2Vπ/π)·arcsin(i) of Eqs. (2.46)–(2.47) (:meth:`voltage_for`) then puts the field at
    ½(i + jq) for any point (i, q) of the unit square. The MZMs' finite extinction leaves a carrier of
    (1 − k)/4 of full transmission at that point, and the φ0 are path imbalances that move every
    bias point (a bias controller would track them; not modelled).
    """

    prefix = "IQM"
    label = "IQ modulator"
    description = "Dual-parallel MZM: I and Q MZMs and an I/Q phase; rf_i, rf_q and three DC biases"
    inputs = ("p1", "rf_i", "rf_q")
    rf_ports = ("rf_i", "rf_q")
    bias_ports = ("bias_i", "bias_q", "bias_p")

    v_pi_phase = Param(
        0.0,
        unit="V",
        label="Vπ (phase electrode)",
        min=0.0,
        max=30.0,
        doc="Vπ of the DC electrode setting the I/Q phase; 0 = same as the I and Q bias electrodes",
    )
    extinction_db = Param(
        30.0, unit="dB", label="Extinction ratio (each MZM)", min=0.1, max=80.0
    )
    phi0_i = Param(
        0.0,
        unit="rad",
        label="φ₀ of the I MZM",
        min=-math.pi,
        max=math.pi,
        doc="Static path imbalance of the I MZM; moves its null away from −Vπ",
    )
    phi0_q = Param(
        0.0,
        unit="rad",
        label="φ₀ of the Q MZM",
        min=-math.pi,
        max=math.pi,
        doc="Static path imbalance of the Q MZM; moves its null away from −Vπ",
    )
    phi0_p = Param(
        0.0,
        unit="rad",
        label="φ₀ of the I/Q phase",
        min=-math.pi,
        max=math.pi,
        doc="Static phase between the arms; moves quadrature away from Vπ_phase/2",
    )

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")
        self.add_port("rf_i", "electrical", "in", "RF1 drive in (I)")
        self.add_port("rf_q", "electrical", "in", "RF2 drive in (Q)")
        self.add_port("bias_i", "electrical", "in", "DC1 bias in (I)")
        self.add_port("bias_q", "electrical", "in", "DC2 bias in (Q)")
        self.add_port("bias_p", "electrical", "in", "DC3 bias in (I/Q phase)")

    @property
    def contrast(self) -> float:
        """Each MZM's arm-imbalance factor k = 2√(P₁P₂)/P_in, from its extinction ratio."""
        er = 10 ** (self.extinction_db / 10)
        return (er - 1) / (er + 1)

    @property
    def v_pi_dc(self) -> float:
        """Vπ of the I and Q bias electrodes."""
        return self.v_pi_bias or self.v_pi

    @property
    def v_pi_p(self) -> float:
        """Vπ of the phase electrode."""
        return self.v_pi_phase or self.v_pi_dc

    # ---- the transfer function --------------------------------------------------------------
    def field(self, rf_i, rf_q, dc_i=0.0, dc_q=0.0, dc_p=0.0) -> np.ndarray:
        """E_out/E_in (insertion loss aside) for the voltages on rf_i, rf_q and the three DC
        electrodes, each as applied at its own port."""
        k = self.contrast
        a, b = math.sqrt((1 + k) / 2), math.sqrt((1 - k) / 2)

        def mzm(rf, dc, phi0):
            phi = (
                np.pi
                * (
                    np.asarray(rf, dtype=float) / self.v_pi
                    + np.asarray(dc, dtype=float) / self.v_pi_dc
                )
                + phi0
            )
            return a * np.cos(phi / 2) + 1j * b * np.sin(phi / 2)

        phi_p = np.pi * np.asarray(dc_p, dtype=float) / self.v_pi_p + self.phi0_p
        return 0.5 * (
            mzm(rf_i, dc_i, self.phi0_i)
            + np.exp(1j * phi_p) * mzm(rf_q, dc_q, self.phi0_q)
        )

    def voltage_for(self, i, q=None) -> tuple[np.ndarray, np.ndarray]:
        """RF drives (V_I, V_Q) that place the field at ½(i + jq), for i and q in [−1, 1], with both
        MZMs at their null at −Vπ: u = (2Vπ/π)·arcsin(i), Eqs. (2.46)–(2.47). ``i`` may also be
        complex symbols i + jq. At the null at +Vπ instead, that axis comes out mirrored.
        """
        if q is None:
            z = np.asarray(i, dtype=complex)
            i, q = z.real, z.imag

        def u(x):
            return (
                2
                * self.v_pi
                / np.pi
                * np.arcsin(np.clip(np.asarray(x, dtype=float), -1.0, 1.0))
            )

        return u(i), u(q)

    def operating_bias(self) -> dict[str, float]:
        """DC voltages for the operating point of Eq. (2.16) when every φ0 is zero: both MZMs at
        their null at −Vπ_bias, the arms in quadrature at +Vπ_phase/2."""
        return {
            "bias_i": -self.v_pi_dc,
            "bias_q": -self.v_pi_dc,
            "bias_p": self.v_pi_p / 2,
        }

    # ---- the engine's modulator contract ----------------------------------------------------
    def _volts(self, port: str, t_mod, t_emit=None) -> np.ndarray:
        src = self._source(port)
        if src is None:
            return np.zeros(np.shape(t_mod))
        return np.asarray(src[0].voltage(src[1], t_mod, t_emit), dtype=float)

    def _samples(self, port: str) -> np.ndarray:
        """The voltage on ``port`` over one period (every symbol of a pattern)."""
        src = self._source(port)
        if src is None:
            return np.zeros(1)
        gen, ch = src
        return (
            np.array([gen.static_voltage(ch)])
            if gen.is_static(ch)
            else np.asarray(gen.waveform(ch), dtype=float)
        )

    def static_voltage(self) -> float:
        raise TypeError(
            f"{self.name}: an IQ modulator has five electrodes; use static_factor()"
        )

    def response(self, volts) -> np.ndarray:
        raise TypeError(
            f"{self.name}: an IQ modulator has five electrodes; use field() or static_factor()"
        )

    def factor(self, t_mod, t_emit) -> np.ndarray:
        return self.field(
            *(self._volts(p, t_mod, t_emit) for p in self.rf_ports + self.bias_ports)
        )

    def static_factor(self) -> complex:
        return complex(
            np.asarray(
                self.field(
                    *(self._port_static(p) for p in self.rf_ports + self.bias_ports)
                )
            )
        )

    def mean_factor(self) -> float:
        """Time-averaged |E_out/E_in|² of the present drive (DC inputs taken at their mean). I and Q
        loaded as patterns of one length are taken symbol by symbol; otherwise as unrelated.
        """
        vi, vq = self._samples("rf_i"), self._samples("rf_q")
        if vi.size != vq.size:
            vi, vq = np.meshgrid(vi, vq)
        dc = [float(self._samples(p).mean()) for p in self.bias_ports]
        return float(np.mean(np.abs(self.field(vi, vq, *dc)) ** 2))

    def phases(self) -> tuple[float, float, float]:
        """(φ_I, φ_Q, φ_P) at the present DC levels with no RF [rad]: −π, −π and π/2 at the
        operating point."""
        dc = {p: float(self._samples(p).mean()) for p in self.bias_ports}
        return (
            np.pi * dc["bias_i"] / self.v_pi_dc + self.phi0_i,
            np.pi * dc["bias_q"] / self.v_pi_dc + self.phi0_q,
            np.pi * dc["bias_p"] / self.v_pi_p + self.phi0_p,
        )

    def info(self):
        out = {}
        for port, label in (
            ("rf_i", "RF1 (I)"),
            ("rf_q", "RF2 (Q)"),
            ("bias_i", "DC1 (I)"),
            ("bias_q", "DC2 (Q)"),
            ("bias_p", "DC3 (I/Q phase)"),
        ):
            src = self._source(port)
            out[label] = (
                f"{src[0].name} {src[1].upper()} · {src[0].describe(src[1])}"
                if src
                else "none (0 V)"
            )
        wrap = lambda x: (math.degrees(x) + 180) % 360 - 180  # noqa: E731
        pi, pq, pp = self.phases()
        out["Bias point"] = (
            f"I {wrap(pi):+.1f}°, Q {wrap(pq):+.1f}° (null ±180°); I/Q phase {wrap(pp):+.1f}° (quadrature 90°)"
        )
        carrier = float(
            np.abs(
                self.field(
                    0.0, 0.0, *(float(self._samples(p).mean()) for p in self.bias_ports)
                )
            )
            ** 2
        )
        out["Carrier at this bias"] = (
            f"{10 * math.log10(max(carrier, 1e-12)):.1f} dB of full transmission"
        )
        return out


class _SwitchRoute:
    """One of the two routes through an :class:`OpticalSwitch`, as the engine sees it: a
    time-dependent amplitude factor driven by the switch's control input (same contract as a
    modulator: ``factor``, ``response``, ``is_static``, ``static_voltage``, ``static_factor``,
    ``mean_factor``, ``drivers``)."""

    def __init__(self, switch: OpticalSwitch, crossed: bool):
        self.switch, self.crossed = switch, crossed

    def drivers(self) -> list:
        return self.switch.drivers()

    def is_static(self) -> bool:
        return self.switch.is_static()

    def static_voltage(self) -> float:
        return self.switch.static_voltage()

    def static_factor(self) -> complex:
        return complex(np.asarray(self.response([self.static_voltage()]))[0])

    def response(self, volts) -> np.ndarray:
        return self.switch.route_amplitude(volts, self.crossed)

    def factor(self, t_mod, t_emit) -> np.ndarray:
        return self.response(self.switch.drive_voltage(t_mod, t_emit))

    def mean_factor(self) -> float:
        return float(np.mean(np.abs(self.response(self.switch.drive_samples())) ** 2))

    def __repr__(self):
        return f"<{self.switch.name} {'cross' if self.crossed else 'bar'} route>"


class OpticalSwitch(FiberConnectors, Component):
    """2×2 electro-optic switch: *bar* (a1→b1, a2→b2) or *cross* (a1→b2, a2→b1).

    Like a modulator it has no setting of its own: the state follows the voltage on its ``ctrl``
    input — cross at or above ``threshold``, bar below, and bar when nothing is connected (0 V).
    A function-generator pattern on ``ctrl`` therefore routes pulse by pulse (an eavesdropper's
    tap, time multiplexing, a protected path). ``voltage_for(cross)`` gives the drive for a wanted
    sequence of states. The unselected route leaks ``crosstalk_db`` below the selected one, both
    carry ``insertion_loss_db``, and the device is reciprocal.
    """

    category = "modulator"
    prefix = "SW"
    label = "2×2 optical switch"
    description = (
        "Electro-optic 2×2 switch: bar below the threshold voltage on ctrl, cross above"
    )
    inputs = ("a1", "a2", "ctrl")
    outputs = ("b1", "b2")

    threshold = Param(
        1.0,
        unit="V",
        label="Switch threshold",
        min=0.1,
        max=30.0,
        doc="ctrl ≥ threshold selects the cross state; voltage_for() swings 0 … 2×threshold",
    )
    insertion_loss_db = Param(1.0, unit="dB", label="Insertion loss", min=0.0, max=20.0)
    crosstalk_db = Param(
        40.0,
        unit="dB",
        label="Crosstalk",
        min=0.0,
        max=100.0,
        doc="Leak into the route that is not selected",
    )

    def _build(self):
        for n in ("a1", "b1", "a2", "b2"):
            self.add_port(n)
        self.add_port("ctrl", "electrical", "in", "Control in")
        self._routes = {
            False: _SwitchRoute(self, False),
            True: _SwitchRoute(self, True),
        }

    # ---- the control input (same contract as the modulators' rf/bias inputs) ------------------
    def _source(self):
        peer = self.ports["ctrl"].peer
        if peer is None or not callable(getattr(peer.component, "voltage", None)):
            return None
        return peer.component, peer.name

    def drivers(self) -> list:
        src = self._source()
        return [src] if src is not None else []

    def is_static(self) -> bool:
        src = self._source()
        return src is None or src[0].is_static(src[1])

    def static_voltage(self) -> float:
        src = self._source()
        return 0.0 if src is None else src[0].static_voltage(src[1])

    def drive_voltage(self, t_mod, t_emit=None) -> np.ndarray:
        src = self._source()
        if src is None:
            return np.zeros(np.shape(t_mod))
        return np.asarray(src[0].voltage(src[1], t_mod, t_emit), dtype=float)

    def drive_samples(self) -> np.ndarray:
        """The control waveform over one period (or every symbol of a pattern), for averaging."""
        src = self._source()
        if src is None or src[0].is_static(src[1]):
            return np.array([self.static_voltage()])
        return np.asarray(src[0].waveform(src[1]), dtype=float)

    # ---- routing --------------------------------------------------------------------------------
    def crossed(self, volts) -> np.ndarray:
        """True where the control voltage selects the cross state."""
        return np.asarray(volts, dtype=float) >= self.threshold

    def route_amplitude(self, volts, crossed_route: bool) -> np.ndarray:
        """Field factor of the bar (False) or cross (True) route at the given control voltages."""
        leak = math.sqrt(db_to_t(self.crosstalk_db))
        selected = self.crossed(volts) == crossed_route
        return np.where(selected, math.sqrt(1.0 - leak * leak), leak)

    def voltage_for(self, cross) -> np.ndarray:
        """Control voltage per symbol for the wanted state: 0 V for bar, 2×threshold for cross."""
        return np.where(np.asarray(cross, dtype=bool), 2.0 * self.threshold, 0.0)

    def scatter(self, port, wavelength):
        t = math.sqrt(db_to_t(self.insertion_loss_db))
        p = self.ports
        bar, cross = {
            "a1": ("b1", "b2"),
            "a2": ("b2", "b1"),
            "b1": ("a1", "a2"),
            "b2": ("a2", "a1"),
        }[port.name]
        return [
            Transfer(p[bar], t, 0.0, self._routes[False]),
            Transfer(p[cross], t, 0.0, self._routes[True]),
        ]

    def info(self):
        src = self._source()
        out = {
            "Control": (
                f"{src[0].name} {src[1].upper()} · {src[0].describe(src[1])}"
                if src
                else "none (0 V → bar)"
            )
        }
        if self.is_static():
            out["State"] = "cross" if self.crossed(self.static_voltage()) else "bar"
        else:
            out["Cross fraction"] = (
                f"{float(np.mean(self.crossed(self.drive_samples()))):.3g}"
            )
        return out
