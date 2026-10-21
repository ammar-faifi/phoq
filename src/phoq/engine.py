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
from .detectors import BalancedPhotodetector, Photodiode, PowerMeter, SinglePhotonDetector, _Detector
from .generators import FunctionGenerator
from .modulators import _Modulator
from .passive import OpticalDelayLine
from .sources import Laser
from .units import (
    FRESNEL,
    MISMATCH_LOSS_DB,
    angled,
    dbm_to_w,
    fmt_si,
    mating,
    open_end_reflection,
    photon_energy,
    w_to_dbm,
)

MAX_HOPS = 40
MIN_GAIN = 1e-18
MAX_REFLECTIONS = 1  # reflections followed per path


@dataclass
class Path:
    source: Laser
    amp: complex
    delay: float
    mods: tuple = ()  # ((modulator, delay at which the light passes it), ...)
    port: str = "inp"  # the detector input it enters (a balanced detector has two)


@dataclass
class Network:
    paths: dict = field(default_factory=lambda: defaultdict(list))  # detector → [Path]
    fibers: dict = field(default_factory=dict)  # cord name → flow
    open_ends: list = field(default_factory=list)
    port_power: dict = field(
        default_factory=lambda: defaultdict(float)
    )  # "NAME.port" → W arriving
    # laser → {(own light?, reflection site or other laser's name) → W entering its output}
    returned: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))

    def delays(self, detector, source=None) -> list[float]:
        """Propagation delays of every path reaching ``detector`` (optionally from one laser)."""
        return [
            p.delay
            for p in self.paths.get(detector, [])
            if source is None or p.source is source
        ]

    def mean_powers(self, detector, port: str | None = None) -> dict[float, float]:
        """Time-averaged optical power per wavelength at a detector.

        Paths from the same laser interfere with the mutual coherence γ(Δτ) (see :func:`coherence`);
        different lasers add in power. Time-varying modulation is replaced by its average power
        factor, and keeps two paths coherent only when both see the same modulators at Δτ = 0.
        """
        by_source = defaultdict(list)
        for p in self.paths.get(detector, []):
            if port is not None and p.port != port:
                continue
            factor, dynamic = 1.0 + 0j, []
            for mod, at in p.mods:
                if mod.is_static():
                    factor *= mod.static_factor()
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
    """Follow every laser's light through the setup.

    Each mated connector pair transmits and reflects according to its two connector types
    (:func:`~phoq.units.mating`), and an open connector reflects back into its fiber. Up to
    ``MAX_REFLECTIONS`` reflections are followed per path: one is enough for the light returning
    to the lasers and for reflectometry-like paths to detectors; a second (a Fabry–Pérot pass)
    is ≤ −80 dB for matching connectors.
    """
    net = Network()
    for src in setup.of_type(Laser):
        if src.power <= 0:
            continue
        wl, P = src.wavelength, src.power
        # (port the light leaves through, amplitude, delay, modulators, hops, reflections, site)
        stack = [(src.ports["out"], 1.0 + 0j, 0.0, (), 0, 0, None)]
        while stack:
            port, amp, delay, mods, hops, refl, site = stack.pop()
            if hops > MAX_HOPS or abs(amp) ** 2 < MIN_GAIN:
                continue
            peer, via = port.peer, ""
            if peer is not None and peer.component.is_adaptor:
                # A mating sleeve: the connector here mates with the one in the sleeve's other side.
                sleeve = peer.component
                peer, via = sleeve.other(peer.name), f" in {sleeve.name}"
                if peer is not None and peer.component.is_adaptor:
                    continue  # sleeve plugged into a sleeve: nothing to mate with
            if peer is None:
                if port.component.is_cord:  # an open connector: light leaves, a flat end reflects 3.6 %
                    r = open_end_reflection(port.connector)
                    net.open_ends.append(
                        {
                            "port": port.path,
                            "power": P * abs(amp) ** 2 * (1 - r),
                            "wavelength": wl,
                        }
                    )
                    if refl < MAX_REFLECTIONS:
                        here = f"open {port.connector} end {port.path}{via}"
                        _enter(net, stack, src, port, amp * math.sqrt(r), delay, mods, hops, refl + 1, here)
                continue
            t, r_back, _ = mating(port.connector, peer.connector)
            if refl < MAX_REFLECTIONS and r_back > 0:
                here = f"{port.path} ({port.connector}) ↔ {peer.path} ({peer.connector}){via}"
                _enter(net, stack, src, port, amp * math.sqrt(r_back), delay, mods, hops, refl + 1, here)
            _enter(net, stack, src, peer, amp * math.sqrt(t), delay, mods, hops, refl, site)
    return net


