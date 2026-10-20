"""Part presets: a component set up as a specific off-the-shelf product, from its datasheet.

A preset is one TOML file. Its ``[params]`` are values for the component's :class:`~phoq.Param`
settings, each with its provenance — which datasheet row it came from and whether that is the
typical, minimum or maximum column, a number read off a plot, a derived figure or an assumption::

    component = "IntensityModulator"
    title = "Exail MX-LN-10"
    vendor = "Exail"

    [params]
    v_pi = { value = 5.5, from = "datasheet-typ", spec = "Vπ RF electrodes @ 50 kHz", max = 6.0 }

``[curves]`` holds datasheet curves against wavelength that a class accepts (a detector's PDE or
responsivity), ``[limits]`` absolute maximum ratings (the lab warns when a setup exceeds them)
and ``[unsupported]`` the datasheet figures the model cannot represent, kept so that nothing is
dropped silently. Every component also has a ``generic`` preset: the class defaults.

Presets are collected from a search path — the ones shipped in ``phoq/presets``, packages that
register the ``phoq.presets`` entry point, ``~/.config/phoq/presets``, the directories in
``$PHOQ_PRESETS`` and anything given to :func:`add_path` (``--presets DIR`` on the command
line). A later directory overrides an earlier one with the same id, so a lab can correct a
shipped value or replace it with a measured one.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import os
import re
import tomllib
import warnings
from dataclasses import asdict, dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

import numpy as np

from .core import Component, Param, _ensure_registry
from .units import fmt_si

BUILTIN_DIR = Path(__file__).resolve().parent / "presets"
ENTRY_POINT_GROUP = "phoq.presets"
ENV_VAR = "PHOQ_PRESETS"
SCHEMA = 1
GENERIC = "generic"

KINDS = (
    "datasheet-typ",
    "datasheet-min",
    "datasheet-max",
    "datasheet-graph",
    "datasheet-condition",
    "derived",
    "measured",
    "assumed",
    "unstated",
)
KIND_DOC = {
    "datasheet-typ": "the typical column of the datasheet",
    "datasheet-min": "the minimum column of the datasheet",
    "datasheet-max": "the maximum column of the datasheet",
    "datasheet-graph": "read off a datasheet plot",
    "datasheet-condition": "the test condition a datasheet figure is quoted at (e.g. its wavelength)",
    "derived": "computed from datasheet figures (see the note)",
    "measured": "measured on a real unit",
    "assumed": "a plausible value that is not in the datasheet",
    "unstated": "given without provenance",
}
TIERS = (GENERIC, "datasheet", "validated", "vendor")
# [limits] keys the lab checks live (phoq.engine.report); other limits are kept for reference
CHECKED_LIMITS = frozenset(
    {"optical_input_w", "optical_input_dbm", "bias_voltage_v", "rf_input_dbm", "photons_per_pulse"}
)
_ID = re.compile(r"[a-z0-9][a-z0-9.\-]*/[a-z0-9][a-z0-9.\-]*")
_PARAM_KEYS = {"value", "from", "spec", "min", "typ", "max", "note"}
_CURVE_KEYS = {"wavelength_nm", "value", "from", "spec", "note"}


class PresetError(ValueError):
    """A preset file that cannot be used; the message names the file and the problem."""


def slug(text: str) -> str:
    """``IntensityModulator`` → ``intensity-modulator``, ``Micro Photon Devices`` → ``micro-photon-devices``."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", text)
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


@dataclass(frozen=True)
class Provenance:
    """Where one parameter value comes from."""

    kind: str
    spec: str = ""  # the datasheet row and its conditions
    min: float | None = None  # the other columns of that row, for reference
    typ: float | None = None
    max: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}

    def __str__(self) -> str:
        bits = [self.kind.replace("-", " ")]
        if self.spec:
            bits.append(self.spec)
        bits += [f"{k} {v:g}" for k, v in (("min", self.min), ("typ", self.typ), ("max", self.max)) if v is not None]
        if self.note:
            bits.append(self.note)
        return " · ".join(bits)


