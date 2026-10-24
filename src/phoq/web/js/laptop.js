// Laptops on the table (one per party or lab PC), a dummy layer-3 switch, and the Ethernet cables
// between them — the classical network, drawn next to the optics.
import * as THREE from 'three';
import { TABLE } from './util.js';
import { STATUS_COLOR } from './console.js';
import { AboveTable } from './fiber.js';

const MAT = {
  body: new THREE.MeshStandardMaterial({ color: 0x2a303b, metalness: 0.7, roughness: 0.35 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x141820, metalness: 0.4, roughness: 0.6 }),
  keys: new THREE.MeshStandardMaterial({ color: 0x1b2029, metalness: 0.2, roughness: 0.8 }),
  bezel: new THREE.MeshStandardMaterial({ color: 0x0c1016, metalness: 0.3, roughness: 0.6 }),
  port: new THREE.MeshStandardMaterial({ color: 0x05070a, metalness: 0.2, roughness: 0.9 }),
  cable: new THREE.MeshStandardMaterial({ color: 0x2563eb, roughness: 0.55, metalness: 0.05 }),
};
export const LAPTOP = { w: 28, d: 19, h: 1.4, open: 100 };   // lid opened 100° from the base
export const SWITCH = { w: 22, d: 12, h: 3.2, ports: 8 };
const LANE = 23;                    // cm from the front edge: the cable lane behind the laptops
export const FRONT_STRIP = 11;      // laptops' center line, cm from the front edge

export class Laptop {
  constructor(lab, info, index) {
    this.lab = lab;
    this.name = info.name;
    this.info = info;
    this.index = index;
    const g = new THREE.Group();
    const base = new THREE.Mesh(new THREE.BoxGeometry(LAPTOP.w, LAPTOP.h, LAPTOP.d), MAT.body);
    base.position.y = LAPTOP.h / 2;
    base.castShadow = base.receiveShadow = true;
    const keys = new THREE.Mesh(new THREE.BoxGeometry(LAPTOP.w - 4, 0.3, LAPTOP.d * 0.5), MAT.keys);
    keys.position.set(0, LAPTOP.h + 0.1, 0.8);
    const pad = new THREE.Mesh(new THREE.BoxGeometry(7, 0.2, 4), MAT.dark);
    pad.position.set(0, LAPTOP.h + 0.1, 6.6);
    // Lid hinged at the back edge of the base. In the lid's own frame it extends along +y with the
    // screen facing +z (the user), so a 90° opening is no rotation and 100° leans it back by 10°.
    const lid = new THREE.Group();
    lid.position.set(0, LAPTOP.h, -LAPTOP.d / 2 + 0.5);
    lid.rotation.x = THREE.MathUtils.degToRad(90 - LAPTOP.open);
    const panel = new THREE.Mesh(new THREE.BoxGeometry(LAPTOP.w, 17, 0.8), MAT.bezel);
    panel.position.set(0, 8.5, 0);
    panel.castShadow = true;
    this.canvas = document.createElement('canvas');
    this.canvas.width = 640; this.canvas.height = 384;
    this.ctx = this.canvas.getContext('2d');
    this.tex = new THREE.CanvasTexture(this.canvas);
    this.tex.colorSpace = THREE.SRGBColorSpace;
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(LAPTOP.w - 2.4, 15.6), new THREE.MeshBasicMaterial({ map: this.tex, toneMapped: false }));
    screen.position.set(0, 8.5, 0.42);
    lid.add(panel, screen);
    // RJ45 port on the back-left of the base; the cable leaves toward the lane.
    const port = new THREE.Mesh(new THREE.BoxGeometry(1.6, 1.0, 0.6), MAT.port);
    port.position.set(-LAPTOP.w / 2 + 3, LAPTOP.h / 2, -LAPTOP.d / 2 - 0.2);
    for (const m of [base, keys, pad, panel, screen, port]) m.userData.laptop = this;
    g.add(base, keys, pad, lid, port);
    this.group = g;
    lab.scene.add(g);
    this.draw({ name: this.name, procs: [] });
  }

  /** Where the Ethernet cable plugs in (world space). */
  portWorld(out) {
    return out.set(-LAPTOP.w / 2 + 3, LAPTOP.h / 2, -LAPTOP.d / 2 - 0.5).applyMatrix4(this.group.matrixWorld);
  }

  place(x, z) {
    this.group.position.set(x, 0, z);
    this.group.updateMatrixWorld(true);
  }

  /** Redraw the screen from the drawer's laptop record { name, host, port, procs: [{name,status,lines,result}] }. */
  draw(rec) {
    const { ctx: g, canvas: c } = this;
    g.fillStyle = '#05090e'; g.fillRect(0, 0, c.width, c.height);
    g.fillStyle = '#0b1219'; g.fillRect(0, 0, c.width, 46);
    g.textBaseline = 'middle';
    g.fillStyle = '#e2e8f0';
    g.font = '700 24px ui-monospace, Menlo, monospace';
    g.fillText(rec.name, 16, 23);
    g.fillStyle = '#64748b';
    g.font = '500 17px ui-monospace, Menlo, monospace';
    const addr = rec.port ? `${rec.host ?? '127.0.0.1'}:${rec.port}` : 'lab PC';
    g.fillText(addr, 16 + g.measureText(rec.name).width * 1.45, 23);
    // one status dot per process, top right
    g.textAlign = 'right';
    let x = c.width - 16;
    for (const p of [...(rec.procs ?? [])].reverse()) {
      g.fillStyle = '#94a3b8'; g.font = '500 15px ui-monospace, Menlo, monospace';
      g.fillText(p.name, x, 23);
      x -= g.measureText(p.name).width + 14;
      g.fillStyle = STATUS_COLOR[p.status] ?? '#64748b';
      g.beginPath(); g.arc(x, 23, 5, 0, Math.PI * 2); g.fill();
      x -= 18;
    }
    g.textAlign = 'left';
    const procs = rec.procs ?? [];
    const multi = procs.length > 1;
    const tail = procs.flatMap((p) => p.lines.slice(-10).map((l) => (multi ? `${p.name}› ` : '') + l)).slice(-11);
    g.font = '500 17px ui-monospace, Menlo, monospace';
    g.fillStyle = '#9fe3c3';
    tail.forEach((line, i) => g.fillText(line.length > 66 ? line.slice(0, 65) + '…' : line, 14, 70 + i * 24));
    const entries = procs.flatMap((p) => Object.entries(p.result ?? {})).slice(0, 3);
    if (entries.length) {
      g.fillStyle = '#0b1219'; g.fillRect(0, c.height - 34, c.width, 34);
      g.fillStyle = '#facc15';
      g.font = '600 16px ui-monospace, Menlo, monospace';
      g.fillText(entries.map(([k, v]) => `${k}: ${typeof v === 'number' ? Number(v.toPrecision(4)) : v}`).join('   '), 14, c.height - 17);
    }
    this.tex.needsUpdate = true;
  }

  dispose() { this.group.removeFromParent(); this.tex.dispose(); }
}

