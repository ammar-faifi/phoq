"""FastAPI server: exposes a live :class:`Setup` and serves the 3D virtual lab as its visualizer.

All physics runs here. The browser only renders what this server reports and sends edits back:

* REST (``/api/...``) for the catalog, the setup state and every edit (add/remove parts,
  set parameters, connect/disconnect ports, autoset, save/export). Interactive docs at ``/docs``.
* WebSocket ``/ws`` streams ``state`` (after any change), ``report`` (powers, currents, warnings),
  ``frame`` (oscilloscope traces, ~15/s) and ``counter`` (photon-counter results, ~1/s).
"""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analysis import TaskWorker, load_tasks
from .parties import Experiment
from . import presets
from .core import Component, Setup, _ensure_registry
from .engine import report, trace
from .instruments import Oscilloscope, PhotonCounter

FRAME_HZ = 15
COUNTER_PERIOD = 1.0


class AddBody(BaseModel):
    type: str
    name: str | None = None
    preset: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ParamsBody(BaseModel):
    params: dict[str, Any]


class PresetBody(BaseModel):
    id: str


class ConnectBody(BaseModel):
    a: str
    b: str


class PortBody(BaseModel):
    port: str


class ScriptBody(BaseModel):
    path: str


def _clean(obj):
    """JSON-safe copy (NaN/inf → None, numpy scalars → Python)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def catalog() -> dict:
    _ensure_registry()
    types = []
    for name, cls in Component.registry.items():
        sample = cls()
        types.append(
            {
                "type": name,
                "category": cls.category,
                "label": cls.label,
                "description": cls.description,
                "prefix": cls.prefix,
                "params": [p.spec() for p in cls.param_specs().values()],
                "presets": [p.summary() for p in presets.search(cls)],
                "ports": [
                    {
                        "name": p.name,
                        "kind": p.kind,
                        "direction": p.direction,
                        "label": p.label,
                    }
                    for p in sample.ports.values()
                ],
            }
        )
    return {"types": types}


class LabSession:
    """The live setup shared by all browser clients."""

    def __init__(
        self,
        setup: Setup,
        save_path: Path | None = None,
        seed: int | None = None,
        tasks=(),
        parties=None,
    ):
        self.setup = setup
        self.lock = threading.RLock()
        self.experiment = (
            Experiment(setup, parties, tasks, seed, lock=self.lock) if parties else None
        )
        self.task_workers: list[TaskWorker] = (
            [] if parties else [TaskWorker(t) for t in tasks]
        )
        self.batch_seq = 0
        self.console_full = True  # send every console's whole buffer on the next tick
        self.save_path = save_path
        self.rng = np.random.default_rng(seed if seed is not None else setup.seed)
        self.clients: set[WebSocket] = set()
        self.version = 0
        self.state_dirty = True
        self.save_at = 0.0
        self.last_counter = 0.0
        self.sent_held: dict[str, int] = (
            {}
        )  # scope → id of the held sweep already streamed
        setup.on_change(self._changed)

    def _changed(self, event, component, param) -> None:
        self.version += 1
        self.state_dirty = True
        if self.save_path is not None:
            self.save_at = time.monotonic() + 0.4

    @property
    def workers(self) -> list:
        return self.experiment.workers if self.experiment else self.task_workers

    def laptops(self) -> list[dict]:
        if self.experiment:
            return self.experiment.laptops()
        return [{"name": "LAB", "kind": "lab"}] if self.task_workers else []

    def start_workers(self) -> None:
        if self.experiment:
            self.experiment.start()
        else:
            for w in self.task_workers:
                w.start()

    # ---- views ----------------------------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            comps = []
            for c in self.setup:
                comps.append(
                    {
                        "name": c.name,
                        "type": type(c).__name__,
                        "category": c.category,
                        "label": c.label,
                        "params": c.params,
                        "preset": c.preset,
                        "preset_title": c.preset_info.title,
                        "provenance": c.provenance(),
                        "limits": c.limits,
                        "fields": c.ui_fields()
                        + [k for k in c.param_specs() if k.startswith("connector")],
                        "info": c.info(),
                        "ports": [p.to_dict() for p in c.ports.values()],
                    }
                )
            return _clean(
                {
                    "name": self.setup.name,
                    "version": self.version,
                    "components": comps,
                    "connections": [
                        [a.path, b.path] for a, b in self.setup.connections()
                    ],
                    "save_path": str(self.save_path) if self.save_path else None,
                    "laptops": self.laptops(),
                }
            )

    def tick(self, with_counters: bool) -> list[dict]:
        """One streaming step: state (if changed), report, scope frames, counters."""
        msgs = []
        with self.lock:
            net = trace(self.setup)
            msgs.append({"type": "report", "report": report(self.setup, net)})
            for inst in self.setup.of_type(Oscilloscope):
                frame = None
                if inst.armed:  # single shot: one try per tick
                    frame = inst.single(net, self.rng, attempts=1)
                elif inst.running:
                    frame = inst.acquire(net, self.rng)
                elif inst.held is not None and self.sent_held.get(inst.name) != id(
                    inst.held
                ):
                    frame = inst.held  # a held sweep goes out once
                if frame is not None:
                    if not inst.running:
                        self.sent_held[inst.name] = id(frame)
                    msgs.append({"type": "frame", "frame": frame.to_json()})
            if self.experiment:  # parties drive the blocks
                if time.monotonic() - self.experiment.last_acquire >= 0.5:
                    for res in (self.experiment.poll() or {}).values():
                        msgs.append({"type": "counter", "counter": res.to_json()})
            elif with_counters:
                for inst in self.setup.of_type(PhotonCounter):
                    if inst.running and any(p.peer for p in inst.ports.values()):
                        res = inst.acquire(net, self.rng)
                        res.seq = self.batch_seq
                        msgs.append({"type": "counter", "counter": res.to_json()})
                        for (
                            w
                        ) in (
                            self.task_workers
                        ):  # the user's analysis tasks get every batch
                            if w.task.accepts(inst.name):
                                w.send(res)
                if any(
                    w.task.accepts(i.name)
                    for w in self.task_workers
                    for i in self.setup.of_type(PhotonCounter)
                ):
                    self.batch_seq += 1
            full = self.console_full
            self.console_full = False
            for w in self.workers:
                if full or w.has_news():
                    msgs.append({"type": "console", "console": w.snapshot(full)})
            if self.state_dirty:  # last, so a capture's new mode rides along
                self.state_dirty = False
                msgs.insert(0, {"type": "state", "state": self.state()})
        return [_clean(m) for m in msgs]

    def save(self) -> Path | None:
        if self.save_path is None:
            return None
        with self.lock:
            self.setup.save(self.save_path)
        return self.save_path

    def worker(self, name: str):
        for w in self.workers:
            if w.name == name:
                return w
        raise KeyError(f"no process named {name!r}")

    def add_script(self, path: str) -> list[TaskWorker]:
        """Load a script's tasks and start them (a task with the same name replaces the old one)."""
        pool = self.experiment.task_workers if self.experiment else self.task_workers
        added = []
        for t in load_tasks(path):
            old = next((w for w in pool if w.name == t.name), None)
            if old is not None:
                old.stop()
                pool.remove(old)
            w = TaskWorker(t)
            w.start()
            pool.append(w)
            added.append(w)
        self.console_full = True
        self.state_dirty = True
        return added

    async def broadcast(self, msgs: list[dict]) -> None:
        for ws in list(self.clients):
            try:
                for m in msgs:
                    await ws.send_text(json.dumps(m))
            except Exception:
                self.clients.discard(ws)

    async def stream(self) -> None:
        while True:
            await asyncio.sleep(1 / FRAME_HZ)
            if self.save_at and time.monotonic() >= self.save_at:
                self.save_at = 0.0
                await asyncio.to_thread(self.save)
            if not self.clients:
                continue
            now = time.monotonic()
            with_counters = now - self.last_counter >= COUNTER_PERIOD
            if with_counters:
                self.last_counter = now
            try:
                msgs = await asyncio.to_thread(self.tick, with_counters)
            except Exception as exc:  # keep streaming; show the problem in the UI
                msgs = [{"type": "error", "message": f"{type(exc).__name__}: {exc}"}]
            await self.broadcast(msgs)


