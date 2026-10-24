"""Running experiments: acquire instruments, sweep parameters."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

import numpy as np

from .core import Component, Setup
from .engine import Network, report, trace


def instruments_of(setup: Setup) -> list[Component]:
    """Every component that can be acquired (oscilloscopes, counters, power meters)."""
    return [c for c in setup if callable(getattr(c, "acquire", None))]


class Results(dict):
    """Instrument name → result object, plus the steady-state ``report`` of the
    network."""

    def __init__(self, *args, report: dict | None = None, **kw):
        super().__init__(*args, **kw)
        self.report = report or {}

    def summary(self) -> str:
        lines = [r.summary() for r in self.values()]
        lines += [f"! {w['text']}" for w in self.report.get("warnings", [])]
        return "\n".join(lines)


@dataclass
class Sweep:
    component: str
    param: str
    values: np.ndarray
    measured: np.ndarray

    def plot(self, ax=None, **kw):
        """Matplotlib plot of the measured value against the swept parameter, in the
        dark_background style."""
        import matplotlib.pyplot as plt

        with plt.style.context("dark_background"):
            ax = ax or plt.subplots(figsize=(6, 3.6))[1]
            ax.plot(self.values, self.measured, "o-", **kw)
            ax.set(xlabel=f"{self.component}.{self.param}", ylabel="measured")
        return ax


class Runner:
    """Runs a :class:`Setup` headlessly. One random generator per runner
    (seed for repeatability)."""

    def __init__(self, setup: Setup, seed: int | None = None):
        self.setup = setup
        self.rng = np.random.default_rng(seed)

    def network(self) -> Network:
        return trace(self.setup)

    def acquire(self, instrument: Component | str, net: Network | None = None):
        inst = self.setup[instrument] if isinstance(instrument, str) else instrument
        return inst.acquire(net or trace(self.setup), self.rng)

    def single(self, instrument: Component | str, attempts: int = 100):
        """Single shot on an oscilloscope: wait for the first trigger and hold that
        sweep."""
        inst = self.setup[instrument] if isinstance(instrument, str) else instrument
        return inst.single(trace(self.setup), self.rng, attempts)

    def run(self, *instruments: Component | str) -> Results:
        """Acquire the given instruments (default: all of them) on one snapshot of the
        network."""
        net = trace(self.setup)
        chosen = [
            self.setup[i] if isinstance(i, str) else i for i in instruments
        ] or instruments_of(self.setup)
        return Results(
            {inst.name: inst.acquire(net, self.rng) for inst in chosen},
            report=report(self.setup, net),
        )

    def sweep(
        self,
        component: Component | str,
        param: str,
        values: Iterable,
        measure: Callable[[Results], float],
        *instruments: Component | str,
    ) -> Sweep:
        """Run once per value of ``component.param`` and collect ``measure(results)``.

        Example: ``runner.sweep(voa, "attenuation_db", range(0, 40, 5), lambda r:
        r["OPM-1"].power)``
        """
        comp = self.setup[component] if isinstance(component, str) else component
        original = getattr(comp, param)
        values = list(values)
        out = []
        try:
            for v in values:
                setattr(comp, param, v)
                out.append(measure(self.run(*instruments)))
        finally:
            setattr(comp, param, original)
        return Sweep(comp.name, param, np.asarray(values), np.asarray(out, dtype=float))

    def average_spectrum(
        self,
        analyzer: Component | str,
        component: Component | str,
        param: str,
        values: Iterable,
    ):
        """Acquire ``analyzer`` once per value of ``component.param`` and power-average
        the spectra (:meth:`~phoq.SpectrumResult.average`); the parameter is restored
        afterward.

        For a delayed self-homodyne, step a phase shifter in one arm over the bias
        phase: ``runner.average_spectrum(esa, ps, "phase", [0, math.pi / 2])``. The
        θ-dependent part of the beat varies as cos 2θ and sin 2θ, so two settings a
        quarter wave apart cancel it.
        """
        from .instruments import SpectrumResult

        comp = self.setup[component] if isinstance(component, str) else component
        original = getattr(comp, param)
        results = []
        try:
            for v in values:
                setattr(comp, param, v)
                results.append(self.acquire(analyzer))
        finally:
            setattr(comp, param, original)
        return SpectrumResult.average(results)
