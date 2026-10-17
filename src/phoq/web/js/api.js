// Client for the Python lab server (FastAPI): REST for edits, a WebSocket for live updates.
// Point at another server with ?api=http://host:port

export class Api {
  constructor() {
    const q = new URLSearchParams(location.search).get('api');
    this.base = (q || (location.protocol.startsWith('http') ? location.origin : 'http://127.0.0.1:8000')).replace(/\/$/, '');
    this.handlers = {};
    this.online = false;
    this.ws = null;
  }

  on(ev, fn) { (this.handlers[ev] ??= []).push(fn); }
  emit(ev, data) { for (const fn of this.handlers[ev] ?? []) fn(data); }

  async req(method, path, body) {
    const res = await fetch(this.base + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const isJson = res.headers.get('content-type')?.includes('json');
    const data = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      const detail = isJson ? data.detail : data;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  catalog() { return this.req('GET', '/api/catalog'); }
  add(type, params = {}) { return this.req('POST', '/api/components', { type, params }); }
  patch(name, params) { return this.req('PATCH', `/api/components/${encodeURIComponent(name)}`, { params }); }
  setPreset(name, id) { return this.req('POST', `/api/components/${encodeURIComponent(name)}/preset`, { id }); }
  remove(name) { return this.req('DELETE', `/api/components/${encodeURIComponent(name)}`); }
  connect(a, b) { return this.req('POST', '/api/connections', { a, b }); }
  disconnect(port) { return this.req('POST', '/api/disconnect', { port }); }
  autoset(name) { return this.req('POST', `/api/instruments/${encodeURIComponent(name)}/autoset`); }
  single(name) { return this.req('POST', `/api/instruments/${encodeURIComponent(name)}/single`); }
  tasks() { return this.req('GET', '/api/tasks'); }
  taskAction(name, action) { return this.req('POST', `/api/tasks/${encodeURIComponent(name)}/${action}`); }
  loadScript(path) { return this.req('POST', '/api/tasks', { path }); }
  save() { return this.req('POST', '/api/save'); }
  exportPython() { return this.req('GET', '/api/export/python'); }

  /** Open (and keep re-opening) the live update socket. */
  open() {
    const url = this.base.replace(/^http/, 'ws') + '/ws';
    let ws;
    try { ws = new WebSocket(url); } catch { setTimeout(() => this.open(), 2000); return; }
    this.ws = ws;
    ws.onopen = () => { this.online = true; this.emit('online', true); };
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      this.emit(msg.type, msg[msg.type] ?? msg);
    };
    ws.onclose = () => {
      if (this.online) { this.online = false; this.emit('online', false); }
      else this.emit('online', false);
      setTimeout(() => this.open(), 1500);
    };
  }
}