/** Dummy layer-3 switch: ports with link LEDs that blink when a laptop talks. */
export class Switch {
  constructor(lab) {
    this.lab = lab;
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(SWITCH.w, SWITCH.h, SWITCH.d), MAT.body);
    body.position.y = SWITCH.h / 2;
    body.castShadow = body.receiveShadow = true;
    g.add(body);
    this.leds = [];
    for (let i = 0; i < SWITCH.ports; i++) {
      const x = -SWITCH.w / 2 + 2.2 + i * 2.5;
      const port = new THREE.Mesh(new THREE.BoxGeometry(1.6, 1.1, 0.4), MAT.port);
      port.position.set(x, 1.1, -SWITCH.d / 2 - 0.1);          // ports on the back face, toward the lane
      const led = new THREE.Mesh(new THREE.BoxGeometry(0.6, 0.3, 0.2), new THREE.MeshStandardMaterial({ color: 0x0a2016, emissive: 0x34d399, emissiveIntensity: 0 }));
      led.position.set(x, 2.2, -SWITCH.d / 2 - 0.1);
      g.add(port, led);
      this.leds.push({ mesh: led, until: 0 });
    }
    const label = new THREE.Mesh(new THREE.PlaneGeometry(10, 1.4), new THREE.MeshBasicMaterial({ map: switchLabel(), transparent: true }));
    label.position.set(-4, SWITCH.h + 0.01, 1);
    label.rotation.x = -Math.PI / 2;
    g.add(label);
    g.traverse((o) => { o.userData.netSwitch = this; });
    this.group = g;
    lab.scene.add(g);
  }

  place(x, z) { this.group.position.set(x, 0, z); this.group.updateMatrixWorld(true); }

  portWorld(i, out) {
    const x = -SWITCH.w / 2 + 2.2 + (i % SWITCH.ports) * 2.5;
    return out.set(x, 1.1, -SWITCH.d / 2 - 0.4).applyMatrix4(this.group.matrixWorld);
  }

  blink(i, now = performance.now()) {
    const led = this.leds[i % SWITCH.ports];
    if (led) led.until = now + 350;
  }

  update(now = performance.now()) {
    for (const led of this.leds) {
      const on = now < led.until;
      led.mesh.material.emissiveIntensity = on ? 2.5 : (led.linked ? 0.35 : 0);
    }
  }

  setLinked(i, linked) { if (this.leds[i]) this.leds[i].linked = linked; }

  dispose() { this.group.removeFromParent(); }
}

function switchLabel() {
  const c = document.createElement('canvas');
  c.width = 400; c.height = 56;
  const g = c.getContext('2d');
  g.fillStyle = '#e2e8f0';
  g.font = '700 30px ui-monospace, Menlo, monospace';
  g.textBaseline = 'middle';
  g.fillText('L3 SWITCH · classical', 6, 28);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** Blue Ethernet cable from a laptop's port along the lane to a switch port. */
export class EthernetCable {
  constructor(lab, laptop, sw, portIndex) {
    this.lab = lab;
    this.laptop = laptop;
    this.sw = sw;
    this.port = portIndex;
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), MAT.cable);
    this.mesh.castShadow = true;
    this.mesh.raycast = () => {};
    this.key = '';
    lab.scene.add(this.mesh);
  }

  update() {
    const a = this.laptop.portWorld(new THREE.Vector3());
    const b = this.sw.portWorld(this.port, new THREE.Vector3());
    const key = `${a.toArray().map((v) => v.toFixed(1))}|${b.toArray().map((v) => v.toFixed(1))}|${TABLE.depth}`;
    if (key === this.key) return;
    this.key = key;
    const y = 0.26, lane = TABLE.depth / 2 - LANE;
    const pts = [a, new THREE.Vector3(a.x, a.y, a.z - 2), new THREE.Vector3(a.x, y, lane), new THREE.Vector3(b.x, y, lane), new THREE.Vector3(b.x, b.y, b.z - 2), b];
    const curve = new AboveTable(new THREE.CatmullRomCurve3(pts, false, 'centripetal', 0.8), y);
    this.mesh.geometry.dispose();
    this.mesh.geometry = new THREE.TubeGeometry(curve, 120, 0.2, 8, false);
  }

  dispose() { this.mesh.removeFromParent(); this.mesh.geometry.dispose(); }
}
