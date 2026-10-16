"""Bob: the receiver. He programs his measurement basis pulse by pulse, reads his time tagger,
announces which pulses clicked ([#1]), and answers Alice's sifting with his bits at the disclosed
positions ([#3]). Only the middle time slot of every pulse carries information; a double click
there gets a random bit."""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    B_X,
    BINS,
    BB84Params,
    Tally,
    Topic,
    account,
    describe,
    estimate,
    key_from,
    measurement_phase,
    middle_slot,
    readouts,
)


class Bob(Party):
    name = "BOB"
    port = 5552
    instruments = ("BOB-PM", "BOB-PS", "BOB-AWG", "DET-0", "DET-1", "TAGGER")
    params = BB84Params()

    def setup(self) -> None:
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        print(
            f"receiver up · basis per pulse with p_Z={self.params.p_z} · "
            f"DET-0 ↔ bit 0, DET-1 ↔ bit 1 in the middle time slot"
        )

    # ---- physical layer -----------------------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        m = n // BINS
        basis = (self.rng.random(m) >= self.params.p_z).astype(np.int8)
        v = self.hw.call("BOB-PM", "voltage_for", measurement_phase(basis))
        self.hw.call("BOB-AWG", "set_pattern", "ch1", v, self.params.rep_rate)
        self.blocks[block] = dict(basis=basis)
        print(
            f"block {block}: measuring {m:,} pulses, {int(np.count_nonzero(basis == B_X)):,} in X"
        )

    def measure(self, batch) -> None:
        st = self.blocks.get(batch.seq)
        if st is None:
            return
        c0, c1 = middle_slot(batch.clicks["ch1"]), middle_slot(batch.clicks["ch2"])
        idx = np.flatnonzero(c0 | c1)
        both = c0[idx] & c1[idx]
        bits = np.where(c1[idx] & ~c0[idx], 1, 0).astype(np.int8)
        bits[both] = self.rng.integers(0, 2, size=int(both.sum()), dtype=np.int8)
        st.update(idx=idx, bits=bits, n=int(c0.size), doubles=int(both.sum()))
        satellites = int(batch.counts["ch1"] + batch.counts["ch2"]) - int(
            c0.sum() + c1.sum()
        )
        size = self.net.send(
            "ALICE",
            Topic.DETECT,
            block=batch.seq,
            idx=idx.astype(np.int32),
            basis=np.packbits(st["basis"].astype(bool)),
            n=int(c0.size),
            doubles=int(both.sum()),
        )
        print(
            f"block {batch.seq}: {idx.size:,} middle-slot detections (p {idx.size / c0.size:.3e}), "
            f"{int(both.sum())} double, {satellites:,} satellite clicks ignored → announced in {size / 1e3:.1f} kB"
        )

    # ---- classical channel --------------------------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if msg.sender != "ALICE" or msg.topic != Topic.SIFT or block not in self.blocks:
            return
        st = self.blocks[block]
        if "idx" not in st:
            return
        keep, label = np.asarray(msg.keep, dtype=bool), np.asarray(
            msg.label, dtype=np.int8
        )
        basis_d = st["basis"][st["idx"]]
        x = keep & (basis_d == B_X)
        zmu = keep & (basis_d == 0) & (label == 0)
        sample = np.asarray(msg.sample, dtype=np.int64)
        sift = dict(
            sent=np.asarray(msg.sent),
            keep=keep,
            label=label,
            basis=basis_d,
            x=x,
            zmu=zmu,
            sample=sample,
            n=st["n"],
            doubles=st["doubles"],
        )
        mine = dict(xbits=st["bits"][x], sample_bits=st["bits"][sample])
        theirs = dict(
            xbits=np.asarray(msg.xbits), sample_bits=np.asarray(msg.sample_bits)
        )
        self.net.send(
            "ALICE",
            Topic.ERRORS,
            block=block,
            xbits=mine["xbits"],
            sample_bits=mine["sample_bits"],
        )
        t = account(sift, mine, theirs)
        self.key.append(key_from(sift, st["bits"]))
        self.tally += t
        est = estimate(self.tally, self.params)
        print(
            f"block {block}: {int(keep.sum()):,} of {keep.size:,} detections sifted · "
            f"key +{t.key_bits:,} = {self.tally.key_bits:,} bits · {describe(est)}"
        )
        self.report(**readouts(self.tally, est, self.net.stats))
        del self.blocks[block]
