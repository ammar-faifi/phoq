"""Alice: the transmitter. She draws bit 0, bit 1 or a decoy for every logical bit,
programs the intensity modulator's AWG, and leads the sifting once Bob has announced his
detections.

Per block, on the classical channel (the paper's steps 2–4):

    [#1] Bob's ``detect`` arrives: which bits gave a data-line click (not in which slot)
         and the slots where his monitoring detectors fired
    → [#2] she flags the detections that were decoys, discloses a random sample of her
           bits at the others for the QBER, and counts DM1 and DM2 clicks where two
           neighboring pulses were both sent: the visibilities V_d (inside decoys) and
           V_1−0 (across 1-0 boundaries) (``sift``)
    → [#3] Bob's ``errors`` returns his bits at the sample → both tally the block.

Error correction and privacy amplification are not implemented: the result is the sifted
key.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from phoq.parties import Message, Party
from .common import (
    DECOY,
    COWParams,
    Tally,
    WINDOW,
    Topic,
    account,
    carving,
    describe,
    draw,
    monitor_counts,
    occupied,
    readouts,
    window,
)


class Alice(Party):
    name = "ALICE"
    port = 5561
    instruments = ("ALICE-LD", "ALICE-IM", "ALICE-AWG", "ALICE-VOA")
    params = COWParams()  # public parameters, agreed in advance

    def setup(self) -> None:
        p = self.params
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        self.recent: deque[Tally] = deque(maxlen=WINDOW)  # the running ("now") figures
        print(
            f"transmitter up · {p.source} laser carved at {p.rate / 1e6:.0f} MHz ·"
            f" μ={p.mu} · decoys f={p.decoy} · {p.distance / 1e3:.0f} km to BOB"
        )

    # ---- physical layer --------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p = self.params
        kind = draw(
            self.rng, n // 2, p
        )  # the lab counts time slots: two per logical bit
        volts = self.hw.call("ALICE-IM", "voltage_for", carving(occupied(kind)))
        self.hw.call("ALICE-AWG", "set_pattern", "ch1", volts, 2 * p.rate)
        self.blocks[block] = dict(kind=kind)
        counts = np.bincount(kind, minlength=3)
        print(
            f"block {block}: programmed {kind.size:,} bits · 0: {counts[0]:,} 1:"
            f" {counts[1]:,} decoy: {counts[2]:,} (kept private)"
        )

    # ---- classical channel -----------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if msg.sender != "BOB" or block is None or block not in self.blocks:
            return
        st = self.blocks[block]
        if msg.topic == Topic.DETECT:
            self._sift(block, st, msg)
        elif msg.topic == Topic.ERRORS and "sample" in st:
            self._finish(block, st, msg)

    def _sift(self, block: int, st: dict, msg: Message) -> None:
        kind = st["kind"]
        idx = np.asarray(msg.idx, dtype=np.int64)
        sent = kind[idx]
        decoy = sent == DECOY
        kept = np.flatnonzero(~decoy)  # positions among Bob's detections
        sample = kept[self.rng.random(kept.size) < self.params.sample_fraction]
        monitor = monitor_counts(kind, msg.dm1, msg.dm2)
        st.update(
            idx=idx,
            decoy=decoy,
            kept=kept,
            sample=sample,
            monitor=monitor,
            doubles=int(msg.doubles),
        )
        st["bits"] = sent.astype(
            np.int8
        )  # her bit at every detection (meaningless at decoys)
        self.net.send(
            "BOB",
            Topic.SIFT,
            block=block,
            decoy=np.packbits(decoy),
            sample=sample.astype(np.int32),
            sample_bits=st["bits"][sample],
            monitor=monitor,
        )

    def _finish(self, block: int, st: dict, msg: Message) -> None:
        mine = st["bits"][st["sample"]]
        t = account(
            st["kind"].size,
            st["idx"].size,
            st["decoy"],
            st["doubles"],
            st["sample"],
            mine,
            np.asarray(msg.sample_bits),
            st["monitor"],
        )
        key = np.ones(st["idx"].size, dtype=bool)
        key[st["decoy"]] = False
        key[st["sample"]] = False
        self.key.append(st["bits"][key])
        self.tally += t
        self.recent.append(t)
        print(
            f"block {block}: {t.detected:,} detections, {t.decoys:,} decoys removed →"
            f" key +{t.key_bits:,} = {self.tally.key_bits:,} bits ·"
            f" {describe(self.tally)}"
        )
        self.report(
            **readouts(self.tally, self.params, self.net.stats, window(self.recent))
        )
        del self.blocks[block]
