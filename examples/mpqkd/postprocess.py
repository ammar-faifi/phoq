"""Classical post-processing primitives for [CLASSICAL #6]: Cascade, universal hashing.

Pure functions and two small state machines, so they can be exercised in one process
(tests) and driven over the classical channel by the stations (:mod:`.station`). Nothing
here knows about sockets: the corrector asks for parities of (pass, lo, hi) blocks and
the reference answers them; every answered parity is one leaked bit and both sides count
the same number.

Cascade (Brassard & Salvail 1994): the key is shuffled per pass with a public seed and
cut into blocks of k_1, 2k_1, 4k_1 … bits; for every block whose parity differs from the
reference the corrector binary-searches the error with the reference's sub-block
parities. Correcting a bit flips the parity of the blocks of earlier passes that contain
it, which exposes further errors ("cascade"). Residual errors are caught by the
verification tag. Toeplitz hashing with a public seed is a universal₂ family, used both
for that tag and for privacy amplification.
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
from scipy.signal import fftconvolve

Block = tuple[
    int, int, int
]  # (pass, lo, hi) in the pass's permuted order, hi exclusive


# ---- shared pieces -------------------------------------------------------------------
def block_sizes(n: int, qber: float, passes: int) -> list[int]:
    """k_1 ≈ 0.73 / QBER, doubling each pass, but never more than half the key: a pass
    whose single block spans everything cannot separate two hidden errors, while two
    blocks still do so half of the time (one or two extra parities per pass)."""
    cap = max(1, n // 2)
    k1 = cap if qber <= 0 else int(math.ceil(0.73 / qber))
    k1 = max(2, min(cap, k1)) if n > 3 else max(1, n)
    return [max(1, min(cap, k1 << i)) for i in range(passes)]


def permutations(seed: int, n: int, passes: int) -> list[np.ndarray]:
    return [np.random.default_rng([seed, i]).permutation(n) for i in range(passes)]


def top_blocks(p: int, n: int, size: int) -> list[Block]:
    return [(p, lo, min(n, lo + size)) for lo in range(0, n, size)]


def _parity(bits: np.ndarray, perm: np.ndarray, lo: int, hi: int) -> int:
    return int(bits[perm[lo:hi]].sum() & 1)


class CascadeReference:
    """The side whose key is the reference (Alice): answers parity queries, counts what
    it leaks."""

    def __init__(self, key: np.ndarray, seed: int, qber: float, passes: int = 4):
        self.key = np.asarray(key, dtype=np.uint8)
        self.n = self.key.size
        self.sizes = block_sizes(self.n, qber, passes)
        self.perms = permutations(seed, self.n, passes)
        self.leak = 0

    def parities(self, blocks: list[Block]) -> list[int]:
        self.leak += len(blocks)
        return [_parity(self.key, self.perms[p], lo, hi) for p, lo, hi in blocks]

    def first_pass(self) -> tuple[list[Block], list[int]]:
        blocks = top_blocks(0, self.n, self.sizes[0]) if self.n else []
        return blocks, self.parities(blocks)


class CascadeCorrector:
    """The side that corrects its key toward the reference (Bob).

    Drive it with ``receive(blocks, parities)`` for every answer (the first call carries
    the first pass's top-level blocks); it returns the next blocks to ask for, ``[]``
    once finished.
    """

    def __init__(self, key: np.ndarray, seed: int, qber: float, passes: int = 4):
        self.key = np.array(key, dtype=np.uint8)
        self.n = self.key.size
        self.passes = passes
        self.sizes = block_sizes(self.n, qber, passes)
        self.perms = permutations(seed, self.n, passes)
        self.known: dict[Block, int] = {}  # the reference's parities, never stale
        self.active: set[Block] = set()  # blocks whose relative parity is odd
        self.started = 0  # passes whose top-level parities are known
        self.corrected: list[int] = []
        self.leak = 0
        self.done = self.n == 0

    # ---- helpers ---------------------------------------------------------------------
    def _mine(self, b: Block) -> int:
        p, lo, hi = b
        return _parity(self.key, self.perms[p], lo, hi)

    def _odd(self, b: Block) -> bool:
        return self._mine(b) != self.known[b]

    def _top_block(self, p: int, bit: int) -> Block:
        pos = int(np.flatnonzero(self.perms[p] == bit)[0])
        lo = pos - pos % self.sizes[p]
        return (p, lo, min(self.n, lo + self.sizes[p]))

    def _flip(self, p: int, lo: int) -> None:
        bit = int(self.perms[p][lo])
        self.key[bit] ^= 1
        self.corrected.append(bit)
        self.active = {
            b for b in self.active if self._odd(b)
        }  # some searches just closed
        for q in range(
            self.started
        ):  # cascade: earlier passes' blocks holding this bit
            top = self._top_block(q, bit)
            if self._odd(top) and not any(
                b[0] == q and top[1] <= b[1] and b[2] <= top[2] for b in self.active
            ):
                self.active.add(top)

    def _step(self) -> list[Block]:
        """Descend every active search as far as cached parities allow; return what to
        ask."""
        while True:
            queries: list[Block] = []
            progressed = False
            for b in sorted(self.active):
                if b not in self.active:
                    continue  # closed by a flip earlier in this sweep
                p, lo, hi = b
                if hi - lo == 1:
                    self.active.discard(b)
                    self._flip(p, lo)
                    progressed = True
                    continue
                mid = (lo + hi) // 2
                left, right = (p, lo, mid), (p, mid, hi)
                if left not in self.known:
                    queries.append(left)
                    continue
                self.known.setdefault(right, self.known[b] ^ self.known[left])
                self.active.discard(b)
                self.active.add(left if self._odd(left) else right)
                progressed = True
            if queries:
                return sorted(set(queries))
            if progressed:
                continue
            if (
                self.active
            ):  # cannot happen: every active block either flips, descends or asks
                raise RuntimeError("cascade stalled")
            if self.started >= self.passes:
                self.done = True
                return []
            blocks = top_blocks(self.started, self.n, self.sizes[self.started])
            return blocks

    def receive(self, blocks: list[Block], parities: list[int]) -> list[Block]:
        """Record the reference's parities for ``blocks`` and return the next
        queries."""
        if self.done:
            return []
        self.leak += len(parities)
        for b, par in zip(blocks, parities):
            self.known[b] = int(par)
        if blocks and all(
            b[0] == self.started and b[1] % self.sizes[self.started] == 0
            for b in blocks
        ):
            self.started += 1  # a pass's top-level parities arrived
        for b in blocks:
            if self._odd(b) and not any(
                x[0] == b[0] and x[1] <= b[1] and b[2] <= x[2] for x in self.active
            ):
                self.active.add(b)
        return self._step()


def cascade_in_process(
    key_a: np.ndarray, key_b: np.ndarray, seed: int, qber: float, passes: int = 4
):
    """Run Cascade between two keys in one process; returns (corrected key_b, reference,
    corrector)."""
    ref = CascadeReference(key_a, seed, qber, passes)
    cor = CascadeCorrector(key_b, seed, qber, passes)
    blocks, parities = ref.first_pass()
    while True:
        asks = cor.receive(blocks, parities)
        if not asks:
            break
        blocks, parities = asks, ref.parities(asks)
    return cor.key, ref, cor


# ---- universal hashing ---------------------------------------------------------------
def toeplitz_hash(bits: np.ndarray, seed: int, m: int) -> np.ndarray:
    """``m`` output bits of a Toeplitz matrix drawn from ``seed`` applied to ``bits``
    over GF(2)."""
    x = np.asarray(bits, dtype=np.uint8)
    n = x.size
    if m <= 0 or n == 0:
        return np.zeros(0, dtype=np.uint8)
    r = np.random.default_rng(seed).integers(0, 2, n + m - 1, dtype=np.uint8)
    conv = fftconvolve(
        r.astype(float), x.astype(float)
    )  # sums are exact in float64 here
    return (np.rint(conv[n - 1 : n - 1 + m]).astype(np.int64) & 1).astype(np.uint8)


def verification_tag(bits: np.ndarray, seed: int, tag_bits: int) -> bytes:
    """A universal-hash tag of the key: equal tags ⇒ equal keys except with probability
    2^-tag_bits."""
    return np.packbits(toeplitz_hash(bits, seed, tag_bits)).tobytes()


def bits_to_hex(bits: np.ndarray) -> str:
    return (
        np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes().hex()
        if np.size(bits)
        else ""
    )


def key_digest(bits: np.ndarray) -> str:
    """Short fingerprint of a key for the read-outs (both consoles must show the same
    one)."""
    return hashlib.sha256(
        np.packbits(np.asarray(bits, dtype=np.uint8)).tobytes()
    ).hexdigest()[:16]
