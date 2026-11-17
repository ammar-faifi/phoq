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

## Charlie's measurement: single-photon interference

Alice's and Bob's pulses meet on Charlie's 50:50 coupler, whose outputs feed DET-L and DET-R. For
every round Charlie announces whether exactly one detector clicked, and which one (`charlie.py`);
rounds with no click or two clicks are dropped. The paper calls this a single-photon interference
measurement (Box 1, step 2):

- **Single-photon.** Each arm loses 10.1 dB between the link input, where μ is set, and the
  coupler: 9.5 dB in the 50 km spool and 0.6 dB at the two matings of the patch cord after it. A
  μ = 0.4 pulse therefore reaches the coupler with 0.039 photons on average. A click almost always
  comes from one photon, and nothing in it says whose. The name describes the detection, not the
  sources, which send weak coherent pulses.
- **Interference.** Which detector clicks depends on Δ, Alice's phase minus Bob's at the coupler.
  With equal intensities the photon reaches DET-L with probability (1 + sin Δ)/2 and DET-R with
  (1 − sin Δ)/2: a sine, because the coupler's cross port adds π/2. A round in which only one party
  sent light splits 50/50. Δ holds both parties' random phases and the phase walk of the two
  free-running lasers, so a single round's L or R is a coin flip to everyone.

**Why single clicks.** Coincidence detection, as in time-bin MDI-QKD, needs a photon from each
party and so pays the loss of both arms, 20.2 dB at 100 km. A single click pays one arm's,
10.1 dB: that halving in dB is the √η scaling of twin-field QKD. Twin-field schemes, however, read
Δ within single rounds, so they need global phase locking.

**How MP-QKD avoids phase locking.** After Charlie's announcement, Alice and Bob pair two
single-click rounds i and j, at most `l` = 1000 rounds apart, and read only what differs between
them:

- **Z pairs**, (0, μ) or (μ, 0): the bit is which round carried the light. Which detector clicked
  does not matter, so interference plays no part. These pairs give the key.
- **X pairs**, (μ, μ): each party's bit comes from its own relative phase φ_j − φ_i, and both keep
  only the pairs whose θ = (φ_j − φ_i) mod π match. For one photon from each party, the same
  detector in both rounds means Alice's and Bob's relative phases agree; different detectors mean
  they differ by π, and Bob flips his bit. These pairs check e₁₁ˣ, the phase-error rate that sizes
  privacy amplification (below).

The phase walk enters both rounds of a pair and cancels, except for how far it went between them.
Two single-click rounds thus do the job of one coincidence (the abstract's "conventional
second-order interference"), and the lasers only have to hold their relative phase across the
pairing interval. With the bench's 3 kHz lasers at 625 MHz, the phase walk gives a pair 1000 rounds
(1.6 µs) apart a phase-error rate of 1.5 %. In the 20-block run of
[post_processing.md](post_processing.md), clicks came about every 100 rounds, key pairs were about
170 ns apart, and e₁₁ˣ came out at 0.16 %.

**Why double clicks are dropped.** A double click helps neither the X pairs nor the key:

- **No X bit.** In an X pair, the detectors tell Bob whether his relative phase agrees with
  Alice's or differs by π. The two cases differ by a π shift of Δ in one round, and that shift
  swaps the coupler's two outputs. With matched detectors, both firing is as likely after the swap
  as before, so a double click cannot tell the cases apart, and Bob would be wrong half the time.
- **No credited key bits.** One photon fires only one detector, so a double click needs two
  photons in one round, or a dark count or afterpulse in the second detector. Privacy
  amplification credits only the share q₁₁ of pairs in which Alice and Bob each sent one photon.
  With a click in each round, such a pair holds one photon per round, so light alone never gives
  it a double click. Key bits from double clicks would cost error-correction parities, then be
  removed by privacy amplification.

Dropping them costs little: a double click needs two photons to cross the fiber in one round, so
it scales with η, like a coincidence, where a single click scales with √η. The `run()` above with
2 × 10⁷ rounds counts about 750 double clicks against 193,000 single clicks, 0.39 % of detections.
Nor does the rule open a loophole, because Charlie is untrusted: the security proof already lets
him announce anything, and Alice and Bob bound q₁₁ and check e₁₁ˣ on the rounds they keep. A BB84
receiver, whose detectors are trusted, cannot simply drop double clicks; the usual rule gives each
one a random bit.

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
