# phoq documentation

How phoq works and how to drive it, beyond the [main README](../README.md)'s quick start:
the concepts and components, fiber connectors, the command line, analysis scripts, the
parties framework, the physics model, the theory of the modulators, delay lines, balanced
detection and instruments, the server API and the release process. The models' checks against
the literature are in the main README, under
[Checked against the literature](../README.md#checked-against-the-literature). Three documents
in this directory go deeper:

- [engine.md](engine.md): how the engine computes, from tracing and field synthesis to the
  detectors and each instrument's acquisition, and what it leaves out.
- [photon-counter.md](photon-counter.md): the time tagger's acquisition, detector dead time
  across records, and what long dead times do to the clicks.
- [tutorial-laser-noise.md](tutorial-laser-noise.md): laser noise measured by hand in the 3D
  lab, with no Python.

Part presets are documented in [src/phoq/presets/README.md](../src/phoq/presets/README.md),
and the examples, the QKD protocols among them, in [examples/README.md](../examples/README.md).

## Concepts

| | |
| --- | --- |
| **Component** | A piece of equipment. Settings are `Param` class attributes, so `laser.wavelength = 1310e-9` is validated against the type, range and choices, and is observable. |
| **Port** | `laser.out`, `pd.inp`, `pd.rf`, … Each is `optical` or `electrical`. |
| **Setup** | Owns named components and port connections. `connect(a, b)` joins two ports. Two bulkhead optical ports automatically get a 1 m `Fiber` patch cord between them. `chain(...)` wires items one after another. |
| **Runner** | `setup.run()` acquires every instrument. `Runner(setup).sweep(comp, "param", values, measure)` sweeps a parameter. |
| **Instruments** | `Oscilloscope` (traces + measurements; selectable analog `bandwidth`, `sample_rate` and record memory `max_points`; `Runner.single(osc)` waits for the first trigger and holds that sweep), `PhotonCounter` (click record per pulse), `PowerMeter`. |

Units are SI throughout: meters, seconds, hertz and watts. Powers use `power_dbm` and losses `*_db`.

### Components

| Category | Classes |
| --- | --- |
| Sources | `Laser` (CW / pulsed / sine / OOK, linewidth, detuning, relative intensity noise, per-pulse phase randomization) |
| Fiber | `Fiber` (patch cord), `FiberSpool` |
| Passive | `Attenuator`, `Coupler` (2×2 fused fiber coupler; `BeamSplitter` is its deprecated old name), `PhaseShifter`, `Isolator`, `MatingSleeve` (FC–FC adapter joining two cords), `FiberTerminator` (cap for an unused port: plugs straight in, absorbs), `OpticalDelayLine` (variable free-space delay: dial, carrier phase, Gaussian-beam coupling loss) |
| Modulators | `PhaseModulator` (φ = π·V/Vπ), `IntensityModulator` (Mach–Zehnder, extinction) — voltage comes only through their `rf` and `bias` inputs; `IQModulator` (dual-parallel MZM: `rf_i`, `rf_q` and three DC biases, `bias_i`, `bias_q`, `bias_p`); `OpticalSwitch` (2×2, bar below the threshold voltage on `ctrl`, cross above: a pattern routes pulse by pulse) |
| Detectors | `Photodiode`, `BalancedPhotodetector` (two FC inputs whose photocurrents subtract before a switchable-gain amplifier: RF OUTPUT, and a MONITOR output per diode), `SinglePhotonDetector`, `PowerMeter` |
| Instruments | `Oscilloscope` (with an optional ADC resolution, `adc_bits`), `SpectrumAnalyzer` (RF spectrum in dBm: RBW, averaging, detectors, trace average and max hold, Lorentzian and short-delay linewidth fits), `PhotonCounter` (time tagger: per-channel input delays, sub-bins for pulses shorter than a bin), `ArbitraryWaveformGenerator` (2 outputs: dc / sine / square / triangle / ramp / loaded pattern; `FunctionGenerator` is its deprecated old name), `DCPowerSupply` (2 outputs, ±30 V) |

Run `phoq catalog` for every port and parameter.

### Fiber connectors

Every fiber port has a connector type, following the usual color code: **FC/UPC blue**, **FC/APC
green**, and FC/PC (the older flat polish, as many datasheets still list it) blue too. A part's
`connector` sets all its fiber ports; a `Fiber` patch cord has `connector_a` and `connector_b`, so
a cord with one of each is a hybrid cord. `setup.connect()` between two bulkheads picks the cord a
lab would take, with ends that match the two ports. In the 3D lab connector housings and boots,
and the collar of every bulkhead, show the color. While you carry a connector, free ports where
it would make an angled-to-flat mating light up amber instead of cyan.

| Mated pair | Loss | Reflected back |
| --- | --- | --- |
| FC/UPC ↔ FC/UPC | 0.3 dB | −50 dB |
| FC/APC ↔ FC/APC | 0.3 dB | −60 dB |
| FC/PC ↔ FC/PC or FC/UPC | 0.3 dB | −40 dB (the worse face) |
| FC/APC ↔ FC/PC or FC/UPC | ≈ 5 dB, assumed | 3.6 % (−14.4 dB) into the flat side, −60 dB into the angled side |
| open FC/UPC or FC/PC end (a cord's or a part's) | — | 3.6 % back into the fiber |
| open FC/APC end | — | −60 dB |

The matching-pair values are typical specifications (`phoq.units.RETURN_LOSS_DB`,
`CONNECTOR_LOSS_DB`). An angled face against a flat one fits mechanically in an FC adaptor but
leaves an air gap. The flat face then reflects the full glass–air Fresnel 3.6 % straight back,
and the loss (`MISMATCH_LOSS_DB`) depends on the ferrule geometry. The 5 dB is an assumed value
to replace with your own measurement; the lab warns about any such mating whatever the number.

Reflections are traced, up to two per path: the inspector of every laser shows the setup's return
loss as the laser sees it and names the strongest reflection, e.g. `open FC/UPC end FIB-3.b
(−15.0 dB)`. Light reflected into a detector through a coupler, as in a reflectometer, is counted
like any other path, and interferes with it. A second reflection sends light on again, a double
bounce. These are kept above −90 dB, so two matching pairs (−100 dB) never count, but an open flat
end or an air gap does. Any unmated connector is open, a part's port as much as a cord's end: a
coupler's spare input left open reflects 3.6 %, and the report warns when what it reflects reaches
a detector within −70 dB of the direct light.

**Terminators.** A `FiberTerminator` caps an unused port. It plugs straight in, with no cord
(`setup.terminate(port)` adds one of the port's own type; `setup.terminate_spares()`, or **Cap spare
ports** in the lab's setup overview, caps every unused port of the couplers and other parts at once), and absorbs
the light it takes, so its only reflection is that of the mated pair: −60 dB for an FC/APC cap on
an FC/APC port. A green cap
on a blue port is an air gap, with the 3.6 % and the warning of any APC↔UPC pair. Cap the vacuum
input of a homodyne and the spare outputs of couplers. Left open, the vacuum input of
`examples/laser_noise.py` adds 5.2 dB of noise above the shot noise at 1.1 mA: LO light reflected
at a coupler-output mating leaves by it, comes back 10 ns late, and turns the laser's 100 kHz of
phase noise into intensity noise.

**Mating sleeves.** A `MatingSleeve` is an FC–FC adapter: plug a cord into each side and the two
cords' connectors mate inside it as one pair, with the loss and reflection of their two types. It
has no polish of its own, so a FC/APC cord meeting a FC/UPC one in a sleeve gets the same warning
as anywhere else. With one side empty, the other cord's end is an open connector. Cords plug
straight into a sleeve; between a sleeve and a part's bulkhead, or between two sleeves,
`connect()` inserts a cord, since two female sides cannot mate. Cascading cords shows what every
connection costs:

```bash
phoq run examples/connector_cascade.py
```

```
                                       matings   at the PD   current back to LD  strongest reflection
1 cord                                       2   -0.60 dBm    827 µA   -47.3 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
4 cords, 3 sleeves                           5   -1.50 dBm    672 µA   -44.1 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
8 cords, 7 sleeves                           9   -2.70 dBm    510 µA   -42.6 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
4 FC/APC cords, APC laser and PD             5   -1.50 dBm    672 µA   -54.1 dB  LD-1.out (FC/APC) ↔ FIB-1.a (FC/APC) (-60.0 dB)
4 cords, one FC/APC among FC/UPC             5  -10.91 dBm   77.1 µA   -15.6 dB  FIB-2.b (FC/UPC) ↔ FIB-3.a (FC/APC) in SLV-2 (-15.6 dB)
APC laser → UPC PD, last cord hybrid         4   -1.20 dBm    721 µA   -50.3 dB  FIB-3.b (FC/UPC) ↔ PD-1.inp (FC/UPC) (-51.8 dB)
4 cords, last end unplugged                  4           —      1 nA   -16.8 dB  open FC/UPC end FIB-4.b (-16.8 dB)
```

Each mating costs 0.3 dB on the way out and returns its reflection through all the matings
before it, so the back-reflection grows more slowly than the loss. FC/APC buys 10 dB on every
reflection. One wrong cord costs two air gaps (2 × 5 dB) and a −15 dB reflection, the same as
an unplugged end. `phoq serve examples/connector_cascade.py` puts the bench on the table:
change a cord's connectors in the inspector, or pull a cord out of a sleeve, and watch the
photodiode current and the laser's back-reflection follow.

### Part presets

A preset sets a component up as a specific product, with every number traced to its datasheet,
as in `IntensityModulator("IM-1", preset="exail/mx-ln-10")`.
[src/phoq/presets/README.md](../src/phoq/presets/README.md) lists the shipped presets and
explains the file format and where phoq looks for your own.

## Command line

```bash
phoq run examples/laser_noise.py --plot noise.png   # an example's analysis, headless
```

```bash
phoq serve examples/mpqkd                  # API + 3D visualizer, the parties as processes
```

```bash
phoq export examples/basic_link.json --format python
```

`phoq run` runs a script that has an `if __name__ == "__main__":` block as `python` would, so the
options after its name are the script's (`python examples/laser_noise.py` works too). A package
directory that defines `PARTIES`, such as `examples/mpqkd`, runs its protocol, each party as a
process, under `phoq run` and `phoq serve` alike, as `--protocol` does; with `--protocol`,
`--party` or `--analysis` the directory is just the setup. Any other setup, a `.py` that only
defines `setup` or `build()` or a saved `.json`, is acquired once and its measurements printed.
phoq's own options go before the file: `phoq run --plot traces.png examples/delay_line.py`
acquires that script's setup once and saves the scope traces.

`run`, `serve` and `export` accept a `.py` file that defines `setup` or `build()` (`setup` wins if
it has both), or a saved `.json`. While serving, every edit made in the browser sets the attribute on the Python
instance and is autosaved. The default target is `<script>.json` next to the script; change it
with `--save PATH`, or turn it off with `--no-save`. Serve that JSON to carry on, or press
**Export .py** to get the setup back as a Python script (`setup.to_python()`).

### Driving modulators

Modulators have no drive settings of their own. Each has two electrical inputs whose voltages
add on the electrodes: `rf` for an AWG output and `bias` for a DC supply output.

```python
psu = setup.add(DCPowerSupply(ch1_voltage=im.v_pi / 2))                      # quadrature bias
setup.connect(psu.ch1, im.bias)
fg = setup.add(ArbitraryWaveformGenerator())
setup.connect(fg.ch1, im.rf)
fg.set(ch1_shape="sine", ch1_freq=10e6, ch1_vpp=1.0)                           # linear modulation
fg.set_pattern("ch2", pm.voltage_for(2 * np.pi * k / 16), rate=laser.rep_rate)   # one voltage per pulse
```

For the intensity modulator 0 V is maximum transmission, Vπ/2 quadrature and Vπ the null. Real
LiNbO₃ devices have separate RF and DC electrodes with different Vπ: set `v_pi_bias` to the
datasheet's DC-electrode value and the supply on `bias` is referred to the RF electrode, so
quadrature is at Vπ_bias/2 on the supply, as on the bench.
`im.voltage_for(T)` and `pm.voltage_for(phase)` convert a wanted transmission or phase into the
rf voltage needed on top of whatever bias is connected, so protocol code works in optical terms.
An `IQModulator` has an input per electrode: `rf_i` and `rf_q` for the two generator channels, and
`bias_i`, `bias_q` and `bias_p` for three supply outputs. `iqm.operating_bias()` gives the
supply voltages, and `iqm.voltage_for(symbols)` gives both channels' patterns (see
[IQ modulator](#iq-modulator)). `set_pattern` sets the channel's amplitude
and offset from the pattern's extremes, and `chN_freq` is the symbol rate. The generator's time
base (`trigger`) is *aligned* by default: symbol k reaches the modulator together with pulse k of
the laser feeding it, as a calibrated AWG would. Use *laser* to set `chN_delay` yourself, or
*free* for a free-running generator. A generator output can also go straight to an oscilloscope
channel to look at the drive. Patterns are data, not parameters: they are not saved in the JSON
setup and have to be loaded by code.

The generator's edges are ideal unless you give it a `rise_time` and a `jitter`. The rise time
(10–90 %) turns every edge of a pattern or a square wave into an error-function step, rising and
falling alike, around the edge's nominal time. The jitter (RMS) moves every edge by its own
Gaussian time error, drawn anew for each acquisition. A modulator takes the drive at the center of
each simulated bin, so the edges show wherever the bins are finer than they are: on a scope, or on
a photon counter with sub-bins (`oversample`).

## Post-processing clicks with your own script

Write the analysis in a file of its own, decorate a function, and point the lab at it. Every
batch a time tagger records is handed to the function, which can keep state between batches:

```python
# my_analysis.py
import numpy as np
from phoq.analysis import task

@task("TAGGER")                         # the counter(s) feeding it; bare @task means every counter
def coincidences(batch):
    t_l, t_r = batch.timestamps("ch1"), batch.timestamps("ch2")   # absolute seconds, continuous
    print(f"batch {batch.seq}: {t_l.size} L, {t_r.size} R, {batch.coincidences('ch1', 'ch2')} both")
    return {"L rate [cps]": batch.rates["ch1"], "R rate [cps]": batch.rates["ch2"]}   # live read-outs
```

```bash
phoq serve examples/mpqkd --analysis my_analysis.py      # live, with a console
```

```bash
phoq run examples/mpqkd --analysis my_analysis.py --batches 20   # headless
```

`@task("TAGGER", party="CHARLIE")` puts a task's console on that party's laptop. A batch is a
`CounterResult`: `clicks[ch]` (one flag per bin), `timestamps(ch)`, `events()`
(time-ordered `(t, ch)` records), `counts`, `rates`, `coincidences(a, b)`, `t0`, `seq`. Bins run
on from one batch to the next, so time stamps are absolute. From Python, `Session(setup).run(10)`
does the same, and a script can run itself with `analysis.main("setup.py")` under
`if __name__ == "__main__":`. `examples/analysis_pairing.py` computes Charlie's click statistics
and the mode-pairing rate against Eq. (4) of the MP-QKD paper.

**How tasks run.** Each task runs in its own worker process (`multiprocessing`, spawn): a thread
would share the interpreter lock with the simulation and could not be stopped if the user's code
hung, and `print` cannot be captured per thread; Python 3.14's sub-interpreters would give
parallelism in one process, but NumPy's support for them is still immature. A process gives full
isolation, its own interpreter lock, a plain pipe for stdout/stderr that feeds the console, and a
clean kill. **Restart** re-imports the script, so edits take effect without restarting the lab.
A task that falls more than two batches behind has the next ones dropped, with a note in its
console (the tagger's buffer overflowing). An exception is printed to the console and the worker
goes on with the next batch.

In the virtual lab every process lives on a laptop (its party's, or the lab PC for plain tasks):
the **Laptops** drawer shows one tab per laptop with its processes' output, status, read-outs,
Stop / Restart / Clear, and "Load script" to attach another file by path; clicking a laptop on
the table opens it, and dragging the drawer's top edge resizes it. The API is `GET /api/tasks`,
`POST /api/tasks {path}` and `POST /api/tasks/{name}/{restart|stop|start|clear}`; the WebSocket
streams `console` messages.

## Parties and the classical channel

A protocol is more than hardware: Alice, Bob and Charlie are *computers* that program their own
instruments, read their own detectors and talk to each other. `phoq.parties` models that
split explicitly, following the `[CLASSICAL #N]` annotations in `examples/mpqkd/reference.py`.

| Layer | What | How it is simulated |
| --- | --- | --- |
| **Physical** | lasers, modulators, VOAs, fibers, Charlie's coupler, SPDs, the time tagger | one `Setup` in the lab process, because the fields of both stations interfere at Charlie |
| **Hardware link** | a station's computer ↔ its own instruments (program the AWG, read the tagger) | `self.hw` — request/reply over the party's pipe, limited to the instruments the party `owns`; a USB/PCIe-style local link, not a public channel |
| **Classical channel** | everything the parties tell each other | `self.net` — real TCP sockets on localhost, one listening port per party, messages pickled, every connection HMAC-authenticated with a pre-shared key: an *authenticated but public* channel, exactly what QKD assumes |

Each party is a `Party` subclass in a file of its own and runs in its own process (the same
reasons as for analysis tasks). The framework drives them block by block (`Experiment`):

```
prepare(block, n)   every station programs its transmitter for the next n pulses   [physical]
      ↓             the lab records the block
measure(batch)      the party owning the tagger gets its time stamps                 [physical]
      ↓
receive(message)    whatever the parties say to each other                           [classical]
```

[`examples/mpqkd`](../examples/mpqkd/README.md#parties-and-the-classical-channel) is split
along these layers, and its README maps each `[CLASSICAL #N]` annotation onto a message. Run a
protocol with its parties as processes, headless or live:

```bash
phoq run --protocol mpqkd --blocks 20
```

```bash
phoq serve --protocol mpqkd
```

`--protocol` takes a package exposing `PARTIES` and `build()`; `--party my_party.py` adds a file
that defines a `Party` subclass (an eavesdropper on the switch, for instance). A run goes on for as
many blocks as it was asked, however long that takes. It gives up only when no block has come for
120 s (`Experiment.run(timeout=…)`), a party being stuck, and then prints how many blocks it ran
and why it stopped. In the virtual lab
every party gets a laptop on the table showing its console, read-outs and TCP port, wired with
an Ethernet cable to a (purely visual) layer-3 switch whose port LEDs blink with the traffic;
clicking a laptop opens its console.

A station that re-encodes what it detects — a relay, a trusted node, an eavesdropper — sets
`stage = 1`. The lab then records its counters first, hands it the batch, waits for its
`measure()` to return (that is where it programs its own transmitter through `self.hw`) and only
then records the stations further down the line, so the feed-forward lands in the same block.
That is what a real station does with some latency, which the receiver's clock calibration
absorbs. `examples/bb84/eve.py` is one.

## Physics model

[engine.md](engine.md) walks through the engine step by step (tracing, field synthesis,
detectors, each instrument's acquisition) and lists its current limitations.
[photon-counter.md](photon-counter.md) goes deeper into the time tagger: its acquisition,
detector dead time across records, and what long dead times do to the clicks.

- **Fields, not just powers.** Light is traced as complex amplitudes through every path:
  connector loss and reflection by connector type (see [Fiber connectors](#fiber-connectors)),
  fiber loss vs λ, propagation phase, a 4.9 ns/m delay, the beam-splitter unitary, attenuators
  and modulators. Paths from one laser interfere, and different lasers add coherently only if
  their wavelengths match. One reflection is followed per path; a second (a Fabry–Pérot pass
  between two connectors) is ≤ −80 dB for matching ones.
- **Lasers:** the envelope is averaged over each sample bin, so a 100 ps pulse is exact at any
  timebase. Phase is a Wiener process from the linewidth, drawn once per acquisition and shared
  by all paths. That makes interferometer visibility and Alice/Bob phase drift physical. A white
  excess RIN (`rin_db_hz`, **Excess RIN** in the lab) can be added, also one realization shared by
  all paths; shot noise comes on top at detection. See
  [examples/laser_noise.md](../examples/laser_noise.md).
- **Photodiode:** I = Σ R(λ)·P + I_dark plus white shot noise (density 2qI) and thermal noise
  (4kT/R), all through a one-pole bandwidth: the noise RMS is √(2qIB) and √(4kTB/R) with
  B = (π/2)·f₃dB. It saturates softly above `saturation`.
- **Balanced photodetector:** U_RF = G·(ℜ₊P₊ − ℜ₋P₋), with the CMRR as a responsivity mismatch,
  through the selected gain's pole and swing; both diodes' shot noise plus the amplifier's NEP;
  a monitor per diode. See [Balanced photodetector](#balanced-photodetector).
- **Single-photon detector:** p(click) = 1 − (1 − p_dark)·exp(−η·n̄) per bin, plus dead time. A
  click's time is its bin's center or, on a photon counter with sub-bins, the place in the bin
  where its photon arrived, plus the detector's `timing_jitter`, a Gaussian given by its FWHM. Its
  `rise_time` (10–90 %) shapes the edges of the click pulse. Both are 0 by default; the PDM-IR
  presets set the datasheet's 200 ps of jitter. The click pulse is 1 V high and `pulse_width`
  long: 5 ns by default, the datasheets' 10 ns in the PDM-IR and SPCM-AQRH presets.
- **Modulators, oscilloscope and spectrum analyzer:** see
  [Theory](#theory-modulators-delay-lines-balanced-detection-and-the-instruments) below.
- **Steady-state report:** `phoq.report(setup)` gives mean powers, currents, open-connector
  emission and warnings. It uses the mutual coherence γ(Δτ) = exp(−πΔν|Δτ|) × envelope overlap.
- **Not modeled:** polarization, dispersion, nonlinearity, afterpulsing.

## Theory: modulators, delay lines, balanced detection and the instruments

The virtual lab's Help (?) window links four animated walkthroughs. Three step through one
acquisition of an instrument line by line, beside a screen that draws the array each line produces
on a real bench: the RF spectrum analyzer's sweep, the oscilloscope's front end, edge trigger, ADC
and measurements, and the photon counter's clicks, dead time and coincidences. The fourth follows
COW vacuum's Bob as he folds his tagger's time stamps onto Alice's slots, from `read_tagger` through
`phoq.timing.fold` to one event per pair. `phoq serve` serves them at `/walkthroughs/`. After
changing those methods, rebuild the pages with `uv run python tools/walkthroughs/build.py`;
`tests/test_walkthroughs.py` fails until you do.

### Electro-optic modulators

A modulator is a passive device with two electrical inputs. The electrode voltage is the sum of
what is connected to them, and an open input is 0 V:

```math
V(t) = V_\mathrm{rf}(t) + V_\mathrm{bias}(t)
```

Both modulators also have an insertion loss $L$ (dB), applied to the field as
$\sqrt{10^{-L/10}}$.

**Phase modulator** (`PhaseModulator`). The electro-optic effect shifts the phase in proportion to
the voltage; $V_\pi$ is the voltage for a shift of π:

```math
E_\mathrm{out}(t) = E_\mathrm{in}(t)\,\sqrt{10^{-L/10}}\;e^{\,i\pi V(t)/V_\pi}
```

With $V_\pi = 4$ V, 1 V gives π/4. `pm.voltage_for(phase)` returns $\phi V_\pi/\pi - V_\mathrm{bias}$,
the rf voltage needed on top of the connected bias. This is the linear (Pockels) electro-optic
effect: the index change $\Delta n = \tfrac12 n_0^3 r E$ over a length $l$ with electrode gap $d$
gives $`V_\pi = d\lambda_0/(l\,n_0^3 r)`$ (Fraunhofer IIS 2024, Eqs. 1–3), so $V_\pi$ grows with the
wavelength; here it is a device parameter at its design wavelength. The paper writes the shift as
$\varphi_0 - \pi V/V_\pi$; the sign is a crystal-orientation convention and the code uses $+$.

**Intensity modulator** (`IntensityModulator`). A Mach–Zehnder modulator splits the light into two
arms carrying $P_1 + P_2 = P_\mathrm{in}$, phase-shifts them in push–pull by $\pm\pi V/2V_\pi$ and
recombines them. With the arms' phase difference $\varphi = \pi V/V_\pi + \varphi_0$ the output power
is $P = \tfrac12 P_\mathrm{in} + \sqrt{P_1P_2}\cos\varphi$ (Fraunhofer IIS 2024, Eq. 6), so

```math
T(V) = \tfrac12\left[1 + k\cos\!\left(\frac{\pi V}{V_\pi} + \varphi_0\right)\right],
\qquad k = \frac{2\sqrt{P_1P_2}}{P_\mathrm{in}} = \frac{\mathrm{ER}-1}{\mathrm{ER}+1}
```

A perfectly balanced device ($k = 1$) is the textbook $\cos^2(\pi V/2V_\pi)$ and goes fully dark;
real devices split slightly unequally, which rounds the null off at $T_\min = 1/(\mathrm{ER}+1)$ and
caps the maximum at $T_\max = \mathrm{ER}/(\mathrm{ER}+1)$, where ER is the (linear) extinction
ratio set by `extinction_db`. $\varphi_0$ (`phi0`) is the arms' intrinsic path difference: it
shifts the whole curve along $V$, and in a real device it drifts with temperature and charge, which
is what a bias controller nulls (the drift itself is not modeled). The three bias points for
$\varphi_0 = 0$:

| $V$ | $T$ | Use |
| --- | --- | --- |
| 0 | $\mathrm{ER}/(\mathrm{ER}+1)$ | maximum transmission (default, nothing connected) |
| $V_\pi/2$ | ½ | *quadrature*: $dT/dV = -\pi k/2V_\pi$, the steepest and most linear point, for analog modulation |
| $V_\pi$ | $1/(\mathrm{ER}+1)$ | *null*: pulse carving, vacuum / decoy states |

At quadrature a small drive $v$ gives $T \approx \tfrac12 - \tfrac{\pi}{2V_\pi}v$, i.e. a modulation
depth of $\pi v_\mathrm{pp}/2V_\pi$ for a peak-to-peak drive $v_\mathrm{pp}$ (a 1 Vpp sine on a
device with $V_\pi$ = 4 V swings $T$ between about 0.30 and 0.70). `im.voltage_for(T)` inverts the curve,
$`V = (V_\pi/\pi)\left[\arccos\!\big((2T - 1)/k\big) - \varphi_0\right] - V_\mathrm{bias}`$, so protocol
code can ask for a transmission. Asking for 0 or 1 gives the null or the maximum.

**Time base.** An AWG pattern has one value per symbol, symbol $k$ centered on
$t = k/f_\mathrm{sym}$. With the default *aligned* time base the generator is treated as a
calibrated AWG locked to the laser clock: the voltage a pulse sees is evaluated at the pulse's
emission time, so pulse $k$ meets symbol $k$ in whichever modulator the channel drives, whatever
the fiber length before it. The *laser* time base uses the time of arrival at the modulator minus
`chN_delay` (you calibrate the delay), and *free* adds a random start each acquisition.

**Steady-state report.** The live power read-outs use the time-averaged transmission
$\langle T\rangle$ over one period of the drive (or over all symbols of a pattern) with the bias
taken as DC; `IntensityModulator.info()` shows it. Paths through a time-varying modulator are
treated as mutually incoherent in that report, which is only an approximation of the full
time-domain synthesis used for the oscilloscope.

### IQ modulator

`IQModulator` is a dual-parallel Mach–Zehnder: the light splits into an in-phase (I) arm and a
quadrature (Q) arm. Each arm is a push–pull MZM like the intensity modulator above. The arms
recombine after a relative phase set on a third DC electrode (Seimetz 2009, §2.1.2, Fig. 2.3) [1].
It has two RF inputs, `rf_i` and `rf_q`, which are RF1 and RF2 on the Exail MXIQ-LN [2]. It has
three bias inputs, `bias_i`, `bias_q` and `bias_p`, which are DC1, DC2 and DC3. In this section
$j$ is the imaginary unit, as in Seimetz, because $i$ is the in-phase coordinate.

**Field.** Each MZM is the intensity modulator above, written for its field rather than its power.
The field keeps its sign, and an MZM at its null flips that sign as the drive crosses it
(Seimetz Fig. 2.2b):

```math
e(\varphi) = \sqrt{\tfrac{1+k}{2}}\,\cos\tfrac{\varphi}{2} + j\sqrt{\tfrac{1-k}{2}}\,\sin\tfrac{\varphi}{2},
\qquad |e|^2 = \tfrac12\left[1 + k\cos\varphi\right] = T,
```

where $`\varphi_I = \pi V_{\mathrm{rf},I}/V_\pi + \pi V_\mathrm{DC1}/V_{\pi,\mathrm{DC}} + \varphi_{0,I}`$, and
likewise for Q. The outer interferometer adds the two arms (Seimetz Eq. 2.11):

```math
\frac{E_\mathrm{out}}{E_\mathrm{in}} = \sqrt{10^{-L/10}}\;\tfrac12\left[e(\varphi_I) + e^{j\varphi_P}\,e(\varphi_Q)\right],
\qquad \varphi_P = \frac{\pi V_\mathrm{DC3}}{V_{\pi,\mathrm{DC3}}} + \varphi_{0,P}.
```

**Operating point.** `operating_bias()` puts both MZMs at their null
($`V_\mathrm{DC1} = V_\mathrm{DC2} = -V_{\pi,\mathrm{DC}}`$) and the arms in quadrature
($`V_\mathrm{DC3} = V_{\pi,\mathrm{DC3}}/2`$). There the field is Seimetz Eq. (2.16):

```math
\frac{E_\mathrm{out}}{E_\mathrm{in}} = \tfrac12\cos\frac{\Delta\varphi_I}{2} + j\,\tfrac12\cos\frac{\Delta\varphi_Q}{2},
\qquad \Delta\varphi_{I,Q} = \frac{\pi u_{I,Q}}{V_\pi},
```

with $u$ the RF drive on top of the $-V_\pi$ bias. Each arm sweeps the field along its own axis, so
together they reach every point of the square. `voltage_for(i, q)` returns the drive of the
conventional IQ transmitter (Eqs. 2.46–2.47):

```math
u_I = \frac{2V_\pi}{\pi}\arcsin i, \qquad u_Q = \frac{2V_\pi}{\pi}\arcsin q,
```

which puts the field exactly at $`\tfrac12(i + jq)`$ for $i, q \in [-1, 1]$. The arcsin undoes the
MZMs' cosine, so the 16-QAM levels ±1/3 and ±1 come out equidistant. A linear drive with the same
±Vπ swing puts the inner level at $\sin(\pi/6) = \tfrac12$ instead of ⅓, and the outer-to-inner
ratio drops from 3 to 2.

Seimetz sets his phase electrode to $-V_\pi/2$. phoq's phases have the opposite sign, so quadrature
is at $+V_{\pi,\mathrm{DC3}}/2$. At the other null, $+V_\pi$, that axis comes out mirrored.

**Extinction and drift.** The MZMs' finite extinction leaves a carrier at the operating point of
$`|E|^2 = (1-k)/4 = 1/[2(\mathrm{ER}+1)]`$ of full transmission. For 30 dB MZMs that is 33 dB below
full. It also adds a quadrature term $`-j\sqrt{(1-k)/2}\,\sqrt{1-i^2}`$ that moves only the inner
levels; at 30 dB it adds about 2 % EVM to 16-QAM. The $\varphi_0$ are path imbalances that move
every bias point. A bias controller such as Exail's MBC-IQ would track them; phoq holds them fixed.

**Demo.** `examples/iq_modulator.py` sends 16-QAM through a 20 dB channel into a homodyne receiver:

```
                  FG-1 CH1, CH2: I and Q levels      PSU-1: DC1, DC2   PSU-2: DC3
                             │                               │
laser ─ TAP ─┬─ IQM (MXIQ-LN-30) ─ VOA (channel) ─ HYB-S ─┬──────────────── MIX-I ═ BPD-I → CH1
             │                                             └──────────────── MIX-Q ═ BPD-Q → CH2
             └─ 2 m ──────────────────────────────── HYB-L ─┬─ PS-I ───────── MIX-I
                                                            └─ PS-Q ───────── MIX-Q
```

The receiver is a 90° hybrid built from four 50:50 couplers and a phase shifter in each LO branch.
Both branches therefore lose the same; with unequal LO powers, the two quadratures would read the
square as a rectangle seen at an angle, which looks like a bias error. Each balanced detector reads
one quadrature of $`E_S E_\mathrm{LO}^*`$. The shifters are set as a homodyne receiver's phase lock
would set them, so those quadratures are the I and Q axes. They land on 0 and π/2 because each
detector's INPUT+ takes the mixer output where the signal and the LO have passed as many cross
ports, each adding a factor $j$; on the other output the lock would land on π and 3π/2. The LO is
the same laser, and every path is 4 m of cord, so the laser's phase noise cancels.

The DSP finds the frame from the known symbols and fits $`\mathrm{CH1} + j\,\mathrm{CH2} = a\,i + b\,q + c`$:

- the angle between $a$ and $b$ is the I/Q angle;
- $|b/a|$ is the Q/I gain;
- $c$ is the carrier left on the light.

In brackets, the model gives the same fit applied to `IQModulator.field()` of every symbol:

```
phoq run examples/iq_modulator.py

  case                                   EVM        I/Q angle       Q/I gain            carrier  outer/inner
  operating point             5.5 % ( 5.5 %)    90.0° ( 90.0°)  1.002 (1.000)  -34.9 dB (-34.3 dB)  3.01 (3.00)
  DC3 10° off quadrature     10.3 % (10.3 %)    99.9° (100.0°)  1.002 (1.000)  -35.7 dB (-35.1 dB)  3.04 (3.03)
  DC1 0.5 V off the null      8.1 % ( 8.0 %)    89.8° ( 89.7°)  1.012 (1.010)  -23.1 dB (-23.0 dB)  2.37 (2.36)
  linear drive, no arcsin    14.9 % (15.0 %)    90.2° ( 90.1°)  1.003 (1.002)  -35.2 dB (-34.9 dB)  2.01 (2.00)

  receiver noise alone: 5.1 % EVM.
```

Each bias error moves its own number:

- **DC3:** the I/Q angle.
- **DC1:** the carrier, and the inner I levels shift sideways.
- **A linear drive:** the outer-to-inner ratio.

That tells you which knob to turn before the EVM alone could. `--plot` saves the four
constellations. In `phoq serve examples/iq_modulator.py`, the scope shows the four I and Q levels at 200 ns/div,
and IQM-1's inspector shows the bias point in degrees and the carrier it leaves.

**Validation.** `tests/test_iq_modulator.py`:

| Check | Theory | phoq |
| --- | --- | --- |
| Field at the operating point over a 21 × 21 grid of drives | Seimetz Eqs. 2.16–2.18 | to 2·10⁻⁴ (80 dB MZMs) |
| 16-QAM and 64-QAM with the arcsin drive | ½(i + jq), Eqs. 2.46–2.47 | to 2·10⁻⁴ |
| One MZM: the arms in phase and driven alike | the intensity modulator's T(V) | to 10⁻¹² |
| Carrier at the operating point, MZMs of 20, 30 and 40 dB | (1 − k)/4 | exact |
| DC3 alone, both MZMs open | cos²(φ_P/2) | to 10⁻⁹, in the steady-state report |
| 16-QAM mean power: report against synthesized fields | 5/18 of the input | to 1 % |
| Through the receiver against the model, four cases | the fit on `field()` | angle 0.5°, gain 0.02, carrier 1.5 dB, outer/inner 0.06, EVM 8 % |

Not modeled:

- The RF bandwidth (25 GHz for the MXIQ-LN-30): drives act instantly, so keep the symbol rate well
  below it.
- Bias drift and bias control.
- The internal monitor photodiodes (option PD).
- Crosstalk between RF1 and RF2.
- Any imbalance of the outer splitter and combiner, which are taken as 50:50.
- Vπ against wavelength.
- Polarization: the device has PM fibers.

[1] M. Seimetz, *High-Order Modulation for Optical Fiber Transmission*, Springer Series in Optical
Sciences 143 (Springer, 2009), ch. 2 "Transmitter Design",
[doi:10.1007/978-3-540-93771-5_2](https://doi.org/10.1007/978-3-540-93771-5_2).
[2] Exail, *MXIQ-LN series: 1550 nm wide bandwidth IQ modulator*, datasheet ED8 (04/2026); presets
`exail/mxiq-ln-10` and `exail/mxiq-ln-30`.

### Optical delay line

`OpticalDelayLine` is a fiber-coupled free-space delay: a collimator, an air gap set by a
translation stage, and a second collimator. Patch cords come in 0.5 m steps (2.45 ns), so cords set
the coarse delay and the delay line trims it to a fraction of a pulse. Its settings:

- the dial `delay` τ, from 0 to `max_delay` (330 ps);
- the fixed `insertion_delay` τ₀ (100 ps: internal fiber and the shortest gap);
- the stage `resolution`;
- `insertion_loss_db` at the best-coupled gap (1 dB);
- the collimated `beam_waist` w₀ (250 µm).

These generic values are assumed, not from a datasheet; a preset replaces them.

**Delay and phase.** Air is nearly dispersionless (n ≈ 1.00027), so one τ sets both the group
delay and the carrier phase:

```math
\tau_g = \tau_0 + \tau, \qquad \varphi = \varphi_0 + 2\pi\nu\tau .
```

One fringe passes per optical period $1/\nu = \lambda/c$: 5.17 fs at 1550 nm, or 1.55 µm of air
path. A move of a few femtoseconds therefore scrambles an interferometer's phase. The delay line
sets timing; a `PhaseShifter` (a fiber stretcher or heater on a bench) sets and holds the phase,
and is relocked after every move of the dial. A dial set beyond the travel stops at its end, and
`report()` warns.

**Coherence.** In a Mach–Zehnder whose arms differ by δ, a laser of Lorentzian linewidth Δν gives
fringe visibility $`V = V_0\,e^{-\pi\Delta\nu|\delta|}`$. Here
$V_0 = 2|a_1a_2|/(|a_1|^2+|a_2|^2)$ is the ceiling left by unequal arm losses. Scanning the delay
line maps out the coherence time $1/(\pi\Delta\nu)$ of a broad source.

**Pulse overlap.** In a delay-line interferometer the arms differ by $T+\delta$, so an output slot
holds pulse $k$ and pulse $k-1$ offset by δ. The interference term counted over the slot is the
overlap of their fields, $`\int E_k(t)\,E_{k-1}(t-\delta)\,dt`$. For Gaussian pulses of intensity FWHM
$w$:

```math
V(\delta) = V_0\; e^{-\pi\Delta\nu\,(T+\delta)}\; e^{-\ln 2\,\delta^2/w^2} .
```

V ≥ 0.99 needs $`|\delta| \le 0.12\,w`$: ±120 ps for 1 ns pulses, but ±12 ps (2.4 mm of fiber) for
100 ps pulses. At 100 MHz a 3 m arm against a 1 m arm leaves δ = −205 ps. That costs 1 ns pulses
3 % of their visibility and leaves 100 ps pulses with 5 %, which is why the DLI's long arm carries a
delay line.

**Coupling loss.** The collimated beam (Rayleigh range $z_R = \pi w_0^2/\lambda$) couples into
the output fiber with the Gaussian mode overlap $1/[1+(d/2z_R)^2]$, where $d$ is how far the gap is
from the best-coupled one at mid-travel. The loss therefore rises toward both ends of the travel:

```math
\mathrm{IL}(\tau) = \mathrm{IL} + 10\log_{10}\!\left[1+\left(\frac{c\,(\tau-\tau_\text{max}/2)}{2\,n_\text{air}\,z_R}\right)^{2}\right].
```

With w₀ = 250 µm that is 0.16 dB at either end of the 330 ps travel at 1550 nm (0.12 dB at
1310 nm). Power changes while you scan for the visibility peak, so watch the visibility, not the
power.

The coupling law is the one Wang et al. (2024) derive for a fiber delay line, and phoq reproduces
their computed curves (see [Checked against the literature](../README.md#checked-against-the-literature)).

Not modeled: stage pitch and yaw (beam walk-off adds loss across the travel: for 12 mm lenses 1 m
apart, Wang et al. find that a 0.0085° tilt or a 0.86 mm offset of the coupling lens halves the
coupling), back-reflection from the collimator faces, thermal drift, backlash and repeatability.

**Counting pulses that miss each other.** The photon counter averages the light over each bin, and
two pulses inside one bin then interfere as if they overlapped. `PhotonCounter(oversample=m)`
synthesizes $m$ sub-bins per bin and adds their photons. A sub-bin of a quarter of the pulse width
gets the overlap right to 2 %, an eighth to 0.5 %. With sub-bins a click is also stamped where in
its bin the photon arrived, not at the bin's center.

**Validation.** `phoq run examples/odl_validation.py` checks each equation above against phoq's
steady-state `report()` and against fields synthesized by the engine; `tests/test_odl.py` asserts
the same checks. The DLI is the one in `examples/dli.py`, balanced at 105 ps on the dial:

| Check | Theory | phoq |
| --- | --- | --- |
| Group delay at dial 0 / 105 / 330 ps, and 400 ps (beyond the travel) | 100 / 205 / 430 / 430 ps | 100 / 205 / 430 / 430 ps, with a warning at 400 |
| 1 fs resolution, dial 123.4567 ps | 223.457 ps | 223.457 ps |
| Fringe period at 1550 / 1310 nm | 5.17024 / 4.36969 fs | 5.17024 / 4.36969 fs |
| MZI visibility, 1 GHz laser, δ = 100 / 200 / 300 / 430 ps | 0.725 / 0.530 / 0.387 / 0.257 | report 0.725 / 0.530 / 0.388 / 0.257; fields 0.725 / 0.532 / 0.390 / 0.255 |
| DLI visibility, 100 ps pulses, δ = −105 / −50 / 0 / +50 / +100 / +225 ps | 0.459 / 0.832 / 0.991 / 0.835 / 0.497 / 0.030 | report: identical; fields 0.460 / 0.832 / 0.991 / 0.835 / 0.498 / 0.030 |
| Standard cords alone (δ = −205 ps), 1 ns / 100 ps pulses | 0.961 / 0.053 | 0.961 / 0.053 |
| Time tagger, 1 ns bins, δ = +100 ps: oversample 1 / 40 | 0.496 | 0.983 (whole bins) / 0.501 |
| Loss at dial 0 / 82.5 / 165 / 330 ps re mid-travel, 1550 nm | 0.162 / 0.041 / 0 / 0.162 dB | 0.162 / 0.041 / 0.000 / 0.162 dB |
| The same at 1310 nm | 0.117 / 0.029 / 0 / 0.117 dB | 0.117 / 0.029 / 0.000 / 0.117 dB |

The steady-state report computes the pulse overlap in closed form; the synthesized fields reach the
same value from bin-averaged envelopes and one shared phase walk, which is the check on the engine.
`phoq serve examples/odl_validation.py` opens the 100 ps DLI with 20 GHz photodiodes on a 1 ns/div
scope. Turn `DLI-ODL`'s dial away from 105 ps and each port shows the two arms' pulses side by side,
no longer interfering.

### Balanced photodetector

`BalancedPhotodetector` is two matched photodiodes behind two FC receptacles, `INPUT+` (`inp_plus`)
and `INPUT−` (`inp_minus`). Their photocurrents subtract before a single transimpedance amplifier.
It has three SMA outputs: `RF OUTPUT` (`rf`) from that amplifier, and `MONITOR+` and `MONITOR−`
(`mon_plus`, `mon_minus`), each following one photodiode alone. The presets are Thorlabs' PDB450C
and PDB450A (switchable gain, 10³–10⁷ V/A) and PDB440C and PDB440A (fixed, 51 × 10³ V/A), taken from
their operation manual [1].

**RF OUTPUT.** Manual §3.3 gives the output into a high-impedance load:

```math
U_\mathrm{RF} = G\,\bigl(\mathcal R_+(\lambda)\,P_+ - \mathcal R_-(\lambda)\,P_-\bigr).
```

The output impedance is 50 Ω, so a 50 Ω load (a spectrum analyzer) gets half. The gain switch
(`gain`) selects one row of `gain_settings`, and each row carries its own −3 dB bandwidth, NEP and
output swing. A gain the unit does not offer is reported as an error. The output follows one pole at
that bandwidth and runs into its rails at ±swing, staying linear to 0.03 % up to 60 % of the swing.

Light that reaches only one input drives the output alone, so the CW saturation power is
$`U_\mathrm{swing}/(G\,\mathcal R)`$: 4.6 mW at 10³ V/A and 46 µW at 10⁵ V/A for the PDB450C. The
manual asks that the *difference* between the inputs stay below it.

**Common-mode rejection.** Light that reaches both inputs equally cancels, except for the CMRR
residue. phoq models the CMRR as the INPUT− diode's responsivity being short by
$`10^{-\mathrm{CMRR}/20}`$ (3.2 % at 30 dB). Equal powers therefore leave
$`U_\mathrm{RF} = G\,\mathcal R P\,10^{-\mathrm{CMRR}/20}`$, and every common-mode fluctuation is
reduced by the same factor: the laser's intensity noise, or a modulation both inputs share.

If the paths from the splitter to the two inputs differ by Δτ, a common-mode component at
frequency $f$ arrives at the two diodes with different phases. The cancellation then becomes
$`|1 - e^{-i2\pi f\Delta\tau}| = 2|\sin(\pi f\Delta\tau)|`$:

```math
\mathrm{CMRR}_\mathrm{path}(f) = -20\log_{10}\bigl(2\sin(\pi f\,\Delta\tau)\bigr).
```

This gives the curves of the manual's §3.7 figure, which is drawn for a path difference in air; in
fiber the allowed difference is 1.5 times smaller. 30 dB at 1 MHz allows 1 m of fiber between the
two cords; at 100 MHz it allows 1 cm. phoq's paths carry their own delays, so this needs no
parameter: a 1 m and a 2 m cord give 30.2 dB at 1 MHz and 10.3 dB at 10 MHz.

**Noise.** Each diode's photocurrent carries shot noise of density $`2q\,i_\pm`$. Behind a 50:50
coupler the two are uncorrelated, because each photon's path is random (RP Photonics, "Balanced
photodetection"). They therefore add in power on RF OUTPUT, even though the mean currents subtract.
The amplifier adds its NEP,
referred to the peak responsivity, and all of it passes through the same pole:

```math
\sigma_\mathrm{RF}^2 = G^2\bigl[2q\,(i_+ + i_-) + (\mathrm{NEP}\,\mathcal R_\mathrm{peak})^2\bigr]\,B,
\qquad B = \tfrac{\pi}{2}\,f_\mathrm{3dB}.
```

This is why balanced detection reaches the shot-noise limit with a strong local oscillator: the
oscillator's noise above the shot noise limit cancels, and its shot noise does not.

**Monitors.** Each monitor reads its own diode's current at 10 V/mW at peak responsivity
(`monitor_gain`), from DC to 1 MHz, up to 10 V into a high-impedance load. They therefore saturate
above 1 mW, as the manual warns. Each adds 180 µV rms of its own noise and has a 220 Ω output
impedance. The manual uses them to balance the two input powers before a measurement.

**Report.** `report()` lists for the detector:

- the power at INPUT+ and at INPUT−;
- RF OUTPUT, both into a high impedance and into 50 Ω;
- both monitors;
- the balance $`(P_+ - P_-)/(P_+ + P_-)`$;
- a status: Balanced, Single input, Saturated or No such gain.

It warns when RF OUTPUT or a monitor would pass its swing, and when one diode receives more than the
preset's 20 mW damage threshold.

**Demo.** `examples/balanced_detection.py` is a heterodyne receiver whose local oscillator is noisy:

```
LO laser, 8 dBm → IM (5 % rms, < 30 kHz) ───────┐            ┌─ 1 m → INPUT+    RF OUTPUT → CH1
                                                ├─ 50:50 ────┤                   MONITOR+  → CH2
signal laser, −30 dBm, +400 kHz ────────────────┘            └─ 1 m → INPUT−    MONITOR−  → CH3
```

The coupler's outputs carry $`P_\pm = \tfrac12(P_\mathrm{LO} + P_S) \pm \sqrt{P_\mathrm{LO}P_S}\cos(2\pi\Delta f\,t + \varphi)`$.
The LO and its noise are the same on both inputs, while the beat has opposite signs:

```
phoq run examples/balanced_detection.py

  INPUT+                       505 µW · -3.0 dBm
  INPUT−                       505 µW · -3.0 dBm
  RF OUTPUT                    +0.1597 V (high-Z), +0.07983 V into 50 Ω
  MONITOR+ / MONITOR−          5.049 V / 4.889 V
  Status                       Balanced

Scope, 100 µs sweeps:
  RF beat             0.535 V amplitude, expected 0.535 V (-0.1 %)
  MONITOR± beat       0.248 V on (MON+ − MON−)/2, expected 0.248 V (-0.2 %): opposite on the two monitors
  signal blocked      LO noise 260 mV rms on MONITOR+ (5.2 % of 5.05 V), the same on MONITOR− (correlation +1.00), 8.4 mV rms on RF
  LO noise rejection  30.0 dB (CMRR 30 dB)
  INPUT− dark         RF would sit at 5.05 V, beyond the ±4.6 V swing
```

RF OUTPUT shows the two diodes' beats added and the LO noise reduced by the CMRR. Without INPUT− the
half milliwatt of LO would saturate the amplifier at this gain. The script ends with the gain
switch: one step up, at 10⁵ V/A, the 5.3 V beat on a 1.6 V residue clips at the +4.6 V rail.
`phoq serve examples/balanced_detection.py` shows the three traces on the scope: the monitors move
together with the LO noise, while the beat on them is in antiphase.

**Validation.** `tests/test_balanced.py` checks the model against the manual and the equations
above:

| Check | Manual or theory | phoq |
| --- | --- | --- |
| RF OUTPUT for a 70:30 split: PDB450C at 1550 nm, PDB450A at 820 nm, PDB440C at 1310 nm | §3.3 | to 10⁻¹² in the report; the scope trace's mean to 1 % |
| Sign in single detector mode (§3.2) | + from INPUT+, − from INPUT− | + / − |
| CW saturation: PDB440C, PDB440A, PDB450C and PDB450A at 10³ V/A | 70 µW, 130 µW, 4.5 mW, 9 mW | 70.6 µW, 134 µW, 4.60 mW, 8.73 mW |
| Monitor conversion gain; monitor saturation | 10 V/mW; above 1 mW | 10 V/mW; 1.00 mW |
| CMRR measured as in §3.6 (one input against both, a sine at 1 and 30 MHz), CMRR 30 dB | 30 dB | 29.99, 30.06 dB |
| 1 m of fiber between the cords (Δτ = 4.90 ns) at 1, 3 and 10 MHz (§3.7) | 30.24, 20.70, 10.27 dB | 30.22, 20.69, 10.27 dB |
| RF noise at 10⁴ V/A: dark, then 50 µW and 400 µW on each input | 1.251, 1.337, 1.829 mV | 1.253, 1.337, 1.827 mV |
| Monitor noise, dark | 180 µV rms | 180 µV rms (±2 %) |
| A 1 MHz tone into the spectrum analyzer's 50 Ω | −0.760 dBm | −0.754 dBm |
| Demo: RF beat, monitor beat, LO noise rejection | 0.535 V, 0.248 V, 30 dB | 0.535 V, 0.248 V, 30.0 dB |

The model leaves out:

- AC-coupled versions (the -AC suffix).
- How a real unit's CMRR falls toward its bandwidth. phoq's mismatch is flat, so attenuating INPUT+
  by 3 % would cancel it at every frequency.
- The amplifier offset and its warm-up drift (§3.3).
- Spot size and power density on the diodes.
- Etalons, polarization and residual frequency modulation, which §3.7 lists as limits on the
  rejection.
- The monitors' swing into 50 Ω: phoq divides 10 V across 220 Ω and 50 Ω, giving 1.85 V, where the
  manual quotes 1.5 V.

The PDB440 has INPUT+ and INPUT− on opposite connectors to the PDB450 (§3.2). In phoq only the port
names matter.

[1] Thorlabs, *PDB440x and PDB450x Balanced Amplified Photodetectors, Operation Manual*, version 2.4
(8 April 2019), document 21709-D02, item M0009-510-1043. The product page,
[Balanced Amplified Photodetectors with Fast Monitor Output](https://www.thorlabs.com/balanced-amplified-photodetectors-with-fast-monitor-output),
lists the same gains, bandwidths and NEPs, and notes that the gain halves into 50 Ω loads. Its
headline CMRR for the PDB450 is ">25 dB"; the presets use the manual's typical 30 dB. The page lists
heterodyne detection, spectroscopy, OCT, optical delay measurements and THz detection as typical
applications. YB Photonics,
[Introduction and application of balance detector](https://www.ybphotonics.com/specials/introduction-and-application-balance-detector),
describes the same principle, two photodiodes followed by a differential amplifier, in coherent
wind lidar, distributed fiber vibration sensing and OCT.

### Oscilloscope

The signal chain is detector → analog front end → sampler → record → display and measurements.

**Front end.** The input bandwidth $f_\mathrm{BW}$ (`bandwidth`) is modeled as two identical
first-order poles at $`f_1 = 1.554\,f_\mathrm{BW}`$, chosen so that $|H(f_\mathrm{BW})|^2 = \tfrac12$:

```math
|H(f)| = \frac{1}{1 + (f/f_1)^2}, \qquad t_\mathrm{rise,10\text{–}90} \approx \frac{0.34}{f_\mathrm{BW}}
```

A 20 MHz front end passes a 10 MHz fundamental at 0.91, its third harmonic at 0.52 and its fifth at
0.28, so a 10 MHz square wave keeps most of its swing but loses its corners, and an SPD click
pulse of 5 ns, the default `pulse_width`, is smeared to about 13 ns. The photodiode's own one-pole
bandwidth acts before the scope's; the slower of the two dominates.

**Sampling.** For a timebase of $s$ s/div the window is $W = 10s$. The effective sample rate is
limited by the record memory $N_\max$ (`max_points`):

```math
f_s = \min\!\left(f_\mathrm{set},\; N_\max / W\right), \qquad N = f_s W, \qquad \Delta t = 1/f_s
```

so a 5 GS/s scope with 20 000 points runs at 5 GS/s up to 400 ns/div and drops to 2 MS/s at
1 ms/div, as a real instrument does. Internally the analog signal is computed on a grid
$\Delta t/k$ with $k = \lceil 6 f_\mathrm{BW}\Delta t\rceil$ (capped) so the front end is resolved,
then *point-sampled* at the sample instants with no anti-alias filter. A component at frequency
$f$ therefore appears at the folded frequency

```math
f_\mathrm{alias} = \left|f - f_s\,\mathrm{round}(f/f_s)\right|
```

A 9 MHz sine sampled at 10 MS/s reads 1 MHz; a 10 MHz sine sampled at 10 MS/s collapses to a flat
line. Keep $f_s \gt 2 f_\mathrm{BW}$ for faithful traces.

**Noise.** The photodiode's shot noise (density $2qI$) and thermal noise ($4k_BT/R$) pass through
its bandwidth like the signal, so their RMS is $\sqrt{2qIB}$ and $\sqrt{4k_BTB/R}$ with $B$ its
noise bandwidth; the scope's front end then narrows it, so the displayed noise RMS falls
roughly as $\sqrt{f_\mathrm{BW}}$ when the bandwidth is reduced — the usual reason to switch a
20 MHz limit in.

**Triggering.** *sync* places $t = 0$ on a laser pulse (one division of pre-trigger shows), *edge*
searches a window twice the record for the first rising crossing of the level on the source
channel, and *free* starts anywhere. The display marks the trigger instant with a triangle at the
top and, for edge mode, the level with a "T" arrow on the right edge. **Single shot** (`arm()`,
`single()`, the SINGLE button) waits for the first trigger, keeps that one record and stops.

**Measurements.** Mean, min, max, peak-to-peak and RMS come from the full record. Frequency is
measured from hysteretic mid-level crossings (±20 % of the swing) when the signal is clean and
well sampled (swing > 8× the sample-to-sample noise estimate); otherwise from the spectral peak
with parabolic interpolation, accepted only if that line carries over 20 % of the AC power — so a
coarsely sampled or aliased sine still reads its (aliased) frequency, while flat noise reads none.
Records longer than 2 000 points are drawn as a min/max envelope per screen column; the numbers
always use every sample.

**ADC.** With `adc_bits` > 0, each channel is digitized: 2^bits levels across the screen's 8
divisions about its offset, and samples off the screen read the end codes. `ScopeResult.codes(ch)`
returns the integer codes. At 0, the default, samples are exact.

**Not modeled:** ADC non-linearity and interleaving errors, equivalent-time sampling, trigger jitter
and holdoff, probe loading. On the modulators: the electrodes' capacitance and velocity mismatch, which make a
real EOM a low-pass on its drive (the generator's voltage is applied instantaneously); bias drift
and its controller; chirp of single-drive devices.

### RF spectrum analyzer

**What it shows.** At every frequency $f$ of the span, an electrical spectrum analyzer (ESA)
displays the power of its input inside the resolution bandwidth (RBW) around $f$, in dBm into its
50 Ω input:

```math
L(f) = 10\log_{10}\frac{\int S(f')\,|H_\mathrm{RBW}(f'-f)|^2\,df'}{1\ \mathrm{mW}}
```

with $S$ the one-sided power spectral density of the input [W/Hz]. A photodiode drives the input
with a current, so $S = R_\mathrm{in} S_i$; a generator drives it with a voltage, $S = S_v/R_\mathrm{in}$.
Two consequences an experimentalist uses all the time:

- a sine of amplitude $A$ shows a line of $A^2/2R_\mathrm{in}$, whatever the RBW;
- noise shows $S \cdot B_N$, with $B_N$ the RBW filter's noise bandwidth, so the noise floor moves
  10 dB per decade of RBW, and the density is $L - 10\log_{10}\mathrm{RBW}$ in dBm/Hz.

`units` picks what the screen shows: **dBm**, the power in the RBW above; **dBm/Hz**, that density
(a tone then moves with the RBW); or **dBc/Hz**, the density over the DC power at the input,
$S/P_\mathrm{DC}$ with $P_\mathrm{DC} = V_\mathrm{DC}^2/R_\mathrm{in}$. On a photodetector's output,
noise and DC pass through the same gain and divider, so dBc/Hz is the RIN of the detected light:

```math
\frac{S}{P_\mathrm{DC}} = \left(\mathrm{RIN} + \frac{2q}{I} + \frac{S_\mathrm{dark}}{I^2}\right)|H(f)|^2 + \frac{\mathrm{DANL}}{P_\mathrm{DC}}
```

the excess RIN, the shot-noise RIN, the detector's floor and the analyzer's, not corrected for the
detector's response $H(f)$ (Obarski and Hale's $C(f)$). An RF analyzer blocks DC, so on the bench
$V_\mathrm{DC}$ comes from a voltmeter or the detector's readout; phoq's analyzer reads it ahead of
its DC block. A DC smaller than the input's AC (the light off, a generator's sine) is no reference,
and the screen says so. The reference level is in the display units, and a change of units moves it so the
trace keeps its place. `SpectrumResult.level` stays dBm in the RBW and `psd()` W/Hz whatever the
units; `dc_power` is the dBc/Hz reference, so `psd()[1] / dc_power` is the RIN spectrum.

**How phoq computes it.** A swept-tuned analyzer mixes the input down with a swept local
oscillator, filters it with the RBW filter, detects the envelope and smooths it with the video
bandwidth. An FFT analyzer samples, windows, transforms and averages. For stationary signals both
estimate the same $L(f)$; phoq takes the FFT route, as modern analyzers do below a few MHz of
span:

1. **Input.** AC-coupled (the DC photocurrent is blocked), impedance $R_\mathrm{in}$ (`input_impedance`,
   50 Ω). A photodiode's current becomes $v = i R_\mathrm{in}$.
2. **Anti-alias and sampling.** The signal is computed at $4f_s$, low-passed by a polyphase FIR
   and decimated to $`f_s = 2.56\,f_\mathrm{stop}`$. Unlike the oscilloscope's point sampling,
   signals above the span do not fold into it.
3. **RBW is the record length.** Each FFT uses $N$ samples under a Hann window, whose
   equivalent noise bandwidth is $1.5 f_s/N$; phoq sets $N = 1.5 f_s/\mathrm{RBW}$, so one record
   lasts $1.5/\mathrm{RBW}$. The FFT is zero-padded four times, keeping a tone's level within
   0.1 dB wherever it falls between bins.
4. **Scaling.** $P_k = 2|X_k|^2 / (R_\mathrm{in}(\sum w_n)^2)$ gives a tone its true power and noise
   $S\cdot\mathrm{RBW}$: the RBW here is a noise bandwidth (an analog Gaussian RBW filter's noise
   bandwidth is about 1.06× its −3 dB width).
5. **Averaging.** Within a sweep, the video bandwidth (`vbw`) smooths the noise, as an analyzer's
   video filter does: $K$ records, 50 % overlapped (Welch), are power-averaged, with
   $`K = 0.475\,\mathrm{RBW}/\mathrm{VBW} + 0.62`$ (rounded), the count whose scatter matches a
   one-pole video filter on the power scale, after a Gaussian RBW filter. FFT analyzers emulate the
   VBW the same way. The noise trace then fluctuates by about $4.3/\sqrt{K}$ dB, and its level
   stays; an analyzer with its video filter on the log scale scatters 15–20 % more and reads noise
   2.5 dB low (see [Checked against the literature](../README.md#checked-against-the-literature)). With
   `auto_vbw`, VBW = RBW: one record. Across sweeps, the trace can be written fresh each sweep,
   averaged, or held at its maximum (`trace`). The trace average counts sweeps up to `sweep_count`
   (100, R&S's Sweep Count) and then runs on, as an analyzer's does in continuous sweep: each new
   sweep weighs 1/`sweep_count`, so the trace still follows a change on the bench. A parameter
   sweep (`Runner.sweep`) clears the trace at each step, so each reading holds that value alone.
6. **Display.** `points` frequencies from `start_freq` to `stop_freq`. Each point takes the bins in
   its bucket through the `detector`: *peak* (the largest: tones read right even when a bucket is
   wider than the RBW), *RMS* (`rms`, the mean power: right for noise) or *sample*. An analyzer's
   RMS detector takes its samples over its dwell on the point, so a slower sweep smooths it. phoq
   has no sweep time: its samples are the bins in the bucket and the records of the sweep. R&S's
   Average detector, a voltage average that reads noise 1.05 dB low, is not modeled. The top line is
   `ref_level`, in the display units (`units`), with `db_per_div` over ten divisions.
7. **Instrument noise.** The displayed average noise level (`noise_floor`, dBm/Hz, at 0 dB input
   attenuation) is added at the input as white noise; the photodiode's shot noise $2qI$ and thermal
   noise $4k_BT/R$ come on top, as on the bench. The input attenuator ahead of the first mixer
   raises that floor dB for dB, and the IF gain makes up for its loss, so a signal keeps its level.
   With `auto_attenuation`, as on an analyzer, the attenuation follows the reference level: the
   least of 0, 10, … 70 dB that keeps a tone at the top of the screen (in dBm, whatever the units)
   at or below `max_mixer_level`, −10 dBm, at the mixer. At the default Ref 0 dBm that is 10 dB,
   so the floor sits 10 dB above `noise_floor`. A reference level far above the signal can bury a
   detector's noise under the analyzer's: lower it, or set `attenuation` by hand.
   `SpectrumResult.attenuation`, the screen's header and the read-outs show the attenuation in use.
8. **Measurements.** The peak (frequency, level), the median of the trace (the noise floor when
   signals cover little of the span), the −3 dB width around the peak, the frequency where the
   trace has fallen by $N$ dB, the power integrated over the span (in dBc/Hz, relative to the DC:
   the RIN integrated over the span, in dBc), and, when the peak sits at the
   start of the span as a self-homodyne beat does, a Lorentzian fit (`fit_lorentzian()`).
   `fit_self_homodyne(tau_d)` fits the self-homodyne spectrum for any delay, below. Several
   sweeps combine with `SpectrumResult.average(results)`, and `Runner.average_spectrum(esa,
   component, param, values)` steps a parameter (a phase shifter) and averages the sweeps, each
   on a cleared trace so that every value weighs the same whatever the trace mode.

The cost of a sweep is about $`3.84\,f_\mathrm{stop}/\mathrm{RBW}\times(K+1)/2`$ samples (×4 before
decimation), so a narrow RBW over a wide span is slow, as on a real analyzer, and so is a narrow
VBW.

### Laser linewidth by delayed self-homodyne

phoq's laser phase is a Wiener process, $\langle(\phi(t+\tau)-\phi(t))^2\rangle = 2\pi\Delta\nu|\tau|$,
which gives a Lorentzian optical spectrum of full width $\Delta\nu$ (`linewidth`) and a coherence
time $\tau_c = 1/\pi\Delta\nu$. A delayed self-homodyne measures $\Delta\nu$ without a narrower
reference laser: a coupler splits the light, one arm runs through a delay $\tau_d$ (a fiber spool),
a second coupler recombines the arms on a photodiode, and the ESA looks at the photocurrent. The
review by J.-Q. Chen et al., *Sensors* 24, 3656 (2024), [doi:10.3390/s24113656](https://doi.org/10.3390/s24113656),
covers this method (§2.2, Fig. 1b) and its relatives; the section references below are to it.
With $P_1$ and $P_2$ the powers each arm brings to the detector:

```math
i(t) = \mathcal{R}\left[P_1 + P_2 + 2\sqrt{P_1P_2}\cos\!\big(\theta + \phi(t) - \phi(t-\tau_d)\big)\right]
```

For $|\tau| \lt \tau_d$ the two phase increments in $\phi(t+\tau)-\phi(t)$ and
$\phi(t+\tau-\tau_d)-\phi(t-\tau_d)$ cover disjoint intervals, so they are independent. When
$\tau_d \gg \tau_c$ the arms are mutually incoherent, the bias phase $\theta$ washes out, and the
photocurrent's autocovariance and one-sided spectrum are

```math
C_i(\tau) = 2\mathcal{R}^2P_1P_2\,e^{-2\pi\Delta\nu|\tau|}, \qquad
S_i(f) = \frac{4\mathcal{R}^2P_1P_2}{\pi}\,\frac{\Delta\nu}{\Delta\nu^2 + f^2}
```

The RF spectrum is the optical lineshape convolved with itself, a Lorentzian of half width
$\Delta\nu$ centered at 0 Hz. In homodyne (no frequency shift) it folds about DC, so **the −3 dB
frequency equals the laser linewidth**. The heterodyne version, with an acousto-optic shift
$f_\mathrm{AOM}$, shows the same Lorentzian around $f_\mathrm{AOM}$ with a full −3 dB width of $2\Delta\nu$.
The level falls 20 dB at $`f = \sqrt{99}\,\Delta\nu \approx 9.95\,\Delta\nu`$. This is the review's rule
that the beat is twice as wide as the laser (§2.2; §2.3.1 for the heterodyne case), and its
"−20 dB width divided by $2\sqrt{99}$" (§2.3.4), which is $\sqrt{99}$ on a one-sided homodyne trace.
For phoq's laser, which has white frequency noise only, a Lorentzian fit
$S_0/(1+(f/\Delta\nu)^2) + S_\mathrm{floor}$ uses the whole trace and is the most robust estimate. On
a real laser, 1/f frequency noise adds a Gaussian component that widens the −3 dB width most and
the −20 dB width least; the review therefore measures at −20 dB, or fits a Voigt profile that
separates the Lorentzian part (§2.3.4, Fig. 3, after M. Chen et al., *Opt. Express* 23, 6803 (2015)).
The integral of $S_i$, $2\mathcal{R}^2P_1P_2$, checks the arm balance: it is largest for $P_1 = P_2$.

**With a reference laser.** The heterodyne method beats the laser under test against a second,
frequency-offset laser (§2.1, Eq. (4), Fig. 2). Lorentzians convolve, so the beat is a Lorentzian
at the offset with full width $\nu_t + \nu_r$: twice the linewidth for two equal lasers, the tested
linewidth for a much narrower reference. Two phoq `Laser`s at the same wavelength with different
`detuning` reproduce it: fitted full widths of 197, 100 and 79 kHz for 100 + 100, 100 + 1 and
60 + 20 kHz (`fit_lorentzian(center=None)`).

**Choosing the delay.** For any delay, averaged over the bias phase $\theta$ (or around
$f_\mathrm{AOM}$ in the heterodyne version), the spectrum is the Lorentzian above times a fringe term
(§4.2.1, Eqs. (28)–(31), after L. Richter et al., *IEEE J. Quantum Electron.* 22, 2070 (1986)):

```math
S_i(f) = \frac{4\mathcal{R}^2P_1P_2}{\pi}\,\frac{\Delta\nu}{\Delta\nu^2+f^2}\Big\lbrace 1 - e^{-2\pi\Delta\nu\tau_d}\Big[\cos 2\pi f\tau_d + \frac{\Delta\nu}{f}\sin 2\pi f\tau_d\Big]\Big\rbrace
```

plus a coherent line at the center of weight $e^{-2\pi\Delta\nu\tau_d}$ (at 0 Hz in homodyne, where the
AC-coupled input blocks it). The fringes have period $1/\tau_d$ and depth $e^{-2\pi\Delta\nu\tau_d}$, the
coherence left between the arms. Averaged over eight settings of a `PhaseShifter` in the short arm,
phoq's spectrum matches this expression in absolute level to 0.07 dB rms from 20 kHz to 1.5 MHz,
for 0.3 km (fringe depth 0.40) and for 1 km (0.046). Below about 1 %
($`\tau_d \gt 0.73/\Delta\nu \approx 2.3\,\tau_c`$) the fringes are lost in the averaging noise and the
lineshape is the Lorentzian; the review asks for a delay above six coherence times (§2.3.3), which
keeps the depth below 0.25 % in any of the conventions for $\tau_c$. Conventions differ: here
$\tau_c = 1/\pi\Delta\nu$, the decay time of the field correlation; the review writes $\Delta\nu = 1/\tau_c$
in §2.2 and $e^{-\tau_0/\tau_c}$ in Eq. (10). The depth $e^{-2\pi\Delta\nu\tau_d}$ is convention-free.

In the limit $\tau_d \ll \tau_c$ the interferometer is a frequency discriminator: it turns frequency
noise into intensity noise, $`\phi(t)-\phi(t-\tau_d) \approx 2\pi\tau_d\,\delta\nu(t)`$, and with the
review's Eq. (20), $S_{\Delta\varphi}(f) = 4[\sin(\pi f\tau_d)/f]^2 S_F(f)$ for white frequency noise
$S_F = \Delta\nu/\pi$, the beat spectrum at quadrature becomes

```math
S_i(f) \propto \Delta\nu\,\frac{\sin^2(\pi f\tau_d)}{f^2}
```

a sinc² with nulls at multiples of $1/\tau_d$ whose width is set by the delay, not by the laser:
the −3 dB point sits at $0.443/\tau_d$, and the linewidth is overestimated. For 100 kHz the delay
must exceed 7.3 µs, about 1.5 km of SMF-28 (19 µs, 3.9 km, by the six-coherence-time rule); 10 km
gives $e^{-31}$.

**Choosing the analyzer settings.** RBW ≤ Δν/10, so the RBW filter does not widen the line; a
span of at least 20 Δν, so the wings and the floor are both on screen; the *RMS* detector and
a VBW of RBW/100 or less, or a trace average, because the beat is noise, not a tone. The review
makes the same points about the analyzer's resolution and sweep time, and asks for repeated
measurements reported with their mean and standard deviation (§4.1).

**Try it.** `examples/linewidth_homodyne.py` builds the bench above — a 100 kHz laser, two 50:50
couplers, a 10 km spool, an attenuator that gives the short arm the spool's loss, a photodiode and
the analyzer at 0–2 MHz, RBW 5 kHz, VBW 1 Hz, RMS detector — and measures:

```bash
phoq run examples/linewidth_homodyne.py --plot dsh.png
```

```
laser linewidth 100 kHz (coherence time 3.18 µs); delay 49 µs = 15 τc, coherence left e^(−2πΔν·τd) = 4.3e-14

  Lorentzian fit       Δν =  99.5 kHz  (-0.5 %)
  −3 dB point          Δν =  95.8 kHz  (-4.2 %)
  −20 dB point / √99   Δν =  97.6 kHz  (-2.4 %)

The delay must beat the coherence time (VBW 2 Hz each):
    spool        τd  τd/τc coherence        fit      −3 dB
    0.3 km   1.47 µs    0.5       0.4    137 kHz    298 kHz
    0.6 km   2.94 µs    0.9      0.16    141 kHz    202 kHz
      1 km    4.9 µs    1.5     0.046    101 kHz    121 kHz
      2 km   9.79 µs    3.1    0.0021    100 kHz   98.3 kHz
     10 km     49 µs   15.4   4.3e-14    100 kHz   94.4 kHz
     25 km    122 µs   38.5   3.9e-34    101 kHz   90.7 kHz
```

The measured spectrum matches $S_i(f)$ above in absolute level to within about 0.3 dB from 1 kHz
to 1 MHz. Two lessons from the numbers: the −3 dB point of a noise-like trace scatters by several
per cent even at VBW 2 Hz, over a thousand records a sweep (it is one crossing on a flat top), so
fit the whole trace; and a delay shorter than about two coherence times reports the delay's sinc²
instead of the laser (0.3 km: the −3 dB point sits at $0.443/\tau_d$ = 301 kHz). `phoq serve
examples/linewidth_homodyne.py` shows the same bench live: the analyzer's trace keeps averaging,
the window marks the −3 dB point and reports the Lorentzian half width.

**Narrow lines: 1 kHz and 100 Hz.** Five coherence times are 1.6 ms of delay for 1 kHz, 330 km of
fiber: one spool, and the Lorentzian fit works. For 100 Hz they are 16 ms, 3,300 km and over 600 dB
of loss, beyond any spool (500 km at most here, and the tracer drops paths below −180 dB) and any
bench without amplifiers. The way out is a delay shorter than the coherence time and a fit of the
expression above with the known delay, `fit_self_homodyne(tau_d)` (the review's coherent-envelope
methods, §4.2, rest on the same Eqs. (28)–(31)). Two conditions make it work:

- **Average over the bias phase.** The expression is the average over $\theta$, and phoq's fiber
  phase does not drift as a bench's does. The θ-dependent part varies as $\cos 2\theta$ and
  $\sin 2\theta$, so spectra taken at θ and θ + π/2 (a `PhaseShifter` in the short arm,
  `Runner.average_spectrum`) cancel it. One phase alone is biased either way, by tens of per cent.
- **Show several fringes.** For $\tau_d \ll \tau_c$ the linewidth only sets the fringe depth
  $e^{-2\pi\Delta\nu\tau_d}$, so the span must cover several periods $1/\tau_d$.

`examples/linewidth_narrow.py` measures both lasers. The short arm is left unattenuated: over
hundreds of km a balanced pair would sit below the analyzer's floor, while the beat scales as
$\sqrt{P_\mathrm{short}P_\mathrm{long}}$. The analyzer sits at the least VBW, 1 Hz, which at an RBW
of 10 Hz is only five records a sweep, so the script averages 60 sweeps at each bias phase (10 at
1 kHz), as a trace average would on the bench.

```bash
phoq run examples/linewidth_narrow.py --plot narrow.png
```

```
1 kHz laser, coherence time τc = 318 µs
    spool   loss  τd/τc  depth     RBW     span  short-delay fit     one θ Lorentzian     −3 dB
    100 km   19 dB   1.54   0.05  100 Hz   20 kHz           998 Hz     1 kHz   1.03 kHz  1.28 kHz
    200 km   38 dB   3.08   0.00  100 Hz   20 kHz         1.02 kHz  1.01 kHz   1.02 kHz  1.04 kHz
    400 km   76 dB   6.15   0.00  100 Hz   20 kHz         1.01 kHz  1.02 kHz   1.01 kHz  1.01 kHz

100 Hz laser, coherence time τc = 3.18 ms
    spool   loss  τd/τc  depth     RBW     span  short-delay fit     one θ Lorentzian     −3 dB
     50 km   10 dB   0.08   0.86   10 Hz 20.4 kHz           101 Hz     33 Hz     655 Hz  1.69 kHz
    100 km   19 dB   0.15   0.74   10 Hz 10.2 kHz          99.9 Hz   69.5 Hz     369 Hz    874 Hz
    200 km   38 dB   0.31   0.54   10 Hz  5.1 kHz          99.2 Hz   35.9 Hz     218 Hz    449 Hz
    500 km   95 dB   0.77   0.22   10 Hz 2.04 kHz          98.5 Hz   63.1 Hz     128 Hz    193 Hz
```

At 1 kHz every column agrees once the delay passes a few $\tau_c$. At 100 Hz only the short-delay
fit on phase-averaged spectra finds the laser, from 50 km (a tenth of a coherence time) up; the
Lorentzian and the −3 dB point report the delay instead. In `phoq serve
examples/linewidth_narrow.py` the analyzer window still shows the plain Lorentzian half width,
which assumes $\tau_d \gg \tau_c$; the short-delay fit runs from Python.

**What the model leaves out.** Only white frequency noise: real lasers also have 1/f (flicker)
frequency noise, which adds a Gaussian part that grows with the delay and turns the lineshape into
a Voigt profile (§2.3.4, §4.1). Perfect polarization overlap: a real setup needs a polarization
controller in one arm (or Faraday mirrors, §2.3.2), and a mismatch scales the beat by the overlap.
No low-frequency environmental noise: on a bench, temperature and vibration add noise near 0 Hz,
right under the homodyne beat. That is why the self-heterodyne, which moves the beat to
$f_\mathrm{AOM}$, is the more common method (Table 1, §3.2, §4.2); phoq has no acousto-optic frequency
shifter yet, so only the homodyne version can be built. No dispersion, nonlinearity or Rayleigh
backscattering in the spool (§5 notes double Rayleigh scattering distorts the lineshape).

### Laser intensity noise and shot noise

`Laser(rin_db_hz=…)` gives a laser relative intensity noise above the shot noise limit, its excess
RIN (**Excess RIN** in the lab): a white one-sided PSD of δP/P̄ in dBc/Hz, one realization per
laser shared by all its paths. Shot noise is drawn at
detection, so a photodiode reads $`S_I = \mathcal R^2\bar P^2\,\mathrm{RIN} + 2q\bar I + 4k_BT/R`$.
`examples/laser_noise.py` measures both with a PDB450C at 10⁵ V/A and the spectrum analyzer:

```
laser (excess RIN −135 dBc/Hz) → VOA → 50:50 ─┬─ 1 m → INPUT+    BPD-1 (PDB450C, 10⁵ V/A)
                                              └─ 1 m → INPUT−    RF OUTPUT → spectrum analyzer
```

| Measurement | Expected | phoq |
| --- | --- | --- |
| Dark | (NEP·ℜ)² = −236.2 dB re 1 A²/Hz | −236.2 dB |
| One input: fit of a·I + b·I², 1.8–28 µA | b = RIN = −135 dBc/Hz, a = 2q | −135.03 dBc/Hz; q to −1.8 % |
| Both inputs, 4 µA to 1.1 mA | the difference at 2q(I₊ + I₋) | within ±0.3 dB; q to −0.8 % |
| The same 1.1 mA on one diode | 20 dB above the shot noise | 20.2 dB |
| Dark-noise clearance at 1.1 mA | shot over amplifier noise | 21.7 dB |

One input bends from the shot noise to the laser's intensity noise at 2q/RIN = 10 µA. The
difference current stays at the shot noise, because the RIN reaches both diodes alike and cancels
to the CMRR residue. The two diodes' shot noises are uncorrelated and do not cancel.
[examples/laser_noise.md](../examples/laser_noise.md) explains where each noise enters phoq's code,
checks the terms against the RP Photonics articles, and has the full tables and plot.
[tutorial-laser-noise.md](tutorial-laser-noise.md) does the same measurements by hand in
the 3D lab, with no Python: the floor, shot noise, RIN read in dBc/Hz, balanced detection and its
CMRR limit, the analyzer's RBW, and an open spare port.
`tests/test_laser_noise.py` checks the photodiode against $`2qI + 4k_BT/R + \mathrm{RIN}\,I^2`$
to 0.1 dB, the variance of δ per bin, and that the noise is shared by every path.

## Server API

`phoq serve …` starts FastAPI. Interactive docs are at `/docs`.

| Method | Path | |
| --- | --- | --- |
| GET | `/api/catalog` | component types, ports, parameter specs, part presets per type |
| GET | `/api/presets`, `/api/presets/{id}` | part presets (`?component=`, `?vendor=`, `?q=`); one in full with provenance, limits and unsupported specs |
| POST | `/api/instruments/{name}/clear` | restart a spectrum analyzer's trace averaging or max hold |
| POST | `/api/components/{name}/preset` | `{id}` applies a preset (`generic` for the class defaults) |
| GET | `/api/state` | the setup: components, parameters, connections |
| GET | `/api/report` | mean powers, currents, warnings, and per laser the back-reflection and its strongest source |
| POST | `/api/components` | `{type, name?, params?}` adds a part |
| PATCH | `/api/components/{name}` | `{params}` sets attributes (422 on invalid values) |
| DELETE | `/api/components/{name}` | removes it and its connections |
| POST | `/api/connections` | `{a, b}` connects ports (409 if either is in use) |
| POST | `/api/disconnect` | `{port}` |
| POST | `/api/instruments/{name}/autoset` | oscilloscope autoset |
| POST | `/api/run` | acquire all instruments once |
| POST | `/api/save` | write the autosave file now |
| GET | `/api/export/python`, `/api/export/json` | the setup as code or JSON |
| GET / POST | `/api/tasks` | analysis tasks; POST `{path}` loads a script's `@task` functions |
| POST | `/api/tasks/{name}/{action}` | `restart`, `stop`, `start` or `clear` a task |
| WS | `/ws` | streams `state`, `report`, `frame` (~15/s), `counter` (~1/s) and `console` (task output) |

## Development and releases

```bash
uv sync --all-extras && uv run pytest -q
```

To release: set `version` in `pyproject.toml`, commit, tag it `vX.Y.Z` and push the tag. The
publish workflow checks that the tag matches the version, runs the tests, builds the sdist and
wheel with `uv build` and uploads them with PyPI *trusted publishing*, so no token is stored in
GitHub. One-time setup: on PyPI (Account → Publishing) add a trusted publisher for the project
`phoq` with this repository, workflow `publish.yml` and environment `pypi`, and create the `pypi`
environment under the repository's Settings → Environments.
