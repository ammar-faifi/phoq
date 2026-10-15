// Laptop consoles. Every process the server runs (a party, or an analysis task) is assigned to a
// laptop; the drawer shows one tab per laptop and, inside it, that laptop's processes.
import { esc } from './util.js';

const MAX_LINES = 2000;
export const STATUS_COLOR = { idle: '#34d399', running: '#22d3ee', starting: '#fbbf24', error: '#f87171', stopped: '#64748b' };

export class ConsoleDrawer {
  constructor(lab) {
    this.lab = lab;
    this.procs = new Map();          // process name → { name, kind, laptop, status, batches, dropped, result, file, sources, net, lines }
    this.laptops = new Map();        // laptop name → { name, kind, host, port } in the server's order
    this.activeLaptop = null;
    this.activeProc = null;
    const $ = (id) => document.getElementById(id);
    this.el = $('console-drawer');
    this.tabs = $('con-tabs');
    this.procTabs = $('con-procs');
    this.laptopInfo = $('con-laptop');
    this.out = $('con-out');
    this.meta = $('con-meta');
    this.result = $('con-result');
    this.btn = $('btn-console');
    this.badge = $('console-count');
    this.empty = $('con-empty');
    this.btn.onclick = () => this.toggle();
    $('con-min').onclick = () => this.close();
    $('con-restart').onclick = () => this.activeProc && lab.taskAction(this.activeProc, 'restart');
    $('con-stop').onclick = () => this.activeProc && lab.taskAction(this.activeProc, this.procs.get(this.activeProc)?.status === 'stopped' ? 'start' : 'stop');
    $('con-clear').onclick = () => {
      if (!this.activeProc) return;
      lab.taskAction(this.activeProc, 'clear');
      const p = this.procs.get(this.activeProc);
      if (p) { p.lines = []; this.renderActive(true); }
    };
    $('con-load').onsubmit = async (e) => {
      e.preventDefault();
      const input = $('con-path');
      if (!input.value.trim()) return;
      const r = await lab.loadScript(input.value.trim());
      if (r) input.value = '';
    };
  }

  /** The laptops the server declares (parties first, then the lab PC). */
  setLaptops(list) {
    for (const l of list) if (!this.laptops.has(l.name)) this.laptops.set(l.name, l);
    if (!this.activeLaptop && this.laptops.size) this.activeLaptop = [...this.laptops.keys()][0];
    this.renderTabs();
  }

  /** A `console` message from the server (full buffer on connect, increments afterwards). */
  onMessage(c) {
    let p = this.procs.get(c.task);
    if (!p) {
      p = { name: c.task, lines: [] };
      this.procs.set(c.task, p);
    }
    const prevNet = p.net;
    Object.assign(p, { kind: c.kind, laptop: c.laptop ?? 'LAB', status: c.status, batches: c.batches, dropped: c.dropped, result: c.result, file: c.file, sources: c.sources, net: c.net, port: c.port, host: c.host });
    if (!this.laptops.has(p.laptop)) this.laptops.set(p.laptop, { name: p.laptop, kind: c.kind === 'party' ? 'party' : 'lab', port: c.port, host: c.host });
    if (!this.activeLaptop) this.activeLaptop = p.laptop;
    if (!this.activeProc && p.laptop === this.activeLaptop) this.activeProc = p.name;
    if (c.full) p.lines = c.lines.slice(-MAX_LINES);
    else if (c.lines.length) {
      p.lines.push(...c.lines);
      if (p.lines.length > MAX_LINES) p.lines.splice(0, p.lines.length - MAX_LINES);
    }
    p.traffic = !!(c.net && prevNet && (c.net.sent !== prevNet.sent || c.net.recv !== prevNet.recv));
    this.renderTabs();
    if (this.activeProc === p.name && !this.el.hidden) this.renderActive(c.full, c.lines.length);
    return p;
  }

  procsOf(laptop) { return [...this.procs.values()].filter((p) => p.laptop === laptop); }

  /** What a laptop's screen shows. */
  laptopRecord(name) {
    const l = this.laptops.get(name) ?? { name };
    return { ...l, procs: this.procsOf(name) };
  }

  open(laptop, proc) {
    if (laptop && this.laptops.has(laptop)) this.activeLaptop = laptop;
    if (proc && this.procs.has(proc)) this.activeProc = proc;
    else if (!this.procsOf(this.activeLaptop).some((p) => p.name === this.activeProc)) this.activeProc = this.procsOf(this.activeLaptop)[0]?.name ?? null;
    this.el.hidden = false;
    document.getElementById('viewport').classList.add('has-console');
    this.btn.classList.add('on');
    this.renderTabs();
    this.renderActive(true);
  }
  close() {
    this.el.hidden = true;
    document.getElementById('viewport').classList.remove('has-console');
    this.btn.classList.remove('on');
  }
  toggle() { this.el.hidden ? this.open() : this.close(); }

