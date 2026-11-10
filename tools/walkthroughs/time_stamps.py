"""Data for the time-stamp walkthrough: COW vacuum's Bob.measure reading one block of his
tagger through read_tagger, CounterResult.timestamps, slot_clicks and phoq.timing's
clicked_slots and fold, then events, replayed line by line on a bright COW-vacuum link,
every intermediate array kept. Alice's carving and the AWG's pattern lookup show where a
slot's center sits. The replay is checked against read_tagger() and events() themselves.
"""

import copy
from pathlib import Path

import numpy as np

from phoq.loading import import_source
from phoq.runner import Runner

ROOT = Path(__file__).resolve().parents[2]
SEED = 3
SLOTS = 20_000  # one block: 20 µs, a couple of hundred clicks to draw
BLOCK = 1  # the second block, so that its offset shows in start
REFS = {
    "measure": "examples/cow_vacuum:bob.Bob.measure",
    "read_tagger": "examples/cow_vacuum:common.read_tagger",
    "timestamps": "phoq.instruments:CounterResult.timestamps",
    "slot_clicks": "examples/cow_vacuum:common.slot_clicks",
    "clicked_slots": "phoq.timing:clicked_slots",
    "fold": "phoq.timing:fold",
    "events": "examples/cow_vacuum:common.events",
    "carving": "examples/cow_vacuum:common.carving",
    "_shape": "phoq.generators:ArbitraryWaveformGenerator._shape",
}
STATES = ("bit 0", "bit 1", "decoy 1", "decoy 2")


def capture() -> dict:
    c = import_source(ROOT / "examples" / "cow_vacuum").common
    p = c.COWParams(mu=0.5, dead_time=0.0)  # the tests' bright setup
    setup = c.build(p, SLOTS)
    runner = Runner(setup, seed=SEED)
    im, awg, tagger, det = (setup[n] for n in ("ALICE-IM", "ALICE-AWG", "TAGGER", "DT"))
    # as run() does: Alice draws and sends a block, the tagger records it, Bob reads it
    for block in range(BLOCK + 1):
        kind = c.draw(runner.rng, tagger.slots // c.BINS_PER_PAIR, p)
        awg.set_pattern("ch1", im.voltage_for(c.carving(c.occupied(kind))), 2 * p.rate)
        batch = runner.acquire("TAGGER")
        batch.seq = block  # the lab's session numbers each batch by its block
        clicked = c.read_tagger(batch, batch.seq, p)
        state = copy.deepcopy(runner.rng.bit_generator.state)
        event, multi = c.events(clicked, runner.rng)

    # Bob.measure: read_tagger(batch, batch.seq, self.params), t0 = 0
    t0 = 0.0
    slots = round(batch.duration / p.slot)
    start = t0 + batch.seq * slots * p.slot
    tau = p.slot
    flags, ch_data = {}, {}
    for ch in (c.DT, c.DM0, c.DM1):
        # CounterResult.timestamps: no jitter and no sub-bins on this bench, so no
        # times inside the bins, as the walk says
        assert ch not in batch.fine
        k = np.flatnonzero(batch.clicks[ch])
        t = (batch.start + k) / batch.rate
        # slot_clicks
        window = (-tau / 2, 0.0) if p.click_window else None
        t0_fold, period = start + tau / 4, tau
        # clicked_slots → fold
        x = (np.asarray(t, dtype=float) - t0_fold) / period
        nf = np.floor(x + 0.5)
        n, phase = nf.astype(np.int64), (x - nf) * period
        # clicked_slots, the cut on φ and the flags
        inside = (phase >= window[0]) & (phase < window[1])
        kept = n[inside]
        out = np.zeros(slots, dtype=bool)
        out[kept[(kept >= 0) & (kept < slots)]] = True
        flags[ch] = out
        ch_data[ch] = dict(
            k=[int(v) for v in k],
            n=[int(v) for v in n],
            phi=[round(float(v) * 1e12, 3) for v in phase],
            kept=int(inside.sum()),
            outside=int(np.count_nonzero((kept < 0) | (kept >= slots))),
        )
    for ch, ref in clicked.items():
        assert np.array_equal(flags[ch], ref), "replay differs from read_tagger()"

    # events
    w = np.stack(
        [flags[c.DT][1::2], flags[c.DT][0::2], flags[c.DM0][1::2], flags[c.DM1][1::2]]
    )
    nsum = w.sum(axis=0)
    ev = np.full(nsum.size, c.NONE, dtype=np.int8)
    one, many = np.flatnonzero(nsum == 1), np.flatnonzero(nsum > 1)
    ev[one] = np.argmax(w[:, one], axis=0)
    rng = np.random.default_rng()
    rng.bit_generator.state = state
    ev[many] = np.argmax(w[:, many] * rng.random((w.shape[0], many.size)), axis=0)
    assert (
        np.array_equal(ev, event) and many.size == multi
    ), "replay differs from events()"

    # back in Bob.measure
    idx, bits, mon, det_of = c.announcements(event)
    edge0, edge1 = c.boundary_clicks(clicked, event)

    # D_T's first stamp, followed through
    first = ch_data[c.DT]
    n0 = first["n"][0]
    pair = n0 // 2
    lo = max(0, pair - 4)
    return {
        "nums": {
            "seed": SEED,
            "block": batch.seq,
            "tau": tau,
            "rate": p.rate,
            "bin_rate": batch.rate,
            "awg_rate": awg.ch1_freq,
            "bins": batch.slots,
            "batch_start": batch.start,
            "duration": batch.duration,
            "slots": slots,
            "pairs": int(event.size),
            "t0": t0,
            "start": start,
            "fold_t0": start + tau / 4,
            "window": [window[0], window[1]],
            "mu": p.mu,
            "distance": p.distance,
            "eff": det.efficiency,
            "dcr": det.dark_count_rate,
            "flagged": {ch: int(f.sum()) for ch, f in flags.items()},
            "ev": {
                name: int(np.count_nonzero(event == v))
                for name, v in (
                    ("NONE", c.NONE),
                    ("T0", c.T0),
                    ("T1", c.T1),
                    ("M0", c.M0),
                    ("M1", c.M1),
                )
            },
            "multi": int(multi),
            "ann": {
                "idx": int(idx.size),
                "mon": int(mon.size),
                "m0": int(np.count_nonzero(det_of == 0)),
                "m1": int(np.count_nonzero(det_of == 1)),
                "edge0": int(edge0.size),
                "edge1": int(edge1.size),
            },
        },
        "ch": ch_data,
        "worked": {
            "bin": int(batch.start + first["k"][0]),
            "t": float((batch.start + first["k"][0]) / batch.rate),
            "x": float(
                ((batch.start + first["k"][0]) / batch.rate - (start + tau / 4)) / tau
            ),
            "n": n0,
            "phi": first["phi"][0],
            "pair": pair,
            "sent": STATES[int(kind[pair])],
            "bit": int(bits[np.searchsorted(idx, pair)]) if pair in idx else None,
        },
        "strip": {
            "first": lo,
            "kind": [STATES[int(s)] for s in kind[lo : pair + 5]],
            "event": [int(e) for e in event[lo : pair + 5]],
        },
    }
