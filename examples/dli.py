"""The delay-line interferometer (DLI) shared by examples/dli_for_dpsk.py, dli_for_cow.py and
odl_validation.py: a helper module, not a bench of its own.

A DLI is an unbalanced Mach–Zehnder whose long arm is exactly one pulse period T longer. Each
output time slot then holds pulse k (short arm) on top of pulse k−1 (long arm), so the outputs
compare *neighbouring* pulses: with Δφ = φ_k − φ_{k−1} the constructive port gets
½[1 + V cos(Δφ + θ)] of the light and the destructive port the rest. V is the coherence between
neighbouring pulses, e^{−πΔν·T} for a laser of linewidth Δν, and θ the DLI's own phase, set with
the phase shifter in the short arm.

Both arms are standard patch cords: 0.5 m + 0.5 m around the phase shifter, 1 m + 2 m around an
optical delay line in the long arm. The cords give 2 m (9.795 ns); the delay line, set once by
`balance`, adds the last 205 ps so the arms differ by exactly T (see examples/odl_validation.py).
"""

import math

import numpy as np

from phoq import Coupler, OpticalDelayLine, PhaseShifter, Setup
from phoq.engine import trace
from phoq.units import fmt_si

RATE = 100e6  # pulse rate: T = 10 ns between neighbouring pulses, what the cords are chosen for


def dli(setup: Setup, tag: str):
    """BS → short arm (0.5 m, phase shifter, 0.5 m) and long arm (1 m, delay line, 2 m) → BS.
    Returns (in, out, phase shifter, delay line); `balance` then sets the delay line."""
    bs_in, bs_out = setup.add(Coupler(f"{tag}-BS1"), Coupler(f"{tag}-BS2"))
    ps, odl = setup.add(PhaseShifter(f"{tag}-PS"), OpticalDelayLine(f"{tag}-ODL"))
    setup.connect(bs_in.b1, ps.p1, length=0.5)
    setup.connect(ps.p2, bs_out.a1, length=0.5)
    setup.connect(bs_in.b2, odl.p1, length=1.0)
    setup.connect(odl.p2, bs_out.a2, length=2.0)
    return bs_in, bs_out, ps, odl


def arms(setup: Setup, laser: str, detector: str) -> tuple:
    """The short- and long-arm paths of ``laser``'s light to ``detector`` (any reflected paths are
    far weaker and later)."""
    paths = sorted((p for p in trace(setup).paths[setup[detector]] if p.source is setup[laser]),
                   key=lambda p: p.delay)
    return paths[0], paths[1]


def balance(setup: Setup, laser: str, detector: str, odl: str, rate: float = RATE) -> float:
    """Set the delay line so the long arm is one slot 1/rate longer than the short one, as you
    would with the dial; fails when the cords leave more (or less) than the line can reach."""
    line = setup[odl]
    short, long_ = arms(setup, laser, detector)
    want = line.setting + 1 / rate - (long_.delay - short.delay)
    if not 0.0 <= want <= line.max_delay:
        raise ValueError(f"{odl} would need {fmt_si(want, 's')}, outside its 0–{fmt_si(line.max_delay, 's')} "
                         "travel: change the cords in the arms")
    line.delay = want
    return want


def lock(setup: Setup, laser: str, detector: str, shifter: str) -> float:
    """Set the phase shifter so neighbouring pulses with equal phases interfere constructively at
    ``detector`` (a real DLI is temperature-tuned to this point). Modulators count at 0 V."""
    ps = setup[shifter]
    ps.phase = 0.0

    def static(p):
        a = complex(p.amp)
        for mod, _ in p.mods:
            a *= complex(np.asarray(mod.response([mod.static_voltage()]))[0])
        return a

    short, long_ = arms(setup, laser, detector)
    ps.phase = float((np.angle(static(long_)) - np.angle(static(short))) % (2 * math.pi))
    return ps.phase


def slot_delay(setup: Setup, laser: str, detector: str) -> float:
    """When pulse k emitted at k·T reaches ``detector`` through the short arm, minus k·T."""
    return min(trace(setup).delays(setup[detector], setup[laser]))
