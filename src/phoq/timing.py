"""A station's clock: its time tagger's stamps, folded onto the slots of a protocol.

A time tagger stamps every click on its own clock and knows nothing of where Alice's
pulses fall. The station's software holds her clock, slot n centered on t₀ + n·τ, and
folds each stamp onto it: t = t₀ + n·τ + φ. The slot n says which of Alice's pulses a
click belongs to, and the phase φ where in the slot it fell, which is what a click
window cuts on. That is how a bench reads its tagger, and how a protocol here reads its
own, rather than through the counter's record of bins (``CounterResult.clicks``), which
only a simulator has.

τ is the agreed clock period, or one recovered from the clicks. t₀, where the tagger
stamps the center of the session's first pulse, is found once per session.
"""

from __future__ import annotations

import numpy as np


def fold(t, t0: float, period: float) -> tuple[np.ndarray, np.ndarray]:
    """The slot n and the phase φ, in [−period/2, period/2), of each time stamp:
    t = t0 + n·period + φ, slot n centered on t0 + n·period."""
    x = (np.asarray(t, dtype=float) - t0) / period
    n = np.floor(x + 0.5)
    return n.astype(np.int64), (x - n) * period
