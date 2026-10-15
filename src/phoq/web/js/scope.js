// Oscilloscope display and window. Traces and measurements are computed by the Python server and
// streamed as frames; this file only draws them and turns knob changes into API calls.
import { fmtSI, CH_COLORS, esc, clamp } from './util.js';

const DIVX = 10, DIVY = 8, PRE_DIV = 1;
const UNIT_OF = { Photodiode: 'A', SinglePhotonDetector: 'V', FunctionGenerator: 'V', DCPowerSupply: 'V' };

function decode(b64) {
  const bin = atob(b64);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Float32Array(u8.buffer);
}

/** Phosphor-style scope screen drawn into its own canvas (also the 3D screen texture). */
export class ScopeDisplay {
  constructor(name) {
    this.name = name;
    this.canvas = document.createElement('canvas');
    this.canvas.width = 1000;
    this.canvas.height = 640;
    this.g = this.canvas.getContext('2d');
    this.W = 1000; this.H = 640; this.top = 46; this.bottom = 594;
    this.frame = null;
    this.data = [];
    this.running = true;
    this.armed = false;        // single shot: waiting for the first trigger
    this.held = false;         // showing the sweep a single shot captured
    this.dirty = true;
    this.g.fillStyle = '#03090b';
    this.g.fillRect(0, 0, this.W, this.H);
  }

  setFrame(frame) {
    this.frame = frame;
    this.data = frame.channels.map((ch) => (ch.data ? decode(ch.data) : null));
    this.dirty = true;
  }

  draw() {
    const { g, W, H, top, bottom } = this;
    const gh = bottom - top, dx = W / DIVX, dy = gh / DIVY, cy = top + gh / 2;
    g.fillStyle = this.running ? 'rgba(3,9,11,0.55)' : '#03090b';
    g.fillRect(0, top, W, gh);
    g.fillStyle = '#060d10';
    g.fillRect(0, 0, W, top);
    g.fillRect(0, bottom, W, H - bottom);

    g.lineWidth = 1;
    g.strokeStyle = 'rgba(90,160,160,0.13)';
    g.beginPath();
    for (let i = 1; i < DIVX; i++) { g.moveTo(i * dx + 0.5, top); g.lineTo(i * dx + 0.5, bottom); }
    for (let j = 1; j < DIVY; j++) { g.moveTo(0, top + j * dy + 0.5); g.lineTo(W, top + j * dy + 0.5); }
    g.stroke();
    g.strokeStyle = 'rgba(120,200,200,0.32)';
    g.beginPath();
    g.moveTo(W / 2 + 0.5, top); g.lineTo(W / 2 + 0.5, bottom);
    g.moveTo(0, cy + 0.5); g.lineTo(W, cy + 0.5);
    for (let i = 0; i <= DIVX * 5; i++) { const x = (i * dx) / 5 + 0.5; g.moveTo(x, cy - 5); g.lineTo(x, cy + 5); }
    for (let j = 0; j <= DIVY * 5; j++) { const y = top + (j * dy) / 5 + 0.5; g.moveTo(W / 2 - 5, y); g.lineTo(W / 2 + 5, y); }
    g.stroke();
    g.strokeStyle = 'rgba(120,200,200,0.4)';
    g.strokeRect(0.5, top + 0.5, W - 1, gh - 1);

    const f = this.frame;
    if (f) {
      g.save();
      g.beginPath(); g.rect(0, top, W, gh); g.clip();
      f.channels.forEach((ch, k) => {
        const d = this.data[k];
        if (!d || !ch.enabled) return;
        g.strokeStyle = CH_COLORS[k];
        g.shadowColor = CH_COLORS[k];
        g.shadowBlur = 7;
        g.lineWidth = 2;
        g.beginPath();
        for (let i = 0; i < d.length; i++) {
          const x = (i / (d.length - 1)) * W;
          const y = cy - (d[i] / ch.scale + ch.offset) * dy;
          if (i) g.lineTo(x, y); else g.moveTo(x, y);
        }
        g.stroke();
      });
      g.restore();
      f.channels.forEach((ch, k) => {
        if (!ch.enabled || !ch.source) return;
        const y = cy - ch.offset * dy;
        g.fillStyle = CH_COLORS[k];
        g.beginPath(); g.moveTo(0, y - 9); g.lineTo(16, y); g.lineTo(0, y + 9); g.fill();
        g.fillStyle = '#05090b';
        g.font = '700 12px ui-monospace, Menlo, monospace';
        g.fillText(String(k + 1), 2, y + 4);
      });
    }
    const tx = PRE_DIV * dx;
    g.fillStyle = '#fb923c';
    g.beginPath(); g.moveTo(tx - 8, top); g.lineTo(tx + 8, top); g.lineTo(tx, top + 11); g.fill();
    // Edge trigger: a "T" arrow on the right edge at the trigger level, in the source channel's scale.
    if (f && f.trigger === 'edge') {
      const ch = f.channels[Number((f.trigger_source || 'ch1').slice(2)) - 1];
      if (ch?.source) {
        const y = clamp(cy - (f.trigger_level / ch.scale + ch.offset) * dy, top + 9, bottom - 9);
        g.beginPath(); g.moveTo(W, y - 9); g.lineTo(W - 18, y); g.lineTo(W, y + 9); g.fill();
        g.fillStyle = '#05090b';
        g.font = '700 12px ui-monospace, Menlo, monospace';
        g.fillText('T', W - 9, y + 4);
      }
    }
    this.drawText();
    this.dirty = false;
  }

