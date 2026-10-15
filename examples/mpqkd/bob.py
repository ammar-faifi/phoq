"""Bob: the other transmitting station. He answers Alice's error-estimation sample."""

from __future__ import annotations

import numpy as np

from .station import Station


class Bob(Station):
    name = "BOB"
    port = 5552
    tag = "BOB"
    partner = "ALICE"
    leader = False
    instruments = ("BOB-LD", "BOB-IM", "BOB-PM", "BOB-AWG", "BOB-VOA")

    def z_bit(self, intensity_front):
        """Z pairs: (μ, 0) → 0, (0, μ) → 1 — the mirror of Alice's mapping."""
        return (intensity_front == 0).astype(np.int8)

    def x_bit(self, k, flip):
        """X pairs: flip the bit when Charlie's two clicks fell on different detectors (L,R) or (R,L)."""
        return (k ^ flip.astype(np.int8)).astype(np.int8)
