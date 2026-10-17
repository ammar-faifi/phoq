"""Physical constants, unit conversions and material data used across the lab."""

from __future__ import annotations

import math

import numpy as np

H = 6.62607015e-34  # Planck constant [J s]
C0 = 299_792_458.0  # speed of light [m/s]
Q_E = 1.602176634e-19  # elementary charge [C]
K_B = 1.380649e-23  # Boltzmann constant [J/K]

N_GROUP = 1.4682  # SMF-28 group index
N_EFF = 1.4677  # SMF-28 effective (phase) index
FIBER_DELAY = N_GROUP / C0  # [s/m], about 4.9 ns per metre

# ---- fiber connectors ---------------------------------------------------------------------------
# End-face polish: PC and UPC are flat (domed, 0°) and mate with each other; APC is angled 8° and
# mates only with APC. Colour code: UPC (and PC) blue, APC green.
CONNECTORS = ("FC/UPC", "FC/APC", "FC/PC")
DEFAULT_CONNECTOR = "FC/UPC"
CONNECTOR_COLORS = {"FC/UPC": "#2563eb", "FC/PC": "#2563eb", "FC/APC": "#16a34a"}
CONNECTOR_LOSS_DB = 0.3  # one mated pair of matching connectors (typical; TIA-568 allows 0.75 dB)
RETURN_LOSS_DB = {"FC/PC": 40.0, "FC/UPC": 50.0, "FC/APC": 60.0}  # typical, per mated pair
# An angled face against a flat one leaves an air gap, and the beam leaves the APC fiber
# refracted by ≈ 3.8°: several dB lost depending on ferrule geometry. An assumed typical value.
MISMATCH_LOSS_DB = 5.0
FRESNEL = ((N_EFF - 1) / (N_EFF + 1)) ** 2  # a flat glass–air face reflects 3.6 % (−14.4 dB)

COMMON_WAVELENGTHS = (635e-9, 780e-9, 850e-9, 1310e-9, 1550e-9)


def db_to_t(db: float) -> float:
    """Power transmission for a loss in dB."""
    return 10.0 ** (-db / 10.0)


def dbm_to_w(dbm: float) -> float:
    return 1e-3 * 10.0 ** (dbm / 10.0)


def w_to_dbm(w: float) -> float:
    return 10.0 * math.log10(w / 1e-3) if w > 0 else -math.inf


def photon_energy(wavelength: float) -> float:
    return H * C0 / wavelength


# Photodiode responsivity [A/W] against wavelength [nm] (typical datasheet curves).
RESPONSIVITY = {
    "InGaAs": (
        [800, 900, 1000, 1100, 1200, 1300, 1400, 1550, 1600, 1650, 1700],
        [0.00, 0.25, 0.55, 0.70, 0.80, 0.90, 0.93, 0.95, 0.90, 0.60, 0.15],
    ),
    "Si": (
        [350, 400, 500, 600, 700, 800, 900, 960, 1000, 1050, 1100, 1150],
        [0.10, 0.20, 0.30, 0.40, 0.48, 0.55, 0.62, 0.60, 0.45, 0.25, 0.05, 0.00],
    ),
    "Ge": (
        [800, 900, 1000, 1100, 1300, 1400, 1550, 1600, 1700, 1800],
        [0.15, 0.25, 0.35, 0.45, 0.60, 0.65, 0.70, 0.55, 0.15, 0.00],
    ),
    "GaAs": (  # read off the Thorlabs DX25C spectral-response plot (TTN142494-S01)
        [500, 550, 600, 650, 700, 750, 800, 830, 850, 865, 875, 890, 900],
        [0.12, 0.17, 0.24, 0.31, 0.38, 0.46, 0.53, 0.57, 0.57, 0.52, 0.35, 0.10, 0.00],
    ),
}
DARK_CURRENT = {"InGaAs": 1e-9, "Si": 0.3e-9, "Ge": 50e-9, "GaAs": 50e-9}  # GaAs: DX25C maximum

# SMF-28 attenuation [dB/km] against wavelength [nm].
FIBER_ATTENUATION = (
    [600, 635, 700, 850, 1000, 1310, 1383, 1450, 1550, 1625, 1700],
    [9.0, 8.0, 5.0, 2.2, 0.8, 0.33, 0.35, 0.25, 0.19, 0.21, 0.30],
)


def angled(connector: str) -> bool:
    """True for an angle-polished (APC) connector."""
    return connector.endswith("APC")


def mating(a: str, b: str) -> tuple[float, float, float]:
    """A mated pair of connectors ``a`` and ``b``: power transmission, and the power reflected
    back into side a and into side b.

    Matching families lose CONNECTOR_LOSS_DB and reflect at the worse of the two return losses.
    An APC against a PC/UPC loses MISMATCH_LOSS_DB; the flat face then sits against an air gap and
    sends the Fresnel 3.6 % back into its own fiber, while the angled face's reflection leaves the
    core as usual.
    """
    if angled(a) == angled(b):
        r = db_to_t(min(RETURN_LOSS_DB[a], RETURN_LOSS_DB[b]))
        return db_to_t(CONNECTOR_LOSS_DB), r, r
    ra = db_to_t(RETURN_LOSS_DB[a]) if angled(a) else FRESNEL
    rb = db_to_t(RETURN_LOSS_DB[b]) if angled(b) else FRESNEL
    return db_to_t(MISMATCH_LOSS_DB), ra, rb


def open_end_reflection(connector: str) -> float:
    """Power reflected back into the fiber by an unmated connector in air."""
    return db_to_t(RETURN_LOSS_DB[connector]) if angled(connector) else FRESNEL


def responsivity(material: str, wavelength: float) -> float:
    xs, ys = RESPONSIVITY[material]
    return float(np.interp(wavelength * 1e9, xs, ys, left=0.0, right=0.0))


def peak_responsivity(material: str) -> float:
    """The maximum of the material's typical responsivity curve [A/W]."""
    return max(RESPONSIVITY[material][1])


def fiber_attenuation(wavelength: float) -> float:
    """SMF-28 loss in dB/km."""
    xs, ys = FIBER_ATTENUATION
    return float(np.interp(wavelength * 1e9, xs, ys))


_PREFIXES = [
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "µ"),
    (1e-9, "n"),
    (1e-12, "p"),
    (1e-15, "f"),
]


def fmt_si(value: float, unit: str = "", digits: int = 3) -> str:
    """Format with an SI prefix: fmt_si(2.3e-4, 'A') -> '230 µA'."""
    if value is None or not math.isfinite(value):
        return "—"
    if value == 0:
        return f"0 {unit}".strip()
    a = abs(value)
    for scale, prefix in _PREFIXES:
        if a >= scale * 0.9995:
            return f"{value / scale:.{digits}g} {prefix}{unit}"
    return f"{value / 1e-15:.{digits}g} f{unit}"


def seq125(lo: float, hi: float) -> list[float]:
    """1-2-5 sequence between lo and hi inclusive (oscilloscope style)."""
    out = []
    for e in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 2):
        for m in (1, 2, 5):
            v = float(f"{m * 10.0 ** e:.3g}")
            if lo * 0.999 <= v <= hi * 1.001:
                out.append(v)
    return out
