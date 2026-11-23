// Automatic placement. Python owns the experiment; the layout is purely a visual choice made here:
// parts flow left → right along the light path (BFS layers from the lasers), each column is
// aligned with its upstream neighbors, instruments sit on the shelf and loose cords in front.
// A lab running parties gets a table per party: each station's parts go on its own table.
import * as THREE from 'three';
import { TABLE, TABLES, TABLE_PRESETS, clamp, tableAt, tableNamed, tableSpan, shelfSpan } from './util.js';

const GAP_X = 6, GAP_Z = 8;
const FRONT_RESERVE = 8;
// Table-relative strips: loose cords lie along the front edge, unconnected parts along the back.
const frontZ = () => TABLE.depth / 2 - FRONT_RESERVE;
const backZ = () => -TABLE.depth / 2 + 11;
const roomier = () => Math.max(1, TABLE.width / 150);   // spread parts out on bigger tables

const split = (path) => { const i = path.lastIndexOf('.'); return [path.slice(0, i), path.slice(i + 1)]; };

/** The table in view: new parts and loose cords go there. */
export const tableInView = (lab) => tableAt(lab.controls.target.x);

/**
 * Neighbors of every part, following chains of patch cords: optical links between the table's
 * parts, or with `all` every link (electrical ones too) between all parts. A plug riding on a
 * port is left out.
 */
