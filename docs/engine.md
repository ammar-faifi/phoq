# How phoq computes: the core and the engine

This document explains what happens between building a `Setup` and reading a number off an
instrument: the object model, how light is traced through a setup, how fields are put on a time
grid, how detectors turn fields into currents and clicks, and how each instrument acquires. It ends
with what the model leaves out. It is written for people who extend phoq or want to know how far to
trust its results; the [README](../README.md) covers usage, and its *Theory* section the
modulator, oscilloscope and spectrum-analyzer physics in more depth.

| Module | Role |
| --- | --- |
| `core.py` | `Param`, `Port`, `Transfer`, `Component`, `Setup`: the object model |
| `engine.py` | `trace()` → `Network`, `synthesize()`, `report()`: the light |
| `sources.py` | `Laser`: the bin-averaged envelope and the phase noise |
| `passive.py`, `modulators.py` | `scatter()` of every optical part; modulators' time-dependent factors |
| `generators.py` | function generator and DC supply: the voltages that drive modulators |
| `detectors.py` | photodiode, single-photon detector, power meter |
| `instruments.py` | oscilloscope, photon counter (time tagger), RF spectrum analyzer |
| `runner.py` | `Runner`: acquire, run, sweep, average spectra |
| `server.py`, `parties.py` | the live lab's 15 Hz loop; protocol experiments block by block |
| `units.py` | constants, fiber and connector data, unit helpers |

## 1. The object model

**Parameters.** Every setting is a `Param` declared on the class: `Laser.power_dbm = Param(-7.0,
unit="dBm", min=-90, max=20)`. On an instance it reads and writes like a plain attribute, but every
write is converted to the declared type, checked against `min`, `max` and `choices`, and reported to
the setup's listeners (the lab server and autosave). `Param.spec()` describes the setting to the
web lab, which builds its inspector from it.

**Ports.** A `Port` belongs to a component and is `optical` or `electrical`, with a direction
(`in`, `out`, `bidi`) and at most one `peer`. Optical ports also have a connector type
(`FC/UPC`, `FC/APC`, `FC/PC`), which decides what a mated pair transmits and reflects.

**Components.** A `Component` subclass declares its `Param`s, creates its ports in `_build()`,
and implements whichever of these contracts apply:

| Method | Who implements it | Called by |
| --- | --- | --- |
| `scatter(port, λ) → [Transfer]` | everything light passes through | `trace()` |
| `envelope(t, dt)`, `phase(t, rng)` | `Laser` | `synthesize()` |
| `factor(t_mod, t_emit)`, `is_static()`, `static_factor()`, `mean_factor()`, `drivers()` | modulators, switch routes | `synthesize()`, `mean_powers()` |
| `voltage(ch, t_mod, t_emit)`, `start(rng)` | generators, DC supply | modulators, instruments |
| `detect(fields, dt, rng, n)` | photodiode, balanced photodetector, single-photon detector | instruments |
| `acquire(net, rng) → Result` | instruments | `Runner`, the lab server, `Experiment` |

A `Transfer(port, amp, delay, mod)` says: light that entered through one port leaves through
`port`, multiplied by the complex amplitude `amp`, after `delay` seconds, and, if `mod` is set,
multiplied at that moment by the modulator's time-dependent factor. A component is therefore a
small scattering matrix with delays, plus at most one time-varying element per route. Subclasses
register themselves by class name, which is how JSON setups and the lab's toolbox find them.

A modulator's factor is a function of the voltages on its electrical inputs, listed in `rf_ports`
and `bias_ports`: one of each for the phase and intensity modulators (`rf`, `bias`), and for the
IQ modulator two RF inputs (`rf_i`, `rf_q`) and three DC inputs (`bias_i`, `bias_q`, `bias_p`).
`drivers()` returns what feeds them, and `static_factor()` the complex factor when none of them
varies. The report checks each of those ports against a preset's `bias_voltage_v` and
`rf_input_dbm`. The IQ modulator's factor is
$`\tfrac12[e(\varphi_I) + e^{j\varphi_P}e(\varphi_Q)]`$, with $e$ the signed field of an MZM of finite
extinction. Its `mean_factor()` averages $|\cdot|^2$ symbol by symbol when I and Q are patterns of
one length, and over every pairing otherwise (README, "IQ modulator").

**Setup.** A `Setup` owns named components and the connections between their ports.
`connect(a, b)` mates two ports. Two bulkhead optical ports never touch directly: phoq inserts a
1 m `Fiber` patch cord whose two connectors match the two ports, as a lab would. `chain(...)` wires
parts one after another through their declared `inputs` and `outputs`. A `MatingSleeve` is an
adaptor: the connectors plugged into its two sides mate with each other inside it. The setup
serializes to JSON and to a runnable Python script; part presets (`presets.py`) set a component's
parameters from a datasheet and are stored by id.

