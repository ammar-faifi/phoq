// 3D models of the Python lab components. A Part only renders; the physics lives in Python.
// Each builder places the ports named exactly like the Python ports (laser.out, pd.inp, pd.rf …).
import * as THREE from 'three';
import { clamp, snapToHole, TABLE, SHELF, fmtSI, wavelengthColor, CH_COLORS, CONNECTOR_COLORS, connectorColor } from './util.js';
import { makeLabel, makeBadge, makeWindingTexture } from './textures.js';
import { ScopeDisplay } from './scope.js';
import { AboveTable } from './fiber.js';

export const MAT = {
  plate:   new THREE.MeshStandardMaterial({ color: 0x2a2f39, metalness: 0.8, roughness: 0.38 }),
  steel:   new THREE.MeshStandardMaterial({ color: 0xb9c0cc, metalness: 1.0, roughness: 0.28 }),
  bright:  new THREE.MeshStandardMaterial({ color: 0xe2e7ef, metalness: 1.0, roughness: 0.16 }),
  black:   new THREE.MeshStandardMaterial({ color: 0x15181e, metalness: 0.45, roughness: 0.42 }),
  blue:    new THREE.MeshStandardMaterial({ color: 0x1d3557, metalness: 0.55, roughness: 0.36 }),
  gray:    new THREE.MeshStandardMaterial({ color: 0x3a404c, metalness: 0.6, roughness: 0.4 }),
  fin:     new THREE.MeshStandardMaterial({ color: 0x22272f, metalness: 0.7, roughness: 0.35 }),
  gold:    new THREE.MeshStandardMaterial({ color: 0xd6b25e, metalness: 1.0, roughness: 0.3 }),
  brass:   new THREE.MeshStandardMaterial({ color: 0x9c8550, metalness: 0.85, roughness: 0.45 }),
  knob:    new THREE.MeshStandardMaterial({ color: 0x2b2f36, metalness: 0.3, roughness: 0.5 }),
  case:    new THREE.MeshStandardMaterial({ color: 0x232a35, metalness: 0.3, roughness: 0.55 }),
  bezel:   new THREE.MeshStandardMaterial({ color: 0x10141b, metalness: 0.2, roughness: 0.7 }),
  acrylic: new THREE.MeshStandardMaterial({ color: 0xa5d8ff, metalness: 0.1, roughness: 0.1, transparent: true, opacity: 0.28, depthWrite: false }),
};

// Bulkhead colour collar per connector type (shared materials: FC/UPC blue, FC/APC green).
const COLLAR = Object.fromEntries(Object.keys(CONNECTOR_COLORS).map((k) => [k,
  new THREE.MeshStandardMaterial({ color: connectorColor(k), metalness: 0.15, roughness: 0.45 })]));

const GEO = {
  hex: new THREE.CylinderGeometry(0.8, 0.8, 0.34, 6),
  barrel: new THREE.CylinderGeometry(0.42, 0.42, 1.0, 20),
  collar: new THREE.CylinderGeometry(0.66, 0.66, 0.2, 24),   // wider than the plugged nut (0.56)
  ring: new THREE.TorusGeometry(1.05, 0.13, 8, 32),
  screw: new THREE.CylinderGeometry(0.45, 0.45, 0.3, 12),
  jack: new THREE.CylinderGeometry(0.55, 0.55, 1.2, 18),
};

const UP = new THREE.Vector3(0, 1, 0);
export const PORT_TIP = 1.35;
const badgeCache = {};

export class Part {
  constructor(lab, comp) {
    this.lab = lab;
    this.name = comp.name;
    this.type = comp.type;
    this.category = comp.category;
    this.group = new THREE.Group();
    this.group.userData.part = this;
    this.ports = new Map();
    this.footprint = { w: 10, d: 10 };
    this.rot = 0;
    this.onShelf = false;
    this.label = null;
    this.comp = comp;
    (BUILDERS[comp.type] ?? buildGeneric)(this, comp);
    this.addMissingPorts(comp);
    this.badge = new THREE.Sprite(new THREE.SpriteMaterial({ transparent: true, depthWrite: false }));
    this.badge.scale.set(5.2, 2.6, 1);
    this.badge.position.set(0, this.height + 4, 0);
    this.badge.visible = false;
    this.badge.raycast = () => {};
    this.group.add(this.badge);
    this.refresh(comp);
  }

  get height() { return this._height ?? 8; }

  /** Python ports this model doesn't place (e.g. a subclass with extra ports) go on the top. */
  addMissingPorts(comp) {
    const extra = comp.ports.filter((p) => !this.ports.has(p.name));
    extra.forEach((p, i) => {
      const x = -this.footprint.w / 2 + 2 + i * 2.6, y = this.height + 0.4;
      if (p.kind === 'electrical') this.addJack(p.name, x, y, 0, 0, 1);
      else this.addPort(p.name, x, y, 0, 0, 1);
    });
  }