  renderTabs() {
    const n = this.procs.size;
    this.badge.hidden = this.laptops.size === 0;
    this.badge.textContent = this.laptops.size;
    this.btn.title = n ? `${this.laptops.size} laptop${this.laptops.size > 1 ? 's' : ''}, ${n} process${n > 1 ? 'es' : ''}` : 'Laptops (start the server with --protocol or --analysis)';
    const worst = (procs) => procs.some((p) => p.status === 'error') ? 'error' : procs.some((p) => p.status === 'running') ? 'running' : procs.some((p) => p.status === 'idle') ? 'idle' : procs.some((p) => p.status === 'starting') ? 'starting' : 'stopped';
    this.tabs.innerHTML = [...this.laptops.values()].map((l) => {
      const procs = this.procsOf(l.name);
      return `<button class="con-tab ${l.name === this.activeLaptop ? 'on' : ''}" data-laptop="${esc(l.name)}" title="${esc(l.port ? `${l.host}:${l.port}` : 'lab PC')}">
        <i style="background:${STATUS_COLOR[worst(procs)] ?? '#64748b'}"></i>${esc(l.name)}<small>${procs.length}</small></button>`;
    }).join('');
    for (const b of this.tabs.querySelectorAll('.con-tab')) b.onclick = () => this.open(b.dataset.laptop);
    const procs = this.procsOf(this.activeLaptop);
    this.procTabs.innerHTML = procs.map((p) => `
      <button class="con-proc ${p.name === this.activeProc ? 'on' : ''}" data-proc="${esc(p.name)}" title="${esc(p.file ?? '')}">
        <i style="background:${STATUS_COLOR[p.status] ?? '#64748b'}"></i>${esc(p.name)}<small>${p.batches ?? 0}</small></button>`).join('');
    for (const b of this.procTabs.querySelectorAll('.con-proc')) b.onclick = () => { this.activeProc = b.dataset.proc; this.renderTabs(); this.renderActive(true); };
    const l = this.laptops.get(this.activeLaptop);
    this.laptopInfo.textContent = l ? (l.port ? `${l.name} · ${l.host ?? '127.0.0.1'}:${l.port}` : `${l.name} · lab PC`) : '';
    this.empty.hidden = n > 0;
    this.el.classList.toggle('no-tasks', n === 0);
  }

  renderActive(full, added = 0) {
    const p = this.procs.get(this.activeProc);
    if (!p) { this.out.textContent = ''; this.meta.innerHTML = ''; this.result.innerHTML = ''; return; }
    const nearBottom = this.out.scrollHeight - this.out.scrollTop - this.out.clientHeight < 40;
    if (full || added === 0) this.out.textContent = p.lines.join('\n');
    else this.out.textContent += (this.out.textContent ? '\n' : '') + p.lines.slice(-added).join('\n');
    if (nearBottom || full) this.out.scrollTop = this.out.scrollHeight;
    const file = (p.file ?? '').split('/').pop();
    const rows = [
      ['Status', `<b style="color:${STATUS_COLOR[p.status] ?? 'inherit'}">${esc(p.status ?? '')}</b>`],
      ['Kind', esc(p.kind ?? '')],
      ['Script', `<b title="${esc(p.file ?? '')}">${esc(file)}</b>`],
    ];
    if (p.kind === 'task') rows.push(['Source', esc((p.sources ?? []).join(', ') || 'all counters')]);
    rows.push(['Batches', `${p.batches ?? 0}${p.dropped ? ` · ${p.dropped} dropped` : ''}`]);
    if (p.net) {
      rows.push(['Sent', `${p.net.sent} msgs · ${(p.net.bytes_out / 1e3).toFixed(1)} kB`], ['Received', `${p.net.recv} msgs · ${(p.net.bytes_in / 1e3).toFixed(1)} kB`]);
      for (const [peer, s] of Object.entries(p.net.peers ?? {})) rows.push([`↔ ${esc(peer)}`, `${s.sent} out · ${s.recv} in`]);
    }
    this.meta.innerHTML = rows.map(([k, v]) => `<div><span>${k}</span>${v.startsWith('<b') ? v : `<b>${v}</b>`}</div>`).join('');
    const r = p.result ?? {};
    this.result.innerHTML = Object.entries(r).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(fmtValue(v))}</dd>`).join('') || '<dt class="muted">report() / a returned dict shows read-outs</dt>';
    document.getElementById('con-stop').textContent = p.status === 'stopped' ? 'Start' : 'Stop';
  }
}

function fmtValue(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v !== 'number') return String(v);
  if (Number.isInteger(v)) return v.toLocaleString();
  const a = Math.abs(v);
  return a !== 0 && (a < 1e-3 || a >= 1e6) ? v.toExponential(3) : Number(v.toPrecision(4)).toString();
}
