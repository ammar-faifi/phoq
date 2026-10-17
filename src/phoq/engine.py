"""Simulation engine.

1. :func:`trace` follows light from every laser through connectors, fibers and components and
   records, for each detector, the list of paths (complex amplitude gain, delay, modulators).
2. :func:`synthesize` turns those paths into complex fields on a time grid. Each laser's phase
   noise is drawn once per call and shared by all its paths, so interference (e.g. in a
   Mach–Zehnder or at Charlie's beam splitter) and its loss of visibility are physical.
3. :func:`report` gives the steady-state picture (mean powers, currents, open fiber ends,
   warnings) that the visualizer shows without running an acquisition.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .core import Setup
from .detectors import Photodiode, PowerMeter, SinglePhotonDetector, _Detector
from .generators import FunctionGenerator
from .modulators import _Modulator
from .sources import Laser
from .units import CONNECTOR_LOSS_DB, db_to_t, dbm_to_w, fmt_si, photon_energy, w_to_dbm

MAX_HOPS = 40
MIN_GAIN = 1e-18
_CONN = math.sqrt(db_to_t(CONNECTOR_LOSS_DB))


@dataclass
class Path:
    source: Laser
    amp: complex
    delay: float
    mods: tuple = ()  # ((modulator, delay at which the light passes it), ...)


@dataclass
class Network:
    paths: dict = field(default_factory=lambda: defaultdict(list))  # detector → [Path]
    fibers: dict = field(default_factory=dict)  # cord name → flow
    open_ends: list = field(default_factory=list)
    port_power: dict = field(
        default_factory=lambda: defaultdict(float)
    )  # "NAME.port" → W arriving

    def delays(self, detector, source=None) -> list[float]:
        """Propagation delays of every path reaching ``detector`` (optionally from one laser)."""
        return [
            p.delay
            for p in self.paths.get(detector, [])
            if source is None or p.source is source
        ]

    def mean_powers(self, detector) -> dict[float, float]:
        """Time-averaged optical power per wavelength at a detector.

        Paths from the same laser interfere with the mutual coherence γ(Δτ) (see :func:`coherence`);
        different lasers add in power. Time-varying modulation is replaced by its average power
        factor, and keeps two paths coherent only when both see the same modulators at Δτ = 0.
        """
        by_source = defaultdict(list)
        for p in self.paths.get(detector, []):
            factor, dynamic = 1.0 + 0j, []
            for mod, at in p.mods:
                if mod.is_static():
                    factor *= complex(
                        np.asarray(mod.response([mod.static_voltage()]))[0]
                    )
                else:
                    factor *= math.sqrt(mod.mean_factor())
                    dynamic.append(id(mod))
            by_source[p.source].append(
                (p.amp * factor, p.delay, tuple(sorted(dynamic)))
            )
        powers: dict[float, float] = defaultdict(float)
        for src, items in by_source.items():
            total = sum(abs(a) ** 2 for a, _, _ in items)
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    (ai, ti, di), (aj, tj, dj) = items[i], items[j]
                    if di != dj or (di and abs(ti - tj) > 1e-15):
                        continue
                    total += 2 * (ai * aj.conjugate()).real * coherence(src, ti - tj)
            powers[src.wavelength] += max(total, 0.0) * src.power
        return dict(powers)


def coherence(src: Laser, tau: float) -> float:
    """Degree of mutual coherence between a laser's light delayed by τ and itself.

    Lorentzian line: exp(−π·Δν·|τ|), times the overlap of the delayed intensity envelope.
    """
    g = math.exp(-math.pi * src.linewidth * abs(tau))
    if src.mode in ("pulsed", "ook"):
        T = 1.0 / src.rep_rate
        k = round(tau / T)
        if src.mode == "pulsed" and src.phase_randomized and k != 0:
            return 0.0
        r = tau - k * T
        if src.mode == "pulsed":
            g *= math.exp(-r * r / (8 * src.sigma**2))
        else:
            g *= max(0.0, src.duty - abs(r) / T) / src.duty
    return g


def trace(setup: Setup) -> Network:
    net = Network()
    for src in setup.of_type(Laser):
        if src.power <= 0:
            continue
        wl, P = src.wavelength, src.power
        stack = [(src.ports["out"], 1.0 + 0j, 0.0, (), 0)]
        while stack:
            port, amp, delay, mods, hops = stack.pop()
            if hops > MAX_HOPS or abs(amp) ** 2 < MIN_GAIN:
                continue
            peer = port.peer
            if peer is None:
                if port.component.is_cord:  # light leaves through an open connector
                    net.open_ends.append(
                        {
                            "port": port.path,
                            "power": P * abs(amp) ** 2,
                            "wavelength": wl,
                        }
                    )
                continue
            amp *= _CONN
            comp = peer.component
            net.port_power[peer.path] += P * abs(amp) ** 2
            if comp.is_cord:
                flow = net.fibers.setdefault(
                    comp.name, {"fwd": 0.0, "bwd": 0.0, "wavelength": wl}
                )
                flow["fwd" if peer.name == "a" else "bwd"] += P * abs(amp) ** 2
                if P * abs(amp) ** 2 >= max(flow["fwd"], flow["bwd"]):
                    flow["wavelength"] = wl
            if isinstance(comp, _Detector):
                if peer.name == "inp":
                    net.paths[comp].append(Path(src, amp, delay, mods))
                continue
            for tr in comp.scatter(peer, wl):
                new_mods = (
                    mods + ((tr.mod, delay + tr.delay),) if tr.mod is not None else mods
                )
                stack.append(
                    (tr.port, amp * tr.amp, delay + tr.delay, new_mods, hops + 1)
                )
    return net


def synthesize(
    net: Network, detectors, t: np.ndarray, dt: float, rng: np.random.Generator
) -> dict:
    """Complex fields [√W] per wavelength at each detector, bin-averaged over [t, t+dt)."""
    out = {d: {} for d in detectors}
    by_source = defaultdict(list)
    for d in detectors:
        for p in net.paths.get(d, []):
            by_source[p.source].append((d, p))
    for gen in driving_generators(net, detectors):
        gen.start(rng)
    for src, items in by_source.items():
        delays = np.array([p.delay for _, p in items])
        emit = t[None, :] - delays[:, None]  # emission time at the start of each bin
        mid = emit + dt / 2  # …and at its centre (pulse k sits at k/f)
        field_ = src.envelope(emit, dt) * np.exp(1j * src.phase(mid, rng))
        for k, (d, p) in enumerate(items):
            f = p.amp * field_[k]
            for mod, at in p.mods:
                f = f * mod.factor(mid[k] + at, mid[k])
            acc = out[d]
            acc[src.wavelength] = (
                acc[src.wavelength] + f if src.wavelength in acc else f
            )
    return out


def driving_generators(net: Network, detectors) -> set:
    """Function generators feeding a modulator on some path to ``detectors``."""
    gens = set()
    for d in detectors:
        for p in net.paths.get(d, []):
            for mod, _ in p.mods:
                for src, _channel in mod.drivers():
                    gens.add(src)
    return gens


def report(setup: Setup, net: Network | None = None) -> dict:
    """Steady-state read-outs and warnings for every detector, fiber and open connector."""
    net = net or trace(setup)
    detectors, warnings = {}, []
    for d in setup.of_type(_Detector):
        powers = net.mean_powers(d)
        P = sum(powers.values())
        entry = {
            "power": P,
            "readouts": {
                "Incident power": (
                    f"{fmt_si(P, 'W')} · {w_to_dbm(P):.1f} dBm" if P > 0 else "—"
                )
            },
        }
        if isinstance(d, Photodiode):
            I = d.mean_current(powers)
            if P <= 0:
                status = ("No light", "")
            elif I - d.dark_current < 1e-12:
                status = ("Blind at this λ", "warn")
                warnings.append(
                    {
                        "level": "warn",
                        "text": f"{d.name} ({d.material}) does not respond at {_lams(powers)}.",
                    }
                )
            elif I > d.saturation:
                status = ("Saturated", "bad")
                warnings.append(
                    {
                        "level": "warn",
                        "text": f"{d.name} is saturated ({fmt_si(I, 'A')} > {fmt_si(d.saturation, 'A')}). Add attenuation.",
                    }
                )
            else:
                status = ("Linear", "ok")
            entry.update(current=I, status=status[0], status_level=status[1])
            entry["readouts"].update(
                {"Mean photocurrent": fmt_si(I, "A"), "Status": status[0]}
            )
        elif isinstance(d, SinglePhotonDetector):
            rate = _pulse_rate(net, d)
            per = 1 / rate if rate else 1e-9
            nbar = sum(p * per / photon_energy(wl) for wl, p in powers.items())
            seen = sum(  # weighted by the spectral PDE: efficiency × seen is the detected mean
                p * per / photon_energy(wl) * d.spectral_factor(wl)
                for wl, p in powers.items()
            )
            entry["readouts"][
                "Photons / pulse" if rate else "Photons / ns"
            ] = f"{nbar:.3g}"
            if d.curve("efficiency") is not None:
                for wl in sorted(powers):
                    entry["readouts"][f"PDE at {wl * 1e9:.0f} nm"] = f"{d.efficiency_at(wl):.3g}"
                if P > 0 and seen == 0:
                    warnings.append(
                        {
                            "level": "warn",
                            "text": f"{d.name} ({d.preset}) does not detect {_lams(powers)}: outside its spectral response.",
                        }
                    )
            if rate:
                pc = float(d.click_probability(np.array([seen]), 1 / rate)[0])
                entry["readouts"]["Click probability"] = f"{pc:.3g}"
                entry["readouts"]["Expected click rate"] = fmt_si(pc * rate, "Hz")
                cap = d.limits.get("photons_per_pulse")
                if cap is not None and nbar > float(cap):
                    warnings.append(
                        {
                            "level": "bad",
                            "text": f"{d.name} receives {nbar:.3g} photons per pulse, above the absolute maximum of {float(cap):g} for {d.preset}.",
                        }
                    )
            if P > 1e-6:
                warnings.append(
                    {
                        "level": "warn",
                        "text": f"{d.name} receives {fmt_si(P, 'W')} — far too much for a single-photon detector.",
                    }
                )
        elif isinstance(d, PowerMeter):
            entry["reading"] = d.reading(powers)
            entry["readouts"]["Display"] = (
                f"{fmt_si(entry['reading'], 'W')}  ({w_to_dbm(entry['reading']):.2f} dBm)"
                if entry["reading"] > 0
                else "—"
            )
        detectors[d.name] = entry
    for c in setup:  # absolute maximum ratings of parts with a datasheet preset
        lim = c.limits
        if not lim:
            continue
        cap = lim.get("optical_input_w")
        if cap is None and "optical_input_dbm" in lim:
            cap = dbm_to_w(float(lim["optical_input_dbm"]))
        if cap is not None and (P := _incident_power(net, c)) > cap:
            warnings.append(
                {
                    "level": "bad",
                    "text": f"{c.name} receives {fmt_si(P, 'W')}, above the absolute maximum optical input of {fmt_si(cap, 'W')} for {c.preset}.",
                }
            )
        if not isinstance(c, _Modulator):
            continue
        rng = lim.get("bias_voltage_v")
        if rng is not None:
            lo, hi = (
                (float(rng[0]), float(rng[1]))
                if isinstance(rng, (list, tuple))
                else (-abs(float(rng)), abs(float(rng)))
            )
            mean, _ = _port_level(c, "bias")
            if not lo <= mean <= hi:
                warnings.append(
                    {
                        "level": "bad",
                        "text": f"{c.name} bias is {mean:g} V, outside the absolute maximum {lo:g} … {hi:g} V for {c.preset}.",
                    }
                )
        rf_dbm = lim.get("rf_input_dbm")
        if rf_dbm is not None:
            _, peak = _port_level(c, "rf")
            v_max = math.sqrt(2 * dbm_to_w(float(rf_dbm)) * 50.0)  # sine of that power into 50 Ω
            if peak > v_max:
                warnings.append(
                    {
                        "level": "bad",
                        "text": f"{c.name} RF drive peaks at {peak:.3g} V, above the absolute maximum RF input of {rf_dbm:g} dBm ({v_max:.3g} V peak into 50 Ω) for {c.preset}.",
                    }
                )
    for g in setup.of_type(FunctionGenerator):
        for ch, pat in g.patterns.items():
            if getattr(g, f"{ch}_shape") == "pattern" and pat is None:
                warnings.append(
                    {
                        "level": "warn",
                        "text": f"{g.name} {ch.upper()} is set to 'pattern' but none is loaded (set_pattern).",
                    }
                )
    for e in net.open_ends:
        if e["power"] > 1e-6:
            vis = "" if e["wavelength"] < 700e-9 else " (invisible infrared!)"
            warnings.append(
                {
                    "level": "bad",
                    "text": f"Open connector {e['port']} emits {fmt_si(e['power'], 'W')} at {e['wavelength'] * 1e9:.0f} nm{vis}.",
                }
            )
    return {
        "detectors": detectors,
        "fibers": net.fibers,
        "open_ends": net.open_ends,
        "warnings": warnings,
    }


def _incident_power(net: Network, comp) -> float:
    """Mean optical power entering a component through all its optical ports."""
    if isinstance(comp, _Detector):
        return sum(net.mean_powers(comp).values())
    return sum(
        net.port_power.get(p.path, 0.0) for p in comp.ports.values() if p.kind == "optical"
    )


def _port_level(mod, port: str) -> tuple[float, float]:
    """(mean, peak |V|) of what drives a modulator's rf or bias input, as volts at that port."""
    src = mod._source(port)
    if src is None:
        return 0.0, 0.0
    gen, ch = src
    if gen.is_static(ch):
        v = float(gen.static_voltage(ch))
        return v, abs(v)
    w = np.asarray(gen.waveform(ch), dtype=float)
    return float(w.mean()), float(np.abs(w).max())


def _pulse_rate(net: Network, d) -> float | None:
    rates = {
        p.source.rep_rate for p in net.paths.get(d, []) if p.source.mode == "pulsed"
    }
    return max(rates) if rates else None


def _lams(powers: dict) -> str:
    return ", ".join(f"{wl * 1e9:.0f} nm" for wl in powers)
