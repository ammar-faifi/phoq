# Virtual lab — 3D visualizer for `phoq`

A browser view of an optics setup that lives in Python (Three.js, no build step). The page does
no physics. It renders the components the Python server reports, lays them out automatically,
streams oscilloscope traces and photon counts, and sends every edit back to the Python objects.

## Run

From the project root:

```bash
phoq serve examples/basic_link.py
```

Then open http://127.0.0.1:8000. The FastAPI server serves this folder as well as the API.
Other setups are in `examples/` (`delay_line.py`, `mzi_interference.py`, `modulated_link.py`,
`mpqkd.py`), or
pass your own script that defines `setup`. To use another server, open the page with
`?api=http://host:port`. Three.js 0.170 loads from the jsDelivr CDN.

## What you can do

| Action | Effect in Python |
| --- | --- |
| Edit a parameter in the Inspector | `setattr(component, name, value)`, validated, then autosaved |
| Drag a fiber connector onto a free port / away from it | `setup.connect(...)` / `setup.disconnect(...)` |
| Pick a channel source in the scope window or Inspector | connects `pd.rf` ↔ `osc.chN` (a generator output can be viewed too) |
| Inspector → Electrical connections on a modulator | plugs a function-generator channel into `mod.rf` or a DC-supply output into `mod.bias`; the modulator has no drive of its own |
| Click or drag a toolbox item | adds an instance of that Python class |
| `Del` | `setup.remove(...)` |
| `Space` on a laser | toggles `enabled` |
| AUTOSET / RUN | `Oscilloscope.autoset()` / the `running` parameter |
| SINGLE | `Oscilloscope.arm()`: the scope waits for the first trigger, shows that one sweep and holds it (RUN resumes) |
| BW / Sample in the scope window | the `bandwidth` and `sample_rate` parameters; the screen's status line shows the effective rate, bandwidth and record length |
| Laptops (top bar) | one tab per laptop — a party's (`--protocol` / `--party`) or the lab PC (`--analysis`) — with its processes' output, status, read-outs and network counters, Stop / Restart (re-imports the script) / Clear; "Load script" attaches another file. Laptops sit along the front of the table and open their tab when clicked |
| Switch and Ethernet cables | the parties' classical channel (TCP on localhost); the switch is visual only, its port LEDs blink when a laptop sends or receives |
| Export .py | downloads `setup.to_python()` |

Moving or rotating parts (`R`) only changes the layout. That is remembered in this browser per
setup, and **Auto layout** re-arranges everything along the light path.

The **table** button in the top bar picks the optical-table size: presets from a 0.9 × 0.6 m
breadboard to a 4.8 × 1.8 m (16′ × 6′) table, or any custom width × depth from 600 × 450 mm to
5000 × 2000 mm. By default the parts are re-arranged to use the new space. Untick that to keep
them where they are; anything that no longer fits is pulled back onto the table. **Fit to
setup** chooses the smallest preset the current layout fits on. A setup you open for the first
time gets that size automatically (at least the standard 1.5 × 0.9 m), and each setup remembers
its table. The table size is a view setting only; the Python setup doesn't change.

If the server stops, the page shows the start command and reconnects on its own.

## Files

```
index.html          layout, import map
style.css           dark theme
js/main.js          Lab: mirrors server state into the scene, routes commands
js/api.js           REST + WebSocket client
js/parts.js         3D models per Python type (generic fallback) and coax cables
js/fiber.js         patch-cord visuals: draggable connectors, light flow, open-end glow
js/layout.js        automatic placement
js/scope.js         scope display (also the 3D screen texture) and scope window
js/interaction.js   picking, dragging, port snapping, keyboard, toolbox drops
js/ui.js            toolbox, generated inspector, warnings, toasts, help
js/scene.js         renderer, camera, lights, table, shelf
js/textures.js      procedural canvas textures
js/util.js          formatting helpers
```
