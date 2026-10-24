// Procedural canvas textures: breadboard holes, part labels, glow sprites, fiber light flow.
import * as THREE from 'three';

function canvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  return [c, c.getContext('2d')];
}

/** One breadboard cell (M6 tapped hole on dark anodized steel); repeat it across the top. */
export function makeBreadboardTexture(renderer) {
  const s = 128;
  const [c, g] = canvas(s, s);
  g.fillStyle = '#444a56';
  g.fillRect(0, 0, s, s);
  for (let i = 0; i < 160; i++) {
    const y = Math.random() * s;
    g.strokeStyle = `rgba(255,255,255,${Math.random() * 0.045})`;
    g.beginPath(); g.moveTo(0, y); g.lineTo(s, y); g.stroke();
  }
  const cx = s / 2, r = s * 0.17;
  const cs = g.createRadialGradient(cx, cx, r * 0.9, cx, cx, r * 1.45);
  cs.addColorStop(0, '#22262e'); cs.addColorStop(1, 'rgba(68,74,86,0)');
  g.fillStyle = cs; g.beginPath(); g.arc(cx, cx, r * 1.45, 0, Math.PI * 2); g.fill();
  const hole = g.createRadialGradient(cx - r * 0.2, cx - r * 0.25, r * 0.1, cx, cx, r);
  hole.addColorStop(0, '#020305'); hole.addColorStop(0.8, '#07080b'); hole.addColorStop(1, '#151820');
  g.fillStyle = hole; g.beginPath(); g.arc(cx, cx, r, 0, Math.PI * 2); g.fill();
  g.strokeStyle = 'rgba(255,255,255,0.07)';
  for (let k = 0; k < 3; k++) { g.beginPath(); g.arc(cx, cx, r * (0.55 + k * 0.13), 0.3, 2.4); g.stroke(); }

  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  return tex;
}

function roundRect(g, x, y, w, h, r) {
  g.beginPath();
  g.moveTo(x + r, y);
  g.arcTo(x + w, y, x + w, y + h, r);
  g.arcTo(x + w, y + h, x, y + h, r);
  g.arcTo(x, y + h, x, y, r);
  g.arcTo(x, y, x + w, y, r);
  g.closePath();
}

/**
 * A printed front-panel label. Returns { texture, paint(opts) } so the text can be refreshed
 * when a part's settings change.
 */
export function makeLabel({ w = 512, h = 192 } = {}) {
  const [c, g] = canvas(w, h);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 8;
  function paint({ title = '', lines = [], accent = '#22d3ee', warn = false, bg = '#0b0f16' }) {
    g.clearRect(0, 0, w, h);
    roundRect(g, 3, 3, w - 6, h - 6, 18);
    g.fillStyle = bg; g.fill();
    g.lineWidth = 3; g.strokeStyle = 'rgba(255,255,255,0.10)'; g.stroke();
    g.fillStyle = accent;
    roundRect(g, 18, 22, 10, h - 44, 5); g.fill();
    g.fillStyle = '#eef2f8';
    g.font = `700 ${Math.round(h * 0.27)}px ui-monospace, Menlo, monospace`;
    g.textBaseline = 'top';
    g.fillText(title, 46, h * 0.13);
    g.fillStyle = '#9aa6b8';
    g.font = `500 ${Math.round(h * 0.16)}px ui-sans-serif, system-ui, sans-serif`;
    lines.slice(0, 2).forEach((l, i) => g.fillText(l, 46, h * (0.5 + i * 0.21)));
    if (warn) {
      const x = w - 74, y = 24, s = 50;
      g.fillStyle = '#facc15';
      g.beginPath(); g.moveTo(x + s / 2, y); g.lineTo(x + s, y + s * 0.88); g.lineTo(x, y + s * 0.88); g.closePath(); g.fill();
      g.fillStyle = '#111';
      g.font = `900 ${s * 0.6}px ui-sans-serif, system-ui`;
      g.textAlign = 'center'; g.fillText('!', x + s / 2, y + s * 0.2); g.textAlign = 'left';
    }
    texture.needsUpdate = true;
  }
  return { texture, paint, aspect: w / h };
}

/** Small rounded badge (used for "CH1"/"CH2" tags above detectors). */
export function makeBadge(text, color) {
  const [c, g] = canvas(160, 80);
  roundRect(g, 4, 4, 152, 72, 18);
  g.fillStyle = 'rgba(8,12,18,0.88)'; g.fill();
  g.lineWidth = 5; g.strokeStyle = color; g.stroke();
  g.fillStyle = color;
  g.font = '800 40px ui-monospace, Menlo, monospace';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(text, 80, 42);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

let glowTex = null;
/** Soft radial glow for light leaving an open fiber end. */
export function getGlowTexture() {
  if (glowTex) return glowTex;
  const [c, g] = canvas(128, 128);
  const gr = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  gr.addColorStop(0, 'rgba(255,255,255,1)');
  gr.addColorStop(0.18, 'rgba(255,255,255,0.75)');
  gr.addColorStop(0.45, 'rgba(255,255,255,0.18)');
  gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 128, 128);
  glowTex = new THREE.CanvasTexture(c);
  return glowTex;
}

let flowImg = null;
/** Emissive stripe pattern scrolled along a fiber to show light traveling through it. */
export function makeFlowTexture() {
  if (!flowImg) {
    const [c, g] = canvas(256, 4);
    const gr = g.createLinearGradient(0, 0, 256, 0);
    gr.addColorStop(0.00, 'rgb(40,40,40)');
    gr.addColorStop(0.55, 'rgb(40,40,40)');
    gr.addColorStop(0.80, 'rgb(255,255,255)');
    gr.addColorStop(0.86, 'rgb(150,150,150)');
    gr.addColorStop(1.00, 'rgb(40,40,40)');
    g.fillStyle = gr; g.fillRect(0, 0, 256, 4);
    flowImg = c;
  }
  const t = new THREE.CanvasTexture(flowImg);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

/** Fine horizontal windings for the fiber spool drum. */
export function makeWindingTexture() {
  const [c, g] = canvas(64, 256);
  g.fillStyle = '#c99f12'; g.fillRect(0, 0, 64, 256);
  for (let y = 0; y < 256; y += 4) {
    g.fillStyle = `rgba(0,0,0,${0.18 + Math.random() * 0.12})`; g.fillRect(0, y, 64, 1.4);
    g.fillStyle = 'rgba(255,240,170,0.18)'; g.fillRect(0, y + 2, 64, 1);
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
