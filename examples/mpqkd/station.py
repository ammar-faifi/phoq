"""What Alice and Bob have in common: a transmitter to program and the whole classical
dialogue.

Physical layer (``prepare``): draw a random intensity level (vacuum / ν / μ) and phase
per pulse and program the station's own AWG through the hardware link. The draws stay in
this process — they are the station's secret.

Classical channel (``receive``), per block, in the order of
``examples/mpqkd/reference.py``'s annotations:

    [#1] Charlie's ``clicks`` arrive → [#2] pair them (same algorithm at both stations)
    → [#3] send our basis labels and the pair's intensity level, wait for the partner's
           → sift
    → [#4] send our alignment angles θ (every pair), wait for the partner's → keep
           matches
    → [#5] disclose the X bits (E_X) and, led by one station, a random sample of the key
           bits (E_Z); the disclosed bits leave the key. Both stations tally every
           pair's public class (basis, level, θ match) — the decoy statistics behind the
           key rate.

and, once ``chunk_bits`` sifted key bits have accumulated, [#6] on that chunk:

    leader ``ec-start`` (Cascade seed, first parities) ⇄ ``ec-query`` / ``ec-parity`` …
    until the partner's ``ec-done`` carries a universal-hash tag of its corrected key →
    ``verified``; the leader then sizes privacy amplification from the key-rate estimate
    (:mod:`.keyrate`) and sends the Toeplitz seed and output length in ``pa``; both hash
    their verified chunks.

Every parity the leader discloses and every tag bit is counted as leaked and subtracted.
"""

from __future__ import annotations

import numpy as np

from phoq.parties import Message, Party
from .common import (
    B_X,
    B_Z,
    LMU,
    MPQKDParams,
    Topic,
    assign_basis,
    draw_levels,
    pairing_rate_theory,
    simple_pairing,
    transmissions,
)
from .keyrate import Counts, estimate, final_length, finite_key_length, h
from .postprocess import (
    CascadeCorrector,
    CascadeReference,
    bits_to_hex,
    key_digest,
    toeplitz_hash,
    verification_tag,
)


