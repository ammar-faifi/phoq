"""A CW laser and an intensity modulator biased at quadrature by a DC supply and driven by a
function generator, with a scope watching the photocurrent next to the drive.

    uv run python examples/modulated_link.py                       # headless
    uv run phoq serve examples/modulated_link.py    # live 3D visualizer

The modulator has no settings of its own beyond Vπ: the supply's CH1 sits on its ``bias`` input
and the generator's CH1 on its ``rf`` input. CH2 of the generator goes straight to the
oscilloscope so the drive can be seen next to the photocurrent.
"""

import numpy as np

from phoq import (
    DCPowerSupply,
    FunctionGenerator,
    IntensityModulator,
    Laser,
    Oscilloscope,
    Photodiode,
    Setup,
)

setup = Setup("Modulated link", seed=4)
laser = setup.add(Laser("LD-1", mode="cw", power_dbm=-6.0))
im = setup.add(IntensityModulator("IM-1"))
pd = setup.add(Photodiode("PD-1", bandwidth=5e9))
scope = setup.add(
    Oscilloscope("OSC-1", timebase=100e-9, ch1_scale=20e-6, ch2_scale=0.5)
)
fg = setup.add(FunctionGenerator("FG-1"))
psu = setup.add(
    DCPowerSupply("PSU-1", ch1_voltage=im.v_pi / 2)
)  # quadrature: the linear point

setup.chain(laser, im, pd, scope)  # optical path, then PD → OSC ch1
setup.connect(psu.ch1, im.bias)  # DC bias
setup.connect(fg.ch1, im.rf)  # small-signal drive
setup.connect(fg.ch2, scope.ch2)  # monitor a copy of the drive

# A 1 Vpp sine about quadrature modulates the power almost linearly.
fg.set(ch1_shape="sine", ch1_freq=10e6, ch1_vpp=1.0)
fg.set(ch2_shape="sine", ch2_freq=10e6, ch2_vpp=1.0)

if __name__ == "__main__":
    print(setup.run().summary())
    print("IM:", im.info())

    # Move the bias to the null and drive 0 → Vπ with a 30 % duty square wave: a pulse carver.
    psu.ch1_voltage = im.v_pi
    fg.set(
        ch1_shape="square",
        ch1_freq=5e6,
        ch1_vpp=im.v_pi,
        ch1_offset=-im.v_pi / 2,
        ch1_duty=0.3,
    )
    print("\nas a pulse carver (bias at the null):")
    print(setup.run().summary())

    # Or load an 8-level staircase of transmissions, one symbol per 100 ns.
    psu.ch1_voltage = 0.0
    fg.set_pattern("ch1", im.voltage_for(np.linspace(1.0, 0.0, 8)), rate=10e6)
    scope.set(timebase=200e-9)
    print("\nwith a pattern:")
    print(setup.run().summary())
