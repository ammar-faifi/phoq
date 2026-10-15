"""A coupler splits a pulse train; one arm runs through a 1 km spool (4.9 µs late on CH2)."""

import numpy as np

from phoq import BeamSplitter, FiberSpool, Laser, Oscilloscope, Photodiode, Setup


def build() -> Setup:
    setup = Setup("Splitter + delay line", seed=2)
    laser = setup.add(Laser("LD-1", power_dbm=-20.0, rep_rate=100e3, pulse_width=20e-9))
    bs = setup.add(BeamSplitter("BS-1"))
    spool = setup.add(FiberSpool("SPL-1", length=1e3))
    pd1, pd2 = setup.add(Photodiode("PD-1"), Photodiode("PD-2"))
    scope = setup.add(
        Oscilloscope("OSC-1", timebase=2e-6, ch1_scale=500e-6, ch2_scale=500e-6)
    )

    setup.connect(laser.out, bs.a1)  # bulkhead ↔ bulkhead: a 1 m cord is inserted
    setup.connect(bs.b1, pd1.inp)
    setup.connect(bs.b2, spool.p1)
    setup.connect(spool.p2, pd2.inp)
    setup.connect(pd1.rf, scope.ch1)
    setup.connect(pd2.rf, scope.ch2)
    return setup


setup = build()

if __name__ == "__main__":
    results = setup.run()
    print(results.summary())
    trace = results["OSC-1"]
    rise = lambda ch: trace.t[
        np.flatnonzero(trace[ch].data > 0.5 * trace[ch].data.max())[0]
    ]  # first rising edge
    print(
        f"\npulse on CH2 arrives {(rise('ch2') - rise('ch1')) * 1e6:.2f} µs after CH1 (spool delay {setup['SPL-1'].delay * 1e6:.2f} µs)"
    )