def _enter(net, stack, src, port, amp, delay, mods, hops, refl, site) -> None:
    """Light of ``src`` enters ``port.component`` through ``port``."""
    comp, wl = port.component, src.wavelength
    w = src.power * abs(amp) ** 2
    net.port_power[port.path] += w
    if comp.is_cord:
        flow = net.fibers.setdefault(comp.name, {"fwd": 0.0, "bwd": 0.0, "wavelength": wl})
        flow["fwd" if port.name == "a" else "bwd"] += w
        if w >= max(flow["fwd"], flow["bwd"]):
            flow["wavelength"] = wl
    if isinstance(comp, _Detector):
        if port.name in comp.inputs:
            net.paths[comp].append(Path(src, amp, delay, mods, port.name))
        return
    if isinstance(comp, Laser):
        own = comp is src
        net.returned[comp][(own, (site or "a fiber loop") if own else src.name)] += w
        return
    for tr in comp.scatter(port, wl):
        new_mods = mods + ((tr.mod, delay + tr.delay),) if tr.mod is not None else mods
        stack.append((tr.port, amp * tr.amp, delay + tr.delay, new_mods, hops + 1, refl, site))


def synthesize(
    net: Network,
    detectors,
    t: np.ndarray,
    dt: float,
    rng: np.random.Generator,
    offsets: dict | None = None,
) -> dict:
    """Complex fields [√W] per wavelength at each detector, bin-averaged over [t, t+dt); for a
    detector with several optical inputs (a balanced detector) keyed by (input port, wavelength).

    ``offsets`` shifts a detector's bins to [t + offset, t + offset + dt) (a time tagger's channel
    delay). All detectors still share one phase walk per laser, so their fields stay correlated.
    """
    out = {d: {} for d in detectors}
    by_source = defaultdict(list)
    for d in detectors:
        for p in net.paths.get(d, []):
            by_source[p.source].append((d, p))
    for gen in driving_generators(net, detectors):
        gen.start(rng)
    offsets = offsets or {}
    for src, items in by_source.items():
        delays = np.array([p.delay - offsets.get(d, 0.0) for d, p in items])
        emit = t[None, :] - delays[:, None]  # emission time at the start of each bin
        mid = emit + dt / 2  # …and at its centre (pulse k sits at k/f)
        field_ = src.envelope(emit, dt) * np.exp(1j * src.phase(mid, rng))
        for k, (d, p) in enumerate(items):
            f = p.amp * field_[k]
            for mod, at in p.mods:
                f = f * mod.factor(mid[k] + at, mid[k])
            acc = out[d]
            key = src.wavelength if len(d.inputs) == 1 else (p.port, src.wavelength)
            acc[key] = acc[key] + f if key in acc else f
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
        if isinstance(d, BalancedPhotodetector):
            entry.update(_balanced(d, net, warnings))
        elif isinstance(d, Photodiode):
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
        for port in c.bias_ports if rng is not None else ():
            lo, hi = (
                (float(rng[0]), float(rng[1]))
                if isinstance(rng, (list, tuple))
                else (-abs(float(rng)), abs(float(rng)))
            )
            mean, _ = _port_level(c, port)
            if not lo <= mean <= hi:
                warnings.append(
                    {
                        "level": "bad",
                        "text": f"{c.name} {port} is {mean:g} V, outside the absolute maximum {lo:g} … {hi:g} V for {c.preset}.",
                    }
                )
        rf_dbm = lim.get("rf_input_dbm")
        for port in c.rf_ports if rf_dbm is not None else ():
            _, peak = _port_level(c, port)
            v_max = math.sqrt(2 * dbm_to_w(float(rf_dbm)) * 50.0)  # sine of that power into 50 Ω
            if peak > v_max:
                drive = "RF drive" if port == "rf" else f"RF drive on {port}"
                warnings.append(
                    {
                        "level": "bad",
                        "text": f"{c.name} {drive} peaks at {peak:.3g} V, above the absolute maximum RF input of {rf_dbm:g} dBm ({v_max:.3g} V peak into 50 Ω) for {c.preset}.",
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
    for odl in setup.of_type(OpticalDelayLine):
        if odl.delay > odl.max_delay:
            warnings.append(
                {
                    "level": "warn",
                    "text": f"{odl.name} is set to {fmt_si(odl.delay, 's')} but travels only "
                    f"{fmt_si(odl.max_delay, 's')}: the stage stops at the end of its travel.",
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
    pairs = [  # every mated pair of connectors: direct, or the two held in a mating sleeve
        (a, b, "")
        for a, b in setup.connections()
        if a.kind == "optical" and not (a.component.is_adaptor or b.component.is_adaptor)
    ] + [
        (s.ports["p1"].peer, s.ports["p2"].peer, f" in {s.name}")
        for s in setup
        if s.is_adaptor and s.ports["p1"].peer is not None and s.ports["p2"].peer is not None
    ]
    for a, b, via in pairs:  # APC against PC/UPC: mechanically fits, optically does not
        if a.component.is_adaptor or b.component.is_adaptor or angled(a.connector) == angled(b.connector):
            continue
        flat = a if not angled(a.connector) else b
        warnings.append(
            {
                "level": "bad",
                "text": f"{a.path} ({a.connector}) is mated to {b.path} ({b.connector}){via}: an angled end face "
                f"against a flat one leaves an air gap — about {MISMATCH_LOSS_DB:g} dB lost, "
                f"{FRESNEL:.1%} reflected back into {flat.path}, and both end faces can be damaged. "
                "Use matching connectors or a hybrid cord.",
            }
        )
    return {
        "detectors": detectors,
        "sources": _sources(setup, net),
        "fibers": net.fibers,
        "open_ends": net.open_ends,
        "warnings": warnings,
    }


def _sources(setup: Setup, net: Network) -> dict:
    """Per laser: its own light reflected back into its output (the setup's return loss as the
    laser sees it, with the strongest reflection named) and light injected by other lasers."""
    out = {}
    for src in setup.of_type(Laser):
        came = net.returned.get(src, {})
        own = {where: w for (is_own, where), w in came.items() if is_own}
        other = sum(w for (is_own, _), w in came.items() if not is_own)
        back = sum(own.values())
        readouts = {}
        if src.power > 0:
            readouts["Back-reflection"] = (
                f"{10 * math.log10(back / src.power):.1f} dB · {fmt_si(back, 'W')}"
                if back > 0
                else "—"
            )
            if back > 0:
                site = max(own, key=own.get)
                readouts["Strongest reflection"] = f"{site} ({10 * math.log10(own[site] / src.power):.1f} dB)"
        if other > 0:
            readouts["Light from other lasers"] = fmt_si(other, "W")
        out[src.name] = {"back_reflection": back, "injected": other, "readouts": readouts}
    return out


def _balanced(d: BalancedPhotodetector, net: Network, warnings: list) -> dict:
    """Steady-state read-outs of a balanced detector: each input, RF OUTPUT and the monitors."""
    plus, minus = net.mean_powers(d, "inp_plus"), net.mean_powers(d, "inp_minus")
    p_plus, p_minus = sum(plus.values()), sum(minus.values())
    i_plus, i_minus = d.mean_currents(plus, minus)
    show = lambda p: f"{fmt_si(p, 'W')} · {w_to_dbm(p):.1f} dBm" if p > 0 else "—"  # noqa: E731
    readouts = {"INPUT+": show(p_plus), "INPUT−": show(p_minus)}
    out = {"power": p_plus + p_minus, "current": i_plus - i_minus, "readouts": readouts}
    try:
        _, _, swing = d.setting()
    except ValueError as e:
        warnings.append({"level": "bad", "text": str(e)})
        return out | {"status": "No such gain", "status_level": "bad"}
    rf = d.gain * (i_plus - i_minus)
    mon = (d.monitor_gain * i_plus, d.monitor_gain * i_minus)
    readouts["RF OUTPUT"] = f"{rf:+.4g} V (high-Z), {rf / 2:+.4g} V into 50 Ω"
    readouts["MONITOR+ / MONITOR−"] = f"{mon[0]:.4g} V / {mon[1]:.4g} V"
    if p_plus + p_minus > 0:
        readouts["Balance (P₊ − P₋)/(P₊ + P₋)"] = f"{(p_plus - p_minus) / (p_plus + p_minus):+.2%}"
    if abs(rf) > swing:
        status = ("Saturated", "bad")
        warnings.append({
            "level": "warn",
            "text": f"{d.name} RF OUTPUT would reach {rf:+.3g} V, beyond its ±{swing:g} V swing at "
            f"{fmt_si(d.gain, 'V/A')}: balance the inputs (their difference must stay below "
            f"{fmt_si(d.cw_saturation_power(), 'W')} at peak responsivity) or lower the gain.",
        })
    elif p_plus + p_minus <= 0:
        status = ("No light", "")
    elif min(p_plus, p_minus) <= 0:
        status = ("Single input", "ok")
    else:
        status = ("Balanced", "ok")
    if d.monitor_swing > 0 and max(mon) > d.monitor_swing:
        warnings.append({
            "level": "warn",
            "text": f"{d.name} MONITOR output would reach {max(mon):.3g} V, beyond its {d.monitor_swing:g} V swing: "
            f"more than {fmt_si(d.monitor_swing / d.monitor_gain / max(d.peak_responsivity(), 1e-12), 'W')} on one input.",
        })
    readouts["Status"] = status[0]
    return out | {"rf": rf, "monitors": mon, "status": status[0], "status_level": status[1]}


def _incident_power(net: Network, comp) -> float:
    """Mean optical power entering a component through all its optical ports; for a detector with
    several inputs (each its own photodiode) the most any one of them receives."""
    if isinstance(comp, _Detector):
        return max(sum(net.mean_powers(comp, port).values()) for port in comp.inputs)
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
