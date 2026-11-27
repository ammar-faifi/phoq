# Part presets

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
| `exail/mxiq-ln-10`, `mxiq-ln-30` | Exail MXIQ-LN-10 and -30 LiNbO₃ IQ modulators, 12 and 25 GHz | `IQModulator` | Vπ of the RF electrodes, of DC1/DC2 and of DC3, insertion loss; maximum optical, RF and bias inputs |
| `hamamatsu/koheras-mikro-e15`, `-a15` | Hamamatsu (formerly NKT Photonics) Koheras MIKRO E15 and A15 single-frequency DFB fiber lasers, 40 and 10 mW at 1550.12 nm | `Laser` | wavelength, power, CW, the 100 Hz linewidth (120 µs), RIN at 10 MHz (−135 and −120 dBc/Hz), FC/APC; the 0.7 MHz RIN peak and the 1/f-like frequency noise kept as unsupported |
| `mpd/pdm-ir`, `pdm-ir-sff` | MPD PDM-IR InGaAs/InP SPAD modules with an SMF-28 pigtail: desktop instrument (DI) and small form factor (SFF), 250 and 500 c/s typical dark counts | `SinglePhotonDetector` | PDE at 1550 nm with the PDE-vs-wavelength curve, dark-count rate, hold-off, 200 ps FWHM timing jitter, 10 ns output pulse |
| `idq/id-qube-nir-fr-ln`, `-uln` | IDQ ID Qube NIR Free-Running InGaAs/InP SPADs with an MMF62.5 fiber input: low-noise (LN) and ultra-low-noise (ULN) models, 2 and 1.2 kHz maximum dark counts at the 25 % efficiency level | `SinglePhotonDetector` | the 25 % efficiency level at 1550 nm with the spectral-response curve, maximum dark-count rate at the 50 µs dead time it is quoted at, 150 ps timing jitter (read as FWHM), 10 ns output pulse |
| `idq/id281-standard-1550` | IDQ ID281 superconducting nanowire single-photon detector (SNSPD): one standard single pixel at 1550 nm, in its closed-cycle cryostat | `SinglePhotonDetector` | the p. 2 bias sweep's operating point at 1550 nm, 96 % SDE with 14 Hz of dark counts (26.5 µA; the table guarantees 80 % and < 100 cps); the 30 ns recovery time as a hard dead time; 30 ps FWHM timing jitter; 5 ns output pulse. The gradual recovery, the bias-current trade-off, polarization and photon-number resolution kept as unsupported |
| `excelitas/spcm-aqrh-10-fc` … `-16-fc` | Excelitas SPCM-AQRH Si SPAD modules with FC adaptor, one per dark-count grade (< 1500 … < 25 c/s) | `SinglePhotonDetector` | PDE at 650 nm with the 400–1060 nm curve, dark counts, 22 ns dead time, 10 ns output pulse; maximum 10⁴ photons per pulse |
| `thorlabs/det08cfc` | Thorlabs DET08CFC 5 GHz InGaAs detector | `Photodiode` | bandwidth, responsivity at 1550 nm with the spectral curve, load, saturation from the 2 V output limit |
| `thorlabs/dx25cf` | Thorlabs DX25CF 25 GHz GaAs detector | `Photodiode` | GaAs response, 0.5 A/W at 850 nm, 25 GHz, 25 Ω load; 10 dBm input limit |
| `thorlabs/dx25hf`, `dx25ha` | Thorlabs DX25HF (FC/PC) and DX25HA (FC/APC) 25 GHz high-linearity InGaAs detectors | `Photodiode` | 0.45 A/W at 1550 nm with the 0.915–1.625 µm curve, 25 GHz, 25 Ω load, linear to 50 mA; 20 dBm input limit |
| `thorlabs/pdb450c`, `pdb450a`, `pdb440c`, `pdb440a` | Thorlabs PDB450C/A (switchable gain 10³–10⁷ V/A, DC–150 MHz … 0.1 MHz) and PDB440C/A (51 × 10³ V/A, 15 MHz) balanced amplified photodetectors, InGaAs (C) and Si (A) | `BalancedPhotodetector` | each gain's bandwidth, NEP and swing; CMRR; responsivity with the spectral curve; monitor gain, bandwidth, swing, impedance and noise; 20 mW per photodiode |
| `fs/smfcsx-upc-upc`, `-apc-apc`, `-apc-upc` | FS SMFCSX 1 m OS2 (G.657.A1) simplex patch cords, SKUs 40740, 173580 and the 173581 hybrid | `Fiber` | connector A and B, 1 m length; ≤ 0.3 dB and UPC ≥ 50 / APC ≥ 60 dB, the mating table's values |

Every preset also sets the part's connector from its datasheet, where the datasheet names one: FC/PC
for the Thorlabs, MPD, ID Qube and Excelitas detectors (the balanced detectors' receptacles also take
FC/APC, but phoq mates connectors as fiber to fiber), FC/APC for the Exail modulators, intensity and IQ
(their FA ordering option; the default is bare fiber). The ID281 brochure names none, so
`idq/id281-standard-1550` keeps phoq's default FC/UPC.

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
shipped curves were digitized from the vendor PDFs at 300 dpi by tracing the series color against
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
