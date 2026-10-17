"""phoq — a headless fiber-optics and quantum-communication lab.

Build an experiment from components, wire it, run it::

    from phoq import Setup, Laser, Fiber, Photodiode, Oscilloscope

    setup = Setup.from_chain(Laser(power_dbm=-7), Fiber(length=2), Photodiode(), Oscilloscope())
    print(setup.run().summary())

Every computation happens here in Python. ``phoq serve my_setup.py`` starts a FastAPI server
and the 3D virtual lab (``phoq/web``) as an optional, live visualizer of the same objects.
"""

from importlib.metadata import PackageNotFoundError, version as _version

from . import presets
from .core import Component, Param, Port, Setup, Transfer
from .detectors import Photodiode, PowerMeter, PowerReading, SinglePhotonDetector
from .engine import Network, report, synthesize, trace
from .generators import DCPowerSupply, FunctionGenerator
from .instruments import CounterResult, Oscilloscope, PhotonCounter, ScopeResult, Trace
from .modulators import IntensityModulator, OpticalSwitch, PhaseModulator
from .passive import (
    Attenuator,
    BeamSplitter,
    Coupler,
    Fiber,
    FiberSpool,
    Isolator,
    PhaseShifter,
)
from .runner import Results, Runner, Sweep
from .sources import Laser

try:
    __version__ = _version("phoq")
except PackageNotFoundError:  # running from a checkout that is not installed
    __version__ = "0.0.0"

__all__ = [
    "Attenuator",
    "BeamSplitter",
    "Component",
    "CounterResult",
    "Coupler",
    "DCPowerSupply",
    "Fiber",
    "FiberSpool",
    "FunctionGenerator",
    "IntensityModulator",
    "Isolator",
    "Laser",
    "Network",
    "OpticalSwitch",
    "Oscilloscope",
    "Param",
    "PhaseModulator",
    "PhaseShifter",
    "PhotonCounter",
    "Photodiode",
    "Port",
    "PowerMeter",
    "PowerReading",
    "Results",
    "Runner",
    "ScopeResult",
    "Setup",
    "SinglePhotonDetector",
    "Sweep",
    "Trace",
    "Transfer",
    "presets",
    "report",
    "synthesize",
    "trace",
    "__version__",
]
