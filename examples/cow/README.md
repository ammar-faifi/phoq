# Coherent one-way QKD (COW), up to the sifted key

`examples/cow` runs the protocol of Stucki, Brunner, Gisin, Scarani & Zbinden, *Fast and simple
one-way quantum key distribution*, Appl. Phys. Lett. **87**, 194108 (2005), quant-ph/0506097, on
the same party framework. It stops at the sifted key, its QBER and the visibility. Error
correction, privacy amplification and the secret-key rate (the paper's Eq. 4, which needs a model
of Eve's information) are not implemented.

**The source is a CW laser.** Alice's intensity modulator carves the CW light into equally spaced
pulses of μ photons, or blocks a slot: bit 0 is (μ, 0), bit 1 is (0, μ) and a decoy (fraction f)
is (μ, μ). Because the laser runs continuously, every pair of non-empty pulses keeps a fixed
phase, inside a decoy and across a 1-0 bit boundary, and that coherence is what Bob's monitoring
line checks. A gain-switched laser restarts from spontaneous emission at every pulse, so its
pulses have random phases and the visibility is zero; the paper's footnote also allows a
mode-locked laser with a pulse picker. `COWParams(source="gain-switched")` swaps in such a laser
to show it.

**The bench.** The defaults follow the paper:

- Alice: CW 1550 nm DFB (1 MHz linewidth, assumed), intensity modulator (30 dB extinction) on an AWG
  running at twice the 434 MHz pulse clock, so each pulse fills the middle half of its 2.30 ns slot;
  a VOA calibrated to μ = 0.5 at the fiber input.
- 25 km of fiber, about 5 dB with the splices and connectors.
- Bob: a 50/50 tap (as in the paper's experiment). The data detector DB reads the bit from the time
  of arrival. The other half crosses a delay-line interferometer one slot (0.47 m) unbalanced. Its
  cords are standard: 1 m + 1 m in the long arm, 3 × 0.5 m in the short one, where an
  `OpticalDelayLine` takes back the 145 ps those cords give too much. A VOA in the long arm evens
  out the arms' losses, and the phase shifter locks DM1 to the constructive port.
- Detectors with η = 10 % and 2.5·10⁻⁵ dark counts per ns, as in the paper; no dead time (theirs
  was 10 µs against afterpulsing, which phoq does not model). The time tagger runs one bin per slot
  with 4 sub-bins to resolve the carved pulses, and its DB channel is delayed 12.4 ns to line up
  with the monitoring detectors.

**The protocol, per block** (the paper's steps 2–4):

1. Bob folds his tagger's time stamps onto Alice's slots. He announces which bits gave a data-line
   click, but not in which slot, and the slots where DM1 and DM2 fired.
2. Alice flags the detections that were decoys, which leave the key, and discloses her bits at a
   random 10 % of the rest. She counts DM1 and DM2 clicks where pulses j−1 and j were both sent:
   V_d inside decoys, V₁₋₀ across 1-0 boundaries. She measures each detector's dark-count
   probability where neither pulse was sent (a 0-1 boundary leaves two empty slots), which gives
   the paper's net visibility next to the raw one.
3. Bob answers with his bits at the sample. Both laptops show the same tally: sifted bits, sampled
   QBER, V raw, V net, V_d and V₁₋₀.

```bash
phoq run --protocol cow --blocks 20
```

```bash
phoq serve --protocol cow
```

The single-process reference `run()` keeps both records in one place, so it counts the QBER on the
whole sifted key and compares every figure with `theory()`. The theory computes per-pulse photon
numbers from the traced losses, click probabilities $1 − (1 − p_d)\cdot e^{−ηm}$, and the dark counts'
share of the raw visibility:

```python
from phoq.loading import import_source

cow = import_source("examples/cow")
print(cow.run(blocks=24, seed=5).summary())
```

```
COW over 25 km, cw source, μ = 0.5, f = 0.1, t_B = 0.5, 4,800,000 bits (9,600,000 slots at 434 MHz)
detected 6.566e-03 per bit (expected 6.549e-03) · sifted 25,733 (5.361e-03 per bit, expected 5.372e-03)
QBER 0.0119 on the whole sifted key (expected 0.0116)
V raw 0.9484 (expected 0.9471) · net 0.9920 (decoy 0.9923, 1-0 0.9941; expected 0.9928)
```

The net visibility is the coherence of neighboring pulses, $e^{-\pi\Delta\nu\tau}$ = 0.993 for
1 MHz over one slot. Dark counts on DM2 bring the raw value down to 0.947. With the gain-switched
source, the data line is unchanged (QBER 1.1 %) but V averages −0.003 ± 0.034 over 12 runs. The
paper measured raw V ≥ 92 % and net V ≈ 98 %. Its QBER was 5.2 %, but 4 % of that came from
detector noise and afterpulses and 1 % from slow modulator electronics, neither of which phoq
models; here the QBER is the dark counts plus the modulator's 30 dB leak.

## COW under attack: intercept-resend in the time basis

The monitoring line exists for attacks that leave the data line alone. `examples/cow/eve.py` is the
simplest one: Eve measures the time of arrival, which is the data line's own measurement, on a
fraction of the bits and re-prepares what she saw. She is a `--party` file:

```bash
phoq run --protocol cow --party examples/cow/eve.py --blocks 20
```

```bash
PHOQ_EVE_FRACTION=0.5 phoq serve --protocol cow --party examples/cow/eve.py
```

**Her station.** A party file cannot add hardware, so once an Eve party is loaded `build()` cuts
the 25 km spool in the middle and splices her station in (`build(eve=True)` does it by hand,
`build(eve=EveParams(...))` with other parts):

- a 2×2 `OpticalSwitch`. In the bar state Alice's light goes on to Bob; in the cross state it goes
  into Eve's detector, and her transmitter's light takes its place on the line;
- a time-of-arrival detector (η = 90 %, 100 Hz dark counts) on her own tagger, one bin per slot
  with 4 sub-bins like Bob's;
- a copy of Alice's transmitter: her own CW laser at Alice's wavelength (1 MHz linewidth, phase
  unrelated to Alice's), an intensity modulator with 40 dB extinction, and a VOA;
- one AWG on the clock she recovers from Alice's light (time base `laser`). CH2 throws the switch,
  CH1 carves her laser.

The AWG's channel delays do the timing, not fiber. CH2 is delayed by the 61.24 µs that Alice's
pulse 0 takes to reach the switch, so symbol k of the switch pattern takes Alice's pulse k. CH1 is
delayed so that Eve's pulse k passes the switch with Alice's pulse k, and so reaches DB, DM1 and DM2
in the same slot and sub-bins. Her VOA is calibrated by a loopback: in the bar state her
transmitter feeds her own detector, and she sets a full pulse there to μ photons. The μ she reads
off her click rate and the intensity she re-sends then refer to the same point, and the switch's
loss cancels because both routes have it. Every cord is a standard 1 m one.

**Her attack, per block.** She is a `stage = 1` party: the lab records her tagger first and waits
for her to program her transmitter before it records Bob's. She picks the bits to intercept
(`PHOQ_EVE_FRACTION`, default 1) and switches both of their slots to her detector, with her
transmitter dark. From her clicks she re-prepares (`intercept()`) bit 0 or bit 1 for a click in one
slot, a decoy for clicks in both (she then knows the sequence, as the paper assumes), and nothing
where her detector stayed dark (`PHOQ_EVE_INCONCLUSIVE=decoy` sends a decoy there instead). About
0.17 photons per pulse reach her detector, which clicks on q = 14 % of Alice's pulses, so she
re-sends rare, bright pulses matched in photon flux to what she took out: μ_E = 0.17/q ≈ 1.2 at
her detector (`resend_mu()`; `PHOQ_EVE_MU` sets it by hand). In the virtual lab her station sits in
the fiber and her laptop next to Alice's and Bob's, showing the fraction she is intercepting, her
click probability and μ_E, then the bits and decoys she has re-sent.

To take her off the line and put her back, select EVE-AWG and untick, then tick, **CH2 output**.
With its drive off the switch rests in the bar state, Alice's light passes untouched, and Eve
stands down until it returns. Alice's and Bob's laptops lead with running figures over the last
10 blocks (`QBER now`, `V_d now`, `V_1-0 now`; the totals follow), so the change shows within
about 30 s: in one run V_d went 0.09 → 0.95 → 0.22 and V₁₋₀ 0.08 → 1.00 → 0.12, while the
QBER stayed between 1.2 % and 2.4 %.

**What Alice and Bob see.** Each row is 40 blocks (8·10⁶ bits) of `run(eve=p_IR)`, with the expected
values from `theory()` and `eve_theory()` in brackets:

| p_IR | detected / bit [10⁻³] | QBER [%] | V_d (net) | V₁₋₀ (net) | paper, 1 − p_IR·ξ |
| --- | --- | --- | --- | --- | --- |
| 0, station idle | 3.84 (3.82) | 1.72 ± 0.08 (1.84) | 0.995 ± 0.003 (0.993) | 0.990 ± 0.003 (0.993) | 1 |
| 0.25 | 3.84 (3.82) | 1.81 ± 0.08 (1.88) | 0.767 ± 0.018 (0.783) | 0.602 ± 0.016 (0.572) | 0.769 |
| 0.5 | 3.82 (3.81) | 1.99 ± 0.09 (1.92) | 0.543 ± 0.023 (0.574) | 0.322 ± 0.019 (0.292) | 0.537 |
| 0.75 | 3.82 (3.80) | 2.03 ± 0.09 (1.97) | 0.312 ± 0.027 (0.365) | 0.150 ± 0.020 (0.153) | 0.306 |
| 1 | 3.81 (3.79) | 2.06 ± 0.09 (2.01) | 0.113 ± 0.029 (0.155) | 0.169 ± 0.020 (0.155) | 0.075 |
| 1, her modulator at 30 dB | 3.90 (3.87) | 3.32 ± 0.11 (3.19) | 0.152 ± 0.029 (0.192) | 0.214 ± 0.020 (0.192) | 0.075 |

The ± is one standard deviation of the counts: about 1000 DM clicks per row land inside decoys
and 2400 on 1-0 boundaries. Without the station (one 25 km spool) the same 40 blocks give
6.58·10⁻³ detections per bit and a QBER of 1.19 %.

The data line cannot tell. Bob's detection rate stays where it was with the station idle and his
QBER moves by a few tenths of a percent (her leak, below), because a time-of-arrival measurement
disturbs nothing the data line reads. The
visibilities fall with the intercepted fraction p_IR (the paper's notation; f stays the decoy
fraction). Coherence survives only where both neighboring pulses come from one
laser. Across a 1-0 boundary that means both bits were left alone, probability (1 − p_IR)², or
both were re-sent by Eve (p_IR²·q). Inside a decoy it means the decoy was left alone, or Eve
recognized it and re-sent a decoy (p_IR·q). Weighted by Bob's monitoring clicks,

```math
V_d = (1-p_{IR})\,V + p_{IR}\,c\,V_E, \qquad V_{1-0} = (1-p_{IR})^2\,V + p_{IR}^2\,c\,V_E, \qquad c = q + 2(1-q)\sqrt{\varepsilon_E},
```

with V = V_E = 0.993 the coherence of either laser over one slot. The √ε_E term is Eve's own
modulator leak: next to a pulse she re-sends alone, the empty slot still holds her laser's light
at √ε_E of the pulse's amplitude, coherent with it. That adds 0.017 at 40 dB extinction and
0.054 at 30 dB. (`eve_theory()` also keeps the switch's −40 dB crosstalk, which lets a diverted
pulse of Alice's interfere with an untouched neighbor: +0.005 at most.) V₁₋₀ falls faster than
V_d, because intercepting a fraction of the bits breaks every boundary between an intercepted bit
and one left alone. This is why the paper requires V₁₋₀ = V_d.

The paper estimates the damage as 1 − V = I·ξ. Here I = p_IR: with the flux matched, every detection
Bob gets in an attacked pair is a bit Eve re-sent and knows. ξ = 2e^{−μt}/(1 + e^{−μt}) is the
chance that Eve saw one pulse of a decoy and not the other; with e^{−μt} → 1 − q, ξ = 0.925. That
estimate counts decoy events, while Bob's clicks weight them by photons, and a decoy Eve
recognizes carries two pulses. The visibility therefore drops by p_IR·(1 − c)·V = 0.84 p_IR rather
than p_IR·ξ = 0.925 p_IR. The two agree for small q and a dark modulator; the table's last column
gives the paper's V.

**What she cannot hide on this bench.** Her station costs 2.4 dB on the bar route (the switch's
1 dB, four more connector pairs, one more splice). Bob's rate is therefore 2.4 dB below the no-Eve
value whether she attacks or not, and with fewer signal photons against the same dark counts the
QBER rises from 1.2 % to 1.8 %. A link sees that as channel loss, which COW's security analysis
gives to Eve anyway. The QBER her attack adds comes from her modulator's leak into the pairs she
leaves empty: her pulses are 2.4 times brighter, and so is their leak. With a 30 dB modulator like
Alice's (`EveParams(extinction_db=30)`) the QBER at p_IR = 1 is 3.3 %, the table's last row.

Sending a decoy when inconclusive (`PHOQ_EVE_INCONCLUSIVE=decoy`, 12 blocks at p_IR = 1) trades
errors for visibility. Her decoys are coherent, so the raw V_d stays at 0.77 and V₁₋₀ at 0.90, but
Bob's bit from one of her decoys is a coin toss and the QBER is 46 %. The net visibility stops
meaning anything as well: her decoys fill the slots where Alice sent nothing, the dark reference
rises 7.5-fold, and net V₁₋₀ comes out near 2.