  // ---- geometry helpers -------------------------------------------------------------------
  add(geo, mat, x = 0, y = 0, z = 0) {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = m.receiveShadow = true;
    m.userData.part = this;
    this.group.add(m);
    return m;
  }
  box(w, h, d, mat, x, y, z) { return this.add(new THREE.BoxGeometry(w, h, d), mat, x, y, z); }
  cyl(r, len, mat, x, y, z, axis = 'y', seg = 24) {
    const m = this.add(new THREE.CylinderGeometry(r, r, len, seg), mat, x, y, z);
    if (axis === 'x') m.rotation.z = Math.PI / 2;
    if (axis === 'z') m.rotation.x = Math.PI / 2;
    return m;
  }
  basePlate(w, d) {
    this.footprint = { w, d };
    this.box(w, 0.6, d, MAT.plate, 0, 0.3, 0);
    for (const sx of [-1, 1]) for (const sz of [-1, 1]) this.add(GEO.screw, MAT.steel, sx * (w / 2 - 1.1), 0.75, sz * (d / 2 - 1.1));
  }
  labelPlane(w, h, x, y, z, faceUp = false) {
    const lab = makeLabel({ w: 512, h: Math.round((512 * h) / w) });
    const mat = new THREE.MeshStandardMaterial({ map: lab.texture, emissive: 0xffffff, emissiveMap: lab.texture, emissiveIntensity: 0.32, roughness: 0.65, transparent: true });
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    m.position.set(x, y, z);
    if (faceUp) m.rotation.x = -Math.PI / 2;
    m.userData.part = this;
    this.group.add(m);
    this.label = lab;
    return lab;
  }
  /** Flat display (power-meter / counter readout) with its own canvas. */
  screen(w, h, x, y, z, px = 512) {
    const c = document.createElement('canvas');
    c.width = px; c.height = Math.round((px * h) / w);
    const tex = new THREE.CanvasTexture(c);
    tex.colorSpace = THREE.SRGBColorSpace;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: tex, toneMapped: false }));
    m.position.set(x, y, z);
    m.userData.part = this;
    this.group.add(m);
    return { canvas: c, ctx: c.getContext('2d'), tex, mesh: m };
  }

  /** FC bulkhead receptacle at (x,y,z) on a panel, pointing along (dx,0,dz); its collar shows
   *  the connector type (blue FC/UPC, green FC/APC). */
  addPort(name, x, y, z, dx, dz) {
    const dir = new THREE.Vector3(dx, 0, dz).normalize();
    const holder = new THREE.Group();
    holder.position.set(x, y, z);
    holder.quaternion.setFromUnitVectors(UP, dir);
    const hex = new THREE.Mesh(GEO.hex, MAT.steel);
    hex.position.y = 0.17;
    const barrel = new THREE.Mesh(GEO.barrel, MAT.bright);
    barrel.position.y = 0.85;
    const collar = new THREE.Mesh(GEO.collar, COLLAR['FC/UPC']);
    collar.position.y = 0.44;
    collar.userData.part = this;
    holder.add(collar);
    const ring = new THREE.Mesh(GEO.ring, new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.5, depthWrite: false, toneMapped: false }));
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 1.15;
    ring.visible = false;
    ring.raycast = () => {};
    for (const m of [hex, barrel]) { m.castShadow = true; m.userData.part = this; }
    holder.add(hex, barrel, ring);
    this.group.add(holder);
    this.ports.set(name, { name, kind: 'optical', dir, ring, collar, connector: 'FC/UPC', tipLocal: new THREE.Vector3(x, y, z).addScaledVector(dir, PORT_TIP) });
  }

  /** BNC/SMA jack for electrical ports. */
  addJack(name, x, y, z, dx, dz, mat = MAT.gold) {
    const dir = new THREE.Vector3(dx, 0, dz).normalize();
    const j = this.add(GEO.jack, mat, x + dir.x * 0.6, y, z + dir.z * 0.6);
    j.quaternion.setFromUnitVectors(UP, dir);
    this.ports.set(name, { name, kind: 'electrical', dir, tipLocal: new THREE.Vector3(x, y, z).addScaledVector(dir, 1.2) });
  }

  portWorld(name, pos, dir) {
    const p = this.ports.get(name);
    if (!p) return null;
    this.group.updateMatrixWorld();
    pos.copy(p.tipLocal).applyMatrix4(this.group.matrixWorld);
    if (dir) dir.copy(p.dir).transformDirection(this.group.matrixWorld);
    return pos;
  }

  // ---- placement -------------------------------------------------------------------------
  halfExtents() {
    const { w, d } = this.footprint;
    return this.rot % 2 ? [d / 2, w / 2] : [w / 2, d / 2];
  }
  setPose(x, z, rot = this.rot) {
    if (this.onShelf) { this.setShelf(x); return; }
    this.rot = ((rot % 4) + 4) % 4;
    const [hw, hd] = this.halfExtents();
    x = clamp(snapToHole(x), -TABLE.width / 2 + hw, TABLE.width / 2 - hw);
    z = clamp(snapToHole(z), -TABLE.depth / 2 + hd, TABLE.depth / 2 - hd);
    this.group.position.set(x, 0, z);
    this.group.rotation.y = -this.rot * Math.PI / 2;
    this.group.updateMatrixWorld(true);
  }
  setShelf(x) {
    const hw = this.footprint.w / 2;
    this.group.position.set(clamp(x, SHELF.xMin + hw, SHELF.xMax - hw), SHELF.y, SHELF.z + 1);
    this.group.rotation.y = 0;
    this.group.updateMatrixWorld(true);
  }
  rotate() { if (!this.onShelf) this.setPose(this.group.position.x, this.group.position.z, this.rot + 1); }

  // ---- state from the server -------------------------------------------------------------
  refresh(comp) {
    this.comp = comp;
    for (const p of comp.ports) {   // connector type of each bulkhead → collar colour
      const port = this.ports.get(p.name);
      if (!port?.collar) continue;
      port.connector = p.connector ?? null;   // a sleeve: what its other side holds, if anything
      if (p.connector) port.collar.material = COLLAR[p.connector] ?? COLLAR['FC/UPC'];
    }
    const L = (LABELS[this.type] ?? labelGeneric)(comp);
    this.label?.paint({ title: comp.name, ...L });
    this.onRefresh?.(comp);
    // Detector → instrument channel badge (CH1…)
    const elec = comp.ports.find((p) => p.kind === 'electrical' && p.direction === 'out' && p.peer);
    if (elec) {
      const ch = elec.peer.split('.').pop().toUpperCase();
      const idx = Math.max(0, Number(ch.replace(/\D/g, '')) - 1);
      const key = `${ch}|${idx}`;
      badgeCache[key] ??= makeBadge(ch, CH_COLORS[idx % 4]);
      this.badge.material.map = badgeCache[key];
      this.badge.material.needsUpdate = true;
      this.badge.visible = true;
    } else {
      this.badge.visible = false;
    }
  }
  setReport() {}
  update() {}

  dispose() {
    this.group.removeFromParent();
    this.group.traverse((o) => { if (o.isMesh && o.material?.map?.isCanvasTexture && !o.material.map.shared) o.material.map.dispose(); });
  }
}

