// Pointer & keyboard handling. Moving parts is a local layout choice; plugging/unplugging fiber
// connectors, deleting parts and toggling lasers are sent to the Python server.
import * as THREE from 'three';
import { TABLE, clamp, isAngled } from './util.js';
import { REST_Y } from './fiber.js';

const SNAP_DIST = 5;      // cm: connector-to-port capture radius
const LIFT = 3;           // height of a connector held "in hand"
const CLICK_PX = 4;

export class Interaction {
  constructor(lab) {
    this.lab = lab;
    this.dom = lab.renderer.domElement;
    this.host = lab.viewport;
    this.ray = new THREE.Raycaster();
    this.ndc = new THREE.Vector2();
    this.plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    this.drag = null;
    this.down = null;
    this.hoverEnd = null;
    this.tip = new THREE.Vector3();

    // Capture phase on the parent so we decide before OrbitControls whether a drag is ours.
    this.host.addEventListener('pointerdown', (e) => this.onDown(e), { capture: true });
    window.addEventListener('pointermove', (e) => this.onMove(e));
    window.addEventListener('pointerup', (e) => this.onUp(e));
    window.addEventListener('pointercancel', (e) => this.onUp(e));
    window.addEventListener('keydown', (e) => this.onKey(e));

    this.host.addEventListener('dragover', (e) => {
      if (!e.dataTransfer.types.includes('application/x-lab-item')) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = 'copy';
      this.host.classList.add('drop-target');
    });
    this.host.addEventListener('dragleave', (e) => {
      if (!e.relatedTarget || !this.host.contains(e.relatedTarget)) this.host.classList.remove('drop-target');
    });
    this.host.addEventListener('drop', (e) => {
      this.host.classList.remove('drop-target');
      const type = e.dataTransfer.getData('application/x-lab-item');
      if (!type) return;
      e.preventDefault();
      const p = this.planePoint(e, 0);
      const onTable = p && Math.abs(p.x) < TABLE.width / 2 && Math.abs(p.z) < TABLE.depth / 2;
      this.lab.add(type, onTable ? { x: p.x, z: p.z } : null);
    });
  }