## 2. The pipeline

```mermaid
flowchart LR
    S[Setup] -->|trace| N[Network<br/>paths per detector]
    N -->|report| R[steady state<br/>mean powers, warnings]
    N -->|synthesize on a time grid| F[complex fields<br/>per detector and λ]
    F -->|detect| D[photocurrent,<br/>clicks, voltages]
    D -->|instrument model| A[ScopeResult,<br/>CounterResult,<br/>SpectrumResult]
```

`trace()` runs once per snapshot of the setup and depends only on the setup. Everything random
happens afterward, in `synthesize()` and the detector and instrument models, so one `Network` can
feed many acquisitions. Three callers drive the pipeline:

- **`Runner`** (headless): `run()` traces once and acquires every instrument on that snapshot,
  together with `report()`; `acquire()`, `sweep()` and `average_spectrum()` build on it. One
  `numpy` generator per runner, seeded by `Setup(seed=…)` or `Runner(seed=…)`, makes runs
  repeatable.
- **The lab server** (`phoq serve`): about 15 times a second it traces, sends the `report`, and an
  oscilloscope frame per running scope. A spectrum analyzer sweeps at most every max(0.2 s, 3 × its
  last sweep time), and photon counters acquire once a second.
- **`Experiment`** (protocols): per block of pulses, every party programs its generators
  (`prepare`), the time tagger records the block with `acquire(start=block·slots)`, and the party
  owning it receives the batch (`measure`).

## 3. `trace()`: from components to paths

phoq does not build a global matrix. It enumerates **paths**: for every laser, every way its light
can reach every detector, each with one complex amplitude, one delay and the list of modulators it
passes:

```python
@dataclass
class Path:
    source: Laser
    amp: complex          # product of every transmission on the way, including fiber phase
    delay: float          # sum of every delay on the way [s]
    mods: tuple           # ((modulator, delay at which the light reaches it), ...)
    port: str             # the detector input it arrives at
    reflections: int      # 0 for the route itself, 1 or 2 for a stray that bounced off connectors
    sites: tuple          # where it reflected, in order: (label, the open port's path or None)
```

The tracer is a depth-first search with an explicit stack. Each entry is the port the light is
about to leave through, the amplitude and delay so far, the modulators met, a hop count, a
reflection count and the places it reflected. Popping an entry:

1. **Find the mating partner.** Normally the port's `peer`. If the peer is a `MatingSleeve`, the
   partner is whatever is plugged into the sleeve's other side.
