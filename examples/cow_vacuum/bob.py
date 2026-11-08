"""Bob: the receiver. His tap sends t_B of the light to the data detector D_T, whose
time of arrival is the bit, and the rest through a one-slot delay-line interferometer to
D_M0 and D_M1. He folds his time tagger's stamps onto Alice's slots, reduces each pair
to one event, announces the pairs with a data-line event, the monitoring events with
their detector and the clicks at the boundaries between pairs ([#1]), and answers
Alice's sifting with his bits at her sample ([#3]). Bob needs no basis choice and no
modulator: his tap is the basis choice.
"""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    COWParams,
    Tally,
    Topic,
    account,
    announcements,
    boundary_clicks,
    describe,
    events,
    read_tagger,
    readouts,
)


class Bob(Party):
    name = "BOB"
    port = 5572
    instruments = (
        "BOB-TAP",
        "BOB-PS",
        "BOB-ODL",
        "BOB-VOA",
        "DT",
        "DM0",
        "DM1",
        "TAGGER",
    )
    params = COWParams()

    def setup(self) -> None:
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        print(
            f"receiver up · data line t_B={self.params.t_b}: second slot → 0, first → 1"
            f" · monitoring DLI one slot ({self.params.slot * 1e9:.2f} ns) unbalanced,"
            " read in each pair's second slot"
            + (" · click window half a slot" if self.params.click_window else "")
        )

    # ---- physical layer --------------------------------------------------------------
    def measure(self, batch) -> None:
        clicked = read_tagger(batch, batch.seq, self.params)
        event, multi = events(clicked, self.rng)
        idx, bits, mon, det = announcements(event)
        edge0, edge1 = boundary_clicks(clicked, event)
        self.blocks[batch.seq] = dict(idx=idx, bits=bits, pairs=event.size, multi=multi)
        size = self.net.send(
            "ALICE",
            Topic.DETECT,
            block=batch.seq,
            idx=idx.astype(np.int32),
            mon=mon.astype(np.int32),
            det=det,
            edge0=edge0.astype(np.int32),
            edge1=edge1.astype(np.int32),
            multi=multi,
        )
        print(
            f"block {batch.seq}: {idx.size:,} data-line events · D_M0"
            f" {np.count_nonzero(det == 0):,} D_M1 {np.count_nonzero(det == 1):,} ·"
            f" {multi} with several clicks → announced in {size / 1e3:.1f} kB"
        )

    # ---- classical channel -----------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if msg.sender != "ALICE" or msg.topic != Topic.SIFT or block not in self.blocks:
            return
        st = self.blocks.pop(block)
        decoy = np.unpackbits(msg.decoy, count=st["idx"].size).astype(bool)
        sample = np.asarray(msg.sample, dtype=np.int64)
        mine = st["bits"][sample]
        self.net.send("ALICE", Topic.ERRORS, block=block, sample_bits=mine)
        t = account(
            st["pairs"],
            st["idx"].size,
            decoy,
            st["multi"],
            sample,
            mine,
            np.asarray(msg.sample_bits),
            msg.monitor,
            msg.boundary,
        )
        key = ~decoy
        key[sample] = False
        self.key.append(st["bits"][key])
        self.tally += t
        print(
            f"block {block}: {t.decoys:,} decoys removed, {t.sampled} bits compared,"
            f" {t.errors} wrong → key +{t.key_bits:,} = {self.tally.key_bits:,} bits ·"
            f" {describe(self.tally, self.params)}"
        )
        self.report(**readouts(self.tally, self.params, self.net.stats))
