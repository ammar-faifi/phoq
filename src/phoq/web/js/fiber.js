// Visual for a Python `Fiber` patch cord: two draggable FC/PC connectors (ports a and b) and a
// cable that drapes onto the table. Light flow and open-end glow come from the server report.
import * as THREE from 'three';
import { TABLE, clamp, wavelengthColor } from './util.js';
import { getGlowTexture, makeFlowTexture } from './textures.js';

const Z = new THREE.Vector3(0, 0, 1);
const UP = new THREE.Vector3(0, 1, 0);
export const CONN_LEN = 4.0;          // ferrule face → end of strain-relief boot
export const CONN_SEAT = 0.8;         // how far the coupling nut slides over the receptacle
export const REST_Y = 0.6;            // connector axis height when lying on the table (nut radius 0.56)
const CABLE_R = 0.16;
const CABLE_Y = CABLE_R + 0.04;       // cable centre line when lying on the breadboard

/**
 * A curve that never dips below `floor`. Catmull-Rom splines overshoot where a cable drops
 * steeply from a connector onto the table; clamping the height keeps it lying on the surface.
 */
export class AboveTable extends THREE.Curve {
  constructor(base, floor) {
    super();
    this.base = base;
    this.floor = floor;
  }
  getPoint(t, out = new THREE.Vector3()) {
    this.base.getPoint(t, out);
    if (out.y < this.floor) out.y = this.floor;
    return out;
  }
}

const onTable = (p, margin = 2) => Math.abs(p.x) <= TABLE.width / 2 - margin && Math.abs(p.z) <= TABLE.depth / 2 - margin;
/** Pull a point that lies on the table surface back inside the table outline. */
const keepOn = (p, margin = 1.5) => {
  p.x = clamp(p.x, -TABLE.width / 2 + margin, TABLE.width / 2 - margin);
  p.z = clamp(p.z, -TABLE.depth / 2 + margin, TABLE.depth / 2 - margin);
  return p;
};

const connGeo = {
  nut: new THREE.CylinderGeometry(0.56, 0.56, 1.0, 18),
  body: new THREE.CylinderGeometry(0.42, 0.42, 1.25, 16),
  key: new THREE.BoxGeometry(0.22, 0.25, 0.7),
  boot: new THREE.CylinderGeometry(0.2, 0.36, 1.75, 14),
  hit: new THREE.CylinderGeometry(1.1, 1.1, CONN_LEN + 0.6, 10),
};
const connMat = {
  nut: new THREE.MeshStandardMaterial({ color: 0xc7ced9, metalness: 1, roughness: 0.25 }),
  body: new THREE.MeshStandardMaterial({ color: 0x2563eb, metalness: 0.1, roughness: 0.4 }),
  boot: new THREE.MeshStandardMaterial({ color: 0xe5be1c, metalness: 0, roughness: 0.55 }),
  hit: new THREE.MeshBasicMaterial({ visible: false }),
};

export class FiberEnd {
  constructor(fiber, index) {
    this.fiber = fiber;
    this.index = index;
    this.portName = index ? 'b' : 'a';     // the Python port of the cord
    this.attached = null;                  // { part, port } when plugged in
    this.emit = 0;
    this.emitLambda = 1550e-9;
    this.dir = new THREE.Vector3(1, 0, 0);

    const g = new THREE.Group();
    const along = (mesh, z) => { mesh.rotation.x = Math.PI / 2; mesh.position.z = z; mesh.castShadow = true; g.add(mesh); return mesh; };
    along(new THREE.Mesh(connGeo.nut, connMat.nut), 0.5);
    along(new THREE.Mesh(connGeo.body, connMat.body), 1.6);
    const key = new THREE.Mesh(connGeo.key, connMat.body);
    key.position.set(0, 0.48, 1.4);
    g.add(key);
    along(new THREE.Mesh(connGeo.boot, connMat.boot), 3.1);
    along(new THREE.Mesh(connGeo.hit, connMat.hit), CONN_LEN / 2 - 0.2);
    g.traverse((o) => { o.userData.fiberEnd = this; });
    this.group = g;

    this.glow = new THREE.Sprite(new THREE.SpriteMaterial({
      map: getGlowTexture(), color: 0xff3fb4, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, toneMapped: false,
    }));
    this.glow.visible = false;
    this.glow.raycast = () => {};
    g.add(this.glow);
  }

