"""Passive fiber components. All are linear and reciprocal unless stated otherwise."""

from __future__ import annotations

import cmath
import math

from .core import Component, FiberConnectors, Param, Transfer
from .units import C0, CONNECTORS, FIBER_DELAY, N_AIR, N_EFF, db_to_t, fiber_attenuation, fmt_si


def _span(
    length: float, wavelength: float, extra_db: float = 0.0
) -> tuple[complex, float]:
    """Amplitude gain and delay of a length of SMF-28."""
    loss_db = fiber_attenuation(wavelength) * length / 1e3 + extra_db
    phase = (2 * math.pi * N_EFF * length / wavelength) % (2 * math.pi)
    return math.sqrt(db_to_t(loss_db)) * cmath.exp(-1j * phase), length * FIBER_DELAY


class Fiber(Component):
    """Single-mode patch cord. Its ends ``a`` and ``b`` each carry an FC/UPC (blue) or FC/APC
    (green) connector; a cord with one of each is a hybrid cord."""

    category = "cord"
    prefix = "FIB"
    label = "Fiber patch cord"
    description = "SMF-28, FC/UPC or FC/APC ends"
    inputs = ("a",)
    outputs = ("b",)
    is_cord = True

    length = Param(
        1.0, unit="m", min=0.05, max=100.0, presets=(0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
    )
    connector_a = Param("FC/UPC", choices=CONNECTORS, label="Connector A")
    connector_b = Param("FC/UPC", choices=CONNECTORS, label="Connector B")

    def _build(self):
        self.add_port("a", "optical", "bidi", "end A")
        self.add_port("b", "optical", "bidi", "end B")

    def connector_of(self, port):
        return self.connector_b if port == "b" else self.connector_a

    @property
    def delay(self) -> float:
        return self.length * FIBER_DELAY

    def scatter(self, port, wavelength):
        amp, delay = _span(self.length, wavelength)
        other = self.ports["b"] if port.name == "a" else self.ports["a"]
        return [Transfer(other, amp, delay)]

    def info(self):
        return {"Propagation delay": fmt_si(self.delay, "s")}


class FiberSpool(FiberConnectors, Component):
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


class MatingSleeve(Component):
    """FC–FC mating sleeve (fiber adapter): joins two patch cords end to end.

    It has no polish of its own: the connectors plugged into its sides ``p1`` and ``p2`` mate with
    each other inside it, as one mated pair with the loss and reflection of their two types — a
    FC/APC cord against a FC/UPC one leaves an air gap here like anywhere else. With one side
    empty, the other cord's end is an open connector. Cascading cords through sleeves shows what
    every extra connection costs in transmitted power and adds in back-reflection.
    """

    category = "passive"
    prefix = "SLV"
    label = "FC mating sleeve"
    description = "FC–FC adapter: joins two patch cords, their connectors mate inside"
    inputs = ("p1",)
    outputs = ("p2",)
    is_adaptor = True

    def _build(self):
        self.add_port("p1", "optical", "bidi", "side 1")
        self.add_port("p2", "optical", "bidi", "side 2")

    def other(self, port: str):
        """The port plugged into the opposite side of ``port`` (None while empty)."""
        return self.ports["p2" if port == "p1" else "p1"].peer

    def connector_of(self, port):
        far = self.other(port)
        return None if far is None or far.component.is_adaptor else far.connector

    def info(self):
        from .units import RETURN_LOSS_DB, mating

        a, b = self.ports["p1"].peer, self.ports["p2"].peer
        if a is None or b is None:
            held = a or b
            return {"Mating": f"{held.path} ({held.connector}) meets an empty side" if held else "empty"}
        t, ra, rb = mating(a.connector, b.connector)
        return {
            "Mating": f"{a.path} ({a.connector}) ↔ {b.path} ({b.connector})",
            "Insertion loss": f"{-10 * math.log10(t):.2f} dB",
            "Reflected back": f"{10 * math.log10(ra):.1f} dB into {a.path} · {10 * math.log10(rb):.1f} dB into {b.path}",
        }


class Attenuator(FiberConnectors, Component):
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


class BeamSplitter(FiberConnectors, Component):
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


class PhaseShifter(FiberConnectors, Component):
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


class OpticalDelayLine(FiberConnectors, Component):
    """Variable optical delay line: collimator → air gap set by a translation stage → collimator.

    The dial ``delay`` τ adds to the fixed ``insertion_delay`` τ₀ (internal fiber and the shortest
    gap). Air is nearly dispersionless, so one τ sets both the group delay and the carrier phase
    2πν·τ: a fringe per optical period, 5.17 fs at 1550 nm. The line sets *timing*; a phase shifter
    sets the phase. The collimated Gaussian beam (1/e² radius ``beam_waist``, Rayleigh range
    z_R = πw₀²/λ) couples into the output fiber best at mid-travel; a gap d away from that couples
    1/[1 + (d/2z_R)²], so the loss grows toward both ends of the travel. A dial set beyond
    ``max_delay`` stops at the end of the travel, and ``resolution`` (0: continuous) is the step.
    """

    category = "passive"
    prefix = "ODL"
    label = "Optical delay line"
    description = "Variable free-space delay, 0–330 ps"
    inputs = ("p1",)
    outputs = ("p2",)

    delay = Param(
        0.0, unit="s", label="Delay", min=0.0, max=10e-9, display=("ps", 1e12),
        doc="Dial setting: the delay added to the delay at zero",
    )
    max_delay = Param(
        330e-12, unit="s", label="Delay range", min=1e-12, max=10e-9, display=("ps", 1e12),
        doc="Full travel of the stage, as delay",
    )
    insertion_delay = Param(
        100e-12, unit="s", label="Delay at zero", min=0.0, max=100e-9, display=("ps", 1e12),
        doc="Connector-to-connector delay at the zero setting (internal fiber and shortest gap)",
    )
    resolution = Param(
        0.0, unit="s", label="Resolution", min=0.0, max=10e-12, display=("fs", 1e15),
        doc="Smallest delay step of the stage (0 = continuous)",
    )
    insertion_loss_db = Param(
        1.0, unit="dB", label="Insertion loss", min=0.0, max=10.0,
        doc="Loss at the best-coupled gap (mid-travel)",
    )
    beam_waist = Param(
        0.25e-3, unit="m", label="Beam waist", min=10e-6, max=5e-3, display=("µm", 1e6),
        doc="1/e² radius of the collimated beam between the collimators",
    )

    def _build(self):
        self.add_port("p1")
        self.add_port("p2")

    @property
    def setting(self) -> float:
        """The delay the stage reaches: the dial on the step grid, stopped at the end of travel."""
        tau = self.delay
        if self.resolution > 0:
            tau = round(tau / self.resolution) * self.resolution
        return min(tau, self.max_delay)

    @property
    def total_delay(self) -> float:
        return self.insertion_delay + self.setting

    def coupling(self, wavelength: float, setting: float | None = None) -> float:
        """Gaussian mode overlap with the output fiber (1 at mid-travel), now or at ``setting``."""
        tau = self.setting if setting is None else setting
        z_r = math.pi * self.beam_waist**2 / wavelength
        d = C0 * (tau - self.max_delay / 2) / N_AIR  # air gap away from the best one
        return 1.0 / (1.0 + (d / (2 * z_r)) ** 2)

    def loss_db(self, wavelength: float, setting: float | None = None) -> float:
        return self.insertion_loss_db - 10 * math.log10(self.coupling(wavelength, setting))

    def scatter(self, port, wavelength):
        # The fixed part counts as fiber of the same delay; the air path adds 2πν·τ.
        cycles = (N_EFF * self.insertion_delay / FIBER_DELAY + C0 * self.setting) / wavelength
        amp = math.sqrt(db_to_t(self.loss_db(wavelength))) * cmath.exp(-2j * math.pi * (cycles % 1.0))
        other = self.ports["p2"] if port.name == "p1" else self.ports["p1"]
        return [Transfer(other, amp, self.total_delay)]

    def info(self):
        wl = 1550e-9
        out = {
            "Total delay": fmt_si(self.total_delay, "s"),
            "Air path added": fmt_si(C0 * self.setting / N_AIR, "m"),
            "Loss @1550 nm": f"{self.loss_db(wl):.2f} dB (ends of travel {self.loss_db(wl, 0.0):.2f} dB)",
            "Fringe period @1550 nm": fmt_si(wl / C0, "s"),
        }
        if self.delay > self.max_delay:
            out["Stage"] = f"at the end stop ({fmt_si(self.max_delay, 's')})"
        return out


class Isolator(FiberConnectors, Component):
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