2. **Open connector.** With no partner, the connector is open, a cord's end or a part's port alike
   (a coupler's spare input, a laser with nothing plugged in): its emitted power is recorded in
   `Network.open_ends`, and the end reflects back into the fiber, 3.6 % (−14.4 dB, the glass–air
   Fresnel reflection) for a flat end, −60 dB for an angled one. A `FiberTerminator` is a plug
   (`is_plug`): `Setup.connect` mates it straight onto a port, with no cord, or into a sleeve to end
   a cord (`Setup.terminate(port)` adds one of the port's own type; `Setup.terminate_spares()` caps
   every unused port of the parts other than lasers and detectors). It absorbs what the mated pair
   transmits, so its only reflection is that pair's.
3. **Mated pair.** Otherwise `mating(type_a, type_b)` gives the power transmission and the power
   reflected back into each side: 0.3 dB and −50 dB for FC/UPC pairs, −60 dB for FC/APC pairs,
   about 5 dB and a 3.6 % reflection into the flat side when an angled connector meets a flat one.
   The transmitted part enters the partner; the reflected part re-enters the component it came from.
4. **Entering a component** (`_enter`): the power is added to `port_power` and, for a patch cord,
   to its forward or backward flow (the lab draws the light flow from this). A detector's input
   (`inp`, or a balanced detector's `inp_plus` and `inp_minus`) ends the path and stores it in
   `Network.paths`, with the input it arrived at as `Path.port`. A laser ends it too and records the light in
   `Network.returned`: its own light by reflection site, others' by name. Anything else is asked for
   `scatter(port, λ)`, and every `Transfer` becomes a new stack entry with `amp × transfer.amp`,
   `delay + transfer.delay` and, for a modulator, the pair (modulator, delay at which the light
   reaches it).

A path is abandoned after 40 hops (`MAX_HOPS`), below a power gain of 10⁻¹⁸ (`MIN_GAIN`, −180 dB),
and after two reflections (`MAX_REFLECTIONS = 2`). One reflection carries the light back to the
lasers and reflectometry-like paths; light that goes on after reflecting needs a second, a double
bounce (one Fabry–Pérot pass). A doubly reflected path is kept only above 10⁻⁹
(`MIN_GAIN_TWICE_REFLECTED`, −90 dB): a stray at power ratio r beats with the light it meets at
2√r, and on the vacuum-noise QRNG −80 dB is already invisible next to the shot noise. That drops
every double bounce between two matching pairs (−100 dB for FC/UPC) and keeps the ones that matter:
an open flat end (−14.4 dB) or an angled-against-flat air gap (3.6 %) against any pair. A 1 m
FC/UPC cord from a laser to a photodiode therefore gives one path to the detector, and the laser
sees its two connector pairs at −50.0 dB and −50.6 dB (the far one has crossed the near one twice).

Left open, a coupler's spare input is such a reflector. In a balanced homodyne, LO light reflected
at the mating on a coupler output (−50 dB) leaves by the open input, comes back 3.6 % of it, and
reaches the detectors about 10 ns late, r·F·(½)² ≈ −71 dB of the direct light per path. It beats
with the LO, so the DC balance moves, and the delay turns the laser's phase noise into intensity
noise: on `examples/laser_noise.py` with a 100 kHz laser, 5.2 dB above the shot noise at
I₊ + I₋ = 1.1 mA, against 0.0 dB with the input capped.
`Network.delays()` returns the routes only (`reflected=True` adds the strays), so code that finds
an interferometer's arms by delay is not misled by them.

The resulting `Network` holds:

| Field | Content |
| --- | --- |
| `paths` | detector → list of `Path`, routes and strays (`Path.reflections`) |
| `fibers` | cord name → forward and backward power and the dominant wavelength (for the lab's light flow) |
| `open_ends` | each open connector's emitted power and wavelength (the report merges them per port) |
| `port_power` | `"NAME.port"` → power arriving there (absolute maximum ratings) |
| `returned` | laser → light coming back into it, by reflection site or by the other laser's name |

Fiber is handled entirely inside `scatter`: a `Fiber` or `FiberSpool` of length $L$ multiplies the
amplitude by $`10^{-\alpha(\lambda)L/20}\,e^{-i2\pi n_\mathrm{eff}L/\lambda}`$, with $\alpha(\lambda)$
the SMF-28 attenuation table (0.19 dB/km at 1550 nm), $n_\mathrm{eff} = 1.4677$, and adds a group
delay $n_g L/c$ ($n_g = 1.4682$, 4.9 ns/m). A `Coupler` with bar ratio $r$ and excess loss is
the unitary $`b_1 = \sqrt r\,a_1 + i\sqrt{1-r}\,a_2`$, $`b_2 = i\sqrt{1-r}\,a_1 + \sqrt r\,a_2`$, in both
directions. Because the phase of each path is exact, interference between paths with any delay
difference is exact too; the price is that the path count grows with every interferometer in
series.

## 4. The laser's field

A laser emits the complex field, in √W,

```math
E(t) = \sqrt{P(t)}\;e^{i\varphi(t)}
```

**Envelope, bin-averaged.** Instruments sample time in bins $[t, t+\Delta t)$, and $\Delta t$ ranges
from picoseconds (a fast scope) to hundreds of microseconds (a narrow-RBW spectrum analyzer). Point
sampling would make results depend on where the samples fall: a 100 ps pulse sampled every 1 ns is
missed between samples, or, if a sample lands on its peak, over-counted. phoq never samples the
envelope; `envelope(t, dt)` returns the square root of the **power averaged over each bin**,
computed in closed form:

| Mode | Average power over $[t, t+\Delta t)$ |
| --- | --- |
| `cw` | $P$ |
| `sine` | $`P\left[1 + m\,\dfrac{\cos\omega t - \cos\omega(t+\Delta t)}{\omega\Delta t}\right]`$ |
| `ook` | $\dfrac{P}{d}\cdot\dfrac{\mathrm{on}(t+\Delta t) - \mathrm{on}(t)}{\Delta t}$, with $\mathrm{on}(x)$ the cumulative on-time at duty $d$ |
| `pulsed` | $\dfrac{P}{f}\cdot\dfrac{C(t+\Delta t) - C(t)}{\Delta t}$, with $C(x) = \sum_k \tfrac12\big[1 + \mathrm{erf}\big((x - k/f)/\sigma\sqrt2\big)\big]$ |

Pulses are Gaussian, centered on $t = k/f$, with $\sigma = \min(\mathrm{FWHM}, 0.45/f)/2.355$. The
energy is exact at any bin size: a 100 ps pulse train integrates to 1.000000 pulse energy per period
with bins of 1 ps, 10 ps, 1 ns or 100 ns, while point-sampling it at 1 ns gives 9.4× the energy in
the same test. Within a bin the field has no structure, so a detector sees the bin's energy.

**Phase noise.** $\varphi$ is a Wiener process: a uniform random start, plus independent Gaussian
increments with $`\mathrm{Var}[\varphi(t+\tau)-\varphi(t)] = 2\pi\Delta\nu\,\tau`$, where $\Delta\nu$ is
`linewidth`. That is white frequency noise and a Lorentzian line of full width $\Delta\nu$; the field
correlation decays as $e^{-\pi\Delta\nu|\tau|}$, so the coherence time is $\tau_c = 1/\pi\Delta\nu$.
(Measured on the code: 0.641 rad² at τ = 1 µs and 100 kHz, against 2πΔντ = 0.628.)
`phase(t, rng)` evaluates **one** realization at an arbitrary set of times, including repeated ones:
it sorts them, draws the increments between neighbors and puts the result back in place. This is
what makes interference physical: two paths with delays $\tau_1$ and $\tau_2$ read the same random
walk at $t-\tau_1$ and $t-\tau_2$. `detuning` adds $`2\pi\,\delta f\,t`$, which is how two lasers beat at
a chosen offset. `phase_randomized` adds a fresh uniform phase per pulse slot, for weak-coherent-
pulse sources.

**Intensity noise.** `rin_db_hz` is the laser's relative intensity noise above the shot noise
limit, the one-sided PSD $S$ of $\delta P/\bar P$, white. `intensity_factor(t, dt, rng)`
multiplies the field by
$\sqrt{1+\delta}$, where $\delta$ is drawn per bin of an absolute grid of `dt`, with variance
$S/2\Delta t$ (the PSD over the bin's bandwidth $1/2\Delta t$). Every path that leaves in the same
bin reads the same $\delta$, as with the phase walk, so the noise is common to all of them and a
balanced detector can cancel it. Shot noise is not part of it: it is drawn at detection (section 6),
so a detector reads $`\mathrm{RIN} + 2h\nu/\eta P`$. `rin_db_hz` is the *excess RIN*, in the
terms of Obarski and Hale
([*How to measure relative intensity noise in lasers*](https://www.laserfocusworld.com/detectors-imaging/article/16548496/how-to-measure-relative-intensity-noise-in-lasers),
Laser Focus World, 1999), which losses leave unchanged. $`2h\nu/\eta P`$ is the *shot-noise RIN*
(their "Poisson RIN"), which every loss raises as 1/η. A detector measures their sum. At the
default, −200 dBc/Hz, nothing is drawn.
See [examples/laser_noise.md](../examples/laser_noise.md).

## 5. `synthesize()`: paths on a time grid

`synthesize(net, detectors, t, dt, rng, offsets=None)` returns, for each requested detector, a dict
wavelength → complex field array on the grid `t` (bin starts), in √W; for a detector with several
optical inputs (the balanced detector) the keys are (input port, wavelength), so each photodiode
gets its own fields. `offsets` moves one
detector's grid to `t + offset` (the time tagger's channel delays) while it keeps sharing the
lasers' phase walks with the others:

1. **Start the generators** that drive any modulator on those paths (`start(rng)`); a free-running
   generator draws a random epoch here.
2. **Group paths by laser.** For a laser with paths of delays $\tau_j$ to detectors with grid
   offsets $o_j$, the emission times are `emit = t + oⱼ − τⱼ` (bin starts) and `mid = emit + dt/2`
   (bin centers).
3. **One envelope and one phase call per laser**, over all its paths at once:
   `envelope(emit, dt) · exp(i·phase(mid, rng))`. Every path of that laser shares the realization.
4. **Per path**, multiply by `amp`, then by each modulator's factor at the moment the light passes
   it: `mod.factor(t_mod = mid + at, t_emit = mid)`. The emission time is passed too, because a
   generator on the *aligned* time base indexes its pattern by emission time, so pattern symbol $k$
   meets pulse $k$ whatever the fiber delay to the modulator.
5. **Sum the paths of one wavelength coherently.** Different wavelengths stay separate keys and
   add in power at the detector. Two lasers at the same wavelength interfere and beat at their
   `detuning` difference.

**Why bin centers.** Phases and modulator voltages are evaluated at the bin center. Pulses and
pattern symbols are centered on $k/f$; evaluating at bin starts would put many samples exactly on a
symbol boundary, where rounding decides the symbol.

**Cost.** Memory and time scale as (number of samples) × (number of paths of each laser), because
the phase walk is evaluated on the union of all paths' emission times.

## 6. Detectors

**Photodiode** (`detect` returns current in A, one value per bin):

```math
i = I_\mathrm{dark} + \sum_\lambda \mathcal R(\lambda)\,|E_\lambda|^2
```

plus white Gaussian noise of one-sided density $`2qi + 4k_BT/R_\mathrm{load}`$ (shot noise of the
instantaneous current and thermal noise of the load: per sample, a variance of that density over
$2\Delta t$). Shot noise belongs to the light, not to the detector (RP Photonics, "Shot noise").
phoq draws it at detection, which gives the same photocurrent statistics for coherent and classical
light: loss, including a quantum efficiency below 1, keeps them Poissonian, so $2qi$ holds for any
η. Light with sub-shot-noise (squeezed) statistics is outside the model.
Signal and noise then pass together through the bandwidth as one exact discrete pole,
$\alpha = 1 - e^{-2\pi B\Delta t}$, so the noise has the density $`(2qi + 4k_BT/R)\,|H(f)|^2`$ and
the variance $`(2qi + 4k_BT/R)\,B_N`$ with the pole's noise bandwidth
$B_N = \alpha/(2-\alpha)/2\Delta t \approx \tfrac{\pi}{2}B$. Finally a soft saturation
$x \to x/(1 + (x/I_\mathrm{sat})^4)^{1/4}$. $\mathcal R(\lambda)$ is the material's typical curve or
a preset's datasheet curve, scaled to the responsivity at the reference wavelength. Instruments
start the record early by `settle_time` = $6/2\pi B$ so the filter has settled.

**Balanced photodetector** (`detect` returns the open-circuit voltages of `rf`, `mon_plus` and
`mon_minus`, from one noise realization). Each photodiode's current is computed as above from the
fields of its own input, with the INPUT− diode's responsivity short by $`10^{-\mathrm{CMRR}/20}`$
(the model of the common-mode rejection), and carries its own shot noise $2qi_\pm$, uncorrelated
with the other diode's: behind a 50:50 coupler each photon's path is random. The amplifier
adds its input noise, the NEP of the selected gain referred to peak responsivity. Then

```math
U_\mathrm{RF} = G\,H(f)\,\bigl(i_+ - i_- + i_\mathrm{amp}\bigr), \qquad
U_{\mathrm{MON}\pm} = G_\mathrm{mon}\,H_\mathrm{mon}(f)\,\bigl(i_\pm + i_{\mathrm{mon}}\bigr),
```

each through one pole (the gain's bandwidth; 1 MHz for the monitors), the monitors with their own
noise $i_\mathrm{mon}$ (`monitor_noise` rms over the monitor's noise bandwidth). The outputs run into
their rails at ±swing with a 16th-power knee, $x \to x/(1 + (x/U_\mathrm{swing})^{16})^{1/16}$:
linear to 0.03 % up to 60 % of the swing. An instrument with a 50 Ω input sees
$`U\,R_\mathrm{in}/(R_\mathrm{in} + R_\mathrm{out})`$, half for RF OUTPUT. The shot noises of the
two diodes add in power while their mean currents subtract; a path difference between the inputs
needs no extra model, since each path keeps its own delay.

**Single-photon detector** (`detect` returns a boolean per bin). The mean photon number in a bin is
$`\bar n = \sum_\lambda k(\lambda)\,|E_\lambda|^2\,\Delta t / h\nu`$, with $k(\lambda)$ the preset's
spectral efficiency curve relative to its reference wavelength (1 without a curve). Each bin clicks
with

```math
p = 1 - (1 - p_\mathrm{dark})\,e^{-\eta\bar n}, \qquad p_\mathrm{dark} = 1 - e^{-R_\mathrm{dark}\Delta t}
```

independently, which is exact for coherent light: its photon number is Poissonian. Dead time then
removes every click within $\lceil t_d/\Delta t\rceil$ bins of the last kept one (non-paralyzable).
For an oscilloscope, `waveform()` turns clicks into 1 V, 5 ns pulses.

**Power meter.** No time grid: it reads `Network.mean_powers` (section 8), weighted by its InGaAs
head's responsivity relative to the calibration wavelength.

## 7. Acquisition

Every instrument implements `acquire(net, rng)` and returns a result object with `summary()` (text)
and `to_json()` (what the lab draws). An acquisition always:

1. chooses a time grid from the instrument's settings,
2. calls `synthesize()` for the detectors on its inputs,
3. runs the detector models,
4. applies the instrument's own signal chain and measurements.

Each acquisition draws fresh randomness: a new phase walk, new noise, new clicks. Two
acquisitions are independent records, not consecutive pieces of one record.

### Oscilloscope

- **Record.** A window of 10 divisions, $`W = 10\,s_\mathrm{div}`$. The sample rate is capped by the
  record memory, $f_s = \min(f_\mathrm{set}, N_\max/W)$, so $N = f_sW$ points (at least 100). At
  1 µs/div with 5 GS/s and 20 000 points: 2 GS/s, 20 000 points, 0.5 ns apart.
- **Analog signal.** Simulated $`k = \lceil 6\,f_\mathrm{BW}\Delta t\rceil`$ times finer than the
  sample interval (capped so the grid stays under 200 000 samples), starting early enough for the
  photodiode and front end to settle. Inputs are photodiode currents, single-photon-detector pulse
  trains, or generator voltages.
- **Front end.** Two identical poles at $`1.554\,f_\mathrm{BW}`$, so the −3 dB point is the set
  bandwidth.
- **Sampling.** Every $k$-th sample: point sampling with no anti-alias filter, so a signal above
  half the sample rate aliases, as on a real scope.
- **ADC.** With `adc_bits` > 0, each sample is rounded to one of $2^\mathrm{bits}$ levels across the
  8 divisions about the channel's offset; off the screen it reads the end code
  (`ScopeResult.codes`). At 0 the samples are exact.
- **Trigger.** *sync* puts $t = 0$ on a pulse of the laser feeding the inputs, with one division of
  pre-trigger. *edge* simulates twice the record and starts at the first rising crossing of the
  level. *free* starts anywhere. Single shot (`arm()`, `single()`) keeps the first triggered record.
- **Measurements.** Mean, extremes, peak-to-peak and RMS on every sample; frequency from hysteretic
  mid-level crossings, or from the FFT peak when the trace is coarsely sampled. Records over 2 000
  points go to the screen as a min/max envelope per column.

### Photon counter (time tagger)

- **Bins.** One bin per pulse: the bin rate is `bin_rate` or the repetition rate of the laser
  feeding its detectors. Bin $k$ is centered on pulse $k$; with `compensate_delay` the bins shift by
  the shortest delay from that laser, so bin $k$ holds pulse $k$ after the fiber.
- **Channel delays.** `chN_delay` is added to channel N's time stamps, like a tagger's input delay:
  a detector whose light arrives Δ before the others' lines up with them at +Δ. Channel N's bin $k$
  then holds the light that arrived at its bin time minus `chN_delay` (a grid offset in
  `synthesize`), and `compensate_delay` uses the earliest time stamp, path delay plus channel
  delay. All channels are still synthesized in one call, so they share the phase walk.
- **Continuous clock.** The instrument keeps a running bin index, so `timestamps()` keep counting
  across acquisitions; a protocol sets `start` explicitly per block.
- **Sub-bins.** With `oversample` = $m$ the light is synthesized on $m$ sub-bins per bin and their
  photons are added before the click is drawn. A single bin averages each path's envelope over the
  whole bin, so two pulses that miss each other inside it (a delay-line interferometer set δ off)
  would still interfere fully; sub-bins of a quarter of the pulse width get their overlap right to
  2 %, at $m$ times the cost.
- **Chunks.** Long records are synthesized in chunks of `chunk` bins (one million by default;
  `chunk` divided by $m$ with sub-bins). A detector that clicked near the end of a chunk stays dead
  into the next (`SinglePhotonDetector.detect(..., blind=)` and `blind_after()`), and into the next
  acquisition when that starts where this one ended.

[photon-counter.md](photon-counter.md) walks through `acquire()` step by step and covers what a long
dead time does to the clicks: how many there are, how a saturated detector strobes a clock, and how
fine the tagger's bin must be.
- **Result.** A boolean click record per channel; `timestamps`, `events`, `counts`, `rates` and
  `coincidences` are derived from it. Clicks have no position inside their bin.

### RF spectrum analyzer

An FFT analyzer: for stationary signals it estimates the same thing as a swept one, the power in the
resolution bandwidth against frequency, in dBm into its input impedance.

- **Sample rate and record.** $`f_s = 2.56\,f_\mathrm{stop}`$. A periodic Hann window has an
  equivalent noise bandwidth of $1.5 f_s/N$, so the record is $N = 1.5 f_s/\mathrm{RBW}$ samples
  ($1.5/\mathrm{RBW}$ seconds), and the RBW is a noise bandwidth.
- **Averaging.** $K$ = `averages` records, overlapped by half (Welch), are power-averaged per sweep.
  A sweep is capped at 4 million samples: fewer averages first, then a coarser RBW.
- **Signal.** Simulated at $4f_s$, then low-passed and decimated by a polyphase FIR (`resample_poly`).
  Unlike the scope's point sampling, a signal above the span does not fold into it. A photodiode's
  current becomes a voltage across the input impedance; the input is AC-coupled, so the mean is
  removed.
- **Instrument noise.** White noise at the displayed average noise level (`noise_floor`, dBm/Hz) is
  added at the input; the photodiode's shot and thermal noise come from its own model.
- **Spectrum.** Each record is zero-padded 4× (a tone's level is right wherever it falls between
  bins) and scaled as $P_k = 2|X_k|^2/(R_\mathrm{in}(\sum w_n)^2)$: a sine of amplitude $A$ reads
  $A^2/2R_\mathrm{in}$ whatever the RBW, and white noise reads its density times the RBW.
- **Display.** `points` frequencies from start to stop. Each display point takes the FFT bins in its
  bucket through the detector: *peak* (tones), *average* (noise) or *sample*. The trace mode
  (*write*, *average*, *max hold*) combines sweeps.
- **Results and fits.** `SpectrumResult` keeps the display trace and the full-resolution bins
  (`psd()`): peak, median level, $N$ dB widths, integrated power, a Lorentzian fit, and
  `fit_self_homodyne(tau_d)` for a delayed self-homodyne at any delay. `SpectrumResult.average()`
  and `Runner.average_spectrum()` combine sweeps, e.g. over an interferometer's bias phase.
- **Cost.** About $4 \times N(K+1)/2$ simulated samples per sweep, times the number of paths: a
  narrow RBW over a wide span is slow, as on a real analyzer.

## 8. The steady state: `report()` and `mean_powers()`

The lab needs every detector's mean power, the light flow in every cord and the warnings 15 times a
second, without a time grid. `Network.mean_powers(detector)` computes the time-averaged power per
wavelength directly from the paths:

```math
\bar P = P_\mathrm{laser}\Big[\sum_j |a_j|^2 + 2\sum_{j \lt k}\mathrm{Re}\big(a_j a_k^{*}\big)\,\gamma(\tau_j - \tau_k)\Big]
```

summed over the paths of each laser (different lasers add in power), with the mutual coherence

```math
\gamma(\tau) = e^{-\pi\Delta\nu|\tau|}\times(\text{overlap of the envelope with itself delayed by }\tau)
```

The overlap is $e^{-r^2/8\sigma^2}$ for Gaussian pulses, with $r$ the delay modulo the period, and the
overlap of the on-windows for OOK. For phase-randomized pulses it is zero between different pulses.
Modulators enter with their exact complex factor when static, and with the square root of their
mean power factor when driven. Two paths that pass driven modulators interfere only if they pass the
same ones and have equal delays; otherwise they add in power. On an unbalanced Mach–Zehnder the
steady state agrees
with the time average of synthesized fields: 0.1085 mW against 0.1091 ± 0.0016 mW with a 300 m arm
($\gamma = 0.63$ at 100 kHz), and 0.268 against 0.274 ± 0.003 mW with 3 km ($\gamma = 0.01$).

`mean_powers(detector, port)` restricts the sum to the paths that arrive at one input.
`report(setup, net)` builds on it:

- **Per detector:** incident power; photodiode current and status (linear, saturated, blind at this
  λ); a balanced detector's power at each input, RF OUTPUT (high impedance and into 50 Ω), both
  monitors, the balance $(P_+ - P_-)/(P_+ + P_-)$ and its status; a single-photon detector's photons per pulse, PDE at each wavelength, click probability and
  expected rate.
- **Per laser:** the back-reflection it sees and its strongest source.
- **Per cord and open end:** the light flow, and what each open end emits.
- **Warnings:** too much light on a single-photon detector; a balanced detector's RF OUTPUT or
  monitor driven past its swing, or set to a gain it does not offer; absolute maximum ratings from
  presets (optical input, per photodiode for a balanced detector, bias voltage, RF drive, photons
  per pulse); a loaded pattern missing;
  open connectors emitting more than 1 µW; an open connector whose reflections reach a detector
  within −70 dB of its direct light (`STRAY_WARN`); angled connectors mated to flat ones.

## 9. Conventions

- SI units throughout. Fields in √W, so $|E|^2$ is power. Powers in the API as `power_dbm`,
  losses as `*_db`.
- One-sided power spectral densities (W/Hz into the analyzer's input impedance).
- Beam splitter: real bar, $+i$ cross amplitude. Fiber: $e^{-i2\pi n_\mathrm{eff}L/\lambda}$ phase,
  $n_gL/c$ delay.
- Time: a generator's pattern symbol $k$ and a laser's pulse $k$ are centered on $t = k/f$; the
  photon counter's bin $k$ is centered on pulse $k$.
- Randomness: one `numpy.random.Generator` per runner or session; `Setup(seed=…)` makes a run
  repeatable.

## 10. Current limitations

**Light and fiber**

- Scalar fields: no polarization, so no polarization drift, no polarization-dependent loss and no
  need for polarization controllers; interferometers have perfect polarization overlap.
- No chromatic dispersion: fiber adds loss, phase and a group delay, but pulses do not broaden.
- No nonlinearity (self-phase modulation, Brillouin, Raman) and no Rayleigh backscattering.
- Fiber phase is static: no thermal or acoustic phase drift, so an interferometer's bias phase never
  wanders (the delayed self-homodyne needs an explicit phase step for that reason).
- Couplers, attenuators, isolators and modulators are wavelength-independent; only fiber loss,
  detector responsivity and single-photon-detector efficiency depend on λ.
- At most two reflections per path: one Fabry–Pérot pass, no third reflection and no resummed
  cavity; doubly reflected paths below −90 dB are dropped. Light returning into a laser is reported
  but does not affect it (an internal isolator is assumed).
- Path enumeration: the number of paths grows with every interferometer in series, and loops (fiber
  rings, recirculating delay lines) are cut at 40 hops and −180 dB. That cut also drops paths
  weaker than −180 dB, e.g. a second 500 km spool.
- No optical amplifiers, frequency shifters (AOM) or optical filters.

**Sources**

- Coherent states only: no single-photon or entangled-pair sources, no Fock-state statistics, no
  Hong–Ou–Mandel interference.
- Laser phase noise is white frequency noise only: no 1/f (flicker) frequency noise, no chirp, no
  wavelength drift. Its intensity noise is white too: no relaxation-oscillation peak, no excess
  low-frequency (pump) noise, and no coupling between intensity and phase noise (linewidth
  enhancement).
- Pulses are Gaussian; no pulse shape from a datasheet.

**Modulators and electronics**

- Modulators respond instantly to the electrode voltage: no RF bandwidth or electrode capacitance,
  no bias drift or bias controller, no chirp, Vπ independent of wavelength and frequency. The IQ
  modulator's outer splitter and combiner are exactly 50:50, with no crosstalk between its RF
  inputs and no internal monitor photodiodes.
- Generators are ideal: no rise time, jitter or output impedance.

**Detectors**

- Photodiode: one pole, Gaussian approximation of shot noise (fine except at a few photons per
  sample), no avalanche gain or excess noise, no 1/f (flicker) noise, no ringing or impulse
  undershoot.
- Balanced photodetector: the CMRR is a flat responsivity mismatch, so it does not fall toward the
  bandwidth as a real unit's does, and attenuating INPUT+ by the mismatch would trim it out at every
  frequency; one pole per output; no AC-coupled versions, amplifier offset or warm-up drift; no
  spot size or power density on the diodes. Its steady-state report checks the swing on the mean
  output only: a beat or modulation that clips shows in the time domain.
- Single-photon detector: no timing jitter (a click sits in its bin), no afterpulsing, no gating or
  twilight events. Dead time is non-paralyzable. It carries across the photon counter's chunks, and
  into the next acquisition when that continues the record, so it may be longer than a chunk.

**Instruments**

- Oscilloscope: the ADC quantizes ideally (no non-linearity or interleaving errors); no trigger
  jitter or holdoff, equivalent-time sampling or probe loading.
- Photon counter: one bin per pulse and no tagger dead time. Each bin averages the light, so two
  pulses inside one bin interfere as if they overlapped, unless `oversample` splits the bin into
  sub-bins shorter than the pulses (a quarter of the width: the overlap is right to 2 %). Channel
  delays are set by hand (`examples/dli_for_cow.py` computes its 9.8 ns from the traced paths); there
  is no arrival-time histogram to read them from.
- Spectrum analyzer: FFT method only (no video bandwidth, swept-filter artifacts, spurs or local-
  oscillator phase noise), a white noise floor, no input attenuator or compression. The lab window
  shows a plain Lorentzian half width, which assumes a delay much longer than the coherence time;
  the short-delay fit runs from Python.

**Time and statistics**

- Each acquisition, and each photon-counter chunk, draws a new laser phase walk: consecutive
  records are statistically independent, not continuous in phase.
- The time grid's cost is samples × paths. Long records with many paths, or a narrow RBW over a wide
  span, are slow; the analyzer reduces averages and then coarsens the RBW to stay under 4 million
  samples per sweep.
- `report()` is a steady-state estimate: it replaces driven modulators by their average and is not
  meant to reproduce single-record fluctuations.