def default_web_dir() -> Path | None:
    """The virtual lab shipped inside the package (``phoq/web``)."""
    web = Path(__file__).resolve().parent / "web"
    return web if (web / "index.html").exists() else None


def create_app(
    setup: Setup,
    *,
    save_path: Path | None = None,
    web_dir: Path | None = None,
    seed: int | None = None,
    tasks=(),
    parties=None,
) -> FastAPI:
    session = LabSession(setup, save_path, seed, tasks, parties)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(session.start_workers)
        task = asyncio.create_task(session.stream())
        yield
        task.cancel()
        for w in session.workers:
            w.stop()
        session.save()

    app = FastAPI(
        title="phoq",
        description="Headless optics lab with a live 3D visualizer",
        lifespan=lifespan,
    )
    app.state.session = session
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.middleware("http")
    async def revalidate_assets(request, call_next):
        # Browsers otherwise keep stale copies of the visualizer's JS modules after an update.
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    def guarded(fn, *args, **kw):
        try:
            with session.lock:
                return fn(*args, **kw)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from None
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e)) from None
        except ConnectionError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/catalog")
    def get_catalog():
        return catalog()

    @app.get("/api/state")
    def get_state():
        return session.state()

    @app.get("/api/report")
    def get_report():
        return _clean(guarded(report, session.setup))

    @app.post("/api/components")
    def add_component(body: AddBody):
        def add():
            _ensure_registry()
            cls = Component.registry.get(body.type)
            if cls is None:
                raise KeyError(f"unknown component type {body.type!r}")
            return session.setup.add(
                cls(body.name, preset=body.preset, **body.params)
            ).name

        return {"name": guarded(add)}

    @app.patch("/api/components/{name}")
    def set_params(name: str, body: ParamsBody):
        guarded(lambda: session.setup[name].set(**body.params))
        return {"ok": True, "params": _clean(session.setup[name].params)}

    @app.post("/api/components/{name}/preset")
    def set_preset(name: str, body: PresetBody):
        guarded(lambda: session.setup[name].apply_preset(body.id))
        comp = session.setup[name]
        return {"ok": True, "preset": comp.preset, "params": _clean(comp.params)}

    @app.get("/api/presets")
    def list_presets(
        component: str | None = None, vendor: str | None = None, q: str | None = None
    ):
        return {
            "presets": [
                p.summary() for p in presets.search(component, vendor=vendor, text=q)
            ]
        }

    @app.get("/api/presets/{preset_id:path}")
    def get_preset(preset_id: str):
        return _clean(guarded(presets.get, preset_id).to_dict())

    @app.delete("/api/components/{name}")
    def remove_component(name: str):
        guarded(session.setup.remove, name)
        return {"ok": True}

    @app.post("/api/connections")
    def connect(body: ConnectBody):
        fiber = guarded(session.setup.connect, body.a, body.b)
        return {"ok": True, "fiber": fiber.name if fiber is not None else None}

    @app.post("/api/disconnect")
    def disconnect(body: PortBody):
        guarded(session.setup.disconnect, body.port)
        return {"ok": True}

    @app.post("/api/instruments/{name}/autoset")
    def autoset(name: str):
        def run():
            inst = session.setup[name]
            if not hasattr(inst, "autoset"):
                raise ValueError(f"{name} has no autoset")
            inst.autoset(trace(session.setup), session.rng)

        guarded(run)
        return {"ok": True}

    @app.post("/api/instruments/{name}/single")
    def single(name: str):
        """Arm an oscilloscope (the first triggered sweep is captured, shown and held), or cancel a pending one."""

        def run():
            inst = session.setup[name]
            if not hasattr(inst, "arm"):
                raise ValueError(f"{name} has no single-shot mode")
            inst.disarm() if inst.armed else inst.arm()
            return inst.armed

        return {"armed": guarded(run)}

    @app.post("/api/run")
    def run_all():
        from .runner import Runner

        def run():
            runner = Runner(session.setup)
            runner.rng = session.rng
            return runner.run()

        results = guarded(run)
        return _clean(
            {
                "summary": results.summary(),
                "results": {k: v.to_json() for k, v in results.items()},
            }
        )

    @app.get("/api/tasks")
    def list_tasks():
        return {
            "tasks": [w.snapshot(full=False) | {"lines": []} for w in session.workers]
        }

    @app.post("/api/tasks")
    def add_tasks(body: ScriptBody):
        def run():
            return [w.task.name for w in session.add_script(body.path)]

        try:
            return {"tasks": guarded(run)}
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from None

    @app.post("/api/tasks/{name}/{action}")
    def task_action(name: str, action: str):
        def run():
            w = session.worker(name)
            if action == "restart":
                w.restart()
            elif action == "stop":
                w.stop()
            elif action == "start":
                w.start()
            elif action == "clear":
                w.clear()
            else:
                raise ValueError(
                    f"unknown action {action!r} (restart, stop, start, clear)"
                )
            session.console_full = True
            return w.status

        return {"status": guarded(run)}

    @app.post("/api/save")
    def save():
        if session.save_path is None:
            raise HTTPException(400, "server was started without a save path")
        return {"path": str(session.save())}

    @app.get("/api/export/python", response_class=PlainTextResponse)
    def export_python():
        return guarded(session.setup.to_python)

    @app.get("/api/export/json")
    def export_json():
        return guarded(session.setup.to_dict)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        session.clients.add(ws)
        session.state_dirty = True
        session.sent_held.clear()  # a new client gets any held sweeps too
        session.console_full = True  # … and every console's history
        try:
            while True:
                await ws.receive_text()  # clients only listen; keeps the socket open
        except WebSocketDisconnect:
            pass
        finally:
            session.clients.discard(ws)

    web_dir = web_dir or default_web_dir()
    if web_dir is not None:
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="virtual-lab")
    return app


def serve(
    setup: Setup,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    save_path: Path | None = None,
    web_dir: Path | None = None,
    seed: int | None = None,
    tasks=(),
    parties=None,
) -> None:
    import uvicorn

    app = create_app(
        setup,
        save_path=save_path,
        web_dir=web_dir,
        seed=seed,
        tasks=tasks,
        parties=parties,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")
