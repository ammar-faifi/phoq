// Automatic placement. Python owns the experiment; the layout is purely a visual choice made here:
// parts flow left → right along the light path (BFS layers from the lasers), each column is
// aligned with its upstream neighbours, instruments sit on the shelf and loose cords in front.
import * as THREE from 'three';
import { TABLE, SHELF, TABLE_PRESETS, clamp } from './util.js';

const GAP_X = 6, GAP_Z = 8;
let FRONT_RESERVE = 8;
/** Keep this many cm free along the front edge (laptops and their cables live there). */
export function reserveFront(cm) { FRONT_RESERVE = Math.max(8, cm); }
// Table-relative strips: loose cords lie along the front edge, unconnected parts along the back.
const frontZ = () => TABLE.depth / 2 - FRONT_RESERVE;
const backZ = () => -TABLE.depth / 2 + 11;
const roomier = () => Math.max(1, TABLE.width / 150);   // spread parts out on bigger tables

const split = (path) => { const i = path.lastIndexOf('.'); return [path.slice(0, i), path.slice(i + 1)]; };

/** Optical neighbours of every table part, following chains of patch cords. */
function opticalGraph(lab) {
  const comps = new Map(lab.state.components.map((c) => [c.name, c]));
  const peer = new Map();
  for (const [a, b] of lab.state.connections) { peer.set(a, b); peer.set(b, a); }
  const adj = new Map();
  for (const part of lab.parts.values()) if (!part.onShelf) adj.set(part.name, new Set());
  for (const name of adj.keys()) {
    for (const port of comps.get(name)?.ports ?? []) {
      if (port.kind !== 'optical' || !port.peer) continue;
      let cur = port.peer;
      const seen = new Set();
      while (cur) {
        const [cn, pn] = split(cur);
        if (comps.get(cn)?.category !== 'cord') {
          if (adj.has(cn) && cn !== name) { adj.get(name).add(cn); adj.get(cn).add(name); }
          break;
        }
        if (seen.has(cn)) break;
        seen.add(cn);
        cur = peer.get(`${cn}.${pn === 'a' ? 'b' : 'a'}`);
      }
    }
  }
  return adj;
}

/** Columns of table parts along the light path (BFS layers from the lasers). */
function columnsOf(lab) {
  const adj = opticalGraph(lab);
  const parts = [...lab.parts.values()].filter((p) => !p.onShelf);
  const layer = new Map();
  const bfs = (start) => {
    const q = [...start];
    while (q.length) {
      const n = q.shift();
      for (const m of adj.get(n) ?? []) if (!layer.has(m)) { layer.set(m, layer.get(n) + 1); q.push(m); }
    }
  };
  const sources = parts.filter((p) => p.category === 'source').map((p) => p.name);
  sources.forEach((n) => layer.set(n, 0));
  bfs(sources);
  for (const p of parts) if (!layer.has(p.name) && adj.get(p.name).size) { layer.set(p.name, 0); bfs([p.name]); }

  const cols = [];
  for (const p of parts) if (layer.has(p.name)) (cols[layer.get(p.name)] ??= []).push(p);
  return { adj, parts, layer, columns: cols.filter(Boolean) };
}

/** Smallest preset table on which the auto layout fits with comfortable spacing. */
export function suggestTableSize(lab) {
  const { columns, parts, layer } = columnsOf(lab);
  const sumW = columns.reduce((s, c) => s + Math.max(...c.map((p) => p.footprint.w)), 0);
  const needW = Math.max(sumW + GAP_X * Math.max(0, columns.length - 1) + 16,
    parts.filter((p) => !layer.has(p.name)).reduce((s, p) => s + p.footprint.w + 4, 8));
  const needD = Math.max(0, ...columns.map((c) => c.reduce((s, p) => s + p.footprint.d + GAP_Z, 0))) + 26 + FRONT_RESERVE;
  return TABLE_PRESETS.find((t) => t.w >= needW && t.d >= needD) ?? TABLE_PRESETS[TABLE_PRESETS.length - 1];
}