  get other() { return this.fiber.ends[1 - this.index]; }
  get path() { return `${this.fiber.name}.${this.portName}`; }
  get position() { return this.group.position; }

  orient(dir) {
    this.dir.copy(dir).normalize();
    this.group.quaternion.setFromUnitVectors(Z, this.dir);
  }
  faceAwayFrom(target) {
    const d = new THREE.Vector3().subVectors(target, this.position).setY(0);
    if (d.lengthSq() < 1e-6) d.set(1, 0, 0);
    this.orient(d.normalize());
  }
  /** Ferrule seated in a component's receptacle. */
  seat(part, portName) {
    const pos = new THREE.Vector3(), dir = new THREE.Vector3();
    if (!part.portWorld(portName, pos, dir)) return;
    this.group.position.copy(pos).addScaledVector(dir, -CONN_SEAT);
    this.orient(dir);
  }
  cableStart(out) { return out.copy(this.dir).multiplyScalar(CONN_LEN).add(this.position); }
}

export class FiberVisual {
  constructor(lab, comp) {
    this.lab = lab;
    this.name = comp.name;
    this.type = 'Fiber';
    this.length = comp.params.length;
    this.ends = [new FiberEnd(this, 0), new FiberEnd(this, 1)];
    this.flowTex = makeFlowTexture();
    this.material = new THREE.MeshStandardMaterial({
      color: 0xe8c21a, roughness: 0.5, metalness: 0.0,
      emissive: 0x000000, emissiveMap: this.flowTex, emissiveIntensity: 1,
    });
    this.mesh = new THREE.Mesh(new THREE.BufferGeometry(), this.material);
    this.mesh.castShadow = true;
    this.mesh.userData.fiber = this;
    this.bow = Math.random() < 0.5 ? 1 : -1;
    this.flow = { fwd: 0, bwd: 0, wavelength: 1550e-9 };
    this.key = '';
    lab.scene.add(this.mesh, this.ends[0].group, this.ends[1].group);
    this.ends.forEach((e, i) => this.rest(e, -10 + i * 20, TABLE.depth / 2 - 9));
  }

  get group() { return this.mesh; }
  get reach() { return Math.min(this.length * 100, 400) - 2 * CONN_LEN - 6; }

  refresh(comp) { this.length = comp.params.length; this.key = ''; }

  rest(end, x, z, faceTarget) {
    end.group.position.set(x, REST_Y, z);
    end.faceAwayFrom(faceTarget ?? end.other.position);
  }

  /** Keep a free end within reach of the other one (dragging pulls the slack along). */
  pull(moved) {
    const other = moved.other;
    if (other.attached) return;
    const d = new THREE.Vector3().subVectors(other.position, moved.position).setY(0);
    if (d.length() <= this.reach) return;
    d.setLength(this.reach);
    other.group.position.set(moved.position.x + d.x, REST_Y, moved.position.z + d.z);
    other.faceAwayFrom(moved.position);
  }