// ---------------------------------------------------------------------------------------------
// Builders: geometry and port placement per Python type
// ---------------------------------------------------------------------------------------------
const BUILDERS = {
  Laser(p) {
    p.basePlate(17.5, 10);
    p.box(14, 6.4, 8.4, MAT.blue, -0.6, 3.8, 0);
    for (let i = 0; i < 7; i++) p.box(12.4, 1.3, 0.34, MAT.fin, -1.2, 7.65, -3.3 + i * 1.1);
    p.box(0.4, 6.0, 8.0, MAT.black, 6.6, 3.8, 0);
    p.addPort('out', 6.8, 3.4, 0, 1, 0);
    p.led = p.add(new THREE.SphereGeometry(0.38, 16, 12), new THREE.MeshStandardMaterial({ color: 0x220808 }), 6.85, 5.9, -2.6);
    p.cyl(0.6, 0.5, MAT.steel, 7.0, 5.9, 2.6, 'x');
    p.cyl(0.35, 5, MAT.black, -9.5, 2.0, 0, 'x', 12);
    p.labelPlane(11, 4.2, -0.6, 3.9, 4.22);
    p._height = 8.5;
    p.onRefresh = (c) => {
      const on = c.params.enabled;
      p.led.material.emissive.setHex(on ? wavelengthColor(c.params.wavelength).hex : 0);
      p.led.material.emissiveIntensity = on ? 2.2 : 0;
    };
  },

  Photodiode(p) {
    p.basePlate(10, 7.5);
    p.box(5.6, 5.6, 5.6, MAT.black, 0, 3.4, 0);
    p.box(0.4, 4.6, 4.6, MAT.gray, -2.95, 3.4, 0);
    p.addPort('inp', -3.15, 3.4, 0, -1, 0);
    p.addJack('rf', 2.8, 3.4, 0, 1, 0);
    p.led = p.add(new THREE.SphereGeometry(0.3, 14, 10), new THREE.MeshStandardMaterial({ color: 0x062012, emissive: 0x34d399, emissiveIntensity: 0 }), -1.4, 6.25, -1.4);
    p.labelPlane(5.2, 2.6, 0, 3.6, 2.82);
    p._height = 6.5;
    p.setReport = (e) => {
      const level = clamp((e?.current ?? 0) / 1e-3, 0, 1);
      p.led.material.emissiveIntensity = level > 1e-4 ? 0.4 + 2.2 * Math.sqrt(level) : 0;
    };
  },

  SinglePhotonDetector(p) {
    p.basePlate(12.5, 10);
    p.box(9, 6.5, 7.5, MAT.case, 0, 3.85, 0);
    for (let i = 0; i < 6; i++) p.box(8, 1.4, 0.3, MAT.fin, 0, 7.8, -2.8 + i * 1.12);
    p.box(0.4, 5, 6, MAT.black, -4.7, 3.6, 0);
    p.addPort('inp', -4.9, 3.6, 0, -1, 0);
    p.addJack('out', 4.5, 3.6, 0, 1, 0, MAT.bright);
    p.led = p.add(new THREE.SphereGeometry(0.33, 14, 10), new THREE.MeshStandardMaterial({ color: 0x0a1a24, emissive: 0x22d3ee, emissiveIntensity: 0 }), 2.6, 6.2, 3.8);
    p.labelPlane(7.6, 3, -0.4, 3.6, 3.77);
    p._height = 8.6;
    p.rate = 0;
    p.setReport = (e) => {
      const r = e?.readouts?.['Expected click rate'];
      p.rate = r ? parseRate(r) : 0;
    };
    p.update = (dt) => {
      const blink = p.rate > 0 && Math.random() < Math.min(0.5, p.rate * dt);
      p.led.material.emissiveIntensity = blink ? 3 : Math.max(0, p.led.material.emissiveIntensity - dt * 12);
    };
  },

  PowerMeter(p) {
    p.basePlate(13, 10);
    p.cyl(1.8, 3.5, MAT.black, -3.3, 3.4, 0, 'x');
    p.addPort('inp', -5.05, 3.4, 0, -1, 0);
    p.box(7, 6.5, 8, MAT.case, 2.4, 3.85, 0);
    p.display = p.screen(5.6, 3.2, 2.4, 4.6, 4.02, 384);
    p.labelPlane(5.6, 1.3, 2.4, 1.85, 4.02);
    p._height = 7.2;
    p.setReport = (e) => {
      const { ctx: g, canvas: c, tex } = p.display;
      g.fillStyle = '#081a14'; g.fillRect(0, 0, c.width, c.height);
      g.fillStyle = '#5eead4'; g.textAlign = 'center'; g.textBaseline = 'middle';
      const w = e?.reading ?? 0;
      g.font = `700 ${c.height * 0.36}px ui-monospace, Menlo, monospace`;
      g.fillText(w > 0 ? fmtSI(w, 'W') : '— — —', c.width / 2, c.height * 0.42);
      g.font = `500 ${c.height * 0.2}px ui-monospace, Menlo, monospace`;
      g.fillStyle = '#2dd4bf';
      g.fillText(w > 0 ? `${(10 * Math.log10(w / 1e-3)).toFixed(2)} dBm` : 'no light', c.width / 2, c.height * 0.8);
      tex.needsUpdate = true;
    };
    p.setReport(null);
  },

  BeamSplitter(p) {
    p.basePlate(15, 8);
    p.box(12, 3.2, 6.4, MAT.gray, 0, 2.2, 0);
    p.box(0.4, 3.0, 6.0, MAT.black, -6.1, 2.2, 0);
    p.box(0.4, 3.0, 6.0, MAT.black, 6.1, 2.2, 0);
    p.addPort('a1', -6.3, 2.2, -1.6, -1, 0);
    p.addPort('a2', -6.3, 2.2, 1.6, -1, 0);
    p.addPort('b1', 6.3, 2.2, -1.6, 1, 0);
    p.addPort('b2', 6.3, 2.2, 1.6, 1, 0);
    p.labelPlane(10.6, 4.8, 0, 3.81, 0, true);
    p._height = 4;
  },

  Attenuator(p) {
    p.basePlate(11, 7.5);
    p.box(8, 5, 6, MAT.blue, 0, 3.1, 0);
    p.addPort('p1', -4.1, 3.1, 0, -1, 0);
    p.addPort('p2', 4.1, 3.1, 0, 1, 0);
    p.knob = new THREE.Group();
    p.knob.position.set(0, 5.6, -0.6);
    const k = new THREE.Mesh(new THREE.CylinderGeometry(1.7, 1.8, 1.4, 28), MAT.knob);
    k.position.y = 0.7;
    const mark = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.1, 1.3), new THREE.MeshBasicMaterial({ color: 0xffffff }));
    mark.position.set(0, 1.45, -0.75);
    for (const m of [k, mark]) { m.castShadow = true; m.userData.part = p; }
    p.knob.add(k, mark);
    p.group.add(p.knob);
    p.labelPlane(7.2, 3, 0, 3.1, 3.02);
    p._height = 7.2;
    p.onRefresh = (c) => { p.knob.rotation.y = -(c.params.attenuation_db / 80) * Math.PI * 1.5; };
  },

  /** FC–FC mating sleeve on a small bracket: a receptacle on each side, no polish of its own. */
  MatingSleeve(p) {
    p.basePlate(8, 5);
    p.box(0.45, 4.6, 2.6, MAT.steel, 0, 2.9, 0);        // bracket
    p.box(0.9, 1.9, 1.9, MAT.bright, 0, 3.4, 0);        // square adapter flange
    p.addPort('p1', -0.45, 3.4, 0, -1, 0);
    p.addPort('p2', 0.45, 3.4, 0, 1, 0);
    for (const port of p.ports.values()) port.collar.visible = false;
    p.labelPlane(3.6, 1.0, 0, 0.62, 1.9, true);         // between the front screws
    p._height = 5.2;
  },

  PhaseShifter(p) {
    p.basePlate(10, 6.5);
    p.box(7, 3.4, 4.6, MAT.gray, 0, 2.3, 0);
    p.addPort('p1', -3.5, 2.3, 0, -1, 0);
    p.addPort('p2', 3.5, 2.3, 0, 1, 0);
    p.labelPlane(6.4, 4, 0, 4.01, 0, true);
    p._height = 4.2;
  },

  Isolator(p) {
    p.basePlate(11, 6);
    p.cyl(1.7, 7, MAT.black, 0, 2.6, 0, 'x');
    p.cyl(1.75, 1.2, MAT.steel, -2.4, 2.6, 0, 'x');
    p.cyl(1.75, 1.2, MAT.steel, 2.4, 2.6, 0, 'x');
    p.addPort('p1', -3.5, 2.6, 0, -1, 0);
    p.addPort('p2', 3.5, 2.6, 0, 1, 0);
    p.labelPlane(4.6, 1.6, 0, 4.36, 0, true);
    p._height = 4.5;
  },

  PhaseModulator(p) { buildModulator(p); },
  IntensityModulator(p) { buildModulator(p); },

  FiberSpool(p) {
    p.basePlate(22.5, 22.5);
    p.cyl(10, 0.5, MAT.black, 0, 0.85, 0, 'y', 48);
    const wind = makeWindingTexture();
    wind.repeat.set(6, 1.5);
    p.add(new THREE.CylinderGeometry(8.6, 8.6, 6, 48, 1, true), new THREE.MeshStandardMaterial({ map: wind, roughness: 0.5, metalness: 0.1 }), 0, 4.1, 0);
    p.cyl(3.2, 6.2, MAT.gray, 0, 4.1, 0);
    p.cyl(10, 0.5, MAT.acrylic, 0, 7.35, 0, 'y', 48).castShadow = false;
    p.cyl(1.4, 0.7, MAT.steel, 0, 7.9, 0, 'y', 20);
    p.box(9, 4.2, 1.2, MAT.black, 0, 2.7, 10.4);
    p.addPort('p1', -2.4, 2.7, 11.0, 0, 1);
    p.addPort('p2', 2.4, 2.7, 11.0, 0, 1);
    p.labelPlane(9.5, 3.6, 0, 7.62, -5.6, true);
    p._height = 8.5;
  },

  Oscilloscope(p, comp) {
    p.onShelf = true;
    p.footprint = { w: 46, d: 17 };
    p.box(46, 25, 17, MAT.case, 0, 12.5, 0);
    p.box(44, 23, 0.6, MAT.bezel, 0, 12.5, 8.6);
    p.display = new ScopeDisplay(comp.name);
    p.screenTex = new THREE.CanvasTexture(p.display.canvas);
    p.screenTex.colorSpace = THREE.SRGBColorSpace;
    p.screenTex.anisotropy = 8;
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(28, 17.9), new THREE.MeshBasicMaterial({ map: p.screenTex, toneMapped: false }));
    scr.position.set(-6.5, 13.4, 8.95);
    scr.userData.part = p;
    p.group.add(scr);
    for (const [x, y] of [[12, 19], [17.5, 19], [12, 13.5]]) p.cyl(1.3, 1.2, MAT.steel, x, y, 9.3, 'z');
    for (let i = 0; i < 4; i++) {
      const x = 9.2 + i * 3.4;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.15, 0.22, 8, 24), new THREE.MeshBasicMaterial({ color: CH_COLORS[i] }));
      ring.position.set(x, 5.2, 8.95);
      p.group.add(ring);
      p.addJack(`ch${i + 1}`, x, 5.2, 8.9, 0, 1);
    }
    p.labelPlane(12, 3, 14.5, 23.3, 8.95);
    p._height = 26;
  },

  FunctionGenerator(p, comp) {
    p.onShelf = true;
    p.footprint = { w: 36, d: 16 };
    p.box(36, 12, 16, MAT.case, 0, 6, 0);
    p.box(34, 10, 0.5, MAT.bezel, 0, 6, 8.05);
    p.display = p.screen(19, 7.6, -6.5, 6.6, 8.35, 512);
    for (let i = 0; i < 2; i++) {
      const x = 7.5 + i * 5.5;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.15, 0.22, 8, 24), new THREE.MeshBasicMaterial({ color: CH_COLORS[i] }));
      ring.position.set(x, 3.6, 8.35);
      p.group.add(ring);
      p.addJack(`ch${i + 1}`, x, 3.6, 8.3, 0, 1);
    }
    for (const [x, y] of [[8, 8.6], [13, 8.6]]) p.cyl(1.1, 1.1, MAT.steel, x, y, 8.7, 'z');
    p.labelPlane(9, 2.3, 11.5, 10.7, 8.35);
    p._height = 12;
    p.onRefresh = (c) => drawGeneratorDisplay(p.display, c);
  },

  DCPowerSupply(p, comp) {
    p.onShelf = true;
    p.footprint = { w: 26, d: 16 };
    p.box(26, 12, 16, MAT.case, 0, 6, 0);
    p.box(24, 10, 0.5, MAT.bezel, 0, 6, 8.05);
    p.display = p.screen(13, 7.2, -4.5, 6.6, 8.35, 384);
    PSU_COLORS.forEach((color, i) => {
      const x = 5.5 + i * 5;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(1.15, 0.26, 8, 24), new THREE.MeshBasicMaterial({ color }));
      ring.position.set(x, 3.4, 8.35);
      p.group.add(ring);
      p.addJack(`ch${i + 1}`, x, 3.4, 8.3, 0, 1, MAT.steel);
      p.cyl(1.0, 1.0, MAT.knob, x, 8.2, 8.6, 'z');
    });
    p.labelPlane(7, 2, 8.3, 10.8, 8.35);
    p._height = 12;
    p.onRefresh = (c) => drawSupplyDisplay(p.display, c);
  },

  PhotonCounter(p) {
    p.onShelf = true;
    p.footprint = { w: 32, d: 16 };
    p.box(32, 12, 16, MAT.case, 0, 6, 0);
    p.display = p.screen(17, 7.4, -5.5, 6.9, 8.02, 512);
    for (let i = 0; i < 4; i++) {
      const x = 6 + i * 2.6;
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.95, 0.18, 8, 20), new THREE.MeshBasicMaterial({ color: CH_COLORS[i] }));
      ring.position.set(x, 3.4, 8.05);
      p.group.add(ring);
      p.addJack(`ch${i + 1}`, x, 3.4, 8.0, 0, 1, MAT.bright);
    }
    p.labelPlane(10, 2.5, 10, 9, 8.02);
    p._height = 12;
    p.showCounts = (cnt) => {
      const { ctx: g, canvas: c, tex } = p.display;
      g.fillStyle = '#05100e'; g.fillRect(0, 0, c.width, c.height);
      g.textBaseline = 'middle';
      g.font = `600 ${c.height * 0.13}px ui-monospace, Menlo, monospace`;
      g.fillStyle = '#94a3b8';
      g.fillText(cnt ? `${cnt.slots.toLocaleString()} bins · ${fmtSI(cnt.rate, 'Hz')}` : 'waiting for data…', 14, c.height * 0.14);
      const chs = Object.keys(cnt?.counts ?? {});
      chs.forEach((ch, i) => {
        const y = c.height * (0.36 + i * 0.2);
        g.fillStyle = CH_COLORS[Number(ch.slice(2)) - 1] ?? '#e2e8f0';
        g.font = `700 ${c.height * 0.15}px ui-monospace, Menlo, monospace`;
        g.fillText(`${ch.toUpperCase()} ${cnt.counts[ch].toLocaleString().padStart(9)}  ${fmtSI(cnt.rates[ch], 'cps')}`, 14, y);
      });
      tex.needsUpdate = true;
    };
    p.showCounts(null);
  },
};

