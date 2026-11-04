"""Alice: the transmitter. She draws one of the four states for every pair, programs the
intensity modulator's AWG, and leads the sifting once Bob has announced his events.

Per block, on the classical channel:

    [#1] Bob's ``detect`` arrives: the pairs with a data-line event (not in which slot)
         and the pairs with a monitoring event and their detector
    → [#2] she flags the data-line events that were decoys, discloses a random sample of
           her bits at the others for E_b, and counts the monitoring events per state
           she sent: the gains of Eq. (7) (``sift``)
    → [#3] Bob's ``errors`` returns his bits at the sample → both tally the block.

Error correction and privacy amplification are not implemented: the result is the sifted
key, its E_b and the bound E_p^u on its phase error rate.
"""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    BINS_PER_PAIR,
    BIT0,
    BIT1,
    DECOY1,
    DECOY2,
    COWParams,
    Tally,
    Topic,
    account,
    carving,
    describe,
    draw,
    monitor_table,
    occupied,
    readouts,
)


class Alice(Party):
    name = "ALICE"
    port = 5571
    instruments = ("ALICE-LD", "ALICE-IM", "ALICE-AWG", "ALICE-ATT", "ALICE-VOA")
    params = COWParams()  # public parameters, agreed in advance

    def setup(self) -> None:
        p = self.params
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        print(
            f"transmitter up · CW laser carved at {p.rate / 1e6:.0f} MHz · μ={p.mu} ·"
            f" decoys |α,α⟩ {p.decoy1}, |0,0⟩ {p.decoy2} · {p.distance / 1e3:.0f} km to"
            " BOB"
        )

    # ---- physical layer --------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p = self.params
        # the lab counts the tagger's bins
        kind = draw(self.rng, n // BINS_PER_PAIR, p)
        volts = self.hw.call("ALICE-IM", "voltage_for", carving(occupied(kind)))
        self.hw.call("ALICE-AWG", "set_pattern", "ch1", volts, 2 * p.rate)
        self.blocks[block] = dict(kind=kind)
        counts = np.bincount(kind, minlength=4)
        print(
            f"block {block}: programmed {kind.size:,} pairs · 0: {counts[BIT0]:,} 1:"
            f" {counts[BIT1]:,} |α,α⟩: {counts[DECOY1]:,} |0,0⟩: {counts[DECOY2]:,}"
            " (kept private)"
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
        decoy = sent >= DECOY1
        kept = np.flatnonzero(~decoy)  # positions among Bob's data-line events
        sample = kept[self.rng.random(kept.size) < self.params.sample_fraction]
        monitor = monitor_table(kind, msg.mon, msg.det)
        # her bit at every event (meaningless at decoys)
        bits = sent.astype(np.int8)
        st.update(
            idx=idx,
            decoy=decoy,
            sample=sample,
            monitor=monitor,
            multi=int(msg.multi),
            bits=bits,
        )
        self.net.send(
            "BOB",
            Topic.SIFT,
            block=block,
            decoy=np.packbits(decoy),
            sample=sample.astype(np.int32),
            sample_bits=bits[sample],
            monitor=monitor,
        )

    def _finish(self, block: int, st: dict, msg: Message) -> None:
        t = account(
            st["kind"].size,
            st["idx"].size,
            st["decoy"],
            st["multi"],
            st["sample"],
            st["bits"][st["sample"]],
            np.asarray(msg.sample_bits),
            st["monitor"],
        )
        key = ~st["decoy"]
        key[st["sample"]] = False
        self.key.append(st["bits"][key])
        self.tally += t
        print(
            f"block {block}: {t.detected:,} data-line events, {t.decoys:,} decoys"
            f" removed → key +{t.key_bits:,} = {self.tally.key_bits:,} bits ·"
            f" {describe(self.tally, self.params)}"
        )
        self.report(**readouts(self.tally, self.params, self.net.stats))
        del self.blocks[block]