  rebuild() {
    const [A, B] = this.ends;
    const pA = A.cableStart(new THREE.Vector3()), pB = B.cableStart(new THREE.Vector3());
    const hA = A.dir.clone().setY(0).normalize(), hB = B.dir.clone().setY(0).normalize();
    const lead = 2.6;
    const a1 = pA.clone().addScaledVector(A.dir, lead);
    const b1 = pB.clone().addScaledVector(B.dir, lead);
    const drop = (p, h) => { const q = p.clone().addScaledVector(h, Math.max(2, p.y * 1.3)); q.y = CABLE_Y; return keepOn(q); };
    const a2 = drop(a1, hA), b2 = drop(b1, hB);

    const chord = new THREE.Vector3().subVectors(b2, a2).setY(0);
    const c = chord.length();
    const L = Math.max(Math.min(this.length * 100, 400) - 2 * (CONN_LEN + lead + 3), 10);
    let h = Math.sqrt(Math.max(0, (3 * c * (L - c)) / 8));
    if (c < L * 0.25) h = Math.max(h, Math.min(L * 0.18, 14));
    h = Math.min(h, 22);
    // Slack drapes toward the front of the table so cords don't tangle, but never past an edge:
    // take the other side if it has more room, and shrink the loop if neither side fits.
    const perp = c > 1e-3 ? new THREE.Vector3().crossVectors(chord, UP).normalize() : new THREE.Vector3(0, 0, 1);
    if (Math.abs(perp.z) > 0.2 ? perp.z < 0 : perp.x * this.bow < 0) perp.negate();
    const mid = a2.clone().lerp(b2, 0.5);
    const room = (dir) => { let r = h; while (r > 0 && !onTable(mid.clone().addScaledVector(dir, r))) r -= 1; return Math.max(0, r); };
    let bow = room(perp);
    if (bow < h) {
      const alt = room(perp.clone().negate());
      if (alt > bow) { perp.negate(); bow = alt; }
    }
    perp.multiplyScalar(bow);

    const pts = [pA, a1, a2];
    for (const t of [0.2, 0.4, 0.6, 0.8]) {
      const p = a2.clone().lerp(b2, t).addScaledVector(perp, 4 * t * (1 - t));
      p.y = CABLE_Y;
      pts.push(keepOn(p));
    }
    pts.push(b2, b1, pB);
    const curve = new AboveTable(new THREE.CatmullRomCurve3(pts, false, 'centripetal'), CABLE_Y);
    const arc = curve.getLength();
    this.mesh.geometry.dispose();
    this.mesh.geometry = new THREE.TubeGeometry(curve, Math.min(260, Math.max(80, Math.round(arc * 1.2))), CABLE_R, 8, false);
    this.flowTex.repeat.set(Math.max(1, arc / 7), 1);
  }

  update(dt, showFlow) {
    for (const e of this.ends) if (e.attached) e.seat(e.attached.part, e.attached.port);
    const key = this.ends.map((e) => `${e.position.x.toFixed(2)},${e.position.y.toFixed(2)},${e.position.z.toFixed(2)},${e.dir.x.toFixed(3)},${e.dir.z.toFixed(3)}`).join('|') + this.length;
    if (key !== this.key) { this.key = key; this.rebuild(); }

    const P = this.flow.fwd + this.flow.bwd;
    if (showFlow && P > 1e-12) {
      const level = Math.min(1, Math.max(0.15, (Math.log10(P) + 10) / 8));   // 100 pW → dim, 10 mW → full
      this.material.color.setHex(0x6e5c10);      // darker jacket so the light colour reads
      this.material.emissive.setHex(wavelengthColor(this.flow.wavelength).hex);
      this.material.emissiveIntensity = 0.25 + 0.9 * level;
      this.flowTex.offset.x -= (this.flow.fwd >= this.flow.bwd ? 1 : -1) * dt * 1.6;
    } else {
      this.material.color.setHex(0xe8c21a);
      this.material.emissive.setHex(0x000000);
    }
    for (const e of this.ends) {
      const lit = !e.attached && e.emit > 1e-12;
      e.glow.visible = lit;
      if (lit) {
        const s = 2.2 + 2.5 * Math.min(1, Math.max(0, (Math.log10(e.emit) + 8) / 6));
        e.glow.scale.set(s, s, 1);
        e.glow.position.set(0, 0, -0.25);
        e.glow.material.color.setHex(wavelengthColor(e.emitLambda).hex);
      }
    }
  }

  dispose() {
    this.mesh.removeFromParent();
    this.mesh.geometry.dispose();
    this.material.dispose();
    this.flowTex.dispose();
    for (const e of this.ends) { e.group.removeFromParent(); e.glow.material.dispose(); }
  }
}