function buildModulator(p) {
  p.basePlate(16, 6.5);
  p.box(13.4, 2.4, 4.4, MAT.brass, 0, 1.8, 0);
  p.box(0.4, 2.2, 4.2, MAT.black, -6.9, 1.8, 0);
  p.box(0.4, 2.2, 4.2, MAT.black, 6.9, 1.8, 0);
  p.addPort('p1', -7.1, 1.8, 0, -1, 0);
  p.addPort('p2', 7.1, 1.8, 0, 1, 0);
  p.addJack('rf', 4.2, 1.8, -2.2, 0, -1, MAT.gold);      // SMA drive input on the back
  p.addJack('bias', 1.2, 1.8, -2.2, 0, -1, MAT.steel);    // DC bias input next to it
  p.labelPlane(8.6, 3.4, -1.6, 3.01, 0, true);
  p._height = 4.5;
}

const SHAPE_GLYPH = {
  sine: (g, x, y, w, h) => { g.beginPath(); for (let i = 0; i <= 24; i++) { const px = x + (i / 24) * w, py = y - h * Math.sin((i / 24) * Math.PI * 2); i ? g.lineTo(px, py) : g.moveTo(px, py); } g.stroke(); },
  square: (g, x, y, w, h) => { g.beginPath(); g.moveTo(x, y + h); g.lineTo(x, y - h); g.lineTo(x + w / 2, y - h); g.lineTo(x + w / 2, y + h); g.lineTo(x + w, y + h); g.lineTo(x + w, y - h); g.stroke(); },
  triangle: (g, x, y, w, h) => { g.beginPath(); g.moveTo(x, y + h); g.lineTo(x + w / 2, y - h); g.lineTo(x + w, y + h); g.stroke(); },
  ramp: (g, x, y, w, h) => { g.beginPath(); g.moveTo(x, y + h); g.lineTo(x + w, y - h); g.lineTo(x + w, y + h); g.stroke(); },
  dc: (g, x, y, w) => { g.beginPath(); g.moveTo(x, y); g.lineTo(x + w, y); g.stroke(); },
  pattern: (g, x, y, w, h) => { g.beginPath(); const lv = [1, -1, -1, 1, -1, 1, 1, -1]; lv.forEach((v, i) => { const px = x + (i / lv.length) * w; g.lineTo(px, y - v * h); g.lineTo(px + w / lv.length, y - v * h); }); g.stroke(); },
};

