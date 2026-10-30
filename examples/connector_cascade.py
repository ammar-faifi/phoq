"""Patch cords cascaded through FC mating sleeves: what every connection costs.

    # compare polishes and cord counts
    uv run python examples/connector_cascade.py
    # the bench in 3D; change cords, unplug ends
    uv run phoq serve examples/connector_cascade.py

A laser feeds patch cords joined end to end by FC–FC mating sleeves, then a photodiode.
Every sleeve is one more mated pair: 0.3 dB less photocurrent, and one more reflection
traveling back to the laser (−50 dB for FC/UPC, −60 dB for FC/APC). A single FC/APC
cord among FC/UPC ones turns both sleeves it sits in into air gaps; an unplugged FC/UPC
end sends 3.6 % straight back.
"""

from phoq import Fiber, Laser, MatingSleeve, Photodiode, Setup
from phoq.engine import report
from phoq.units import fmt_si, w_to_dbm

U, A = "FC/UPC", "FC/APC"


def build(
    cords=(U, U, U, U), *, laser=U, detector=U, plugged=True, name="Connector cascade"
) -> Setup:
    """A laser, ``cords`` patch cords joined by mating sleeves, then a photodiode.

    Each entry of ``cords`` is one cord: a connector type for both ends, or an
    (end A, end B) pair for a hybrid cord. ``plugged=False`` leaves the last cord's end
    in the air.
    """
    setup = Setup(name, seed=1)
    ld = setup.add(Laser("LD-1", mode="cw", power_dbm=0.0, connector=laser))
    pd = setup.add(Photodiode("PD-1", connector=detector))
    prev = ld.out
    for i, kind in enumerate(cords, 1):
        a, b = (kind, kind) if isinstance(kind, str) else kind
        cord = setup.add(Fiber(f"FIB-{i}", connector_a=a, connector_b=b))
        if i > 1:  # join this cord to the previous one through a sleeve
            sleeve = setup.add(MatingSleeve(f"SLV-{i - 1}"))
            setup.connect(prev, sleeve.p1)
            prev = sleeve.p2
        setup.connect(prev, cord.a)
        prev = cord.b
    if plugged:
        setup.connect(prev, pd.inp)
    return setup


CASES = [
    ("1 cord", dict(cords=(U,))),
    ("4 cords, 3 sleeves", dict()),
    ("8 cords, 7 sleeves", dict(cords=(U,) * 8)),
    ("4 FC/APC cords, APC laser and PD", dict(cords=(A,) * 4, laser=A, detector=A)),
    ("4 cords, one FC/APC among FC/UPC", dict(cords=(U, U, A, U))),
    ("APC laser → UPC PD, last cord hybrid", dict(cords=(A, A, (A, U)), laser=A)),
    ("4 cords, last end unplugged", dict(plugged=False)),
]

if __name__ == "__main__":
    print(
        f"{'':38} {'matings':>7} {'at the PD':>11} {'current':>9} {'back to LD':>10} "
        " strongest reflection"
    )
    for title, kw in CASES:
        setup = build(**kw)
        r = report(setup)
        pd, src = r["detectors"]["PD-1"], r["sources"]["LD-1"]
        # laser–cord, sleeves, cord–PD
        matings = len(kw.get("cords", (U,) * 4)) + kw.get("plugged", True)
        power = f"{w_to_dbm(pd['power']):.2f} dBm" if pd["power"] > 0 else "—"
        back = src["readouts"]["Back-reflection"].split(" · ")[0]
        strongest = src["readouts"].get("Strongest reflection", "")
        print(
            f"{title:38} {matings:7d} {power:>11} {fmt_si(pd['current'], 'A'):>9}"
            f" {back:>10}  {strongest}"
        )
        for w in r["warnings"]:
            print(f"{'':40}! {w['text'].split(': ')[0]}")