  drawText() {
    const { g, W, H, bottom } = this;
    const f = this.frame;
    g.textBaseline = 'middle';
    g.font = '600 19px ui-monospace, Menlo, monospace';
    let x = 14;
    for (const [k, ch] of (f?.channels ?? []).entries()) {
      if (!ch.source) continue;
      const txt = `CH${k + 1} ${fmtSI(ch.scale, ch.unit)}/div`;
      g.fillStyle = ch.enabled ? CH_COLORS[k] : '#3b4652';
      g.fillText(txt, x, 15);
      x += g.measureText(txt).width + 22;
    }
    if (!f || !f.channels.some((c) => c.source)) {
      g.fillStyle = '#64748b';
      g.fillText('no input connected', 14, 15);
    }
    g.textAlign = 'right';
    if (f) {
      g.fillStyle = '#e2e8f0';
      g.fillText(`${fmtSI(f.timebase, 's')}/div`, W - 230, 15);
      g.fillStyle = '#fb923c';
      g.fillText(`T ${f.trigger.toUpperCase()}`, W - 100, 15);
    }
    const [status, color] = this.armed ? ['WAIT', '#fbbf24'] : this.held ? ['SINGLE', '#38bdf8']
      : !this.running ? ['STOP', '#f87171'] : f?.triggered ? ["Trig'd", '#34d399'] : ['Auto', '#fbbf24'];
    g.fillStyle = color;
    g.fillText(status, W - 12, 15);
    g.textAlign = 'left';
    if (f?.fs) {                                    // acquisition settings, like a scope's status line
      g.font = '500 14px ui-monospace, Menlo, monospace';
      g.fillStyle = '#64748b';
      g.fillText(`${fmtSI(f.fs, 'S/s')} · BW ${fmtSI(f.bandwidth, 'Hz')} · ${f.points.toLocaleString()} pts`, 14, 36);
    }

    g.font = '500 18px ui-monospace, Menlo, monospace';
    let fx = 14;
    const y = (bottom + H) / 2;
    for (const [k, ch] of (f?.channels ?? []).entries()) {
      if (!ch.enabled || !ch.source || !ch.meas) continue;
      const m = ch.meas;
      const txt = `CH${k + 1} avg ${fmtSI(m.mean, ch.unit)}  pk-pk ${fmtSI(m.pk_pk, ch.unit)}${m.freq ? `  f ${fmtSI(m.freq, 'Hz')}` : ''}`;
      g.fillStyle = CH_COLORS[k];
      g.fillText(txt, fx, y);
      fx += g.measureText(txt).width + 26;
    }
  }
}

