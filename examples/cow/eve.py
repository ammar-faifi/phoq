"""Eve: intercept-resend in the time basis on a fraction of the bits, from a station
spliced into the fiber.

Load her with ``--party examples/cow/eve.py`` (``build()`` then puts her station on the
line, see ``common.eavesdropper``). For every logical bit she picks, her 2×2 switch
sends both of its slots into her time-of-arrival detector and puts her own transmitter's
light onto the line instead; every other bit passes through the bar state untouched. She
re-prepares what she saw with her own CW laser (``common.intercept``): the bit, a decoy
when both slots clicked, nothing when neither did (or a decoy,
``PHOQ_EVE_INCONCLUSIVE=decoy``). Her pulses are brighter and rarer, matched to the
photon flux she took out (``common.resend_mu``), so Bob's detection rate stays put and
his data-line QBER barely moves (her modulator's leak into the pairs she leaves empty).
What she cannot restore is the phase between neighboring pulses: across every boundary
between one of her pulses and one of Alice's, and inside every decoy she took for a bit,
the coherence is gone, and V_d and V_1−0 fall with the fraction she intercepts
(``common.eve_theory``).

She is a ``stage = 1`` party: the lab records her tagger before Bob's and lets her
program her transmitter from the clicks (``measure``) before the light goes on — the
feed-forward a real station does with some latency, absorbed by Bob's clock calibration.

Environment: ``PHOQ_EVE_FRACTION`` (default 1.0), ``PHOQ_EVE_INCONCLUSIVE`` (``empty``,
the default, or ``decoy``) and ``PHOQ_EVE_MU`` (photons per re-sent pulse at her
detector; default: flux-matched) set her strategy without editing this file. In the lab,
turning EVE-AWG's CH2 output off leaves her switch in the bar state: she stands down
until it is back on.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from phoq.loading import import_source
from phoq.parties import Party

# the protocol package, however it was loaded
cow = import_source(Path(__file__).resolve().parent)
common = cow.common


class Eve(Party):
    name = "EVE"
    port = 5563
    stage = 1  # she detects before Bob and re-encodes in measure()
    instruments = (
        "EVE-SW",
        "EVE-D",
        "EVE-TAGGER",
        "EVE-TX-LD",
        "EVE-TX-IM",
        "EVE-TX-VOA",
        "EVE-AWG",
    )
    params = common.COWParams()  # Alice and Bob's public parameters: she knows them too
    # share of the bits intercepted
    fraction = float(os.environ.get("PHOQ_EVE_FRACTION", "1.0"))
    # what she re-sends when she saw nothing
    inconclusive = os.environ.get("PHOQ_EVE_INCONCLUSIVE", "empty")
    resend_mu = (
        float(os.environ["PHOQ_EVE_MU"]) if "PHOQ_EVE_MU" in os.environ else None
    )

    def setup(self) -> None:
        if self.inconclusive not in common.INCONCLUSIVE:
            raise ValueError(
                f"PHOQ_EVE_INCONCLUSIVE must be one of {common.INCONCLUSIVE}"
            )
        self.blocks: dict[int, np.ndarray] = {}
        self.totals = dict(
            bits=0, attacked=0, clicks=0, resent_bits=0, resent_decoys=0, empty=0
        )
        # her AWG's CH2 drives the switch; with that output off it rests in bar
        self.engaged = True
        # calibrated: μ photons per pulse at her detector
        self.att0 = self.hw.get("EVE-TX-VOA", "attenuation_db")
        self.efficiency = self.hw.get("EVE-D", "efficiency")
        self.dark = float(self.hw.call("EVE-TX-IM", "voltage_for", 0.0))
        print(
            "eavesdropper up · intercept-resend in the time basis on"
            f" {self.fraction:.0%} of the bits · inconclusive → {self.inconclusive} ·"
            " re-sends with μ_E ="
            f" {'flux-matched' if self.resend_mu is None else self.resend_mu}"
        )

    # ---- physical layer --------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        p = self.params
        engaged = bool(self.hw.get("EVE-AWG", "ch2_enabled"))
        if engaged != self.engaged:
            self.engaged = engaged
            print(
                "switch drive on: intercepting again"
                if engaged
                else (
                    "switch drive off: the switch rests in bar, Alice's light passes"
                    " untouched"
                )
            )
        # per logical bit: both of its slots
        attack = (self.rng.random(n // 2) < self.fraction) & engaged
        # cross: into her detector, hers out
        v_sw = self.hw.call("EVE-SW", "voltage_for", np.repeat(attack, 2))
        self.hw.call("EVE-AWG", "set_pattern", "ch2", v_sw, p.rate)
        # her transmitter dark while she listens: a one-symbol pattern keeps the channel
        # in pattern mode
        self.hw.call("EVE-AWG", "set_pattern", "ch1", [self.dark], 2 * p.rate)
        self.blocks[block] = attack

    def measure(self, batch) -> None:
        attack = self.blocks.pop(batch.seq, None)
        if attack is None:
            return
        p, d = self.params, batch.clicks["ch1"]
        c0, c1 = d[0::2], d[1::2]
        sent = common.intercept(c0, c1, attack, self.inconclusive)
        occ = common.occupied(sent)
        clicks = int(np.count_nonzero(c0 & attack) + np.count_nonzero(c1 & attack))
        attacked = int(attack.sum())
        mu_e = self.resend_mu or common.resend_mu(
            clicks, attacked, int(occ.sum()), self.efficiency, p
        )
        self.hw.set(
            "EVE-TX-VOA", attenuation_db=common.resend_attenuation(self.att0, mu_e, p)
        )
        volts = self.hw.call("EVE-TX-IM", "voltage_for", common.carving(occ))
        self.hw.call("EVE-AWG", "set_pattern", "ch1", volts, 2 * p.rate)
        counts = np.bincount(sent[attack], minlength=4)
        t = self.totals
        t["bits"] += attack.size
        t["attacked"] += attacked
        t["clicks"] += clicks
        t["resent_bits"] += int(counts[common.BIT0] + counts[common.BIT1])
        t["resent_decoys"] += int(counts[common.DECOY])
        t["empty"] += int(counts[common.NOTHING])
        print(
            f"block {batch.seq}: intercepted {attacked:,} of {attack.size:,} bits,"
            f" {clicks:,} clicks → re-sent {counts[0] + counts[1]:,} bits,"
            f" {counts[2]:,} decoys, {counts[3]:,} empty · μ_E = {mu_e:.3f}"
        )
        pulses = attacked * (1 + p.decoy)
        self.report(
            # this block first (the laptop's screen shows three entries), then the
            # totals
            **{
                "intercepting": attacked / attack.size,
                "p(click | pulse)": clicks / pulses if pulses else float("nan"),
                "re-sent μ_E": mu_e,
                "bits intercepted": t["attacked"],
                "bits re-sent": t["resent_bits"],
                "decoys re-sent": t["resent_decoys"],
                "pairs left empty": t["empty"],
            }
        )
