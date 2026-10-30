"""Post-processing of Charlie's clicks, written as a separate script.

The framework streams every batch the time tagger records to the functions below, each
in its own process, with its own console in the virtual lab:

    uv run phoq serve examples/mpqkd --analysis examples/analysis_pairing.py
    uv run phoq run   examples/mpqkd --analysis examples/analysis_pairing.py --batches 5

or simply `uv run phoq run examples/analysis_pairing.py`. A batch is a `CounterResult`:
click records per channel, `timestamps(ch)` in absolute seconds, `events()` as a
time-ordered list, `rates`, `counts`, `coincidences(a, b)`. State kept in module globals
persists between batches.
"""

from pathlib import Path

import numpy as np

from phoq.analysis import task
from phoq.loading import import_source

# the showcase next to this script
mpqkd = import_source(Path(__file__).parent / "mpqkd")
pairing_rate_theory, simple_pairing = mpqkd.pairing_rate_theory, mpqkd.simple_pairing

totals = {"L": 0, "R": 0, "both": 0, "bins": 0}


@task("TAGGER")
def rates(batch):
    """Singles, coincidences and dark-time statistics, accumulated over the run."""
    left, right = batch.clicks["ch1"], batch.clicks["ch2"]
    totals["L"] += int(left.sum())
    totals["R"] += int(right.sum())
    totals["both"] += int(np.count_nonzero(left & right))
    totals["bins"] += batch.slots
    t_l = batch.timestamps("ch1")
    gaps = np.diff(t_l)
    print(
        f"batch {batch.seq:3d}  t = {batch.t0 * 1e3:8.3f} ms  L"
        f" {batch.counts['ch1']:5d}  R {batch.counts['ch2']:5d}  both"
        f" {batch.coincidences('ch1', 'ch2'):3d}  mean gap on L"
        f" {gaps.mean() * 1e6 if gaps.size else float('nan'):.2f} µs"
    )
    return {
        "L rate [cps]": batch.rates["ch1"],
        "R rate [cps]": batch.rates["ch2"],
        "p(single click)": (
            (totals["L"] + totals["R"] - 2 * totals["both"]) / totals["bins"]
        ),
        "p(double click)": totals["both"] / totals["bins"],
        "bins seen": totals["bins"],
    }


@task("TAGGER")
def pairing(batch):
    """Mode-pairing rate for several pairing intervals l, against Eq. (4) of the MP-QKD
    paper."""
    left, right = batch.clicks["ch1"], batch.clicks["ch2"]
    single = np.flatnonzero(left ^ right)  # exactly one detector clicked
    p = single.size / batch.slots
    out = {"p click": p}
    for l in (10, 100, 1000):
        fronts, rears = simple_pairing(single, l)
        r_sim, r_th = fronts.size / batch.slots, pairing_rate_theory(p, l)
        out[f"r_p l={l}"] = r_sim
        print(
            f"batch {batch.seq:3d}  l={l:5d}  pairs {fronts.size:6d}  r_p {r_sim:.3e} "
            f" Eq.(4) {r_th:.3e}  ratio {r_sim / r_th if r_th else float('nan'):.3f}"
        )
    return out


if __name__ == "__main__":
    from phoq.analysis import main

    main("examples/mpqkd", batches=5)