const PSU_COLORS = ['#f87171', '#60a5fa'];

/** Front-panel display of a DC supply: a voltage readout per output. */
function drawSupplyDisplay(display, comp) {
  const { ctx: g, canvas: c, tex } = display;
  g.fillStyle = '#060b12'; g.fillRect(0, 0, c.width, c.height);
  g.textBaseline = 'middle';
  ['ch1', 'ch2'].forEach((ch, i) => {
    const y = c.height * (0.3 + i * 0.42), on = comp.params[`${ch}_enabled`];
    g.fillStyle = on ? PSU_COLORS[i] : '#3b4652';
    g.font = `700 ${c.height * 0.17}px ui-monospace, Menlo, monospace`;
    g.fillText(ch.toUpperCase(), 14, y);
    g.font = `700 ${c.height * 0.22}px ui-monospace, Menlo, monospace`;
    g.textAlign = 'right';
    g.fillText(on ? `${comp.params[`${ch}_voltage`].toFixed(3)} V` : 'OFF', c.width - 16, y);
    g.textAlign = 'left';
  });
  tex.needsUpdate = true;
}

/** Front-panel display of a function generator: one row per channel. */
function drawGeneratorDisplay(display, comp) {
  const { ctx: g, canvas: c, tex } = display;
  g.fillStyle = '#060b12'; g.fillRect(0, 0, c.width, c.height);
  g.textBaseline = 'middle'; g.lineCap = 'round'; g.lineJoin = 'round';
  ['ch1', 'ch2'].forEach((ch, i) => {
    const y = c.height * (0.28 + i * 0.44), on = comp.params[`${ch}_enabled`], shape = comp.params[`${ch}_shape`];
    g.strokeStyle = g.fillStyle = on ? CH_COLORS[i] : '#3b4652';
    g.font = `700 ${c.height * 0.17}px ui-monospace, Menlo, monospace`;
    g.fillText(ch.toUpperCase(), 14, y);
    g.lineWidth = 3;
    (SHAPE_GLYPH[shape] ?? SHAPE_GLYPH.dc)(g, 72, y, 46, c.height * 0.11);
    g.font = `500 ${c.height * 0.15}px ui-monospace, Menlo, monospace`;
    const text = !on ? 'output off' : shape === 'dc' ? `${comp.params[`${ch}_offset`].toFixed(2)} V` : `${fmtSI(comp.params[`${ch}_freq`], 'Hz')}  ${comp.params[`${ch}_vpp`].toFixed(2)} Vpp`;
    g.fillText(text, 134, y);
  });
  g.fillStyle = '#64748b';
  g.font = `500 ${c.height * 0.12}px ui-sans-serif, system-ui, sans-serif`;
  g.fillText(`time base: ${comp.params.trigger}`, 14, c.height * 0.9);
  tex.needsUpdate = true;
}