@dataclass(frozen=True)
class Curve:
    """A datasheet curve against wavelength (a detector's PDE or responsivity), zero outside it."""

    name: str
    wavelength: tuple[float, ...]  # [m], increasing
    value: tuple[float, ...]
    provenance: Provenance

    def __call__(self, wavelength: float) -> float:
        return float(np.interp(wavelength, self.wavelength, self.value, left=0.0, right=0.0))

    @property
    def peak(self) -> float:
        return max(self.value)

    @property
    def span(self) -> tuple[float, float]:
        return self.wavelength[0], self.wavelength[-1]

    def to_dict(self) -> dict:
        return {
            "wavelength_nm": [round(w * 1e9, 6) for w in self.wavelength],
            "value": list(self.value),
            **self.provenance.to_dict(),
        }

    def __str__(self) -> str:
        lo, hi = self.span
        return f"{lo * 1e9:.0f}–{hi * 1e9:.0f} nm, {len(self.value)} points, peak {self.peak:.3g} · {self.provenance}"


@dataclass
class Preset:
    id: str
    component: str
    title: str
    vendor: str = GENERIC
    part_number: str = ""
    description: str = ""
    tier: str = "datasheet"
    tags: tuple[str, ...] = ()
    source: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Provenance] = field(default_factory=dict)
    curves: dict[str, Curve] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    unsupported: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    path: Path | None = None
    origin: str = "phoq"

    @property
    def generic(self) -> bool:
        return self.tier == GENERIC

    def summary(self) -> dict:
        return {
            "id": self.id,
            "component": self.component,
            "title": self.title,
            "vendor": self.vendor,
            "part_number": self.part_number,
            "description": self.description,
            "tier": self.tier,
            "tags": list(self.tags),
            "origin": self.origin,
            "has_limits": bool(CHECKED_LIMITS & set(self.limits)),
            "curves": sorted(self.curves),
        }

    def to_dict(self) -> dict:
        return {
            **self.summary(),
            "source": self.source,
            "params": self.params,
            "provenance": {k: p.to_dict() for k, p in self.provenance.items()},
            "curves": {k: c.to_dict() for k, c in self.curves.items()},
            "limits": self.limits,
            "unsupported": self.unsupported,
            "notes": self.notes,
            "path": str(self.path) if self.path else None,
        }

    def describe(self) -> str:
        """Plain-text sheet for ``phoq presets show``."""
        cls = Component.lookup(self.component)
        specs = cls.param_specs() if cls else {}
        out = [f"{self.id} — {self.title}  [{self.component} · {self.tier} · {self.origin}]"]
        if self.description:
            out.append(f"  {self.description}")
        if self.part_number:
            out.append(f"  part number  {self.part_number}")
        src = self.source
        if src:
            ref = " · ".join(str(src[k]) for k in ("title", "revision", "document", "url", "vendor_url") if src.get(k))
            out.append(f"  source       {ref}")
            if src.get("conditions"):
                out.append(f"  conditions   {src['conditions']}")
        out.append("  params" if self.params else "  params       class defaults")
        for key, value in self.params.items():
            spec = specs.get(key)
            shown = _fmt(value, spec.unit if spec else "")
            out.append(f"    {key:<20} {shown:<14} {self.provenance[key]}")
        if self.curves:
            out.append("  curves")
            for key, curve in self.curves.items():
                out.append(f"    {key:<20} {curve}")
        if self.limits:
            out.append("  limits       " + " · ".join(f"{k} = {v}" for k, v in self.limits.items()))
        if self.unsupported:
            out.append("  not modelled")
            for key, value in self.unsupported.items():
                out.append(f"    {key:<24} {_fmt_entry(value)}")
        if self.notes:
            out.append("  notes        " + self.notes.strip().replace("\n", "\n               "))
        return "\n".join(out)


def _fmt(value: Any, unit: str = "") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return fmt_si(float(value), unit) if unit else f"{value:g}"


