"""Bob: the receiver. His tap sends t_B of the light to the data detector DB, whose time of
arrival is the bit, and the rest through a one-slot delay-line interferometer to DM1 and DM2. He
reads his time tagger, announces which bits clicked and when his monitoring detectors fired
([#1]), and answers Alice's sifting with his bits at her sample ([#3]). Bob needs no basis choice
and no modulator: the data line is a plain time-of-arrival measurement.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from phoq.parties import Message, Party
from .common import DB, DM1, DM2, WINDOW, COWParams, Tally, Topic, account, decode, describe, readouts, window


class Bob(Party):
    name = "BOB"
    port = 5562
    instruments = ("BOB-TAP", "BOB-PS", "BOB-ODL", "BOB-VOA", "DB", "DM1", "DM2", "TAGGER")
    params = COWParams()

    def setup(self) -> None:
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        self.recent: deque[Tally] = deque(maxlen=WINDOW)  # the running ("now") figures
        print(f"receiver up · data line t_B={self.params.t_b}: first slot → 0, second → 1 · "
              f"monitoring DLI one slot ({self.params.slot * 1e9:.2f} ns) unbalanced")

    # ---- physical layer -----------------------------------------------------------------------------
    def measure(self, batch) -> None:
        idx, bits, doubles = decode(batch.clicks[DB], self.rng)
        dm1, dm2 = (np.flatnonzero(batch.clicks[ch]).astype(np.int32) for ch in (DM1, DM2))
        self.blocks[batch.seq] = dict(idx=idx, bits=bits, n=batch.slots // 2, doubles=doubles)
        size = self.net.send("ALICE", Topic.DETECT, block=batch.seq, idx=idx.astype(np.int32), dm1=dm1,
                             dm2=dm2, doubles=doubles)
        print(f"block {batch.seq}: {idx.size:,} data-line detections ({doubles} double) · "
              f"DM1 {dm1.size:,} DM2 {dm2.size:,} clicks → announced in {size / 1e3:.1f} kB")

    # ---- classical channel --------------------------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if msg.sender != "ALICE" or msg.topic != Topic.SIFT or block not in self.blocks:
            return
        st = self.blocks.pop(block)
        decoy = np.unpackbits(msg.decoy, count=st["idx"].size).astype(bool)
        sample = np.asarray(msg.sample, dtype=np.int64)
        mine = st["bits"][sample]
        self.net.send("ALICE", Topic.ERRORS, block=block, sample_bits=mine)
        t = account(st["n"], st["idx"].size, decoy, st["doubles"], sample, mine,
                    np.asarray(msg.sample_bits), msg.monitor)
        key = ~decoy
        key[sample] = False
        self.key.append(st["bits"][key])
        self.tally += t
        self.recent.append(t)
        print(f"block {block}: {t.decoys:,} decoys removed, {t.sampled} bits compared, {t.errors} wrong → "
              f"key +{t.key_bits:,} = {self.tally.key_bits:,} bits · {describe(self.tally)}")
        self.report(**readouts(self.tally, self.params, self.net.stats, window(self.recent)))