  setRay(e) {
    const r = this.dom.getBoundingClientRect();
    this.ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.ndc, this.lab.camera);
  }

  planePoint(e, y) {
    this.setRay(e);
    this.plane.constant = -y;
    return this.ray.ray.intersectPlane(this.plane, new THREE.Vector3());
  }

  pick(e) {
    this.setRay(e);
    const lab = this.lab;
    const objs = [];
    for (const f of lab.fibers.values()) objs.push(f.ends[0].group, f.ends[1].group, f.mesh);
    for (const p of lab.parts.values()) objs.push(p.group);
    for (const l of lab.laptops.values()) objs.push(l.group);
    if (lab.netSwitch) objs.push(lab.netSwitch.group);
    for (const h of this.ray.intersectObjects(objs, true)) {
      const u = h.object.userData;
      if (u.laptop) return { kind: 'laptop', laptop: u.laptop };
      if (u.netSwitch) return { kind: 'switch' };
      if (u.fiberEnd) return { kind: 'end', end: u.fiberEnd };
      if (u.part) return { kind: 'part', part: u.part };
      if (u.fiber) return { kind: 'fiber', fiber: u.fiber };
    }
    return null;
  }

  onDown(e) {
    if (e.target !== this.dom || e.button !== 0) return;
    const hit = this.pick(e);
    this.down = { x: e.clientX, y: e.clientY, hit };
    if (!hit) return;                                   // let OrbitControls rotate the view
    this.lab.controls.enabled = false;
    if (hit.kind === 'laptop' || hit.kind === 'switch') { this.drag = { kind: 'none', pending: true }; return; }
    if (hit.kind === 'end') {
      this.lab.select(hit.end.fiber.name);
      this.drag = { kind: 'end', end: hit.end, snap: null, pending: true };
    } else if (hit.kind === 'part') {
      this.lab.select(hit.part.name);
      if (hit.part.onShelf || hit.part.mount) { this.drag = { kind: 'none', pending: true }; return; }
      const p = this.planePoint(e, 0) ?? hit.part.group.position.clone();
      this.drag = { kind: 'part', part: hit.part, offset: hit.part.group.position.clone().sub(p), pending: true };
    } else {
      this.lab.select(hit.fiber.name);
      this.drag = { kind: 'none', pending: true };
    }
  }

  onMove(e) {
    const d = this.drag;
    if (!d) {
      if (e.buttons === 0 && e.target === this.dom) this.updateHover(e);
      return;
    }
    if (d.pending) {
      if (Math.hypot(e.clientX - this.down.x, e.clientY - this.down.y) < CLICK_PX) return;
      d.pending = false;
      if (d.kind === 'end') {
        this.lab.dragEnd = d.end;
        if (d.end.attached) this.lab.unplugEnd(d.end);
        this.showPorts(true, d.end);
      }
    }
    if (d.kind !== 'none') this.dom.style.cursor = 'grabbing';
    if (d.kind === 'part') {
      const p = this.planePoint(e, 0);
      if (p) d.part.setPose(p.x + d.offset.x, p.z + d.offset.z);
    } else if (d.kind === 'end') {
      const p = this.planePoint(e, LIFT);
      if (p) this.moveEnd(d, p);
    }
  }

  moveEnd(d, p) {
    const end = d.end;
    const snap = this.nearestPort(p);
    if (d.snap && (!snap || d.snap.ring !== snap.ring)) this.ringState(d.snap, 'free');
    d.snap = snap;
    if (snap) {
      end.seat(snap.part, snap.port);
      this.ringState(snap, 'snap');
      end.fiber.pull(end);
      return;
    }
    const other = end.other;
    if (other.attached) {             // the far end is plugged in: the cord can't stretch further
      const v = new THREE.Vector3().subVectors(p, other.position).setY(0);
      if (v.length() > end.fiber.reach) { v.setLength(end.fiber.reach); p.set(other.position.x + v.x, LIFT, other.position.z + v.z); }
    }
    p.x = clamp(p.x, -TABLE.width / 2 - 4, TABLE.width / 2 + 4);
    p.z = clamp(p.z, -TABLE.depth / 2 - 4, TABLE.depth / 2 + 4);
    end.group.position.set(p.x, LIFT, p.z);
    end.faceAwayFrom(other.position);
    end.fiber.pull(end);
  }

  nearestPort(p) {
    let best = null, bestD = SNAP_DIST;
    for (const part of this.lab.parts.values()) {
      for (const [name, port] of part.ports) {
        if (port.kind !== 'optical' || !this.lab.portFree(part.name, name)) continue;
        part.portWorld(name, this.tip);
        const dist = Math.hypot(this.tip.x - p.x, this.tip.z - p.z);
        if (dist < bestD) { bestD = dist; best = { part, port: name, ring: port.ring }; }
      }
    }
    return best;
  }

  ringState(target, state) {
    const r = target.ring;
    const bad = this.mismatch(target);   // amber: an APC end on a flat port, or the reverse
    r.visible = state !== 'hidden';
    r.material.color.setHex(state === 'snap' ? (bad ? 0xf59e0b : 0x34d399) : (bad ? 0xfbbf24 : 0x22d3ee));
    r.material.opacity = state === 'snap' ? 1 : 0.55;
    r.userData.snap = state === 'snap';
  }

  /** Would the carried connector mate an angled face with a flat one at this port? */
  mismatch(target) {
    const port = target.part ? target.part.ports.get(target.port) : target;
    return !!(this.carry && port?.connector && isAngled(this.carry) !== isAngled(port.connector));
  }

  showPorts(on, end = null) {
    this.carry = on ? end?.connector ?? null : null;
    for (const part of this.lab.parts.values()) {
      for (const [name, port] of part.ports) {
        if (port.kind !== 'optical') continue;
        this.ringState(port, on && this.lab.portFree(part.name, name) ? 'free' : 'hidden');
      }
    }
  }

  onUp(e) {
    const d = this.drag, down = this.down;
    this.drag = null;
    this.down = null;
    if (d) {
      this.lab.controls.enabled = true;
      this.dom.style.cursor = '';
      if (d.kind === 'end' && !d.pending) {
        this.showPorts(false);
        this.lab.dragEnd = null;
        if (d.snap) this.lab.plugEnd(d.end, d.snap.part, d.snap.port);
        else { d.end.group.position.y = REST_Y; d.end.placed = true; d.end.fiber.pull(d.end); }
      }
      if (d.kind === 'part' && !d.pending) this.lab.rememberLayout();
      if (d.pending && down?.hit?.part?.type === 'Oscilloscope') this.lab.ui.showScope(true);
      if (d.pending && down?.hit?.kind === 'laptop') this.lab.consoles.open(down.hit.laptop.name);
      if (d.pending && down?.hit?.kind === 'switch') this.lab.ui.toast('Layer-3 switch: the parties\' classical channel (TCP on localhost) — visual only');
      return;
    }
    if (down && e.target === this.dom && Math.hypot(e.clientX - down.x, e.clientY - down.y) < CLICK_PX) this.lab.select(null);
  }

  updateHover(e) {
    const hit = this.pick(e);
    const cursor = { end: 'grab', part: hit?.part?.onShelf ? 'pointer' : 'grab', fiber: 'pointer', laptop: 'pointer', switch: 'pointer' }[hit?.kind] ?? '';
    this.dom.style.cursor = cursor;
    const end = hit?.kind === 'end' ? hit.end : null;
    if (end !== this.hoverEnd) {
      this.hoverEnd?.group.scale.setScalar(1);
      end?.group.scale.setScalar(1.18);
      this.hoverEnd = end;
    }
  }

  /** Pulse the free-port rings while a connector is being carried. */
  update(t) {
    if (this.drag?.kind !== 'end' || this.drag.pending) return;
    const s = 1 + 0.18 * Math.sin(t * 7);
    for (const part of this.lab.parts.values()) for (const port of part.ports.values()) {
      if (port.ring?.visible) port.ring.scale.setScalar(port.ring.userData.snap ? 1.25 : s);
    }
  }

  onKey(e) {
    if (e.target.closest?.('input, select, textarea') || e.metaKey || e.ctrlKey) return;
    const lab = this.lab, name = lab.selected;
    const comp = lab.state?.components.find((c) => c.name === name);
    switch (e.key) {
      case 'Delete':
      case 'Backspace':
        if (name) { lab.remove(name); e.preventDefault(); }
        break;
      case 'r':
      case 'R':
        lab.parts.get(name)?.rotate();
        lab.rememberLayout();
        break;
      case ' ':
        if (comp?.type === 'Laser') { lab.setParams(name, { enabled: !comp.params.enabled }); e.preventDefault(); }
        break;
      case 'Escape':
        lab.select(null);
        lab.ui.toggleHelp(false);
        break;
      case 'h':
      case 'H':
      case '?':
        lab.ui.toggleHelp();
        break;
    }
  }
}
