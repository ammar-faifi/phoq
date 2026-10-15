"""Alice: one of the two transmitting stations. She leads the error estimation."""

from __future__ import annotations

import numpy as np

from .station import Station


class Alice(Station):
    name = "ALICE"
    port = 5551
    tag = "ALICE"
    partner = "BOB"
    leader = True
    instruments = ("ALICE-LD", "ALICE-IM", "ALICE-PM", "ALICE-AWG", "ALICE-VOA")

    def z_bit(self, intensity_front):
        """Z pairs: (0, μ) → 0, (μ, 0) → 1."""
        return (intensity_front > 0).astype(np.int8)

    def x_bit(self, k, flip):
        """X pairs: Alice keeps her phase-difference bit; the flip is Bob's."""
        return k
