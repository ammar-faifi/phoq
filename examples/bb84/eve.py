"""Eve: intercept-resend on a fraction of the pulses, from a station spliced into the fiber.

Load her with ``--party examples/bb84/eve.py`` (``build()`` then puts her station on the line,
see ``common.build``). Her 2×2 optical switch diverts the pulses she picks into her own receiver —
a copy of Bob's — and puts her own transmitter's pulse into the line instead; every other pulse
passes through the bar state untouched. She measures in a random basis and, for every click,
re-sends the state she found. Because her detection is lossy she re-sends brighter pulses, so
that Bob's gain looks the same on average (``resend_mu``); the decoy statistics still see it.

She is a ``stage = 1`` party: the lab records her tagger before Bob's and lets her program her
transmitter from the clicks (``measure``) before the light goes on — the feed-forward a real
station does with some latency, absorbed by Bob's clock calibration.

Environment: ``PHOQ_EVE_FRACTION`` (default 1.0) and ``PHOQ_EVE_MU`` (default: as bright as
needed to match Bob's expected gain) set her strategy without editing this file.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from phoq.loading import import_source
from phoq.parties import Party

bb84 = import_source(
    Path(__file__).resolve().parent
)  # the protocol package, however it was loaded
common = bb84.common


class Eve(Party):
    name = "EVE"
    port = 5553
    stage = 1  # she detects before Bob and re-encodes in measure()
    instruments = (
        "EVE-SW",
        "EVE-RX-PM",
        "EVE-RX-PS",
        "EVE-RX-AWG",
        "EVE-DET-0",
        "EVE-DET-1",
        "EVE-TAGGER",
        "EVE-TX-LD",
        "EVE-TX-IM",
        "EVE-TX-PM",
        "EVE-TX-PS",
        "EVE-TX-VOA",
        "EVE-TX-AWG",
        "EVE-TX-OPM",
    )
    params = common.BB84Params()
    fraction = float(
        os.environ.get("PHOQ_EVE_FRACTION", "1.0")
    )  # share of pulses intercepted
    resend_mu = (  # photons per re-sent pulse at the switch; None: match Alice's mean intensity / her click probability
        float(os.environ["PHOQ_EVE_MU"]) if "PHOQ_EVE_MU" in os.environ else None
    )

    def setup(self) -> None:
        self.blocks: dict[int, dict] = {}
        self.totals = dict(blocks=0, pulses=0, intercepted=0, clicks=0, resent=0)
        self.att0 = self.hw.get(
            "EVE-TX-VOA", "attenuation_db"
        )  # calibrated: μ photons per pulse at the switch
        print(
            f"eavesdropper up · intercept-resend on {self.fraction:.0%} of the pulses · "
            f"re-sends with μ_E = {'gain-matched' if self.resend_mu is None else self.resend_mu}"
        )

    # ---- physical layer -----------------------------------------------------------------------------
    def prepare(self, block: int, n: int) -> None:
        m = n // common.BINS
        attack = self.rng.random(m) < self.fraction
        basis = self.rng.integers(0, 2, size=m, dtype=np.int8)
        rate = self.params.rep_rate
        v_sw = self.hw.call(
            "EVE-SW", "voltage_for", attack
        )  # cross: into her receiver, hers out
        v_pm = self.hw.call("EVE-RX-PM", "voltage_for", common.measurement_phase(basis))
        self.hw.call("EVE-RX-AWG", "set_pattern", "ch2", v_sw, rate)
        self.hw.call("EVE-RX-AWG", "set_pattern", "ch1", v_pm, rate)
        self.blocks[block] = dict(attack=attack, basis=basis)

    def measure(self, batch) -> None:
        st = self.blocks.pop(batch.seq, None)
        if st is None:
            return
        attack, basis = st["attack"], st["basis"]
        c0, c1 = common.middle_slot(batch.clicks["ch1"]), common.middle_slot(
            batch.clicks["ch2"]
        )
        idx = np.flatnonzero((c0 | c1) & attack)
        both = c0[idx] & c1[idx]
        bits = np.where(c1[idx] & ~c0[idx], 1, 0).astype(np.int8)
        bits[both] = self.rng.integers(0, 2, size=int(both.sum()), dtype=np.int8)
        # re-send what she found, in the basis she guessed; nothing where she saw nothing
        transmission = np.zeros(attack.size)
        transmission[idx] = 1.0
        phase = np.zeros(attack.size)
        phase[idx] = common.phase_for(basis[idx], bits)
        intercepted = int(attack.sum())
        p_click = idx.size / intercepted if intercepted else 0.0
        mu_e = (
            self.resend_mu
            if self.resend_mu is not None
            else min(self.params.mean_intensity / max(p_click, 1e-3), 50.0)
        )
        p = self.params
        self.hw.set(
            "EVE-TX-VOA",
            attenuation_db=float(
                np.clip(self.att0 - 10 * np.log10(mu_e / p.mu), 0.0, 80.0)
            ),
        )
        v_int = self.hw.call("EVE-TX-IM", "voltage_for", transmission)
        v_ph = self.hw.call("EVE-TX-PM", "voltage_for", phase)
        self.hw.call("EVE-TX-AWG", "set_pattern", "ch1", v_int, p.rep_rate)
        self.hw.call("EVE-TX-AWG", "set_pattern", "ch2", v_ph, p.rep_rate)
        t = self.totals
        t["blocks"] += 1
        t["pulses"] += attack.size
        t["intercepted"] += intercepted
        t["clicks"] += idx.size
        t["resent"] += idx.size
        print(
            f"block {batch.seq}: intercepted {intercepted:,} of {attack.size:,} pulses, "
            f"{idx.size:,} clicks (p {p_click:.3e}) → re-sent with μ_E = {mu_e:.2f}"
        )
        self.report(
            **{
                "intercepted": t["intercepted"] / t["pulses"] if t["pulses"] else 0.0,
                "clicks": t["clicks"],
                "p(click | intercepted)": (
                    t["clicks"] / t["intercepted"] if t["intercepted"] else 0.0
                ),
                "resend μ_E": mu_e,
            }
        )
