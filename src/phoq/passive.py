"""Passive fiber components. All are linear and reciprocal unless stated otherwise."""

from __future__ import annotations

import cmath
import math

from .core import Component, Param, Transfer
from .units import FIBER_DELAY, N_EFF, db_to_t, fiber_attenuation, fmt_si


def _span(
    length: float, wavelength: float, extra_db: float = 0.0
) -> tuple[complex, float]:
    """Amplitude gain and delay of a length of SMF-28."""
    loss_db = fiber_attenuation(wavelength) * length / 1e3 + extra_db
    phase = (2 * math.pi * N_EFF * length / wavelength) % (2 * math.pi)
    return math.sqrt(db_to_t(loss_db)) * cmath.exp(-1j * phase), length * FIBER_DELAY


class Fiber(Component):
    """Single-mode patch cord with FC/PC connectors at ends ``a`` and ``b``."""

    category = "cord"
    prefix = "FIB"
    label = "Fiber patch cord"
    description = "SMF-28, FC/PC ↔ FC/PC"
    inputs = ("a",)
    outputs = ("b",)
    is_cord = True

    length = Param(
        1.0, unit="m", min=0.05, max=100.0, presets=(0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
    )

    def _build(self):
        self.add_port("a", "optical", "bidi", "end A")
        self.add_port("b", "optical", "bidi", "end B")

    @property
    def delay(self) -> float:
        return self.length * FIBER_DELAY

    def scatter(self, port, wavelength):
        amp, delay = _span(self.length, wavelength)
        other = self.ports["b"] if port.name == "a" else self.ports["a"]
        return [Transfer(other, amp, delay)]

    def info(self):
        return {"Propagation delay": fmt_si(self.delay, "s")}


class FiberSpool(Component):
    """Long fiber on a spool (delay line / transmission link) with two FC bulkheads."""

    category = "passive"
    prefix = "SPL"
    label = "Fiber spool"
    description = "1–100 km SMF-28 delay line"
    inputs = ("p1",)
    outputs = ("p2",)

    length = Param(
        10e3,
        unit="m",
        min=1.0,
        max=500e3,
        presets=(1e3, 2e3, 5e3, 10e3, 25e3, 50e3, 100e3),
    )
    splice_loss_db = Param(0.2, unit="dB", label="Splice loss", min=0.0, max=10.0)

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")

    @property
    def delay(self) -> float:
        return self.length * FIBER_DELAY

    def loss_db(self, wavelength: float) -> float:
        return fiber_attenuation(wavelength) * self.length / 1e3 + self.splice_loss_db

    def scatter(self, port, wavelength):
        amp, delay = _span(self.length, wavelength, self.splice_loss_db)
        other = self.ports["p2"] if port.name == "p1" else self.ports["p1"]
        return [Transfer(other, amp, delay)]

    def info(self):
        return {
            "Delay": fmt_si(self.delay, "s"),
            "Loss @1550 nm": f"{self.loss_db(1550e-9):.2f} dB",
            "Loss @1310 nm": f"{self.loss_db(1310e-9):.2f} dB",
        }


class Attenuator(Component):
    """Variable optical attenuator."""

    category = "passive"
    prefix = "VOA"
    label = "Variable attenuator"
    description = "0–80 dB, bidirectional"
    inputs = ("p1",)
    outputs = ("p2",)

    attenuation_db = Param(10.0, unit="dB", label="Attenuation", min=0.0, max=80.0)
    insertion_loss_db = Param(0.6, unit="dB", label="Insertion loss", min=0.0, max=10.0)

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")

    def scatter(self, port, wavelength):
        other = self.ports["p2"] if port.name == "p1" else self.ports["p1"]
        return [
            Transfer(
                other, math.sqrt(db_to_t(self.attenuation_db + self.insertion_loss_db))
            )
        ]

    def info(self):
        return {"Total loss": f"{self.attenuation_db + self.insertion_loss_db:.1f} dB"}


class BeamSplitter(Component):
    """2×2 fused fiber coupler. ``ratio`` is the bar-port power fraction (a1→b1).

    Field transfer: b1 = √r·a1 + i√(1−r)·a2,  b2 = i√(1−r)·a1 + √r·a2 (and the reverse).
    """

    category = "passive"
    prefix = "BS"
    label = "2×2 fiber coupler"
    description = "Fiber beam splitter, adjustable ratio"
    inputs = ("a1", "a2")
    outputs = ("b1", "b2")

    ratio = Param(0.5, label="Split ratio (bar)", min=0.0, max=1.0)
    excess_loss_db = Param(0.15, unit="dB", label="Excess loss", min=0.0, max=10.0)

    def _build(self):
        for n in ("a1", "a2", "b1", "b2"):
            self.add_port(n)

    def scatter(self, port, wavelength):
        t = math.sqrt(db_to_t(self.excess_loss_db))
        bar, cross = math.sqrt(self.ratio) * t, 1j * math.sqrt(1 - self.ratio) * t
        p = self.ports
        pairs = {
            "a1": ("b1", "b2"),
            "a2": ("b2", "b1"),
            "b1": ("a1", "a2"),
            "b2": ("a2", "a1"),
        }[port.name]
        return [Transfer(p[pairs[0]], bar), Transfer(p[pairs[1]], cross)]

    def info(self):
        return {"Split": f"{self.ratio * 100:.0f} : {(1 - self.ratio) * 100:.0f}"}


Coupler = BeamSplitter


class PhaseShifter(Component):
    """Static phase shift (e.g. a fiber stretcher or thermal tuner)."""

    category = "passive"
    prefix = "PS"
    label = "Phase shifter"
    description = "Static, tunable phase"
    inputs = ("p1",)
    outputs = ("p2",)

    phase = Param(0.0, unit="rad", min=-100.0, max=100.0)
    insertion_loss_db = Param(0.1, unit="dB", label="Insertion loss", min=0.0, max=10.0)

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")

    def scatter(self, port, wavelength):
        other = self.ports["p2"] if port.name == "p1" else self.ports["p1"]
        return [
            Transfer(
                other,
                math.sqrt(db_to_t(self.insertion_loss_db)) * cmath.exp(1j * self.phase),
            )
        ]


class Isolator(Component):
    """Optical isolator: passes p1 → p2, blocks the reverse direction."""

    category = "passive"
    prefix = "ISO"
    label = "Isolator"
    description = "One-way, 40 dB isolation"
    inputs = ("p1",)
    outputs = ("p2",)

    insertion_loss_db = Param(0.5, unit="dB", label="Insertion loss", min=0.0, max=10.0)
    isolation_db = Param(40.0, unit="dB", label="Isolation", min=0.0, max=100.0)

    def _build(self):
        self.add_port("p1", "optical", "in")
        self.add_port("p2", "optical", "out")

    def scatter(self, port, wavelength):
        if port.name == "p1":
            return [
                Transfer(self.ports["p2"], math.sqrt(db_to_t(self.insertion_loss_db)))
            ]
        return [Transfer(self.ports["p1"], math.sqrt(db_to_t(self.isolation_db)))]