/** Fallback for any component type without a dedicated model: a box with its ports. */
function buildGeneric(p, comp) {
  const optical = comp.ports.filter((x) => x.kind === 'optical');
  const elec = comp.ports.filter((x) => x.kind === 'electrical');
  const left = optical.filter((x, i) => x.direction === 'in' || (x.direction === 'bidi' && i % 2 === 0));
  const right = optical.filter((x) => !left.includes(x));
  const d = Math.max(7, 3.2 * Math.max(left.length, right.length, 1) + 2);
  if (comp.category === 'instrument') {
    p.onShelf = true;
    p.footprint = { w: 30, d: 16 };
    p.box(30, 12, 16, MAT.case, 0, 6, 0);
    elec.forEach((x, i) => p.addJack(x.name, -10 + i * 3.4, 3.4, 8, 0, 1));
    p.labelPlane(14, 3.5, 4, 8, 8.02);
    p._height = 12;
    return;
  }
  p.basePlate(13, d + 1.5);
  p.box(10, 4, d, MAT.gray, 0, 2.6, 0);
  const place = (list, x, dx) => list.forEach((port, i) => p.addPort(port.name, x, 2.6, (i - (list.length - 1) / 2) * 3.2, dx, 0));
  place(left, -5.1, -1);
  place(right, 5.1, 1);
  elec.forEach((x, i) => p.addJack(x.name, -3 + i * 2.5, 2.6, d / 2, 0, 1));
  p.labelPlane(9, 3.6, 0, 4.61, 0, true);
  p._height = 5;
}

