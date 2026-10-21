# phoq

[![CI](https://github.com/ammar-faifi/phoq/actions/workflows/ci.yml/badge.svg)](https://github.com/ammar-faifi/phoq/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/phoq.svg)](https://pypi.org/project/phoq/)

**phoq** (photon + q, it sounds like Fock) is a quantum optics and quantum-communication lab for Python. Build fiber-optic experiments from parts — lasers, modulators, couplers, attenuators, photodiodes, single-photon
detectors, function generators, time taggers, oscilloscopes — run them, post-process the clicks
with your own scripts, and run multi-party protocols such as QKD with every party in its own
process talking over real sockets. A 3D virtual lab in the browser shows the same Python objects
live and lets you edit them.

![The phoq virtual lab running the MP-QKD example: Alice's and Bob's transmitters, Charlie's coupler and detectors, the instrument shelf, and one laptop per party wired to a switch](https://raw.githubusercontent.com/ammar-faifi/phoq/main/docs/images/virtual-lab.webp)

*`phoq serve --protocol mpqkd`: Alice and Bob's transmitters, Charlie's coupler and single-photon
detectors, the generators on the shelf, and a laptop per party on the classical network. The
inspector shows the selected detector's parameters and the live click statistics computed in Python.*

## Install

```bash
pip install phoq
```

`pip install "phoq[plot]"` adds matplotlib for the plotting helpers. Python 3.14 or newer.
Inside this repository, `uv sync` installs everything and commands are run with `uv run …`.

## Quick start

```python
from phoq import Setup, Laser, Fiber, Photodiode, Oscilloscope

laser = Laser(power_dbm=-7, mode="pulsed", rep_rate=10e6, pulse_width=5e-9)
setup = Setup.from_chain(laser, Fiber(length=2), Photodiode(bandwidth=1e9), Oscilloscope(timebase=20e-9))

print(setup.run().summary())     # OSC-1: ch1 ← PD-1: mean 163 µA, pk-pk 2.99 mA, f 10 MHz
laser.power_dbm = 0              # parameters are plain, validated attributes
setup.serve()                    # optional: http://127.0.0.1:8000 shows it live in 3D
```

```bash
phoq run examples/delay_line.py              # headless
```

```bash
phoq serve examples/basic_link.py            # the virtual lab at http://127.0.0.1:8000
```

```bash
phoq serve --protocol examples/mpqkd         # MP-QKD: Alice, Bob and Charlie as processes
```

```bash
phoq run --protocol bb84 --party examples/bb84/eve.py   # decoy-state BB84 with an eavesdropper
```

## Concepts

| | |
| --- | --- |
| **Component** | A piece of equipment. Settings are `Param` class attributes, so `laser.wavelength = 1310e-9` is validated against the type, range and choices, and is observable. |
| **Port** | `laser.out`, `pd.inp`, `pd.rf`, … Each is `optical` or `electrical`. |
| **Setup** | Owns named components and port connections. `connect(a, b)` joins two ports. Two bulkhead optical ports automatically get a 1 m `Fiber` patch cord between them. `chain(...)` wires items one after another. |
| **Runner** | `setup.run()` acquires every instrument. `Runner(setup).sweep(comp, "param", values, measure)` sweeps a parameter. |
| **Instruments** | `Oscilloscope` (traces + measurements; selectable analogue `bandwidth`, `sample_rate` and record memory `max_points`; `Runner.single(osc)` waits for the first trigger and holds that sweep), `PhotonCounter` (click record per pulse), `PowerMeter`. |

Units are SI throughout: metres, seconds, hertz and watts. Powers use `power_dbm` and losses `*_db`.

### Components

| Category | Classes |
| --- | --- |
| Sources | `Laser` (CW / pulsed / sine / OOK, linewidth, detuning, per-pulse phase randomisation) |
| Fiber | `Fiber` (patch cord), `FiberSpool` |
| Passive | `Attenuator`, `Coupler` (2×2 fused fiber coupler; `BeamSplitter` is its deprecated old name), `PhaseShifter`, `Isolator`, `MatingSleeve` (FC–FC adapter joining two cords), `OpticalDelayLine` (variable free-space delay: dial, carrier phase, Gaussian-beam coupling loss) |
| Modulators | `PhaseModulator` (φ = π·V/Vπ), `IntensityModulator` (Mach–Zehnder, extinction) — voltage comes only through their `rf` and `bias` inputs; `OpticalSwitch` (2×2, bar below the threshold voltage on `ctrl`, cross above: a pattern routes pulse by pulse) |
| Detectors | `Photodiode`, `SinglePhotonDetector`, `PowerMeter` |
| Instruments | `Oscilloscope`, `SpectrumAnalyzer` (RF spectrum in dBm: RBW, averaging, detectors, trace average and max hold, Lorentzian and short-delay linewidth fits), `PhotonCounter` (time tagger: per-channel input delays, sub-bins for pulses shorter than a bin), `FunctionGenerator` (2 outputs: dc / sine / square / triangle / ramp / loaded pattern), `DCPowerSupply` (2 outputs, ±30 V) |

Run `phoq catalog` for every port and parameter.

### Fiber connectors

Every fiber port has a connector type, following the usual colour code: **FC/UPC blue**, **FC/APC
green**, and FC/PC (the older flat polish, as many datasheets still list it) blue too. A part's
`connector` sets all its fiber ports; a `Fiber` patch cord has `connector_a` and `connector_b`, so
a cord with one of each is a hybrid cord. `setup.connect()` between two bulkheads picks the cord a
lab would take, with ends that match the two ports. In the 3D lab connector housings and boots,
and the collar of every bulkhead, show the colour. While you carry a connector, free ports where
it would make an angled-to-flat mating light up amber instead of cyan.

| Mated pair | Loss | Reflected back |
| --- | --- | --- |
| FC/UPC ↔ FC/UPC | 0.3 dB | −50 dB |
| FC/APC ↔ FC/APC | 0.3 dB | −60 dB |
| FC/PC ↔ FC/PC or FC/UPC | 0.3 dB | −40 dB (the worse face) |
| FC/APC ↔ FC/PC or FC/UPC | ≈ 5 dB, assumed | 3.6 % (−14.4 dB) into the flat side, −60 dB into the angled side |
| open FC/UPC or FC/PC end | — | 3.6 % back into the fiber |
| open FC/APC end | — | −60 dB |

The matching-pair values are typical specifications (`phoq.units.RETURN_LOSS_DB`,
`CONNECTOR_LOSS_DB`). An angled face against a flat one fits mechanically in an FC adaptor but
leaves an air gap. The flat face then reflects the full glass–air Fresnel 3.6 % straight back,
and the loss (`MISMATCH_LOSS_DB`) depends on the ferrule geometry. The 5 dB is an assumed value
to replace with your own measurement; the lab warns about any such mating whatever the number.

Reflections are traced (one per path): the inspector of every laser shows the setup's return
loss as the laser sees it and names the strongest reflection, e.g. `open FC/UPC end FIB-3.b
(−15.0 dB)`. Light reflected into a detector through a coupler, as in a reflectometer, is counted
like any other path, and interferes with it.

**Mating sleeves.** A `MatingSleeve` is an FC–FC adapter: plug a cord into each side and the two
cords' connectors mate inside it as one pair, with the loss and reflection of their two types. It
has no polish of its own, so a FC/APC cord meeting a FC/UPC one in a sleeve gets the same warning
as anywhere else. With one side empty, the other cord's end is an open connector. Cords plug
straight into a sleeve; between a sleeve and a part's bulkhead, or between two sleeves,
`connect()` inserts a cord, since two female sides cannot mate. Cascading cords shows what every
connection costs:

```bash
python examples/connector_cascade.py
```

```
                                       matings   at the PD   current back to LD  strongest reflection
1 cord                                       2   -0.60 dBm    827 µA   -47.3 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
4 cords, 3 sleeves                           5   -1.50 dBm    672 µA   -44.1 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
8 cords, 7 sleeves                           9   -2.70 dBm    510 µA   -42.6 dB  LD-1.out (FC/UPC) ↔ FIB-1.a (FC/UPC) (-50.0 dB)
4 FC/APC cords, APC laser and PD             5   -1.50 dBm    672 µA   -54.1 dB  LD-1.out (FC/APC) ↔ FIB-1.a (FC/APC) (-60.0 dB)
4 cords, one FC/APC among FC/UPC             5  -10.90 dBm   77.2 µA   -15.6 dB  FIB-2.b (FC/UPC) ↔ FIB-3.a (FC/APC) in SLV-2 (-15.6 dB)
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

A preset sets a component up as a specific product, with every number traced to its datasheet:

```python
im = IntensityModulator("IM-1", preset="exail/mx-ln-10")   # Vπ 5.5 V (RF) / 4.2 V (DC), 22 dB ER, 3.5 dB
pd = Photodiode(preset="thorlabs/det08cfc", load=50)        # explicit arguments override the preset
setup["DET-L"].apply_preset("mpd/pdm-ir")                   # on an existing part; "generic" goes back to the defaults
print(im.preset, im.provenance()["v_pi"])                   # exail/mx-ln-10 {'kind': 'datasheet-typ', 'spec': 'Vπ RF …', 'max': 6.0, …}
```

Only the parameters a preset lists are set; operating settings (a laser's power, a scope's
timebase) are left alone. Every class has a `generic` preset — its defaults — and a part stays
`generic` until you give it one. Saved JSON and **Export .py** keep the preset id plus your edits.
In the virtual lab the inspector has a part picker, and each datasheet-backed value carries a
badge (`typ`, `max`, `derived`, … and `edited` once you change it) whose tooltip quotes the
datasheet row. Absolute maximum ratings from the preset are checked live: too much optical
input, a bias outside its range or an RF drive above the rated power shows up as a warning.

```bash
phoq presets                       # every preset: id, class, title
```

```bash
phoq presets exail/mx-ln-10        # values with provenance, limits, and what the model cannot represent
```

Shipped presets, each citing the datasheet document and revision it was read from:

| Preset | Part | Class | Sets |
| --- | --- | --- | --- |
| `exail/mx-ln-0.1`, `-05`, `-10`, `-20`, `-40` | Exail MX-LN series LiNbO₃ intensity modulators, 400 MHz – 40 GHz | `IntensityModulator` | Vπ of the RF and of the DC electrodes, insertion loss, extinction ratio; maximum optical, RF and bias inputs |
| `mpd/pdm-ir` | MPD PDM-IR InGaAs/InP SPAD module | `SinglePhotonDetector` | PDE at 1550 nm with the PDE-vs-wavelength curve, dark-count rate, hold-off |
| `excelitas/spcm-aqrh-10-fc` … `-16-fc` | Excelitas SPCM-AQRH Si SPAD modules with FC adaptor, one per dark-count grade (< 1500 … < 25 c/s) | `SinglePhotonDetector` | PDE at 650 nm with the 400–1060 nm curve, dark counts, 22 ns dead time; maximum 10⁴ photons per pulse |
| `thorlabs/det08cfc` | Thorlabs DET08CFC 5 GHz InGaAs detector | `Photodiode` | bandwidth, responsivity at 1550 nm with the spectral curve, load, saturation from the 2 V output limit |
| `thorlabs/dx25cf` | Thorlabs DX25CF 25 GHz GaAs detector | `Photodiode` | GaAs response, 0.5 A/W at 850 nm, 25 GHz, 25 Ω load; 10 dBm input limit |
| `thorlabs/dx25hf`, `dx25ha` | Thorlabs DX25HF (FC/PC) and DX25HA (FC/APC) 25 GHz high-linearity InGaAs detectors | `Photodiode` | 0.45 A/W at 1550 nm with the 0.915–1.625 µm curve, 25 GHz, 25 Ω load, linear to 50 mA; 20 dBm input limit |
| `fs/smfcsx-upc-upc`, `-apc-apc`, `-apc-upc` | FS SMFCSX 1 m OS2 (G.657.A1) simplex patch cords, SKUs 40740, 173580 and the 173581 hybrid | `Fiber` | connector A and B, 1 m length; ≤ 0.3 dB and UPC ≥ 50 / APC ≥ 60 dB, the mating table's values |

Every preset also sets the part's connector from its datasheet: FC/PC for the Thorlabs, MPD and
Excelitas detectors, FC/APC for the Exail modulators (their FA ordering option; the default is
bare fiber).

**Writing one.** A preset is a TOML file whose id is `vendor/part` (the vendor slug and the file
name, unless `id` is given):

```toml
component = "IntensityModulator"
title = "Exail MX-LN-10"
vendor = "Exail"
part_number = "MX-LN-10"
description = "10 GHz LiNbO₃ X-cut Mach–Zehnder intensity modulator"
tier = "datasheet"                        # datasheet | validated (checked against a unit) | vendor

[source]
title = "MX-LN series datasheet"
document = "exail-datasheet-mx-ln-series-linbo3-optic-modulators.pdf"
revision = "ED15 (05/2026)"
accessed = 2026-10-16
conditions = "25 °C, 1550 nm"

[params]                                  # one entry per Param of the class, validated like any edit
v_pi = { value = 5.5, from = "datasheet-typ", spec = "Vπ RF electrodes @ 50 kHz", max = 6.0 }
extinction_db = 22.0                      # a bare value is recorded as "unstated"

[limits]                                  # absolute maximum ratings: the lab warns when exceeded
optical_input_dbm = 20.0
rf_input_dbm = 28.0
bias_voltage_v = [-20.0, 20.0]

[unsupported]                             # datasheet figures the model cannot use, kept on purpose
eo_bandwidth_hz = { typ = 12e9, min = 10e9, note = "no RF roll-off in the model" }
```

`from` is one of `datasheet-typ`, `datasheet-min`, `datasheet-max`, `datasheet-graph` (read off a
plot), `datasheet-condition` (the condition a figure is quoted at, such as its wavelength),
`derived` (say how in `note`), `measured` or `assumed`. Quote the datasheet as it states the
number, with the conditions in `spec`; a figure the model has no parameter for belongs in
`[unsupported]`, not in a parameter it merely resembles.

Detectors also take a spectral curve from their datasheet plot — `efficiency` for a
`SinglePhotonDetector`, `responsivity` (A/W) for a `Photodiode` — zero outside its range:

```toml
[params]
efficiency = { value = 0.62, from = "derived", spec = "PDE at 650 nm, × 0.95 for the FC adaptor" }
ref_wavelength = { value = 650e-9, from = "datasheet-condition", spec = "Table 1 PDE row at 650 nm" }

[curves.efficiency]
from = "datasheet-graph"
spec = "Figure 3, typical PDE vs wavelength"
wavelength_nm = [400, 420, 440, …, 1060]
value = [0.085, 0.119, 0.179, …, 0.020]
```

The curve gives the shape; the table value at `ref_wavelength` fixes the scale, so the datasheet's
headline number is reproduced exactly where it was specified and the plot fills in the rest.
Where the plot and the table disagree (they often do by 5–20 %), the preset's `note` says so. The
shipped curves were digitized from the vendor PDFs at 300 dpi by tracing the series colour against
the plot's gridlines.

**Your own presets.** phoq looks, in this order, in the package, in packages that register the
`phoq.presets` entry point, in `~/.config/phoq/presets`, in the directories of `$PHOQ_PRESETS`
(`:`-separated), and in what you pass to `phoq.presets.add_path(dir)` or `--presets DIR`. A later
directory overrides an earlier one with the same id, so your lab's measured values can replace
shipped ones. `phoq presets --paths` prints the search path. To publish a pack, a package adds

```toml
[project.entry-points."phoq.presets"]
mylab = "mylab_presets"        # a package whose directory holds the .toml files, or a callable returning a path
```

What the datasheets made the models learn:

- Modulators have `v_pi_bias`, the Vπ of the DC electrodes. A supply on `bias` is referred to the
  RF electrode by Vπ/Vπ_bias, so quadrature sits at Vπ_bias/2 on the supply, as on the bench.
- Photodiodes have `ref_responsivity` at `ref_wavelength`, which scales the spectral curve (the
  preset's, else the material's typical one), and a `GaAs` material.
- Single-photon detectors have a wavelength-dependent efficiency η(λ) = efficiency ·
  PDE(λ)/PDE(λ_ref) when the preset gives a curve. A silicon SPAD on a 1550 nm link does not
  click, and the lab says so; the inspector lists the PDE at each incident wavelength. A generic
  detector keeps a flat efficiency.

## Command line

```bash
phoq run examples/delay_line.py --plot traces.png   # headless
```

```bash
phoq serve examples/mpqkd                  # API + 3D visualizer
```

```bash
phoq export examples/basic_link.json --format python
```

`run`, `serve` and `export` accept a `.py` file that defines `setup` (or `build()`), or a saved
`.json`. While serving, every edit made in the browser sets the attribute on the Python
instance and is autosaved. The default target is `<script>.json` next to the script; change it
with `--save PATH`, or turn it off with `--no-save`. Serve that JSON to carry on, or press
**Export .py** to get the setup back as a Python script (`setup.to_python()`).

### Driving modulators

Modulators have no drive settings of their own. Each has two electrical inputs whose voltages
add on the electrodes: `rf` for a function-generator output and `bias` for a DC supply output.

```python
psu = setup.add(DCPowerSupply(ch1_voltage=im.v_pi / 2))                      # quadrature bias
setup.connect(psu.ch1, im.bias)
fg = setup.add(FunctionGenerator())
setup.connect(fg.ch1, im.rf)
fg.set(ch1_shape="sine", ch1_freq=10e6, ch1_vpp=1.0)                           # linear modulation
fg.set_pattern("ch2", pm.voltage_for(2 * np.pi * k / 16), rate=laser.rep_rate)   # one voltage per pulse
```

For the intensity modulator 0 V is maximum transmission, Vπ/2 quadrature and Vπ the null. Real
LiNbO₃ devices have separate RF and DC electrodes with different Vπ: set `v_pi_bias` to the
datasheet's DC-electrode value and the supply on `bias` is referred to the RF electrode, so
quadrature is at Vπ_bias/2 on the supply, as on the bench.
`im.voltage_for(T)` and `pm.voltage_for(phase)` convert a wanted transmission or phase into the
rf voltage needed on top of whatever bias is connected, so protocol code works in optical terms. `set_pattern` sets the channel's amplitude
and offset from the pattern's extremes, and `chN_freq` is the symbol rate. The generator's time
base (`trigger`) is *aligned* by default: symbol k reaches the modulator together with pulse k of
the laser feeding it, as a calibrated AWG would. Use *laser* to set `chN_delay` yourself, or
*free* for a free-running generator. A generator output can also go straight to an oscilloscope
channel to look at the drive. Patterns are data, not parameters: they are not saved in the JSON
setup and have to be loaded by code.

![The modulated-link example: a 10 MHz sine from the function generator modulates the laser through the intensity modulator, and the oscilloscope shows the photocurrent next to the drive](https://raw.githubusercontent.com/ammar-faifi/phoq/main/docs/images/oscilloscope.webp)

*`examples/modulated_link.py`: the DC supply holds the intensity modulator at quadrature, the
generator's CH1 drives it with a 10 MHz sine, and the scope shows the photocurrent (CH1, yellow)
next to a copy of the drive (CH2, cyan) with its measurements.*

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
the table opens it. The API is `GET /api/tasks`, `POST /api/tasks {path}` and
`POST /api/tasks/{name}/{restart|stop|start|clear}`; the WebSocket streams `console` messages.

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
measure(batch)      the party owning the tagger gets the clicks                      [physical]
      ↓
receive(message)    whatever the parties say to each other                           [classical]
```

`examples/mpqkd` is split accordingly — `common.py` (public parameters, the
hardware, the pairing/sifting maths), `station.py` (what Alice and Bob share), `alice.py`,
`bob.py`, `charlie.py` — and the annotations map onto messages:

| `examples/mpqkd/reference.py` | Message | Who |
| --- | --- | --- |
| #1 announce C_i and L/R | `clicks` (single-click rounds, detector, block) | Charlie → all |
| #2 pairing with public l | no message: both stations run the same `simple_pairing` on #1 | — |
| #3 basis announcement | `basis` (one label per pair and how bright it was — vacuum, ν or μ — never which pulse) | Alice ↔ Bob |
| #4 alignment angle | `theta` (per pair) | Alice ↔ Bob |
| #5 parameter estimation | `xbits` (all sifted X bits, for E_X); `sample` / `sample-reply` (a 10 % sample of the key bits, dropped from the key, for E_Z) | Alice ↔ Bob |
| #6 error correction, verification | `ec-start` / `ec-query` / `ec-parity` / `ec-done`: Cascade on chunks of 1 000 sifted bits, every parity Alice discloses counted as leaked; `verified`: a 32-bit universal-hash tag of Bob's corrected chunk matched Alice's | Alice ↔ Bob |
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
clearly labelled, and is zero at this scale.

Run it with the three parties as processes, headless or live:

```bash
phoq run --protocol mpqkd --blocks 20
```

```bash
uv run python examples/mpqkd/sweep.py --plot sweep.png     # key rate vs fiber length, against the theory curve
```

```bash
phoq serve --protocol mpqkd
```

`--protocol` takes a package exposing `PARTIES` and `build()`; `--party my_party.py` adds a file
that defines a `Party` subclass (an eavesdropper on the switch, for instance). In the virtual lab
every party gets a laptop on the table showing its console, read-outs and TCP port, wired with
an Ethernet cable to a (purely visual) layer-3 switch whose port LEDs blink with the traffic;
clicking a laptop opens its console.

A station that re-encodes what it detects — a relay, a trusted node, an eavesdropper — sets
`stage = 1`. The lab then records its counters first, hands it the batch, waits for its
`measure()` to return (that is where it programs its own transmitter through `self.hw`) and only
then records the stations further down the line, so the feed-forward lands in the same block.
That is what a real station does with some latency, which the receiver's clock calibration
absorbs. `examples/bb84/eve.py` is one.

![Laptops for Alice, Charlie and Bob on the optical table, with Alice's console open showing block-by-block pairing, sifting and key growth, and her classical-channel traffic](https://raw.githubusercontent.com/ammar-faifi/phoq/main/docs/images/laptops.webp)

*Alice's console: each block's pulses, pairs and sifted Z/X bits with their error rates, the key
growing, and the messages exchanged with Charlie and Bob over the classical channel (screenshot
from before error correction and privacy amplification were added).*

## Physics model

[docs/engine.md](docs/engine.md) walks through the engine step by step (tracing, field synthesis,
detectors, each instrument's acquisition) and lists its current limitations.

- **Fields, not just powers.** Light is traced as complex amplitudes through every path:
  connector loss and reflection by connector type (see [Fiber connectors](#fiber-connectors)),
  fiber loss vs λ, propagation phase, a 4.9 ns/m delay, the beam-splitter unitary, attenuators
  and modulators. Paths from one laser interfere, and different lasers add coherently only if
  their wavelengths match. One reflection is followed per path; a second (a Fabry–Pérot pass
  between two connectors) is ≤ −80 dB for matching ones.
- **Lasers:** the envelope is averaged over each sample bin, so a 100 ps pulse is exact at any
  timebase. Phase is a Wiener process from the linewidth, drawn once per acquisition and shared
  by all paths. That makes interferometer visibility and Alice/Bob phase drift physical.
- **Photodiode:** I = Σ R(λ)·P + I_dark plus white shot noise (density 2qI) and thermal noise
  (4kT/R), all through a one-pole bandwidth: the noise RMS is √(2qIB) and √(4kTB/R) with
  B = (π/2)·f₃dB. It saturates softly above `saturation`.
- **Single-photon detector:** p(click) = 1 − (1 − p_dark)·exp(−η·n̄) per bin, plus dead time.
- **Modulators, oscilloscope and spectrum analyzer:** see
  [Theory](#theory-modulators-the-oscilloscope-and-the-spectrum-analyzer) below.
- **Steady-state report:** `phoq.report(setup)` gives mean powers, currents, open-connector
  emission and warnings. It uses the mutual coherence γ(Δτ) = exp(−πΔν|Δτ|) × envelope overlap.
- **Not modelled:** polarisation, dispersion, nonlinearity, afterpulsing.

## Theory: modulators, delay lines, the oscilloscope and the spectrum analyzer

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
is what a bias controller nulls (the drift itself is not modelled). The three bias points for
$\varphi_0 = 0$:

| $V$ | $T$ | Use |
| --- | --- | --- |
| 0 | $\mathrm{ER}/(\mathrm{ER}+1)$ | maximum transmission (default, nothing connected) |
| $V_\pi/2$ | ½ | *quadrature*: $dT/dV = -\pi k/2V_\pi$, the steepest and most linear point, for analogue modulation |
| $V_\pi$ | $1/(\mathrm{ER}+1)$ | *null*: pulse carving, vacuum / decoy states |

At quadrature a small drive $v$ gives $T \approx \tfrac12 - \tfrac{\pi}{2V_\pi}v$, i.e. a modulation
depth of $\pi v_\mathrm{pp}/2V_\pi$ for a peak-to-peak drive $v_\mathrm{pp}$ (a 1 Vpp sine on a
device with $V_\pi$ = 4 V swings $T$ between about 0.30 and 0.70). `im.voltage_for(T)` inverts the curve,
$`V = (V_\pi/\pi)\left[\arccos\!\big((2T - 1)/k\big) - \varphi_0\right] - V_\mathrm{bias}`$, so protocol
code can ask for a transmission. Asking for 0 or 1 gives the null or the maximum.

**Time base.** A function-generator pattern has one value per symbol, symbol $k$ centred on
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
their computed curves (see [Checked against the literature](#checked-against-the-literature)).

Not modelled: stage pitch and yaw (beam walk-off adds loss across the travel: for 12 mm lenses 1 m
apart, Wang et al. find that a 0.0085° tilt or a 0.86 mm offset of the coupling lens halves the
coupling), back-reflection from the collimator faces, thermal drift, backlash and repeatability.

**Counting pulses that miss each other.** The photon counter averages the light over each bin, and
two pulses inside one bin then interfere as if they overlapped. `PhotonCounter(oversample=m)`
synthesizes $m$ sub-bins per bin and adds their photons. A sub-bin of a quarter of the pulse width
gets the overlap right to 2 %, an eighth to 0.5 %.

**Validation.** `python examples/odl_validation.py` checks each equation above against phoq's
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

### Oscilloscope

The signal chain is detector → analogue front end → sampler → record → display and measurements.

**Front end.** The input bandwidth $f_\mathrm{BW}$ (`bandwidth`) is modelled as two identical
first-order poles at $`f_1 = 1.554\,f_\mathrm{BW}`$, chosen so that $|H(f_\mathrm{BW})|^2 = \tfrac12$:

```math
|H(f)| = \frac{1}{1 + (f/f_1)^2}, \qquad t_\mathrm{rise,10\text{–}90} \approx \frac{0.34}{f_\mathrm{BW}}
```

A 20 MHz front end passes a 10 MHz fundamental at 0.91, its third harmonic at 0.52 and its fifth at
0.28, so a 10 MHz square wave keeps most of its swing but loses its corners, and an SPD click
pulse of 5 ns is smeared to about 13 ns. The photodiode's own one-pole bandwidth acts before the
scope's; the slower of the two dominates.

**Sampling.** For a timebase of $s$ s/div the window is $W = 10s$. The effective sample rate is
limited by the record memory $N_\max$ (`max_points`):

```math
f_s = \min\!\left(f_\mathrm{set},\; N_\max / W\right), \qquad N = f_s W, \qquad \Delta t = 1/f_s
```

so a 5 GS/s scope with 20 000 points runs at 5 GS/s up to 400 ns/div and drops to 2 MS/s at
1 ms/div, as a real instrument does. Internally the analogue signal is computed on a grid
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

**Not modelled:** ADC quantisation, equivalent-time sampling, trigger jitter and holdoff,
probe loading. On the modulators: the electrodes' capacitance and velocity mismatch, which make a
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
5. **Averaging.** `averages` records, 50 % overlapped (Welch), are power-averaged in every sweep;
   the noise trace then fluctuates by about $4.3/\sqrt{K}$ dB. The trace can also be written fresh
   each sweep, averaged over sweeps, or held at its maximum (`trace`).
6. **Display.** `points` frequencies from `start_freq` to `stop_freq`. Each point takes the bins in
   its bucket through the `detector`: *peak* (the largest: tones read right even when a bucket is
   wider than the RBW), *average* (mean power: right for noise) or *sample*. The top line is
   `ref_level`, with `db_per_div` over ten divisions.
7. **Instrument noise.** The displayed average noise level (`noise_floor`, dBm/Hz) is added at the
   input as white noise; the photodiode's shot noise $2qI$ and thermal noise $4k_BT/R$ come on top,
   as on the bench.
8. **Measurements.** The peak (frequency, level), the median of the trace (the noise floor when
   signals cover little of the span), the −3 dB width around the peak, the frequency where the
   trace has fallen by $N$ dB, the power integrated over the span, and, when the peak sits at the
   start of the span as a self-homodyne beat does, a Lorentzian fit (`fit_lorentzian()`).
   `fit_self_homodyne(tau_d)` fits the self-homodyne spectrum for any delay, below. Several
   sweeps combine with `SpectrumResult.average(results)`, and `Runner.average_spectrum(esa,
   component, param, values)` steps a parameter (a phase shifter) and averages the sweeps.

The cost of a sweep is about $`3.84\,f_\mathrm{stop}/\mathrm{RBW}\times(K+1)/2`$ samples (×4 before
decimation), so a narrow RBW over a wide span is slow, as on a real analyzer.

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
$\Delta\nu$ centred at 0 Hz. In homodyne (no frequency shift) it folds about DC, so **the −3 dB
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

plus a coherent line at the centre of weight $e^{-2\pi\Delta\nu\tau_d}$ (at 0 Hz in homodyne, where the
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
span of at least 20 Δν, so the wings and the floor are both on screen; the *average* detector and
≥ 100 averages, because the beat is noise, not a tone. The review makes the same points about the
analyzer's resolution and sweep time, and asks for repeated measurements reported with their mean
and standard deviation (§4.1).

**Try it.** `examples/linewidth_homodyne.py` builds the bench above — a 100 kHz laser, two 50:50
couplers, a 10 km spool, an attenuator that gives the short arm the spool's loss, a photodiode and
the analyzer at 0–2 MHz, RBW 5 kHz, average detector — and measures:

```bash
python examples/linewidth_homodyne.py --plot dsh.png
```

```
laser linewidth 100 kHz (coherence time 3.18 µs); delay 49 µs = 15 τc, coherence left e^(−2πΔν·τd) = 4.3e-14

  Lorentzian fit       Δν =  99.7 kHz  (-0.3 %)
  −3 dB point          Δν =    96 kHz  (-4.0 %)
  −20 dB point / √99   Δν =  97.4 kHz  (-2.6 %)

The delay must beat the coherence time (1000 averages each):
    spool        τd  τd/τc coherence        fit      −3 dB
    0.3 km   1.47 µs    0.5       0.4    137 kHz    297 kHz
    0.6 km   2.94 µs    0.9      0.16    141 kHz    202 kHz
      1 km    4.9 µs    1.5     0.046    101 kHz    122 kHz
      2 km   9.79 µs    3.1    0.0021    100 kHz     99 kHz
     10 km     49 µs   15.4   4.3e-14    100 kHz   94.5 kHz
     25 km    122 µs   38.5   3.9e-34    101 kHz   88.7 kHz
```

The measured spectrum matches $S_i(f)$ above in absolute level to within about 0.3 dB from 1 kHz
to 1 MHz. Two lessons from the numbers: the −3 dB point of a noise-like trace scatters by several
per cent even after thousands of averages (it is one crossing on a flat top), so fit the whole
trace; and a delay shorter than about two coherence times reports the delay's sinc² instead of the
laser (0.3 km: the −3 dB point sits at $0.443/\tau_d$ = 301 kHz). `phoq serve
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
$\sqrt{P_\mathrm{short}P_\mathrm{long}}$.

```bash
python examples/linewidth_narrow.py --plot narrow.png
```

```
1 kHz laser, coherence time τc = 318 µs
    spool   loss  τd/τc  depth     RBW     span  short-delay fit     one θ Lorentzian     −3 dB
    100 km   19 dB   1.54   0.05  100 Hz   20 kHz           995 Hz    977 Hz   1.03 kHz  1.21 kHz
    200 km   38 dB   3.08   0.00  100 Hz   20 kHz           999 Hz     1 kHz     999 Hz    961 Hz
    400 km   76 dB   6.15   0.00  100 Hz   20 kHz           992 Hz     1 kHz     992 Hz    971 Hz

100 Hz laser, coherence time τc = 3.18 ms
    spool   loss  τd/τc  depth     RBW     span  short-delay fit     one θ Lorentzian     −3 dB
     50 km   10 dB   0.08   0.86   10 Hz 20.4 kHz           100 Hz   33.3 Hz     654 Hz  1.76 kHz
    100 km   19 dB   0.15   0.74   10 Hz 10.2 kHz          99.1 Hz   69.2 Hz     369 Hz    867 Hz
    200 km   38 dB   0.31   0.54   10 Hz  5.1 kHz          99.6 Hz   35.5 Hz     217 Hz    463 Hz
    500 km   95 dB   0.77   0.22   10 Hz 2.04 kHz          99.5 Hz   63.4 Hz     129 Hz    192 Hz
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

### Checked against the literature

The models above were compared with seven references (October 2026):

- *S. J. van Enk, "Coherent states, beam splitters and photons".* §8 and §12–14: a laser mode is
  a coherent state with Poissonian photon number $P(n) = e^{-|\alpha|^2}|\alpha|^{2n}/n!$ and mean
  $|\alpha|^2$, its initial phase is unknown, and a coherent state with a random phase equals a
  Poissonian mixture of number states. The `Laser` follows this: `mean_photon_number` $= E/h\nu$, a
  uniformly random start phase per acquisition, a Wiener-process phase whose correlation decays
  with the coherence time $1/\pi\Delta\nu$, and `phase_randomized` for weak-coherent-pulse
  sources. §16: a beam splitter maps
  $(\alpha_1, \alpha_2) \to ((\alpha_1 + i\alpha_2)/\sqrt2, (i\alpha_1 + \alpha_2)/\sqrt2)$,
  the convention `Coupler` uses, and §20 the Mach–Zehnder
  fringe $(1 - \cos\varphi)/2$ that the MZI tests reproduce.
- *J. A. T. Santiago, "Photon statistics for Fock and coherent states interfering in a beamsplitter"
  (arXiv:2508.08223).* Eq. (1.1) is the same beam-splitter transformation with $|t|^2$ equal to `ratio`;
  §4, Eqs. (4.3)–(4.7): two coherent inputs give product coherent outputs
  $|t\alpha + ir\beta\rangle|ir\alpha + t\beta\rangle$, still Poissonian ($Q = 0$,
  $g^{(2)}(0) = 1$). That is why the engine may
  add fields and then treat each detector's photon number as Poissonian with mean
  $\eta|E|^2\Delta t/h\nu$: the SPD's $p(\mathrm{click}) = 1 - (1 - p_\mathrm{dark})e^{-\eta\bar n}$ is
  exact for this light, and Charlie's two detectors in MP-QKD click independently. Fock-state
  inputs (§2–3), including Hong–Ou–Mandel interference, are outside the model: there are no
  single-photon sources, and a threshold detector facing $|1\rangle$ would click with probability
  $\eta$, not $1 - e^{-\eta}$.
- *C. Neumüller et al., "Bias control and linearization of the transfer function of electro-optic
  and acousto-optic modulators" (Fraunhofer IIS, 2024), §3.1.* Eqs. (1)–(3) give the phase
  modulator's $\varphi = \pi V/V_\pi$ and the origin of $V_\pi$; Eqs. (4)–(5) the MZM transmission
  $\cos^2(\varphi/2)$; Eq. (6) the finite extinction ratio from unequal arm powers, which replaced an
  earlier clamp in `IntensityModulator`; Eq. (7) the sum $V = V_\mathrm{RF} + V_\mathrm{DC}$ that the
  `rf` and `bias` inputs implement; Eq. (8) the drift term $\varphi_\mathrm{drift}$ behind the new
  `phi0`; and Eqs. (9)–(10) the push–pull drive. The paper's bias-control and predistortion
  sections (§4–5) are not modelled. AOMs are not part of the lab.
- *J.-Q. Chen et al., "Linewidth measurement of a narrow-linewidth laser: principles, methods, and
  systems", Sensors 24, 3656 (2024), [doi:10.3390/s24113656](https://doi.org/10.3390/s24113656).*
  §2.2 and Fig. 1b: the delayed self-homodyne interferometer that `examples/linewidth_homodyne.py`
  builds, and a beat twice as wide as the laser (here: the one-sided −3 dB point at $\Delta\nu$);
  §2.3.1 and Eq. (10): a Lorentzian once $\tau_d \gg \tau_c$; §2.3.4: the −20 dB width over $2\sqrt{99}$
  and Voigt fitting against 1/f noise. Eqs. (28)–(31), the spectrum for any delay: phoq averaged
  over the bias phase matches it to 0.07 dB rms at 0.3 and 1 km. §2.1, Eq. (4) and Fig. 2: a
  two-laser beat of width $\nu_t + \nu_r$, reproduced to within 2 %. Eq. (20): the frequency-
  discriminator limit behind the sinc² at short delays. Both checks are tests in
  `tests/test_spectrum.py`. Outside the model: 1/f noise, the self-heterodyne (no AOM), the
  recirculating, Brillouin and phase-noise (β-separation line) methods of §2.3.3–2.5.
- *D. J. Blumenthal, "Photodetection and Photodetectors", ECE228B Lecture 2 (UC Santa Barbara).*
  Slides 6–7: shot noise σ² = 2q(I_p + I_d)Δf, Poissonian but treated as Gaussian, with a flat
  density 2qI over the detector bandwidth. Checking this showed that phoq's photodiode had the
  right variance but spread it flat up to the sampling Nyquist frequency: at 50× oversampling the
  density inside the detector bandwidth was 15 dB too low. The noise now passes through the
  detector's pole with the signal, and its density is 2qI·|H(f)|² to within 1.5 %. Slide 10: the
  shot-noise-limited SNR ηP/(2hνB): phoq 1889, formula 1888 at 1 µW (η = 0.76, B = 1.57 GHz).
  Slides 11–12: the heterodyne beat 2R√(P_lo·P_s), reproduced to 10⁻⁴, and with a strong local
  oscillator SNR = ηP_s/(hνB), to 1 %; homodyne's further 3 dB (slide 14) follows from the same
  beat. Slides 17–18: R = ηq/hν with η ≤ 1 (the material tables peak at η = 0.85–0.86 for Si,
  GaAs and InGaAs, 0.58 for Ge) and the band-gap cut-offs (Si 1110, Ge 1850, GaAs 870 nm), which
  the tables respect up to the absorption tail of the datasheet curves. Tests in
  `tests/test_detector_noise.py`. Outside the model: photoconductive gain (slides 19–20).
- *I. S. Kolev, T. V. Karadzhov and E. N. Petkova, "Noises in Photodetectors", ICEST 2005, Niš,
  pp. 725–728.* Eqs. (20)–(21) and (28)–(29): thermal noise 4kTΔf/R and shot noise
  2q(I_ph + I_D)Δf, the two terms of phoq's photodiode. In the dark, with 50 Ω at 300 K, phoq gives
  (2qI_d + 4kT/R)·B to 0.2 %; there thermal noise is 10⁶ times the dark current's shot noise.
  §II.H: photon noise is Poissonian, √N, which is the single-photon detector's click statistics.
  Outside the model: 1/f (flicker) noise, which the paper finds insignificant above 10 kHz (its
  cut-off is at most 20 Hz for a PIN photodiode, and none at zero bias), and avalanche excess noise
  2qIΔf·M^X (Eq. 37).
- *K. Wang et al., "Design of optical fiber delay line with large delay range and low insertion
  loss", Front. Phys. 12:1362101 (2024),
  [doi:10.3389/fphy.2024.1362101](https://doi.org/10.3389/fphy.2024.1362101).* §2.2–2.3: the
  fiber's LP₀₁ mode as a Gaussian beam, collimated and refocused, with the insertion loss set by the
  Gaussian mode overlap (Eq. 7), which is the coupling phoq's `OpticalDelayLine` uses. Fig. 5 gives
  the coupling against a 0–1 m gap for 4, 8, 12 and 16 mm lenses at 633 nm. Traced from the figure
  and fitted with one parameter, the 4 mm curve's Rayleigh range (0.64 m, a 4.5 µm mode diameter),
  phoq matches it to 0.001 rms. Through z_R ∝ f² it predicts the 8 mm curve to 0.003 rms, and a
  0.8 % and 0.2 % drop over 1 m for 12 and 16 mm, where the paper finds less than 1 %; a test in
  `tests/test_odl.py` repeats this. Eq. (2): the rest of the insertion loss is the fiber end faces
  (4 %) and twelve lens surfaces, about 0.5 dB in all for their design, against phoq's assumed
  1 dB. Outside the model: the lens and fiber-end misalignments of Figs. 6–10, and the aberrations
  behind their measured 95.3 → 92.7 % over 0–1 m (Fig. 12), more than either diffraction model
  predicts.

## Quantum protocols

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

AWG patterns are indexed by pulse number. `PhotonCounter(compensate_delay=True)` removes the
time of flight, so `clicks[k]` belongs to pulse `k` of the patterns; `chN_delay` lines up a
channel whose detector sits behind a different length of fiber.

### BB84 with decoy states, and an eavesdropper

`examples/bb84` is the second protocol on the same party framework — two parties, no relay —
and shows that the framework is protocol-agnostic. It is phase-coding BB84 with weak coherent
pulses and three decoy intensities: Alice's pulsed laser → intensity modulator (μ, ν or vacuum
per pulse) → unbalanced Mach–Zehnder interferometer with a phase modulator in the long arm
(φ ∈ {0, π} for Z, {π/2, 3π/2} for X between the early and the late time slot) → VOA calibrated to
μ → fiber spool → Bob's matching interferometer (phase shifter for the stabilisation, phase
modulator for θ = 0 or π/2) → two SPDs → time tagger with three bins per pulse. In the middle
bin the late half of the early slot interferes with the early half of the late slot: DET-0 clicks
for φ − θ = 0, DET-1 for π, and the satellite bins are ignored. `build()` calibrates μ at the
fiber input and the interferometer phase, then adds the residual phase error of an imperfectly
stabilised interferometer (0.2 rad ≈ 1 % QBER).

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

### Delay-line interferometer: DPSK and the COW monitoring line

A delay-line interferometer (DLI) is an unbalanced Mach–Zehnder whose long arm is exactly one pulse
period $T$ longer. It is built from two `Coupler`s and standard cords: 0.5 m + 0.5 m around a
`PhaseShifter` (the interferometer phase $\theta$) in the short arm, 1 m + 2 m around an
`OpticalDelayLine` in the long arm. The cords give 2 m (9.795 ns), and the delay line adds the last
205 ps (100 ps insertion delay and 105 ps on the dial; see [Optical delay line](#optical-delay-line)). Each output slot then
holds pulse $k$ on top of pulse $k-1$, so the constructive port receives
$\tfrac12[1 + V\cos(\varphi_k - \varphi_{k-1} + \theta)]$ of the light and the destructive port the
rest. Here $V = e^{-\pi\Delta\nu T}$ is the coherence between neighbouring pulses. This is the
demodulator of differential phase-shift keying (DPSK) and the monitoring line of coherent one-way
(COW) QKD, which checks that neighbouring non-empty pulses keep their phase relation: an attack
that measures and re-prepares pulses breaks it, and the visibility drops.

The interferometer lives in `examples/dli.py`, a helper module, and two benches use it, each
with its own `build()` so `phoq serve` opens it on its own.

**DPSK demodulator: `examples/dli_for_dpsk.py`.** A 100 MHz, 1 ns pulse train and a phase modulator
writing $\varphi_k = \varphi_{k-1} + \pi b_k$, with the DLI and photodiodes on both ports. This is
the receiver of DPS-QKD:

```bash
python examples/dli_for_dpsk.py --plot dpsk.png
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
phase to swap the ports, raise `LD-1`'s linewidth to 10 or 100 MHz to watch neighbouring pulses
lose their phase relation, or tick *Random phase per pulse* to break it altogether.

**COW monitoring line: `examples/dli_for_cow.py`.** Bits as a pulse in one of two slots, 10 %
decoys (two pulses), μ = 0.5, 25 km, a 90:10 tap at Bob with the data detector on the 90 % side
and the DLI on the 10 % side, three SNSPD-like detectors and a time tagger. The full protocol, with
Alice's and Bob's parties, is [`examples/cow`](#coherent-one-way-qkd-cow-up-to-the-sifted-key).

```bash
python examples/dli_for_cow.py
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
one phase walk between the two arms (see [docs/engine.md](docs/engine.md)). The data line's
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

### Coherent one-way QKD (COW), up to the sifted key

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

1. Bob announces which bits gave a data-line click, but not in which slot, and the slots where DM1
   and DM2 fired.
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
cow = import_source("examples/cow")
print(cow.run(blocks=24, seed=5).summary())
```

```
COW over 25 km, cw source, μ = 0.5, f = 0.1, t_B = 0.5, 4,800,000 bits (9,600,000 slots at 434 MHz)
detected 6.566e-03 per bit (expected 6.549e-03) · sifted 25,733 (5.361e-03 per bit, expected 5.372e-03)
QBER 0.0119 on the whole sifted key (expected 0.0116)
V raw 0.9484 (expected 0.9471) · net 0.9920 (decoy 0.9923, 1-0 0.9941; expected 0.9928)
```

The net visibility is the coherence of neighbouring pulses, $e^{-\pi\Delta\nu\tau}$ = 0.993 for
1 MHz over one slot. Dark counts on DM2 bring the raw value down to 0.947. With the gain-switched
source, the data line is unchanged (QBER 1.1 %) but V averages −0.003 ± 0.034 over 12 runs. The
paper measured raw V ≥ 92 % and net V ≈ 98 %. Its QBER was 5.2 %, but 4 % of that came from
detector noise and afterpulses and 1 % from slow modulator electronics, neither of which phoq
models; here the QBER is the dark counts plus the modulator's 30 dB leak.

### COW under attack: intercept-resend in the time basis

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
fraction). Coherence survives only where both neighbouring pulses come from one
laser. Across a 1-0 boundary that means both bits were left alone, probability (1 − p_IR)², or
both were re-sent by Eve (p_IR²·q). Inside a decoy it means the decoy was left alone, or Eve
recognised it and re-sent a decoy (p_IR·q). Weighted by Bob's monitoring clicks,

```math
V_d = (1-p_{IR})\,V + p_{IR}\,c\,V_E, \qquad V_{1-0} = (1-p_{IR})^2\,V + p_{IR}^2\,c\,V_E, \qquad c = q + 2(1-q)\sqrt{\varepsilon_E},
```

with V = V_E = 0.993 the coherence of either laser over one slot. The √ε_E term is Eve's own
modulator leak: next to a pulse she re-sends alone, the empty slot still holds her laser's light
at √ε_E of the pulse's amplitude, coherent with it. That adds 0.017 at 40 dB extinction and
0.054 at 30 dB. (`eve_theory()` also keeps the switch's −40 dB crosstalk, which lets a diverted
pulse of Alice's interfere with an untouched neighbour: +0.005 at most.) V₁₋₀ falls faster than
V_d, because intercepting a fraction of the bits breaks every boundary between an intercepted bit
and one left alone. This is why the paper requires V₁₋₀ = V_d.

The paper estimates the damage as 1 − V = I·ξ. Here I = p_IR: with the flux matched, every detection
Bob gets in an attacked pair is a bit Eve re-sent and knows. ξ = 2e^{−μt}/(1 + e^{−μt}) is the
chance that Eve saw one pulse of a decoy and not the other; with e^{−μt} → 1 − q, ξ = 0.925. That
estimate counts decoy events, while Bob's clicks weight them by photons, and a decoy Eve
recognises carries two pulses. The visibility therefore drops by p_IR·(1 − c)·V = 0.84 p_IR rather
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

### Clock and frame synchronization from time stamps

Before any QKD post-processing, Bob must know which of Alice's symbols each click belongs to. His
time tagger runs on its own clock: its rate is off from Alice's by some ppm, and its time zero is
unrelated to hers. Alice and Bob agreed beforehand on a random string and its length, and Alice
sends it over and over. `examples/clock_sync.py` recovers Alice's clock and the delay from the
clicks alone.

**The bench.**

- Alice: a CW laser and an intensity modulator. Her AWG runs at 100 MHz + 23.4 ppm (Bob does not
  know the offset) with four samples per symbol, and plays each bit of an L = 1000-bit string as
  (s_k, 0, 0, 0). A 1 lights a 2.5 ns pulse centred on her clock tick, a 0 stays dark, and a frame
  is P = 10 µs. A VOA sets 0.2 photons per "1" pulse at the detector.
- Bob: a single-photon detector (η = 25 %, 1 µs dead time) and a free-running time tagger with
  100 ps bins: no delay compensation, no lock to Alice, and an unknown 3.21 µs clock epoch, set as
  the tagger's `ch1_delay`.

The analysis uses only the time stamps, the agreed string and the nominal rate:

1. **Clock.** The click train has a spectral line at Alice's symbol rate. The FFT of the click
   histogram finds it to one FFT bin (1/T = 500 Hz for a 2 ms record). The periodogram of the time
   stamps themselves, $Z(f) = |\sum_i e^{2\pi i f t_i}|^2$, maximised around that bin, then fits the
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
python examples/clock_sync.py --plot sync.png
```

| | patch cords only | 25 km of fiber |
| --- | --- | --- |
| Clicks in 2 ms | 1,441 | 1,435 |
| Clock: FFT peak → periodogram | 100.002313 → 100.0023402 MHz | 100.002314 → 100.0023444 MHz |
| Clock error against Alice's | +2 ppb | +44 ppb |
| Frame check (epoch folding) | L = 1000 at 33 σ; best coprime length 3.3 σ | L = 1000 at 29 σ; 4.3 σ |
| Cross-correlation peak | D = 322 symbols, 24 σ | D = 566 symbols, 25 σ |
| Δ mod P against the traced flight time + epoch | 3.224694 µs against 3.224692 µs (+2 ps) | 5.667165 µs against 5.667099 µs (+66 ps) |
| Clicks given their true symbol index | 100 % | 100 % |

With 25 km the true delay is 125.664 µs, more than twelve frames. Time stamps alone give it only
modulo P, so a real link resolves the rest with a longer frame or a coarse classical timestamp.
The delay error has two sources: the per-click spread over √N (about 19 ps), and the clock error
times the 1 ms between Alice's frame origin and the middle of the record (the 44 ppb of the
25 km run is 44 ps).

**1 ns time bins.** `build(rate=, resolution=, record=, dead_time=)` scales the bench, and
`GHZ = dict(rate=1e9, resolution=20e-12)` is the 1 GHz case. There the AWG runs at 4 GS/s playing
(1, 0, 0, 0), so each 1 is a 250 ps pulse, a frame is 1 µs and the tagger bins are 20 ps (phoq's
finest):

| Link | Record | Dead time | Clicks | Click spacing | Clock error | Frame check (σ; best coprime) | Delay error | Assigned |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 km | 200 µs | 1 µs | 199 | 1025 symbols | −10 ppb | 1053, wrong (6 σ; 5.1 σ) | −3.3 ps | 100 % |
| 0 km | 2 ms | 1 µs | 1993 | 1024 | −1 ppb | 1000 (54 σ; 22.9 σ) | −1.6 ps | 100 % |
| 0 km | 200 µs | 50 ns | 2251 | 76 | −22 ppb | 1000 (46 σ; 2.8 σ) | −2.1 ps | 100 % |
| 25 km | 200 µs | 50 ns | 2228 | 77 | −20 ppb | 1000 (47 σ; 3.3 σ) | −4.8 ps | 100 % |

At 1 GHz the detector, not the line, sets the statistics. The flux asks for 24 million clicks per
second; a 1 µs dead time allows about one per microsecond, whatever the symbol rate. Saturated,
the detector clicks once per dead time plus a short wait (the median spacing is 1025 symbols), a
rhythm of its own right next to the 1000-symbol frame:

- With 200 clicks, the blind frame check scores that rhythm (1053) above the true L. The true L
  only reaches about N/√(2L) ≈ 4.5 σ there.
- With 2000 clicks the true L wins at 54 σ, but the rhythm still shows at 23 σ.
- A 50 ns detector, as with SNSPDs, gives ten times the clicks in the same 200 µs and takes the
  artefact away (a 2.8 σ floor).

The clock and the delay never needed the blind check, since they use the agreed L.
`tests/test_sync.py` runs the 50 ns case and the saturated 1 µs case.

**Why the AWG carves pulses.** With a CW laser the modulator alone shapes the light. A pulse
filling a fraction d of the symbol spreads its clicks over a phase 2πd of the clock, so the line at
f_A keeps a depth sinc(πd). NRZ (d = 1, the string played one sample per symbol) has its spectral
null exactly at the symbol rate, and step 1 has nothing to lock to:

| Pulse | Line depth (sinc(πd)) | Clock error | Delay error | Clicks assigned |
| --- | --- | --- | --- | --- |
| 2.5 ns (d = ¼) | 0.894 (0.900) | +2 ppb | +2 ps | 100 % |
| 5 ns (d = ½) | 0.644 (0.637) | +54 ppb | −5 ps | 100 % |
| NRZ (d = 1) | 0.006 (0) | +50.7 ppm: lost | lost | 9.8 % |

The quarter-symbol depth sits just under sinc(πd) because the CW light leaks through the modulator
(30 dB extinction) during the dark samples. With NRZ, the clock would have to come from the string
itself, through a joint search of frequency and delay that the example does not implement.
phoq's AWG and modulator are ideal, with instant edges and no rise time, and its detectors have no
timing jitter; on a bench both add to σ_t and widen the errors in proportion.
`tests/test_sync.py` also repeats the patch-cord run and the 25 km run (with a −57 ppm clock and a
different epoch), checks the carving table's depths, and runs the analysis on hand-made clicks.

## Adding a component

Subclass `Component`, declare `Param`s, create ports in `_build`, and describe the optics in
`scatter`. The class registers itself, shows up in the visualizer's toolbox and inspector, and
gets a generic 3D model until you add one in `src/phoq/web/js/parts.js`.

```python
import math
from phoq import Component, Param, Transfer

class Mirror(Component):
    category, prefix, label = "passive", "M", "Fiber mirror"
    reflectivity = Param(0.99, min=0.0, max=1.0)

    def _build(self):
        self.add_port("p1")

    def scatter(self, port, wavelength):
        return [Transfer(port, math.sqrt(self.reflectivity))]
```

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

## Repository layout

```
src/phoq/            the package; src/phoq/web is the virtual lab, shipped with it
src/phoq/presets/    part presets (.toml) from vendor datasheets, one file per part
examples/            setups, analysis scripts and the MP-QKD showcase
examples/mpqkd/      Alice, Bob, Charlie (+ station/common), the original single-process
                     reference.py with its [CLASSICAL #N] notes, and a marimo notebook
examples/bb84/       decoy-state BB84: Alice, Bob (+ common), Eve as a --party file
examples/cow/        coherent one-way QKD up to the sifted key: Alice, Bob (+ common), Eve as a --party file
tests/               pytest suite
docs/images/         README screenshots of the virtual lab
.github/workflows/   ci.yml (tests on push / PR), publish.yml (PyPI on a version tag)
```

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

## License

phoq is free software under the [GNU Affero General Public License v3.0 or later](LICENSE)
(SPDX: `AGPL-3.0-or-later`). If you run a modified phoq as a network service, the AGPL asks you to
offer its users the corresponding source.
