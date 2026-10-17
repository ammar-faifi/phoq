// Entry point. The Lab mirrors the Python setup served by `phoq serve …`:
// it renders the components the server reports, lays them out, and sends every edit back.
import * as THREE from 'three';
import { createScene, HOME_VIEW } from './scene.js';
import { Api } from './api.js';
import { Part, Coax } from './parts.js';
import { FiberVisual, REST_Y } from './fiber.js';
import { ScopePanel } from './scope.js';
import { Interaction } from './interaction.js';
import { UI } from './ui.js';
import { ConsoleDrawer } from './console.js';
import { Laptop, Switch, EthernetCable, LAPTOP, SWITCH, FRONT_STRIP } from './laptop.js';
import { autoLayout, layoutShelf, placeNew, restFibers, suggestTableSize, reserveFront } from './layout.js';
import { TABLE, TABLE_PRESETS } from './util.js';

const split = (path) => { const i = path.lastIndexOf('.'); return [path.slice(0, i), path.slice(i + 1)]; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class Lab {
  constructor() {
    this.viewport = document.getElementById('viewport');
    Object.assign(this, createScene(this.viewport));
    this.api = new Api();
    this.parts = new Map();          // name → Part (everything except patch cords)
    this.fibers = new Map();         // name → FiberVisual
    this.coax = new Map();           // "a|b" → Coax
    this.counters = new Map();       // name → latest photon-counter result
    this.specs = new Map();          // "Type.param" → Param spec
    this.presets = new Map();        // Type → part presets (generic first)
    this.state = null;
    this.report = null;
    this.selected = null;
    this.showFlow = true;
    this.dragEnd = null;
    this.pendingPos = new Map();
    this.lastReadout = 0;

    this.selBox = new THREE.BoxHelper(undefined, 0x38bdf8);
    this.selBox.visible = false;
    this.selBox.raycast = () => {};
    this.scene.add(this.selBox);

    this.ui = new UI(this);
    this.consoles = new ConsoleDrawer(this);
    this.laptops = new Map();        // laptop name → Laptop on the table
    this.netSwitch = null;
    this.ethernet = new Map();       // laptop name → EthernetCable
    this.scopePanel = new ScopePanel(this);
    this.interaction = new Interaction(this);

    this.api.on('online', (ok) => { this.ui.setOnline(ok); if (ok) this.loadCatalog(); });
    this.api.on('state', (s) => this.applyState(s));
    this.api.on('report', (r) => this.applyReport(r));
    this.api.on('frame', (f) => this.parts.get(f.instrument)?.display?.setFrame(f));
    this.api.on('counter', (c) => { this.counters.set(c.instrument, c); this.parts.get(c.instrument)?.showCounts?.(c); });
    this.api.on('console', (c) => this.onConsole(c));
    this.api.on('error', (e) => this.ui.toast(`Server: ${e.message}`, 'err'));

    new ResizeObserver(() => this.resize()).observe(this.viewport);
    this.clock = new THREE.Clock();
    this.viewAnim = null;
    this.renderer.setAnimationLoop(() => this.tick());
    this.boot();
  }

  async boot() {
    this.ui.setOnline(false);
    while (!(await this.loadCatalog())) await sleep(1500);
    this.api.open();
  }

  async loadCatalog() {
    try {
      const cat = await this.api.catalog();
      this.specs.clear();
      for (const t of cat.types) for (const p of t.params) this.specs.set(`${t.type}.${p.name}`, p);
      this.presets = new Map(cat.types.map((t) => [t.type, t.presets ?? []]));
      this.ui.buildToolbox(cat);
      return true;
    } catch {
      return false;
    }
  }

  spec(type, key) { return this.specs.get(`${type}.${key}`); }

  // ---- server → scene ----------------------------------------------------------------------
  applyState(s) {
    const first = !this.state || this.state.name !== s.name;
    if (first) this.clearScene();
    this.state = s;
    this.ui.setTitle(s.name);
    if (s.laptops?.length) { reserveFront(LAPTOP.d + 11); this.ensureLaptops(s.laptops); }
    const names = new Set(s.components.map((c) => c.name));
    for (const [n, p] of this.parts) if (!names.has(n)) { p.dispose(); this.parts.delete(n); }
    for (const [n, f] of this.fibers) if (!names.has(n)) { f.dispose(); this.fibers.delete(n); }

    const added = [];
    for (const c of s.components) {
      if (c.category === 'cord') {
        const f = this.fibers.get(c.name);
        if (f) f.refresh(c); else this.fibers.set(c.name, new FiberVisual(this, c));
        continue;
      }
      let p = this.parts.get(c.name);
      if (p && p.type !== c.type) { p.dispose(); this.parts.delete(c.name); p = null; }
      if (p) { p.refresh(c); continue; }
      p = new Part(this, c);
      this.scene.add(p.group);
      this.parts.set(c.name, p);
      added.push(p);
    }

    // Fiber connectors follow the server's connections (except the one in the user's hand).
    for (const c of s.components) {
      if (c.category !== 'cord') continue;
      for (const e of this.fibers.get(c.name).ends) {
        if (e === this.dragEnd) continue;
        const peer = c.ports.find((p) => p.name === e.portName)?.peer;
        const [pn, port] = peer ? split(peer) : [];
        const part = peer && this.parts.get(pn);
        if (part) e.attached = { part, port };
        else if (e.attached) { e.attached = null; e.group.position.y = REST_Y; e.placed = true; }
      }
    }

    if (first) { this.initialTable(); this.initialLayout(); }
    else {
      for (const p of added) {
        if (p.onShelf) layoutShelf(this);
        else placeNew(this, p, this.pendingPos.get(p.name));
        p.placed = true;
      }
      if (added.length) this.rememberLayout();
    }
    restFibers(this, false);
    this.syncCoax(s);
    if (this.selected && !names.has(this.selected)) this.selected = null;
    this.scopePanel.sync();
    this.ui.render();
  }

  clearScene() {
    for (const p of this.parts.values()) p.dispose();
    for (const f of this.fibers.values()) f.dispose();
    for (const c of this.coax.values()) c.dispose();
    for (const l of this.laptops.values()) l.dispose();
    for (const c of this.ethernet.values()) c.dispose();
    this.netSwitch?.dispose();
    this.netSwitch = null;
    this.parts.clear(); this.fibers.clear(); this.coax.clear(); this.counters.clear(); this.laptops.clear(); this.ethernet.clear();
    this.selected = null;
  }

  // ---- table size (a visual choice, remembered per setup in this browser) ------------------
  tableKey() { return `phoq.table.${this.state?.name}`; }

  /** Saved size for this setup, else the smallest preset the auto layout fits (≥ standard). */
  initialTable() {
    let dims = null;
    try { dims = JSON.parse(localStorage.getItem(this.tableKey()) ?? 'null'); } catch { /* storage unavailable */ }
    if (!dims) {
      const need = suggestTableSize(this);
      dims = TABLE_PRESETS.find((t) => t.w >= Math.max(need.w, 150) && t.d >= Math.max(need.d, 90)) ?? need;
    }
    this.resizeTable(dims.w, dims.d, { initial: true });
  }

  /**
   * Rebuild the table at w × d cm. Parts stay where they are (pulled back onto smaller tables),
   * or are re-arranged to use the space when `relayout` is set.
   */
  resizeTable(w, d, { relayout = false, initial = false } = {}) {
    const dims = this.setTableSize(w, d);
    this.ui.setTableSize(dims);
    this.placeLaptops();
    if (initial) {
      this.camera.position.copy(HOME_VIEW.pos);
      this.controls.target.copy(HOME_VIEW.target);
      return dims;
    }
    try { localStorage.setItem(this.tableKey(), JSON.stringify(dims)); } catch { /* storage unavailable */ }
    if (relayout) this.relayout();
    else {
      for (const p of this.parts.values()) if (!p.onShelf) p.setPose(p.group.position.x, p.group.position.z);
      layoutShelf(this);
      for (const f of this.fibers.values()) {
        for (const e of f.ends) {
          if (!e.attached && (Math.abs(e.position.x) > TABLE.width / 2 || Math.abs(e.position.z) > TABLE.depth / 2)) e.placed = false;
        }
      }
      restFibers(this, false);
      this.rememberLayout();
    }
    this.resetView();
    return dims;
  }

  initialLayout() {
    const saved = this.loadLayout();
    const known = [...this.parts.values()].filter((p) => !p.onShelf && saved[p.name]);
    if (!known.length) { autoLayout(this); for (const p of this.parts.values()) p.placed = true; this.rememberLayout(); return; }
    for (const p of known) { const [x, z, rot] = saved[p.name]; p.setPose(x, z, rot); p.placed = true; }
    layoutShelf(this);
    for (const p of this.parts.values()) if (!p.placed) { placeNew(this, p); p.placed = true; }
  }

  syncCoax(s) {
    const want = new Map();
    for (const [a, b] of s.connections) {
      const [an, ap] = split(a), [bn, bp] = split(b);
      const pa = this.parts.get(an), pb = this.parts.get(bn);
      if (pa?.ports.get(ap)?.kind === 'electrical' && pb) want.set(`${a}|${b}`, [{ part: pa, port: ap }, { part: pb, port: bp }]);
    }
    for (const [k, c] of this.coax) if (!want.has(k)) { c.dispose(); this.coax.delete(k); }
    for (const [k, [a, b]] of want) if (!this.coax.has(k)) this.coax.set(k, new Coax(this, a, b));
  }

  applyReport(r) {
    this.report = r;
    for (const [n, f] of this.fibers) {
      f.flow = r.fibers[n] ?? { fwd: 0, bwd: 0, wavelength: 1550e-9 };
      for (const e of f.ends) e.emit = 0;
    }
    for (const o of r.open_ends) {
      const [fn, pn] = split(o.port);
      const e = this.fibers.get(fn)?.ends.find((x) => x.portName === pn);
      if (e) { e.emit = o.power; e.emitLambda = o.wavelength; }
    }
    for (const [n, p] of this.parts) p.setReport(r.detectors[n]);
    this.ui.updateWarnings(r);
    const now = performance.now();
    if (now - this.lastReadout > 250) { this.lastReadout = now; this.ui.updateReadouts(); }
  }

  /** Console output of a process: update the drawer and the laptop it belongs to. */
  onConsole(c) {
    const p = this.consoles.onMessage(c);
    if (!this.laptops.has(p.laptop)) this.ensureLaptops([{ name: p.laptop, kind: c.kind === 'party' ? 'party' : 'lab', port: c.port, host: c.host }]);
    this.laptops.get(p.laptop)?.draw(this.consoles.laptopRecord(p.laptop));
    if (p.traffic) this.netSwitch?.blink(this.laptops.get(p.laptop)?.index ?? 0);
  }

  /** Laptops the server declares: one per party, plus the lab PC for plain analysis tasks. */
  ensureLaptops(list) {
    this.consoles.setLaptops(list);
    let added = false;
    for (const info of list) {
      if (this.laptops.has(info.name)) continue;
      if (!this.netSwitch) this.netSwitch = new Switch(this);
      const laptop = new Laptop(this, info, this.laptops.size);
      this.laptops.set(info.name, laptop);
      this.ethernet.set(info.name, new EthernetCable(this, laptop, this.netSwitch, laptop.index));
      this.netSwitch.setLinked(laptop.index, true);
      added = true;
    }
    if (added) this.placeLaptops();
  }

  /** Laptops along the front strip of the table, the switch in the front-right corner. */
  placeLaptops() {
    if (!this.netSwitch) return;
    const z = TABLE.depth / 2 - FRONT_STRIP;
    this.netSwitch.place(TABLE.width / 2 - SWITCH.w / 2 - 3, z);
    const n = this.laptops.size;
    const xMin = -TABLE.width / 2 + LAPTOP.w / 2 + 3, xMax = TABLE.width / 2 - SWITCH.w - 8 - LAPTOP.w / 2;
    [...this.laptops.values()].forEach((l, i) => l.place(n > 1 ? xMin + (i * (xMax - xMin)) / (n - 1) : (xMin + xMax) / 2, z));
    for (const c of this.ethernet.values()) c.key = '';
  }
  taskAction(name, action) { return this.call(this.api.taskAction(name, action)); }
  loadScript(path) { return this.call(this.api.loadScript(path), `Loaded ${path.split('/').pop()}`); }

  // ---- commands → server -------------------------------------------------------------------
  async call(promise, ok) {
    try {
      const r = await promise;
      if (ok) this.ui.toast(ok, 'ok');
      return r ?? true;
    } catch (e) {
      this.ui.toast(e.message, 'err');
      return null;
    }
  }

  async add(type, at) {
    const r = await this.call(this.api.add(type));
    if (!r) return;
    const part = this.parts.get(r.name);
    if (at && part && !part.onShelf) { part.setPose(at.x, at.z); this.rememberLayout(); }
    else if (at) this.pendingPos.set(r.name, at);
    this.select(r.name);
    this.ui.toast(`Added ${r.name}`);
  }
  setParams(name, params) { return this.call(this.api.patch(name, params)); }
  setPreset(name, id) { return this.call(this.api.setPreset(name, id), `${name} is now ${id === 'generic' || id.startsWith('generic/') ? 'generic' : id}`); }
  remove(name) { return this.call(this.api.remove(name), `Removed ${name}`); }
  autoset(name) { return this.call(this.api.autoset(name)); }
  single(name) { return this.call(this.api.single(name)); }
  connect(a, b) { return this.call(this.api.connect(a, b), `${a} → ${b}`); }
  disconnect(port, quiet = false) { return this.call(this.api.disconnect(port), quiet ? null : `Unplugged ${port}`); }

  unplugEnd(end) {
    const was = end.attached;
    end.attached = null;
    this.api.disconnect(end.path)
      .then(() => this.ui.toast(`${end.path} unplugged from ${was.part.name}.${was.port}`, 'off'))
      .catch((e) => this.ui.toast(e.message, 'err'));
  }

  plugEnd(end, part, port) {
    end.attached = { part, port };
    end.seat(part, port);
    this.api.connect(end.path, `${part.name}.${port}`)
      .then(() => this.ui.toast(`${end.path} → ${part.name}.${port}`, 'ok'))
      .catch((e) => {
        end.attached = null;
        end.group.position.y = REST_Y;
        this.ui.toast(e.message, 'err');
      });
  }

  /** An optical port is free if neither the server nor an optimistic local plug uses it. */
  portFree(partName, port) {
    const c = this.state?.components.find((x) => x.name === partName);
    if (c?.ports.find((p) => p.name === port)?.peer) return false;
    for (const f of this.fibers.values()) for (const e of f.ends) if (e.attached?.part.name === partName && e.attached.port === port) return false;
    return true;
  }

  select(name) {
    if (this.selected === name) return;
    this.selected = name;
    this.ui.render();
  }

  // ---- layout memory (per setup, in this browser only) ----------------------------------
  layoutKey() { return `phoq.layout.${this.state?.name}`; }
  loadLayout() {
    try { return JSON.parse(localStorage.getItem(this.layoutKey()) ?? '{}'); } catch { return {}; }
  }
  rememberLayout() {
    const out = {};
    for (const p of this.parts.values()) if (!p.onShelf) out[p.name] = [p.group.position.x, p.group.position.z, p.rot];
    try { localStorage.setItem(this.layoutKey(), JSON.stringify(out)); } catch { /* storage unavailable */ }
  }
  relayout() {
    autoLayout(this);
    for (const p of this.parts.values()) p.placed = true;
    this.rememberLayout();
  }

  // ---- view ----------------------------------------------------------------------------------
  resize() {
    const w = this.viewport.clientWidth, h = this.viewport.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  resetView() {
    this.viewAnim = { t: 0, fromPos: this.camera.position.clone(), fromTarget: this.controls.target.clone() };
  }

  tick() {
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;
    if (this.viewAnim) {
      const a = this.viewAnim;
      a.t = Math.min(1, a.t + dt / 0.7);
      const k = 1 - Math.pow(1 - a.t, 3);
      this.camera.position.lerpVectors(a.fromPos, HOME_VIEW.pos, k);
      this.controls.target.lerpVectors(a.fromTarget, HOME_VIEW.target, k);
      if (a.t >= 1) this.viewAnim = null;
    }
    this.controls.update();

    for (const p of this.parts.values()) {
      p.update(dt, t);
      if (p.display?.dirty) { p.display.draw(); p.screenTex.needsUpdate = true; }
    }
    for (const f of this.fibers.values()) f.update(dt, this.showFlow);
    for (const c of this.coax.values()) c.update();
    for (const c of this.ethernet.values()) c.update();
    this.netSwitch?.update(performance.now());
    this.interaction.update(t);

    const sel = this.selected && (this.parts.get(this.selected)?.group ?? this.fibers.get(this.selected)?.mesh);
    this.selBox.visible = !!sel;
    if (sel) this.selBox.setFromObject(sel);
    this.scopePanel.render(performance.now());
    this.renderer.render(this.scene, this.camera);
  }
}

window.lab = new Lab();
