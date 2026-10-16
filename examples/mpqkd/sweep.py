"""Secret-key rate versus fiber length: the three-party showcase run headless at each distance,
next to the theory curve of the MP-QKD paper evaluated on this lab's parameters.

    uv run python examples/mpqkd/sweep.py
    uv run python examples/mpqkd/sweep.py --distances 50 100 150 200 --blocks 40 --plot sweep.png

Each point runs Alice, Bob and Charlie as processes (``phoq.parties.Experiment``) for ``--blocks``
blocks of ``--rounds`` pulses and reads the stations' read-outs. Three rates are shown:

* ``final``   — the key both stations actually distilled, final bits / pulses. It comes in chunks
  (``chunk_bits`` sifted bits per privacy-amplification step), so with few blocks it is quantised
  and at long distances a chunk may not fill before the run ends.
* ``formula`` — the stations' asymptotic estimate on the same run: key pairs per pulse × secret
  fraction (q_11 from the decoy bound on the run's own counts, e_11 from the model) minus the
  measured Cascade leak.
* ``theory``  — Eq. (D1) of the paper with the ideal single-photon-pair quantities, and in
  brackets the same with q_11 replaced by the decoy bound the stations use (its asymptotic limit).

Whatever disagrees is printed as a note, not tuned away.
"""

from __future__ import annotations

import argparse
import math
import time
from pathlib import Path

import numpy as np

from phoq.parties import Experiment, load_protocol

mpqkd = load_protocol(
    Path(__file__).resolve().parent
)  # the parties' processes re-import it the same way
MPQKDParams, build, theory, arm_transmittance = (
    mpqkd.MPQKDParams,
    mpqkd.build,
    mpqkd.theory,
    mpqkd.arm_transmittance,
)


def run_point(distance: float, blocks: int, rounds: int, seed: int) -> dict:
    p = MPQKDParams(distance=distance)
    setup = build(p, rounds)
    t0 = time.monotonic()
    alice, charlie, bob = Experiment(setup, mpqkd.PARTIES, seed=seed).run(
        blocks, echo=False
    )
    a, b = alice.result or {}, bob.result or {}
    eta = arm_transmittance(setup, "ALICE", p)
    th = theory(p, eta, distance)
    traceback = any("Traceback" in l for w in (alice, bob, charlie) for l in w.lines)
    return dict(
        distance=distance,
        eta=eta,
        seconds=time.monotonic() - t0,
        pulses=blocks * rounds,
        pairs_per_round=a.get("pairs / round", 0.0),
        q11=a.get("q_11 (decoy lower bound)"),
        e11=next(
            (
                v
                for k, v in a.items()
                if k.startswith("e_11 (") and "decoy bound" not in k
            ),
            None,
        ),
        e11_decoy=a.get("e_11 (decoy bound)"),
        e_x=a.get("E_X (μμ)"),
        e_x_model=a.get("E_X model"),
        z_decoy=a.get("Z-decoy (ν,ν) pairs", 0),
        sifted=a.get("sifted bits", 0),
        corrected=a.get("corrected bits", 0),
        leak=a.get("leak EC [bits]", 0),
        final=a.get("final key bits", 0),
        final_bob=b.get("final key bits", 0),
        keys_match=bool(a.get("final key sha256"))
        and a.get("final key sha256") == b.get("final key sha256"),
        rate_final=a.get("key rate [bit/pulse]", 0.0),
        rate_formula=a.get("rate, asymptotic formula [bit/pulse]", 0.0),
        theory=th,
        traceback=traceback,
    )


def fmt(v, spec=".3g"):
    return (
        "—"
        if v is None or (isinstance(v, float) and math.isnan(v))
        else format(v, spec)
    )


