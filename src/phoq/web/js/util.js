// Shared constants and formatting. Scene unit: 1 = 1 cm; the breadboard top is at y = 0.
// No physics lives here: every number shown comes from the Python lab server.

export const GRID = 2.5;                // M6 hole pitch: 25 mm
export const TABLE = { width: 150, depth: 90, thickness: 20, legHeight: 72 };
export const SHELF = { y: 48.8, z: -59, xMin: -72, xMax: 72 };

// Common optical-table / breadboard sizes [cm] (width along x, depth along z).
export const TABLE_PRESETS = [
  { w: 90, d: 60, label: 'Breadboard' },
  { w: 120, d: 90, label: 'Small table' },
  { w: 150, d: 90, label: 'Standard' },
  { w: 180, d: 120, label: '6′ × 4′' },
  { w: 240, d: 120, label: '8′ × 4′' },
  { w: 300, d: 150, label: '10′ × 5′' },
  { w: 360, d: 150, label: '12′ × 5′' },
  { w: 480, d: 180, label: '16′ × 6′' },
];
export const TABLE_LIMITS = { wMin: 60, wMax: 500, dMin: 45, dMax: 200 };

/** Resize the table (multiples of 5 cm keep the hole grid aligned with the edges). */
export function setTableDims(w, d) {
  const r5 = (v) => Math.round(v / 5) * 5;
  TABLE.width = clamp(r5(w), TABLE_LIMITS.wMin, TABLE_LIMITS.wMax);
  TABLE.depth = clamp(r5(d), TABLE_LIMITS.dMin, TABLE_LIMITS.dMax);
  SHELF.z = -TABLE.depth / 2 - 14;
  SHELF.xMin = -TABLE.width / 2 + 3;
  SHELF.xMax = TABLE.width / 2 - 3;
  return { w: TABLE.width, d: TABLE.depth };
}

/** Table size as metres, e.g. "1.5 × 0.9 m". */
export const fmtTable = (w, d) => `${w / 100} × ${d / 100} m`;
export const CH_COLORS = ['#facc15', '#22d3ee', '#f472b6', '#4ade80'];

const PREFIX = [[1e9, 'G'], [1e6, 'M'], [1e3, 'k'], [1, ''], [1e-3, 'm'], [1e-6, 'µ'], [1e-9, 'n'], [1e-12, 'p'], [1e-15, 'f']];

/** Format a number with an SI prefix: fmtSI(2.3e-4, 'A') -> "230 µA". */
export function fmtSI(v, unit = '', digits = 3) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  if (v === 0) return `0 ${unit}`.trim();
  const a = Math.abs(v);
  for (const [s, p] of PREFIX) {
    if (a >= s * 0.9995) return `${Number((v / s).toPrecision(digits))} ${p}${unit}`;
  }
  return `${Number((v / 1e-15).toPrecision(digits))} f${unit}`;
}

const PLAIN_UNITS = new Set(['dB', 'dBm', 'rad', 'deg', 'div', 'K', 'Ω', '']);

/** Display a parameter value using its spec (unit, display scaling such as metres → nm). */
export function fmtParam(value, spec) {
  if (spec.type === 'bool') return value ? 'on' : 'off';
  if (typeof value !== 'number') return String(value);
  if (spec.display) return `${Number((value * spec.display[1]).toPrecision(5))} ${spec.display[0]}`;
  if (PLAIN_UNITS.has(spec.unit)) {
    const s = Math.abs(value) >= 1e5 || (value !== 0 && Math.abs(value) < 1e-3) ? value.toExponential(2) : Number(value.toPrecision(4)).toString();
    return `${s} ${spec.unit}`.trim();
  }
  return fmtSI(value, spec.unit);
}

/** Wavelength → colour: true colour in the visible, false colour for the infrared bands. */
export function wavelengthColor(m) {
  const nm = m * 1e9;
  if (nm >= 380 && nm <= 700) {
    let r = 0, g = 0, b = 0;
    if (nm < 440) { r = (440 - nm) / 60; b = 1; }
    else if (nm < 490) { g = (nm - 440) / 50; b = 1; }
    else if (nm < 510) { g = 1; b = (510 - nm) / 20; }
    else if (nm < 580) { r = (nm - 510) / 70; g = 1; }
    else if (nm < 645) { r = 1; g = (645 - nm) / 65; }
    else { r = 1; }
    const c = [r, g, b].map((v) => Math.round(255 * Math.min(1, v * 0.9 + 0.1)));
    return { hex: (c[0] << 16) | (c[1] << 8) | c[2], css: `rgb(${c.join(',')})` };
  }
  if (nm < 1000) return { hex: 0xe0245e, css: '#ff4d7d' };
  if (nm < 1450) return { hex: 0x9b5cff, css: '#a978ff' };
  return { hex: 0xff3fb4, css: '#ff5cc0' };
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Snap a coordinate to the centre of the nearest breadboard hole. */
export const snapToHole = (v) => Math.round((v - GRID / 2) / GRID) * GRID + GRID / 2;

export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
