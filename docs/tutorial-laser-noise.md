# Laser noise in the virtual lab

A hands-on tour of laser noise, done entirely in phoq's 3D lab: no Python. You build one bench
and use it to see, measure and tell apart the detector's noise floor and the two parts of the
light's intensity noise: its shot noise and the laser's excess noise. You also see what balanced
detection cancels, where it stops, and what the spectrum analyzer actually displays.

Every reading quoted below is what the lab shows for these settings, to within a few tenths of a
dB (the noise is random). The theory behind each step is in
[examples/laser_noise.md](../examples/laser_noise.md). The term *excess RIN*, and the way RIN is
measured, follow Obarski and Hale (NIST), [*How to measure relative intensity noise in
lasers*][oh-rin], Laser Focus World (1999); the shot-noise RIN is what they call Poisson RIN.

**Contents**

1. [Open the lab](#1-open-the-lab)
2. [Build the bench](#2-build-the-bench)
3. [Read the laser first](#3-read-the-laser-first)
4. [The floor: switch the laser off](#4-the-floor-switch-the-laser-off)
5. [Shot noise: both inputs](#5-shot-noise-both-inputs)
6. [Intensity noise: one input](#6-intensity-noise-one-input)
7. [Balanced detection and its limit](#7-balanced-detection-and-its-limit)
8. [What the analyzer shows](#8-what-the-analyzer-shows)
9. [A trap: an open spare port](#9-a-trap-an-open-spare-port)
10. [A real laser](#10-a-real-laser)
11. [What to remember](#11-what-to-remember)

## 1. Open the lab

```bash
uv run phoq serve
```

Open <http://localhost:8000>. The table is empty. Add `--no-save` if you don't want your edits
autosaved to `lab_setup.json`.

To skip the building in section 2, open the finished bench instead. It's built by a script, but
from then on you only use the lab. Set the laser's Excess RIN to −130 dBc/Hz, the attenuation to
0 dB and the analyzer as in step 4 of section 2, then go on from section 3:

```bash
uv run phoq serve examples/laser_noise.py
```

## 2. Build the bench

```
laser (CW, 3 dBm) → attenuator → 2×2 coupler ─┬─ INPUT+   balanced detector    RF OUTPUT → spectrum analyzer
          (spare input capped)                └─ INPUT−   (PDB450C, 100 kV/A)
```

Open the **Toolbox**: click the tab on the left edge (it closes when the mouse leaves it). Add each
part by clicking it or dragging it onto the table:

| Part | Then set, in the inspector |
|---|---|
| **Laser module** | Modulation **cw** (it starts as pulsed); Average power **3 dBm**; Wavelength **1550 nm**; Excess RIN **−130 dBc/Hz** |
| **Variable attenuator** | Attenuation **0 dB** |
| **2×2 fiber coupler** | Split ratio (bar) **0.5** |
| **Balanced photodetector** | Part preset **Thorlabs PDB450C**; Transimpedance gain **100 kV/A** (the preset starts at 10 kV/A) |
| **RF spectrum analyzer** | (it goes on the shelf) |
| **Fiber patch cord**, four of them | — |

As soon as the laser is on the table, a warning says its open connector emits invisible infrared.
That's expected: it clears once a cord is plugged in.

Then wire it:

1. **Fibers.** Drag a patch cord's yellow connector near a port: free ports light up, and it snaps
   in when close. Patch laser `out` → attenuator `p1`; attenuator `p2` → coupler `a1`; coupler
   `b1` → detector **INPUT+**; coupler `b2` → detector **INPUT−**.
2. **RF.** Select the detector. Under **Electrical connections**, set **RF OUTPUT** to the
   analyzer's input, `ESA-1.inp`.
3. **The spare port.** Click the empty table to show the setup overview. It counts **Spare ports
   open: 1**, the coupler's second input `a2`. Click **Cap spare ports**: a terminator of the
   port's own type plugs onto it. Section 9 shows why this matters.
4. **The analyzer.** Click **Oscilloscope** in the top bar to open the instrument panel. It shows
   the analyzer, ESA-1; if the panel is collapsed to its title bar, click its **+**. Set Start
   **0**, Stop **2 MHz**, RBW **10 kHz**, Avg **50**, Detector **average**, Trace **average**, Ref
   **−60 dBm**, Scale **5 dB/div**. The green **RUN** in the title bar means it is acquiring. If
   it reads **STOP**, click it.

The measurement box under the screen shows the **Median level**: on a noise floor, that is the
noise power in one resolution bandwidth. Press **Clear trace** after every change so the
running average starts afresh, and read once the box's **Sweeps** count passes 20. A fresh trace
reads low, by up to 0.2 dB after one sweep: the median of noise sits below its mean until many
sweeps are averaged. Even settled, a reading wanders by a few hundredths of a dB, so its last digit
can differ by one from the numbers below.

## 3. Read the laser first

Select the laser. Its readouts include:

> Excess RIN: −130 dBc/Hz; shot-noise RIN at this power: −158.9 dBc/Hz

These are the two parts of the beam's RIN, both "noise power over mean power²" per Hz:

- **−130 dBc/Hz** is the **excess RIN**, the laser's intensity noise above the shot noise limit,
  from quantum noise (gain and resonator losses) and technical noise (the pump, vibrations).
  Losses leave it unchanged: a laser has it no matter how you attenuate or detect the light.
- **−158.9 dBc/Hz** is the **shot-noise RIN** of a beam of this power, 2hν/P, the shot noise
  limit: the fundamental minimum RIN of a laser beam. It belongs to the light (coherent light
  arrives Poissonian), not to the laser's quality.

Set Average power to **0 dBm**. The shot-noise RIN becomes −155.9 dBc/Hz: it falls as power
rises, ∝ 1/P. The excess RIN stays −130 dBc/Hz. Set the power back to **3 dBm**.

## 4. The floor: switch the laser off

Set the laser's **Emission** off. The analyzer reads:

| | Median level (RBW 10 kHz) | as a density |
|---|---|---|
| laser off | **−89.4 dBm** | −129.4 dBm/Hz |

That is the detector's amplifier noise. At 100 kV/A the PDB450C has an NEP of 1.55 pW/√Hz,
−129.2 dBm/Hz at the analyzer. The median sits mid-span, at 1 MHz, where the detector's 4 MHz
bandwidth takes 0.26 dB (section 6 comes back to this), and the analyzer's own floor adds 0.03 dB:
−129.4 dBm/Hz. Nothing optical is involved. The application-note name for it is
"thermal noise"; Park calls it the electronic noise.

The analyzer has a floor of its own too. Select it and set **Noise floor (DANL)** to
**−120 dBm/Hz**: the reading rises to about −79.5 dBm, and now you're measuring the analyzer.
A bare photodiode straight into the analyzer's 50 Ω is worse off. Its floor is the thermal noise
of that 50 Ω load, 4kT/R, which the analyzer shows as 4kT = −168 dBm/Hz; the shot noise of 1 mA,
2qI·50 Ω, is the same −168 dBm/Hz. Both sit below a typical analyzer's DANL, which is why noise is
measured with an amplified detector. (Obarski and Hale draw the thermal limit at kT = −174 dBm/Hz,
the noise a matched 50 Ω source delivers. A photodiode is a current source, so the load's full
4kT/R shows, 6 dB more.) Set the DANL back to **−150 dBm/Hz**, and the Emission back on.

## 5. Shot noise: both inputs

With both inputs patched, the two photocurrents subtract. Step the attenuator and read:

| Attenuation | INPUT+, INPUT− | Median level |
|---|---|---|
| 0 dB | 555 µW each | **−67.4 dBm** |
| 3 dB | 278 µW each | −70.6 dBm |
| 6 dB | 139 µW each | −73.6 dBm |
| 10 dB | 55.5 µW each | −77.5 dBm |

The noise falls **1 dB for every dB** of attenuation: it is proportional to the photocurrent.
That is the signature of shot noise. The laser's excess noise, which would scale as I², has
cancelled.

Check the absolute level. The photocurrents are I = ℜ·P with ℜ = 1.00 and 0.968 A/W, so
I₊ + I₋ = 1.09 mA at 0 dB. The shot noise is 2q(I₊ + I₋). RF OUTPUT drives the analyzer's 50 Ω
from 50 Ω, so the analyzer sees half the voltage G·i:

  S = G²·2q(I₊ + I₋) / (4 · 50 Ω) = −107.6 dBm/Hz, so −67.6 dBm in 10 kHz.

That is within 0.2 dB of the −67.4 dBm on screen. Two small effects make up the rest, pulling
opposite ways. A trace of the laser's excess RIN leaks through the detector's imbalance (+0.4 dB;
section 7 removes it). The detector's 4 MHz bandwidth trims the noise toward 2 MHz (−0.3 dB).

## 6. Intensity noise: one input

Take INPUT− away so nothing subtracts. Select the cord between coupler `b2` and INPUT− and click
**Unplug**: the cord stays on the table, loose, for section 7. Three warnings appear. Two say the
coupler's open `b2` emits light and reflects it back toward the detector. Click the empty table
and **Cap spare ports** again: it caps `b2`, which a careful experimenter would do rather than
leave a fiber end open, and those two clear.

The third stays: RF OUTPUT now carries the whole DC, and G·I₊ must stay under the detector's
4.6 V swing. At **10 dB** it still reads "RF OUTPUT would reach +5.55 V, beyond its ±4.6 V
swing…". Work at **13 dB** and above.

Read the noise with the laser's **Excess RIN** at −130 dBc/Hz, then again with it off
(**−200 dBc/Hz**):

| Attenuation | INPUT+ | excess RIN −130 | excess RIN off |
|---|---|---|---|
| 13 dB | 27.8 µW | **−73.8 dBm** | −82.7 dBm |
| 16 dB | 13.9 µW | −79.1 dBm | −84.9 dBm |
| 20 dB | 5.55 µW | −84.6 dBm | −87.0 dBm |
| 25 dB | 1.75 µW | −88.1 dBm | −88.5 dBm |
| 30 dB | 555 nW | −89.1 dBm | −89.1 dBm |

Both columns are the light's intensity noise over the amplifier's floor. How it grows with the
photocurrent I tells its two parts apart:

- With the excess RIN off, only the shot-noise part is left, 2qI: 1 dB per dB at high power.
- With it on, the high-power end climbs toward **2 dB per dB**: the excess part, RIN·I², is
  ∝ I².
- At low power the two columns meet. The shot noise, ∝ I, wins below the crossover current
  2q/RIN = **3.2 µA**, between 20 and 25 dB here. By 30 dB both are lost in the amplifier's
  floor.

### The RIN, read off the screen

The relative measure is noise density over the carrier's power, in dBc/Hz. Obarski and Hale write
it R(f) = P_E(f) / (P·C(f)): the noise above the floor, over the DC power, corrected for the
detection system's frequency response C(f). At **20 dB**:

1. Noise density: −84.6 dBm − 10·log₁₀(10 kHz) = −124.6 dBm/Hz.
2. Remove the floor (section 4, −129.4 dBm/Hz) by subtracting powers, not dB:
   10^(−12.46) − 10^(−12.94) mW/Hz, which is −126.3 dBm/Hz.
3. Correct for C(f). The detector's 4 MHz bandwidth passes 1/(1 + (f/4 MHz)²) of the noise
   power. The median level sits mid-span, at 1 MHz, where that is −0.26 dB, so the noise is
   −126.1 dBm/Hz before the roll-off.
4. The carrier: the detector's readout shows RF OUTPUT **+0.2773 V into 50 Ω**, so
   (0.2773 V)²/50 Ω = 1.54 mW = **+1.9 dBm**. It is DC, where the response is 1.
5. Measured RIN: −126.1 − 1.9 = **−128.0 dBc/Hz**.

The measured RIN is the laser's excess RIN plus the detected shot-noise RIN 2q/I. With
I = 5.55 µA, 2q/I = −132.4 dBc/Hz, and taking it away (again in powers) leaves **−130.0 dBc/Hz**:
the laser's setting, its excess RIN. Skip step 3 and you get −130.3 dBc/Hz instead. The noise is
only 4.8 dB above the floor here, so the floor must be read well: the 0.2 dB a fresh trace can be
off by moves the result by 0.15 dB. Noise and carrier pass through the same 50 Ω divider and the
same gain, so neither the gain nor the responsivity needs calibrating, only the shape of the
response. That is the use of a relative measure.

The analyzer can do steps 1 and 4 for you. Set its **Units** to **dBc/Hz**. Ref moves to
−101.9 dBc/Hz, so the trace stays where it was, and the measurement box shows **DC at input
+1.9 dBm**: phoq's analyzer reads the DC ahead of its DC block. The Median level now reads
**−126.5 dBc/Hz**, everything at the input over that DC: the floor is still in it and C(f) is
not applied, so steps 2, 3 and 5 stay yours. Against this DC the floor is −129.4 − 1.9 =
−131.3 dBc/Hz, and the arithmetic ends at the same −128.0 dBc/Hz. A real RF analyzer blocks DC,
which is why step 4 reads it off the detector. Set the Units back to **dBm**.

Only the excess RIN is a property of the laser. The measured RIN depends on the attenuation,
through 2q/I: at 25 dB, I = 1.75 µA and the shot-noise part, 2q/I = −127.4 dBc/Hz, is already
larger than the laser's −130 dBc/Hz. Attenuate enough and any laser looks shot-noise limited.
Attenuation adds that noise because it removes photons at random, and the randomness is itself
noise ([RP Photonics][rp-rin]). In quantum terms, vacuum enters through the attenuator's loss
port: the same vacuum that [`vacuum_noise_qrng.py`](../examples/vacuum_noise_qrng.py) digitizes
from the coupler's unlit input.

## 7. Balanced detection and its limit

Put INPUT− back. Select the terminator on coupler `b2` and set **Plugged into** to **— not
plugged in —**; it drops off the port (leave it on the table, or **Remove** it). Then drag the
loose cord's two connectors back onto `b2` and INPUT−. Set the attenuation to **0 dB**: the
reading returns to **−67.4 dBm**, as in section 5, even though each diode now carries 100× the
light of section 6's 20 dB reading.

Now make the laser noisier: Excess RIN **−120 dBc/Hz**.

| | RF OUTPUT (DC, high-Z) | Median level |
|---|---|---|
| excess RIN −120, split 0.5 | +1.754 V | **−65.1 dBm**, 2.3 dB above section 5 |
| excess RIN −120, split **0.492** | +0.007 V | **−67.8 dBm**, the shot noise alone |

The PDB450C's INPUT− is 3.2 % less responsive than INPUT+ (its CMRR is 30 dB). With a 50:50
coupler the two photocurrents differ by that much, so the residue RIN·ΔI² gets through: the
non-zero DC on RF OUTPUT is the giveaway. Set the coupler's split ratio to **0.492** to give
INPUT− the extra light: drag the slider, or click it and step with the arrow keys (0.001 per
press). The DC drops to millivolts and the excess RIN cancels again. The reading even lands 0.4 dB
below section 5's, which still carried a trace of the −130 dBc/Hz excess RIN. That is Park's
precision balancing. Set the Excess RIN back to −130 dBc/Hz and the split to 0.5.

## 8. What the analyzer shows

The analyzer displays **power in one resolution bandwidth** (dBm in the RBW), not a density.
On the balanced bench at 0 dB, change the RBW:

| RBW | Median level | as a density |
|---|---|---|
| 1 kHz | −77.4 dBm | −107.4 dBm/Hz |
| 10 kHz | −67.4 dBm | −107.4 dBm/Hz |
| 100 kHz | −57.4 dBm | −107.4 dBm/Hz |

Noise power grows 10 dB per decade of RBW; the density stays put. To get the power spectral
density, subtract 10·log₁₀(RBW), or set the analyzer's **Units** to **dBm/Hz**. A tone is the
opposite: its level doesn't change with RBW. Set the RBW back to 10 kHz. Two other settings:
- **Avg** smooths the trace without moving the level.
- **Detector average** is the one for noise. **Peak** suits tones, and reads noise high whenever
  a display point spans several analyzer bins (at these settings each holds one, so they agree).

A real analyzer that averages in log units reads noise low, by about 2.5 dB, and needs a
correction. phoq averages power, so it needs none.

## 9. A trap: an open spare port

Still on both inputs at 0 dB, select the terminator on the coupler's `a2`, the input no light
should enter, and set **Plugged into** to **— not plugged in —**. The port is now an open flat
FC/UPC end, and a warning appears:

> Open connector BS-1.a2 reflects light on to BPD-1, −58 dB under the light that reaches it
> directly…

The reading rises from −67.4 to **−62.6 dBm**. Light reflected at the coupler's outputs leaves by
`a2`, its flat end sends 3.6 % back, and it reaches the detector about 10 ns late. Set the laser's
**Linewidth** to **0 Hz**: the reading falls back to about −67.4 dBm. The extra noise was the
laser's phase noise, turned into intensity noise by that delayed copy. The open port has built,
by accident, the optical frequency discriminator that [RP Photonics][rp-laser] lists for
measuring phase noise: an unbalanced interferometer. Light needs two reflections to come back
forward to the detectors, so it is this double bounce that matters. Restore the linewidth to
100 kHz and set the terminator's **Plugged into** back to `BS-1.a2`.

## 10. A real laser

Select the laser and set **Part preset** to **Koheras MIKRO E15**, a 1550 nm fiber laser: RIN
< −135 dBc/Hz at 10 MHz and a 100 Hz linewidth, from its datasheet. The Excess RIN selector now
shows −135 dBc/Hz, the preset's value. The preset sets 40 mW, which overdrives the detector (two
swing warnings); set the Average power back to **3 dBm** and they clear (the real laser's output
is adjustable).

The preset also gives the laser its real FC/APC pigtail, and the lab warns that an angled
connector now meets your flat FC/UPC cord: about 5 dB lost and 3.6 % reflected into the cord.
Select that cord and set the connector at the laser end to **FC/APC**: its inspector lists which
end (A or B) is plugged into the laser. The warning clears.

The bench reads as before, because phoq's excess RIN is white. The real E15's RIN has a
relaxation-oscillation peak near **0.7 MHz**, inside this analyzer span, at up to **−100 dBc/Hz**:
35 dB above what the lab shows. On a real bench, the balanced detector would need about 55 dB of
rejection at 0.7 MHz to reach the shot noise, so you would measure well above 1 MHz instead. The
preset lists the peak and the laser's frequency-noise spectrum under *unsupported*.

## 11. What to remember

| Noise | Absolute (what the analyzer shows) | Relative (dBc/Hz) | Balanced detection |
|---|---|---|---|
| amplifier floor (the detector's) | constant | ∝ 1/I² | stays |
| shot noise (quantum-limited intensity noise) | ∝ I: 1 dB per dB | shot-noise RIN 2q/I = 2hν/(ηP) ∝ 1/I | stays: 2q(I₊ + I₋) |
| excess noise (above the shot noise limit) | ∝ I²: 2 dB per dB | constant: the excess RIN | cancels, down to the CMRR residue |

The last two rows are the light's intensity noise; the first is not.

- **The crossovers:** shot noise beats the floor above the current where 2qI equals the floor;
  the excess noise beats the shot noise above 2q/RIN.
- **Absolute numbers** tell you what dominates on your bench and whether a signal clears the
  floor. **Relative numbers** need no gain calibration, only the detector's frequency response.
  The excess RIN describes the laser and survives attenuation; the measured RIN does not,
  because its shot-noise part 2q/I grows as you attenuate.
- **The analyzer shows power in the RBW.** Subtract 10·log₁₀(RBW) for a density, and read noise
  with the average detector.
- **Cap every spare port.** An open flat end turns phase noise into intensity noise.

[oh-rin]: https://www.laserfocusworld.com/detectors-imaging/article/16548496/how-to-measure-relative-intensity-noise-in-lasers
[rp-rin]: https://www.rp-photonics.com/relative_intensity_noise.html
[rp-laser]: https://www.rp-photonics.com/laser_noise.html
