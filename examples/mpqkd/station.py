"""What Alice and Bob have in common: a transmitter to program and the sifting dialogue.

Physical layer (``prepare``): draw random intensities and phases, program the station's own AWG
through the hardware link. The draws stay in this process — they are the station's secret.

Classical channel (``receive``), per block, in the order of ``examples/mpqkd/reference.py``'s annotations:

    [#1] Charlie's ``clicks`` arrive  →  [#2] pair them (same algorithm at both stations)
    → [#3] send our basis labels, wait for the partner's  →  sift Z and X pairs
    → [#4] send our alignment angles θ for X pairs, wait for the partner's  →  keep matches
    → [#5] disclose the X bits (E_X) and, led by one station, a random sample of Z bits (E_Z);
           the disclosed Z bits are dropped from the key.

Error correction and privacy amplification ([#6]) are not implemented.
"""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    B_X,
    B_Z,
    MPQKDParams,
    Topic,
    assign_basis,
    pairing_rate_theory,
    simple_pairing,
)


class Station(Party):
    abstract = True
    tag = ""  # "ALICE" or "BOB": prefix of this station's instruments
    partner = ""
    leader = False  # the leader picks the Z sample to disclose
    params = MPQKDParams()  # public parameters, agreed in advance

    # ---- what differs between Alice and Bob ----------------------------------------------------
    def z_bit(self, intensity_front: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def x_bit(self, k: np.ndarray, flip: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    # ---- lifecycle ----------------------------------------------------------------------------------
    def setup(self) -> None:
        p = self.params
        self.blocks: dict[int, dict] = {}
        self.key: list[np.ndarray] = []
        self.key_bits = 0
        self.totals = dict(
            blocks=0, rounds=0, pairs=0, z=0, x=0, z_disclosed=0, z_errors=0, x_errors=0
        )
        print(
            f"station up · public parameters μ={p.mu} p_μ={p.p_mu} D={p.D} l={p.l} · "
            f"partner {self.partner} · {'leads' if self.leader else 'follows'} the error estimation"
        )

    # ---- physical layer -----------------------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p, tag = self.params, self.tag
        bright = self.rng.random(n) < p.p_mu
        k = self.rng.integers(0, p.D, size=n)
        rate = self.hw.get(f"{tag}-LD", "rep_rate")
        v_int = self.hw.call(f"{tag}-IM", "voltage_for", np.where(bright, 1.0, 0.0))
        v_ph = self.hw.call(f"{tag}-PM", "voltage_for", 2 * np.pi * k / p.D)
        self.hw.call(f"{tag}-AWG", "set_pattern", "ch1", v_int, rate)
        self.hw.call(f"{tag}-AWG", "set_pattern", "ch2", v_ph, rate)
        self.blocks.setdefault(block, {})["prep"] = (
            bright.astype(np.int8),
            k.astype(np.int16),
        )
        print(
            f"block {block}: programmed {n:,} pulses, {int(bright.sum()):,} with μ (intensities and phases kept private)"
        )

    # ---- classical channel ----------------------------------------------------------------------------
    def receive(self, msg: Message) -> None:
        block = msg.block
        if block is None or block not in self.blocks:
            return  # a block we never prepared (e.g. after a restart)
        st = self.blocks[block]
        if msg.topic == Topic.CLICKS and msg.sender == "CHARLIE":
            st["clicks"] = msg
            self._pair(block, st)
        elif msg.sender != self.partner:
            return
        elif msg.topic == Topic.BASIS:
            st["basis_partner"] = msg.basis
            self._sift(block, st)
        elif msg.topic == Topic.THETA:
            st["theta_partner"] = msg.theta
            self._align(block, st)
        elif msg.topic == Topic.XBITS:
            st["x_partner"] = msg.bits
            self._finish(block, st)
        elif msg.topic == Topic.SAMPLE:  # we follow: answer with our bits
            st["sample_pos"], st["sample_partner"] = msg.positions, msg.bits
            self._reply_sample(block, st)
            self._finish(block, st)
        elif msg.topic == Topic.SAMPLE_REPLY:  # we lead: the partner's bits came back
            st["sample_partner"] = msg.bits
            self._finish(block, st)

    def _pair(self, block: int, st: dict) -> None:
        m = st["clicks"]
        F, R = simple_pairing(m.idx, self.params.l)  # [#2] identical at both stations
        intensity = st["prep"][0]
        det_at = lambda rounds: m.det[
            np.searchsorted(m.idx, rounds)
        ]  # Charlie's L/R record, per clicked round
        st.update(
            F=F,
            R=R,
            det_F=det_at(F),
            det_R=det_at(R),
            p_click=m.idx.size / m.n,
            basis_mine=assign_basis(intensity[F], intensity[R]),
        )
        self.net.send(
            self.partner, Topic.BASIS, block=block, basis=st["basis_mine"]
        )  # [#3]
        self._sift(block, st)

    def _sift(self, block: int, st: dict) -> None:
        if "basis_mine" not in st or "basis_partner" not in st or "kz" in st:
            return
        ba, bb = st["basis_mine"], st["basis_partner"]
        z, x = (ba == B_Z) & (bb == B_Z), (ba == B_X) & (bb == B_X)
        intensity, phase = st["prep"]
        F, R = st["F"], st["R"]
        st["kz"] = self.z_bit(intensity[F][z])
        half = self.params.D // 2
        d = (phase[R][x].astype(int) - phase[F][x].astype(int)) % self.params.D
        flip = st["det_F"][x] != st["det_R"][x]  # (L,R) or (R,L): Bob's bit flips
        st["kx_all"], st["theta_mine"] = self.x_bit(
            (d // half).astype(np.int8), flip
        ), (d % half).astype(np.int8)
        self.net.send(
            self.partner, Topic.THETA, block=block, theta=st["theta_mine"]
        )  # [#4]
        self._align(block, st)
        self._reply_sample(block, st)

    def _align(self, block: int, st: dict) -> None:
        if "theta_mine" not in st or "theta_partner" not in st or "kx" in st:
            return
        keep = st["theta_mine"] == st["theta_partner"]
        st["kx"] = st["kx_all"][keep]
        self.net.send(
            self.partner, Topic.XBITS, block=block, bits=st["kx"]
        )  # [#5] X bits: estimation only
        if self.leader:
            sel = np.flatnonzero(
                self.rng.random(st["kz"].size) < self.params.sample_fraction
            )
            st["sample_pos"] = sel
            self.net.send(
                self.partner,
                Topic.SAMPLE,
                block=block,
                positions=sel,
                bits=st["kz"][sel],
            )  # [#5] Z sample
        self._finish(block, st)

    def _reply_sample(self, block: int, st: dict) -> None:
        if self.leader or "kz" not in st or "sample_pos" not in st or st.get("replied"):
            return
        st["replied"] = True
        self.net.send(
            self.partner,
            Topic.SAMPLE_REPLY,
            block=block,
            bits=st["kz"][st["sample_pos"]],
        )

    def _finish(self, block: int, st: dict) -> None:
        if any(k not in st for k in ("kx", "x_partner", "sample_partner")) or st.get(
            "done"
        ):
            return
        st["done"] = True
        kz, pos = st["kz"], st["sample_pos"]
        z_err = int(np.count_nonzero(kz[pos] != st["sample_partner"]))
        x_err = int(np.count_nonzero(st["kx"] != st["x_partner"]))
        keep = np.ones(kz.size, dtype=bool)
        keep[pos] = False  # disclosed bits leave the key
        self.key.append(kz[keep])
        self.key_bits += int(keep.sum())
        t, n = self.totals, st["clicks"].n
        t["blocks"] += 1
        t["rounds"] += n
        t["pairs"] += st["F"].size
        t["z"] += kz.size
        t["x"] += st["kx"].size
        t["z_disclosed"] += pos.size
        t["z_errors"] += z_err
        t["x_errors"] += x_err
        e_z = z_err / pos.size if pos.size else float("nan")
        e_x = x_err / st["kx"].size if st["kx"].size else float("nan")
        r_p = st["F"].size / n
        print(
            f"block {block}: {st['F'].size:,} pairs (r_p {r_p:.2e}, Eq.4 {pairing_rate_theory(st['p_click'], self.params.l):.2e}) → "
            f"{kz.size:,} Z ({pos.size} disclosed, E_Z {e_z:.3f}) · {st['kx'].size:,} X (E_X {e_x:.3f}) · "
            f"key +{int(keep.sum()):,} = {self.key_bits:,} bits"
        )
        self.report(
            **{
                "key bits": self.key_bits,
                "blocks": t["blocks"],
                "pairs / round": t["pairs"] / t["rounds"],
                "E_Z (disclosed)": (
                    t["z_errors"] / t["z_disclosed"] if t["z_disclosed"] else None
                ),
                "E_X": t["x_errors"] / t["x"] if t["x"] else None,
                "sent [kB]": self.net.stats["bytes_out"] / 1e3,
                "received [kB]": self.net.stats["bytes_in"] / 1e3,
            }
        )
        del self.blocks[block]