// ---------------------------------------------------------------------------------------------
// Label text per type (always from the server's parameter values)
// ---------------------------------------------------------------------------------------------
const nm = (m) => `${Math.round(m * 1e9)} nm`;
const LABELS = {
  Laser: (c) => {
    const p = c.params;
    const mode = p.mode === 'cw' ? 'CW' : `${p.mode} ${fmtSI(p.rep_rate, 'Hz')}`;
    return { lines: [`${nm(p.wavelength)} · ${p.power_dbm.toFixed(1)} dBm`, mode], accent: wavelengthColor(p.wavelength).css, warn: true };
  },
  Photodiode: (c) => ({ lines: [`${c.params.material} · ${fmtSI(c.params.bandwidth, 'Hz')}`], accent: '#34d399' }),
  SinglePhotonDetector: (c) => ({ lines: [`η ${Math.round(c.params.efficiency * 100)}% · DCR ${fmtSI(c.params.dark_count_rate, 'Hz')}`], accent: '#22d3ee' }),
  PowerMeter: () => ({ lines: [], accent: '#2dd4bf' }),
  BeamSplitter: (c) => ({ lines: [`2×2 coupler ${Math.round(c.params.ratio * 100)}:${Math.round(100 - c.params.ratio * 100)}`, 'a1 a2 ⇄ b1 b2'], accent: '#a3e635' }),
  Attenuator: (c) => ({ lines: [`${c.params.attenuation_db.toFixed(1)} dB`], accent: '#fb923c' }),
  PhaseShifter: (c) => ({ lines: [`φ = ${c.params.phase.toFixed(2)} rad`], accent: '#c084fc' }),
  Isolator: () => ({ lines: ['→'], accent: '#94a3b8' }),
  MatingSleeve: () => ({ lines: ['FC–FC'], accent: '#94a3b8' }),
  PhaseModulator: (c) => ({ lines: [`Vπ ${c.params.v_pi} V · ${driveText(c, 'rf')}`, driveText(c, 'bias')], accent: '#f472b6' }),
  IntensityModulator: (c) => ({ lines: [`Vπ ${c.params.v_pi} V · ${driveText(c, 'rf')}`, driveText(c, 'bias')], accent: '#fbbf24' }),
  OpticalSwitch: (c) => ({ lines: ['2×2 switch · a1 a2 ⇄ b1 b2', `ctrl ${driveText(c, 'ctrl').replace('ctrl ', '')}`], accent: '#38bdf8' }),
  FunctionGenerator: () => ({ lines: ['2-ch AWG'], accent: '#a78bfa' }),
  DCPowerSupply: () => ({ lines: ['2 outputs'], accent: '#f87171' }),
  FiberSpool: (c) => ({ lines: [`${(c.params.length / 1e3).toPrecision(3)} km SMF-28`, `delay ${fmtSI(c.params.length * 4.897e-9, 's')}`], accent: '#facc15' }),
  Oscilloscope: () => ({ lines: ['4 ch · photocurrent'], accent: '#facc15' }),
  PhotonCounter: () => ({ lines: ['time tagger'], accent: '#22d3ee' }),
};
const labelGeneric = (c) => ({ lines: [c.label ?? c.type], accent: '#94a3b8' });
const driveText = (c, name) => { const port = c.ports.find((p) => p.name === name); return port?.peer ? `${name} ← ${port.peer}` : `no ${name}`; };

