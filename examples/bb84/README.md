# BB84 with decoy states, and an eavesdropper

`examples/bb84` is the second protocol on the same party framework — two parties, no relay —
and shows that the framework is protocol-agnostic. It is phase-coding BB84 with weak coherent
pulses and three decoy intensities: Alice's pulsed laser → intensity modulator (μ, ν or vacuum
per pulse) → unbalanced Mach–Zehnder interferometer with a phase modulator in the long arm
(φ ∈ {0, π} for Z, {π/2, 3π/2} for X between the early and the late time slot) → VOA calibrated to
μ → fiber spool → Bob's matching interferometer (phase shifter for the stabilization, phase
modulator for θ = 0 or π/2) → two SPDs → time tagger with three bins per pulse. In the middle of
the three output slots the late half of the early slot interferes with the early half of the late
slot: DET-0 clicks for φ − θ = 0, DET-1 for π. Bob folds his tagger's time stamps onto Alice's
pulses and keeps those in the middle slot; the satellites on either side are ignored. `build()`
calibrates μ at the fiber input and the interferometer phase, then adds the residual phase error of
an imperfectly stabilized interferometer (0.2 rad ≈ 1 % QBER).

```bash
phoq run --protocol bb84 --blocks 20
```

```bash
phoq serve --protocol bb84
```

Per block Bob announces which pulses clicked and his basis string; Alice keeps the detections
whose bases match, discloses every detection's intensity label, all her X-basis bits and a 10 %
sample of her Z-μ bits; Bob answers with his bits at those positions. From these public counts
both consoles and result dicts report the QBER per basis, the decoy-state bounds on the vacuum
and single-photon yields Y₀, Y₁ and on the single-photon error e₁ (Ma, Qi, Zhao & Lo 2005, with
the vacuum decoy's true leak through the intensity modulator), the single-photon gain Q₁ and the
asymptotic GLLP key rate in secret bits per laser pulse:
R = (N_{μ,Z}/N)·[Q₁(1 − H₂(e₁)) − Q_μ f H₂(E_μ)]. The Z-μ detections minus the sample are the
raw key; error correction and privacy amplification are not implemented.

```python
from phoq.loading import import_source

bb84 = import_source("examples/bb84")
print(bb84.run(bb84.BB84Params(distance=20e3, mu=0.5, nu=0.1), pulses=300_000, seed=1).summary())
```

Eve is a `--party` file: `phoq run --protocol bb84 --party examples/bb84/eve.py`. A party file
cannot add hardware, so when an Eve party has been loaded `build()` cuts the fiber at
`eve_position` and splices in her station (`build(eve=True)` does the same by hand): a 2×2
`OpticalSwitch` whose bar state lets Alice's pulse pass and whose cross state sends it into Eve's
receiver — a copy of Bob's — while her own transmitter's pulse, delayed to arrive in the same
time slot, takes its place. She throws the switch for a random fraction of the pulses
(`PHOQ_EVE_FRACTION`, default 1), measures them in a random basis and re-sends what she found,
brighter than Alice to hide the loss of her own detection (`PHOQ_EVE_MU` overrides). She is a
`stage = 1` party, so the lab records her tagger before Bob's and lets her program her
transmitter from the clicks within the block. With everything intercepted the QBER rises to about
25 %, e₁ reaches ½ and the key rate drops to zero; the decoy bounds also register her
multi-photon re-sends. In the virtual lab her station appears in the fiber and her laptop next to
Alice's and Bob's.
