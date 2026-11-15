# Mode-pairing QKD (MP-QKD)

Mode-pairing QKD after Zeng, Zhou, Wu & Ma, *Mode-pairing quantum key distribution*, Nat.
Commun. 13, 3903 (2022), on phoq's party framework, from the clicks to a secret key.

`examples/mpqkd` builds the MP-QKD hardware: two lasers → intensity and phase
modulators (each station's AWG drives them with vacuum / ν / μ and one of D phases) → VOAs
calibrated to μ → fiber spools → Charlie's coupler → two SPDs → time tagger. The parties
(`alice.py`, `bob.py`, `charlie.py`) run the whole protocol on the simulated clicks — pairing,
sifting, parameter estimation, Cascade, verification and Toeplitz privacy amplification sized by
the MP-QKD key-rate formula (`keyrate.py`, `postprocess.py`) — and `sweep.py` plots the key rate
against fiber length next to the paper's theory curve. The single-process reference `run()`
stops after sifting:

```python
from phoq.loading import import_source

mpqkd = import_source("examples/mpqkd")       # the showcase package
print(mpqkd.run(mpqkd.MPQKDParams(distance=100e3, mu=0.4), rounds=2_000_000, seed=1).summary())
```

## Parties and the classical channel

phoq's [parties framework](../../docs/README.md#parties-and-the-classical-channel) keeps the
physical layer, each station's link to its own instruments and the classical channel apart,
following the `[CLASSICAL #N]` annotations in `reference.py`.
`examples/mpqkd` is split accordingly — `common.py` (public parameters, the
hardware, the pairing/sifting math), `station.py` (what Alice and Bob share), `alice.py`,
`bob.py`, `charlie.py` — and the annotations map onto messages:

| `examples/mpqkd/reference.py` | Message | Who |
| --- | --- | --- |
| #1 announce C_i and L/R | `clicks` (single-click rounds, detector, block) | Charlie → all |
| #2 pairing with public l | no message: both stations run the same `simple_pairing` on #1 | — |
| #3 basis announcement | `basis` (one label per pair and how bright it was — vacuum, ν or μ — never which pulse) | Alice ↔ Bob |
| #4 alignment angle | `theta` (per pair) | Alice ↔ Bob |
| #5 parameter estimation | `xbits` (all sifted X bits, for E_X). No key bit is disclosed: E_Z is the error rate Cascade corrects (#6) | Alice ↔ Bob |
| #6 error correction, verification | `ec-start` / `ec-query` / `ec-parity` / `ec-done`: Cascade on chunks of 1 000 sifted bits, its blocks sized from the error rate corrected in the chunks before (1 % for the first), every parity Alice discloses counted as leaked; `verified`: a 32-bit universal-hash tag of Bob's corrected chunk matched Alice's | Alice ↔ Bob |
| #6 privacy amplification | `pa`: Toeplitz seed and output length — corrected bits × q₁₁ [1 − h(e₁₁)] minus the leaked bits, from the MP-QKD key-rate formula (`keyrate.py`) | Alice → Bob |

Each station's console shows every block's pairs and sifted bits, each chunk's Cascade run
(errors corrected, parities leaked, tag check) and every privacy-amplification step with the
final key in hex — the same hex on both laptops. The read-outs give sifted, corrected and final
key bits, the key rate per pulse, the leak, q₁₁, e₁₁, E_Z, E_X and the key's fingerprint.

**How the key is sized.** The paper's rate is `R = r_p r_s {q₁₁ [1 − h(e₁₁ˣ)] − f h(E_Z)}`. Pulses
take one of three intensities (vacuum, decoy ν, signal μ); the key comes from Z pairs whose
bright pulses were μ, every other pair class only feeds the decoy statistics. Both stations tally
the public class of every pair (basis, intensity level, θ match), so they compute the same
numbers: q₁₁ from the vacuum + weak decoy lower bound on the single-photon-pair yield of the Z-type
classes (Appendix C of the paper, the standard MDI two-decoy formula), and e₁₁ˣ — by default —
from the honest-channel model (laser linewidths and detuning read from the stations' own lasers,
dark counts, the observed pairing intervals; Eq. (D15)), checked against the measured E_X of the
signal X pairs. The rigorous decoy bound on e₁₁ˣ is also computed and shown as *unresolved*: it
is a small difference of X-basis error gains of the weakest decoy class and needs ~10⁹–10¹²
pulses (what experiments collect, and why the paper's curves are asymptotic; a 20-block run has
4 × 10⁶), and at this showcase's X intensities (2ν = 0.2, 2μ = 0.8 per pair) the two-intensity
bound on the X single-photon-pair yield does not close even asymptotically — the paper's own
simulation takes e₁₁ˣ from the model too (its Eq. D15). `MPQKDParams(estimator="decoy")` sizes
with the rigorous bound instead and then distils no key here. All sizing is asymptotic; a
simplified finite-key length (Chernoff/Hoeffding terms, ε = 10⁻¹⁰) is reported next to it,
clearly labeled, and is zero at this scale.

[post_processing.md](post_processing.md) walks through each step after
sifting, with the functions, messages and equations it uses and the numbers of a 20-block run. Its
Cascade is checked against Mueller et al. (2025) in the main README, under
[Checked against the literature](../../README.md#checked-against-the-literature).

Run it with the three parties as processes, headless or live:

```bash
phoq run --protocol mpqkd --blocks 20
```

```bash
uv run phoq run examples/mpqkd/sweep.py --plot sweep.png   # key rate vs fiber length, against the theory curve
```

```bash
phoq serve --protocol mpqkd
```
