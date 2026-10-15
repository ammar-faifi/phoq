"""Laser → patch cord → photodiode → oscilloscope.

uv run python examples/basic_link.py                     # headless: print measurements
uv run phoq serve examples/basic_link.py  # live 3D visualizer
"""

from phoq import Fiber, Laser, Oscilloscope, Photodiode, Setup

laser = Laser(
    "LD-1",
    wavelength=1550e-9,
    power_dbm=-7.0,
    mode="pulsed",
    rep_rate=10e6,
    pulse_width=5e-9,
)
pd = Photodiode("PD-1", material="InGaAs", bandwidth=1e9)
scope = Oscilloscope("OSC-1", timebase=20e-9, ch1_scale=500e-6)

# Wire it like a pipeline: each item is connected to the next one.
setup = Setup.from_chain(
    laser, Fiber(length=1.0), pd, scope, name="Basic fiber link", seed=1
)

if __name__ == "__main__":
    print(setup.describe(), "\n")
    print(setup.run().summary())

    # Change a parameter on the instance and run again.
    laser.power_dbm = 0.0
    print("\nAt 0 dBm:")
    print(setup.run().summary())
