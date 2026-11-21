# phoq examples

Every example runs headless with `phoq run` and opens in the virtual lab with `phoq serve`:

```bash
phoq run examples/laser_noise.py --plot noise.png
```

```bash
phoq serve examples/connector_cascade.py
```

Most single-file examples are described where the part or instrument they exercise is, in
[docs/README.md](../docs/README.md). This README lists them all, and has the rest: the QKD
protocols, the delay-line interferometer, clock synchronization and the QRNG.

## QKD protocols

Four protocols run on phoq's [parties framework](../docs/README.md#parties-and-the-classical-channel),
each party in its own process. Each directory has a README:

- [mpqkd](mpqkd/README.md): mode-pairing QKD with Alice, Bob and Charlie, through Cascade and
  privacy amplification to a secret key. [post_processing.md](mpqkd/post_processing.md)
  explains every step after sifting.
- [bb84](bb84/README.md): phase-coding BB84 with three decoy intensities, and an
  intercept-resend eavesdropper.
- [cow](cow/README.md): coherent one-way QKD up to the sifted key, and an intercept-resend
  attack in the time basis.
- [cow_vacuum](cow_vacuum/README.md): COW with a vacuum decoy sequence, up to the bound on the
  phase error rate.

**How the stations read their taggers.** AWG patterns are indexed by pulse number.
`PhotonCounter(compensate_delay=True)` removes the time of flight, so `clicks[k]` belongs to pulse
`k` of the patterns; `chN_delay` lines up a channel whose detector sits behind a different length of
fiber. A bench has no such record: its tagger hands the station's software a time stamp per click,
and the software folds the stamps onto Alice's slots, t = t₀ + nτ + φ, with `phoq.timing.fold`.
Every station here reads its tagger that way, Eve's included, and no protocol reads `clicks`: each
protocol's `read_tagger` marks the slots holding a stamp whose phase φ falls in the station's click
window (`phoq.timing.clicked_slots`). The stamps are still partly the simulator's. BB84, COW and
MP-QKD leave their detectors' `timing_jitter` at 0, so a click is stamped at the center of its bin,
or, where the tagger has sub-bins as COW's does, at the place in the bin where its photon arrived.
COW with vacuum decoys uses the PDM-IR preset, whose 200 ps FWHM of jitter blurs each stamp. And the
delay compensation puts Alice's first pulse at t₀ = 0 on the tagger's clock, so no station has to
find t₀, which a bench does once per session from its clicks or a sync channel. The lab's Help (?)
window walks through COW vacuum's reading line by line, under "Time stamps".

## Other examples

