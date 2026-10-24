"""Charlie: the untrusted relay in the middle. He owns the beam splitter, the detectors
and the time tagger, and announces every round's outcome publicly ([CLASSICAL #1])."""

from __future__ import annotations

import numpy as np

from phoq.parties import Party
from .common import DET_L, DET_R, Topic


class Charlie(Party):
    name = "CHARLIE"
    port = 5553
    instruments = ("TAGGER", "DET-L", "DET-R", "CHARLIE")

    def setup(self) -> None:
        self.totals = dict(blocks=0, rounds=0, single=0, double=0)
        print("relay up · announcing every round's click outcome to ALICE and BOB")

    def measure(self, batch) -> None:
        left, right = batch.clicks["ch1"], batch.clicks["ch2"]
        single = np.flatnonzero(left ^ right)  # exactly one detector clicked
        det = np.where(left[single], DET_L, DET_R).astype(np.int8)  # … and which one
        double = int(np.count_nonzero(left & right))
        size = self.net.broadcast(
            Topic.CLICKS,
            block=batch.seq,
            idx=single.astype(np.int32),
            det=det,
            n=batch.slots,
        )
        t = self.totals
        t["blocks"] += 1
        t["rounds"] += batch.slots
        t["single"] += single.size
        t["double"] += double
        print(
            f"block {batch.seq}: {single.size:,} single clicks (p"
            f" {single.size / batch.slots:.3e}), {double} double clicks discarded →"
            f" announced in {size / 1e3:.1f} kB"
        )
        self.report(
            **{
                "p(single click)": t["single"] / t["rounds"],
                "p(double click)": t["double"] / t["rounds"],
                "L rate [cps]": batch.rates["ch1"],
                "R rate [cps]": batch.rates["ch2"],
                "announced [kB]": self.net.stats["bytes_out"] / 1e3,
            }
        )
