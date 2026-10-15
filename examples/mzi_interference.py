"""Fiber Mach–Zehnder interferometer: sweep the phase shifter and record both output ports."""

import numpy as np

from phoq import BeamSplitter, Laser, PhaseShifter, PowerMeter, Runner, Setup

setup = Setup("Mach–Zehnder interferometer", seed=3)
laser = setup.add(Laser("LD-1", mode="cw", power_dbm=0.0, linewidth=100e3))
bs_in, bs_out = setup.add(BeamSplitter("BS-IN"), BeamSplitter("BS-OUT"))
shifter = setup.add(PhaseShifter("PS-1"))
meters = setup.add(PowerMeter("OPM-1"), PowerMeter("OPM-2"))

setup.connect(laser.out, bs_in.a1)
setup.connect(bs_in.b1, shifter.p1)
setup.connect(shifter.p2, bs_out.a1)
setup.connect(bs_in.b2, bs_out.a2)
setup.connect(bs_out.b1, meters[0].inp)
setup.connect(bs_out.b2, meters[1].inp)

if __name__ == "__main__":
    runner = Runner(setup)
    phases = np.linspace(0, 4 * np.pi, 33)
    p1 = runner.sweep(shifter, "phase", phases, lambda r: r["OPM-1"].power)
    p2 = runner.sweep(shifter, "phase", phases, lambda r: r["OPM-2"].power)
    visibility = (p1.measured.max() - p1.measured.min()) / (
        p1.measured.max() + p1.measured.min()
    )
    print(
        f"fringe visibility {visibility:.3f}; total power {(p1.measured + p2.measured).mean() * 1e3:.3f} mW (constant)"
    )

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ax = p1.plot(label="OPM-1")
    p2.plot(ax, label="OPM-2")
    ax.set(xlabel="phase shift [rad]", ylabel="power [W]", title="MZI fringes")
    ax.legend()
    plt.tight_layout()
    plt.savefig("mzi_fringes.png", dpi=140)
    print("saved mzi_fringes.png")