function linkGraph(lab, all = false) {
  const comps = new Map(lab.state.components.map((c) => [c.name, c]));
  const peer = new Map();
  for (const [a, b] of lab.state.connections) { peer.set(a, b); peer.set(b, a); }
  const adj = new Map();
  for (const part of lab.parts.values()) if (!part.mount && (all || !part.onShelf)) adj.set(part.name, new Set());
  for (const name of adj.keys()) {
    for (const port of comps.get(name)?.ports ?? []) {
      if (!port.peer || (port.kind !== 'optical' && !(all && port.kind === 'electrical'))) continue;
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

/** Light-path layer of every linked table part: BFS from the lasers (then from what they miss). */
function lightLayers(lab, adj) {
  const layer = new Map();
  const bfs = (start) => {
    const q = [...start];
    while (q.length) {
      const n = q.shift();
      for (const m of adj.get(n) ?? []) if (!layer.has(m)) { layer.set(m, layer.get(n) + 1); q.push(m); }
    }
  };
  const names = [...adj.keys()];
  const sources = names.filter((n) => lab.parts.get(n).category === 'source');
  sources.forEach((n) => layer.set(n, 0));
  bfs(sources);
  for (const n of names) if (!layer.has(n) && adj.get(n).size) { layer.set(n, 0); bfs([n]); }
  return layer;
}

/**
 * The party each part belongs to: the one listing it among its instruments, else the party of
 * the nearest listed part along cords and cables, the one earlier on the light path on a tie (a
 * station's fiber spool goes with the station that feeds it, not with the receiver).
 */
function ownersOf(lab, parties) {
  const owner = new Map();
  for (const l of lab.state.laptops ?? []) {
    if (parties.includes(l.name)) for (const n of l.instruments ?? []) if (lab.parts.has(n)) owner.set(n, l.name);
  }
  const adj = linkGraph(lab, true);
  const layer = lightLayers(lab, linkGraph(lab));
  const rank = (n) => [layer.get(n) ?? Infinity, parties.indexOf(owner.get(n))];
  const before = (a, b) => { const [ra, rb] = [rank(a), rank(b)]; return ra[0] - rb[0] || ra[1] - rb[1]; };
  const out = new Map(owner);
  for (const name of lab.parts.keys()) {
    if (owner.has(name)) continue;
    let ring = [name];
    const seen = new Set(ring);
    while (ring.length) {
      const next = [];
      for (const n of ring) for (const m of adj.get(n) ?? []) if (!seen.has(m)) { seen.add(m); next.push(m); }
      const listed = next.filter((m) => owner.has(m)).sort(before);
      if (listed.length) { out.set(name, owner.get(listed[0])); break; }
      ring = next;
    }
  }
  return out;
}

/**
 * The parties left to right: the order that keeps linked stations closest (summed distance, in
 * tables, over the links between them), the declared order on a tie. An eavesdropper loaded
 * with --party is declared last but belongs between the stations whose link she taps.
 */
export function partyOrder(lab, names) {
  if (names.length < 3 || names.length > 7) return names;
  const owner = ownersOf(lab, names);
  const links = [];
  for (const [n, ms] of linkGraph(lab, true)) {
    for (const m of ms) {
      const a = names.indexOf(owner.get(n)), b = names.indexOf(owner.get(m));
      if (n < m && a >= 0 && b >= 0 && a !== b) links.push([a, b]);
    }
  }
  let best = null, bestCost = Infinity;
  const perm = [];
  const visit = () => {                     // permutations in lexicographic order, declared first
    if (perm.length === names.length) {
      const pos = [];
      perm.forEach((p, k) => { pos[p] = k; });
      const cost = links.reduce((s, [a, b]) => s + Math.abs(pos[a] - pos[b]), 0);
      if (cost < bestCost) { bestCost = cost; best = [...perm]; }
      return;
    }
    for (let i = 0; i < names.length; i++) if (!perm.includes(i)) { perm.push(i); visit(); perm.pop(); }
  };
  visit();
  return best.map((i) => names[i]);
}

/** The table each part goes on (or on whose shelf): its party's; a part linked to no party's
 * equipment, and every part of a lab with one table, keeps the table it is nearest to. */
export function tablesOf(lab) {
  const out = new Map();
  const owner = TABLES.length > 1 ? ownersOf(lab, TABLES.map((t) => t.name)) : new Map();
  for (const p of lab.parts.values()) out.set(p.name, tableNamed(owner.get(p.name)) ?? tableAt(p.group.position.x));
  return out;
}

/** Table parts with their light-path layers and tables. */
function lightPath(lab) {
  const adj = linkGraph(lab);
  const parts = [...lab.parts.values()].filter((p) => !p.onShelf && !p.mount);   // a mounted plug rides on its port
  return { adj, parts, layer: lightLayers(lab, adj), tableOf: tablesOf(lab) };
}

/** One table's columns along the light path (light-path layers in order) and its unlinked parts. */
function tableColumns({ parts, layer, tableOf }, t) {
  const mine = parts.filter((p) => tableOf.get(p.name) === t);
  const cols = new Map();
  for (const p of mine) {
    if (!layer.has(p.name)) continue;
    const L = layer.get(p.name);
    if (!cols.has(L)) cols.set(L, []);
    cols.get(L).push(p);
  }
  const columns = [...cols.entries()].sort((a, b) => a[0] - b[0]).map(([L, c]) => ({ layer: L, parts: c }));
  return { columns, loose: mine.filter((p) => !layer.has(p.name)) };
}

/**
 * How a table's optical links to other tables run: `flip` when its light leaves toward tables on
 * its left (the layout is then mirrored, so the station's output faces the table it feeds), and
 * `side` −1 or +1 when all its links leave on that side (the chain then moves up to that edge,
 * keeping the patch cords across the gap short), else 0.
 */
function crossLinks({ adj, layer, tableOf }, t, columns) {
  let lean = 0, left = 0, right = 0;
  for (const { parts } of columns) {
    for (const p of parts) {
      for (const n of adj.get(p.name)) {
        const u = tableOf.get(n);
        if (u === t) continue;
        if (u.x < t.x) left++; else right++;
        if (layer.get(n) > layer.get(p.name)) lean += Math.sign(u.x - t.x);
      }
    }
  }
  return { flip: lean < 0, side: left && !right ? -1 : right && !left ? 1 : 0 };
}

/** Smallest preset table on which the auto layout fits with comfortable spacing (each table of
 * a row has the same size, that of its most crowded one). */
export function suggestTableSize(lab) {
  const path = lightPath(lab);
  let needW = 0, needD = 0;
  for (const t of TABLES) {
    const { columns, loose } = tableColumns(path, t);
    const sumW = columns.reduce((s, c) => s + Math.max(...c.parts.map((p) => p.footprint.w)), 0);
    needW = Math.max(needW, sumW + GAP_X * Math.max(0, columns.length - 1) + 16, loose.reduce((s, p) => s + p.footprint.w + 4, 8));
    needD = Math.max(needD, Math.max(0, ...columns.map((c) => c.parts.reduce((s, p) => s + p.footprint.d + GAP_Z, 0))) + 26 + FRONT_RESERVE);
  }
  return TABLE_PRESETS.find((t) => t.w >= needW && t.d >= needD) ?? TABLE_PRESETS[TABLE_PRESETS.length - 1];
}

export function autoLayout(lab) {
  const path = lightPath(lab);
  const { adj, parts } = path;
  for (const p of parts) p.rot = 0;

  // x: each table's columns side by side, compressed to fit it (mirrored when its light leaves
  // toward the left, the parts turned round so their outputs face that way)
  const columns = [];
  const loose = [];
  for (const t of TABLES) {
    const tc = tableColumns(path, t);
    loose.push([t, tc.loose]);
    const { flip, side } = crossLinks(path, t, tc.columns);
    const widths = tc.columns.map((c) => Math.max(...c.parts.map((p) => p.footprint.w)));
    const sumW = widths.reduce((a, b) => a + b, 0);
    const n = tc.columns.length;
    const gap = n > 1 ? clamp((TABLE.width - 8 - sumW) / (n - 1), 1, GAP_X * 3 * roomier()) : 0;
    const span = sumW + gap * (n - 1);
    const shift = side * Math.max(0, (TABLE.width - 8 - span) / 2);
    let x = -span / 2;
    tc.columns.forEach((c, i) => {
      const cx = x + widths[i] / 2;
      x += widths[i] + gap;
      columns.push({ ...c, x: t.x + (flip ? -cx : cx) + shift, rot: flip ? 2 : 0 });
    });
  }

  // z: column by column along the light path, aiming for the mean z of already-placed
  // neighbors (on any table), then resolving overlaps
  columns.sort((a, b) => a.layer - b.layer);
  const z = new Map();
  for (const col of columns) {
    const items = col.parts.map((p) => {
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
      it.p.setPose(col.x, it.z, col.rot);
    }
  }

  // Parts with no optical connection: a row along the back edge of their table
  for (const [t, ps] of loose) {
    let bx = tableSpan(t).xMin + 4;
    for (const p of ps) {
      p.setPose(bx + p.footprint.w / 2, backZ() + p.footprint.d / 2 - 4, 0);
      bx += p.footprint.w + 4;
    }
  }
  layoutShelf(lab, path.tableOf);
  restFibers(lab, true);
}

/** Each table's instruments along its shelf, from the right end. */
export function layoutShelf(lab, tableOf = tablesOf(lab)) {
  for (const t of TABLES) {
    let x = shelfSpan(t).xMax - 2;
    for (const p of [...lab.parts.values()].filter((q) => q.onShelf && tableOf.get(q.name) === t)) {
      p.setShelf(x - p.footprint.w / 2);
      x -= p.footprint.w + 4;
    }
  }
}

/** Put unplugged connectors somewhere sensible (all of them, or only ones never placed). */
export function restFibers(lab, all = false) {
  let fx = tableSpan(tableInView(lab)).xMin + 12;
  // x on the table nearest `near`, `margin` in from its edges
  const onIt = (x, near, margin) => { const s = tableSpan(tableAt(near)); return clamp(x, s.xMin + margin, s.xMax - margin); };
  for (const f of lab.fibers.values()) {
    for (const e of f.ends) {
      if (e.attached || (!all && e.placed)) continue;
      const other = e.other;
      if (other.attached) {
        const pos = new THREE.Vector3(), dir = new THREE.Vector3();
        other.attached.part.portWorld(other.attached.port, pos, dir);
        const target = pos.clone().addScaledVector(dir, 14);
        f.rest(e, onIt(target.x, pos.x, 3), clamp(target.z, -TABLE.depth / 2 + 2, TABLE.depth / 2 - 2), pos);
      } else if (!other.placed || all) {
        const fz = frontZ();
        f.rest(e, fx, fz, new THREE.Vector3(fx + 30, 0, fz + 2));
        f.rest(other, fx + 24, fz + 3, e.position);
        other.placed = true;
        fx += 34;
      } else {
        f.rest(e, onIt(other.position.x + 20, other.position.x, 3), other.position.z, other.position);
      }
      e.placed = true;
    }
  }
}

/** Find a free table spot for a newly added part, near `at` or near its optical neighbors. */
export function placeNew(lab, part, at = null) {
  if (part.onShelf) { layoutShelf(lab); return; }
  const adj = linkGraph(lab);
  const near = [...(adj.get(part.name) ?? [])].map((n) => lab.parts.get(n)).filter((p) => p && p.placed);
  let start = at ?? (near.length
    ? { x: near.reduce((s, p) => s + p.group.position.x, 0) / near.length + 20, z: near.reduce((s, p) => s + p.group.position.z, 0) / near.length }
    : { x: tableInView(lab).x, z: 12 });
  const [hw, hd] = part.halfExtents();
  const free = (x, z) => {
    if (Math.abs(x - tableAt(x).x) + hw > TABLE.width / 2 || Math.abs(z) + hd > TABLE.depth / 2) return false;
    for (const q of lab.parts.values()) {
      if (q === part || q.onShelf || q.mount || !q.placed) continue;
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