class Station(Party):
    abstract = True
    tag = ""  # "ALICE" or "BOB": prefix of this station's instruments
    partner = ""
    leader = (
        False  # the leader picks the Z sample, starts error correction and sizes PA
    )
    params = MPQKDParams()  # public parameters, agreed in advance

    # ---- what differs between Alice and Bob ------------------------------------------
    def z_bit(self, level_front: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def x_bit(self, k: np.ndarray, flip: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    # ---- lifecycle -------------------------------------------------------------------
    def setup(self) -> None:
        p = self.params
        self.blocks: dict[int, dict] = {}
        self.counts = Counts()
        self.pool: dict[int, dict] = (
            {}
        )  # block → sifted key bits waiting for error correction
        self.chunks: dict[int, dict] = (
            {}
        )  # chunk → its Cascade state, corrected key, leak, tag
        self.verified: list[int] = []  # chunks reconciled and verified, waiting for PA
        self.ec_active: int | None = None
        self.next_chunk = 0
        self.final: list[np.ndarray] = []
        self.final_bits = 0
        self.totals = dict(
            blocks=0,
            rounds=0,
            pairs=0,
            sifted=0,
            corrected=0,
            discarded=0,
            leak_ec=0,
            leak_verify=0,
            z_disclosed=0,
            z_errors=0,
            ec_errors=0,
            chunks=0,
        )
        self.laser = (
            0.0,
            0.0,
        )  # my laser's linewidth and detuning, announced to the partner
        print(
            f"station up · public parameters μ={p.mu} ν={p.nu} p_μ={p.p_mu}"
            f" p_ν={p.p_nu} D={p.D} l={p.l} · chunks of {p.chunk_bits} bits, Cascade"
            f" ×{p.cascade_passes}, {p.verify_bits}-bit tags, PA sized with the"
            f" {p.estimator} estimator · partner {self.partner} ·"
            f" {'leads' if self.leader else 'follows'} the post-processing"
        )

    # ---- physical layer --------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p, tag = self.params, self.tag
        level = draw_levels(self.rng, n, p)
        k = self.rng.integers(0, p.D, size=n)
        rate = self.hw.get(f"{tag}-LD", "rep_rate")
        self.laser = (
            float(self.hw.get(f"{tag}-LD", "linewidth")),
            float(self.hw.get(f"{tag}-LD", "detuning")),
        )
        v_int = self.hw.call(f"{tag}-IM", "voltage_for", transmissions(p)[level])
        v_ph = self.hw.call(f"{tag}-PM", "voltage_for", 2 * np.pi * k / p.D)
        self.hw.call(f"{tag}-AWG", "set_pattern", "ch1", v_int, rate)
        self.hw.call(f"{tag}-AWG", "set_pattern", "ch2", v_ph, rate)
        self.blocks.setdefault(block, {})["prep"] = (level, k.astype(np.int16))
        print(
            f"block {block}: programmed {n:,} pulses, {int((level == LMU).sum()):,}"
            f" with μ and {int((level == 1).sum()):,} with ν (levels and phases kept"
            " private)"
        )

    # ---- classical channel -----------------------------------------------------------
    def receive(self, msg: Message) -> None:
        if msg.sender == "CHARLIE" and msg.topic == Topic.CLICKS:
            st = self.blocks.get(msg.block)
            if (
                st is not None
            ):  # a block we never prepared (e.g. after a restart) is ignored
                st["clicks"] = msg
                self._pair(msg.block, st)
            return
        if msg.sender != self.partner:
            return
        if msg.topic == Topic.EC_START:
            self._ec_start(msg)
        elif msg.topic == Topic.EC_QUERY:
            self._ec_query(msg)
        elif msg.topic == Topic.EC_PARITY:
            self._ec_parity(msg)
        elif msg.topic == Topic.EC_DONE:
            self._ec_done(msg)
        elif msg.topic == Topic.VERIFIED:
            self._chunk_verified(msg.chunk, msg.ok)
        elif msg.topic == Topic.PA:
            self._pa(msg)
        else:
            st = self.blocks.get(msg.block)
            if st is None:
                return
            if msg.topic == Topic.BASIS:
                st["basis_partner"], st["level_partner"] = msg.basis, msg.level
                st["laser_partner"] = (msg.linewidth, msg.detuning)
                self._sift(msg.block, st)
            elif msg.topic == Topic.THETA:
                st["theta_partner"] = msg.theta
                self._align(msg.block, st)
            elif msg.topic == Topic.XBITS:
                st["x_partner"] = msg.bits
                self._finish(msg.block, st)
            elif msg.topic == Topic.SAMPLE:  # we follow: answer with our bits
                st["sample_pos"], st["sample_partner"] = msg.positions, msg.bits
                self._reply_sample(msg.block, st)
                self._finish(msg.block, st)
            elif (
                msg.topic == Topic.SAMPLE_REPLY
            ):  # we lead: the partner's bits came back
                st["sample_partner"] = msg.bits
                self._finish(msg.block, st)

    # ---- [#1]–[#5]: pairing, sifting, estimation -------------------------------------
    def _pair(self, block: int, st: dict) -> None:
        m = st["clicks"]
        F, R = simple_pairing(m.idx, self.params.l)  # [#2] identical at both stations
        level = st["prep"][0]
        det_at = lambda rounds: m.det[
            np.searchsorted(m.idx, rounds)
        ]  # Charlie's L/R per clicked round
        basis, lev = assign_basis(level[F], level[R])
        st.update(
            F=F,
            R=R,
            det_F=det_at(F),
            det_R=det_at(R),
            p_click=m.idx.size / m.n,
            basis_mine=basis,
            level_mine=lev,
        )
        self.net.send(  # [#3] the label and how bright, never which pulse was bright
            self.partner,
            Topic.BASIS,
            block=block,
            basis=basis,
            level=lev,
            linewidth=self.laser[0],
            detuning=self.laser[1],
        )
        self._sift(block, st)

    def _sift(self, block: int, st: dict) -> None:
        if "basis_mine" not in st or "basis_partner" not in st or "kz" in st:
            return
        ba, bb = st["basis_mine"], st["basis_partner"]
        la, lb = st["level_mine"], st["level_partner"]
        level, phase = st["prep"]
        F, R = st["F"], st["R"]
        z = (ba == B_Z) & (bb == B_Z) & (la == LMU) & (lb == LMU)  # the key pairs
        st["kz"] = self.z_bit(level[F][z])
        half = self.params.D // 2
        d = (phase[R].astype(int) - phase[F].astype(int)) % self.params.D
        flip = st["det_F"] != st["det_R"]  # (L,R) or (R,L): Bob's bit flips
        st["kx_all"] = self.x_bit(
            (d // half).astype(np.int8), flip
        )  # every pair, used on X ones
        st["theta_mine"] = (d % half).astype(np.int8)
        self.net.send(
            self.partner, Topic.THETA, block=block, theta=st["theta_mine"]
        )  # [#4]
        self._align(block, st)
        self._reply_sample(block, st)

    def _align(self, block: int, st: dict) -> None:
        if "theta_mine" not in st or "theta_partner" not in st or "kx" in st:
            return
        st["match"] = st["theta_mine"] == st["theta_partner"]
        st["xx"] = (
            (st["basis_mine"] == B_X) & (st["basis_partner"] == B_X) & st["match"]
        )
        st["kx"] = st["kx_all"][st["xx"]]
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
        p = self.params
        kz, pos = st["kz"], st["sample_pos"]
        z_err = int(np.count_nonzero(kz[pos] != st["sample_partner"]))
        x_err = st["kx"] != st["x_partner"]
        keep = np.ones(kz.size, dtype=bool)
        keep[pos] = False  # disclosed bits leave the key
        n = st["clicks"].n
        c = self.counts
        c.linewidth = (self.laser[0], st["laser_partner"][0])
        c.detuning = self.laser[1] - st["laser_partner"][1]
        c.add_block(
            n,
            st["clicks"].idx.size,
            st["basis_mine"],
            st["level_mine"],
            st["basis_partner"],
            st["level_partner"],
            st["match"],
            x_err,
            st["R"] - st["F"],
        )
        c.z_sample += pos.size
        c.z_sample_errors += z_err
        self.pool[block] = dict(
            bits=kz[keep].astype(np.uint8), sample=pos.size, errors=z_err
        )
        t = self.totals
        t["blocks"] += 1
        t["rounds"] += n
        t["pairs"] += st["F"].size
        t["sifted"] += int(keep.sum())
        t["z_disclosed"] += pos.size
        t["z_errors"] += z_err
        n_xx = int(c.x_sifted[LMU, LMU])
        e_z = z_err / pos.size if pos.size else float("nan")
        e_x = c.x_errors[LMU, LMU] / n_xx if n_xx else float("nan")
        r_p = st["F"].size / n
        print(
            f"block {block}: {st['F'].size:,} pairs (r_p {r_p:.2e}, Eq.4"
            f" {pairing_rate_theory(st['p_click'], p.l):.2e}) → {kz.size:,} key Z pairs"
            f" ({pos.size} disclosed, E_Z {e_z:.3f}) · {st['kx'].size:,} X"
            f" ({int(st['xx'].sum())} μμ, E_X(μμ) {e_x:.3f}) · sifted"
            f" +{int(keep.sum()):,} = {t['sifted']:,} bits"
        )
        del self.blocks[block]
        if self.leader:
            self._maybe_start_ec()
        self._readouts()

    # ---- [#6] error correction -------------------------------------------------------
    def _maybe_start_ec(self) -> None:
        """Leader: reconcile the pooled sifted bits once a chunk's worth is there
        (one chunk at a time)."""
        p = self.params
        if (
            self.ec_active is not None
            or sum(e["bits"].size for e in self.pool.values()) < p.chunk_bits
        ):
            return
        blocks = sorted(self.pool)
        raw = np.concatenate([self.pool[b]["bits"] for b in blocks])
        sample = sum(self.pool[b]["sample"] for b in blocks)
        errors = sum(self.pool[b]["errors"] for b in blocks)
        qber = (errors + 1) / (sample + 2)  # Laplace estimate from the disclosed sample
        for b in blocks:
            del self.pool[b]
        chunk, self.next_chunk = self.next_chunk, self.next_chunk + 1
        seed = int(self.rng.integers(2**31))
        ref = CascadeReference(raw, seed, qber, p.cascade_passes)
        first, parities = ref.first_pass()
        self.chunks[chunk] = dict(
            blocks=blocks, n=raw.size, key=raw, ref=ref, qber=qber
        )
        self.ec_active = chunk
        self.net.send(
            self.partner,
            Topic.EC_START,
            chunk=chunk,
            blocks=blocks,
            seed=seed,
            qber=qber,
            parity_blocks=first,
            parities=parities,
        )
        print(
            f"chunk {chunk}: reconciling {raw.size:,} sifted bits of blocks"
            f" {blocks[0]}–{blocks[-1]} (Cascade, k₁ = {ref.sizes[0]} from E_Z ≈"
            f" {qber:.4f}, {len(parities)} first parities)"
        )

    def _ec_start(self, msg: Message) -> None:
        """Follower: start correcting our copy of the chunk toward the leader's."""
        missing = [b for b in msg.blocks if b not in self.pool]
        if missing:
            print(
                f"⚠ chunk {msg.chunk}: blocks {missing} never finished here; chunk"
                " dropped"
            )
            return
        raw = np.concatenate([self.pool.pop(b)["bits"] for b in msg.blocks])
        cor = CascadeCorrector(raw, msg.seed, msg.qber, self.params.cascade_passes)
        self.chunks[msg.chunk] = dict(
            blocks=list(msg.blocks), n=raw.size, cor=cor, qber=msg.qber
        )
        self.ec_active = msg.chunk
        print(
            f"chunk {msg.chunk}: reconciling {raw.size:,} sifted bits of blocks"
            f" {msg.blocks[0]}–{msg.blocks[-1]}"
        )
        self._ec_continue(
            msg.chunk, cor.receive(list(msg.parity_blocks), list(msg.parities))
        )

    def _ec_continue(self, chunk: int, asks: list) -> None:
        if asks:
            self.net.send(self.partner, Topic.EC_QUERY, chunk=chunk, blocks=asks)
            return
        ch = self.chunks[chunk]
        cor = ch["cor"]
        ch["key"], ch["leak"], ch["errors"] = cor.key, cor.leak, len(cor.corrected)
        seed = int(self.rng.integers(2**31))
        ch["tag"] = verification_tag(cor.key, seed, self.params.verify_bits)
        self.net.send(
            self.partner,
            Topic.EC_DONE,
            chunk=chunk,
            errors=ch["errors"],
            leak=ch["leak"],
            tag_seed=seed,
            tag=ch["tag"],
        )

    def _ec_query(self, msg: Message) -> None:
        ref = self.chunks[msg.chunk]["ref"]
        self.net.send(
            self.partner,
            Topic.EC_PARITY,
            chunk=msg.chunk,
            blocks=msg.blocks,
            parities=ref.parities(msg.blocks),
        )

    def _ec_parity(self, msg: Message) -> None:
        cor = self.chunks[msg.chunk]["cor"]
        self._ec_continue(msg.chunk, cor.receive(list(msg.blocks), list(msg.parities)))

    def _ec_done(self, msg: Message) -> None:
        """Leader: the partner finished Cascade; compare its tag with ours and tell
        it."""
        ch = self.chunks[msg.chunk]
        ch["leak"], ch["errors"] = ch["ref"].leak, msg.errors
        if msg.leak != ch["leak"]:
            print(
                f"⚠ chunk {msg.chunk}: leak accounting differs ({ch['leak']} sent,"
                f" partner counted {msg.leak})"
            )
        ch["tag"] = verification_tag(ch["key"], msg.tag_seed, self.params.verify_bits)
        ok = ch["tag"] == msg.tag
        self.net.send(self.partner, Topic.VERIFIED, chunk=msg.chunk, ok=ok)
        self._chunk_verified(msg.chunk, ok)
        self._maybe_pa()
        self._maybe_start_ec()

    def _chunk_verified(self, chunk: int, ok: bool) -> None:
        ch = self.chunks[chunk]
        p, t = self.params, self.totals
        t["leak_ec"] += ch["leak"]
        t["leak_verify"] += p.verify_bits
        t["ec_errors"] += ch["errors"]
        if ok:
            t["corrected"] += ch["n"]
            self.verified.append(chunk)
        else:
            t["discarded"] += ch["n"]
            del self.chunks[chunk]
        if self.ec_active == chunk:
            self.ec_active = None
        f_ec = (
            ch["leak"] / (ch["n"] * h(ch["errors"] / ch["n"]))
            if ch["errors"]
            else float("nan")
        )
        print(
            f"chunk {chunk}: {ch['n']:,} bits reconciled, {ch['errors']} errors"
            f" corrected (E_Z {ch['errors'] / ch['n']:.2e}), {ch['leak']} parities"
            " leaked"
            + (f" (f = {f_ec:.2f})" if ch["errors"] else "")
            + f" · {p.verify_bits}-bit tag"
            f" {'matches ✓' if ok else 'DIFFERS ✗ — chunk discarded'}"
        )
        self._readouts()

    # ---- [#6] privacy amplification --------------------------------------------------
    def _maybe_pa(self) -> None:
        """Leader: size the Toeplitz hash of all verified chunks from the key-rate
        estimate."""
        if not self.verified:
            return
        p = self.params
        n = sum(self.chunks[c]["n"] for c in self.verified)
        leak_ec = sum(self.chunks[c]["leak"] for c in self.verified)
        leak_v = p.verify_bits * len(self.verified)
        est = estimate(self.counts, p)
        length = final_length(n, est, leak_ec, leak_v)
        if length <= 0:
            print(
                f"PA: no secret key yet from {n:,} corrected bits — secret fraction"
                f" {est.secret_fraction:.3f} (q₁₁ ≥ {est.q11_lower:.3f}, e₁₁"
                f" {est.e11_used:.4f}) against {leak_ec + leak_v} leaked bits; waiting"
                " for more"
            )
            return
        seed = int(self.rng.integers(2**31))
        chunks = list(self.verified)
        self.net.send(self.partner, Topic.PA, chunks=chunks, seed=seed, length=length)
        self._amplify(chunks, seed, length, est)

    def _pa(self, msg: Message) -> None:
        est = estimate(
            self.counts, self.params
        )  # from the same public numbers as the leader's
        n = sum(self.chunks[c]["n"] for c in msg.chunks)
        mine = final_length(
            n,
            est,
            sum(self.chunks[c]["leak"] for c in msg.chunks),
            self.params.verify_bits * len(msg.chunks),
        )
        if abs(mine - msg.length) > max(
            8, 0.25 * msg.length
        ):  # our counts may lead by a block
            print(
                f"⚠ PA: the leader sizes {msg.length} final bits, our own estimate says"
                f" {mine}"
            )
        self._amplify(list(msg.chunks), msg.seed, msg.length, est)

    def _amplify(self, chunks: list[int], seed: int, length: int, est) -> None:
        raw = np.concatenate([self.chunks[c]["key"] for c in chunks])
        leak = sum(
            self.chunks[c]["leak"] for c in chunks
        ) + self.params.verify_bits * len(chunks)
        final = toeplitz_hash(raw, seed, length)
        self.final.append(final)
        self.final_bits += length
        for c in chunks:
            self.verified.remove(c)
            del self.chunks[c]
        self.totals["chunks"] += len(chunks)
        print(
            f"PA: {raw.size:,} corrected bits × secret fraction"
            f" {est.secret_fraction:.3f} (q₁₁ ≥ {est.q11_lower:.3f}, e₁₁"
            f" {est.e11_used:.4f} [{est.estimator}]) − {leak} leaked → {length} final"
            f" bits · key {bits_to_hex(final)}"
        )
        self._readouts()

    # ---- read-outs -------------------------------------------------------------------
    def _readouts(self) -> None:
        p, t, c = self.params, self.totals, self.counts
        est = estimate(c, p) if c.pairs else None
        rounds = t["rounds"] or 1
        n_xx = int(c.x_sifted[LMU, LMU])
        corrected_rate = (
            t["leak_ec"] / t["corrected"] if t["corrected"] else float("nan")
        )
        formula = (
            (c.key_pairs / rounds)
            * (1 - p.sample_fraction)
            * (est.secret_fraction - (corrected_rate if t["corrected"] else 0.0))
            if est
            else 0.0
        )
        out = {
            "final key bits": self.final_bits,
            "key rate [bit/pulse]": self.final_bits / rounds,
            "sifted bits": t["sifted"],
            "corrected bits": t["corrected"],
            "leak EC [bits]": t["leak_ec"],
            "leak verify [bits]": t["leak_verify"],
            "rate, asymptotic formula [bit/pulse]": formula,
            "secret fraction": est.secret_fraction if est else None,
            "q_11 (decoy lower bound)": est.q11_lower if est else None,
            f"e_11 ({p.estimator})": est.e11_used if est else None,
            "e_11 (decoy bound)": (
                est.e11_decoy
                if est and est.e11_decoy == est.e11_decoy
                else "unresolved (see keyrate.py)"
            ),
            "E_Z (sample)": (
                t["z_errors"] / t["z_disclosed"] if t["z_disclosed"] else None
            ),
            "E_Z (corrected)": (
                t["ec_errors"] / t["corrected"] if t["corrected"] else None
            ),
            "E_X (μμ)": c.x_errors[LMU, LMU] / n_xx if n_xx else None,
            "E_X model": est.e_x_model if est else None,
            "Z-decoy (ν,ν) pairs": int(c.z[1, 1]),
            "blocks": t["blocks"],
            "pairs / round": t["pairs"] / rounds,
            "final key sha256": (
                key_digest(np.concatenate(self.final)) if self.final else ""
            ),
            "sent [kB]": self.net.stats["bytes_out"] / 1e3,
            "received [kB]": self.net.stats["bytes_in"] / 1e3,
        }
        if est and t["corrected"]:
            out["finite-key bits (simplified estimate, ε=%g)" % p.eps] = (
                finite_key_length(
                    t["corrected"], c, p, est, t["leak_ec"], t["leak_verify"]
                )
            )
        self.report(**out)
