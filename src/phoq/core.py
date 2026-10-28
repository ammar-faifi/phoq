"""Core building blocks: parameters, ports, components and the Setup that wires them
together.

A *component* is any piece of lab equipment (laser, fiber, detector, oscilloscope …).
Its settings are declared as :class:`Param` class attributes, so they are ordinary
instance attributes::

    laser = Laser(power_dbm=-3)
    laser.wavelength = 1310e-9  # validated; observers (the visualizer) are notified

Components expose *ports* (``laser.out``, ``pd.inp``, ``pd.rf``). A :class:`Setup` owns
the components and the connections between ports. Optical ports are always joined
through a fiber patch cord; connecting two bulkhead ports directly inserts a 1 m
:class:`~phoq.Fiber`.
"""

from __future__ import annotations

import json
import keyword
import math
import re
import runpy
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

_MISSING = object()


class Param:
    """A validated, self-describing component setting.

    ``choices`` are enforced (for string options); ``presets`` are only suggested values
    that the visualizer offers in a drop-down. ``display=(unit, scale)`` shows e.g.
    meters as nanometers.
    """

    def __init__(
        self,
        default: Any,
        *,
        unit: str = "",
        label: str = "",
        doc: str = "",
        min: float | None = None,
        max: float | None = None,
        choices=None,
        presets=None,
        log: bool = False,
        display: tuple[str, float] | None = None,
    ):
        self.default = default
        self.unit = unit
        self.label = label
        self.doc = doc
        self.min = min
        self.max = max
        self.choices = tuple(choices) if choices else None
        self.presets = tuple(presets) if presets else None
        self.log = log
        self.display = display
        self.type = type(default).__name__
        self.name = ""

    def __set_name__(self, owner, name: str) -> None:
        self.name = name
        if not self.label:
            self.label = name.replace("_", " ").capitalize()

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj._values[self.name]

    def __set__(self, obj, value) -> None:
        value = self.validate(value)
        old = obj._values.get(self.name, _MISSING)
        obj._values[self.name] = value
        if old is not _MISSING and old != value:
            obj._param_changed(self.name)

    def validate(self, value):
        try:
            if self.type == "bool":
                value = (
                    value.strip().lower() in ("1", "true", "yes", "on")
                    if isinstance(value, str)
                    else bool(value)
                )
            elif self.type == "int":
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError
                value = int(value)
            elif self.type == "float":
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError
            elif self.type == "str":
                value = str(value)
        except TypeError, ValueError:
            raise TypeError(
                f"{self.name}: expected {self.type}, got {value!r}"
            ) from None
        if self.choices is not None and value not in self.choices:
            raise ValueError(
                f"{self.name}: {value!r} is not one of {list(self.choices)}"
            )
        if self.min is not None and value < self.min:
            raise ValueError(
                f"{self.name}: {value!r} is below the minimum {self.min!r}"
            )
        if self.max is not None and value > self.max:
            raise ValueError(
                f"{self.name}: {value!r} is above the maximum {self.max!r}"
            )
        return value

    def spec(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "unit": self.unit,
            "default": self.default,
            "doc": self.doc,
            "min": self.min,
            "max": self.max,
            "choices": list(self.choices) if self.choices else None,
            "presets": list(self.presets) if self.presets else None,
            "log": self.log,
            "display": list(self.display) if self.display else None,
        }


class Port:
    """A connector on a component. ``kind`` is "optical" or "electrical"."""

    __slots__ = ("component", "name", "kind", "direction", "label", "peer")

    def __init__(
        self,
        component: Component,
        name: str,
        kind: str = "optical",
        direction: str = "bidi",
        label: str = "",
    ):
        self.component = component
        self.name = name
        self.kind = kind
        self.direction = direction  # "in", "out" or "bidi"
        self.label = label or name
        self.peer: Port | None = None

    @property
    def path(self) -> str:
        return f"{self.component.name}.{self.name}"

    @property
    def connector(self) -> str | None:
        """Connector type of an optical port (``"FC/UPC"``, ``"FC/APC"``, …); None if
        electrical, and for a mating sleeve the type plugged into its other side
        (None while that is empty).
        """
        return (
            self.component.connector_of(self.name) if self.kind == "optical" else None
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "direction": self.direction,
            "label": self.label,
            "connector": self.connector,
            "peer": self.peer.path if self.peer else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Port {self.path}"
            f" {self.kind}{' → ' + self.peer.path if self.peer else ''}>"
        )


