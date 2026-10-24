"""Alice: the transmitter. She draws intensity, basis and bit for every pulse, programs
her AWG, and leads the sifting once Bob has announced his detections.

Per block, on the classical channel:

    [#1] Bob's ``detect`` arrives (which pulses clicked, his basis for every pulse)
    → [#2] she keeps the detections whose bases match, counts what she sent per
           (intensity, basis) against Bob's bases, discloses the intensity label of
           every detection, all her X-basis bits and a random sample of her Z-μ bits
           (``sift``)
    → [#3] Bob's ``errors`` returns his bits at those positions → both tally the block.

Error correction and privacy amplification are not implemented: the key rate is the
asymptotic GLLP figure computed from the tallies.
"""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    B_X,
    B_Z,
    BINS,
    I_MU,
    BB84Params,
    Tally,
    Topic,
    account,
    describe,
    estimate,
    key_from,
    phase_for,
    readouts,
)


class Alice(Party):
    name = "ALICE"
    port = 5551
    instruments = (
        "ALICE-LD",
        "ALICE-IM",
        "ALICE-PM",
        "ALICE-AWG",
        "ALICE-VOA",
        "ALICE-OPM",
    )
    params = BB84Params()  # public parameters, agreed in advance

    def setup(self) -> None:
        p = self.params
        self.blocks: dict[int, dict] = {}
        self.tally = Tally()
        self.key: list[np.ndarray] = []
        print(
            f"transmitter up · μ={p.mu} ν={p.nu} vac≈{p.vacuum:.1e} with"
            f" p={p.probabilities} · p_Z={p.p_z} · {p.distance / 1e3:.0f} km to BOB"
        )

    # ---- physical layer --------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p, m = self.params, n // BINS  # the lab counts tagger bins; three per pulse
        label = self.rng.choice(3, size=m, p=p.probabilities).astype(np.int8)
        basis = (self.rng.random(m) >= p.p_z).astype(np.int8)  # 0 = Z, 1 = X
        bit = self.rng.integers(0, 2, size=m, dtype=np.int8)
        rate = self.hw.get("ALICE-LD", "rep_rate")
        transmission = np.array([1.0, p.nu / p.mu, 0.0])[label]  # μ, ν, vacuum
        v_int = self.hw.call("ALICE-IM", "voltage_for", transmission)
        v_ph = self.hw.call("ALICE-PM", "voltage_for", phase_for(basis, bit))
        self.hw.call("ALICE-AWG", "set_pattern", "ch1", v_int, rate)
        self.hw.call("ALICE-AWG", "set_pattern", "ch2", v_ph, rate)
        self.blocks[block] = dict(label=label, basis=basis, bit=bit)
        counts = np.bincount(label, minlength=3)
        print(
            f"block {block}: programmed {m:,} pulses · μ {counts[0]:,} ν {counts[1]:,}"
            f" vac {counts[2]:,} · Z {int(np.count_nonzero(basis == B_Z)):,} X"
            f" {int(np.count_nonzero(basis == B_X)):,} (all kept private)"
        )

    # ---- classical channel -----------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if msg.sender != "BOB" or block is None or block not in self.blocks:
            return
        st = self.blocks[block]
        if msg.topic == Topic.DETECT:
            self._sift(block, st, msg)
        elif msg.topic == Topic.ERRORS and "sift" in st:
            self._finish(block, st, msg)

    def _sift(self, block: int, st: dict, msg: Message) -> None:
        p = self.params
        label, basis, bit = st["label"], st["basis"], st["bit"]
        idx = np.asarray(msg.idx, dtype=np.int64)
        bob_basis = np.unpackbits(msg.basis, count=basis.size).astype(np.int8)
        same = basis == bob_basis
        sent = np.zeros(
            (3, 2), dtype=np.int64
        )  # what she sent, where Bob chose the same basis
        for k in range(3):
            for b in range(2):
                sent[k, b] = np.count_nonzero((label == k) & (basis == b) & same)
        keep = same[idx]
        label_d, basis_d, bits_d = label[idx], basis[idx], bit[idx]
        x = keep & (basis_d == B_X)  # X detections: all bits disclosed for e₁
        zmu = keep & (basis_d == B_Z) & (label_d == I_MU)  # Z-μ detections: the key
        pos = np.flatnonzero(zmu)
        sample = pos[
            self.rng.random(pos.size) < p.sample_fraction
        ]  # disclosed for E_μ^Z
        st["sift"] = dict(
            sent=sent,
            keep=keep,
            label=label_d,
            basis=basis_d,
            x=x,
            zmu=zmu,
            sample=sample,
            n=int(msg.n),
            doubles=int(msg.doubles),
        )
        st["bits"] = bits_d
        st["mine"] = dict(xbits=bits_d[x], sample_bits=bits_d[sample])
        self.net.send(
            "BOB",
            Topic.SIFT,
            block=block,
            keep=keep,
            label=label_d,
            sent=sent,
            xbits=st["mine"]["xbits"],
            sample=sample.astype(np.int32),
            sample_bits=st["mine"]["sample_bits"],
        )

    def _finish(self, block: int, st: dict, msg: Message) -> None:
        sift = st["sift"]
        theirs = dict(
            xbits=np.asarray(msg.xbits), sample_bits=np.asarray(msg.sample_bits)
        )
        t = account(sift, st["mine"], theirs)
        self.key.append(key_from(sift, st["bits"]))
        self.tally += t
        est = estimate(self.tally, self.params)
        print(
            f"block {block}: {int(sift['keep'].sum()):,} of {sift['keep'].size:,}"
            f" detections sifted · key +{t.key_bits:,} = {self.tally.key_bits:,} bits ·"
            f" {describe(est)}"
        )
        self.report(**readouts(self.tally, est, self.net.stats))
        del self.blocks[block]