/** The floating HTML oscilloscope window, bound to one Oscilloscope component on the server. */
export class ScopePanel {
  constructor(lab) {
    this.lab = lab;
    const $ = (id) => document.getElementById(id);
    this.el = $('scope-panel');
    this.select = $('scope-select');
    this.canvas = $('scope-canvas');
    this.ctx = this.canvas.getContext('2d');
    this.empty = $('scope-empty');
    this.body = this.el.querySelector('.scope-body');
    this.tb = $('scope-tb');
    this.trig = $('scope-trig');
    this.level = $('scope-level');
    this.levelUnit = $('scope-level-unit');
    this.hposOut = $('scope-hpos');
    this.bw = $('scope-bw');
    this.fs = $('scope-fs');
    this.runBtn = $('scope-run');
    this.singleBtn = $('scope-single');
    this.chEl = $('scope-channels');
    this.measEl = $('scope-meas');
    this.current = null;
    this.lastMeas = 0;

    const set = (params) => this.current && lab.setParams(this.current, params);
    this.select.onchange = () => { this.current = this.select.value; this.sync(); };
    this.tb.onchange = () => set({ timebase: Number(this.tb.value) });
    this.trig.onchange = () => {
      const [trigger, src] = this.trig.value.split(':');
      set({ trigger, trigger_source: src ?? '' });
    };
    this.level.onchange = () => set({ trigger_level: Number(this.level.value) * (this.levelScale ?? 1) });
    this.bw.onchange = () => set({ bandwidth: Number(this.bw.value) });
    this.fs.onchange = () => set({ sample_rate: Number(this.fs.value) });
    this.el.querySelectorAll('[data-h]').forEach((b) => {
      b.onclick = () => { const c = this.comp(); if (c) set({ h_position: c.params.h_position + Number(b.dataset.h) * c.params.timebase }); };
      b.ondblclick = () => set({ h_position: 0 });
    });
    this.runBtn.onclick = () => { const c = this.comp(); if (c) set({ running: !c.params.running }); };
    this.singleBtn.onclick = () => this.current && lab.single(this.current);   // arms, or cancels a pending wait
    $('scope-auto').onclick = () => this.current && lab.autoset(this.current);
    $('scope-min').onclick = () => {
      this.el.classList.toggle('collapsed');
      $('scope-min').textContent = this.el.classList.contains('collapsed') ? '+' : '–';
    };
    if (window.matchMedia('(max-width: 820px)').matches) $('scope-min').click();

    this.rows = CH_COLORS.map((color, k) => {
      const row = document.createElement('div');
      row.className = 'chrow';
      row.style.setProperty('--c', color);
      row.innerHTML = `
        <label class="chname"><input type="checkbox" aria-label="CH${k + 1} on" /><span>CH${k + 1}</span></label>
        <select class="src" aria-label="CH${k + 1} input"></select>
        <select class="scale" aria-label="CH${k + 1} scale"></select>
        <span class="pos"><button data-d="1" title="Move trace up">▲</button><button data-d="-1" title="Move trace down">▼</button></span>`;
      const on = row.querySelector('input'), src = row.querySelector('.src'), scale = row.querySelector('.scale');
      const ch = `ch${k + 1}`;
      on.onchange = () => set({ [`${ch}_enabled`]: on.checked });
      scale.onchange = () => set({ [`${ch}_scale`]: Number(scale.value) });
      src.onchange = async () => {
        const port = `${this.current}.${ch}`;
        const c = this.comp();
        const peer = c?.ports.find((p) => p.name === ch)?.peer;
        if (peer) await lab.disconnect(port, true);
        if (src.value) await lab.connect(src.value, port);
      };
      row.querySelectorAll('[data-d]').forEach((b) => {
        b.onclick = () => { const c = this.comp(); if (c) set({ [`${ch}_offset`]: c.params[`${ch}_offset`] + Number(b.dataset.d) * 0.5 }); };
      });
      this.chEl.appendChild(row);
      return { row, on, src, scale };
    });
    this.makeDraggable($('scope-drag'));
  }

  comp() { return this.lab.state?.components.find((c) => c.name === this.current) ?? null; }