def _fmt_entry(value: Any) -> str:
    if isinstance(value, dict):
        note = value.get("note", "")
        nums = " ".join(f"{k} {v:g}" for k, v in value.items() if k in ("value", "min", "typ", "max") and isinstance(v, (int, float)))
        return (nums + (" — " + note if note else "")).strip() or str(value)
    if isinstance(value, list):
        return "[" + ", ".join(f"{v:g}" if isinstance(v, (int, float)) else str(v) for v in value) + "]"
    return _fmt(value)


def _num(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresetError(f"{where}: expected a number, got {value!r}")
    return float(value)


def _curve(name: str, entry: Any, where: str) -> Curve:
    at = f"{where}: curves.{name}"
    if not isinstance(entry, dict):
        raise PresetError(f"{at} must be a table")
    unknown = set(entry) - _CURVE_KEYS
    if unknown:
        raise PresetError(f"{at}: unexpected keys {sorted(unknown)}")
    xs, ys = entry.get("wavelength_nm"), entry.get("value")
    if not isinstance(xs, list) or not isinstance(ys, list) or len(xs) != len(ys) or len(xs) < 2:
        raise PresetError(f"{at} needs 'wavelength_nm' and 'value' lists of the same length (at least 2 points)")
    xs = [_num(v, f"{at}.wavelength_nm") for v in xs]
    ys = [_num(v, f"{at}.value") for v in ys]
    if any(v is None for v in xs + ys):
        raise PresetError(f"{at}: every point needs a number")
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise PresetError(f"{at}: wavelength_nm must increase")
    if any(v < 0 for v in ys):
        raise PresetError(f"{at}: values must not be negative")
    kind = str(entry.get("from", "unstated"))
    if kind not in KINDS:
        raise PresetError(f"{at}: unknown provenance {kind!r}; use one of {', '.join(KINDS)}")
    prov = Provenance(kind, str(entry.get("spec", "")), note=str(entry.get("note", "")))
    return Curve(name, tuple(x * 1e-9 for x in xs), tuple(ys), prov)


# ---- one file ----------------------------------------------------------------------------------
def parse(text: str, *, path: Path | None = None, origin: str = "user") -> Preset:
    """Parse a preset's TOML text (``path`` is only used for messages and the default id)."""
    where = str(path) if path else "preset"
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PresetError(f"{where}: not valid TOML: {e}") from None
    if data.get("schema", SCHEMA) != SCHEMA:
        raise PresetError(f"{where}: schema {data.get('schema')!r} is not supported (this phoq reads schema {SCHEMA})")
    for key in ("component", "title"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise PresetError(f"{where}: '{key}' is required")
    vendor = str(data.get("vendor") or GENERIC)
    pid = str(data.get("id") or f"{slug(vendor)}/{path.stem if path else slug(data['title'])}")
    if not _ID.fullmatch(pid):
        raise PresetError(f"{where}: id {pid!r} must look like 'vendor/part' (lower-case letters, digits, '-' and '.')")
    tier = str(data.get("tier", "datasheet"))
    if tier not in TIERS:
        raise PresetError(f"{where}: tier {tier!r} is not one of {', '.join(TIERS)}")
    for key in ("params", "curves", "limits", "unsupported", "source"):
        if not isinstance(data.get(key, {}), dict):
            raise PresetError(f"{where}: [{key}] must be a table")
    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise PresetError(f"{where}: 'tags' must be a list")

    params, prov = {}, {}
    for key, entry in data.get("params", {}).items():
        if isinstance(entry, dict):
            if "value" not in entry:
                raise PresetError(f"{where}: params.{key} needs a 'value'")
            kind = str(entry.get("from", "unstated"))
            if kind not in KINDS:
                raise PresetError(f"{where}: params.{key}: unknown provenance {kind!r}; use one of {', '.join(KINDS)}")
            unknown = set(entry) - _PARAM_KEYS
            if unknown:
                raise PresetError(f"{where}: params.{key}: unexpected keys {sorted(unknown)}")
            params[key] = entry["value"]
            prov[key] = Provenance(
                kind,
                str(entry.get("spec", "")),
                _num(entry.get("min"), f"{where}: params.{key}.min"),
                _num(entry.get("typ"), f"{where}: params.{key}.typ"),
                _num(entry.get("max"), f"{where}: params.{key}.max"),
                str(entry.get("note", "")),
            )
        else:
            params[key] = entry
            prov[key] = Provenance("unstated")
    source = {
        k: (v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v)
        for k, v in data.get("source", {}).items()
    }
    return Preset(
        id=pid,
        component=Component.canonical(data["component"]),
        title=data["title"],
        vendor=vendor,
        part_number=str(data.get("part_number", "")),
        description=str(data.get("description", "")),
        tier=tier,
        tags=tuple(str(t) for t in tags),
        source=source,
        params=params,
        provenance=prov,
        curves={k: _curve(k, v, where) for k, v in data.get("curves", {}).items()},
        limits=dict(data.get("limits", {})),
        unsupported=dict(data.get("unsupported", {})),
        notes=str(data.get("notes", "")),
        path=path,
        origin=origin,
    )


def load_file(path: str | Path, origin: str = "user") -> Preset:
    path = Path(path)
    return parse(path.read_text(encoding="utf-8"), path=path, origin=origin)


def check(preset: Preset) -> list[str]:
    """Problems that stop a preset from being applied: unknown class, unknown or invalid params."""
    _ensure_registry()
    cls = Component.lookup(preset.component)
    if cls is None:
        return [f"{preset.id}: component {preset.component!r} is not a registered class"]
    specs = cls.param_specs()
    problems = []
    for key, value in preset.params.items():
        spec = specs.get(key)
        if spec is None:
            problems.append(f"{preset.id}: {preset.component} has no parameter {key!r} (valid: {', '.join(specs)})")
            continue
        try:
            spec.validate(value)
        except (TypeError, ValueError) as e:
            problems.append(f"{preset.id}: {e}")
    problems += [f"{preset.id}: {msg}" for msg in _curve_problems(preset, cls)]
    return problems


def _curve_problems(preset: Preset, cls: type[Component]) -> list[str]:
    accepted = getattr(cls, "spectral_curves", {})
    return [
        f"{cls.__name__} takes no {name!r} curve"
        + (f" (accepted: {', '.join(accepted)})" if accepted else "")
        for name in preset.curves
        if name not in accepted
    ]


# ---- the search path ----------------------------------------------------------------------------
_extra_paths: list[Path] = []
_cache: dict[str, Preset] | None = None


def _entry_point_dir(ep) -> Path:
    obj = ep.load()
    if callable(obj) and not hasattr(obj, "__path__"):
        obj = obj()
    if isinstance(obj, (str, Path)):
        return Path(obj)
    if hasattr(obj, "__path__"):  # a package: its directory
        return Path(next(iter(obj.__path__)))
    if getattr(obj, "__file__", None):
        return Path(obj.__file__).parent
    raise PresetError("the entry point must name a directory, a package, or a callable returning one")


def user_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "phoq" / "presets"


def search_path() -> list[tuple[str, Path]]:
    """``(origin, directory)`` pairs in precedence order: later entries override earlier ones."""
    dirs: list[tuple[str, Path]] = [("phoq", BUILTIN_DIR)]
    try:
        eps = list(entry_points(group=ENTRY_POINT_GROUP))
    except Exception:  # a broken installation should not take the lab down
        eps = []
    for ep in eps:
        try:
            dirs.append((f"plugin:{ep.name}", _entry_point_dir(ep)))
        except Exception as e:
            warnings.warn(f"phoq.presets entry point {ep.name!r} skipped: {e}", stacklevel=2)
    dirs.append(("user", user_dir()))
    for d in filter(None, os.environ.get(ENV_VAR, "").split(os.pathsep)):
        dirs.append(("env", Path(d).expanduser()))
    dirs += [("added", p) for p in _extra_paths]
    return dirs


def add_path(path: str | Path) -> Path:
    """Register a directory of ``.toml`` presets (searched recursively); it overrides earlier ones."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise PresetError(f"{p} is not a directory")
    if p not in _extra_paths:
        _extra_paths.append(p)
    reload()
    try:
        _files()  # a broken file is reported now, not at the first lookup
    except PresetError:
        _extra_paths.remove(p)
        reload()
        raise
    return p


def reload() -> None:
    """Forget the cached files (after editing a preset on disk)."""
    global _cache
    _cache = None


def _files() -> dict[str, Preset]:
    global _cache
    if _cache is None:
        found: dict[str, Preset] = {}
        for origin, d in search_path():
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*.toml")):
                p = load_file(f, origin)
                found[p.id] = p  # a later directory wins
        _cache = found
    return _cache


# ---- lookup --------------------------------------------------------------------------------------
def generic_id(cls: type[Component]) -> str:
    return f"{GENERIC}/{slug(cls.__name__)}"


def generic_for(cls: type[Component]) -> Preset:
    """The implicit preset of a class: its defaults, no vendor."""
    return Preset(
        id=generic_id(cls),
        component=cls.__name__,
        title=f"Generic {cls.label.lower()}",
        description=cls.description,
        tier=GENERIC,
    )


def registry() -> dict[str, Preset]:
    """Every preset by id: a generic one per component class, then the files."""
    _ensure_registry()
    out = {generic_id(c): generic_for(c) for c in Component.registry.values()}
    out.update(_files())
    return out


def get(preset_id: str) -> Preset:
    reg = registry()
    if preset_id in reg:
        return reg[preset_id]
    close = difflib.get_close_matches(preset_id, reg, n=3)
    hint = f" — did you mean {', '.join(close)}?" if close else ""
    raise KeyError(f"unknown preset {preset_id!r}{hint}")


def _class_names(component) -> set[str] | None:
    if component is None:
        return None
    if isinstance(component, str):
        cls = Component.lookup(component)
        return {k.__name__ for k in cls.__mro__} if cls else {component}
    return {k.__name__ for k in component.__mro__}


def search(
    component: str | type[Component] | None = None,
    *,
    vendor: str | None = None,
    tag: str | None = None,
    text: str | None = None,
    generic: bool = True,
) -> list[Preset]:
    """Presets for a component class (a subclass sees its base classes' presets), vendor, tag or text."""
    names = _class_names(component)
    own = component.__name__ if isinstance(component, type) else component and Component.canonical(component)
    out = []
    for p in registry().values():
        if names is not None and p.component not in names:
            continue
        if p.generic and (not generic or (own and p.component != own)):
            continue  # a subclass inherits its bases' parts, but has one generic of its own
        if vendor and slug(vendor) != slug(p.vendor):
            continue
        if tag and tag.lower() not in {t.lower() for t in p.tags}:
            continue
        if text:
            hay = " ".join([p.id, p.title, p.part_number, p.description, p.vendor, *p.tags]).lower()
            if text.lower() not in hay:
                continue
        out.append(p)
    return sorted(out, key=lambda p: (not p.generic, p.vendor.lower(), p.title.lower()))


def resolve(ref: str | Preset, cls: type[Component]) -> Preset:
    """The preset for ``ref`` (an id, ``"generic"`` or a Preset), checked against ``cls``."""
    if isinstance(ref, Preset):
        p = ref
    elif ref in (GENERIC, generic_id(cls)):
        return generic_for(cls)
    else:
        p = get(ref)
    if p.component not in {k.__name__ for k in cls.__mro__}:
        raise PresetError(f"preset {p.id!r} is for a {p.component}, not a {cls.__name__}")
    return p


def validate_for(preset: Preset, cls: type[Component]) -> None:
    """Raise before any value is applied if a preset value does not fit the class."""
    specs: dict[str, Param] = cls.param_specs()
    for key, value in preset.params.items():
        if key not in specs:
            raise PresetError(f"preset {preset.id!r}: {cls.__name__} has no parameter {key!r}")
        try:
            specs[key].validate(value)
        except (TypeError, ValueError) as e:
            raise PresetError(f"preset {preset.id!r}: {e}") from None
    for msg in _curve_problems(preset, cls):
        raise PresetError(f"preset {preset.id!r}: {msg}")