export function autoLayout(lab) {
  const { adj, parts, layer, columns } = columnsOf(lab);
  for (const p of parts) p.rot = 0;

  // x: columns side by side, compressed to fit the table
  const widths = columns.map((c) => Math.max(...c.map((p) => p.footprint.w)));
  const sumW = widths.reduce((a, b) => a + b, 0);
  const gap = columns.length > 1 ? clamp((TABLE.width - 8 - sumW) / (columns.length - 1), 1, GAP_X * 3 * roomier()) : 0;
  let x = -(sumW + gap * (columns.length - 1)) / 2;
  const z = new Map();
  columns.forEach((col, i) => {
    const cx = x + widths[i] / 2;
    x += widths[i] + gap;
    // z: aim for the mean z of already-placed neighbours, then resolve overlaps
    const items = col.map((p) => {
      const placed = [...adj.get(p.name)].filter((n) => z.has(n)).map((n) => z.get(n));
      return { p, want: placed.length ? placed.reduce((a, b) => a + b, 0) / placed.length : -TABLE.depth / 15, d: p.footprint.d };
    }).sort((a, b) => a.want - b.want || a.p.name.localeCompare(b.p.name));
    let prev = null;
    for (const it of items) {
      it.z = prev ? Math.max(it.want, prev.z + prev.d / 2 + it.d / 2 + GAP_Z * Math.max(1, TABLE.depth / 90)) : it.want;
      prev = it;
    }
    const shift = items.reduce((s, it) => s + (it.want - it.z), 0) / items.length;
    for (const it of items) {
      it.z = clamp(it.z + shift, -TABLE.depth / 2 + it.d / 2 + 1, frontZ() - 6 - it.d / 2);
      z.set(it.p.name, it.z);
      it.p.setPose(cx, it.z, 0);
    }
  });

  // Parts with no optical connection: a row along the back edge
  let bx = -TABLE.width / 2 + 4;
  for (const p of parts.filter((q) => !layer.has(q.name))) {
    p.setPose(bx + p.footprint.w / 2, backZ() + p.footprint.d / 2 - 4, 0);
    bx += p.footprint.w + 4;
  }
  layoutShelf(lab);
  restFibers(lab, true);
}

export function layoutShelf(lab) {
  let x = SHELF.xMax - 2;
  for (const p of [...lab.parts.values()].filter((q) => q.onShelf)) {
    p.setShelf(x - p.footprint.w / 2);
    x -= p.footprint.w + 4;
  }
}

/** Put unplugged connectors somewhere sensible (all of them, or only ones never placed). */
export function restFibers(lab, all = false) {
  let fx = -TABLE.width / 2 + 12;
  for (const f of lab.fibers.values()) {
    for (const e of f.ends) {
      if (e.attached || (!all && e.placed)) continue;
      const other = e.other;
      if (other.attached) {
        const pos = new THREE.Vector3(), dir = new THREE.Vector3();
        other.attached.part.portWorld(other.attached.port, pos, dir);
        const target = pos.clone().addScaledVector(dir, 14);
        f.rest(e, clamp(target.x, -TABLE.width / 2 + 3, TABLE.width / 2 - 3), clamp(target.z, -TABLE.depth / 2 + 2, TABLE.depth / 2 - 2), pos);
      } else if (!other.placed || all) {
        const fz = frontZ();
        f.rest(e, fx, fz, new THREE.Vector3(fx + 30, 0, fz + 2));
        f.rest(other, fx + 24, fz + 3, e.position);
        other.placed = true;
        fx += 34;
      } else {
        f.rest(e, other.position.x + 20, other.position.z, other.position);
      }
      e.placed = true;
    }
  }
}

/** Find a free table spot for a newly added part, near `at` or near its optical neighbours. */
export function placeNew(lab, part, at = null) {
  if (part.onShelf) { layoutShelf(lab); return; }
  const adj = opticalGraph(lab);
  const near = [...(adj.get(part.name) ?? [])].map((n) => lab.parts.get(n)).filter((p) => p && p.placed);
  let start = at ?? (near.length
    ? { x: near.reduce((s, p) => s + p.group.position.x, 0) / near.length + 20, z: near.reduce((s, p) => s + p.group.position.z, 0) / near.length }
    : { x: 0, z: 12 });
  const [hw, hd] = part.halfExtents();
  const free = (x, z) => {
    if (Math.abs(x) + hw > TABLE.width / 2 || Math.abs(z) + hd > TABLE.depth / 2) return false;
    for (const q of lab.parts.values()) {
      if (q === part || q.onShelf || !q.placed) continue;
      const [qw, qd] = q.halfExtents();
      const p = q.group.position;
      if (Math.abs(p.x - x) < qw + hw + 1.5 && Math.abs(p.z - z) < qd + hd + 1.5) return false;
    }
    return true;
  };
  for (let r = 0; r <= 90; r += 2.5) {
    const n = Math.max(1, Math.round(r * 1.2));
    for (let k = 0; k < n; k++) {
      const a = (k / n) * Math.PI * 2;
      const x = start.x + r * Math.cos(a), z = start.z + r * Math.sin(a);
      if (free(x, z)) { part.setPose(x, z); return; }
    }
  }
  part.setPose(start.x, start.z);
}