class Transfer(NamedTuple):
    """Light entering a component leaves through ``port`` with complex amplitude gain
    ``amp``."""

    port: Port
    amp: complex
    delay: float = 0.0
    mod: Any = None  # a modulator applying a time-dependent factor, if any


class Component:
    """Base class for every piece of equipment."""

    category: ClassVar[str] = (
        "component"  # source | passive | modulator | detector | instrument | cord
    )
    prefix: ClassVar[str] = "X"
    label: ClassVar[str] = "Component"
    description: ClassVar[str] = ""
    inputs: ClassVar[tuple[str, ...]] = ()  # port order used by Setup.chain()
    outputs: ClassVar[tuple[str, ...]] = ()
    is_cord: ClassVar[bool] = False  # fiber patch cords join optical ports
    is_adaptor: ClassVar[bool] = (
        False  # a mating sleeve: the connectors in its two sides mate
    )
    # plugs straight into a port or a sleeve, like a cord's end (a terminator)
    is_plug: ClassVar[bool] = False
    spectral_curves: ClassVar[dict[str, str]] = (
        {}
    )  # datasheet curves a preset may give: name → unit
    registry: ClassVar[dict[str, type[Component]]] = {}
    aliases: ClassVar[dict[str, str]] = {}  # a renamed class's old name → its name now

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.__name__.startswith("_"):
            Component.registry[cls.__name__] = cls

    @staticmethod
    def canonical(name: str) -> str:
        """A class name, with a renamed class's old name replaced by the current one."""
        return Component.aliases.get(name, name)

    @staticmethod
    def lookup(name: str) -> type[Component] | None:
        """The registered class called ``name`` (or formerly called so), None if there
        is none. Saved setups, presets and the API name classes this way."""
        return Component.registry.get(Component.canonical(name))

    @classmethod
    def param_specs(cls) -> dict[str, Param]:
        specs: dict[str, Param] = {}
        for klass in reversed(cls.__mro__):
            for key, value in vars(klass).items():
                if isinstance(value, Param):
                    specs[key] = value
        return specs

    def __init__(self, name: str | None = None, *, preset: str | None = None, **params):
        self.name = name
        self.setup: Setup | None = None
        self._values: dict[str, Any] = {
            k: p.default for k, p in self.param_specs().items()
        }
        self._preset = None  # the applied part preset; None = generic (class defaults)
        self.ports: dict[str, Port] = {}
        self._build()
        if preset:
            self.apply_preset(preset)
        self.set(**params)

    def _build(self) -> None:
        """Create the component's ports (overridden by subclasses)."""

    def add_port(
        self, name: str, kind: str = "optical", direction: str = "bidi", label: str = ""
    ) -> Port:
        port = Port(self, name, kind, direction, label)
        self.ports[name] = port
        return port

    def __getattr__(self, item):
        ports = self.__dict__.get("ports")
        if ports is not None and item in ports:
            return ports[item]
        raise AttributeError(f"{type(self).__name__} has no attribute or port {item!r}")

    def set(self, **params) -> Component:
        """Set several parameters at once: ``laser.set(power_dbm=0, mode="cw")``."""
        specs = self.param_specs()
        for key, value in params.items():
            if key not in specs:
                raise TypeError(
                    f"{type(self).__name__} has no parameter {key!r} (valid:"
                    f" {', '.join(specs)})"
                )
            setattr(self, key, value)
        return self

    @property
    def params(self) -> dict[str, Any]:
        return dict(self._values)

    # ---- part presets (see phoq.presets) ---------------------------------------------
    @property
    def preset(self) -> str:
        """Id of the applied part preset, or ``"generic"`` for the class defaults."""
        from .presets import GENERIC

        return self._preset.id if self._preset is not None else GENERIC

    @property
    def preset_info(self):
        """The applied :class:`~phoq.presets.Preset` (a synthesized generic one when
        none is)."""
        from .presets import generic_for

        return self._preset if self._preset is not None else generic_for(type(self))

    @property
    def limits(self) -> dict[str, Any]:
        """Absolute maximum ratings from the preset (empty for a generic part)."""
        return dict(self._preset.limits) if self._preset is not None else {}

    def curve(self, name: str):
        """The preset's datasheet curve ``name`` (a :class:`~phoq.presets.Curve`), or
        None."""
        return self._preset.curves.get(name) if self._preset is not None else None

    def apply_preset(self, preset) -> Component:
        """Set this part up as a product: ``im.apply_preset("exail/mx-ln-10")``.

        Only the parameters the preset lists are set, so operating settings (a laser's
        power, a scope's timebase) stay as they are; the ones the previous preset had
        set go back to their defaults first. ``"generic"`` returns to the class
        defaults.
        """
        from . import presets as _presets

        p = _presets.resolve(preset, type(self))
        _presets.validate_for(p, type(self))
        specs = self.param_specs()
        if self._preset is not None:
            for key in self._preset.params:
                if key in specs:
                    setattr(self, key, specs[key].default)
        self._preset = None if p.generic else p
        self.set(**p.params)
        return self

    def provenance(self) -> dict[str, dict]:
        """Where each preset-set parameter comes from, and whether it was edited
        since."""
        if self._preset is None:
            return {}
        return {
            key: {
                **prov.to_dict(),
                "overridden": self._values.get(key) != self._preset.params[key],
            }
            for key, prov in self._preset.provenance.items()
        }

    def baseline(self) -> dict[str, Any]:
        """Parameter values before any edit: the class defaults overlaid with the
        preset's."""
        base = {k: p.default for k, p in self.param_specs().items()}
        if self._preset is not None:
            base.update(self._preset.params)
        return base

    def ui_fields(self) -> list[str]:
        """Parameters worth showing for the current settings
        (the visualizer hides the rest)."""
        return [k for k in self.param_specs() if not k.startswith("connector")]

    def connector_of(self, port: str) -> str:
        """Connector type on an optical port: the part's ``connector`` setting, else
        FC/UPC."""
        from .units import DEFAULT_CONNECTOR

        return self._values.get("connector", DEFAULT_CONNECTOR)

    def info(self) -> dict[str, str]:
        """Derived, human-readable quantities shown as read-outs."""
        return {}

    def param_units(self) -> dict[str, str]:
        """Units that follow another setting, in place of a parameter's own (an
        analyzer's reference level is in its display units)."""
        return {}

    def scatter(self, port: Port, wavelength: float) -> list[Transfer]:
        """Optical transfer: light entering ``port`` → list of outgoing Transfers."""
        return []

    def _param_changed(self, name: str) -> None:
        if self.setup is not None:
            self.setup._notify("param", self, name)

    def to_dict(self, full: bool = False) -> dict:
        """Name, type, preset id (if any) and the parameters that differ from the
        baseline."""
        base = self.baseline()
        params = {k: v for k, v in self._values.items() if full or v != base[k]}
        out: dict[str, Any] = {"name": self.name, "type": type(self).__name__}
        if self._preset is not None:
            out["preset"] = self._preset.id
        out["params"] = params
        return out

    def __repr__(self) -> str:
        base = self.baseline()
        args = [repr(self.name)] if self.name else []
        if self._preset is not None:
            args.append(f"preset={self._preset.id!r}")
        args += [f"{k}={v!r}" for k, v in self._values.items() if v != base[k]]
        return f"{type(self).__name__}({', '.join(args)})"