def main(argv=None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--distances", type=float, nargs="+", default=[50, 100, 150, 200], metavar="KM"
    )
    ap.add_argument("--blocks", type=int, default=20)
    ap.add_argument("--rounds", type=int, default=200_000, help="pulses per block")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument(
        "--plot",
        metavar="PNG",
        help="save a rate-versus-distance plot (needs matplotlib)",
    )
    args = ap.parse_args(argv)

    print(
        f"MP-QKD key rate versus fiber length — {args.blocks} blocks × {args.rounds:,} pulses per point, seed {args.seed}\n"
        f"{'L [km]':>7} {'η_arm':>8} {'pairs/pulse':>18} {'q11 sim/th':>13} {'e11 sim/th':>13} {'E_X sim/th':>12} "
        f"{'sifted':>7} {'final':>6} {'R final':>9} {'R formula':>10} {'R theory (bounds)':>22} {'formula/theory':>14}"
    )
    rows, notes = [], []
    for km in args.distances:
        r = run_point(km * 1e3, args.blocks, args.rounds, args.seed)
        rows.append(r)
        th = r["theory"]
        ratio = (
            r["rate_formula"] / th.rate_bounds if th.rate_bounds > 0 else float("nan")
        )
        print(
            f"{km:7.0f} {r['eta']:8.2e} {fmt(r['pairs_per_round'], '.3e'):>8}/{th.pairs_per_round:.3e} "
            f"{fmt(r['q11'], '.3f'):>6}/{th.q11_lower:.3f} {fmt(r['e11'], '.4f'):>7}/{th.e11:.4f} "
            f"{fmt(r['e_x'], '.3f'):>5}/{th.e_x:.3f} {r['sifted']:7d} {r['final']:6d} {r['rate_final']:9.2e} "
            f"{r['rate_formula']:10.2e} {th.rate:10.2e} ({th.rate_bounds:.2e}) {fmt(ratio, '.2f'):>14}"
            + ("" if r["keys_match"] or not r["final"] else "   KEYS DIFFER")
            + ("   TRACEBACK" if r["traceback"] else ""),
            flush=True,
        )
        if r["final"] and not r["keys_match"]:
            notes.append(f"{km:.0f} km: Alice's and Bob's final keys differ")
        if r["traceback"]:
            notes.append(f"{km:.0f} km: a party raised an exception (see its console)")
        if r["final"] == 0 and r["rate_formula"] > 0:
            notes.append(
                f"{km:.0f} km: positive asymptotic rate but no chunk of {MPQKDParams().chunk_bits} sifted bits "
                f"completed in {args.blocks} blocks ({r['sifted']} sifted) — run more blocks"
            )
        if th.rate_bounds > 0 and math.isfinite(ratio) and abs(ratio - 1) > 0.35:
            notes.append(
                f"{km:.0f} km: the run's formula rate is {ratio:.2f}× the theory curve — the q₁₁ bound rests on "
                f"{r['z_decoy']} (ν,ν) Z pairs (q₁₁ {fmt(r['q11'], '.3f')} vs {th.q11_lower:.3f} asymptotically)"
            )
        if (
            r["e_x"] is not None
            and r["e_x_model"] is not None
            and abs(r["e_x"] - r["e_x_model"]) > 0.06
        ):
            notes.append(
                f"{km:.0f} km: E_X measured {r['e_x']:.3f} vs model {r['e_x_model']:.3f}"
            )
        if not isinstance(r["e11_decoy"], float) or not math.isfinite(r["e11_decoy"]):
            notes.append(
                f"{km:.0f} km: the rigorous decoy bound on e_11 is unresolved at {r['pulses']:.1e} pulses (expected, see keyrate.py)"
            )
    print("\nnotes:" if notes else "\nno discrepancies beyond the expected statistics")
    for n in notes:
        print("  -", n)

    if args.plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from phoq.units import CONNECTOR_LOSS_DB, db_to_t, fiber_attenuation

        p = MPQKDParams()
        setup = build(p)
        wl = setup["ALICE-LD"].wavelength
        fixed_db = (
            setup["ALICE-SPL"].splice_loss_db
            + 4 * CONNECTOR_LOSS_DB
            + setup["CHARLIE"].excess_loss_db
        )
        grid = np.linspace(10, max(args.distances) * 1.3, 60)
        eta = (
            db_to_t(fiber_attenuation(wl) * grid / 2 + fixed_db)
            * setup["DET-L"].efficiency
        )
        th = [theory(p, e, km * 1e3) for e, km in zip(eta, grid)]
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        ax.semilogy(
            grid, [t.rate for t in th], "-", label="theory, Eq. (D1) ideal (1,1)"
        )
        ax.semilogy(
            grid,
            [t.rate_bounds for t in th],
            "--",
            label="theory with the decoy bound on q₁₁",
        )
        km = [r["distance"] / 1e3 for r in rows]
        ax.semilogy(
            km,
            [max(r["rate_formula"], 1e-12) for r in rows],
            "o",
            label="stations' asymptotic estimate",
        )
        ax.semilogy(
            km,
            [max(r["rate_final"], 1e-12) for r in rows],
            "s",
            label="final key bits / pulse",
        )
        ax.set(
            xlabel="Alice–Bob distance [km]",
            ylabel="secret key rate [bit / pulse]",
            ylim=(1e-8, 1e-2),
            title=f"MP-QKD showcase, {args.blocks} × {args.rounds:,} pulses per point",
        )
        ax.grid(True, which="both", alpha=0.3)
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(args.plot, dpi=140)
        print(f"saved {args.plot}")
    return rows


if __name__ == "__main__":
    main()