  /** Rebuild the controls from the latest server state. */
  sync() {
    const state = this.lab.state;
    const scopes = state?.components.filter((c) => c.type === 'Oscilloscope') ?? [];
    if (!scopes.some((s) => s.name === this.current)) this.current = scopes[0]?.name ?? null;
    this.select.innerHTML = scopes.map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('') || '<option>—</option>';
    this.select.value = this.current ?? '';
    this.select.disabled = scopes.length < 2;
    const c = this.comp();
    this.empty.hidden = !!c;
    this.body.hidden = !c;
    if (!c) return;

    const p = c.params;
    const spec = (n) => this.lab.spec('Oscilloscope', n);
    this.tb.innerHTML = (spec('timebase')?.presets ?? [p.timebase]).map((v) => `<option value="${v}">${fmtSI(v, 's')}/div</option>`).join('');
    this.tb.value = String(p.timebase);
    if (this.tb.value !== String(p.timebase)) this.tb.insertAdjacentHTML('beforeend', `<option value="${p.timebase}" selected>${fmtSI(p.timebase, 's')}/div</option>`);
    this.trig.innerHTML = ['<option value="sync">Laser SYNC</option>']
      .concat([1, 2, 3, 4].map((i) => `<option value="edge:ch${i}">CH${i} edge ↑</option>`), ['<option value="free">Free run</option>']).join('');
    this.trig.value = p.trigger === 'edge' ? `edge:${p.trigger_source || 'ch1'}` : p.trigger;
    const edgeCh = p.trigger === 'edge' ? Number((p.trigger_source || 'ch1').slice(2)) - 1 : -1;
    const unit = edgeCh >= 0 ? this.unitOf(c, edgeCh) : 'A';
    this.levelScale = unit === 'A' ? 1e-3 : 1;
    this.levelUnit.textContent = unit === 'A' ? 'mA' : unit;
    this.level.value = Number((p.trigger_level / this.levelScale).toPrecision(3));
    this.level.disabled = p.trigger !== 'edge';
    this.hposOut.textContent = fmtSI(p.h_position, 's');
    const fill = (sel, key, unit) => {
      const vals = [...new Set([...(spec(key)?.presets ?? []), p[key]])].sort((a, b) => a - b);
      sel.innerHTML = vals.map((v) => `<option value="${v}">${fmtSI(v, unit)}</option>`).join('');
      sel.value = String(p[key]);
    };
    fill(this.bw, 'bandwidth', 'Hz');
    fill(this.fs, 'sample_rate', 'S/s');
    this.runBtn.textContent = p.running ? 'RUN' : 'STOP';
    this.runBtn.classList.toggle('on', p.running);
    this.el.classList.toggle('stopped', !p.running);
    const mode = c.info?.Mode ?? (p.running ? 'run' : 'stop');
    const armed = mode.startsWith('wait'), held = mode.startsWith('hold');
    this.singleBtn.textContent = armed ? 'WAIT…' : 'SINGLE';
    this.singleBtn.classList.toggle('armed', armed);
    this.singleBtn.title = armed ? 'Waiting for the first trigger — press again to cancel' : 'Capture the first triggered sweep and hold it';
    this.el.classList.toggle('armed', armed);
    this.el.classList.toggle('held', held);
    const part = this.lab.parts.get(c.name);
    if (part?.display) {
      Object.assign(part.display, { running: p.running, armed, held });
      part.display.dirty = true;
    }

    const outs = state.components.flatMap((d) => d.ports.filter((pt) => pt.kind === 'electrical' && pt.direction === 'out').map((pt) => ({ comp: d, port: pt })));
    this.rows.forEach((r, k) => {
      const ch = `ch${k + 1}`;
      const peer = c.ports.find((pt) => pt.name === ch)?.peer;
      r.on.checked = p[`${ch}_enabled`];
      r.src.innerHTML = '<option value="">— no input —</option>' + outs
        .filter(({ port }) => !port.peer || port.peer === `${c.name}.${ch}`)
        .map(({ comp, port }) => `<option value="${esc(comp.name)}.${esc(port.name)}">${esc(comp.name)} · ${esc(port.label)}</option>`).join('');
      r.src.value = peer ?? '';
      const u = this.unitOf(c, k);
      r.scale.innerHTML = (spec(`${ch}_scale`)?.presets ?? []).map((v) => `<option value="${v}">${fmtSI(v, u)}/div</option>`).join('');
      r.scale.value = String(p[`${ch}_scale`]);
      r.row.classList.toggle('idle', !peer);
    });
  }

  unitOf(c, k) {
    const peer = c.ports.find((pt) => pt.name === `ch${k + 1}`)?.peer;
    if (!peer) return '';
    const comp = this.lab.state.components.find((d) => d.name === peer.split('.')[0]);
    return UNIT_OF[comp?.type] ?? '';
  }

  /** Called every animation frame: mirror the selected scope's display into the window. */
  render(now) {
    const part = this.current && this.lab.parts.get(this.current);
    if (!part?.display || this.el.classList.contains('hidden') || this.el.classList.contains('collapsed')) return;
    this.ctx.drawImage(part.display.canvas, 0, 0, this.canvas.width, this.canvas.height);
    if (now - this.lastMeas > 250) {
      this.lastMeas = now;
      this.updateMeasurements(part.display.frame);
    }
  }

  updateMeasurements(frame) {
    if (!frame) { this.measEl.innerHTML = ''; return; }
    this.measEl.innerHTML = frame.channels.map((ch, k) => {
      if (!ch.source) return '';
      const m = ch.meas ?? {};
      const rows = [['Mean', fmtSI(m.mean, ch.unit)], ['Pk-pk', fmtSI(m.pk_pk, ch.unit)], ['Max', fmtSI(m.max, ch.unit)], ['RMS', fmtSI(m.rms, ch.unit)], ['Freq', m.freq ? fmtSI(m.freq, 'Hz') : '—']];
      return `<div class="meas" style="--c:${CH_COLORS[k]}"><h4>CH${k + 1} · ${esc(ch.source)}</h4><dl>${rows.map(([a, b]) => `<dt>${a}</dt><dd>${b}</dd>`).join('')}</dl></div>`;
    }).join('');
  }

  makeDraggable(handle) {
    let start = null;
    handle.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button, select')) return;
      const r = this.el.getBoundingClientRect(), pr = this.el.offsetParent.getBoundingClientRect();
      start = { x: e.clientX, y: e.clientY, left: r.left - pr.left, top: r.top - pr.top, pr, w: r.width };
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener('pointermove', (e) => {
      if (!start) return;
      const left = Math.min(Math.max(0, start.left + e.clientX - start.x), start.pr.width - start.w);
      const top = Math.min(Math.max(0, start.top + e.clientY - start.y), start.pr.height - 40);
      Object.assign(this.el.style, { left: `${left}px`, top: `${top}px`, right: 'auto', bottom: 'auto' });
    });
    handle.addEventListener('pointerup', () => { start = null; });
  }
}