| Example | What it shows | Details |
| --- | --- | --- |
| [basic_link.py](basic_link.py) | Laser → patch cord → photodiode → oscilloscope | [Quick start](../README.md#quick-start) |
| [delay_line.py](delay_line.py) | A coupler splits a pulse train; one arm runs through a 1 km spool | — |
| [modulated_link.py](modulated_link.py) | An intensity modulator at quadrature, driven by the generator's 10 MHz sine, on the scope | [Driving modulators](../docs/README.md#driving-modulators) |
| [connector_cascade.py](connector_cascade.py) | Patch cords cascaded through mating sleeves: what every connection costs | [Fiber connectors](../docs/README.md#fiber-connectors) |
| [mzi_interference.py](mzi_interference.py) | A fiber Mach–Zehnder: the phase shifter swept, both output ports recorded | — |
| [iq_modulator.py](iq_modulator.py) | 16-QAM from a dual-parallel MZM, read back by a homodyne receiver | [IQ modulator](../docs/README.md#iq-modulator) |
| [odl_validation.py](odl_validation.py) | The optical delay line checked against its theory | [Optical delay line](../docs/README.md#optical-delay-line) |
| [dli.py](dli.py), [dli_for_dpsk.py](dli_for_dpsk.py), [dli_for_cow.py](dli_for_cow.py) | A delay-line interferometer as a DPSK demodulator and as COW's monitoring line | [below](#delay-line-interferometer-dpsk-and-the-cow-monitoring-line) |
| [balanced_detection.py](balanced_detection.py) | A weak heterodyne beat on a noisy local oscillator | [Balanced photodetector](../docs/README.md#balanced-photodetector) |
| [laser_noise.py](laser_noise.py), [laser_noise.md](laser_noise.md) | Intensity noise and shot noise, with a balanced detector and the spectrum analyzer | [Laser intensity noise and shot noise](../docs/README.md#laser-intensity-noise-and-shot-noise) |
| [linewidth_homodyne.py](linewidth_homodyne.py), [linewidth_narrow.py](linewidth_narrow.py) | A laser's linewidth by delayed self-homodyne, down to 100 Hz | [Laser linewidth by delayed self-homodyne](../docs/README.md#laser-linewidth-by-delayed-self-homodyne) |
| [spad_comb.py](spad_comb.py), [spad_tones.py](spad_tones.py) | A SPAD's clicks on the spectrum analyzer: the comb up to 1.25 GHz, and the clock's tone | — |
| [vacuum_noise_qrng.py](vacuum_noise_qrng.py) | A quantum random number generator from vacuum fluctuations, digitized by the scope | [below](#quantum-random-numbers-from-vacuum-fluctuations) |
| [clock_sync.py](clock_sync.py) | Alice's clock and the delay recovered from single-photon time stamps | [below](#clock-and-frame-synchronization-from-time-stamps) |
| [clock_sync_dli.py](clock_sync_dli.py) | The same at 1 GHz on three SPADs: a direct line, and a DLI's bright and dark lines, each with its own pattern | [below](#three-spads-behind-a-dli-at-1-ghz) |
| [analysis_pairing.py](analysis_pairing.py) | An analysis script on Charlie's clicks: the mode-pairing rate against Eq. (4) of the paper | [Post-processing clicks with your own script](../docs/README.md#post-processing-clicks-with-your-own-script) |

## Delay-line interferometer: DPSK and the COW monitoring line

A delay-line interferometer (DLI) is an unbalanced Mach–Zehnder whose long arm is exactly one pulse
period $T$ longer. It is built from two `Coupler`s and standard cords: 0.5 m + 0.5 m around a
`PhaseShifter` (the interferometer phase $\theta$) in the short arm, 1 m + 2 m around an
`OpticalDelayLine` in the long arm. The cords give 2 m (9.795 ns), and the delay line adds the last
205 ps (100 ps insertion delay and 105 ps on the dial; see [Optical delay line](../docs/README.md#optical-delay-line)). Each output slot then
holds pulse $k$ on top of pulse $k-1$, so the constructive port receives
$\tfrac12[1 + V\cos(\varphi_k - \varphi_{k-1} + \theta)]$ of the light and the destructive port the
rest. Here $V = e^{-\pi\Delta\nu T}$ is the coherence between neighboring pulses. This is the
demodulator of differential phase-shift keying (DPSK) and the monitoring line of coherent one-way
(COW) QKD, which checks that neighboring non-empty pulses keep their phase relation: an attack
that measures and re-prepares pulses breaks it, and the visibility drops.

The interferometer lives in `examples/dli.py`, a helper module, and two benches use it, each
with its own `build()` so `phoq serve` opens it on its own.

**DPSK demodulator: `examples/dli_for_dpsk.py`.** A 100 MHz, 1 ns pulse train and a phase modulator
writing $\varphi_k = \varphi_{k-1} + \pi b_k$, with the DLI and photodiodes on both ports. This is
the receiver of DPS-QKD:

```bash
phoq run examples/dli_for_dpsk.py --plot dpsk.png
```

```
DLI: cords 3 m (long arm) − 1 m (short arm) = 9.79 ns; delay line 100 ps + 105 ps on the dial → one slot T = 10 ns

DPSK demodulation (photodiodes, one 2 µs scope sweep)
   linewidth  100 kHz: 180 slots decoded, 0 errors, mean contrast +0.991  (e^(−πΔνT) = 0.997)
   linewidth   10 MHz: 180 slots decoded, 6 errors, mean contrast +0.731  (e^(−πΔνT) = 0.730)
   linewidth  100 MHz: 180 slots decoded, 82 errors, mean contrast +0.025  (e^(−πΔνT) = 0.043)
```

`phoq serve examples/dli_for_dpsk.py` opens this bench. The scope shows the constructive port
(CH1), the destructive port (CH2) and the bits (CH3, the generator's second channel delayed into
the decoding slot); every "1" leaves by the destructive port. In the inspector, turn `DLI-PS`'s
phase to swap the ports, raise `LD-1`'s linewidth to 10 or 100 MHz to watch neighboring pulses
lose their phase relation, or tick *Random phase per pulse* to break it altogether.

**COW monitoring line: `examples/dli_for_cow.py`.** Bits as a pulse in one of two slots, 10 %
decoys (two pulses), μ = 0.5, 25 km, a 90:10 tap at Bob with the data detector on the 90 % side
and the DLI on the 10 % side, three SNSPD-like detectors and a time tagger. The full protocol, with
Alice's and Bob's parties, is [`examples/cow`](cow/README.md).

```bash
phoq run examples/dli_for_cow.py
```

```
COW monitoring line (μ = 0.5, 25 km, 10 % tap to the DLI, SNSPD-like detectors)
   source                                      V expected    DM1    DM2  data QBER
   coherent pulse train, 100 kHz           0.989    0.997   5300     28  0.0011 (128952 bits)
   coherent pulse train, 10 MHz            0.716    0.730   4613    762  0.0011 (128952 bits)
   coherent pulse train, 30 MHz            0.379    0.390   3740   1683  0.0011 (128952 bits)
   no phase relation between pulses        0.018    0.000   2809   2707  0.0013 (128604 bits)
```

Both the DPSK contrast and the COW visibility follow $e^{-\pi\Delta\nu T}$, because the engine shares
one phase walk between the two arms (see [docs/engine.md](../docs/engine.md)). The data line's
error rate does not move at all: only the monitoring line sees coherence. The visibility is counted
on slots where pulse $k$ and pulse $k-1$ were both sent, across bit boundaries and inside decoys.
The delay line loses 0.92 dB more than the phase shifter, which caps the visibility at
$2\sqrt{t}/(1+t) = 0.994$ for that transmission ratio $t$; a bench would balance the arms with an
attenuator in the short one. The monitoring detectors sit behind 2 m more fiber than the data
detector, so DB's light arrives 9.8 ns earlier; the tagger's `ch1_delay` adds 9.8 ns to its time
stamps, as one sets a tagger's input delay, and bin $k$ holds pulse $k$ on all three channels.
Without it the visibility, counted on the wrong pulse pairs, falls to 0.43.

`phoq serve examples/dli_for_cow.py` opens this bench. The tagger counts DB on CH1, DM1 on CH2 and
DM2 on CH3. With the 100 kHz laser, a 2 ms run gives about 30,100, 2,160 and 920 clicks. DM1 leads
DM2 by about 2.4× rather than more, because output slots that hold only one pulse split 50/50
between the ports. Tick *Random phase per pulse* on `LD-A` and DM1 and DM2 even out (about 1,510
and 1,420) while DB stays where it was.

## Clock and frame synchronization from time stamps

Before any QKD post-processing, Bob must know which of Alice's symbols each click belongs to. His
time tagger runs on its own clock: its rate is off from Alice's by some ppm, and its time zero is
unrelated to hers. Alice and Bob agreed beforehand on a random string and its length, and Alice
sends it over and over. `examples/clock_sync.py` recovers Alice's clock and the delay from the
clicks alone.

**The bench.**

- Alice: a CW laser and an intensity modulator. Her AWG runs at 100 MHz + 23.4 ppm (Bob does not
  know the offset) with four samples per symbol, and plays each bit of an L = 1000-bit string as
  (s_k, 0, 0, 0). A 1 lights a 2.5 ns pulse centered on her clock tick, a 0 stays dark, and a frame
  is P = 10 µs. A VOA sets 0.2 photons per "1" pulse at the detector.
- Bob: a single-photon detector (η = 25 %, 1 µs dead time) and a free-running time tagger with
  100 ps bins: no delay compensation, no lock to Alice, and an unknown 3.21 µs clock epoch, set as
  the tagger's `ch1_delay`.

The analysis uses only the time stamps, the agreed string and the nominal rate:

1. **Clock.** The click train has a spectral line at Alice's symbol rate. The FFT of the click
   histogram finds it to one FFT bin (1/T = 500 Hz for a 2 ms record). The periodogram of the time
   stamps themselves, $Z(f) = |\sum_i e^{2\pi i f t_i}|^2$, maximized around that bin, then fits the
   clicks' phase against time. Its precision is $`\sigma_f \approx \sqrt{12}\,f\sigma_t/(T\sqrt N)`$
   for N clicks spread by $\sigma_t$ around the clock ticks, here the 2.5 ns pulse's 0.72 ns rms:
   tens of ppb.
2. **A check of the agreed frame length, by epoch folding.** Fold the clicks' symbol indices at
   each trial length ℓ. At ℓ = L they pile onto the string's 1s and leave its 0s empty, so the
   folded histogram's χ² exceeds its ℓ − 1 degrees of freedom by about N. A length sharing a factor
   g with L keeps about N·g/L of that excess, and 2L keeps all of it but over twice the degrees of
   freedom. So the significance $(\chi^2-\ell+1)/\sqrt{2(\ell-1)}$ peaks at L if the light really
   repeats every L symbols. The delay step uses the agreed L either way.
3. **Delay.** The phase of $Z(f_A)$ places the clicks inside a symbol, $\theta = \mathrm{frac}(\Delta f_A)$.
   Each click then gets a symbol index, the indices are folded modulo L, and they are circularly
   cross-correlated with the string as ±1 by FFT. The peak's lag D is the whole-symbol offset, and
   $\Delta = (D+\theta)/f_A$ modulo the frame. The string is random so that the peak is unique: its
   ±1 autocorrelation is L at zero lag and only about ±√L elsewhere. A periodic 1010… pattern would
   give Δ only modulo two symbols.

```bash
phoq run examples/clock_sync.py --plot sync.png
```

| | patch cords only | 25 km of fiber |
| --- | --- | --- |
| Clicks in 2 ms | 1,434 | 1,432 |
| Clock: FFT peak → periodogram | 100.002313 → 100.0023396 MHz | 100.002314 → 100.0023430 MHz |
| Clock error against Alice's | −4 ppb | +30 ppb |
| Frame check (epoch folding) | L = 1000 at 33 σ; best coprime length 3.2 σ | L = 1000 at 30 σ; 4.5 σ |
| Cross-correlation peak | D = 322 symbols, 24 σ | D = 566 symbols, 25 σ |
| Δ mod P against the traced flight time + epoch | 3.224683 µs against 3.224692 µs (−9 ps) | 5.667150 µs against 5.667099 µs (+52 ps) |
| Clicks given their true symbol index | 100 % | 100 % |

With 25 km the true delay is 125.664 µs, more than twelve frames. Time stamps alone give it only
modulo P, so a real link resolves the rest with a longer frame or a coarse classical timestamp.
The delay error has two sources: the per-click spread over √N (about 19 ps), and the clock error
times the 1 ms between Alice's frame origin and the middle of the record (the 30 ppb of the
25 km run is 30 ps).

**1 ns time bins.** `build(rate=, resolution=, record=, dead_time=, mu=)` scales the bench, and
`GHZ = dict(rate=1e9, resolution=20e-12)` is the 1 GHz case. There the AWG runs at 4 GS/s playing
(1, 0, 0, 0), so each 1 is a 250 ps pulse, a frame is 1 µs and the tagger bins are 20 ps (phoq's
finest):

| Link | Record | Dead time | Clicks | Click spacing | Clock error | Frame check (σ; best coprime) | Delay error | Assigned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 km | 200 µs | 1 µs | 193 | 1022 symbols | +10 ppb | 1034, wrong (9 σ; 6.8 σ) | +6.3 ps | 100 % |
| 0 km | 2 ms | 1 µs | 1928 | 1026 | −0 ppb | 1000 (38 σ; 10.1 σ) | −0.3 ps | 100 % |
| 0 km | 200 µs | 50 ns | 2250 | 76 | −22 ppb | 1000 (46 σ; 2.8 σ) | −2.1 ps | 100 % |
| 25 km | 200 µs | 50 ns | 2227 | 77 | −19 ppb | 1000 (47 σ; 3.3 σ) | −4.8 ps | 100 % |

At 1 GHz the detector, not the line, sets the statistics. The flux asks for 24 million clicks per
second; a 1 µs dead time allows about one per microsecond, whatever the symbol rate. Saturated,
the detector clicks once per dead time plus a short wait (the median spacing is about 1025
symbols), a rhythm of its own right next to the 1000-symbol frame:

- With 200 clicks, the blind frame check scores that rhythm (1034) above the true L. The true L
  only reaches about N/√(2L) ≈ 4.5 σ there.
- With 1900 clicks the true L wins at 38 σ, but the rhythm still shows at 10 σ.
- A 50 ns detector, as with SNSPDs, gives ten times the clicks in the same 200 µs and takes the
  artifact away (a 2.8 σ floor).

The clock and the delay never needed the blind check, since they use the agreed L.
`tests/test_sync.py` runs the 50 ns case and the saturated 1 µs case.

**Long dead times.** A free-running InGaAs SPAD is often held off 40–100 µs to keep afterpulsing
down. phoq has no afterpulsing, so here the dead time only costs clicks: a record T holds about
$`N = T/(\tau_d + W)`$ of them, where W is a live detector's mean wait for its next click. At 1 GHz
with 250 ps tagger bins (`LONG`):

| Dead time | μ | Record | Clicks | T/(τd + W) | W/τd | Runner-up | Clock error | Frame check (σ; best coprime) | Delay error | Assigned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 µs | 0.2 | 20 ms | 500 | 500 | 0.001 | 1.00 | +0 ppb | 4004, wrong (19 σ; 16.2 σ) | −0.1 ps | 100 % |
| 40 µs | 0.002 | 2 ms | 45 | 46 | 0.097 | 0.38 | +43 ppb | 3271, wrong (7 σ; 6.1 σ) | +48 ps | 100 % |
| 40 µs | 0.002 | 20 ms | 456 | 456 | 0.097 | 0.13 | −1 ppb | 1000 (11 σ; 4.3 σ) | −14 ps | 100 % |
| 100 µs | 0.2 | 50 ms | 500 | 500 | 0.000 | 1.00 | −19 991 ppb: lost | 2565, wrong (23 σ; 19.0 σ) | lost | 0 % |
| 100 µs | 0.0008 | 50 ms | 455 | 456 | 0.097 | 0.13 | +0 ppb | 1000 (8 σ; 3.9 σ) | +1.0 ps | 100 % |

Saturated (μ = 0.2, W ≈ 40 ns), the detector clicks almost exactly once per dead time, and this
regular rhythm causes two problems:

- **The clock is ambiguous.** The clicks strobe Alice's clock at a rate of about $1/\tau_d$, so the
  line at $f_A$ has copies every $1/\tau_d$: 25 ppm of the clock at 40 µs and 10 ppm at 100 µs.
  Bob's ±100 ppm search holds several of them, almost equally tall. "Runner-up" is the tallest
  other peak in the search window over the chosen one; Bob can compute it without the truth, and
  at 1.00 it says the clock cannot be trusted. Which copy wins is chance: the 40 µs run happened
  to pick the right one, while the 100 µs run landed two copies off (−20 ppm) and lost the delay.
- **The frame check is fooled.** The rhythm (40,040 symbols at 40 µs) piles the clicks up at
  divisors of itself, such as 4004.

More clicks do not cure this: N grows with T, but the clicks stay just as regular. Attenuating
until the detector waits about a tenth of its dead time does, here μ = 0.002 and 0.0008. Each wait
then adds about $W$ of jitter to the rhythm, so the copies wash out within a few clicks (runner-up
0.13), while the count barely drops.

From there:

- Clock and delay come right from the 45 clicks of 2 ms.
- The blind frame check needs $N/\sqrt{2L}$ well above its ≈ 4 σ noise: about 450 clicks, which is
  20 ms at 40 µs and 50 ms at 100 µs.
- An oscillator known to better than half the copy spacing (±12 ppm at 40 µs, ±5 ppm at 100 µs)
  would also pick the right copy.

**The string breaks the tie.** A copy $k/\bar\tau$ from Alice's line, $\bar\tau$ being the mean
interval between clicks, stands as tall as hers because there click i's phase gains about $k\,i$
whole turns, which the periodogram cannot see. Step 3's symbol indices count those turns: off by
$k\,i$, they no longer follow the string when folded, and the clicks land on its 1s only by chance.
`break_tie` refines each copy in the search window as step 1 refines its peak, runs step 3 at every
line, and keeps the one that puts the most clicks on the string's 1s. Bob gets $\bar\tau$ from his
own time stamps. On the same records:

| Dead time | μ | Record | Lines | j | On 1s | Next best | Clock error, step 1 | Clock error, string | Delay error | Assigned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 µs | 0.2 | 20 ms | 8 | 0 | 99.4 % | 60.6 % | +0 ppb | +0 ppb | −0.1 ps | 100 % |
| 40 µs | 0.002 | 2 ms | 9 | 0 | 100 % | 77.8 % | +43 ppb | +43 ppb | +48 ps | 100 % |
| 40 µs | 0.002 | 20 ms | 9 | 0 | 100 % | 59.2 % | −1 ppb | −1 ppb | −14 ps | 100 % |
| 100 µs | 0.2 | 50 ms | 20 | +2 | 100 % | 60.8 % | −19 991 ppb | −0 ppb | +0.4 ps | 100 % |
| 100 µs | 0.0008 | 50 ms | 22 | 0 | 99.6 % | 60.7 % | +0 ppb | +0 ppb | +1.0 ps | 100 % |

"Lines" are step 1's line and its copies in the window, and j counts the copies from step 1's line
to the string's. "On 1s" is the share of the clicks that line's fold puts on the string's 1s, and
"next best" is the best other line's share. Chance puts half the clicks on the 1s at any one lag,
more at the best of the 1000 lags, and more still with fewer clicks: about 60 % of 500 clicks and
78 % of 45. The 100 µs run moves up two copies and gets its clock and delay back. The 40 µs run
keeps its line, now by a margin of 99.4 % to 60.6 % rather than by chance. Below saturation there
are no copies, and the string keeps step 1's line. The frame check is still fooled by the rhythm,
but step 3 uses the agreed L. On a 5 ms record, `tests/test_sync.py` starts `break_tie` from step
1's own pick and from a copy two lines off, and checks that both come back to Alice's line.

[docs/photon-counter.md](../docs/photon-counter.md) derives these results and walks through the
tagger's acquisition. Two engine details matter here:

- **Dead time across chunks.** The tagger synthesizes a record in chunks of a million bins (20 µs
  at 20 ps), and the detector's dead time carries from one chunk to the next, and into the next
  acquisition when it continues the record. `build()` splits records over the tagger's 10⁸ bins
  into back-to-back acquisitions.
- **Tagger bins must be shorter than half a symbol.** With 500 ps bins at 1 GHz the tagger's
  Nyquist frequency is the clock itself. The time stamps' periodogram then mirrors the line about
  it: Alice's +23.4 ppm and a −23.4 ppm image stand equally tall.

**Why the AWG carves pulses.** With a CW laser the modulator alone shapes the light. A pulse
filling a fraction d of the symbol spreads its clicks over a phase 2πd of the clock, so the line at
f_A keeps a depth sinc(πd). NRZ (d = 1, the string played one sample per symbol) has its spectral
null exactly at the symbol rate, and step 1 has nothing to lock to:

| Pulse | Line depth (sinc(πd)) | Clock error | Delay error | Clicks assigned |
| --- | --- | --- | --- | --- |
| 2.5 ns (d = ¼) | 0.895 (0.900) | −4 ppb | −9 ps | 100 % |
| 5 ns (d = ½) | 0.645 (0.637) | +42 ppb | −19 ps | 100 % |
| NRZ (d = 1) | 0.008 (0) | +50.5 ppm: lost | lost | 10.2 % |

The quarter-symbol depth sits just under sinc(πd) because the CW light leaks through the modulator
(30 dB extinction) during the dark samples. With NRZ, the clock would have to come from the string
itself, through a joint search of frequency and delay that the example does not implement.
This bench leaves the AWG's `rise_time` and `jitter` and the detector's `timing_jitter` at 0:
instant edges and no timing jitter. On a bench both add to σ_t and widen the errors in proportion.
`tests/test_sync.py` also repeats the patch-cord run and the 25 km run (with a −57 ppm clock and a
different epoch), checks the carving table's depths, and runs the analysis on hand-made clicks.

### Three SPADs behind a DLI, at 1 GHz

`examples/clock_sync_dli.py` runs the same synchronization on the receiver of a COW link, at one
setting: 1 GHz symbols (250 ps pulses) over 25 km, SPADs held off 10 µs as free-running InGaAs
SPADs are, and a 10 ms record in 250 ps tagger bins (`LONG`). It takes about 20 s.

- Bob's 90:10 tap sends 90 % of the light to the direct line's SPAD and the rest through a
  delay-line interferometer (DLI) to the bright and the dark line's SPADs, all on one tagger, every
  channel at Bob's unknown epoch. A VOA sets 0.2 photons per "1" pulse at the direct SPAD.
- The DLI's long arm is one 1 ns symbol, 0.20 m of fiber, longer. As in `examples/cow_vacuum`,
  standard cords make it 0.25 m longer and a delay line in the short arm takes back the rest. A
  VOA in the long arm evens out the arms' losses, and the phase shifter is locked so that a pulse
  adds to the one before it on the bright SPAD.

Two steps change. The clock comes once from all three lines: each line has its own delay, and so
its own phase of the clock line, so their periodograms add as powers. Each line's delay comes from
its own pattern, the light it should see in each symbol, in units of a "1" pulse at the line's
input:

| Bits $s_{k-1}\,s_k$ | 00 | 01 | 10 | 11 |
| --- | --- | --- | --- | --- |
| Direct line, $s_k$ | 0 | 1 | 0 | 1 |
| Bright line, $(s_k+s_{k-1})^2/4$ | 0 | ¼ | ¼ | 1 |
| Dark line, $(s_k-s_{k-1})^2/4$ | 0 | ¼ | ¼ | 0 |

In the DLI, symbol k holds pulse k through the short arm on top of pulse k − 1 through the long
one, each arm carrying half a pulse's light. Their fields add on the bright SPAD and cancel on the
dark one, which lights only where the string changes. Each line needs its own pattern:

- Against the string itself, the bright line peaks at its delay and again one symbol later, as
  tall, since it holds $s_k$ and $s_{k-1}$ alike. In the run below the two stand 13.9 and 13.8 σ.
- The dark line does not peak against the string at all: a random string's changes are
  uncorrelated with the string at every lag. They are as random as the string itself, though, so
  the dark line's own pattern gives one clear peak.

A DLI that is not locked moves the light of two 1s between its SPADs, and the bright and dark
patterns no longer hold.

```bash
phoq run examples/clock_sync_dli.py --plot sync_dli.png
```

| | direct | bright | dark |
| --- | --- | --- | --- |
| Clicks in 10 ms | 996 | 902 | 735 |
| A live SPAD's mean wait W | 0.004 τd: saturated | 0.11 τd | 0.36 τd |
| Frame check (σ; best coprime) | 3347, wrong (23 σ; 19.4 σ) | 1000 (17 σ; 3.8 σ) | 1000 (16 σ; 3.6 σ) |
| Cross-correlation peak against the line's pattern | D = 672, 23 σ | D = 683, 17 σ | D = 683, 20 σ |
| Δ mod P, error against the traced flight time + epoch | −1 ps | +1 ps | −3 ps |
| Clicks given their true symbol index | 100 % | 100 % | 100 % |
| Against the string itself | — | D = 683 (13.9 σ) or 684 (13.8 σ) | no peak (2.8 σ) |

**The hold-off splits the lines in two.** The direct SPAD, with 90 % of the light, waits only
40 ns once live. Saturated, it clicks almost every 10 µs, and as with the long hold-offs above,
that rhythm strobes Alice's clock:

- On the direct line alone the clock line has copies every 1/τd, 100 ppm of the clock. The one
  inside Bob's ±100 ppm search, 76 kHz below nominal, stands 0.95 as tall as the line itself, so
  which one step 1 picks would be chance. [spad_tones.py](spad_tones.py) shows the same copies on
  the spectrum analyzer: with W ≪ τd they stand every 1/(τd + W) on each side of the clock line,
  nearly as tall as the line itself.
- The rhythm also pulls the direct line's frame check onto 3347, a third of its 10,040 symbols.

The DLI's lines get a tenth of the light, less the DLI's losses, and wait 1 and 4 µs. Their waits
scatter the rhythm, so they show one clock line and confirm L. Added to the direct line's, their
periodograms bring the copy down to 0.40 of the line, and the shared clock lands within a ppb of
Alice's. Every line's delay then comes to a few ps. The DLI's lines sit 11.245 ns behind the
direct one (traced 11.243 ns): the 1 m cord into the DLI, the short arm's 1.25 m and the delay
line.

`tests/test_sync_dli.py` runs this setting. It checks each line's clicks against its pattern,
symbol by symbol, that the string alone cannot place the DLI's lines, and that the DLI's lines
rescue the saturated direct line's clock. It also runs the analysis on hand-made clicks on all
three lines.

## Quantum random numbers from vacuum fluctuations

`examples/vacuum_noise_qrng.py` builds the vacuum-noise quantum random number generator of Gehring et al. (2021)
and Park et al. (2025), with the oscilloscope as its ADC:

```
laser (LO, 4 dBm) → BS-1 ─┬─ 1 m → INPUT+    BPD-1 (PDB450C, 10⁴ V/A, 45 MHz)
 FC/APC cap: vacuum ───────┘─ 1 m → INPUT−    RF OUTPUT → OSC-1 CH1 (adc_bits=8)
```

Only the vacuum enters the coupler's second input, which is capped with an angled terminator. The balanced detector's difference current
keeps the vacuum's quadrature noise amplified by the LO, which is the shot noise of
[Laser intensity noise and shot noise](../docs/README.md#laser-intensity-noise-and-shot-noise), while the LO's own noise cancels. Each 8-bit sample is Gaussian, with variance
$`\sigma^2 = \sigma_Q^2 + \sigma_E^2`$: the vacuum's share plus the classical excess (the amplifier,
any leaked laser noise).
Only $\sigma_Q$ is unpredictable. The script estimates three things:

- **Min-entropy against classical side information** (Park Eqs. 7–8): the adversary knows each
  sample's classical part and guesses its bin, $`H = -\log_2\max\{c_1, \mathrm{erf}(\delta/2\sqrt2\sigma_Q)\}`$.
  Here c₁ is the end bin, reached when the classical noise pushes a sample up to 5σ_E toward it.
- **Min-entropy against quantum side information** (Gehring Eq. 67). The excess noise is a thermal
  state of n photons on a vacuum of variance g², so σ² = g²(1+2n). The bound is maximized over δ;
  for n = 0 it is the classical bound.
- **Correlations** (Gehring Eqs. 14, 22, 24). Only what the past cannot predict counts:
  $`\sigma_X^2 = \exp\langle\ln f(\lambda)\rangle`$, the geometric mean of the samples' PSD. Likewise
  $\sigma_U^2$ for the excess noise alone. Then g² = σ_X² − σ_U² and n = ½σ²/g² − ½.

A Toeplitz hash then compresses N samples to $`\ell = N H - \log_2(1/2\varepsilon^2)`$ bits
(Gehring Eq. 1).

The detector's DC output matters. phoq's balanced detector is DC-coupled, with INPUT− 3.2 % less
responsive, so `build()` trims the coupler to 0.4920 so that the photocurrents match. That is Park's
precision balancing, and it also cancels the LO's −140 dBc/Hz RIN. At 0.5000 the 0.29 V of DC puts
every sample on an end code of the ±9 mV screen.

| Check | Result |
| --- | --- |
| Shot noise: σ² − σ_E² against LO power, −2 to 4 dBm | ∝ I: 401–406 codes² per mA, against 404 from 2q·I through the detector's and the scope's filters; R² = 1.0000 (Park: 0.9957) |
| Dark noise σ_E² | 279 codes², against 280 from the NEP through the same filters (and 1/12 of quantization) |
| Quantum-to-classical noise ratio at 1.8 mA | +4.2 dB = 2q(I₊ + I₋) over the NEP noise (+4.17 dB) |
| Sample rate against the 45 MHz detector | acf(1) 0.002, 0.005, 0.072, 0.290 at 20, 50, 100, 200 MS/s, against 0.000, 0.004, 0.069, 0.292 from the filters' impulse response; H per sample 5.57, 5.63, 5.57, 5.43 bits; rate 0.11, 0.28, 0.56, 1.09 Gb/s |
| 8-bit min-entropy against the ADC range, ±3σ, ±4σ, ±6σ, ±8σ | classical 1.56, 4.14, 5.50, 5.08 bits; quantum 5.78, 5.57, 4.98, 4.57 bits (best range 5.80) |
| 12 and 16 bits at ±8σ | classical 9.08 and 13.09 bits; quantum 8.57 and 12.57 bits |
| Extraction at 100 MS/s, 8 bits | 512 samples → 2726 bits, 532 Mb/s; output passes the frequency, runs and byte-χ² checks |
| Vacuum input left open (flat FC/UPC), or an FC/APC cap on that flat port | RF OUTPUT at 37.9 mV, past the ±8.9 mV screen: every sample an end code; +36 to +41 % classical noise on top of the shot noise; capped FC/APC on FC/APC: none |

Every record draws its own noise: sweep k of record j comes from numpy's stream (seed, j, k), and
`--seed` changes them all. With seeds 1, 2 and 3 the shot noise per mA stays within 401–410
codes², acf(1) within 0.006 of the filters' values, the min-entropies within ±0.05 bit (±0.02 at the best range), and
every extraction passes.

The two side-information models want different ADC ranges. The classical worst case lets the
adversary push a sample toward an end bin, so it needs a wide range. The quantum bound takes the
excess noise as Gaussian and is best near ±4σ at 8 bits. Sampling faster than the detector's
bandwidth still adds bits per second, but each sample carries fewer.

The vacuum input must stay dark and must not reflect. Left open, its flat end sends back the LO
light that the matings at the coupler's outputs reflect (−50 dB each), about 10 ns late. That
copy beats with the LO, so RF OUTPUT leaves the screen, and the delay turns the laser's phase
noise into intensity noise. The LO-off calibration cannot see that noise, so it would count it
as quantum. The report names the port: `Open connector BS-1.a2 reflects light on to BPD-1, -58 dB
under the light that reaches it directly`.

The bounds reproduce the papers' own numbers (`tests/test_qrng.py`):

| Paper | Their value | phoq |
| --- | --- | --- |
| Park Eq. (8): σ_Q = 4212 and σ_E = 2107 codes, 16 bits | 13.37 bits | 13.366 bits |
| Gehring Table 1: σ², σ_X², σ_U² at the worst corner of their confidence intervals | 10.74 bits | 10.78 bits |
| Gehring Eq. (1): 352 samples at 3.51 bits, ε_hash = 10⁻³² | 1024 bits extracted | 1023 bits |

phoq's "quantum" noise is numpy's pseudo-random generator, so this models a QRNG's design (power,
balance, bandwidth against sample rate, ADC range and resolution, extraction) and not its
randomness. Not modeled: the detector's AC coupling, the ADC's non-linearity and interleaving
errors (which cost Gehring 7.23 of their 10.74 bits), and drifts that would break stationarity.
[laser_noise.md](laser_noise.md) connects this to the shot-noise measurement.

The comparison with both papers is in the main README, under
[Checked against the literature](../README.md#checked-against-the-literature).