function parseRate(s) {
  const m = /([\d.]+)\s*([kMG]?)Hz/.exec(s);
  return m ? Number(m[1]) * ({ k: 1e3, M: 1e6, G: 1e9 }[m[2]] ?? 1) : 0;
}

// ---------------------------------------------------------------------------------------------
// Coax cable for an electrical connection (detector output → instrument input)
// ---------------------------------------------------------------------------------------------
const coaxMat = new THREE.MeshStandardMaterial({ color: 0x1b1e24, roughness: 0.6, metalness: 0.1 });
const COAX_R = 0.28, COAX_Y = COAX_R + 0.04;   // centre height when lying on the table

export class Coax {
  constructor(lab, a, b) {
    this.lab = lab;
    this.a = a;          // { part, port }
    this.b = b;
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), coaxMat);
    this.mesh.castShadow = true;
    this.mesh.raycast = () => {};
    this.key = '';
    lab.scene.add(this.mesh);
  }
  update() {
    const pa = new THREE.Vector3(), da = new THREE.Vector3(), pb = new THREE.Vector3(), db = new THREE.Vector3();
    if (!this.a.part.portWorld(this.a.port, pa, da) || !this.b.part.portWorld(this.b.port, pb, db)) return;
    const key = `${pa.toArray().map((v) => v.toFixed(1))}|${pb.toArray().map((v) => v.toFixed(1))}`;
    if (key === this.key) return;
    this.key = key;
    const pts = [pa, pa.clone().addScaledVector(da, 3)];
    const hi = pa.y < 20 !== pb.y < 20;      // table ↔ shelf: route over the back edge
    if (hi) {
      const low = pa.y < pb.y ? pa : pb, high = pa.y < pb.y ? pb : pa;
      const lowDir = low === pa ? da : db;
      pts.length = 0;
      pts.push(low, low.clone().addScaledVector(lowDir, 3));
      pts.push(new THREE.Vector3(low.x, COAX_Y, low.z).addScaledVector(lowDir, 6));
      pts.push(new THREE.Vector3((low.x + high.x) / 2, COAX_Y, -TABLE.depth / 2 + 2));
      pts.push(new THREE.Vector3(high.x, high.y - 6, high.z + 10));
      pts.push(high.clone().addScaledVector(high === pa ? da : db, 4), high);
    } else {
      pts.push(pb.clone().addScaledVector(db, 3), pb);
    }
    const curve = new AboveTable(new THREE.CatmullRomCurve3(pts, false, 'centripetal'), COAX_Y);
    this.mesh.geometry.dispose();
    this.mesh.geometry = new THREE.TubeGeometry(curve, 120, COAX_R, 8, false);
  }
  dispose() { this.mesh.removeFromParent(); this.mesh.geometry.dispose(); }
}
