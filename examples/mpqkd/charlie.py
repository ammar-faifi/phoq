"""Charlie: the untrusted relay in the middle. He owns the beam splitter, the detectors
and the time tagger, folds the tagger's stamps onto the rounds, and announces every
round's outcome publicly ([CLASSICAL #1])."""

from __future__ import annotations

import numpy as np

from phoq.parties import Party
from .common import DET_L, DET_R, MPQKDParams, Topic, read_tagger


class Charlie(Party):
    name = "CHARLIE"
    port = 5553
    instruments = ("TAGGER", "DET-L", "DET-R", "CHARLIE")
    params = MPQKDParams()  # public parameters, agreed in advance

    def setup(self) -> None:
        self.totals = dict(blocks=0, rounds=0, single=0, double=0)
        print("relay up · announcing every round's click outcome to ALICE and BOB")

    def measure(self, batch) -> None:
        clicked = read_tagger(batch, batch.seq, self.params)
        left, right = clicked["ch1"], clicked["ch2"]
        single = np.flatnonzero(left ^ right)  # exactly one detector clicked
        det = np.where(left[single], DET_L, DET_R).astype(np.int8)  # … and which one
        double = int(np.count_nonzero(left & right))
        rounds = left.size
        size = self.net.broadcast(
            Topic.CLICKS,
            block=batch.seq,
            idx=single.astype(np.int32),
            det=det,
            n=rounds,
        )
        t = self.totals
        t["blocks"] += 1
        t["rounds"] += rounds
        t["single"] += single.size
        t["double"] += double
        print(
            f"block {batch.seq}: {single.size:,} single clicks (p"
            f" {single.size / rounds:.3e}), {double} double clicks discarded →"
            f" announced in {size / 1e3:.1f} kB"
        )
        self.report(
            **{
                "p(single click)": t["single"] / t["rounds"],
                "p(double click)": t["double"] / t["rounds"],
                "L rate [cps]": batch.timestamps("ch1").size / batch.duration,
                "R rate [cps]": batch.timestamps("ch2").size / batch.duration,
                "announced [kB]": self.net.stats["bytes_out"] / 1e3,
            }
        )
