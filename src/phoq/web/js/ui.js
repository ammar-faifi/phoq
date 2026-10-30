// Side panels: toolbox (from the Python catalog), inspector (from each component's Param specs),
// warnings, toasts, help and the server-connection banner. Every edit goes to the API.
import { fmtSI, fmtParam, esc, wavelengthColor, fmtTable, TABLE, TABLE_PRESETS, TABLE_LIMITS, connectorCss } from './util.js';
import { suggestTableSize } from './layout.js';

const svg = (body) => `<svg viewBox="0 0 32 32" width="28" height="28" fill="none" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
export const ICONS = {
  Laser: svg('<rect x="3" y="10" width="15" height="12" rx="2" stroke="#7aa2d6"/><path d="M18 16h3" stroke="#cbd5e1"/><path d="M22 16h8" stroke="#ff3fb4" stroke-width="2.4"/><circle cx="7" cy="14" r="1.3" fill="#ff3fb4" stroke="none"/>'),
  Photodiode: svg('<rect x="13" y="9" width="13" height="14" rx="2" stroke="#7aa2d6"/><path d="M2 16h8" stroke="#ff3fb4" stroke-width="2.4"/><path d="M8 13l3 3-3 3" stroke="#ff3fb4"/><path d="M26 16h4" stroke="#d6b25e" stroke-width="2.4"/><path d="M17 19l2-6 2 6" stroke="#34d399"/>'),
  BalancedPhotodetector: svg('<rect x="10" y="6" width="17" height="20" rx="2" stroke="#7aa2d6"/><path d="M1 11h9M1 21h9" stroke="#ff3fb4" stroke-width="2.2"/><path d="M13 11h3.5M14.75 9.25v3.5M13 21h3.5" stroke="#34d399"/><path d="M19 16h4" stroke="#cbd5e1"/><path d="M27 11h4M27 16h4M27 21h4" stroke="#d6b25e"/>'),
  SinglePhotonDetector: svg('<rect x="11" y="9" width="15" height="14" rx="2" stroke="#7aa2d6"/><path d="M13 6h11M13 4h11" stroke="#64748b"/><path d="M2 16h7" stroke="#ff3fb4" stroke-width="2.4"/><circle cx="18.5" cy="16" r="2.4" fill="#22d3ee" stroke="none"/>'),
  PowerMeter: svg('<rect x="13" y="8" width="16" height="16" rx="2" stroke="#7aa2d6"/><rect x="16" y="11" width="10" height="6" rx="1" stroke="#2dd4bf"/><path d="M2 16h9" stroke="#ff3fb4" stroke-width="2.4"/>'),
  Fiber: svg('<path d="M4 22c6 0 6-12 12-12s6 12 12 12" stroke="#e8c21a" stroke-width="2.2"/><rect x="1" y="20" width="5" height="4" rx="1" fill="#2563eb" stroke="none"/><rect x="26" y="20" width="5" height="4" rx="1" fill="#2563eb" stroke="none"/>'),
  Coupler: svg('<path d="M2 10h7c4 0 5 6 7 6s3-6 7-6h7M2 22h7c4 0 5-6 7-6s3 6 7 6h7" stroke="#a3e635"/><rect x="11" y="13" width="10" height="6" rx="1.5" stroke="#7aa2d6"/>'),
  Attenuator: svg('<rect x="6" y="12" width="20" height="12" rx="2" stroke="#7aa2d6"/><circle cx="16" cy="9" r="4.5" stroke="#fb923c"/><path d="M16 9l2.5-2.5" stroke="#fb923c"/><path d="M1 18h5M26 18h5" stroke="#e8c21a"/>'),
  FiberSpool: svg('<ellipse cx="16" cy="16" rx="12" ry="12" stroke="#7aa2d6"/><circle cx="16" cy="16" r="8.5" stroke="#e8c21a" stroke-dasharray="2 1.4"/><circle cx="16" cy="16" r="3" stroke="#cbd5e1"/>'),
  PhaseShifter: svg('<rect x="7" y="10" width="18" height="12" rx="2" stroke="#7aa2d6"/><path d="M16 12v8M12.5 16a3.5 3.5 0 1 0 7 0a3.5 3.5 0 1 0-7 0" stroke="#c084fc"/><path d="M1 16h6M25 16h6" stroke="#e8c21a"/>'),
  FiberTerminator: svg('<path d="M1 16h7" stroke="#e8c21a"/><rect x="8" y="13" width="3" height="6" rx="0.8" fill="#16a34a" stroke="none"/><rect x="11" y="11.5" width="12" height="9" rx="2" stroke="#cbd5e1"/><path d="M23 13.5h2.5v5H23" stroke="#94a3b8"/>'),
  MatingSleeve: svg('<rect x="13" y="9" width="6" height="14" rx="1" stroke="#cbd5e1"/><path d="M6 13h7v6H6zM19 13h7v6h-7z" stroke="#94a3b8"/><path d="M1 16h5M26 16h5" stroke="#e8c21a"/><rect x="2" y="14" width="3" height="4" rx="0.8" fill="#2563eb" stroke="none"/><rect x="27" y="14" width="3" height="4" rx="0.8" fill="#16a34a" stroke="none"/>'),
  OpticalDelayLine: svg('<rect x="5" y="11" width="22" height="11" rx="2" stroke="#7aa2d6"/><path d="M8 19h16" stroke="#94a3b8"/><rect x="15" y="13.5" width="4.5" height="4" rx="1" stroke="#f59e0b"/><path d="M8 15.5h7" stroke="#ff3fb4" stroke-dasharray="1.6 1.2"/><path d="M11 7h10M11 7l2-2M11 7l2 2M21 7l-2-2M21 7l-2 2" stroke="#f59e0b"/><path d="M1 16h4M27 16h4" stroke="#e8c21a"/>'),
  Isolator: svg('<rect x="7" y="11" width="18" height="10" rx="5" stroke="#7aa2d6"/><path d="M11 16h9M17 13l3 3-3 3" stroke="#cbd5e1"/><path d="M1 16h6M25 16h6" stroke="#e8c21a"/>'),
  PhaseModulator: svg('<rect x="4" y="12" width="24" height="8" rx="1.5" stroke="#d6b25e"/><path d="M8 16c2-3 4 3 6 0s4 3 6 0 4 3 4 0" stroke="#f472b6"/><path d="M16 12V7" stroke="#d6b25e"/>'),
  IQModulator: svg('<rect x="3" y="8" width="26" height="16" rx="1.5" stroke="#d6b25e"/><path d="M3 16h4l3-4h12l3 4h4M7 16l3 4h12l3-4" stroke="#a78bfa"/><path d="M13 12V5M13 20v7" stroke="#d6b25e"/>'),
  IntensityModulator: svg('<rect x="4" y="12" width="24" height="8" rx="1.5" stroke="#d6b25e"/><path d="M8 18h3v-4h4v4h3v-4h4v4h2" stroke="#fbbf24"/><path d="M16 12V7" stroke="#d6b25e"/>'),
  ArbitraryWaveformGenerator: svg('<rect x="3" y="7" width="26" height="18" rx="2" stroke="#7aa2d6"/><rect x="6" y="10" width="13" height="8" rx="1" stroke="#334155"/><path d="M7 14c1.5-3 3-3 4.5 0s3 3 4.5 0" stroke="#a78bfa"/><circle cx="23" cy="13" r="1.6" stroke="#facc15"/><circle cx="23" cy="19" r="1.6" stroke="#22d3ee"/><path d="M7 22h11" stroke="#475569"/>'),
  DCPowerSupply: svg('<rect x="3" y="7" width="26" height="18" rx="2" stroke="#7aa2d6"/><rect x="6" y="10" width="12" height="6" rx="1" stroke="#334155"/><path d="M8 13h8" stroke="#f87171"/><circle cx="23" cy="13" r="1.8" stroke="#f87171"/><circle cx="23" cy="19" r="1.8" stroke="#60a5fa"/><path d="M7 21h4M13 21h4" stroke="#475569"/>'),
  Oscilloscope: svg('<rect x="3" y="6" width="26" height="20" rx="2" stroke="#7aa2d6"/><rect x="6" y="9" width="15" height="11" rx="1" stroke="#334155"/><path d="M7 16c2 0 2-5 4-5s2 7 4 7 2-4 5-4" stroke="#facc15"/><circle cx="25" cy="12" r="1.6" stroke="#cbd5e1"/><circle cx="25" cy="18" r="1.6" stroke="#cbd5e1"/>'),
  SpectrumAnalyzer: svg('<rect x="3" y="6" width="26" height="20" rx="2" stroke="#7aa2d6"/><rect x="6" y="9" width="15" height="11" rx="1" stroke="#334155"/><path d="M7 19l2-1 1.5-6 1.5 6 2-3 2 2 2-1 2 2" stroke="#facc15"/><circle cx="25" cy="20" r="1.6" stroke="#facc15"/>'),
  PhotonCounter: svg('<rect x="3" y="8" width="26" height="16" rx="2" stroke="#7aa2d6"/><path d="M7 13h4M7 17h7M7 21h3" stroke="#22d3ee"/><path d="M18 20v-6M21 20v-9M24 20v-4" stroke="#facc15"/>'),
};
const GENERIC = svg('<rect x="6" y="8" width="20" height="16" rx="2" stroke="#7aa2d6"/><path d="M1 16h5M26 16h5" stroke="#e8c21a"/>');
const GROUPS = [['source', 'Sources'], ['cord', 'Fiber'], ['passive', 'Passive optics'], ['modulator', 'Modulators'], ['detector', 'Detectors'], ['instrument', 'Instruments']];
// Provenance badges for values that come from a part preset (phoq.presets).
const PROV_SHORT = { 'datasheet-typ': 'typ', 'datasheet-min': 'min', 'datasheet-max': 'max', 'datasheet-graph': 'graph', 'datasheet-condition': 'cond', derived: 'derived', measured: 'measured', assumed: 'assumed', unstated: '?' };
const provTitle = (p) => [
  `${(p.kind ?? '').replace('-', ' ')}${p.overridden ? ' · edited since the preset was applied' : ''}`, p.spec,
  ...['min', 'typ', 'max'].filter((k) => p[k] != null).map((k) => `${k} ${p[k]}`), p.note,
].filter(Boolean).join(' · ');

export class UI {
  constructor(lab) {
    this.lab = lab;
    const $ = (id) => document.getElementById(id);
    this.body = $('inspector-body');
    this.warnEl = $('warnings');
    this.toastEl = $('toasts');
    this.help = $('help');
    this.offline = $('offline');
    this.scopeBtn = $('btn-scope');
    this.lastWarn = '';
    this.throttle = new Map();

    $('btn-flow').onclick = (e) => { lab.showFlow = !lab.showFlow; e.currentTarget.classList.toggle('on', lab.showFlow); };
    this.scopeBtn.onclick = () => this.showScope(document.getElementById('scope-panel').classList.contains('hidden'));
    $('btn-layout').onclick = () => lab.relayout();
    this.buildTablePicker();
    $('btn-view').onclick = () => lab.resetView();
    $('btn-export').onclick = () => this.exportPython();
    $('btn-help').onclick = () => this.toggleHelp(true);
    $('help-close').onclick = () => this.toggleHelp(false);
    this.help.addEventListener('click', (e) => { if (e.target === this.help) this.toggleHelp(false); });
    $('offline-cmd').textContent = 'phoq serve examples/basic_link.py';
    this.setupToolboxDrawer();
    let seen = false;
    try { seen = localStorage.getItem('phoq.helpSeen2') === '1'; } catch { /* storage unavailable */ }
    if (!seen) this.toggleHelp(true);
  }

  // ---- toolbox ---------------------------------------------------------------------------
  /**
   * The toolbox stays hidden and slides out while the pointer is on its tab or on the panel.
   * Dragging a tool out closes it, so the table is in view for the drop. Clicking or tapping the
   * tab, or focusing it with the keyboard, opens it too; leaving, Escape or a click on the table
   * closes it.
   */
  setupToolboxDrawer() {
    const tab = document.getElementById('toolbox-tab'), box = document.getElementById('toolbox');
    let timer = 0;
    const set = (open) => {
      clearTimeout(timer);
      box.classList.toggle('open', open);
      tab.setAttribute('aria-expanded', String(open));
    };
    const later = (open, ms) => { clearTimeout(timer); timer = setTimeout(() => set(open), ms); };
    this.toolbox = { open: () => set(true), close: () => set(false) };

    tab.addEventListener('mouseenter', () => later(true, 60));     // a pointer passing by does not open it
    tab.addEventListener('mouseleave', () => { if (!box.matches(':hover')) later(false, 220); });
    box.addEventListener('mouseenter', () => clearTimeout(timer));
    box.addEventListener('mouseleave', () => { if (!box.contains(document.activeElement)) later(false, 220); });
    tab.addEventListener('click', () => set(true));   // touch has no hover; leaving, Escape or the table close it
    tab.addEventListener('focus', () => set(true));
    box.addEventListener('focusout', (e) => { if (!box.contains(e.relatedTarget) && e.relatedTarget !== tab) later(false, 0); });
    box.addEventListener('dragstart', () => setTimeout(() => set(false), 0));  // after the drag image is taken
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape' || !box.classList.contains('open')) return;
      if (box.contains(document.activeElement) || document.activeElement === tab) document.activeElement.blur();
      set(false);
    });
    this.lab.viewport.addEventListener('pointerdown', () => set(false), { capture: true });
  }

  buildToolbox(catalog) {
    const byCat = Object.fromEntries(GROUPS.map(([k]) => [k, []]));
    for (const t of catalog.types) (byCat[t.category] ??= []).push(t);
    document.getElementById('tool-list').innerHTML = Object.entries(byCat).filter(([, ts]) => ts.length).map(([cat, ts]) => `
      <div class="tool-group">${GROUPS.find(([k]) => k === cat)?.[1] ?? cat}</div>
      ${ts.map((t) => {
        const n = (t.presets ?? []).filter((p) => p.tier !== 'generic').length;
        return `
        <div class="tool" draggable="true" data-type="${esc(t.type)}" title="${esc(t.description)} — drag onto the table or click to add">
          <div class="ico">${ICONS[t.type] ?? GENERIC}</div>
          <div><b>${esc(t.label)}</b><small>${esc(t.description)}</small>${n ? `<small class="presets-n">${n} part preset${n > 1 ? 's' : ''}</small>` : ''}</div>
        </div>`;
      }).join('')}`).join('');
    for (const el of document.querySelectorAll('.tool')) {
      el.addEventListener('dragstart', (e) => {
        e.dataTransfer.setData('application/x-lab-item', el.dataset.type);
        e.dataTransfer.effectAllowed = 'copy';
      });
      el.addEventListener('click', () => this.lab.add(el.dataset.type, null));
    }
  }

  // ---- table size ------------------------------------------------------------------------
  buildTablePicker() {
    const $ = (id) => document.getElementById(id);
    const btn = $('btn-table'), pop = $('table-pop');
    const maxW = Math.max(...TABLE_PRESETS.map((t) => t.w)), maxD = Math.max(...TABLE_PRESETS.map((t) => t.d));
    $('table-presets').innerHTML = TABLE_PRESETS.map((t, i) => `
      <button class="preset" data-i="${i}" title="${t.w * 10} × ${t.d * 10} mm">
        <span class="shape"><i style="width:${Math.round(8 + (36 * t.w) / maxW)}px;height:${Math.round(6 + (22 * t.d) / maxD)}px"></i></span>
        <span><b>${fmtTable(t.w, t.d)}</b><small>${esc(t.label)}</small></span>
      </button>`).join('');
    const apply = (w, d) => {
      this.lab.resizeTable(w, d, { relayout: $('table-relayout').checked });
      this.toast(`Table: ${fmtTable(TABLE.width, TABLE.depth)}`);
      this.toggleTablePicker(false);
    };
    for (const b of pop.querySelectorAll('.preset')) {
      b.onclick = () => { const t = TABLE_PRESETS[Number(b.dataset.i)]; apply(t.w, t.d); };
    }
    const lim = TABLE_LIMITS;
    $('table-apply').onclick = () => {
      const w = Number($('table-w').value) / 10, d = Number($('table-d').value) / 10;
      if (!(w >= lim.wMin && w <= lim.wMax && d >= lim.dMin && d <= lim.dMax)) {
        this.toast(`Table size must be ${lim.wMin * 10}–${lim.wMax * 10} × ${lim.dMin * 10}–${lim.dMax * 10} mm`, 'err');
        return;
      }
      apply(w, d);
    };
    for (const id of ['table-w', 'table-d']) $(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') $('table-apply').click(); });
    $('table-fit').onclick = () => { const t = suggestTableSize(this.lab); apply(t.w, t.d); };
    btn.onclick = () => this.toggleTablePicker(pop.hidden);
    document.addEventListener('pointerdown', (e) => {
      if (!pop.hidden && !pop.contains(e.target) && !btn.contains(e.target)) this.toggleTablePicker(false);
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') this.toggleTablePicker(false); });
  }

  toggleTablePicker(show) {
    const pop = document.getElementById('table-pop');
    pop.hidden = !show;
    document.getElementById('btn-table').setAttribute('aria-expanded', String(show));
    if (!show) return;
    document.getElementById('table-w').value = TABLE.width * 10;
    document.getElementById('table-d').value = TABLE.depth * 10;
    const fit = this.lab.parts.size ? suggestTableSize(this.lab) : null;
    document.getElementById('table-fit').textContent = fit ? `Fit to setup (${fmtTable(fit.w, fit.d)})` : 'Fit to setup';
  }

  setTableSize({ w, d }) {
    document.getElementById('table-size').textContent = fmtTable(w, d);
    document.querySelectorAll('#table-presets .preset').forEach((b) => {
      const t = TABLE_PRESETS[Number(b.dataset.i)];
      b.classList.toggle('on', t.w === w && t.d === d);
    });
  }

  // ---- misc ------------------------------------------------------------------------------
  setOnline(ok) {
    this.offline.hidden = ok;
    document.getElementById('server-dot').classList.toggle('on', ok);
    document.getElementById('server-label').textContent = ok ? this.lab.api.base.replace(/^https?:\/\//, '') : 'offline';
  }
  setTitle(name) { document.getElementById('setup-name').textContent = name; }

  toggleHelp(show = this.help.classList.contains('hidden')) {
    this.help.classList.toggle('hidden', !show);
    if (!show) { try { localStorage.setItem('phoq.helpSeen2', '1'); } catch { /* ignore */ } }
  }
  showScope(show) {
    document.getElementById('scope-panel').classList.toggle('hidden', !show);
    this.scopeBtn.classList.toggle('on', show);
  }
  toast(msg, cls = '') {
    const el = document.createElement('div');
    el.className = `toast ${cls}`;
    el.textContent = msg;
    this.toastEl.prepend(el);
    setTimeout(() => el.remove(), cls === 'err' ? 5000 : 2600);
    while (this.toastEl.children.length > 4) this.toastEl.lastChild.remove();
  }
  async exportPython() {
    try {
      const text = await this.lab.api.exportPython();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/x-python' }));
      a.download = `${(this.lab.state?.name ?? 'setup').replace(/\W+/g, '_').toLowerCase()}.py`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { this.toast(`Export failed: ${e.message}`, 'err'); }
  }

  // ---- inspector -------------------------------------------------------------------------
  render() {
    const lab = this.lab;
    const comp = lab.state?.components.find((c) => c.name === lab.selected);
    if (!comp) { this.renderOverview(); return; }
    const focused = document.activeElement?.closest?.('#inspector-body') ? document.activeElement.dataset.key : null;
    let html = `<div class="insp-head"><div class="ico">${ICONS[comp.type] ?? GENERIC}</div><div><b>${esc(comp.name)}</b><small>${esc(comp.label)} · <code>${esc(comp.type)}</code></small></div></div>`;
    html += this.presetHtml(comp);
    for (const key of comp.fields) html += this.fieldHtml(comp, key);
    if (comp.plug) html += this.plugHtml(comp);
    const elec = comp.ports.filter((p) => p.kind === 'electrical');
    if (elec.length) {
      html += '<div class="sub">Electrical connections</div>';
      for (const port of elec) html += this.wireHtml(comp, port);
    }
    html += `<div class="readouts" id="readouts"></div><div class="insp-actions">`;
    const part = lab.parts.get(comp.name);
    if (part && !part.onShelf && !part.mount) html += `<button data-act="rotate" title="Rotate 90° (R)">⟳ Rotate</button>`;
    if (comp.category === 'cord') html += `<button data-act="unplug" title="Unplug both connectors">Unplug</button>`;
    html += `<button data-act="delete" class="danger" title="Remove (Del)">Remove</button></div>
      <p class="hint-py">Python: <code>setup[${esc(JSON.stringify(comp.name))}]</code></p>`;
    this.body.innerHTML = html;
    this.bind(comp);
    if (focused) this.body.querySelector(`[data-key="${focused}"]`)?.focus();
    this.updateReadouts();
  }

  /** Part picker: the generic class defaults, or a datasheet preset of this type (phoq.presets). */
  presetHtml(comp) {
    const list = this.lab.presets.get(comp.type) ?? [];
    if (list.length < 2) return '';
    const cur = list.find((p) => p.tier !== 'generic' && p.id === comp.preset);
    const opt = (p) => {
      const sel = p.tier === 'generic' ? comp.preset === 'generic' : p.id === comp.preset;
      const text = p.tier === 'generic' ? 'Generic (class defaults)' : p.title + (p.part_number && !p.title.includes(p.part_number) ? ` · ${p.part_number}` : '');
      return `<option value="${esc(p.id)}" ${sel ? 'selected' : ''}>${esc(text)}</option>`;
    };
    return `<div class="field part-preset"><label for="f-preset"><span>Part preset</span>${cur ? `<span class="unit">${esc(cur.tier)}</span>` : ''}</label>
      <select id="f-preset" data-preset="1" aria-label="Part preset">${list.map(opt).join('')}</select>
      ${cur ? `<small class="part-desc">${esc(cur.description)}${cur.curves?.length ? ' · spectral curve from the datasheet' : ''}${cur.has_limits ? ' · absolute maximum ratings are checked' : ''}</small>` : ''}</div>`;
  }

  /** A parameter's spec, its unit replaced where the component says it follows another
   *  setting (the analyzer's reference level is in its display units). */
  specOf(comp, key) {
    const spec = this.lab.spec(comp.type, key), unit = comp.units?.[key];
    return spec && unit ? { ...spec, unit } : spec;
  }

  fieldHtml(comp, key) {
    const spec = this.specOf(comp, key);
    if (!spec) return '';
    const v = comp.params[key];
    const id = `f-${key}`;
    const attrs = `id="${id}" data-key="${key}" aria-label="${esc(spec.label)}"`;
    const doc = spec.doc ? ` title="${esc(spec.doc)}"` : '';
    const prov = comp.provenance?.[key];
    const badge = prov ? `<i class="prov${prov.overridden ? ' edited' : ''}" title="${esc(provTitle(prov))}">${prov.overridden ? 'edited' : (PROV_SHORT[prov.kind] ?? prov.kind)}</i>` : '';
    const swatch = key.startsWith('connector') ? `<i class="conn-swatch" style="background:${connectorCss(v)}"></i>` : '';
    const name = `<span>${swatch}${esc(spec.label)}${badge}</span>`;
    if (spec.type === 'bool') {
      return `<div class="field"${doc}><label class="switch" for="${id}">${name}<input type="checkbox" ${attrs} ${v ? 'checked' : ''}/></label></div>`;
    }
    if (spec.choices) {
      return `<div class="field"${doc}><label for="${id}">${name}</label><select ${attrs}>${spec.choices.map((c) => `<option ${c === v ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
    }
    if (spec.type === 'str') {
      return `<div class="field"${doc}><label for="${id}">${name}</label><input type="text" ${attrs} value="${esc(v)}" placeholder="auto"/></div>`;
    }
    if (spec.presets) {
      const opts = [...new Set([...spec.presets, v])].sort((a, b) => a - b);
      return `<div class="field"${doc}><label for="${id}">${name}</label><select ${attrs} data-num="1">${opts.map((o) => `<option value="${o}" ${o === v ? 'selected' : ''}>${esc(fmtParam(o, spec))}</option>`).join('')}</select></div>`;
    }
    if (!spec.display && spec.min !== null && spec.max !== null && spec.max - spec.min <= 1000 && spec.type === 'float') {
      const step = spec.max - spec.min <= 1 ? 0.001 : spec.max - spec.min <= 20 ? 0.05 : 0.5;
      return `<div class="field"${doc}><label for="${id}">${name}<output data-out="${key}">${esc(fmtParam(v, spec))}</output></label><input type="range" ${attrs} min="${spec.min}" max="${spec.max}" step="${step}" value="${v}"/></div>`;
    }
    const scale = spec.display ? spec.display[1] : 1;
    return `<div class="field"${doc}><label for="${id}">${name}<span class="unit">${esc(spec.display ? spec.display[0] : spec.unit)}</span></label><input type="number" ${attrs} data-scale="${scale}" value="${Number((v * scale).toPrecision(8))}" step="any"/></div>`;
  }

  wireHtml(comp, port) {
    const state = this.lab.state;
    const want = port.direction === 'out' ? 'in' : 'out';
    const cands = state.components.flatMap((c) => c.ports.filter((p) => p.kind === 'electrical' && p.direction === want && (!p.peer || p.peer === `${comp.name}.${port.name}`)).map((p) => `${c.name}.${p.name}`));
    return `<div class="field"><label>${esc(port.label)} <span class="unit">${esc(port.name)}</span></label><select data-wire="${esc(port.name)}" aria-label="${esc(port.label)}">
      <option value="">— not connected —</option>${cands.map((c) => `<option ${c === port.peer ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
  }

  /** A plug (a terminator) has no cord to drag: pick the free optical port it caps. */
  plugHtml(comp) {
    const port = comp.ports[0], state = this.lab.state;
    const cands = state.components.filter((c) => !c.plug && c.name !== comp.name).flatMap((c) =>
      c.ports.filter((p) => p.kind === 'optical' && (!p.peer || p.peer === `${comp.name}.${port.name}`)).map((p) => `${c.name}.${p.name}`));
    return `<div class="field"><label>Plugged into <span class="unit">${esc(port.name)}</span></label><select data-wire="${esc(port.name)}" aria-label="Plugged into">
      <option value="">— not plugged in —</option>${cands.map((c) => `<option ${c === port.peer ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
  }

  bind(comp) {
    const lab = this.lab;
    for (const el of this.body.querySelectorAll('[data-key]')) {
      const key = el.dataset.key, spec = this.specOf(comp, key);
      const value = () => {
        if (el.type === 'checkbox') return el.checked;
        if (el.dataset.num || el.type === 'range') return Number(el.value);
        if (el.type === 'number') return Number(el.value) / Number(el.dataset.scale || 1);
        return el.value;
      };
      const send = () => lab.setParams(comp.name, { [key]: spec.type === 'int' ? Math.round(value()) : value() });
      if (el.type === 'range') {
        el.addEventListener('input', () => {
          const out = this.body.querySelector(`[data-out="${key}"]`);
          if (out) out.textContent = fmtParam(value(), spec);
          clearTimeout(this.throttle.get(key));
          this.throttle.set(key, setTimeout(send, 90));
        });
      } else {
        el.addEventListener('change', send);
      }
    }
    const preset = this.body.querySelector('[data-preset]');
    if (preset) preset.addEventListener('change', () => lab.setPreset(comp.name, preset.value));
    for (const el of this.body.querySelectorAll('[data-wire]')) {
      el.addEventListener('change', async () => {
        const port = `${comp.name}.${el.dataset.wire}`;
        const cur = comp.ports.find((p) => p.name === el.dataset.wire)?.peer;
        if (cur) await lab.disconnect(port, true);
        if (el.value) await lab.connect(port, el.value);
      });
    }
    for (const b of this.body.querySelectorAll('[data-act]')) {
      b.onclick = () => {
        if (b.dataset.act === 'rotate') lab.parts.get(comp.name)?.rotate();
        if (b.dataset.act === 'unplug') comp.ports.forEach((p) => p.peer && lab.disconnect(`${comp.name}.${p.name}`));
        if (b.dataset.act === 'delete') lab.remove(comp.name);
      };
    }
  }

  renderOverview() {
    const s = this.lab.state;
    if (!s) { this.body.innerHTML = '<div class="empty"><p>Waiting for the Python lab server…</p></div>'; return; }
    const counts = {};
    for (const c of s.components) counts[c.label] = (counts[c.label] ?? 0) + 1;
    const legend = [635e-9, 850e-9, 1310e-9, 1550e-9].map((w) => `<div><i style="background:${wavelengthColor(w).css}"></i>${Math.round(w * 1e9)} nm</div>`).join('');
    this.body.innerHTML = `
      <div class="empty"><p>Select a part, fiber or connector to edit it. Every change is applied to the Python object on the server${s.save_path ? ' and autosaved' : ''}.</p></div>
      <div class="readouts">${Object.entries(counts).map(([k, n]) => `<div><span>${esc(k)}</span><b>${n}</b></div>`).join('') || '<div><span>The table is empty</span><b>—</b></div>'}
        <div><span>Autosave</span><b class="path">${esc(s.save_path ?? 'off')}</b></div>
        <div title="${esc((s.spares ?? []).join(', '))}"><span>Spare ports open</span><b class="${s.spares?.length ? 'warn' : ''}">${s.spares?.length ?? 0}</b></div></div>
      <div class="insp-actions"><button data-act="caps" ${s.spares?.length ? '' : 'disabled'} title="Cap every unused port of the couplers and other parts with a terminator of its own connector type">Cap spare ports</button></div>
      <div class="legend"><div style="color:var(--text)">Light-flow colors (false color for IR)</div>${legend}</div>`;
    const caps = this.body.querySelector('[data-act="caps"]');
    if (caps) caps.onclick = () => this.lab.capSpares();
  }

  /** Live values: server-side derived info + the latest network report. */
  updateReadouts() {
    const el = this.body.querySelector('#readouts');
    const lab = this.lab;
    const comp = lab.state?.components.find((c) => c.name === lab.selected);
    if (!el || !comp) return;
    const rows = Object.entries(comp.info ?? {});
    const det = lab.report?.detectors?.[comp.name];
    if (det) rows.push(...Object.entries(det.readouts));
    const src = lab.report?.sources?.[comp.name];
    if (src) rows.push(...Object.entries(src.readouts));
    if (comp.category === 'cord') {
      const f = lab.report?.fibers?.[comp.name];
      rows.push(['Optical power', f ? fmtSI(f.fwd + f.bwd, 'W') : '—']);
    }
    const cnt = lab.counters.get(comp.name);
    if (cnt) {
      rows.push(['Bins', `${cnt.slots.toLocaleString()} @ ${fmtSI(cnt.rate, 'Hz')}`]);
      for (const ch of Object.keys(cnt.counts)) rows.push([`${ch} (${cnt.sources[ch]})`, `${cnt.counts[ch].toLocaleString()} · ${fmtSI(cnt.rates[ch], 'cps')}`]);
    }
    for (const p of comp.ports.filter((q) => q.kind === 'optical')) {
      const type = comp.type === 'MatingSleeve' ? '' : ` · ${p.connector}`;   // a sleeve has no polish
      rows.push([`Port ${p.label}${type}`, p.peer ?? (comp.type === 'MatingSleeve' ? 'empty' : 'open'), p.peer ? 'ok' : '']);
    }
    const status = det?.status_level;
    el.innerHTML = rows.map(([k, v, cls]) => `<div><span>${esc(k)}</span><b class="${cls ?? (k === 'Status' ? status : '')}">${esc(v)}</b></div>`).join('');
  }

  updateWarnings(report) {
    const html = (report?.warnings ?? []).slice(0, 3).map((w) => `<div class="warn ${w.level === 'bad' ? 'bad' : ''}">⚠ ${esc(w.text)}</div>`).join('');
    if (html !== this.lastWarn) { this.warnEl.innerHTML = html; this.lastWarn = html; }
  }
}