class FiberConnectors:
    """Mixin for parts with fiber ports: one ``connector`` type for all of them."""

    connector = Param(
        "FC/UPC",
        choices=("FC/UPC", "FC/APC", "FC/PC"),
        label="Connectors",
        doc=(
            "End-face polish of this part's fiber connectors: FC/UPC (blue) and FC/PC"
            " are flat, FC/APC (green) is angled 8°. An APC mated to a flat connector"
            " leaves an air gap."
        ),
    )


def _free_port(comp: Component, names: tuple[str, ...], kind: str) -> Port | None:
    for n in names:
        p = comp.ports.get(n)
        if p is not None and p.kind == kind and p.peer is None:
            return p
    return None


class Setup:
    """An experiment: a set of named components and the connections between their
    ports."""

    def __init__(self, name: str = "experiment", *, seed: int | None = None):
        self.name = name
        self.seed = seed
        self.components: dict[str, Component] = {}
        self._listeners: list[Callable] = []

    # ---- building --------------------------------------------------------------------
    def add(self, *components: Component):
        """Add components (auto-naming unnamed ones). Returns the component, or a
        tuple."""
        for c in components:
            if c.setup is self:
                continue
            if c.setup is not None:
                raise ValueError(f"{c.name} already belongs to setup {c.setup.name!r}")
            if c.name is None:
                c.name = self._auto_name(c)
            elif c.name in self.components:
                raise ValueError(f"duplicate component name {c.name!r}")
            elif not re.fullmatch(r"[A-Za-z0-9_\-]+", c.name):
                raise ValueError(
                    "component names may only use letters, digits, '-' and '_':"
                    f" {c.name!r}"
                )
            c.setup = self
            self.components[c.name] = c
            self._notify("add", c)
        return components[0] if len(components) == 1 else components

    def _auto_name(self, c: Component) -> str:
        i = 1
        while f"{c.prefix}-{i}" in self.components:
            i += 1
        return f"{c.prefix}-{i}"

    def remove(self, component: Component | str) -> None:
        c = self[component] if isinstance(component, str) else component
        for p in c.ports.values():
            if p.peer is not None:
                self.disconnect(p)
        del self.components[c.name]
        c.setup = None
        self._notify("remove", c)

    def port(self, ref: Port | str) -> Port:
        """Resolve a Port or a ``"NAME.port"`` reference."""
        if isinstance(ref, Port):
            return ref
        name, _, port = ref.rpartition(".")
        if name not in self.components:
            raise KeyError(f"no component named {name!r}")
        if port not in self.components[name].ports:
            raise KeyError(
                f"{name} has no port {port!r} (ports:"
                f" {', '.join(self.components[name].ports)})"
            )
        return self.components[name].ports[port]

    def connect(
        self,
        a: Port | str,
        b: Port | str,
        fiber: Component | None = None,
        *,
        length: float = 1.0,
    ):
        """Connect two ports. Two bulkhead optical ports get a patch cord (returned) in
        between; a plug (a terminator) mates straight onto the other port."""
        a, b = self.port(a), self.port(b)
        for p in (a, b):
            if p.component.setup is not self:
                self.add(p.component)
        if a.kind != b.kind:
            raise ConnectionError(
                f"cannot connect {a.kind} port {a.path} to {b.kind} port {b.path}"
            )
        if a is b:
            raise ConnectionError(f"cannot connect {a.path} to itself")
        for p in (a, b):
            if p.peer is not None:
                raise ConnectionError(f"{p.path} is already connected to {p.peer.path}")
        if (
            a.kind == "electrical"
            and a.direction == b.direction
            and a.direction != "bidi"
        ):
            raise ConnectionError(
                f"cannot connect two electrical {a.direction}puts ({a.path}, {b.path})"
            )
        if a.kind == "optical" and a.component.is_plug and b.component.is_plug:
            raise ConnectionError(f"cannot plug {a.path} into {b.path}: both are plugs")
        if a.kind == "optical" and not any(
            p.component.is_cord or p.component.is_plug for p in (a, b)
        ):
            from .passive import Fiber

            if (
                fiber is None
            ):  # the patch cord a lab would take: its ends match the two ports
                from .units import DEFAULT_CONNECTOR

                fiber = Fiber(
                    length=length,
                    connector_a=a.connector
                    or DEFAULT_CONNECTOR,  # an empty sleeve has none yet
                    connector_b=b.connector or DEFAULT_CONNECTOR,
                )
            self.add(fiber)
            self._mate(fiber.ports["a"], a)
            self._mate(fiber.ports["b"], b)
            return fiber
        self._mate(a, b)
        return None

    def terminate(self, port: Port | str, connector: str | None = None) -> Component:
        """Cap an unused optical port with a fiber terminator of its own connector type
        (or ``connector``), as one caps a homodyne's vacuum input or a coupler's spare
        output.
        """
        from .passive import FiberTerminator
        from .units import DEFAULT_CONNECTOR

        p = self.port(port)
        cap = self.add(
            FiberTerminator(connector=connector or p.connector or DEFAULT_CONNECTOR)
        )
        self.connect(p, cap.ports["p"])
        return cap

    def spare_ports(self) -> list[Port]:
        """Unused optical ports of the passive parts and modulators, such as a coupler's
        spare input or output. A laser with nothing on it, a free detector input and an
        unplugged cord end are not spares."""
        from .detectors import _Detector
        from .sources import Laser

        return [
            p
            for c in self
            if not (
                c.is_cord
                or c.is_adaptor
                or c.is_plug
                or isinstance(c, (Laser, _Detector))
            )
            for p in c.ports.values()
            if p.kind == "optical" and p.peer is None
        ]

    def terminate_spares(self) -> list[Component]:
        """Cap every spare port (:meth:`spare_ports`) with a terminator of its own
        connector type; returns the terminators."""
        return [self.terminate(p) for p in self.spare_ports()]

    def _mate(self, a: Port, b: Port) -> None:
        a.peer, b.peer = b, a
        self._notify("connect", a.component)

    def disconnect(self, port: Port | str) -> None:
        p = self.port(port)
        if p.peer is None:
            return
        q = p.peer
        p.peer = q.peer = None
        self._notify("disconnect", p.component)

    def chain(self, *items: Component) -> Setup:
        """Wire components one after another, e.g.
        ``chain(laser, Fiber(2), pd, scope)``."""
        prev = None
        for item in items:
            if item.setup is not self:
                self.add(item)
            if prev is not None:
                self._link(prev, item)
            prev = item
        return self

    def _link(self, a: Component, b: Component) -> None:
        for kind in ("optical", "electrical"):
            out, inp = _free_port(a, a.outputs, kind), _free_port(b, b.inputs, kind)
            if out and inp:
                self.connect(out, inp)
                return
        raise ConnectionError(
            f"chain: no free compatible ports from {a.name} to {b.name}"
        )

    @classmethod
    def from_chain(
        cls, *items: Component, name: str = "experiment", seed: int | None = None
    ) -> Setup:
        return cls(name, seed=seed).chain(*items)

    # ---- queries ---------------------------------------------------------------------
    def __getitem__(self, name: str) -> Component:
        return self.components[name]

    def __contains__(self, name: str) -> bool:
        return name in self.components

    def __iter__(self) -> Iterator[Component]:
        return iter(list(self.components.values()))

    def __len__(self) -> int:
        return len(self.components)

    def of_type(self, *types: type) -> list:
        return [c for c in self if isinstance(c, types)]

    def connections(self) -> list[tuple[Port, Port]]:
        seen, out = set(), []
        for c in self:
            for p in c.ports.values():
                if p.peer is not None and id(p.peer) not in seen:
                    seen.add(id(p))
                    out.append((p, p.peer))
        return out

    def describe(self) -> str:
        """Plain-text netlist."""
        lines = [f"Setup {self.name!r}: {len(self)} components"]
        lines += [f"  {c!r}" for c in self]
        lines += [f"  {a.path} ── {b.path}" for a, b in self.connections()]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"<Setup {self.name!r}: {len(self)} components, {len(self.connections())}"
            " connections>"
        )

    # ---- running ---------------------------------------------------------------------
    def run(self, *instruments, seed: int | None = None):
        """Acquire every instrument (or the given ones). Returns
        :class:`~phoq.Results`."""
        from .runner import Runner

        return Runner(self, seed=seed if seed is not None else self.seed).run(
            *instruments
        )

    def serve(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        save_path: str | Path | None = None,
    ) -> None:
        """Start the API server and the 3D virtual lab for this setup
        (blocks until Ctrl-C)."""
        from .server import serve

        print(
            f"virtual lab: http://{host}:{port}   API docs: http://{host}:{port}/docs"
        )
        serve(
            self, host=host, port=port, save_path=Path(save_path) if save_path else None
        )

    # ---- change notifications --------------------------------------------------------
    def on_change(self, fn: Callable) -> Callable:
        """Register ``fn(event, component, param)``; called on every edit."""
        self._listeners.append(fn)
        return fn

    def _notify(
        self, event: str, component: Component | None = None, param: str | None = None
    ) -> None:
        for fn in list(self._listeners):
            fn(event, component, param)

    # ---- persistence -----------------------------------------------------------------
    def to_dict(self, full: bool = False) -> dict:
        return {
            "name": self.name,
            "seed": self.seed,
            "components": [c.to_dict(full) for c in self],
            "connections": [[a.path, b.path] for a, b in self.connections()],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Setup:
        _ensure_registry()
        setup = cls(data.get("name", "experiment"), seed=data.get("seed"))
        for cd in data["components"]:
            ctype = Component.lookup(cd["type"])
            if ctype is None:
                raise ValueError(f"unknown component type {cd['type']!r}")
            setup.add(
                ctype(cd["name"], preset=cd.get("preset"), **cd.get("params", {}))
            )
        for a, b in data.get("connections", []):
            setup._mate(setup.port(a), setup.port(b))
        return setup

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path: str | Path) -> Setup:
        """Load a setup from a ``.json`` file, a Python script, or a package directory
        that defines ``setup`` or ``build()``."""
        path = Path(path)
        if path.suffix == ".json":
            return cls.from_dict(json.loads(path.read_text()))
        if path.is_dir():  # a package such as examples/mpqkd
            from .loading import import_source

            namespace = vars(import_source(path))
        else:
            namespace = runpy.run_path(str(path), run_name="__lab__")
        if callable(namespace.get("build")):
            setup = namespace["build"]()
        else:
            setup = namespace.get("setup") or next(
                (v for v in namespace.values() if isinstance(v, Setup)), None
            )
        if not isinstance(setup, Setup):
            raise ValueError(
                f"{path} defines no Setup (expected a `setup` variable or a `build()`"
                " function)"
            )
        return setup

    def to_python(self) -> str:
        """Python source that rebuilds this setup (parameters that differ from defaults
        only)."""
        var: dict[str, str] = {}
        for c in self:
            v = re.sub(r"\W+", "_", c.name).strip("_").lower() or "part"
            if v[0].isdigit() or keyword.iskeyword(v):
                v = f"c_{v}"
            while v in var.values():
                v += "_"
            var[c.name] = v
        types = sorted({type(c).__name__ for c in self})
        seed = f", seed={self.seed!r}" if self.seed is not None else ""
        out = [
            f'"""{self.name} — generated by phoq."""',
            "",
            f"from phoq import {', '.join(['Setup'] + types)}",
            "",
            f"setup = Setup({self.name!r}{seed})",
        ]
        for c in self:
            d = c.to_dict()
            args = [repr(c.name)]
            if "preset" in d:
                args.append(f"preset={d['preset']!r}")
            args += [f"{k}={v!r}" for k, v in d["params"].items()]
            out.append(
                f"{var[c.name]} = setup.add({type(c).__name__}({', '.join(args)}))"
            )
        out.append("")
        for a, b in self.connections():
            out.append(
                f"setup.connect({var[a.component.name]}.{a.name},"
                f" {var[b.component.name]}.{b.name})"
            )
        out += [
            "",
            'if __name__ == "__main__":',
            "    print(setup.run().summary())",
            "",
        ]
        return "\n".join(out)


def _ensure_registry() -> None:
    from . import (
        detectors,
        generators,
        instruments,
        modulators,
        passive,
        sources,
    )  # noqa: F401
